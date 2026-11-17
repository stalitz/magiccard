"""scratch：需求窗口口径 A/B —— 「空闲炉位+1」 vs 「主炉总数」。

背景（2026-11-17 用户定调）：
  「如果是 dp 这么排的，说明他觉得这是最优，但实际不是的话，一定是少输入条件了」
  `_calc_sets` 的需求窗口写的是 `min(5, free_slots+1)` —— 炉满（free_main=0）时
  窗口退化成 1 套，而 `max_rounds=0`（无限轮）这条已知信息只进了 pipe_sets 判断，
  没进窗口。于是 DP 的问题实例永远只有「当前 1~2 套」，让炉位空着不拖累这 2 套的
  makespan = 它眼里的最优。

A/B（生产零改动，只在测试里替换 `free_slots` 入参）：
  A = 现状：free_slots 原值 → 窗口 = 空闲炉位 + 1
  B = 候选：free_slots 强制 = 主炉总数 → 炉满也给满窗口

其余全同（同一 sim、同一 seed、同一引擎/执行层）。同时看 sim_h 与 peak_box，
不做单指标结论。
"""
import asyncio
import contextlib
import io
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("MC_DP_SETS_WINDOW", "0")

from backend import _sim_faithful as sim
from backend.auto_refine import AutoRefine
from backend.engine_v2.engine import Engine

logging.disable(logging.CRITICAL)

BASE_CALC = AutoRefine._calc_sets
SEEN: Counter = Counter()
MODE = "A"
MAIN_STOVES = 4

# 真机 10708702 口径：4 主炉（stovenum=4）+ 1 偷炉
sim.VirtualSession.STOVES = MAIN_STOVES
sim.VirtualSession.SLOTS_PER_STOVE = 2
sim.VirtualSession.MAIN_SLOTS = list(range(MAIN_STOVES * 2))
sim.VirtualSession.STEAL_SLOT = MAIN_STOVES * 2


def patch_calc() -> None:
    def calc(self, plan, stock, pending, free_slots=0, box_cur=0):
        fs = (MAIN_STOVES if MODE == "B"
              else 0 if MODE == "C" else free_slots)
        v = BASE_CALC(self, plan, stock, pending, fs, box_cur)
        SEEN[int(v)] += 1
        return v
    AutoRefine._calc_sets = calc


# ---- tick 级「该投没投」统计（本轮的判据：炉满时窗口退化 → 后续环无人排）----
BASE_LOG = AutoRefine._log
FREE_TICKS = 0          # 有空闲主炉的 tick 数
FREE_IDLE = 0           # 有空闲主炉但本 tick 零投炉
BUSY_TICKS = 0          # 无空闲主炉的 tick 数
_refined_now = [False]


def patch_log() -> None:
    def log(self, action, ok, detail=""):
        if ok and action in ("炼卡", "DP炼卡", "合成", "偷炉", "DP偷炉"):
            _refined_now[0] = True
        return BASE_LOG(self, action, ok, detail)
    AutoRefine._log = log


def make_tick_spy():
    orig_tick = AutoRefine.tick

    async def tick(self):
        _refined_now[0] = False
        await orig_tick(self)
        vs = self.s
        busy = {s // vs.SLOTS_PER_STOVE for s, st in vs.stoves.items()
                if s != vs.STEAL_SLOT and st["endtime"] - vs.now > 1e-6}
        free = max(0, vs.STOVES - len(busy))
        global FREE_TICKS, FREE_IDLE, BUSY_TICKS
        if free > 0:
            FREE_TICKS += 1
            if not _refined_now[0]:
                FREE_IDLE += 1
        else:
            BUSY_TICKS += 1
    AutoRefine.tick = tick


async def one(mode: str, store_cur: int, maxticks: int, seed: int = 0,
              timeout: float = 1800.0) -> dict:
    global MODE, FREE_TICKS, FREE_IDLE, BUSY_TICKS
    MODE = mode
    patch_calc()
    SEEN.clear()
    FREE_TICKS = FREE_IDLE = BUSY_TICKS = 0
    eng = Engine([1652], buy=True, max_rounds=0, steal_enabled=True,
                 use_dp=True, surplus_sets=0)
    collects: list[float] = []
    orig = eng._tally

    def tally(action, ok, detail):
        if action == "上交" and ok:
            collects.append(sim._VT["now"])
        orig(action, ok, detail)
    eng._tally = tally
    try:
        async def _run():
            with contextlib.redirect_stdout(io.StringIO()):
                return await eng.run_sim(change_cur=18, store_cur=store_cur,
                                         gift=0, max_ticks=maxticks,
                                         interval=45, seed=seed)
        t0 = sim._VT.get("now", 0) or 0
        vs, ticks = await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"mode": mode, "store": store_cur, "timeout": True}
    st = eng.stats
    itv = [round((b - a) / 60.0, 1)
           for a, b in zip([0.0] + collects[:-1], collects)]
    return {
        "mode": mode, "store": store_cur, "timeout": False,
        "h": round(ticks * 45 / 3600, 2),
        "done": st.get("collected", 0),
        "bought": st.get("bought", 0), "buy_fail": st.get("buy_fail", 0),
        "refined": st.get("refined", 0), "errors": st.get("errors", 0),
        "peak": st.get("peak_box_used", 0),
        "chg_peak": st.get("peak_change_used", 0),
        "store_peak": st.get("peak_store_used", 0),
        "furn": round((st.get("slot_busy_ticks", 0) or 0)
                      / max(1, st.get("total_ticks", 1)), 2),
        "idle": round(100 * (1 - (st.get("busy_ticks", 0) or 0)
                             / max(1, st.get("total_ticks", 1))), 1),
        "free_ticks": FREE_TICKS, "free_idle": FREE_IDLE,
        "busy_ticks": BUSY_TICKS,
        "collects": [round((c - t0) / 60.0, 1) for c in collects],
        "intervals": itv,
        "sets_seen": dict(SEEN),
        "dp": {"solves": getattr(eng.dp_ctrl, "solve_count", 0),
               "ms": round(getattr(eng.dp_ctrl, "solve_total_ms", 0), 0)},
    }


async def main():
    patch_log()
    make_tick_spy()
    if os.environ.get("MC_REAL_SECS") == "1":
        # 真机 10708702 实测时长（2026-11-17 日志回溯）：
        #   150 面 08:29 投 → 收卡跨 123 分钟；40 面 07:24 投 → 08:25 收 = 60.7 分钟。
        # 仿真默认口径（×0.6232）是 150=2.49h / 40=0.62h，40 面比真机快 38%，
        # 炉位比真机松 —— 结论可能反向，故提供真机口径档。
        sim.SECS[23928] = 7380
        for _c in (23927, 23926, 23925):
            sim.SECS[_c] = 3600
        print("  [真机时长口径] 150=123min / 40=60min", file=sys.stderr, flush=True)
    ticks = int(os.environ.get("MC_TICKS", "800"))
    stores = [int(x) for x in os.environ.get("MC_STORES", "59,200").split(",")]
    seeds = [int(x) for x in os.environ.get("MC_SEEDS", "0").split(",")]
    modes = os.environ.get("MC_MODES", "A,B").split(",")
    print(f"\n=== 1652 / 4主炉+1偷炉 / {ticks} ticks = {ticks*45/3600:.2f}h ===",
          file=sys.stderr, flush=True)
    out = []
    for seed in seeds:
        for store in stores:
            for mode in modes:
                r = await one(mode, store, ticks, seed)
                out.append(r)
                if r.get("timeout"):
                    print(f"  seed{seed} 保{store} {mode} TIMEOUT",
                          file=sys.stderr, flush=True)
                    continue
                ss = ",".join(f"{k}×{v}" for k, v in sorted(r["sets_seen"].items()))
                idle_pct = (100.0 * r["free_idle"] / r["free_ticks"]
                            if r["free_ticks"] else 0.0)
                print(f"  seed{seed} 保{store:<4}{mode}  完{r['done']:>3}套"
                      f"  {r['h']:>6.2f}h  买{r['bought']:>4} 炼{r['refined']:>4}"
                      f"  峰{r['peak']:>3} 换{r['chg_peak']:>3} 保{r['store_peak']:>3}"
                      f"  均占炉{r['furn']:>5.2f}  空炉tick{r['free_ticks']:>4}"
                      f"  该投没投{r['free_idle']:>4}({idle_pct:>4.1f}%)"
                      f"\n      上交@{r['collects']}  间隔{r['intervals']}  sets[{ss}]",
                      file=sys.stderr, flush=True)
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
