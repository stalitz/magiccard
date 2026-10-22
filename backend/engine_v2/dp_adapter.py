"""DP 适配器：把 ThemeModel 转成 sched_dp 能解的类型级 RCPSP 实例，并产出精炼顺序。

关键：叶子底卡（is_base）是「买即来、无限供应、0 炉时」——从实例里剔除，
只作为非底卡素材的「无限可用前置」，不进 sched_dp 的 types/mats 消耗。
这样 sched_dp 只排「非底卡合成卡」在炉上的最优时序，与游戏真实语义一致。

复用 sched_cmp/sched_dp.solve（自包含、零第三方依赖、支持滚动重规划）。
"""
from __future__ import annotations
from typing import Dict, List, Tuple

from backend.sched_cmp.sched_dp import solve

from .model import ThemeModel


def build_dp_instance(model: "ThemeModel", sets: int, machines: int,
                      stock: dict | None = None, pending: dict | None = None) -> dict:
    """构造剔除底卡的 RCPSP 实例（复用 sched_dp.solve 的实例格式）。

    只保留需精炼的非底卡；底卡从 mats 里剥掉（当无限前置）。
    inst 需含 sched_dp 要求的键：dur / mats / machines / need0 / n / cp / depth。

    stock/pending：当前持有（箱内 + 炉内在炼）。net_plan 已按持有量净减需求，
    同时这些持有量作为 init_stock 喂给 solve——可作为父卡的免费素材消耗
    （滚动重规划：轮次边界库存不同 → 最优序不同）。
    """
    st = dict(stock or {})
    pd = dict(pending or {})
    runs, _buy = model.net_plan(st, pd, sets=sets)   # 净需求（按持有量净减）
    # types = 待炼的非底卡 ∪ 已持有但仍充当别人素材的非底卡。后者 runs=0（需求已被
    # 持有覆盖）却仍被父卡 mats 引用——必须进实例（need0=0 + init_stock 供消耗），
    # 否则 mats 索引越界（2026-10-22 滚动重规划实测 KeyError 176）。need0=0 类型
    # 在 sched_dp 里惰性（need[i]>0 才门控生产），stock 仍可被父卡消耗。
    held = {c: st.get(c, 0) + pd.get(c, 0) for c in model.ids if not model.is_base(c)}
    types = [cid for cid in model.ids
             if (not model.is_base(cid))
             and (runs.get(cid, 0) > 0 or held.get(cid, 0) > 0)]
    idx = {cid: i for i, cid in enumerate(types)}
    n = len(types)
    dur = [model.secs(cid) for cid in types]
    mats: List[Tuple] = []
    for cid in types:
        mc: Dict[int, int] = {}
        for f in model.feeders(cid):
            if not model.is_base(f) and f in idx:
                mc[idx[f]] = mc.get(idx[f], 0) + 1
        mats.append(tuple(sorted(mc.items())))
    need0 = [runs.get(cid, 0) for cid in types]
    depth = [model.depth.get(cid, 0) for cid in types]
    # 关键路径（链式下界），sched_dp._ub_schedule 需要 inst["cp"]/["depth"]
    cp = [0] * n

    def cpf(i: int) -> int:
        if cp[i]:
            return cp[i]
        cp[i] = dur[i] + max((cpf(j) for j, _ in mats[i]), default=0)
        return cp[i]

    for i in range(n):
        cpf(i)
    return {
        "machines": machines, "types": types, "n": n, "dur": dur,
        "mats": mats, "need0": need0, "depth": depth, "cp": cp,
        "init_stock": [st.get(cid, 0) + pd.get(cid, 0) for cid in types],
    }


def dp_order(model: "ThemeModel", sets: int = 1, machines: int = 3,
             time_budget: float = 2.0, node_budget: int = 8_000_000,
             stock: dict | None = None, pending: dict | None = None
             ) -> Tuple[List[int], dict]:
    """求 DP 最优精炼顺序（按 start time 升序的 cid 列表）。

    返回 (order, stats)：
      · order  = 精炼优先级顺序（cid 列表），引擎按此顺序做列表调度（list scheduling）
                 —— 对同构并行机，最优优先序的列表调度即最优 makespan。
      · stats  = sched_dp 统计（含 makespan_h / nodes / time_s / n_types 等）。

    machines 默认 3：建模的 3 主炉（可炼任意卡）；偷炉仅 40 面专属、作 bonus 不计入
    DP 容量（保守——引擎实际有 4 炉，故引擎能达成或优于 DP 估计）。

    stock/pending：滚动重规划用。传入当前持有量时，net_plan 净减需求 + init_stock
    免费素材，重解出的顺序按「当前真实状态」最优（轮次边界调用）。
    """
    inst = build_dp_instance(model, sets, machines, stock=stock, pending=pending)
    mk, sched, stats = solve(inst, time_budget=time_budget, node_budget=node_budget,
                             init_stock=inst["init_stock"])
    types = inst["types"]
    # sched: list of (type_idx, start_sec) —— 按 start_sec 升序即得精炼优先序
    order = [types[i] for (i, _t) in sorted(sched, key=lambda x: x[1])]
    return order, {
        "makespan_h": round(mk / 3600, 2),
        "nodes": stats.get("nodes"),
        "time_s": round(stats.get("time", 0), 3),
        "bounded": stats.get("bounded"),
        "opt": stats.get("opt"),
        "n_types": inst["n"],
    }
