"""全量模拟自动练卡：把每个星级的套都走一遍「从头炼起」和「卡箱有卡」两个场景。

与既有 box_gift_sim.py 的区别：
  - 那个只模拟变卡环节；本脚本复刻**整个 tick**（收卡 → 变卡 → 炼卡 → 上交 → 补底卡），
    跑的是**真实 AutoRefine.tick()**，不是另写一份逻辑。
  - 输出是每个套能不能收敛（集齐并上交）、排产顺序对不对、有没有囤卡。

假服务端 SimWorld 复刻真机行为：
  - 主炉 5 位（不是 7），炉内卡到点自动 flag=2 可收；
  - 炼卡成功 → 素材从箱里扣、成品进炉；素材不足（服务端会拒）→ code=-1002；
  - 变卡成败**都烧**源卡（真机实测）；
  - 上交 → 整套卡清空；买卡 → 底卡进箱。

用法：
    python tools/auto_sim_all.py                # 按星级抽样 + 主线 1810
    python tools/auto_sim_all.py --all          # 全部 1681 套（静态层 + 抽样动态层）
    python tools/auto_sim_all.py --tid 1810     # 只看某一套的完整时间线
    python tools/auto_sim_all.py --dynamic 0    # 只跑静态层（快）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

from backend import gift as gf                        # noqa: E402
from backend.auto_refine import (                     # noqa: E402
    AutoRefine, ThemePlan, parse_full, face40_gap, card_name, _maps)
from backend.constants import (                       # noqa: E402
    EP_MAINPAGE, EP_REFINE, EP_REFINED_GET, EP_BUY,
    EP_COLLECTION_ADD, EP_PAI_OPEN, EP_TRANSFER)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name + (f"｜{extra}" if extra else ""))
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))


# ----------------------------------------------------------------- 卡库索引
_CARDS, _COMP, _THEMES = _maps()
_GIFT48 = gf._face48_ids()


def face_of(cid: int) -> int:
    c = _CARDS.get(int(cid))
    return int(c[3] or 0) if c else 0


def secs_of(cid: int) -> int:
    c = _COMP.get(int(cid))
    return int(c[6] or 0) if c else 0


def theme_star(tid: int) -> int:
    t = _THEMES.get(int(tid))
    return int(t[2] or 0) if t else -1


def theme_name(tid: int) -> str:
    t = _THEMES.get(int(tid))
    return str(t[1]).strip() if t else f"?{tid}"


def n_base(plan: ThemePlan) -> int:
    """这套里「没有配方、只能买」的卡有几张 —— 它们全要挤在换卡箱的 18 格里。"""
    return sum(1 for c in plan.ids if int(c) not in plan.comp)


def all_themes() -> list[int]:
    """_maps() 的第三张表已经是 {tid: row}（不是列表）。"""
    return sorted(int(t) for t in _THEMES)


# ------------------------------------------------------------------ 假服务端
class SimWorld:
    """真机行为的假服务端：只认端点名与参数，不认识引擎内部逻辑。"""

    MAIN_STOVE = 5          # 主炉实际上限 5（parse_full 注释：stovebox cur=7 含偷炉格）
    # 2026-09-26 真机一次性读到的真实容量（tools/.live_boxes.py）：
    #   changebox cur=18 max=18  ← 买卡**落在这一箱**（auto_refine.py:5 实测注明），18 格是硬顶
    #   storebox   cur=168 max=200 ← 保险箱很大，成品卡全丢这
    #   stovebox   cur=7  max=7
    # 之前把两个箱子都当成 18 格，会低估换卡箱的压力、也错判过「卡箱满」。
    MAX_BOX = 18
    MAX_STORE = 168

    def __init__(self, cards: list[tuple[int, int]] | None = None,
                 stove: list[tuple[int, int]] | None = None,
                 now: float = 0.0, ok_seq: list[bool] | None = None,
                 tick_secs: int = 900):
        # cards: [(cid, slottype=0|1), ...]
        self.uin = "10001"                      # 引擎拿它判断「是不是自己炉上的卡」
        self.now = now
        self.tick_secs = tick_secs
        self.ok_seq = list(ok_seq or []) or [True] * 99
        self._transfer_n = 0
        self.next_slot = 1
        self.cards: list[dict] = []
        for cid, st in (cards or []):
            self.cards.append({"id": int(cid), "slottype": int(st), "slot": self._slot()})
        # stove: [(cid, slottype)] —— slottype 沿用素材来源箱子
        self.stoves: list[dict] = []
        for cid, st in (stove or []):
            self.stoves.append({"id": int(cid), "flag": 0, "ready_at": now + secs_of(cid),
                                "slot": 0, "stove": 0})
        self.ops: Counter = Counter()          # 端点调用次数（诊断用）
        self.codes: Counter = Counter()        # 返回的非零 code（诊断用）
        self.refined: list[int] = []           # 炼出的卡 id（顺序）
        self.collected: list[int] = []         # 上交的 theme
        self.bought: Counter = Counter()
        self.changed: Counter = Counter()      # 变卡产出 {dst: n}

    def _slot(self) -> int:
        s = self.next_slot
        self.next_slot += 1
        return s

    # ---------------------------------------------------------- XML 组装
    def _box(self, box: list[dict], mx: int) -> str:
        """占位已删除，真实拼接见 _mk()。"""

    def _raw(self) -> str:
        """按当前状态生成 mainpage XML（炉内到点的卡自动升级成 flag=2）。"""
        ch = [c for c in self.cards if c["slottype"] == 0]
        st = [c for c in self.cards if c["slottype"] == 1]
        live = [c for c in self.stoves if c["flag"] == 0 and self.now >= c["ready_at"]]
        done = [c for c in self.stoves if c["flag"] == 0 and self.now < c["ready_at"]]
        for c in done:
            c["flag"] = 2                        # 到点 → 可收
        stove_items = []
        for c in live + [x for x in self.stoves if x["flag"] == 2]:
            stove_items.append(f'<card slot="{c["slot"]}" id="{c["id"]}" flag="2" stove="0"/>')
        free = self.MAIN_STOVE - len(self.stoves)
        for i in range(max(0, free)):
            stove_items.append(f'<card slot="0" id="0"/>')
        stove_items += ['<card slot="0" id="0" slottype="steal"/>']   # 偷炉位
        return ("<qqhome><user randchance=\"0\"/>" +
                self._mk("changebox", ch, self.MAX_BOX, self.MAX_BOX) +
                self._mk("storebox", st, self.MAX_STORE, 200) +
                f'<stovebox cur="7" max="7" used="{len(self.stoves)}">'
                  + "".join(stove_items) + "</stovebox>"
                + "</qqhome>")

    @staticmethod
    def _mk(name: str, items: list[dict], cur: int, mx: int | None = None) -> str:
        body = "".join(f'<card slot="{c["slot"]}" id="{c["id"]}"/>'
                       for c in sorted(items, key=lambda x: x["slot"]))
        return (f'<{name} cur="{cur}" max="{mx or cur}" used="{len(items)}">'
                f'{body}</{name}>')

    # ---------------------------------------------------------- 端点分派
    async def post(self, endpoint: str, **params) -> dict:
        self.ops[endpoint] += 1
        if endpoint == EP_MAINPAGE:
            return {"code": 0, "raw": self._raw()}
        if endpoint == EP_REFINE:
            return self._refine(params)
        if endpoint == EP_REFINED_GET:
            return self._get_card(params)
        if endpoint == EP_BUY:
            return self._buy(params)
        if endpoint == EP_COLLECTION_ADD:
            return self._collect(params)
        if endpoint == EP_PAI_OPEN:
            return {"code": -1, "message": "system busy"}    # 模拟：没有待翻牌
        return {"code": -1, "message": f"未模拟端点 {endpoint}", "raw": ""}

    async def qzone(self, method: str, endpoint: str, **params) -> dict:
        self.ops[endpoint] += 1
        if endpoint != EP_TRANSFER:
            return {"code": -1, "raw": ""}
        return self._transfer(params)

    # --------------------------------- tick() 会直接调的门面方法（非端点名）
    async def get_refined_card(self, slotid: int, slottype: int = 1) -> dict:
        return await self.post(EP_REFINED_GET, slotid=slotid, slottype=slottype,
                               ver=1, code="")

    async def get_steal_card(self, slotid: int, opuin: str = "") -> dict:
        return await self.post(EP_REFINED_GET, slotid=slotid, slottype=1, ver=1, code="")

    async def open_pai(self) -> dict:
        return await self.post(EP_PAI_OPEN, type=1, ver=1, code="")

    # ------------------------------------------------------------ 各端点
    def _refine(self, p: dict) -> dict:
        """炼卡：服务端校验素材槽是否真的躺在箱里、面额是否对得上（否则拒）。"""
        target = int(p.get("targetid") or 0)
        mats = _comp_mats(target)
        picked: list[dict] = []
        for i, want in enumerate(mats):
            slot = p.get(f"slot{i + 1}")
            st = p.get(f"slottype{i + 1}")
            if slot is None or st is None or int(slot) == 0:
                self.codes[EP_REFINE] += 1
                return {"code": -1002, "message": "系统繁忙，请稍后再试!"}
            hit = [c for c in self.cards
                   if c["slot"] == int(slot) and c["id"] == want
                   and (c["slottype"] == 0 if int(st) == 0 else c["slottype"] == 1)]
            if not hit:
                self.codes[EP_REFINE] += 1
                return {"code": -1002, "message": "系统繁忙，请稍后再试!"}
            picked.append({"slot": int(slot), "slottype": int(st), "cid": want})
        for m in picked:
            self.cards = [c for c in self.cards if not (c["slot"] == m["slot"]
                                                        and c["id"] == m["cid"])]
        self.stoves.append({"id": target, "flag": 0, "ready_at": self.now + secs_of(target),
                            "slot": 0, "stove": 0, "slottype": picked[0]["slottype"]
                            if picked else 0})
        self.refined.append(target)
        return {"code": 0, "message": "炼卡成功", "raw": "<qqhome code=\"0\"/>"}

    def _get_card(self, p: dict) -> dict:
        slot = int(p.get("slotid") or 0)
        idx = -1
        for i, c in enumerate(self.stoves):
            if c["flag"] == 2 and (c["slot"] == slot or slot == 0):
                idx = i
                break
        if idx < 0:
            self.codes[EP_REFINED_GET] += 1
            return {"code": -33011, "message": "卡片尚未炼制完。"}
        c = self.stoves.pop(idx)
        # 炼好的成品卡进**保险箱**（slottype=1）：换卡箱是「待用素材区」，
        # 真机取卡回来的成品不会把待用区塞满 —— 否则换卡箱满 → 买不进底卡 →
        # 整套齐了也上不了交（这个死锁在模拟里真实发生过了）。
        self.cards.append({"id": c["id"], "slottype": 1, "slot": self._slot()})
        return {"code": 0, "message": "取卡成功", "raw": "<qqhome code=\"0\"/>"}

    def _buy(self, p: dict) -> dict:
        """买卡落地 = 换卡箱（slottype=0），**满 18 格就买不进来**。"""
        cid = int(p.get("card_id") or 0)
        if sum(1 for c in self.cards if c["slottype"] == 0) >= self.MAX_BOX:
            self.codes[EP_BUY] += 1
            return {"code": -1002, "message": "卡箱已满，请先整理"}
        self.cards.append({"id": cid, "slottype": 0, "slot": self._slot()})
        self.bought[cid] += 1
        return {"code": 0, "message": "购买成功", "raw": "<qqhome code=\"0\"/>"}

    def _collect(self, p: dict) -> dict:
        tid = int(p.get("theme") or 0)
        self.collected.append(tid)
        return {"code": 0, "message": "成功放入集卡册", "raw": "<qqhome code=\"0\"/>"}

    def _transfer(self, p: dict) -> dict:
        """变卡：成败**都**烧源卡（真机实测）。"""
        self._transfer_n += 1
        idx = -1
        for i, c in enumerate(self.cards):
            if c["slot"] == p.get("slotid"):
                idx = i
                break
        src = self.cards.pop(idx) if idx >= 0 else None
        ok = self._transfer_n <= len(self.ok_seq) and self.ok_seq[self._transfer_n - 1]
        dst = int(p.get("dstid") or 0)
        if ok and dst:
            self.cards.append({"id": dst, "slottype": 0, "slot": self._slot()})
            self.changed[dst] += 1
        return {"code": 0,
                "raw": '<qqhome code="0" result="1"/>' if ok else '<qqhome code="0" result="0"/>',
                "message": ""}

    # ------------------------------------------------------------ 场景构造
    def stock(self) -> Counter:
        c = Counter()
        for x in self.cards:
            c[x["id"]] += 1
        return c

    def pending(self) -> Counter:
        c = Counter()
        for x in self.stoves:
            c[x["id"]] += 1
        return c

    def advance(self) -> None:
        """推进一个 tick 的时间。"""
        self.now += self.tick_secs


def _comp_mats(cid: int) -> list[int]:
    c = _COMP.get(int(cid))
    return [int(m) for m in (c[3], c[4], c[5]) if m] if c else []




# ------------------------------------------------------------ 场景构造器
def scene_blank(plan: ThemePlan) -> tuple[list, list]:
    """场景 A：从头炼起 —— 卡箱一张卡都没有。"""
    return [], []


def scene_seeded(plan: ThemePlan, gift_n: int = 4, seed_ratio: float = 0.5
                 ) -> tuple[list, list]:
    """场景 B：卡箱有卡 —— 礼物源卡若干张 + 手上已有的中间面额卡。

    seed_ratio：套内卡按「整套需求量 × 该比例」播种，模拟「玩到一半手里有货」。
    礼物卡固定放换卡箱（slottype=0），其余放保险箱（slottype=1）。
    """
    cards: list[tuple[int, int]] = []
    # 礼物源卡只有在这种「确实需要一张 40 面卡」时才塞 —— 变卡只出 40 面卡，
    # 全套都是底卡的套（1570/742/1802 那类）拿到礼物卡只是白占换卡箱格位。
    if any(face_of(int(c)) == 40 for c in plan.ids):
        for cid in sorted(gf._face48_ids())[:gift_n]:
            cards.append((cid, 0))                   # 礼物源卡 → 换卡箱
    # 中间面额卡（有配方、不是底卡）：按整套需求的一部分播种，模拟「玩到一半手里有货」
    inner = [int(c) for c in plan.ids
             if _COMP.get(int(c)) and plan.depth.get(int(c), 0) <= 2]
    if not inner:
        # 整套没有配方的套：那就播它自己的底卡（就这么个「玩到一半」的样子）
        inner = [int(c) for c in plan.ids]
    for cid in inner:
        need = int(plan.demand.get(cid, 0) or 0) or 1
        for _ in range(max(1, round(need * seed_ratio))):
            cards.append((cid, 1))                   # 保险箱
    return cards, []


# ------------------------------------------------------------------ 模拟跑
async def run_sim(tid: int, scene: str, max_ticks: int = 400,
                  verbose: bool = False, trace: bool = False) -> dict:
    """跑一个完整的炼卡闭环，返回诊断信息。"""
    plan = ThemePlan(tid)
    if scene == "blank":
        cards, stove = scene_blank(plan)
    else:
        cards, stove = scene_seeded(plan)

    world = SimWorld(cards=cards, stove=stove, now=1727366400, tick_secs=900)
    eng = AutoRefine(world, [tid])
    eng.buy = True
    eng.gift_drain = False
    eng.box_gift_change = True
    eng.steal_enabled = False                      # 偷炉需要好友，模拟里不涉及
    eng.max_rounds = 1                             # 只集 1 轮就够

    timeline: list[dict] = []
    first_refine_tick = None
    first_orders: list[int] = []        # 第一个炼卡 tick 的开工序列（面额，用于验排产）
    n_tick = 0
    for t in range(1, max_ticks + 1):
        n_tick = t
        n_before = len(world.refined)          # 必须在 tick() **之前**取，否则减出来恒为空
        steps = await eng.tick()
        acted = [s for s in steps if s.get("action") in ("炼卡", "买卡", "上交集卡册", "收卡")]
        if verbose:
            for s in acted:
                print(f"    t{t} {s['action']}: {s['detail']}")
        if any(s["action"] == "炼卡" for s in steps):
            if first_refine_tick is None:
                first_refine_tick = t
                # 只取**第一个**炼卡 tick 的开工序列；放在 if 外会被后面每个
                # 炼卡 tick 覆盖，量出来的是「最后一炉炼了什么」（会误判囤卡）。
                first_orders = world.refined[n_before:]
        if any(s["action"] == "上交集卡册" for s in steps):
            timeline.append({"tick": t, "done": True})
            break
        world.advance()
    else:
        timeline.append({"tick": max_ticks, "done": False})       # for-else：没上交
    if not any(x["done"] for x in timeline):
        timeline.append({"tick": n_tick, "done": False})

    # 统计：收敛用了几个 tick
    return {
        "tid": tid, "name": plan.name, "star": theme_star(tid),
        "scene": scene, "done": bool(world.collected), "ticks": n_tick,
        "rounds": plan.rounds, "refined": len(world.refined),
        "bought": sum(world.bought.values()),
        "orders": [f"{face_of(c)}面" for c in first_orders] or [],
        "kept40": int(sum(1 for c in world.cards if face_of(c["id"]) == 40)),
        "kept_total": len(world.cards),
        # 每种 40 面卡「炼出几张」vs「整套需求量」—— 炼超了就是囤卡
        "ref40": {plan.cname(k): v for k, v in
                  Counter(c for c in world.refined if face_of(c) == 40).items()},
        "need40": {plan.cname(k): int(v) for k, v in plan.demand.items()
                   if face_of(int(k)) == 40},
        "chg40": dict(world.changed),
        "seed40": int(sum(1 for c, _ in cards if face_of(c) == 40)),
        "face40_credit": {plan.cname(k): v for k, v in
                          (eng.face40_credit.get(tid) or {}).items()},
        "codes": dict(world.codes),
        "stock": {card_name(k): v for k, v in world.stock().items()},
    }


# --------------------------------------------------------------- 静态扫描层
def static_scan(tids: list[int]) -> dict:
    """不跑循环，只验「从头炼起」的净需求是否闭合（能不能炼到顶层）。"""
    bad: list[str] = []
    no40: list[int] = []
    for tid in tids:
        try:
            p = ThemePlan(tid)
        except Exception as e:                       # noqa: BLE001
            bad.append(f"{tid}: {e}")
            continue
        # 从零库存展开：需求应该最终全部落在「买底卡」上
        runs, buy = p.net_plan(Counter(), Counter())
        # 顶层卡必须被炼出来（或者已经在库里）
        top = [int(c) for c in p.ids if not _COMP.get(int(c)) or int(p.comp[c][6]) > 0]
        top = [int(c) for c in p.ids if int(p.cards[c][3] or 0) >= 40 or not _COMP.get(int(c))]
        if not top:
            continue
        if not any(int(p.cards[c][3] or 0) >= 40 for c in p.ids):
            no40.append(tid)
        # 底卡必须全是套内卡里「没有配方」的那些（否则会永远炼不出来）
        missing = [c for c in p.ids if c not in p.comp and c not in buy]
        if missing:
            bad.append(f"{tid}: 底卡买不到 {[card_name(c) for c in missing][:3]}")
    return {"bad": bad, "no40": no40}


# ------------------------------------------------------------------ 主流程
def pick_samples() -> list[int]:
    """按星级抽样：每星取若干 + 主线 1810。"""
    by_star: dict[int, list[int]] = defaultdict(list)
    for t in all_themes():
        by_star[theme_star(t)].append(t)
    rnd = random.Random(20260926)
    out = [1810]                                     # 用户主线
    for star in sorted(by_star):
        pool = sorted(by_star[star])
        take = min(len(pool), 4 if star >= 4 else 3)
        out += sorted(rnd.sample(pool, take))
    return sorted(set(out))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tid", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--big", action="store_true",
                    help="只跑「套内底卡 > 18 张」的套：买卡落在换卡箱，"
                         "而换卡箱只有 18 格，这类套就是重灾区")
    ap.add_argument("--dynamic", type=int, default=1, help="1=跑动态闭环，0=只静态")
    args = ap.parse_args()

    themes = all_themes()
    print(f"卡库主题总数：{len(themes)}")

    # ---- 静态层：全量 ----
    print("\n" + "=" * 78)
    print("【静态层】全量 1681 套：从零展开的净需求是否闭合（能不能炼到顶层）")
    print("=" * 78)
    scan = static_scan(themes)
    check("全量套的配方树都闭合（没有「买不到 / 炼不出来」的卡）", not scan["bad"],
          f"{len(scan['bad'])} 项异常" + (": " + "; ".join(scan["bad"][:3]) if scan["bad"] else ""))
    n40 = len(scan["no40"])
    print(f"    没有 40 面卡的套：{n40} 个（这些套的炼卡路径是另一条，正常）")

    if not args.dynamic:
        print(f"\n===== {len(PASS)} PASS / {len(FAIL)} FAIL =====")
        return

    # ---- 动态层：抽样 ----
    if args.tid:
        tids = [args.tid]
    elif args.all:
        tids = themes
    elif args.big:
        tids = [t for t in themes if n_base(ThemePlan(t)) > SimWorld.MAX_BOX]
    else:
        tids = pick_samples()
    print("\n" + "=" * 78)
    print(f"【动态层】抽样 {len(tids)} 套 × 2 场景（从头炼起 / 卡箱有卡），跑真实 tick 闭环")
    print("=" * 78)

    for scene in ("blank", "seeded"):
        print(f"\n----- 场景 {'A 从头炼起' if scene == 'blank' else 'B 卡箱有卡'} -----")
        rows = []
        for tid in tids:
            r = await run_sim(tid, scene, verbose=bool(args.tid))
            rows.append(r)
            tag = "✓" if r["done"] else "✗"
            print(f"  {tag} {r['tid']:<6} 星{r['star']} {r['name']:<8} "
                  f"{'收敛' if r['done'] else '未收敛'} tick={r['ticks']:<4} "
                  f"炼{r['refined']:<4} 买{r['bought']:<4} "
                  f"余卡{r['kept_total']:<3} 余40面{r['kept40']}")
        ok_cnt = sum(1 for r in rows if r["done"])
        check(f"[{scene}] 每套都能收敛（集齐并上交集卡册）", ok_cnt == len(rows),
              f"{ok_cnt}/{len(rows)} 收敛")
        if scene == "seeded":
            # 用户 2026-09-26 的质疑：「明明只可以直接开始炼 80 的」→
            # 箱里有 40 面卡时，第一个开工的炉次里必须出现 ≥80 面卡。
            # 一开局就集齐、全程一炉没开的那些套，这条不适用（播种给够了）。
            refined_rows = [r for r in rows if r["refined"] > 0]
            check("[seeded] 开工的套第一炉就炼 ≥80 面卡（手上的 40 面卡直接喂给高面额）",
                  all(any(int(o.split("面")[0]) >= 80 for o in r["orders"])
                      for r in refined_rows),
                  str({r["tid"]: r["orders"] for r in refined_rows
                       if not any(int(o.split("面")[0]) >= 80 for o in r["orders"])}))
            # 「箱里的 40 面卡变多了」本身不是罪（变卡本来就产 40 面卡）；
            # 真正的囤卡是：**炼出来的** 40 面卡超过这套的需求量（同款炼了两张）。
            over = {}
            for r in rows:
                for name, n in r["ref40"].items():
                    need = r["need40"].get(name, 0)
                    if n > need:
                        over.setdefault(r["tid"], {})[name] = f"炼{n}张/需{need}张"
            check("[seeded] 每种 40 面卡炼出数不超过整套需求量（不囤同款）",
                  not over, str(over))
        if args.tid:
            print(json.dumps(rows[0], ensure_ascii=False, indent=2))

    print(f"\n===== {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAIL: " + f)


if __name__ == "__main__":
    asyncio.run(main())
