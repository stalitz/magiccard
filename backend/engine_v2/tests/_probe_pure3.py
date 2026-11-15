"""纯 DP 在 3 套场景下的重解序列诊断。

目的（2026-11-15）：3 套 PURE 仿真 106.19h ≈ 根解 105.34h（重规划零收益），
而 BASE 从根解 100.75h 滚到 96.0h。需要看清每次 warm 重解到底交付了什么：
空解？dive 解？还是 105 的 B&B 解？以及 warm 预算是否被 dive 吃干。

用法:
  python -m backend.engine_v2.tests._probe_pure3 --ticks 3000
"""
import argparse
import asyncio
import json
import os
import sys

import backend.engine_v2.dp_controller as M
import backend.sched_cmp._sched_dp_search as CAND_S
import backend.sched_cmp.sched_dp as BASE_S
from backend.engine_v2.tests.sim_report import run_one

BASE_DPC = M.DPTickController
CALLS = []


def _wrap(mod, tag):
    orig = mod.solve
    calls = [0]

    def spy(inst, **kw):
        calls[0] += 1
        mk, sched, st = orig(inst, **kw)
        if len(CALLS) < 400:
            CALLS.append({
                "n": calls[0], "tb": kw.get("time_budget"),
                "mk": None if mk is None or mk > 1e8 else round(mk / 3600.0, 2),
                "ops": 0 if not sched else len(sched),
                "dive": st.get("dive_v") and round(st["dive_v"] / 3600.0, 2),
                "ub": round((st.get("ub0") or 0) / 3600.0, 2),
                "from": st.get("from"),
                "need": sum(inst.get("need0") or []),
                "nodes": st.get("nodes"),
                "bounded": st.get("bounded"),
            })
        return mk, sched, st
    mod.solve = spy
    return orig


async def main(want: str, ticks: int, tid_arg: str, stoves: int):
    tids = [int(x) for x in tid_arg.split(",")]
    M.DPTickController = BASE_DPC
    if want == "BASE":
        M.solve = _wrap(BASE_S, "base")
        os.environ.pop("MC_DP_PURE", None)
    else:
        os.environ["MC_DP_PURE"] = "1"
        M.solve = _wrap(CAND_S, "cand")
    r = await run_one("probe3", tids, dict(change_cur=18, store_cur=200),
                      1, ticks, use_dp=True)
    print(f"[{want}] sim_h={r.get('sim_h')} done={r.get('done')} "
          f"peak={r.get('peak_box')} refined={r.get('refined')} "
          f"bought={r.get('bought')} ticks={r.get('ticks')} err={r.get('errors')}")
    print("solve calls:", len(CALLS))
    tot_ops = sum(c["ops"] or 0 for c in CALLS)
    empty = sum(1 for c in CALLS if not c["ops"])
    print(f"  empty={empty} sum_ops={tot_ops} "
          f"mk={[c['mk'] for c in CALLS[:18]]}")
    print(f"  ops={[c['ops'] for c in CALLS[:18]]}")
    print(f"  dive={[c['dive'] for c in CALLS[:18]]}")
    print(f"  from={[c['from'] for c in CALLS[:18]]}")
    print(f"  need={[c['need'] for c in CALLS[:18]]}")
    print(f"  tb={[c['tb'] for c in CALLS[:18]]}")
    for c in CALLS[:8]:
        print("   ", c)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="PURE")
    ap.add_argument("--ticks", type=int, default=3000)
    ap.add_argument("--tids", default="60,59,62")
    ap.add_argument("--stoves", type=int, default=3)
    a = ap.parse_args()
    asyncio.run(main(a.mode, a.ticks, a.tids, a.stoves))
