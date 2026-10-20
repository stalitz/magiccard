"""生产引擎（当前接入页面的 auto_refine 逻辑）vs sched_cmp 四法（贪心/DP/A*/ILP）对比。

口径对齐（关键，否则数字不可比）：
  · 同一实例：tid=290 七雄Q传、空箱（gift=0，无变卡）、3 主炉+1 偷炉=4 台机器、
    净需求 = recipes.compute_runs(N)（每张套内卡含 +1 收藏需求 → 30N 个 smelt 作业）。
  · 生产引擎用**页面默认逻辑**（surplus_sets=0，按未收集 qq秀数定套数）：
    seed 已收集 qq秀使未收集数=N（--uncollected N），每上交 1 轮未收集数 -1、
    排产目标自动收缩 → 整个 run 恰好产出 30N 个作业（done multiset 与
    compute_runs(N) 逐一比对，不一致会报错）。
    ⚠ 不用 --sets N（固定套数）做多轮对比：net_plan 不扣已交轮次，每次上交后
    重排满 N 套 → 超产（实测 sets=2 产出 85 张 vs 应 60 张），数字不可比。
  · 生产引擎指标 = 最后一批作业的**精确完工时刻**（t_last，虚拟服务端记录 endtime），
    含 45s tick 粒度延迟；sched_cmp 指标 = executor.evaluate 的 makespan（事件驱动、连续时间）。
    两者差 ≤ 每事件 45s 的决策粒度，属实现差异而非调度质量。

跑法：python run_prod_compare.py [-t 求解预算秒]
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent          # 项目根（backend/_sim_faithful 用 -m 跑）
PY = "/Users/stalitz/.workbuddy/binaries/python/envs/default/bin/python"
sys.path.insert(0, str(HERE))

import instance, executor, sched_greedy, sched_dp, sched_astar, sched_ilp, recipes  # noqa: E402


def run_prod_sim(sets, timeout=2400):
    """跑页面逻辑仿真（auto + seed 未收集=N + rounds=N），返回 SUMMARY dict。"""
    cache = HERE / f"prod_sim_sets{sets}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    cmd = [PY, "-u", "-m", "backend._sim_faithful", "--tid", "290",
           "--gift", "0", "--steal", "on",
           "--uncollected", str(sets), "--rounds", str(sets)]
    t0 = time.time()
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                         timeout=timeout)
    for line in out.stdout.splitlines():
        if line.startswith("=== SUMMARY "):
            s = json.loads(line[len("=== SUMMARY "):])
            s["wall_s"] = round(time.time() - t0, 1)
            cache.write_text(json.dumps(s, ensure_ascii=False, indent=1),
                             encoding="utf-8")
            return s
    raise RuntimeError("仿真无 SUMMARY 输出：\n" + out.stdout[-1500:] + out.stderr[-800:])


def check_work(sim, sets):
    """仿真产量 multiset 必须精确等于 compute_runs(sets)，否则对比不成立。"""
    runs = recipes.compute_runs(sets)
    expected = {int(c): int(v) for c, v in runs.items() if v > 0}
    done = {int(k): int(v) for k, v in sim["done"].items()}
    if done != expected:
        raise RuntimeError(f"sets={sets} 产量不一致！仿真 {done} vs 实例 {expected}")
    return expected


def solve_ref(sets, budget):
    """四法解同一实例，返回 {名字: (makespan_h, util, ok, 说明)}。"""
    inst = instance.build_instance(sets, 4)
    out = {}

    sc = sched_greedy.schedule(inst)
    ok, m = executor.evaluate(inst, sc)
    out["贪心基线"] = (m["makespan_h"], m["utilization"], ok, "list scheduling")

    ms, sc2, st = sched_dp.solve(inst, time_budget=budget)
    ok2, m2 = executor.evaluate(inst, sc2)
    out["DP"] = (m2["makespan_h"], m2["utilization"], ok2,
                 f"{st['nodes']}节点/{st['time']:.2f}s" + ("" if not st["bounded"] else " 未证完"))

    ms, sc3, st = sched_astar.solve(inst, time_budget=budget)
    ok3, m3 = executor.evaluate(inst, sc3)
    out["A*"] = (m3["makespan_h"], m3["utilization"], ok3,
                 f"{st['expanded']}展开/{st['time']:.2f}s" + ("" if not st["bounded"] else " 未证完"))

    ms, sc4, st = sched_ilp.solve(inst, time_budget=budget)
    ok4, m4 = executor.evaluate(inst, sc4)
    out["ILP"] = (m4["makespan_h"], m4["utilization"], ok4,
                  f"{st['nodes']}节点/{st['time']:.2f}s" + ("" if not st["bounded"] else " 未证完"))
    return out


def main():
    budget = 60.0
    if "-t" in sys.argv:
        budget = float(sys.argv[sys.argv.index("-t") + 1])

    results = {}
    for sets in (1, 2, 4):
        sim = run_prod_sim(sets)
        check_work(sim, sets)
        ref = solve_ref(sets, budget)
        results[sets] = {"sim": sim, "ref": ref}
        print(f"sets={sets} 生产引擎 t_last={sim['t_last_h']}h util={sim['util']} "
              f"交={sim['submit_times_h']} 作业数={sim['n_done']} ✓与实例一致", flush=True)
        for k, (mk, ut, ok, note) in ref.items():
            print(f"    {k:<6} {mk:6.2f}h util={ut:.3f} ok={ok} {note}", flush=True)

    # ---- 汇总表 ----
    lines = []
    lines.append("# 生产引擎（页面逻辑）vs sched_cmp 四法对比")
    lines.append("")
    lines.append("日期：2026-10-20 · 生产引擎 = `backend/auto_refine.py`（页面现役逻辑，经 "
                 "`backend/_sim_faithful.py` 忠实 tick 仿真：只换网络+时钟，`tick()` 决策代码一字未改）")
    lines.append("")
    lines.append("实例：tid=290 七雄Q传、空箱、无变卡、4 炉（3 主+1 偷）、净需求 30N 个作业"
                 "（= `recipes.compute_runs(N)`，每张套内卡含 +1 收藏需求）。"
                 "引擎用页面默认 auto 模式（按未收集 qq秀数定套数，seed 未收集=N），"
                 "产量 multiset 与实例逐一比对一致后才计入。")
    lines.append("")
    lines.append("| sets | 生产引擎(页面逻辑) | 贪心基线 | DP | A* | ILP(时间索引B&B) |")
    lines.append("|---|---|---|---|---|---|")
    for sets in (1, 2, 4):
        sim = results[sets]["sim"]
        ref = results[sets]["ref"]
        def cell(name):
            mk, ut, ok, note = ref[name]
            flag = "" if ok else " ⚠️不可行"
            done = " ✅证完" if ("未证完" not in note) else " ❌未证完"
            return f"{mk:.2f}h{flag}{done}"
        lines.append(f"| {sets} | **{sim['t_last_h']:.2f}h**（利用率{sim['util']:.3f}） "
                     f"| {cell('贪心基线')} | {cell('DP')} | {cell('A*')} | {cell('ILP')} |")
    lines.append("")
    lines.append("生产引擎明细（t_last=最后作业精确完工时刻，含 45s tick 决策粒度）：")
    lines.append("")
    lines.append("| sets | 末次完工 | 各次上交 | 顶层卡(西施)完工 | 作业数 | 总炉时 | 利用率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for sets in (1, 2, 4):
        sim = results[sets]["sim"]
        lines.append(f"| {sets} | {sim['t_last_h']:.2f}h | {sim['submit_times_h']} "
                     f"| {sim['top_times_h']} | {sim['n_done']} | {sim['work_h']}h "
                     f"| {sim['util']:.3f} |")
    lines.append("")
    # 差距分析
    lines.append("## 差距分析")
    lines.append("")
    for sets in (1, 2, 4):
        sim = results[sets]["sim"]
        best = min(v[0] for v in results[sets]["ref"].values())
        ref = results[sets]["ref"]
        dp = ref["DP"][0]
        gap = sim["t_last_h"] - dp
        lines.append(f"- **sets={sets}**：引擎 {sim['t_last_h']:.2f}h vs DP最优 {dp:.2f}h，"
                     f"落后 **{gap:+.2f}h**（{gap / dp * 100:+.1f}%）；"
                     f"vs 贪心基线 {ref['贪心基线'][0]:.2f}h，"
                     f"{'领先' if sim['t_last_h'] < ref['贪心基线'][0] else '落后'} "
                     f"{abs(sim['t_last_h'] - ref['贪心基线'][0]):.2f}h")
    lines.append("")
    text = "\n".join(lines)
    (HERE / "RESULTS_PROD.md").write_text(text, encoding="utf-8")
    print()
    print(text)


if __name__ == "__main__":
    main()
