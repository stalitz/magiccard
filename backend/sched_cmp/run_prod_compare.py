"""生产引擎（当前接入页面的 auto_refine 逻辑）vs sched_cmp 四法（贪心/DP/A*/ILP）对比。

口径对齐（关键，否则数字不可比）：
  · 同一实例：指定 tid 主题、空箱（gift=0，无变卡）、3 主炉+1 偷炉=4 台机器、
    净需求 = recipes.compute_runs(N)（每张套内卡含 +1 收藏需求 → 30N 个 smelt 作业，
    具体数随主题卡片数变化）。
  · 生产引擎用**页面默认逻辑**（surplus_sets=0，按未收集 qq秀数定套数）：
    seed 已收集 qq秀使未收集数=N（--uncollected N），每上交 1 轮未收集数 -1、
    排产目标自动收缩 → 整个 run 恰好产出 compute_runs(N) 个作业（done multiset 与
    compute_runs(N) 逐一比对，不一致会报错）。
    ⚠ 不用 --sets N（固定套数）做多轮对比：net_plan 不扣已交轮次，每次上交后
    重排满 N 套 → 超产，数字不可比。
  · 生产引擎指标 = 最后一批作业的**精确完工时刻**（t_last，虚拟服务端记录 endtime），
    含 45s tick 粒度延迟；sched_cmp 指标 = executor.evaluate 的 makespan（事件驱动、连续时间）。
    两者差 ≤ 每事件 45s 的决策粒度，属实现差异而非调度质量。

用法：python run_prod_compare.py [--tid 290] [-t 求解预算秒] [sets ...]
  默认 tid=290；sets 默认 1 2 4。每个 tid 输出到 RESULTS_PROD_{tid}.md
  （tid=290 仍写 RESULTS_PROD.md，保持引用兼容）。
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

# ---- 参数 ----
argv = list(sys.argv[1:])
TID = 290
TIME_BUDGET = 60.0
if "--tid" in argv:
    i = argv.index("--tid"); TID = int(argv[i + 1]); del argv[i:i + 2]
if "-t" in argv:
    i = argv.index("-t"); TIME_BUDGET = float(argv[i + 1]); del argv[i:i + 2]
SETS_LIST = [int(x) for x in argv if x.isdigit()] or [1, 2, 4]
RECIPE = recipes.build_recipe_set(TID) if TID != 290 else recipes
OUT_MD = HERE / (f"RESULTS_PROD_{TID}.md" if TID != 290 else "RESULTS_PROD.md")


def run_prod_sim(sets, timeout=2400):
    """跑页面逻辑仿真（auto + seed 未收集=N + rounds=N），返回 SUMMARY dict。"""
    cache = HERE / f"prod_sim_{TID}_sets{sets}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    cmd = [PY, "-u", "-m", "backend._sim_faithful", "--tid", str(TID),
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
    runs = RECIPE.compute_runs(sets)
    expected = {int(c): int(v) for c, v in runs.items() if v > 0}
    done = {int(k): int(v) for k, v in sim["done"].items()}
    if done != expected:
        raise RuntimeError(f"tid={TID} sets={sets} 产量不一致！仿真 {done} vs 实例 {expected}")
    return expected


def solve_ref(sets, budget):
    """四法解同一实例，返回 {名字: (makespan_h, util, ok, 说明)}。

    预算说明：1星 sets=2 是「机器产能受限」硬核实例（总炉时/4 仅比最优低 0.5h），
    纯结构下界填不满缝隙，需较大搜索量才能证完最优性。
    - DP：相对状态记忆化（合并所有 t 平移重复态）+ 累资源下界，节点预算给足 80M、
      时限 240s → 可证完 1星 sets=2（约 34M 节点/167s）。
    - A*：best-first 在本类机器受限实例节点速率远低于 DP（24k/s vs 200k/s），
      即便 120s 仍难证完 1星 sets=2；但其返回的 14.0h 即真实最优（与其它法+贪心一致）。
    - ILP：时间索引 B&B（无 LP 松弛求解器），成败看结构，1星 sets=2 仍不证完（已知）。
    """
    inst = instance.build_instance(sets, 4, recipe=RECIPE)
    out = {}

    sc = sched_greedy.schedule(inst)
    ok, m = executor.evaluate(inst, sc)
    out["贪心基线"] = (m["makespan_h"], m["utilization"], ok, "list scheduling")

    ms, sc2, st = sched_dp.solve(inst, time_budget=max(budget, 240.0),
                                node_budget=80_000_000)
    ok2, m2 = executor.evaluate(inst, sc2)
    out["DP"] = (m2["makespan_h"], m2["utilization"], ok2,
                 f"{st['nodes']}节点/{st['time']:.2f}s" + ("" if not st["bounded"] else " 未证完"))

    ms, sc3, st = sched_astar.solve(inst, time_budget=min(budget, 120.0) if budget < 240 else 120.0,
                                   max_queue=12_000_000)
    ok3, m3 = executor.evaluate(inst, sc3)
    out["A*"] = (m3["makespan_h"], m3["utilization"], ok3,
                 f"{st['expanded']}展开/{st['time']:.2f}s" + ("" if not st["bounded"] else " 未证完"))

    ms, sc4, st = sched_ilp.solve(inst, time_budget=min(budget, 60.0))
    ok4, m4 = executor.evaluate(inst, sc4)
    out["ILP"] = (m4["makespan_h"], m4["utilization"], ok4,
                  f"{st['nodes']}节点/{st['time']:.2f}s" + ("" if not st["bounded"] else " 未证完"))
    return out


def main():
    results = {}
    for sets in SETS_LIST:
        sim = run_prod_sim(sets)
        check_work(sim, sets)
        ref = solve_ref(sets, TIME_BUDGET)
        results[sets] = {"sim": sim, "ref": ref}
        print(f"sets={sets} 生产引擎 t_last={sim['t_last_h']}h util={sim['util']} "
              f"交={sim['submit_times_h']} 作业数={sim['n_done']} ✓与实例一致", flush=True)
        for k, (mk, ut, ok, note) in ref.items():
            print(f"    {k:<6} {mk:6.2f}h util={ut:.3f} ok={ok} {note}", flush=True)

    # ---- 汇总表 ----
    lines = []
    lines.append(f"# 生产引擎（页面逻辑）vs sched_cmp 四法对比 —— {RECIPE.name}"
                 f"(tid={TID}, {RECIPE.star}星)")
    lines.append("")
    lines.append(f"日期：2026-10-20 · 生产引擎 = `backend/auto_refine.py`"
                 f"（页面现役逻辑，经 `backend/_sim_faithful.py` 忠实 tick 仿真："
                 f"只换网络+时钟，`tick()` 决策代码一字未改）")
    lines.append("")
    lines.append(f"实例：{RECIPE.name}(tid={TID}, {RECIPE.star}星)、空箱、无变卡、"
                 f"4 炉（3 主+1 偷）、净需求 30N 个作业"
                 "（= `recipes.compute_runs(N)`，每张套内卡含 +1 收藏需求，"
                 "实际作业数随主题卡片数变化）。"
                 "引擎用页面默认 auto 模式（按未收集 qq秀数定套数，seed 未收集=N），"
                 "产量 multiset 与实例逐一比对一致后才计入。")
    lines.append("")
    lines.append("| sets | 生产引擎(页面逻辑) | 贪心基线 | DP | A* | ILP(时间索引B&B) |")
    lines.append("|---|---|---|---|---|---|")
    for sets in SETS_LIST:
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
    # 耗时 / 搜索量（执行效率）
    lines.append("耗时 / 搜索量（纯算法求解用时，不含仿真）：")
    lines.append("")
    lines.append("| sets | 生产引擎仿真墙钟 | 贪心 | DP | A* | ILP(时间索引B&B) |")
    lines.append("|---|---|---|---|---|---|")
    for sets in SETS_LIST:
        sim = results[sets]["sim"]
        ref = results[sets]["ref"]
        lines.append(f"| {sets} | {sim.get('wall_s', 0):.1f}s（含 subprocess/网络模拟） "
                     f"| {ref['贪心基线'][3]} | {ref['DP'][3]} | {ref['A*'][3]} | {ref['ILP'][3]} |")
    lines.append("")
    # 生产引擎明细
    lines.append("生产引擎明细（t_last=最后作业精确完工时刻，含 45s tick 决策粒度）：")
    lines.append("")
    lines.append("| sets | 末次完工 | 各次上交 | 顶层卡完工 | 作业数 | 总炉时 | 利用率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for sets in SETS_LIST:
        sim = results[sets]["sim"]
        lines.append(f"| {sets} | {sim['t_last_h']:.2f}h | {sim['submit_times_h']} "
                     f"| {sim['top_times_h']} | {sim['n_done']} | {sim['work_h']}h "
                     f"| {sim['util']:.3f} |")
    lines.append("")
    # 差距分析
    lines.append("## 差距分析")
    lines.append("")
    for sets in SETS_LIST:
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
    OUT_MD.write_text(text, encoding="utf-8")
    print()
    print(text)


if __name__ == "__main__":
    main()
