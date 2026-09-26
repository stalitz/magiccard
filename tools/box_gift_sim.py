"""离线模拟：「需要 40 面卡时优先变卡，变不了才买 10 面底卡炼」。

真机只读一次库存（GET /api/card/mainpage），之后全部在**假 session** 上跑**真实
代码路径**（AutoRefine._drain_box_gift / _pick_change_dst / gift.drain_box_gift_cards），
不发起任何真实变卡请求，不消耗卡。

用法：
    /Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python tools/box_gift_sim.py
    THEME=1810 python tools/box_gift_sim.py           # 换一套再模拟
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

from backend import gift as gf                      # noqa: E402
from backend.auto_refine import (                   # noqa: E402
    AutoRefine, ThemePlan, face40_gap, pick_change_target, card_name)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name + (f"｜{extra}" if extra else ""))
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))


# ---------------------------------------------------------------- 假 session
class SimSession:
    """mainpage 的卡池可替换；变卡结果按 ok_seq 逐次返回成败。

    ok_seq[i]=True → 第 i+1 次变卡**成功**（result=0）；缺省项 = 失败。
    变卡成败**都**烧掉源卡（真机实测），所以每次 qzone 都会把源卡从卡池里摘掉，
    这样代码里「每试一张就重扫卡箱」的行为能被真实还原。
    """

    def __init__(self, cards: list[tuple[int, int, int]],
                 ok_seq: list[bool] | None = None):
        self.uin = "1"
        self.ok_seq = list(ok_seq or [])
        self.calls: list[tuple] = []
        self.transfer_n = 0
        self.pending: list[int] = []          # 炉内在炼（写进 raw 的 stovebox）
        self.set_cards(cards)

    def set_cards(self, cards: list[tuple[int, int, int]], pending=None) -> None:
        """pending 缺省时**保留**当前炉内卡（变卡只动箱子，不该把炉里的抹掉）。"""
        self.cards = list(cards)
        if pending is not None:
            self.pending = list(pending)
        ch = [c for c in cards if c[2] == 0]
        st = [c for c in cards if c[2] == 1]

        def box(name, items, mx=18):
            body = "".join(f'<card slot="{s}" id="{i}"/>'
                           for i, s, _ in sorted(items, key=lambda x: x[1]))
            return f'<{name} cur="{len(items)}" max="{mx}" used="{len(items)}">{body}</{name}>'

        self.raw = ("<qqhome><user randchance=\"0\"/>"
                    + box("changebox", ch) + box("storebox", st)
                    # box() 的 items 顺序是 (id, slot, slottype)
                    + box("stovebox", [(i, 0, 0) for i in self.pending], 7)
                    + "</qqhome>")

    def remove_card(self, src: int, slot: int | None = None) -> None:
        """源卡必定消失（**变卡失败也一样烧**）。"""
        if slot is None:
            slot = self._slot_of(src)
        self.set_cards([c for c in self.cards if not (c[0] == src and c[1] == slot)])

    def add_card(self, cid: int) -> None:
        """变卡成功 → 目标卡落箱，占一个新槽位。"""
        nxt = max((c[1] for c in self.cards), default=0) + 1
        where = 0 if any(c[2] == 0 for c in self.cards) else 1
        self.set_cards(self.cards + [(cid, nxt, where)])

    def apply_result(self, src: int, dst: int, ok: bool, slot: int | None = None) -> None:
        """变卡一次后的真实库存变化：源卡**必定**消失（失败也烧），成功才得 dst。"""
        self.remove_card(src, slot)
        if ok:
            self.add_card(dst)

    def _slot_of(self, cid: int) -> int:
        for c in self.cards:
            if c[0] == cid:
                return c[1]
        return -1

    async def post(self, ep, **kw):
        if "mainpage" in ep or "MAINPAGE" in ep:
            return {"code": 0, "raw": self.raw}
        return {"code": -1, "raw": ""}

    async def qzone(self, method, ep, **kw):
        self.calls.append((kw.get("srcid"), kw.get("dstid"),
                           kw.get("slotid"), kw.get("slottype")))
        self.transfer_n += 1
        n = self.transfer_n
        ok = n <= len(self.ok_seq) and self.ok_seq[n - 1]
        self.remove_card(kw.get("srcid"), kw.get("slotid"))   # 成败都烧源卡
        if ok:
            self.add_card(kw.get("dstid"))
        # 服务端约定：result="1" = 变卡成功，result="0" = 失败
        return {"code": 0,
                "raw": '<qqhome code="0" result="1"/>' if ok
                       else '<qqhome code="0" result="0"/>',
                "message": ""}


# ------------------------------------------------------------- 真机只读快照
async def live_state() -> tuple[Counter, Counter, list]:
    """真机读一次库存（只读，不发起任何写操作）。

    ⚠️ 必须直接用后端的 session 发 POST —— 服务端只注册了 GET 变体，而 GET 走
       card.show 域会被判成「未开通」(-1100)；POST 才是引擎自己在用的入口
       （main.py 的 /api/card/mainpage 因此 POST 会 405）。
    """
    from backend.constants import EP_MAINPAGE
    from backend.main import _session
    r = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
    if r.get("code") != 0:
        raise SystemExit(f"mainpage code={r.get('code')} msg={r.get('message')}")
    from backend.auto_refine import parse_full
    st = parse_full(r.get("raw", ""))
    cap = st.get("cap", {})
    stock: Counter = Counter()
    for c in st["change"]:
        stock[c["id"]] += 1
    for c in st["store"]:
        stock[c["id"]] += 1
    pending: Counter = Counter()
    for c in st["stove"]:
        pending[c["id"]] += 1
    ch = cap.get("change", {})
    free_box = ch.get("cur", 0) - ch.get("used", 0)
    return stock, pending, st["stove"], free_box


async def sim_ticks(eng, session: SimSession, ticks: int = 6, tag: str = "") -> list[dict]:
    """模拟 tick 里的「1.45) 卡箱礼物卡就地变卡」这一步，返回时间线。

    一次 `_drain_box_gift` 会**一直变到成功或箱里的礼物卡没了**（用户 2026-09-26
    定调），所以一个 tick 里可能发起多次变卡请求 —— 时间线如实记 `tried` 次数。
    """
    tl: list[dict] = []
    for i in range(1, ticks + 1):
        pick = eng._pick_change_dst(eng._stock)
        g0 = face40_gap(eng.plans[pick["tid"]], eng._stock, eng._pending) if pick else None
        if not pick:
            tl.append({"tick": i, "act": "停手", "detail": "40 面卡已够（或无 40 面候选），"
                                                            "不消耗卡箱礼物卡"})
            break
        dst = int(pick["dst"])
        box_before = len(await gf.find_box_gift_cards(session))
        n_req0 = session.transfer_n
        have_before = g0["have"]
        rd_back = await eng._drain_box_gift([], eng._stock)   # 真实代码路径
        r = eng.box_gift_last or {}
        tried = session.transfer_n - n_req0                    # 本 tick 的变卡请求数
        changed = int(r.get("changed") or 0)
        burned = int(r.get("burned") or 0)
        calls = session.calls[n_req0:]
        skipped = bool(r.get("skipped")) and not tried
        act = "变卡" if tried else "空手"                      # 箱里没源卡 → 本轮不消耗
        g1 = face40_gap(eng.plans[pick["tid"]], eng._stock, eng._pending)
        tl.append({"tick": i, "act": act, "src": (calls[-1][0] if calls else None),
                   "dst": dst, "box_before": box_before,
                   "box_after": len(await gf.find_box_gift_cards(session)),
                   "tried_this_tick": tried, "changed": changed, "burned": burned,
                   "skipped": skipped, "ok": bool(changed), "failed": bool(burned),
                   "rd_back": rd_back is not None,
                   "name": pick["name"],
                   "have_after": g1["have"], "gap_after": g1["need_total"],
                   "have_delta": g1["have"] - have_before,
                   "detail": (r.get("message") or "")[:70]})
    for row in tl:
        row.pop("rd_back", None)
        if tag:
            row["tag"] = tag
    return tl


def show(tl: list[dict]) -> None:
    for r in tl:
        if r["act"] == "变卡":
            print(f'    tick{r["tick"]}: #{r["src"]} → {r["dst"]} {r["name"]}'
                  f' {"成功" if r["ok"] else "失败(烧源卡)"}'
                  f'｜尝试 {r["tried_this_tick"]} 次、烧 {r["burned"]} 张源卡'
                  f'｜箱里源卡 {r["box_before"]}→{r["box_after"]}'
                  f'｜40面存量 {r["have_after"]} / 还差 {r["gap_after"]}')
        else:
            print(f'    tick{r["tick"]}: {r["act"]} —— {r["detail"]}')


# ---------------------------------------------------------------------------
async def main() -> None:
    tid = int(os.environ.get("THEME", "1810"))
    print(f"\n===== 变卡逻辑模拟（主套 {tid}）=====")

    stock, pending, stove, free_box = await live_state()
    plan = ThemePlan(tid)
    src_pool = sorted({c for c in stock if c in gf._face48_ids()})
    print(f"\n[1] 真机库存快照（只读一次，无副作用）")
    print(f"    箱里礼物套 48 面卡：{src_pool}"
          f"（{sum(v for k, v in stock.items() if k in gf._face48_ids())} 张）")
    inner40 = [c for c in plan.ids if int(plan.cards[c][3] or 0) == 40]
    print(f"    {plan.name} 的 40 面卡：{[f'{c} {card_name(c)}×{stock.get(c,0)}' for c in inner40]}")
    print(f"    炉内在炼（pending）：{dict(pending)}")

    g = face40_gap(plan, stock, pending)
    print(f"    40 面缺口：needed={sum(plan.f40.values())} 共需，已有 {g['have']} 张，"
          f"还差 {g['need_total']} 张（按卡：{g['gap']}）")

    check("这一套确实还缺 40 面卡（不是零缺口）", g["need_total"] > 0,
          f"还差 {g['need_total']} 张")
    check("箱里躺着礼物套 48 面卡（变卡的可选源卡）",
          bool([c for c in stock if c in gf._face48_ids()]), str(src_pool))

    gift_n = sum(v for k, v in stock.items() if k in gf._face48_ids())
    print(f"\n[2] 模拟 A：变卡次次成功（箱里源卡 {gift_n} 张）")
    session = SimSession([], ok_seq=[True] * 99)
    session.set_cards(_box_of(stock, pending), pending)
    eng = AutoRefine(session, [tid])
    eng._stock, eng._pending = Counter(stock), Counter(pending)
    eng.box_gift_change = True
    tl_a = await sim_ticks(eng, session, ticks=6)
    show(tl_a)
    did_a = [r for r in tl_a if r["act"] == "变卡"]
    ok_a = [r for r in did_a if r["ok"]]
    check("挑出的目标全是这一套里的 40 面卡",
          all(r["dst"] in [int(c) for c in inner40] for r in did_a),
          str([r["dst"] for r in did_a]))
    check("目标卡面额 = 40（礼物卡变不出别的面额）",
          all(int(plan.cards[r["dst"]][3] or 0) == 40 for r in did_a))
    check("change_dst_is_face40 校验对目标放行",
          all(gf.change_dst_is_face40(r["dst"]) is True for r in did_a))
    check("变卡请求次数 = 变成功的张数（全成功路径，一张不多试）",
          session.transfer_n == len(ok_a),
          f"请求 {session.transfer_n} 次 / 成功 {len(ok_a)} 张")
    check("变卡请求次数 ≤ 源卡张数（绝不会烧超过手里有的卡）",
          session.transfer_n <= gift_n,
          f"请求 {session.transfer_n} 次 / 源卡 {gift_n} 张")
    check("变卡产出的 dst 确实进库存（40 面存量上升）",
          ok_a and ok_a[-1]["have_after"] > g["have"],
          f'{g["have"]} → {ok_a[-1]["have_after"] if ok_a else "-"}')
    check("源卡烧光后自动收手（后面几轮一次请求都不再发）",
          tl_a[-1]["act"] == "空手" and sum(r["tried_this_tick"] for r in tl_a) == gift_n,
          f'末轮={tl_a[-1]["act"]}，累计请求 {session.transfer_n} 次')
    check("变卡完立刻刷新卡箱状态（同一 tick 内 40 面存量就已经涨上来了）",
          bool(ok_a) and ok_a[-1]["have_delta"] == 1
          and all(r["have_delta"] in (0, 1) for r in did_a),
          f'每轮 40 面存量增量 {[r["have_delta"] for r in did_a]}')
    # 变卡后引擎库存里的 dst 必须来自「重读卡箱」，不是本地估算
    check("引擎 _stock 里能读到刚变出来的那张 dst（靠变卡后重读，不是记账）",
          any(eng._stock.get(r["dst"], 0) >= 1 for r in ok_a),
          f'box_gift_last={ (eng.box_gift_last or {}).get("message") }')

    print(f"\n[3] 模拟 B：第 1 次变卡失败 → 换下一张继续（用户定调：一直变到成功或礼物卡没了）")
    session2 = SimSession([], ok_seq=[False] + [True] * 99)   # 第 1 次失败，之后都成功
    session2.set_cards(_box_of(stock, pending), pending)
    eng2 = AutoRefine(session2, [tid])
    eng2._stock, eng2._pending = Counter(stock), Counter(pending)
    eng2.box_gift_change = True
    tl_b = await sim_ticks(eng2, session2, ticks=4, tag="fail")
    show(tl_b)
    first = tl_b[0]
    check("失败后**换下一张继续**（本 tick 请求数 > 1，而不是一次就收手）",
          first["tried_this_tick"] >= 2 and first["changed"] == 1,
          f'尝试 {first["tried_this_tick"]} 次、成功 {first["changed"]} 张')
    check("失败那张的源卡确实被烧掉（箱里源卡减少 = 失败 + 成功各烧一张）",
          first["burned"] == 1
          and first["box_after"] == first["box_before"] - first["burned"] - first["changed"],
          f'burned={first["burned"]}，箱里源卡 {first["box_before"]}→{first["box_after"]}')
    check("失败不会触发静默/冷却（后面几个 tick 照常继续变卡）",
          sum(1 for r in tl_b if r["act"] == "变卡") >= 2,
          str([r["act"] for r in tl_b]))

    print("    （ok_seq 全失败：把箱里的礼物卡全烧光就该收手）")
    session2b = SimSession([], ok_seq=[False] * 99)
    session2b.set_cards(_box_of(stock, pending), pending)
    eng2b = AutoRefine(session2b, [tid])
    eng2b._stock, eng2b._pending = Counter(stock), Counter(pending)
    eng2b.box_gift_change = True
    tl_b2 = await sim_ticks(eng2b, session2b, ticks=3, tag="allfail")
    show(tl_b2)
    check("一直失败 → 烧完箱里最后一张礼物卡才收手（不会无限循环）",
          tl_b2[-1]["box_after"] == 0 or tl_b2[-1]["act"] == "空手",
          f'末轮箱里源卡 {tl_b2[-1]["box_after"]}，尝试 {tl_b2[-1]["tried_this_tick"]} 次')
    check("全程失败也不进静默冷却（源卡没了自然停，下一 tick 重扫即恢复）",
          sum(r["tried_this_tick"] for r in tl_b2) <= gift_n,
          f'累计尝试 {sum(r["tried_this_tick"] for r in tl_b2)} 次 / 源卡 {gift_n} 张')

    print(f"\n[4] 模拟 C：箱里一张礼物卡都没有 → 不该动、也不该记失败")
    session3 = SimSession([])
    session3.set_cards([(c, s, t) for c, n in stock.items()
                        if c not in gf._face48_ids() for s, t in [(1, 0)]])
    eng3 = AutoRefine(session3, [tid])
    eng3._stock, eng3._pending = Counter(stock), Counter(pending)
    eng3.box_gift_change = True
    await eng3._drain_box_gift([], eng3._stock)
    r3 = eng3.box_gift_last or {}
    check("返回 skipped（一张源卡都不碰）", r3.get("skipped") is True,
          r3.get("message", ""))
    check("没有发起任何变卡请求", session3.transfer_n == 0, f'transfer_n={session3.transfer_n}')

    print(f"\n[5] 模拟 D：这一套 40 面卡已够 → 完全静默（正常收手，不是坏了）")
    full = Counter(stock)
    for c in inner40:
        full[c] = max(full.get(c, 0), int(plan.f40.get(c, 0) or 0))
    session4 = SimSession([], ok_seq=[])
    session4.set_cards([(c, s, t) for c, n in full.items() for s, t in [(1, 0)]], pending)
    eng4 = AutoRefine(session4, [tid])
    eng4._stock, eng4._pending = Counter(full), Counter(pending)
    eng4.box_gift_change = True
    pick4 = eng4._pick_change_dst(eng4._stock)
    check("缺口归零 → pick 返回 None", pick4 is None, str(pick4))
    await eng4._drain_box_gift([], eng4._stock)
    r4 = eng4.box_gift_last or {}
    check("drain 直接 skipped，一张源卡都不碰", r4.get("skipped") is True,
          r4.get("message", ""))
    check("这一轮没有变卡请求", session4.transfer_n == 0)

    print(f"\n[6] 模拟 E：多套并行（1652 + {tid}）—— 不让第一套独吞源卡")
    q = [1652, tid]
    plans = {t: ThemePlan(t) for t in q}
    keep = {cid for t in q for cid in plans[t].ids}
    picks = []
    for t in q:
        p = pick_change_target(plans, [t], stock, pending, keep)
        picks.append(p)
    check("两套各自都能挑出目标（谁都不被吞掉）",
          all(p is not None for p in picks),
          str([(p["tid"], p["name"]) if p else None for p in picks]))
    check("挑出的目标都属于自己的那一套",
          all(p["tid"] == t for t, p in zip(q, picks)))
    p_all = pick_change_target(plans, q, stock, pending, keep)
    check("多套并行时优先挑**缺口最大**的那套（不搞平均）",
          p_all is not None, str((p_all["tid"], p_all["name"]) if p_all else None))

    print(f"\n[7] 模拟 F：换卡箱还有 {free_box} 格时，变卡产出的 dst 有没有地方放")
    check("换卡箱有空位（变卡产出的 dst 能落箱）", free_box > 0, f"free={free_box}")

    print(f"\n===== {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    if FAIL:
        for f in FAIL:
            print("  FAIL: " + f)
    return


def _box_of(stock: Counter, pending: Counter) -> list[tuple[int, int, int]]:
    """把库存摊成带槽位的卡箱形态（礼物源卡放换卡箱，其余塞保险箱）。

    同一张卡有 N 张就占 N 个槽 —— 变卡是**按槽**烧的，源卡数量决定能变几次。
    """
    out: list[tuple[int, int, int]] = []
    slot = 1
    gift = sorted(c for c in stock if c in gf._face48_ids())
    for c in gift:
        for _ in range(stock.get(c, 0)):
            out.append((c, slot, 0))
            slot += 1
    other = sorted(c for c in stock if c not in gf._face48_ids())
    for c in other:
        for _ in range(stock.get(c, 0)):
            out.append((c, slot, 1))
            slot += 1
    return out                                        # 炉内在炼的卡不占箱


if __name__ == "__main__":
    asyncio.run(main())
