"""实测：engine_v2 单套高星「贪心 vs DP」真实差距。

不引用 sched_cmp 旧数据——直接在同一引擎上两种模式各跑一遍，对比 makespan / 完成轮。
用法：python -m backend.engine_v2.tests.bench_dp
"""
from __future__ import annotations
import asyncio
import sys

from backend.engine_v2.engine import Engine


async def one(tid, dp_single, max_rounds, max_ticks):
    eng = Engine([tid], buy=True, max_rounds=max_rounds,
                 steal_enabled=True, dp_single=dp_single)
    vs, ticks = await eng.run_sim(change_cur=18, store_cur=200,
                                  max_ticks=max_ticks, interval=45)
    st = eng.stats
    done = sum(eng.completed_rounds.values())
    sim_h = round(ticks * 45 / 3600, 2)
    util = round(100.0 * st["slot_busy_ticks"] / st["slot_total_ticks"], 1) \
        if st["slot_total_ticks"] else 0.0
    return done, ticks, sim_h, util, st["refined"], st["bought"], \
        st["buy_fail"], st["errors"]


async def main():
    cases = [(59, "3星"), (60, "4星"), (709, "5星")]
    print(f"{'套卡':<14}{'模式':<8}{'目标轮':<6}{'完成':<6}{'耗时h':<10}"
          f"{'炉占%':<8}{'炼':<6}{'买':<6}{'买失败':<6}{'错':<4}")
    for tid, label in cases:
        for dp in (False, True):
            done, ticks, sim_h, util, rf, bt, bf, er = await one(
                tid, dp, 1, 40000)
            mode = "DP" if dp else "贪心"
            print(f"{label+'(tid'+str(tid)+')':<14}{mode:<8}{1:<6}{done:<6}"
                  f"{sim_h:<10}{util:<8}{rf:<6}{bt:<6}{bf:<6}{er:<4}")
        print("-" * 70)


if __name__ == "__main__":
    asyncio.run(main())
