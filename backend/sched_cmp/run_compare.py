"""三法（DP / A* / ILP）+ 贪心基线 对比：各自跑仿真，比 makespan / 可行性 / 耗时 / 节点。

四者完全独立实现、互不引用；统一进 executor.evaluate 校验
（素材充足 + 产能 + 产量达标）。ILP 为零第三方依赖的自写 B&B（时间索引）。
用法：从本目录 `python run_compare.py [-t 秒数] [sets ...]`，默认 sets=1 2 4。
"""
import sys
import time as _t

import instance
import executor
import sched_greedy
import sched_dp
import sched_astar
import sched_ilp

MACHINES = 4
args = [a for a in sys.argv[1:]]
TIME_BUDGET = 60.0
if "-t" in args:
    i = args.index("-t")
    TIME_BUDGET = float(args[i + 1])
    del args[i:i + 2]
SETS_LIST = [int(x) for x in args if x.isdigit()] or [1, 2, 4]


def run_one(sets):
    inst = instance.build_instance(sets, MACHINES)
    print(f"\n=== sets={sets} | {instance.summary(inst)} ===")
    rows = []

    # 贪心基线
    t0 = _t.time()
    a = sched_greedy.schedule(inst)
    ok, m = executor.evaluate(inst, a)
    rows.append(("贪心基线", m["makespan_h"], m["utilization"], _t.time() - t0,
                 "-", ok, False))

    # DP / A* / ILP
    for name, mod in (("DP", sched_dp), ("A*", sched_astar), ("ILP", sched_ilp)):
        try:
            t0 = _t.time()
            ms, a, st = mod.solve(inst, time_budget=TIME_BUDGET)
            el = _t.time() - t0
            ok, m = executor.evaluate(inst, a)
            nd = st.get("nodes", st.get("expanded", "-"))
            rows.append((name, m["makespan_h"] if ok else None,
                         m["utilization"] if ok else None, el, nd, ok,
                         bool(st.get("bounded"))))
        except Exception as e:                     # noqa: BLE001
            rows.append((name, None, None, _t.time() - t0, "-", False, None))
            print(f"  {name}: ERROR {e!r}")

    print(f"  {'方法':<8}{'makespan':>10}{'可行':>6}{'利用率':>8}{'耗时s':>9}"
          f"{'节点/展开':>12}{'搜完':>5}")
    for name, mk, ut, el, nd, ok, bd in rows:
        mk_s = f"{mk:.2f}h" if mk else "-"
        ok_s = "是" if ok else ("超时" if bd else "否")
        ut_s = f"{ut:.3f}" if ut else "-"
        bd_s = "-" if bd is None else ("是" if not bd else "否")
        print(f"  {name:<8}{mk_s:>10}{ok_s:>6}{ut_s:>8}{el:>9.2f}{str(nd):>12}{bd_s:>5}")
    return rows


if __name__ == "__main__":
    print(f"时间预算 {TIME_BUDGET:.0f}s/法；机器={MACHINES}；实例=七雄Q传(tid=290) 2星4层配方")
    for s in SETS_LIST:
        run_one(s)
    print("\n完成。")
