# -*- coding: utf-8 -*-
"""紧箱边界仿真（2026-11-05）。

用户要求：「所有的问题都是在紧箱状态下的边界问题，理想空箱都不是问题……你需要
在这个边界条件下把所有的场景都仿真模拟一下」。

做法：**不重写判据**。造一个假服务端（箱/炉/金币状态机 + mainpage XML），把真实的
`AutoRefine.tick()` 跑起来，让所有闸门、延后、腾位逻辑原样执行，然后在几十个
紧箱初始态下各跑 N tick，看它到底会不会：

  · 死锁（N tick 零进展，且物理上还有路可走）
  · 空转（买了又卖、卖了又买，净进度零）
  · 拆套（取了卡把买底卡的格位吃掉，集齐窗口被关掉）
  · 冻结过度（明明能靠投炉/腾位推进，却被闸住）

用法：
    python3 tools/sim_tight_box.py            # 跑全部场景
    python3 tools/sim_tight_box.py --ticks 40 # 指定 tick 数
    python3 tools/sim_tight_box.py -v         # 打印每 tick 摘要

判定口径（与真机一致）：
  · 换卡箱 changebox cur=18，保险箱 storebox cur 可配（168 / 47 / 小），简易箱 15
  · 买卡只落换卡箱；**取出的卡优先落保险箱，满了才溢到换卡箱**
  · 取卡准入 = 换卡箱空位 ≥ 2；取一张占 1 格
  · 投炉 = 消耗 3 张指定素材，产物进炉（flag=1 → 到点 flag=2）
  · 腾位只能：10 面底卡卖掉 / 别主题卡移保险箱（保险箱满则失败）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.auto_refine import AutoRefine  # noqa: E402
from backend.constants import (EP_BUY, EP_COLLECTION_ADD, EP_COLLECTION_GET,  # noqa: E402
                               EP_MAINPAGE, EP_REFINE, EP_STEAL, EP_USER_LIST)


# ---------------------------------------------------------------- 卡库
_LIB = json.load(open(ROOT / "data" / "card_lib.json"))
NAME = {int(c[0]): str(c[2]) for c in _LIB["card_list"]}
PRICE = {int(c[0]): int(c[3] or 0) for c in _LIB["card_list"]}
THEMES = {int(t[0]): t for t in _LIB["theme_card_list"]}
COMP = {}


def set_ids(tid: int) -> list[int]:
    return [int(x) for x in (THEMES[tid][11] or []) if x]


def feeders(cid: int) -> list[int]:
    """卡库里的全部配方（跨套）：cid -> 3 素材。"""
    out = []
    for r in _LIB["theme_compose_list"]:
        if int(r[2]) == int(cid):
            out.append([int(r[3]), int(r[4]), int(r[5])])
    return out[-1] if out else []


def is_base(cid: int) -> bool:
    return not feeders(cid)


def cheapest_base(tid: int) -> list[int]:
    """本套「只差底卡」时最可能被买的那几张（面额 10 的底卡）。"""
    out = []
    for c in set_ids(tid):
        if is_base(c) and PRICE.get(c, 0) <= 10:
            out.append(c)
    return out


# ---------------------------------------------------------------- 假服务端
class FakeWorld:
    """箱 / 炉 / 金币状态机。所有容量语义与真机一致（cur 是已开通容量）。"""

    def __init__(self, ch_cap=18, st_cap=168, sp_cap=15, gold=200000,
                 stoves=5, main_slots=5):
        self.ch_cap = ch_cap
        self.st_cap = st_cap
        self.sp_cap = sp_cap
        self.gold = gold
        self.n_stove = stoves          # <stovebox stovenum>
        self.main_slots = main_slots   # 主卡位数（5 主 + 1 偷 = cur 7）
        self.change: list[dict] = []   # [{id, st}]  st = 0
        self.store: list[dict] = []
        self.simple: list[dict] = []
        self.stove: list[dict] = []    # [{id, stove, flag, btime, locktime}]
        self.now = 1_800_000_000
        self.sold: Counter = Counter()
        self.bought: Counter = Counter()
        self.moved: Counter = Counter()
        self.parked: Counter = Counter()   # 保险箱 → 简易箱 的寄存次数
        self.took_lucky = 0            # 幸运事件触发次数（仿真里恒 0，留字段）

    # ---- 箱操作 ----
    def _norm(self):
        for box in (self.change, self.store, self.simple):
            for i, c in enumerate(box):
                c["slot"] = i

    def ch_free(self):
        return self.ch_cap - len(self.change)

    def st_free(self):
        return self.st_cap - len(self.store)

    def sp_free(self):
        return self.sp_cap - len(self.simple)

    def add_change(self, cid: int, n=1) -> int:
        added = 0
        for _ in range(n):
            if self.ch_free() <= 0:
                break
            self.change.append({"id": int(cid), "st": 0})
            added += 1
        self._norm()
        return added

    def add_store(self, cid: int, n=1) -> int:
        added = 0
        for _ in range(n):
            if self.st_free() <= 0:
                break
            self.store.append({"id": int(cid), "st": 1})
            added += 1
        self._norm()
        return added

    def add_any(self, cid: int, n=1) -> int:
        """取卡落箱：**优先进保险箱**，满了才溢到换卡箱（用户 2026-11-05 定调）。"""
        added = self.add_store(cid, n)
        return added + self.add_change(cid, n - added)

    def stock(self) -> Counter:
        c = Counter()
        for box in (self.change, self.store, self.simple):
            for x in box:
                c[int(x["id"])] += 1
        return c

    # ---- 炉操作 ----
    def add_stove(self, cid: int, left=0, flag=2, stove=None) -> bool:
        """投一张进炉。flag=1 在炼 / flag=2 到点待收。"""
        used = len(self.stove)
        if used >= self.main_slots:
            return False
        if stove is None:
            stove = used % max(1, self.n_stove)
        self.stove.append({"id": int(cid), "stove": stove, "flag": flag,
                           "btime": self.now, "locktime": int(left)})
        return True

    def advance(self, secs: int):
        self.now += secs
        for c in self.stove:
            if c["flag"] == 1 and self.now >= c["btime"] + c["locktime"]:
                c["flag"] = 2

    # ---- mainpage XML ----
    def xml(self) -> str:
        def cards(box, st):
            return "".join(f'<card slot="{c["slot"]}" id="{c["id"]}" slottype="{st}" '
                           f'type="0" />' for c in box)

        # 炉：有卡的槽 + 空槽（空槽 id=0 用于 free_main 计数）
        stove_cards = []
        occupied = set()
        for i, c in enumerate(self.stove):
            stove_cards.append(
                f'<card slot="{i}" id="{c["id"]}" stove="{c["stove"]}" '
                f'flag="{c["flag"]}" btime="{c["btime"]}" locktime="{c["locktime"]}" '
                f'opuin="10001" slottype="0" />')
            occupied.add(i)
        for i in range(len(self.stove), self.main_slots):
            if i in occupied:
                continue
            stove_cards.append(f'<card slot="{i}" id="0" slottype="0" />')
        stove_cards.append(f'<card slot="6" id="0" slottype="steal" />')
        stoves = "".join(
            f'<stove id="{i}" endtime="{self.now + 60}" lv="9" />'
            for i in range(self.n_stove))
        return (
            f'<hello time="{self.now}" />'
            f'<user uin="10001" randchance="0" />'
            f'<changebox cur="{self.ch_cap}" max="{self.ch_cap}">'
            f'{cards(self.change, 0)}</changebox>'
            f'<storebox cur="{self.st_cap}" max="{max(self.st_cap, 200)}">'
            f'{cards(self.store, 1)}</storebox>'
            f'<simplebox cur="{self.sp_cap}" max="{self.sp_cap}">'
            f'{cards(self.simple, 2)}</simplebox>'
            f'<stovebox cur="7" max="7" stovenum="{self.n_stove}">'
            f'{"".join(stove_cards)}</stovebox>'
            f'{stoves}'
        )


class FakeSession:
    """AutoRefine 用到的全部 session 接口（post / sell / move / 取卡 / 翻牌）。"""

    def __init__(self, w: FakeWorld, tid: int | None = None):
        self.w = w
        self.tid = tid
        self.uin = "10001"
        self.calls: Counter = Counter()
        self.denied_take = 0
        self.denied_buy = 0
        self.denied_move = 0
        # ★「卖出瞬间这张是否正好是集齐还缺的卡」——判定卖缺口卡必须用当时的缺口，
        #   不能用最终缺口（卖完后续操作会让本来不缺的卡也变缺，会误判）。
        self.sold_gap: Counter = Counter()

    async def post(self, path, **kw):
        self.calls[path] += 1
        w = self.w
        if path == EP_MAINPAGE:
            return {"code": 0, "raw": w.xml(), "message": ""}
        if path == EP_BUY:
            cid = int(kw.get("card_id") or 0)
            if w.ch_free() <= 0:
                self.denied_buy += 1
                return {"code": -1, "message": "卡箱 0 空位，买不进来"}
            if w.gold < PRICE.get(cid, 0):
                return {"code": -1, "message": "金币不足"}
            w.gold -= PRICE.get(cid, 0)
            w.add_change(cid, 1)
            w.bought[cid] += 1
            return {"code": 0, "message": "购买成功"}
        if path == EP_REFINE:
            return self._refine(kw)
        if path == EP_STEAL:
            return {"code": -1, "message": "仿真不偷炉"}
        if path == EP_COLLECTION_GET:
            return {"code": 0, "raw": "", "message": ""}
        if path == EP_COLLECTION_ADD:
            # 上交：整套各消耗 1 张（真机要求同时在箱）
            tid = int(kw.get("theme") or 0)
            need = Counter(set_ids(tid))
            have = w.stock()
            if any(have.get(c, 0) < n for c, n in need.items()):
                return {"code": -1, "message": "卡片不齐"}
            for c, n in need.items():
                removed = 0
                for box in (w.change, w.store, w.simple):
                    keep = []
                    for x in box:
                        if int(x["id"]) == c and removed < n:
                            removed += 1
                            continue
                        keep.append(x)
                    box[:] = keep
            w._norm()
            return {"code": 0, "raw": "", "message": "成功放入集卡册"}
        if path == EP_USER_LIST:
            return {"code": 0, "raw": "", "message": ""}
        return {"code": -1, "message": "仿真未实现 %s" % path}

    def _refine(self, kw):
        w = self.w
        target = int(kw.get("targetid") or 0)
        mats = []
        for i in (1, 2, 3):
            slot = kw.get(f"slot{i}")
            st = int(kw.get(f"slottype{i}") or 0)
            if slot is None:
                return {"code": -1, "message": "素材不全"}
            box = (w.change, w.store, w.simple)[st]
            slot = int(slot)
            hit = next((x for x in box if x["slot"] == slot), None)
            if hit is None:
                return {"code": -1, "message": "素材槽位不存在"}
            mats.append((box, hit))
        # 素材不能是同一张卡（真机按 slot 取，这里 slot 唯一即可）
        if len(w.stove) >= w.main_slots:
            return {"code": -1, "message": "炉位已满"}
        for box, hit in mats:
            box.remove(hit)
        w._norm()
        w.add_stove(target, left=1800, flag=1)
        return {"code": 0, "message": "炼制成功"}

    async def sell_card(self, slot, cid, box_type=1):
        w = self.w
        box = w.change
        hit = next((x for x in box if x["slot"] == int(slot)), None)
        if hit is None or int(hit["id"]) != int(cid):
            return {"ok": False, "message": "槽位不匹配"}
        if self.tid:
            # 卖完之后这张卡在本套里是否变成「一张都没有」= 它成了集齐缺口
            after = w.stock()
            after[int(cid)] -= 1
            if int(cid) in set_ids(self.tid) and after[int(cid)] < 1:
                self.sold_gap[int(cid)] += 1
        box.remove(hit)
        w._norm()
        w.gold += PRICE.get(int(cid), 0)
        w.sold[int(cid)] += 1
        return {"ok": True, "message": "卖出成功"}

    async def move_card(self, cid, slot, from_box=0):
        w = self.w
        if int(from_box) == 0:      # 换卡箱 → 保险箱
            if w.st_free() <= 0:
                self.denied_move += 1
                return {"ok": False, "message": "保险箱已满"}
            hit = next((x for x in w.change if x["slot"] == int(slot)), None)
            if hit is None:
                return {"ok": False, "message": "槽位不存在"}
            w.change.remove(hit)
            w.store.append({"id": hit["id"], "st": 1})
            w._norm()
            w.moved[int(hit["id"])] += 1
            return {"ok": True, "message": "移动成功"}
        # 保险箱 → 简易箱（真机只支持这种跨箱转移）
        if w.sp_free() <= 0:
            self.denied_move += 1
            return {"ok": False, "message": "简易箱已满"}
        hit = next((x for x in w.store if x["slot"] == int(slot)), None)
        if hit is None:
            return {"ok": False, "message": "槽位不存在"}
        w.store.remove(hit)
        w.simple.append({"id": hit["id"], "st": 2})
        w._norm()
        return {"ok": True, "message": "移动成功"}

    async def get_simple_box(self):
        w = self.w
        return {"ok": True, "cur": w.sp_cap, "max": w.sp_cap,
                "filled": len(w.simple), "expire_time": w.now + 20 * 86400,
                "cards": [{"slot": c["slot"], "id": c["id"], "end_time": 0}
                          for c in w.simple]}

    async def to_simple_box(self, cid, store_slot):
        """保险箱 → 简易箱（真机 card_user_temp_slot act=3 type=0）。"""
        w = self.w
        if w.sp_free() <= 0:
            return {"ok": False, "code": -1, "message": "简易箱已满，无处寄存"}
        hit = next((x for x in w.store if x["slot"] == int(store_slot)), None)
        if hit is None or int(hit["id"]) != int(cid):
            return {"ok": False, "code": -1, "message": "保险箱槽位不匹配"}
        w.store.remove(hit)
        w.simple.append({"id": hit["id"], "st": 2})
        w._norm()
        w.parked[int(cid)] += 1
        return {"ok": True, "code": 0, "message": "已寄存到简易箱"}

    async def open_pai(self):
        return {"ok": True, "message": "", "raw": "", "items": []}

    async def get_refined_card(self, slot):
        w = self.w
        # 炉内 slot == 列表下标（与 xml() 序列化一致）
        idx = int(slot)
        if not (0 <= idx < len(w.stove)):
            return {"ok": False, "message": "炉位没有卡"}
        hit = w.stove[idx]
        if w.ch_free() < 2:
            self.denied_take += 1
            return {"ok": False,
                    "message": "亲，可能会出现幸运事件，请空出两个卡箱的位置，再取卡！"}
        w.stove.pop(idx)
        w.add_any(int(hit["id"]), 1)
        return {"ok": True, "message": "取卡成功"}

    async def get_steal_card(self, slot, opuin):
        return {"ok": False, "message": "仿真不偷炉"}


# ---------------------------------------------------------------- 场景
def build_world(*, ch_cap=18, st_cap=168, sp_cap=15, stoves=5,
                owned: dict[int, int] | None = None,
                st_owned: dict[int, int] | None = None,
                sp_cards: list[int] | None = None,
                stove_cards: list[tuple[int, int, int]] | None = None,
                gold=200000) -> FakeWorld:
    """按张数精确铺场。owned/st_owned = {cid: 张数}（换卡箱 / 保险箱）。"""
    w = FakeWorld(ch_cap=ch_cap, st_cap=st_cap, sp_cap=sp_cap, stoves=stoves, gold=gold)
    for cid, n in (owned or {}).items():
        w.add_change(cid, n)
    for cid, n in (st_owned or {}).items():
        w.add_store(cid, n)
    for cid in (sp_cards or []):
        if w.sp_free() > 0:
            w.simple.append({"id": int(cid), "st": 2})
    for cid, left, flag in (stove_cards or []):
        w.add_stove(cid, left=left, flag=flag)
    w._norm()
    return w


async def run(w: FakeWorld, tid: int, ticks=25, *, dp=False, buy=True,
              interval=900, verbose=False) -> dict:
    """跑真实 tick() N 轮，返回观测指标。

    `interval` 是**虚拟秒/tick**：真机 45s，但 40 面要炼 1h(=80 tick)、150 面 4h，
    25 个 tick 根本等不到出炉，会把「正常等炉」误判成死锁。仿真按 15min/tick
    压缩等待，动作节奏（每 tick 一次买/取/投）与真机一致。
    """
    s = FakeSession(w, tid)
    ar = AutoRefine(s, [tid], buy=buy, max_rounds=0)
    ar.interval = interval
    ar.dp_mode = dp
    ar.steal_enabled = False
    ar.box_gift_change = False       # 礼物变卡是可选环节，与紧箱判据无关
    ar.gift_drain = False
    S = set_ids(tid)
    st = None
    hist = []
    t0 = time.time()
    for i in range(ticks):
        try:
            await ar.tick()
        except Exception as e:      # noqa: BLE001  仿真里任何异常都要看见
            return {"error": f"{type(e).__name__}: {e}", "tick": i}
        w.advance(interval)
        st = w.stock()
        missing = [c for c in S if st.get(c, 0) < 1]
        spent = Counter(s.calls)
        hist.append({
            "tick": i,
            "ch": len(w.change), "st": len(w.store), "sp": len(w.simple),
            "ch_free": w.ch_free(), "st_free": w.st_free(),
            "missing": len(missing),
            "collected": ar.stats["collected"],
            "refined": ar.stats["refined"],
            "bought": ar.stats["bought"],
            "sold": sum(w.sold.values()),
            "stove": len(w.stove),
            "logs": len(ar.log),
        })
        if verbose:
            h = hist[-1]
            print(f"    t{i:02d} ch={h['ch']:2d}/{w.ch_cap} st={h['st']:3d} "
                  f"缺{h['missing']:2d} 交{h['collected']:2d} 炼{h['refined']:2d} "
                  f"买{h['bought']:2d} 卖{h['sold']:2d} 炉{h['stove']}")
    last = hist[-1] if hist else {}
    # 关键日志（冻结/腾位不足/物理装不下/买卡失败），去重后给几条样本
    # 报错样本：先收「显式报告箱位不足」这类关键行，再补其他失败行 ——
    # 否则 4 条「移入保险箱失败」就把 5 个坑占满，真正的「需清卡箱」报告被挤掉，
    # 判定误判成 STUCK（静默死锁），而引擎其实报了（2026-11-05 D2）。
    KEY = ("箱位不足", "物理装不下", "腾位不足", "真的空间不足", "需清卡箱", "无卡可腾")
    seen, notes, key_notes = set(), [], []
    for e in ar.log:
        if e["ok"]:
            continue
        d = e["detail"][:120]
        k = d[:60]
        if k in seen:
            continue
        seen.add(k)
        (key_notes if any(x in d for x in KEY) else notes).append(d)
    notes = (key_notes + notes)[:6]
    return {
        "hist": hist,
        "last": last,
        "collected": ar.stats["collected"],
        "refined": ar.stats["refined"],
        "bought": ar.stats["bought"],
        "errors": ar.stats["errors"],
        "sold": dict(w.sold),
        "sold_gap": dict(s.sold_gap),
        "denied_take": s.denied_take,
        "denied_buy": s.denied_buy,
        "denied_move": s.denied_move,
        "notes": notes,
        "elapsed": time.time() - t0,
        "final_missing_ids": [c for c in S if w.stock().get(c, 0) < 1],
        "final_missing": [NAME.get(c, c) for c in S if w.stock().get(c, 0) < 1],
        "ch_free": w.ch_free(), "st_free": w.st_free(), "sp_free": w.sp_free(),
    }


# ---------------------------------------------------------------- 场景定义
def scenarios() -> list[dict]:
    """紧箱边界场景矩阵。每个场景 = 一个真实的边界形态 + 期望行为。

    好少年(tid326) 17 张 = 11 张合成卡（540×1 + 150×4 + 40×6）+ 6 张 10 面底卡
    （4934 大显身手 / 4933 消防少年 / 4932 智慧少年 / 4931 热心少年 /
      4930 神通广大 / 4929 一马当先）。
    """
    T = 326
    S = set_ids(T)
    SYN = S[:11]                 # 11 张合成卡
    B = S[11:]                   # 6 张 10 面底卡
    # 真实别主题卡（面值 > 10，才不会被当底卡卖）
    OTHER3 = {26275: 1, 26108: 1, 26099: 1}                       # 杌凳17/压水井141/露天竹床阵141
    OTHER8 = {26275: 1, 26108: 1, 26099: 1, 25403: 1, 25404: 1,
              25405: 1, 25406: 1, 25407: 1}                        # 凑满 8 格保险箱
    out = []

    def add(key, title, world_fn, expect, tid=T, ticks=25, dp=False):
        out.append(dict(key=key, title=title, world_fn=world_fn, expect=expect,
                        tid=tid, ticks=ticks, dp=dp))

    def syn(n=11):
        return {c: 1 for c in SYN[:n]}

    # ---- A 组：收尾集齐态（缺口全是可买底卡） ----
    add("A1", "理想紧箱：箱 13/18（无重复）、保险箱满、缺 4 张底卡",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1}, st_cap=8,
                            st_owned=dict(OTHER8)),
        "买 4 张 → 集齐 → 上交 → 续炼", ticks=30)

    add("A1b", "差 1 格：箱 15/18（含 2 张重复）、保险箱满、缺 4 张底卡",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1, SYN[5]: 1, SYN[6]: 1},
                            st_cap=8, st_owned=dict(OTHER8)),
        "卖重复卡腾位（有净收益）→ 买齐；不得卖单张底卡", ticks=25)

    add("A2", "事故现场：箱 18/18（2 重复 + 3 别主题）、保险箱满、缺 4 张底卡",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1, SYN[5]: 1, SYN[6]: 1,
                                   **OTHER3},
                            st_cap=8, st_owned=dict(OTHER8)),
        "报『物理装不下』且不空转（买-卖不循环）", ticks=25)

    # ---- B 组：两箱全满 ----
    add("B1", "两箱全满 + 缺 2 张底卡",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1, B[2]: 1, B[3]: 1,
                                   **OTHER3},
                            st_cap=8, st_owned=dict(OTHER8)),
        "冻结 + 报缺几格（不得卖底卡）", ticks=20)

    add("B2", "两箱全满 + 缺口含 2 张合成卡（素材已在箱）",
        lambda: build_world(owned={**syn(9), **{c: 1 for c in B}, **OTHER3},
                            st_cap=8, st_owned=dict(OTHER8)),
        "缺口含合成卡 → 必须放行投炉（不能冻结死锁）", ticks=25)

    add("B3", "换卡箱满 + 保险箱满 + 简易箱 15 格全空（有解）",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1, SYN[5]: 1, SYN[6]: 1,
                                   **OTHER3},
                            st_cap=8, st_owned=dict(OTHER8), sp_cards=[]),
        "有解：保险箱卡挪简易箱 → 换卡箱卡进保险箱 → 腾格买底卡", ticks=25)

    # ---- C 组：取卡边界 ----
    add("C1", "缺 4 底卡 + 换卡箱空 1 格 + 炉里 1 张多余卡待收",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1, OTHER3[26275]: 1},
                            st_cap=8, st_owned=dict(OTHER8),
                            stove_cards=[(SYN[5], 0, 2)]),
        "取卡要 2 格 → 只有 1 格时该延后、不能拆掉集齐窗口", ticks=25)

    add("C2", "炉里 4 张多余卡待收 + 箱内 14 张 + 缺 4 底卡（n_extra 逃生阀测试）",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1},
                            st_cap=8, st_owned=dict(OTHER8),
                            stove_cards=[(26275, 0, 2), (26108, 0, 2),
                                         (26099, 0, 2), (25403, 0, 2)]),
        "取 4 张多余卡会把买底卡的格位吃光 → 应延后或取后立刻腾位", ticks=25)

    add("C3", "取卡自愈：箱 15/18、炉里 2 张待收",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1},
                            st_cap=8, st_owned=dict(OTHER8),
                            stove_cards=[(SYN[5], 0, 2), (SYN[6], 0, 2)]),
        "腾位 → 取卡 → 释放炉位", ticks=25)

    # ---- D 组：投炉素材买不进来 ----
    add("D1", "换卡箱空 1、缺 1 张 40 面（要买 2 张底卡才能炼）",
        lambda: build_world(owned={**syn(8), B[0]: 1, B[1]: 1, B[2]: 1, B[3]: 1,
                                   B[4]: 1, OTHER3[26275]: 1},
                            st_cap=8, st_owned=dict(OTHER8)),
        "投炉要 2 格、只剩 1 格 → 先腾位（别主题移保险箱失败则卖重复）再投", ticks=25)

    add("D2", "两箱全满、炉空、缺口含合成卡（真死锁形态）",
        lambda: build_world(owned={**syn(9), B[0]: 1, B[1]: 1, **OTHER3,
                                   25403: 1, 25404: 1, 25405: 1, 25406: 1,
                                   25407: 1},
                            st_cap=8, st_owned=dict(OTHER8)),
        "必须明确报『需清卡箱』，不能反复买失败", ticks=20)

    # ---- E 组：其他星数（空箱起步，验证不被紧箱逻辑误伤） ----
    add("E1", "1★ 85 空箱起步",
        lambda: build_world(owned={}, st_owned={}),
        "从零开炼，正常滚动", tid=85, ticks=30)

    add("E2", "3★ 1223 空箱起步",
        lambda: build_world(owned={}, st_owned={}),
        "从零开炼，正常滚动", tid=1223, ticks=30)

    # ---- F 组：DP 路径 ----
    add("F1", "事故现场（同 A2）走 DP 调度",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1, SYN[5]: 1, SYN[6]: 1,
                                   **OTHER3},
                            st_cap=8, st_owned=dict(OTHER8)),
        "DP 下同样不得空转/超炼", ticks=20, dp=True)

    add("F2", "理想紧箱（同 A1）走 DP",
        lambda: build_world(owned={**syn(), B[0]: 1, B[1]: 1}, st_cap=8,
                            st_owned=dict(OTHER8)),
        "DP 下能集齐上交", ticks=30, dp=True)
    return out


# ---------------------------------------------------------------- 判定
def judge(r: dict, sc: dict) -> tuple[str, str]:
    """返回 (状态, 说明)。

    状态：
      OK     有进展（买/炼/交 至少一项在动，缺口在减）
      DONE   已集齐并上交
      SPIN   买了又卖，缺口一张没少（净收益 0 的空转）
      SELLGAP 卖掉了「集齐还缺的底卡」—— 卖一张缺口+1，永远收敛不了
      FROZEN 零动作，但有显式的「箱位不足/物理装不下」报告（预期内，等人工清箱）
      STUCK  零动作且没有任何可执行报告（静默死锁，最坏）
      BUG    抛异常
    """
    if r.get("error"):
        return "BUG", "异常：" + r["error"]
    h = r["hist"]
    if not h:
        return "BUG", "没有 tick"
    d_bought = h[-1]["bought"] - h[0]["bought"]
    d_missing = h[0]["missing"] - h[-1]["missing"]
    d_sold = h[-1]["sold"] - h[0]["sold"]
    d_coll = h[-1]["collected"] - h[0]["collected"]
    d_ref = h[-1]["refined"] - h[0]["refined"]
    d_stove = h[-1]["stove"] - h[0]["stove"]

    # ① 卖了「集齐还缺的卡」= 结构性错误（净收益 0，永远收敛不了）
    sold_gap = set((r.get("sold_gap") or {}).keys())
    if sold_gap:
        return "SELLGAP", ("卖掉了还缺的底卡：" +
                           "、".join(NAME.get(c, str(c)) for c in sold_gap))

    if d_coll > 0:
        return "DONE", f"集齐并上交 {d_coll} 次，继续滚动（买 {d_bought} 炼 {d_ref}）"
    if d_bought >= 4 and d_missing <= 0:
        return "SPIN", f"买 {d_bought} 次、卖 {d_sold} 次，缺口一张没少"
    if d_missing > 0:
        return "OK", f"缺口 {h[0]['missing']}→{h[-1]['missing']}，买 {d_bought} 炼 {d_ref}"
    if d_bought or d_ref or d_stove:
        return "OK", f"买 {d_bought} 炼 {d_ref}（缺口未变，仍在滚）"
    # 零动作：区分「显式冻结」与「静默死锁」
    txt = " ".join(r.get("notes") or [])
    if any(k in txt for k in ("箱位不足", "物理装不下", "腾位不足", "真的空间不足",
                              "需清卡箱", "无卡可腾")):
        return "FROZEN", f"{len(h)} tick 零动作，已显式报箱位不足（缺 {h[-1]['missing']} 张）"
    return "STUCK", f"{len(h)} tick 零动作、无报告，还缺 {h[-1]['missing']} 张"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=0, help="覆盖每个场景的 tick 数")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-k", "--keys", default="", help="只跑指定场景，逗号分隔")
    args = ap.parse_args()
    only = {k.strip().upper() for k in args.keys.split(",") if k.strip()}
    if not args.verbose:
        logging.disable(logging.INFO)      # engine_logger 会往 stdout 打，静音后只看结果表

    rows = []
    for sc in scenarios():
        if only and sc["key"] not in only:
            continue
        w = sc["world_fn"]()
        n = args.ticks or sc["ticks"]
        if args.verbose:
            print(f"\n=== {sc['key']} {sc['title']}")
        r = await run(w, sc["tid"], ticks=n, dp=sc["dp"], verbose=args.verbose)
        st, why = judge(r, sc)
        rows.append((sc, r, st, why))
        mark = {"OK": "✅", "DONE": "🎉", "SPIN": "♻️", "SELLGAP": "🔻",
                "FROZEN": "⛔", "STUCK": "🔒", "BUG": "💥"}[st]
        print(f"{mark} {sc['key']:<3} {sc['title'][:42]:<44} "
              f"买{r.get('bought', 0):<3} 炼{r.get('refined', 0):<3} "
              f"交{r.get('collected', 0):<2} 卖{sum((r.get('sold') or {}).values()):<3} "
              f"终箱{r.get('ch_free')}/{r.get('st_free')} | {why}")

    bad = [x for x in rows if x[2] in ("SPIN", "SELLGAP", "STUCK", "BUG")]
    print("\n" + "=" * 78)
    print(f"共 {len(rows)} 个场景，异常 {len(bad)} 个")
    for sc, r, st, why in bad:
        print(f"\n【{sc['key']}】{sc['title']}")
        print(f"  期望：{sc['expect']}")
        print(f"  实际：{why}")
        for nt in (r.get("notes") or [])[:4]:
            print(f"    · {nt}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
