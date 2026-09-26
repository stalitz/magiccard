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
    """mainpage 的卡池可替换；变卡结果按 fail_seq 逐次返回成败。"""

    def __init__(self, cards: list[tuple[int, int, int]],
                 fail_seq: list[bool] | None = None):
        self.uin = "1"
        self.fail_seq = list(fail_seq or [])
        self.calls: list[tuple] = []
        self.transfer_n = 0
        self.set_cards(cards)

    def set_cards(self, cards: list[tuple[int, int, int]]) -> None:
        self.cards = list(cards)
        ch = [c for c in cards if c[2] == 0]
        st = [c for c in cards if c[2] == 1]

        def box(name, items):
            body = "".join(f'<card slot="{s}" id="{i}"/>'
                           for i, s, _ in sorted(items, key=lambda x: x[1]))
            return f'<{name} cur="{len(items)}" max="18" used="{len(items)}">{body}</{name}>'

        self.raw = ("<qqhome><user randchance=\"0\"/>"
                    + box("changebox", ch) + box("storebox", st) + "</qqhome>")

    def apply_result(self, src: int, dst: int, ok: bool, slot: int | None = None) -> None:
        """变卡一次后的真实库存变化：源卡**必定**消失（失败也烧），成功才得 dst。"""
        if slot is None:
            slot = self._slot_of(src)
        rest = [c for c in self.cards if not (c[0] == src and c[1] == slot)]
        self.set_cards(rest)
        if ok:
            nxt = max((c[1] for c in self.cards), default=0) + 1
            where = 0 if any(c[2] == 0 for c in self.cards) else 1
            self.set_cards(self.cards + [(dst, nxt, where)])

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
        if n <= len(self.fail_seq) and self.fail_seq[n - 1]:
            return {"code": 0, "raw": '<qqhome code="0" result="0"/>', "message": ""}
        return {"code": 0, "raw": '<qqhome code="0" result="1"/>', "message": ""}


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
    """模拟 tick 里的「1.45) 卡箱礼物卡就地变卡」这一步，返回时间线。"""
    tl: list[dict] = []
    for i in range(1, ticks + 1):
        if eng._box_gift_fail >= 2 or time.time() < eng._box_gift_cooldown:
            tl.append({"tick": i, "act": "静默", "detail": "连败≥2 或冷却中，本轮不动卡"})
            continue
        pick = eng._pick_change_dst(eng._stock)
        if not pick:
            tl.append({"tick": i, "act": "停手",
                       "detail": "40 面卡已够（或无 40 面候选），不消耗卡箱礼物卡"})
            break
        dst = int(pick["dst"])
        box_before = len(await gf.find_box_gift_cards(session))
        await eng._drain_box_gift([], eng._stock)      # 真实代码路径（返回值挂 box_gift_last）
        r = eng.box_gift_last or {}
        src = slot = None
        if session.calls:
            src, _dst, slot, _st = session.calls[-1]
        n_req = session.transfer_n
        changed = int(r.get("changed") or 0)
        if src is not None:
            session.apply_result(src, dst, bool(changed), slot)
            eng._stock[src] -= 1                        # 源卡必定消失
            if changed:
                eng._stock[dst] += 1
        skipped = bool(r.get("skipped"))
        failed = (not skipped) and changed == 0
        act = "变卡" if not skipped else "空手"        # 箱里没源卡 → 本轮不消耗
        tl.append({"tick": i, "act": act, "src": src, "dst": dst,
                   "box_before": box_before, "tried_this_tick": n_req,
                   "failed": failed, "changed": changed,
                   "skipped": skipped,
                   "r": {k: r.get(k) for k in ("skipped", "changed", "inbox", "message")},
                   "name": pick["name"], "ok": bool(changed),
                   "gap_after": face40_gap(eng.plans[pick["tid"]], eng._stock,
                                           eng._pending)["need_total"],
                   "have_after": face40_gap(eng.plans[pick["tid"]], eng._stock,
                                            eng._pending)["have"],
                   "fail_cnt": eng._box_gift_fail,
                   "cooldown_left": int(eng._box_gift_cooldown - time.time()),
                   "detail": (r.get("message") or "")[:60]})
    for row in tl:
        if tag:
            row["tag"] = tag
    return tl


def show(tl: list[dict]) -> None:
    for r in tl:
        if r["act"] == "变卡":
            print(f'    tick{r["tick"]}: #{r["src"]} → {r["dst"]} {r["name"]}'
                  f' {"成功" if r["ok"] else ("跳过" if r["r"].get("skipped") else "失败(烧源卡)")}'
                  f'｜箱里源卡 {r["box_before"]}、本轮尝试 {r["tried_this_tick"]} 次'
                  f'｜40面存量 {r["have_after"]} / 还差 {r["gap_after"]}'
                  f'｜连败 {r["fail_cnt"]}')
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
    session = SimSession([])
    session.set_cards(_box_of(stock, pending))
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
    reqs = [r["tried_this_tick"] for r in did_a]
    check("每个 tick 最多只发 1 次变卡请求（max_try=1，失败也不连烧源卡）",
          all(b - a <= 1 for a, b in zip([0] + reqs, reqs)),
          f"每轮累计请求数 {list(zip([r['tick'] for r in did_a], reqs))}")
    check("变卡请求次数 ≤ 源卡张数（绝不会烧超过手里有的卡）",
          session.transfer_n <= gift_n,
          f"请求 {session.transfer_n} 次 / 源卡 {gift_n} 张")
    check("变卡产出的 dst 确实进库存（40 面存量上升）",
          ok_a and ok_a[-1]["have_after"] > g["have"],
          f'{g["have"]} → {ok_a[-1]["have_after"] if ok_a else "-"}')
    check("源卡烧光后自动收手（后面几轮一次请求都不再发）",
          tl_a[-1]["act"] == "空手" and tl_a[-1]["tried_this_tick"] == session.transfer_n,
          f'末轮={tl_a[-1]["act"]}，累计请求 {session.transfer_n} 次')
    check("全成功路径不进冷却（fail 计数为 0）",
          all(r.get("cooldown_left", 0) <= 0 for r in tl_a))
    check("全成功路径下 fail 计数始终 0（不进静默）",
          all(r.get("fail_cnt", 0) == 0 for r in tl_a))

    print(f"\n[3] 模拟 B：变卡失败（真机实测失败也烧源卡，成功率约 50%）")
    print("    （fail_seq=[失败,成功]：第 1 轮故意失败，第 2 轮该轮空）")
    session2 = SimSession([], fail_seq=[True, False])
    session2.set_cards(_box_of(stock, pending))
    eng2 = AutoRefine(session2, [tid])
    eng2._stock, eng2._pending = Counter(stock), Counter(pending)
    eng2.box_gift_change = True
    tl_b = await sim_ticks(eng2, session2, ticks=5, tag="fail")
    show(tl_b)
    burned = [r for r in tl_b if r.get("failed")]
    check("失败也烧掉一张源卡（40 面存量没有上升）",
          burned and burned[-1]["have_after"] == g["have"],
          f'失败后存量 {burned[-1]["have_after"] if burned else "-"}（起点 {g["have"]}）')
    check("失败**一次**就进 30 分钟静默（真代码在失败分支直接置冷却）",
          burned and burned[-1]["cooldown_left"] > 1700,
          f'冷却剩 {burned[-1]["cooldown_left"] if burned else 0} 秒')
    check("静默期内一个 tick 都不动卡",
          all(r["act"] == "静默" for r in tl_b[1:]),
          str([r["act"] for r in tl_b]))
    eng2._box_gift_cooldown = 0.0                     # 冷却到期，看看会不会自愈
    eng2._box_gift_fail = 0
    tl_b2 = await sim_ticks(eng2, session2, ticks=2)
    check("冷却结束后自动恢复（不是一次性死锁）",
          any(r["act"] in ("变卡", "空手") for r in tl_b2),
          str([(r["act"], r.get("dst")) for r in tl_b2]))

    print(f"\n[4] 模拟 C：箱里一张礼物卡都没有 → 不该动、也不该记失败")
    session3 = SimSession([])
    session3.set_cards([(c, s, t) for c, n in stock.items()
                        if c not in gf._face48_ids() for s, t in [(1, 0)]])
    eng3 = AutoRefine(session3, [tid])
    eng3._stock, eng3._pending = Counter(stock), Counter(pending)
    eng3.box_gift_change = True
    await eng3._drain_box_gift([], eng3._stock)
    r3 = eng3.box_gift_last or {}
    check("返回 skipped（不消耗、不记失败）", r3.get("skipped") is True,
          r3.get("message", ""))
    check("fail 计数不因此累加（箱里没卡是常态）", eng3._box_gift_fail == 0)
    check("没有发起任何变卡请求", session3.transfer_n == 0, f'transfer_n={session3.transfer_n}')

    print(f"\n[5] 模拟 D：这一套 40 面卡已够 → 完全静默（正常收手，不是坏了）")
    full = Counter(stock)
    for c in inner40:
        full[c] = max(full.get(c, 0), int(plan.f40.get(c, 0) or 0))
    session4 = SimSession([], fail_seq=[])
    session4.set_cards([(c, s, t) for c, n in full.items() for s, t in [(1, 0)]])
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
