"""只读探针：1★「9 张卡」套 vs「13 张卡」套在紧箱下的差别（用户 2026-10-27 指正）。

用户原话：「我说你测试一个 9 张卡的 1 星，你非要选一个 13 张卡的，结果卡箱本来
10 格够，现在不够了」。

- 13 张套：tid=85 国粹京剧（684~696）
-  9 张套：tid=178 蜻蜓款款飞 / 179 瓢虫物语 / 185 橡皮泥的记忆 / 922 果味酸奶 /
           1652 七夕情人节
真机口径：换卡箱 18 格硬顶，其中 9 格长期被用户藏品卡占用 → 实际 9 格可用；
上交要求「整套 distinct 卡各 1 张同时持有」（两箱合计都算）。

跑法：python -m backend.engine_v2.tests._probe_1star_9v13
"""
from __future__ import annotations
import asyncio

from backend.engine_v2 import dp_controller as dpc
from backend.engine_v2.engine import Engine

_orig_init = dpc.DPTickController.__init__


def _fast_init(self, *a, **k):
    k["time_budget"] = 0.1
    k["time_budget_cold"] = 2.0
    k["warm_solves"] = 8
    return _orig_init(self, *a, **k)


dpc.DPTickController.__init__ = _fast_init

CASES = [(85, "13张 国粹京剧", 9), (85, "13张 国粹京剧", 18),
         (178, "9张 蜻蜓款款飞", 9), (178, "9张 蜻蜓款款飞", 18),
         (185, "9张 橡皮泥的记忆", 9)]


async def one(tid, label, change_cur, max_ticks=4000):
    eng = Engine([tid], buy=True, max_rounds=1, steal_enabled=True, use_dp=True)
    vs, ticks = await eng.run_sim(change_cur=change_cur, store_cur=200,
                                  max_ticks=max_ticks, interval=45)
    st = eng.stats
    done = sum(eng.completed_rounds.values())
    sim_h = round(ticks * 45 / 3600, 2)
    print(f"  {label:16s} 换卡箱{change_cur:2d}格 完成{done}轮 {sim_h:6.2f}h  "
          f"炼{st['refined']:3d} 买{st['bought']:3d} 错{st['errors']:3d} "
          f"峰值箱{st['peak_box_used']}", flush=True)
    return done, sim_h


async def main():
    for tid, label, cc in CASES:
        await one(tid, label, cc)


asyncio.run(main())
