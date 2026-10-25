"""CP-SAT 精确/近似求解器（生产用）：与 `sched_dp.solve` **同契约**的求解后端。

契约（与 sched_dp.solve 一致）：
    mk, sched, stats = solve(inst, time_limit=120.0, ...)
      · mk     剩余 makespan（秒，相对当前时刻）
      · sched  [(type_idx, start_sec)]，每张卡/每次买卡一条
      · stats  dict（status/proven/bound/n_vars...）

与 sched_dp 的差别（能力边界，必须清楚）：
  · sched_dp：DFS + memo + LDS 截断 + 2s anytime → 快、但只是启发式（2★ 实测差真最优 18%）。
  · cp_sat   ：作业级 RCPSP-with-materials 精确模型，靠约束传播+分支定界 →
              1★ 11s 证最优；2★~5★ 180~240s 出 anytime 可行解，已全线优于 sched_dp。
  · 代价：单次 100s 量级，**不能进每 tick 的滚动循环**。只适合
    「冷启/离线出全局骨架」→ 喂 executor 滚动执行。

模型要点（时间单位 = 该 instance 全部 dur 的 gcd，绝不能用固定 45）：
  · duration 决策：start/end，e == s + dur。
  · 机器容量 AddCumulative：主炉 machines / 买卡 shop_cap / 偷炉 steal_cap。
  · class 表 [0,2]（40 面卡）：主炉 or 偷炉二选一（optional interval）。
  · 物料：mats[i] 的 k 张 j 由 assignment boolean 绑定，end[j] ≤ start[i]。
  · 目标 min makespan。
  · init_stock / init_running：**当前只支持冷启（全空）**。
    非冷启直接返回 None，由调用方回退 sched_dp（滚动重解走 sched_dp 快路径）。
"""
import math
import time as _t

from ortools.sat.python import cp_model

UNIT_FALLBACK = 45.0


def _unit_of(dur):
    g = 0
    for d in dur:
        g = math.gcd(g, int(round(d)))
    return float(g) if g > 0 else UNIT_FALLBACK


def _cls_of(mclass, i):
    c = mclass[i]
    return tuple(c) if isinstance(c, (list, tuple)) else (int(c),)


def is_cold_start(inst):
    """CP 模型只支持冷启（空箱 + 空炉）。"""
    stock = inst.get("init_stock")
    if stock and any(int(x) > 0 for x in stock):
        return False
    if inst.get("init_running"):
        return False
    if inst.get("init_machine_free"):
        if any(float(x) > 0 for x in inst["init_machine_free"]):
            return False
    return True


def solve(inst, time_limit=120.0, workers=8, verbose=False, **_kw):
    """→ (mk_sec, sched, stats)。不可解/不支持时 sched 为 None。"""
    n = inst["n"]
    need0 = [int(x) for x in inst["need0"]]
    dur = [float(x) for x in inst["dur"]]
    mats = inst["mats"]
    mclass = inst.get("mclass") or [0] * n
    M = int(inst["machines"])
    S = int(inst.get("shop_cap") or 0)
    steal = int(inst.get("steal_cap") or 0)

    if not is_cold_start(inst):
        return None, None, {"status": "UNSUPPORTED_WARM", "wall": 0.0,
                            "n_vars": 0, "n_bool": 0, "backend": "cp_sat"}

    unit = _unit_of(dur)
    dur_t = [int(round(d / unit)) for d in dur]
    H = sum(dur_t) + 1

    model = cp_model.CpModel()
    start = [[None] * need0[i] for i in range(n)]
    end = [[None] * need0[i] for i in range(n)]
    mode = [[None] * need0[i] for i in range(n)]

    main_it, shop_it, steal_it = [], [], []
    for i in range(n):
        cls = _cls_of(mclass, i)
        for u in range(need0[i]):
            s = model.NewIntVar(0, H, f"s_{i}_{u}")
            e = model.NewIntVar(0, H, f"e_{i}_{u}")
            model.Add(e == s + dur_t[i])
            start[i][u], end[i][u] = s, e
            if cls == (1,):
                shop_it.append(model.NewIntervalVar(s, dur_t[i], e, f"it_{i}_{u}"))
                mode[i][u] = ("shop",)
            elif cls == (0,):
                main_it.append(model.NewIntervalVar(s, dur_t[i], e, f"it_{i}_{u}"))
                mode[i][u] = ("main",)
            elif 0 in cls and 2 in cls:
                b0 = model.NewBoolVar(f"b0_{i}_{u}")
                b2 = model.NewBoolVar(f"b2_{i}_{u}")
                model.Add(b0 + b2 == 1)
                main_it.append(model.NewOptionalIntervalVar(
                    s, dur_t[i], e, b0, f"it0_{i}_{u}"))
                steal_it.append(model.NewOptionalIntervalVar(
                    s, dur_t[i], e, b2, f"it2_{i}_{u}"))
                mode[i][u] = ("ms", b0, b2)
            elif cls == (2,):
                steal_it.append(model.NewIntervalVar(s, dur_t[i], e, f"it_{i}_{u}"))
                mode[i][u] = ("steal",)
            else:
                raise ValueError(f"未知 mclass {cls} at type {i}")

    if main_it:
        model.AddCumulative(main_it, [1] * len(main_it), M)
    if shop_it:
        model.AddCumulative(shop_it, [1] * len(shop_it), max(1, S))
    if steal_it:
        model.AddCumulative(steal_it, [1] * len(steal_it), max(0, steal))

    use_pool = {}
    n_bool = 0
    for i in range(n):
        for (j, k) in mats[i]:
            j, k = int(j), int(k)
            if need0[i] == 0 or need0[j] == 0:
                continue
            for u in range(need0[i]):
                us = []
                for v in range(need0[j]):
                    b = model.NewBoolVar(f"u_{i}_{u}_{j}_{v}")
                    us.append(b)
                    use_pool.setdefault((j, v), []).append(b)
                    model.Add(end[j][v] <= start[i][u]).OnlyEnforceIf(b)
                    n_bool += 1
                model.Add(sum(us) == k)
    for _key, lst in use_pool.items():
        model.Add(sum(lst) <= 1)

    mk = model.NewIntVar(0, H, "mk")
    for i in range(n):
        for u in range(need0[i]):
            model.Add(mk >= end[i][u])
    model.Minimize(mk)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_search_workers = int(workers)
    if verbose:
        solver.parameters.log_search_progress = True
    t0 = _t.perf_counter()
    status = solver.Solve(model)
    wall = _t.perf_counter() - t0
    st = solver.StatusName(status)
    stats = {"status": st, "wall": wall, "n_bool": n_bool,
             "n_vars": len(model.Proto().variables), "unit": unit,
             "backend": "cp_sat", "nodes": 0, "time": wall, "bounded": False,
             "memo_hits": 0}
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        stats["opt"] = None
        return None, None, stats

    mkt = solver.Value(mk)
    sched = []
    detail = []
    for i in range(n):
        for u in range(need0[i]):
            stt = solver.Value(start[i][u])
            sched.append((i, stt * unit))
            m = mode[i][u]
            if m[0] == "ms":
                which = "main" if solver.Value(m[1]) == 1 else "steal"
            else:
                which = m[0]
            detail.append((i, u, stt, dur_t[i], which))
    stats.update(mk_tick=mkt, bound=solver.BestObjectiveBound(),
                 proven=(status == cp_model.OPTIMAL),
                 opt=(status == cp_model.OPTIMAL), detail=detail)
    return mkt * unit, sched, stats


def precise_check(detail, M, S, steal, unit=UNIT_FALLBACK):
    """按实际 mode 的事件扫描容量检查（权威口径）。"""
    ev = {}
    for (_i, _u, stt, d, which) in detail:
        ev.setdefault(stt, {}).setdefault(which, 0)
        ev[stt][which] += 1
        ev.setdefault(stt + d, {}).setdefault(which, 0)
        ev[stt + d][which] -= 1
    cur = {"main": 0, "shop": 0, "steal": 0}
    cap = {"main": M, "shop": S, "steal": steal}
    for t in sorted(ev):
        for k in ("main", "shop", "steal"):
            cur[k] += ev[t].get(k, 0)
            if cur[k] > cap[k]:
                return False, f"{k}超容@{t * unit:.0f}s {cur[k]}>{cap[k]}"
    return True, "OK"
