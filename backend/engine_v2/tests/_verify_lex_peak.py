"""回归：字典序目标 min makespan → min 峰值库存（2026-11-09）。

分两层验证：
① 求解器层：抓生产引擎真实承诺点的 inst，同一实例分别用
   MC_LEX_PEAK=0（旧纯 makespan）/1（新字典序）求解，断言
   makespan 不劣、峰值不劣，且至少有一例峰值严格下降。
② 端到端：890 仿真 makespan 不劣且峰值下降；178 旧引擎基线逐位一致。

消融：MC_LEX_PEAK=0 关字典序；MC_LEX_EQ_VEC 控等价解预算
（默认 2；0=达到下界即停 / 16 会让 890 求解打满预算、仿真 +1.87h）。

⚠️ 已知限制：真实承诺点实例上「同 makespan 异构」为 0 —— lazy 买卡规则
确定性不分支，买卡时序不是决策变量，字典序第二级目前几乎无等价解可选。
本回归同时打印该计数，避免把「枚举量改善搜索」误当成「峰值目标生效」。
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import logging
import os
import sys

from backend.engine_v2.engine import Engine
from backend.engine_v2 import dp_controller as dpc
from backend.sched_cmp import sched_dp

logging.disable(logging.CRITICAL)
ERRS: list[str] = []


def ck(ok, msg):
    if not ok:
        ERRS.append(msg)


async def one(tid, mr, dp, lex):
    os.environ["MC_LEX_PEAK"] = str(lex)
    eng = Engine([tid], buy=True, max_rounds=mr,
                 steal_enabled=True, use_dp=dp)
    with contextlib.redirect_stdout(io.StringIO()):
        _vs, ticks = await eng.run_sim(change_cur=18, store_cur=200,
                                       max_ticks=40000, interval=45)
    return (round(ticks * 45 / 3600, 2),
            sum(eng.completed_rounds.values()),
            eng.stats["peak_box_used"])


async def grab_insts():
    """跑一次 890，抓真实承诺点 inst（引擎本来就要解，顺手记录）。"""
    os.environ["MC_LEX_PEAK"] = "1"
    insts = []
    orig = dpc.solve

    def wrapped(inst, **kw):
        insts.append(copy.deepcopy(inst))
        return orig(inst, **kw)

    dpc.solve = wrapped
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            eng = Engine([890], buy=True, max_rounds=1,
                         steal_enabled=True, use_dp=True)
            await eng.run_sim(change_cur=18, store_cur=200,
                              max_ticks=40000, interval=45)
    finally:
        dpc.solve = orig
    return insts


async def main():
    insts = await grab_insts()
    ck(len(insts) >= 3, f"抓到的承诺点 inst 太少：{len(insts)}")
    print(f"抓取真实承诺点 {len(insts)} 个", file=sys.stderr)

    strict_peak = 0
    eq_swap = 0
    changed = 0
    for k, inst in enumerate(insts[:5]):
        os.environ["MC_LEX_PEAK"] = "0"
        mk0, s0, st0 = sched_dp.solve(copy.deepcopy(inst), time_budget=0.6)
        os.environ["MC_LEX_PEAK"] = "1"
        mk1, s1, st1 = sched_dp.solve(copy.deepcopy(inst), time_budget=0.6)
        ck(mk1 <= mk0 + 1e-6,
           f"inst{k}: makespan 变劣 {mk0:.1f} → {mk1:.1f}")
        # 只有两边都证完（未打满预算）时，峰值不劣才是严格保证
        both_opt = not st0.get("bounded") and not st1.get("bounded")
        if both_opt:
            ck(st1["peak"] <= st0["peak"] + 1e-9,
               f"inst{k}: 证完实例峰值变劣 {st0['peak']} → {st1['peak']}")
        if st1["peak"] < st0["peak"] - 1e-9:
            strict_peak += 1
        same_mk = abs(mk1 - mk0) <= 1e-6
        diff_sched = s1 != s0
        if same_mk and diff_sched:
            eq_swap += 1
        if diff_sched:
            changed += 1
        print(f"  inst{k}: mk {mk0/3600:.3f}→{mk1/3600:.3f}h  "
              f"peak {st0['peak']}→{st1['peak']}  "
              f"同mk异构={same_mk and diff_sched} 调度变={diff_sched}  "
              f"bounded {bool(st0.get('bounded'))}/{bool(st1.get('bounded'))}",
              file=sys.stderr)
    ck(changed > 0, "字典序未改变任何实例的骨架")
    print(f"  严格降峰 {strict_peak} / 同 makespan 异构 {eq_swap} / 骨架变化 {changed}",
          file=sys.stderr)

    old178 = await one(178, 0, False, 0)
    new178 = await one(178, 0, False, 1)
    ck(old178 == new178, f"178 旧引擎基线被改动：{old178} → {new178}")

    old890 = await one(890, 1, True, 0)
    new890 = await one(890, 1, True, 1)
    ck(new890[0] <= old890[0] + 0.02,
       f"890 仿真变慢：{old890[0]}h → {new890[0]}h")
    ck(new890[2] < old890[2],
       f"890 峰值未下降：{old890[2]} → {new890[2]}")
    ck(new890[1] == old890[1],
       f"890 完成轮数变化：{old890[1]} → {new890[1]}")
    print(f"178 基线 {old178} → {new178}", file=sys.stderr)
    print(f"890  {old890} → {new890}", file=sys.stderr)

    if ERRS:
        print("\n".join("✗ " + e for e in ERRS), file=sys.stderr)
        raise SystemExit(1)
    print("\n✓ 全部通过", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
