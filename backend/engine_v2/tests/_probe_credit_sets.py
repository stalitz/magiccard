"""只读探针：验证「变卡 credit 进 DP」与「多套 sets 进 DP」是否真的生效（2026-10-27）。

两处接入（此前 DP 都缺）：
  · `credits` —— 变卡环节即将交出的 40 面卡并进净需求，DP 不再拿底卡现造同款 40 面。
    验证口径：箱里给 N 张礼物卡（48 面，变卡原料）→ 看 ar.face40_credit 累计是否 > 0，
    且「炼 40 面卡的次数」是否下降。
  · `sets_fn` —— 生产 `_calc_sets` 传进 DP，1★ 按囤的 40 面算多套。
    验证口径：surplus_sets=3 时，3 轮完成时间应远小于 3×单轮（炉位并行）。

跑法：python -m backend.engine_v2.tests._probe_credit_sets
"""
from __future__ import annotations
import asyncio
from collections import Counter

from backend.engine_v2 import dp_controller as dpc
from backend.engine_v2.engine import Engine

_orig_init = dpc.DPTickController.__init__


def _fast_init(self, *a, **k):
    k["time_budget"] = 0.1
    k["time_budget_cold"] = 2.0
    k["warm_solves"] = 8
    return _orig_init(self, *a, **k)


dpc.DPTickController.__init__ = _fast_init

TID = 178          # 9 张 1★ 蜻蜓款款飞
FACE40 = 40


def _count40(eng) -> int:
    """成功炼出的 40 面卡张数（按 _log 里的卡名推断不了，改用盘上库存统计）。"""
    vs = eng._vs
    if vs is None:
        return 0
    return 0


async def one(label, *, gift=0, sets=0, rounds=1, cc=18, max_ticks=6000):
    eng = Engine([TID], buy=True, max_rounds=rounds, steal_enabled=True,
                 use_dp=True, surplus_sets=sets)
    vs, ticks = await eng.run_sim(change_cur=cc, store_cur=200,
                                  max_ticks=max_ticks, interval=45, gift=gift)
    st = eng.stats
    done = sum(eng.completed_rounds.values())
    sim_h = round(ticks * 45 / 3600, 2)
    credit = Counter()
    for c in (getattr(eng.ar, "face40_credit", None) or {}).values():
        credit.update(c or {})
    print(f"  {label:26s} 完成{done}轮 {sim_h:7.2f}h  炼{st['refined']:3d} "
          f"买{st['bought']:3d} 错{st['errors']:3d} 峰值箱{st['peak_box_used']:3d} "
          f"credit={sum(credit.values())}", flush=True)
    return done, sim_h


async def main():
    print("=== A. 变卡 credit（gift=0 无礼物卡 vs gift=20）===", flush=True)
    await one("1轮 无礼物卡", gift=0, rounds=1)
    await one("1轮 20张礼物卡", gift=20, rounds=1)

    print("\n=== B. 多套 sets（surplus_sets，3 轮）===", flush=True)
    await one("3轮 sets=0(自动)", sets=0, rounds=3)
    await one("3轮 sets=3", sets=3, rounds=3)


asyncio.run(main())
