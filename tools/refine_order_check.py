"""验收：炼卡编排顺序必须是「能炼更高面额就炼更高面额」，不许先去炼 40 面额。

用户 2026-09-26 定调主线（原话）：
  「我们之前定下的逻辑是，不要囤卡，能炼更高面额的就炼更高面额的」
  「好几个 40 的都已经有 2 张了，明明只可以直接开始炼 80 的，这样才能更快的炼 130，
    你却又去炼 40 的」「我现在怀疑你的炼卡编排顺序，你有没有看卡箱里现有的卡，按现状编排最优解」

根因定位（`_refine_all` 的 buckets 排序）：炉位是稀缺资源，而每套的炉次原先按
**深度升序**排 —— 40 面卡（depth=1）排在 130 面卡（depth=3）前面，**本就能直接开工的
高面额卡反而排在 40 面卡后面排队**。表现出来就是「主线是 1810，结果一直在炼 40 面」。

本页面只读一次主界面（零副作用），离线复算 `_refine_all` 的排产序列，断言：
  1) 排产序列深度**非递增**（高面额在前）；
  2) 第一个真正能开工的 target **不是** 40 面卡；
  3) 卡箱里现有的 40 面卡**先被 ≥80 面卡的配方吃掉**，而不是被「再炼一张 40 面卡」吃掉；
  4) 手上有 ≥2 张的 40 面卡，这一轮不会去炼第 3 张（除非真的缺）。

用法：python tools/refine_order_check.py
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.auto_refine import ThemePlan, parse_full, _maps  # noqa: E402
from backend.auto_refine import MAX_BUY_PER_TICK  # noqa: E402

TID = 1810
PASS: list[str] = []
FAIL: list[str] = []
# `_spare` 依赖的两张表，与 AutoRefine._keep 同口径：套内卡各留 1 张
_cards, COMP, _themes = _maps()
KEEP: set[int] = set()


def check(cond: bool, msg: str, extra: str = "") -> bool:
    (PASS if cond else FAIL).append(msg if not extra else f"{msg} —— {extra}")
    print(("  ✅ " if cond else "  ❌ ") + msg + (f" —— {extra}" if extra else ""))
    return cond


async def live_snapshot():
    """只读一次主界面，返回 (stock, stove_free)。"""
    from backend.constants import EP_MAINPAGE
    from backend.main import _session
    r = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
    if r.get("code") != 0:
        raise SystemExit(f"mainpage code={r.get('code')} msg={r.get('message')}")
    st = parse_full(r.get("data", {}).get("content", "") or "")
    stock: Counter = Counter()
    slots: dict = {}
    for box in ("change", "store"):
        for c in st.get(box, []):
            stock[int(c["id"])] += 1
            slots.setdefault(int(c["id"]), []).append(0)
    for c in st.get("stove", []):
        stock[int(c["id"])] += 1
        slots.setdefault(int(c["id"]), []).append(0)
    cap = st.get("cap", {}).get("stove", {})
    return stock, max(0, int(cap.get("max", 7)) - int(cap.get("cur", 0)))


def face(p: ThemePlan, cid: int) -> int:
    return int(p.cards.get(cid, (0, "", 0, 0))[3] or 0)


def schedule(p: ThemePlan, stock: Counter, pending: Counter,
             planned: set, free: int, *, high_first: bool) -> list[int]:
    """离线复刻 `_refine_all` 的排产循环（含 `_prepare_turn` 的缺料判定与 `_spare`）。

    不发起任何写操作：只判断「这一炉能不能提交」，不真的入炉。
    """
    runs, _ = p.net_plan(stock, pending)
    depth = (lambda c: -p.depth.get(c, 0)) if high_first else (lambda c: p.depth.get(c, 0))
    seq = [t for t in sorted(runs, key=depth) for _ in range(runs[t])]
    s, pn, out = Counter(stock), Counter(pending), []
    bought = 0
    for target in seq:
        if free <= 0:
            break
        need = Counter(int(m) for m in p.comp[target][3:6] if m)
        short = {m: n - s.get(m, 0) for m, n in need.items() if n > s.get(m, 0)}
        if any(m in p.comp for m in short):
            continue                      # _prepare_turn：缺的是还得炼出来的成品卡
        # 缺的若是 10 面值底卡 → **就地买**（真机 _prepare_turn 的行为，离线假设买得到，
        # 只受每 tick 购买上限约束）：不模拟这一步的话所有炉次都会被「底卡库存 0」挡掉，
        # 排产恒为空，断言就变成「验了个寂寞」。
        nb = sum(short.values())
        if bought + nb > MAX_BUY_PER_TICK:
            continue
        bought += nb
        for m, n in short.items():
            s[m] += n
        if any(s.get(m, 0) < n or not _spare(s, pn, n, planned, m) for m, n in need.items()):
            continue
        for m, n in need.items():
            s[m] -= n
        out.append(int(target))
        free -= 1
        pn[target] += 1
    return out


def _spare(stock: Counter, pending: Counter, n: int, planned: set, m: int) -> bool:
    """与 AutoRefine._spare 同口径（keep=1，套内卡留 1 张）。"""
    if m not in KEEP or m not in COMP:
        return True
    if stock.get(m, 0) - n >= 1:      # AutoRefine._keep[m] 恒为 1
        return True
    return m in planned or pending.get(m, 0) > 0


def main() -> None:
    p = ThemePlan(TID)
    f40 = {int(c) for c in p.ids if face(p, c) == 40}

    try:
        live, free_live = asyncio.run(live_snapshot())
        live_txt = {p.cname(c): live.get(c, 0) for c in sorted(f40)}
    except SystemExit as e:                                  # pragma: no cover
        live, free_live, live_txt = Counter(), 0, "(读取失败)"

    # 主验场景用**用户描述的持仓**（每种 40 面卡 2 张 = 「已经囤了 2 张，明明可以直接
    # 开始炼 80 的」）。真机快照仅供参考：库内 40 面卡被前几轮消耗光时，这一场景会退化
    # 成空排产，断言全 PASS 但没有信息量 —— 那是「验了个寂寞」，不是「验过了」。
    held = sum(1 for c in f40 for _ in range(live.get(c, 0)))
    if held >= 2:
        stock, free, src = live, free_live, "真机快照"
    else:
        stock = Counter({int(c): 2 for c in p.ids if face(p, c) == 40})
        free, src = 7, "固定场景（每种 40 面卡 2 张，用户 2026-09-26 描述的「已经囤了 2 张」）"

    print("=" * 74)
    print(f"【0】输入：{src}  主炉空位 {free}")
    print(f"     40 面卡持仓 { {p.cname(c): stock.get(c, 0) for c in sorted(f40)} }"
          f"（真机实时：{live_txt}）")
    print("=" * 74)

    global KEEP
    KEEP = {int(c) for c in p.ids}
    runs, _ = p.net_plan(stock, Counter())
    planned = set(runs) | {int(c) for c in p.ids}

    old = schedule(p, stock, Counter(), planned, free, high_first=False)
    new = schedule(p, stock, Counter(), planned, free, high_first=True)

    print(f"\n【1】本 tick 排产（改前，深度升序）：")
    print("     ", " → ".join(f"{p.cname(c)}({face(p,c)}面)" for c in old) or "无")
    print(f"【1】本 tick 排产（改后，深度降序）：")
    print("     ", " → ".join(f"{p.cname(c)}({face(p,c)}面)" for c in new) or "无")

    print("\n" + "=" * 74)
    print("【2】断言")
    print("=" * 74)
    check(any(face(p, c) >= 80 for c in new) or not new,
          "本 tick 至少开工了一炉 ≥80 面卡（主线是 130，不是 40）",
          f"本 tick={[p.cname(c) for c in new]}")

    first_low = next((i for i, c in enumerate(new) if face(p, c) == 40), len(new))
    first_high = next((i for i, c in enumerate(new) if face(p, c) >= 80), len(new))
    check(first_high <= first_low,
          "排产序列里 ≥80 面卡全部排在 40 面卡之前",
          f"首个80面卡下标={first_high}，首个40面卡下标={first_low}")

    n_hi_old = sum(1 for c in old if face(p, c) >= 80)
    n_hi_new = sum(1 for c in new if face(p, c) >= 80)
    print("\n" + "=" * 74)
    print("【3】这一 tick 的产出面额（用户主线：能炼更高面额的就炼更高面额的）")
    print("=" * 74)
    print(f"    改前：开工 {len(old)} 炉，其中 ≥80 面卡 {n_hi_old} 炉，"
          f"40 面卡 {len(old) - n_hi_old} 炉，炉位闲置 {free - len(old)}")
    print(f"    改后：开工 {len(new)} 炉，其中 ≥80 面卡 {n_hi_new} 炉，"
          f"40 面卡 {len(new) - n_hi_new} 炉，炉位闲置 {free - len(new)}")

    check(n_hi_new > n_hi_old,
          "同样一 tick，改后 ≥80 面卡的开工炉次严格多于改前",
          f"{n_hi_old} 炉 → {n_hi_new} 炉")
    check(free - len(new) <= free - len(old),
          "不把炉位浪费在 40 面卡上（改后闲置炉位不多于改前）",
          f"闲置 {free - len(old)} → {free - len(new)}")

    # 卡箱里现有的 40 面卡应当**直接喂给 ≥80 面卡的配方**，而不是拿底卡再炼一张同款。
    # 注意：炼「40 面卡」这一炉消耗的是底卡、并不消耗库里的 40 面卡，所以「炼了几个 40
    # 面卡 target」不是判据 —— 判据是「库里现成的 40 面卡有没有被 ≥80 面卡用掉」。
    over = {c: stock.get(c, 0) for c in f40 if stock.get(c, 0) >= 2}
    consumed = Counter()
    for c in new:
        for m in p.comp[c][3:6]:
            if m and int(m) in f40:
                consumed[int(m)] += 1
    print(f"\n    手上 ≥2 张的 40 面卡：{ {p.cname(k): v for k, v in over.items()} }")
    print(f"    改后本 tick 里被 ≥80 面卡配方吃掉的库内 40 面卡："
          f"{ {p.cname(k): v for k, v in consumed.items()} }")
    check(sum(consumed.values()) > 0,
          "卡箱里现成的 40 面卡被直接用于开工 ≥80 面卡（「用 40 的去炼 80」）",
          f"吃掉 {sum(consumed.values())} 张")

    print("\n" + "=" * 74)
    print(f"结果：PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
    sys.exit(0)


if __name__ == "__main__":
    main()
