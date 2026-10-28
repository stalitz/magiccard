"""`_calc_sets` 箱位封顶回归（改 sets 口径 / 换卡箱容量语义后必跑）。

为什么需要（2026-10-28 真机事故）：
  1★ 的 sets 原口径 = `max(顶层三张 40 面的持有数)`，上限只按「炉位/卡位数」卡。
  而 N 张一套的卡**必须同时在换卡箱里才能上交**，18 格换卡箱放不下 2 套以上。
  于是 40 面一囤起来 sets 就跳到 5~6 → 一次排 18 张半成品 → 箱满 → 底卡买不进
  → 炉子清空后永久停摆（真机表现：同一个 40 面卡囤 13 张、三箱全满、零上交）。

判据：
  1. **公式**：`_calc_sets` 结果 ≤ max(1, change_cur // 套内张数)；
     `box_cur=0`（未知容量）时不封顶，保持旧行为。
  2. **端到端**：生产默认路径（不勾 DP + max_rounds=0 不限轮）跑 178 蜻蜓，
     900 tick 内必须**至少上交 1 套**且换卡箱**不能顶满** —— 修前是 0 套 + 18/18。

用法：python -m backend.engine_v2.tests._verify_sets_boxcap
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter

from backend.auto_refine import AutoRefine, ThemePlan


def _bare() -> AutoRefine:
    """不跑 __init__（它会连 Session），只为调 `_calc_sets` 造一个空壳。"""
    ar = object.__new__(AutoRefine)
    ar.surplus_sets = 0
    ar._tick_collected = {}
    ar._box_cur = 0
    return ar


def check_formula() -> list[str]:
    errs: list[str] = []
    ar = _bare()
    for tid, n_expect in ((178, 9), (85, 13)):
        p = ThemePlan(tid)
        assert len(p.ids) == n_expect, f"tid={tid} 套内张数应为 {n_expect}，实为 {len(p.ids)}"
        # 囤满一整层 40 面（模拟真机囤积）→ 原口径会算出 6 套
        f40 = list(p.f40)[:3]
        stock = Counter({c: 6 for c in f40})
        raw = ar._calc_sets_raw(p, stock, Counter(), free_slots=7)
        capped = ar._calc_sets(p, stock, Counter(), free_slots=7, box_cur=18)
        want = max(1, 18 // n_expect)
        print(f"  tid={tid} 张数={n_expect} 原口径={raw} 封顶后={capped} 期望≤{want}")
        if raw <= want:
            errs.append(f"tid={tid} 场景没造出放大（raw={raw} ≤ {want}），本回归失效")
        if capped > want:
            errs.append(f"tid={tid} 封顶失效：{capped} > {want}")
        # box_cur=0 → 不封顶（保持旧行为，供未读状态的预检路径用）
        if ar._calc_sets(p, stock, Counter(), free_slots=7, box_cur=0) != raw:
            errs.append(f"tid={tid} box_cur=0 时应退回原口径")
    return errs


async def check_end2end() -> list[str]:
    from backend.engine_v2.engine import Engine     # 延迟导入：它会接管 time.time

    errs: list[str] = []
    eng = Engine([178], buy=True, max_rounds=0, steal_enabled=True, use_dp=False)
    vs, n = await eng.run_sim(change_cur=18, store_cur=168, max_ticks=900, interval=45)
    rounds = eng.ar.plans[178].rounds
    used = len(vs.change)
    print(f"  178 贪心 mr=0: {round(n * 45 / 3600, 2)}h 上交={rounds} "
          f"换卡箱={used}/18 refined={eng.stats['refined']}")
    if rounds < 1:
        errs.append(f"900 tick 内一套都没交（修前为 0 套 + 18/18 死锁）")
    if used >= 18:
        errs.append(f"换卡箱顶满 {used}/18 —— 囤积未解决")
    return errs


def main() -> int:
    print("① 公式（箱位封顶）")
    errs = check_formula()
    print("② 端到端（生产默认路径：不勾 DP + 不限轮）")
    errs += asyncio.run(check_end2end())
    if errs:
        print("\n✗ 失败：")
        for e in errs:
            print("   -", e)
        return 1
    print("\n✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
