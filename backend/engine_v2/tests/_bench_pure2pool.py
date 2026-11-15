"""条件给全（三空位贯穿买卡）+ 纯 DP 的全量仿真 A/B。

三档，唯一变量 = 交给 controller 的 solve 实现：
  BASE  生产 `sched_cmp.sched_dp.solve`（上线版，交付 UB 兜底）
  CAND  候选 `_sched_dp_search.solve`（POOL_FIX 已默认开，仍允许 UB 兜底）
  PURE  候选 + MC_DP_PURE=1（交付解必须来自 dfs 自身；POOL_FIX 默认开）

判据（铁律）：`sim_h` 与 `peak_box` **同看**——单实例 makespan 不是跨轮吞吐的
好代理；另外 `done`（完成轮数）、`overflow`、`refined` 必须不劣。

用法：
  python -m backend.engine_v2.tests._bench_pure2pool --modes BASE,PURE
  python -m backend.engine_v2.tests._bench_pure2pool --quick
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import backend.engine_v2.dp_controller as M
import backend.engine_v2._dp_controller_candidate2 as C2
import backend.sched_cmp.sched_dp as BASE_S
import backend.sched_cmp._sched_dp_search as CAND_S
from backend.engine_v2.tests.sim_report import run_one

BASE_DPC = M.DPTickController
BOX = dict(change_cur=18, store_cur=200)
TIGHT = dict(change_cur=18, store_cur=60)
# (名称, tids, 箱, max_rounds, max_ticks, use_dp)
CASES = [
    ("1星·单轮", [85], BOX, 1, 20000, True),
    ("2星·单轮", [62], BOX, 1, 20000, True),
    ("3星·单轮", [59], BOX, 1, 25000, True),
    ("4星·单轮", [60], BOX, 1, 25000, True),
    ("5星·单轮", [709], BOX, 1, 25000, True),
    ("1星×3轮", [85], BOX, 3, 25000, True),
    ("4星·无限炼(mr=0)", [60], BOX, 0, 12000, True),
    ("4星·无限炼·贪心(dp=False,mr=0)", [60], BOX, 0, 12000, False),
    ("2套(4+3)×2轮", [60, 59], BOX, 2, 30000, True),
    ("3套(4+3+2)·1轮", [60, 59, 62], BOX, 1, 30000, True),
    ("紧箱2套(2+3)·1轮", [62, 59], TIGHT, 1, 15000, True),
]
QUICK = [CASES[0], CASES[1], CASES[3], CASES[4], CASES[9], CASES[10]]


def _one(args):
    name, tids, box, mr, mt, dp, mode = args
    os.environ.pop("MC_DP_PURE", None)
    # 档位：全部用**生产 controller**（单一变量 = solver），只换 solve 实现。
    # ★ 2026-11-15：候选 controller 的两个差异（deadlock_report 默认 0 / `_keep_old`）
    #   与纯 DP 无关，却单独带来 +3.1h（3套 96.0→99.12），故 A/B 不再混入。
    M.DPTickController = BASE_DPC
    if mode == "BASE":
        os.environ["MC_DP_PURE"] = "0"      # 生产默认已是纯 DP，A/B 需显式关
        M.solve = C2.solve = BASE_S.solve
    else:
        M.solve = C2.solve = CAND_S.solve
        if mode == "PURE":
            os.environ["MC_DP_PURE"] = "1"
    t0 = time.perf_counter()
    r = asyncio.run(run_one(name, tids, box, mr, mt, use_dp=dp))
    dt = time.perf_counter() - t0
    keep = {k: r.get(k) for k in
            ("sim_h", "ticks", "done", "util", "refined", "bought", "buy_fail",
             "errors", "peak_box", "overflow", "hit_cap")}
    keep["wall"] = round(dt, 1)
    return (name, mode, keep)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="BASE,CAND,PURE")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="/tmp/pure2pool.json")
    a = ap.parse_args()
    cases = QUICK if a.quick else CASES
    if a.only:
        cases = [c for c in cases if any(k in c[0] for k in a.only.split(","))]
    modes = a.modes.split(",")
    jobs = [(c[0], c[1], c[2], c[3], c[4], c[5], m) for c in cases for m in modes]
    rows = {}
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(_one, j): j for j in jobs}
        for f in as_completed(futs):
            name, mode, r = f.result()
            rows.setdefault(name, {})[mode] = r
            print(f"{name:<30} {mode:<5} {r['sim_h']:>7}h done={r['done']} "
                  f"ref={r['refined']} peak={r['peak_box']} "
                  f"ovf={r['overflow']} err={r['errors']} "
                  f"wall={r['wall']}s", flush=True)
    print("\n=== 汇总（sim_h / done / peak_box）===")
    bad = []
    for name, d in rows.items():
        b = d.get("BASE", {})
        line = f"{name:<30}"
        for m in modes:
            r = d.get(m, {})
            line += f" | {m} {r.get('sim_h')}h/{r.get('done')}轮 pk{r.get('peak_box')}"
        print(line, flush=True)
        if "PURE" in d and "BASE" in d:
            p, bs = d["PURE"], d["BASE"]
            if p["done"] < bs["done"]:
                bad.append(f"{name}: 完成轮数下降 {bs['done']}→{p['done']}")
            if p["overflow"] or p["buy_fail"]:
                bad.append(f"{name}: PURE 溢出/买失败 ovf={p['overflow']} "
                           f"bf={p['buy_fail']}")
            if p["sim_h"] - bs["sim_h"] > 0.5:
                bad.append(f"{name}: sim_h 变慢 {bs['sim_h']}→{p['sim_h']}")
            if p["peak_box"] > bs["peak_box"] + 2:
                bad.append(f"{name}: peak_box 升高 {bs['peak_box']}→{p['peak_box']}")
    print("\nVERDICT:", "PASS" if not bad else "FAIL")
    for x in bad:
        print("  -", x)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "bad": bad}, f, ensure_ascii=False, indent=1)
    print("saved", a.out)


if __name__ == "__main__":
    asyncio.run(main())
