"""聚焦对比（2026-10-23，JIT 预买修复后）：DP 控制器 vs 贪心，关键场景快速验证。

覆盖：单套全星阶(1轮) + 2套/3套代表组合(1轮)。max_rounds=1 控制 tick 数（3轮多套压力
测试已在历史会话验证 done=1/无溢出；本脚本只验证「JIT 预买修复」不回归 + DP 是否仍≤贪心）。
输出 DP 每 tick 解算耗时 profiling（max/avg solve_ms、solve 次数、bounded 占比）。
"""
import asyncio
from collections import Counter

from backend.engine_v2.tests.sim_report import run_one
from backend.engine_v2.engine import Engine

CASES = [
    # 单套全星阶（1 轮）
    ("单套·1星", [85], 1, 20000),
    ("单套·2星", [62], 1, 20000),
    ("单套·3星", [59], 1, 30000),
    ("单套·4星", [60], 1, 30000),
    ("单套·5星", [709], 1, 30000),
    # 2 套代表（含/不含 5 星）
    ("2套(4+3星)·1轮", [60, 59], 1, 20000),
    ("2套(4+5星)·1轮", [60, 709], 1, 30000),
    ("2套(1+5星)·1轮", [85, 709], 1, 30000),
    ("2套(2+3星)·1轮", [62, 59], 1, 20000),
    ("2套(1+2星)·1轮", [85, 62], 1, 20000),
    ("2套(3+5星)·1轮", [59, 709], 1, 30000),
    # 3 套代表
    ("3套(2+3+4星)·1轮", [62, 59, 60], 1, 25000),
    ("3套(3+4+5星)·1轮", [59, 60, 709], 1, 30000),
    ("3套(1+2+3星)·1轮", [85, 62, 59], 1, 25000),
    ("3套(1+4+5星)·1轮", [85, 60, 709], 1, 30000),
]

BOX = dict(change_cur=18, store_cur=200)


async def main():
    print("=" * 132)
    print(f"{'场景':<22}{'模式':<9}{'耗时h':>9}{'tick':>7}{'炉占%':>8}"
          f"{'完成':>6}{'买失败':>7}{'错误':>6}{'峰值箱':>9}{'overflow':>9}")
    print("=" * 132)
    bounded_total = 0
    solve_total = 0
    dp_wins = dp_losses = dp_ties = 0
    for name, tids, mr, mt in CASES:
        g = await run_one(name, tids, BOX, mr, mt, use_dp=False)
        eng = Engine(tids, buy=True, max_rounds=mr, steal_enabled=True, use_dp=True)
        eng.dp_ctrl.time_budget = 0.3
        vs, ticks = await eng.run_sim(change_cur=BOX["change_cur"],
                                      store_cur=BOX["store_cur"], max_ticks=mt,
                                      interval=45)
        st = eng.stats
        total_rounds = sum(eng.completed_rounds.values())
        sim_h = round(ticks * 45 / 3600, 2)
        util = round(100.0 * st["slot_busy_ticks"] / st["slot_total_ticks"], 1) \
            if st["slot_total_ticks"] else 0.0
        target = (mr * len(tids)) if mr > 0 else None
        ctrl = eng.dp_ctrl
        overflow = (st["buy_fail"] > 0) or (st["peak_box_used"] > BOX["change_cur"] + BOX["store_cur"])
        dp = {"done": total_rounds, "sim_h": sim_h, "ticks": ticks, "util": util,
              "buy_fail": st["buy_fail"], "errors": st["errors"],
              "peak_box": st["peak_box_used"], "overflow": overflow,
              "target": target,
              "solve_count": ctrl.solve_count,
              "solve_max_ms": round(ctrl.solve_max_ms, 2),
              "solve_avg_ms": round(ctrl.solve_total_ms / max(1, ctrl.solve_count), 2),
              "bounded": ctrl.bounded_count}
        print(f"{name:<22}{'贪心':<9}{g['sim_h']:>9}{g['ticks']:>7}{g['util']:>8}"
              f"{str(g['done']):>6}{g['buy_fail']:>7}{g['errors']:>6}"
              f"{g['peak_box']:>9}{str(g['overflow']):>9}")
        print(f"{'(DP)':<22}{'DP':<9}{dp['sim_h']:>9}{dp['ticks']:>7}{dp['util']:>8}"
              f"{str(dp['done']):>6}{dp['buy_fail']:>7}{dp['errors']:>6}"
              f"{dp['peak_box']:>9}{str(dp['overflow']):>9}  "
              f"[solve {dp['solve_count']}次 max {dp['solve_max_ms']}ms "
              f"avg {dp['solve_avg_ms']}ms bounded {dp['bounded']}]")
        if g["done"] >= g["target"] and dp["done"] >= dp["target"] and not dp["overflow"]:
            diff = dp["sim_h"] - g["sim_h"]
            if diff < -0.05:
                dp_wins += 1
                verdict = f"DP 快 {abs(diff):.2f}h"
            elif diff > 0.05:
                dp_losses += 1
                verdict = f"DP 慢 {diff:.2f}h"
            else:
                dp_ties += 1
                verdict = "持平"
            print(f"   → {verdict}  (DP炉占 {dp['util']}% vs 贪心 {g['util']}%)")
        else:
            print(f"   → ⚠异常: DP done={dp['done']}/{dp['target']} overflow={dp['overflow']}")
        print("-" * 132)
    print(f"\n汇总: DP 快 {dp_wins} / 慢 {dp_losses} / 持平 {dp_ties} （共 {len(CASES)} 场景）")


if __name__ == "__main__":
    asyncio.run(main())
