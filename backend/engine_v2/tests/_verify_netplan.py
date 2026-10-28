"""net_plan 净需求正确性回归（改 net_plan / sets 口径后必跑）。

为什么需要：`net_plan` 是 DP 骨架与贪心 Planner 共同的唯一需求源，
口径错一位就会「多买底卡」或「漏炼中间层」，而这两种错在长周期仿真里
表现为「炉空转」或「箱爆」，极难从结果反推。所以用随机状态做守恒校验。

判据（★ 只查缺、不查余）：
  1. **不能缺**：按 runs 炼、按 buy 买之后，每张套内卡 >= sets 份。
  2. **不能多买**：每张底卡的 buy 必须恰好 = max(0, 总需求 - 已持有)，其中
     总需求 = sets(收藏) + Σ(炼它上层的那些 runs 各消耗 1 张)。
  3. 「余」不算错：库存/在炉本来就有超量的卡，net_plan 无权撤回在炉的卡。

用法：python -m backend.engine_v2.tests._verify_netplan
"""
from __future__ import annotations

import random
import sys
from collections import Counter

from backend.engine_v2.model import ThemeModel

CASES = ((178, 1), (178, 2), (178, 3), (85, 1), (85, 3), (290, 1), (290, 2),
         (59, 1), (60, 1), (709, 1))


def check_one(m: ThemeModel, sets: int, stock: Counter, pend: Counter):
    """返回 (lack, overbuy)。两者都为空才算通过。"""
    runs, buy = m.net_plan(Counter(stock), Counter(pend), sets=sets)
    avail = Counter(stock) + Counter(pend)

    # ---- 1. 不能缺 ----
    have = Counter(avail)
    for c, n in runs.items():
        have[c] += n
        for f in m.feeders(c):
            have[f] -= n
    for c, n in (buy or {}).items():
        have[c] += n
    lack = {c: sets - have.get(c, 0) for c in m.ids if have.get(c, 0) < sets}

    # ---- 2. 不能多买（用 runs 反算消耗，独立于原实现） ----
    overbuy = {}
    for c in m.depth:
        if m.comp.get(c):
            continue                       # 只看底卡：非底卡不该出现在 buy
        total = sets + sum(n for p, n in runs.items() if c in m.feeders(p))
        want = max(0, total - avail.get(c, 0))
        got = (buy or {}).get(c, 0)
        if got != want:
            overbuy[c] = (got, want)
    # 非底卡出现在 buy 里一定是错的（能炼的不该买）
    for c in (buy or {}):
        if m.comp.get(c):
            overbuy[c] = ((buy or {})[c], 0)
    return lack, overbuy


def main() -> int:
    rnd = random.Random(7)
    bad_total = 0
    for tid, sets in CASES:
        m = ThemeModel(tid)
        bad = []
        for _ in range(300):
            stock = Counter({c: rnd.randint(0, 2) for c in m.depth
                             if rnd.random() < .5})
            pend = Counter({c: rnd.randint(0, 2) for c in m.depth
                            if rnd.random() < .2})
            lack, ob = check_one(m, sets, stock, pend)
            if lack or ob:
                bad.append((dict(stock), dict(pend), lack, ob))
        flag = "OK" if not bad else f"⚠ {len(bad)}/300 不通过"
        print(f"{tid:>4}x{sets}  {m.star}★ ids={len(m.ids):>2}  {flag}")
        for x in bad[:2]:
            print(f"      stock={x[0]}\n      pend={x[1]}\n      缺={x[2]} 多买={x[3]}")
        bad_total += len(bad)
    print("\n" + ("全部通过：net_plan 需求守恒。" if not bad_total
                  else f"★ {bad_total} 例不通过，净需求口径有问题。"))
    return 0 if not bad_total else 1


if __name__ == "__main__":
    sys.exit(main())
