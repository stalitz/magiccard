"""DP/A* 版本对照工具：修复前(git 714ef02) vs 修复后(当前)，同预算跑同一实例。

用途：任何「某次代码修改带来了多少收益」的结论，都必须用本工具做**同预算对照**才能下，
否则会把「预算截断值」误读成「方法不行」，或把「加预算后能证完」误归因给代码修复。

★ 口径铁律：RECIPE 选择必须与 run_compare.py / run_prod_compare.py **完全一致**
  （tid==290 用模块级 recipes，其余用 build_recipe_set(tid)）。
  两者类型索引序不同（升序 vs 降序）→ 分支遍历序不同 → 搜索树不同 → 节点数可差数倍
  （makespan 相同）。口径不一致的对照数字没有可比性。

用法：
    python sched_cmp/cmp_dp_versions.py            # DP，9 实例，240s/80M 节点
    python sched_cmp/cmp_dp_versions.py --astar     # A*，1星 sets=2，120s
"""
import subprocess
import sys
import tempfile
import time as _t
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import instance
import recipes

HERE = Path(__file__).resolve().parent
OLD_COMMIT = "714ef02"      # 修复前的 commit（相对状态规范化 + 累资源下界 之前）
TIME_BUDGET = 240.0
NODE_BUDGET = 80_000_000
ASTAR_BUDGET = 120.0


def _load_old(fname: str, alias: str):
    """从 git 取回旧版实现为临时模块（不落仓库）。"""
    src = subprocess.run(
        ["git", "-C", str(HERE.parent.parent), "show", f"{OLD_COMMIT}:backend/sched_cmp/{fname}"],
        capture_output=True, text=True, check=True).stdout
    tmp = Path(tempfile.mkdtemp()) / f"{alias}.py"
    tmp.write_text(src)
    sys.path.insert(0, str(tmp.parent))
    return __import__(alias)


def _recipe(tid: int):
    """与 run_compare.py 完全一致。"""
    return recipes.build_recipe_set(tid) if tid != 290 else recipes


def cmp_dp():
    import sched_dp
    old_dp = _load_old("sched_dp.py", "_old_dp_cmp")
    print(f"{'实例':<18}{'版本':<6}{'makespan':>10}{'nodes':>14}{'memo_hits':>13}"
          f"{'time':>9}{'证完':>6}")
    print("-" * 78)
    for tid in (78, 290, 311):
        rec = _recipe(tid)
        for sets in (1, 2, 4):
            inst = instance.build_instance(sets, 4, recipe=rec)
            tag = f"tid{tid} sets={sets}"
            for name, mod in (("旧", old_dp), ("新", sched_dp)):
                t0 = _t.time()
                ms, _sched, st = mod.solve(inst, time_budget=TIME_BUDGET,
                                           node_budget=NODE_BUDGET)
                el = _t.time() - t0
                print(f"{tag:<18}{name:<6}{ms/3600:>9.2f}h{st.get('nodes'):>14,}"
                      f"{st.get('memo_hits'):>13,}{el:>8.2f}s"
                      f"{('否' if st.get('bounded') else '是'):>6}", flush=True)
            print(flush=True)


def cmp_astar():
    import sched_astar
    old_astar = _load_old("sched_astar.py", "_old_astar_cmp")
    inst = instance.build_instance(2, 4, recipe=_recipe(78))
    for name, mod in (("旧", old_astar), ("新", sched_astar)):
        t0 = _t.time()
        ms, _sched, st = mod.solve(inst, time_budget=ASTAR_BUDGET)
        el = _t.time() - t0
        print(f"{name}: makespan={ms/3600:.2f}h expanded={st.get('expanded'):,} "
              f"time={el:.1f}s 证完={not st.get('bounded')}", flush=True)


if __name__ == "__main__":
    if "--astar" in sys.argv:
        cmp_astar()
    else:
        cmp_dp()
    print("完成。")
