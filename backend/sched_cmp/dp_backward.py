"""倒排（backward）可行性构造器 —— 把「每层子卡在父卡开工那一刻齐套」变成可执行排程。

思路（对应用户的炼法）：makespan 只由顶层卡决定，所以从目标完工时刻 T 往回收：
    顶层卡最晚开工 = T - dur[顶层]
    → 它的三个子卡 deadline = 该时刻（齐套零等待）
    → 再往下一层的 deadline = 子卡自己的开工时刻
每一层都按「尽量贴近 deadline」（即尽量晚）反向放置，于是子卡的完工时刻自然
贴近父卡开工时刻；机器竞争造成的错开就是「接近度」的损耗。

★ 定位：给 DP 提供 incumbent / 上界，以及一个独立的可行性判定器。
  它本身是启发式（反向序列 SGS + 插空），不保证最优，必须过 validate_pools。

用法：
    from dp_backward import solve_backward, backward_at
    mk, sched, meta, st = solve_backward(inst)          # 自动找最小可行 T
    ok, plan = backward_at(inst, T)                     # 给定 T 判可行
"""

from __future__ import annotations

import heapq

EPS = 1e-6
BIG = 1e18


# ------------------------------------------------------------------ 基础工具
def _prep(inst):
    n = int(inst["n"])
    dur = [float(x) for x in inst["dur"]]
    mats = [[(int(j), int(k)) for j, k in row] for row in inst["mats"]]
    need = [int(x) for x in inst["need0"]]
    is_base = [1 in tuple(c) for c in inst["mclass"]]
    return n, dur, mats, need, is_base


def _best_slot(free, d, deadline, allowed=None):
    """在若干台机器的空闲区间里，找「start 最大且 finish<=deadline」的位置。

    free[mi] = [(ls, le), ...]（升序）。返回 (start, mi, li)，放不下返回 None。
    allowed 限定可用机位（mclass=(0,) 的卡只能上主炉，禁止进偷炉）。
    """
    best = None
    rng = allowed if allowed is not None else range(len(free))
    for mi in rng:
        ivs = free[mi]
        # 从最右的区间往左找：右侧区间给出的 start 一定更大
        for li in range(len(ivs) - 1, -1, -1):
            ls, le = ivs[li]
            hi = le if le < deadline else deadline
            s = hi - d
            if s >= ls - EPS and s >= -EPS:
                if best is None or s > best[0] + EPS:
                    best = (s, mi, li)
                break
    return best


def _take(free, mi, li, s, d):
    """占用 [s, s+d]，切割该机器的空闲区间。"""
    ivs = free[mi]
    ls, le = ivs[li]
    e = s + d
    if s <= ls + EPS and e >= le - EPS:
        del ivs[li]
    elif s <= ls + EPS:
        ivs[li] = (e, le)
    elif e >= le - EPS:
        ivs[li] = (ls, s)
    else:
        ivs[li:li + 1] = [(ls, s), (e, le)]


# ------------------------------------------------------------------ 工序实例化
def _build_ops(n, dur, mats, need, is_base):
    """把 need0 展开成具体 op，并把子卡 op 分配给父卡 op。

    同型卡可互换，所以「先被父卡认领的」就是被消耗的，剩下的是纯交付。
    """
    ops = []
    by_type = [[] for _ in range(n)]
    for i in range(n):
        for _ in range(need[i]):
            k = len(ops)
            ops.append({"i": i, "kids": [], "par": [], "s": None, "dl": None})
            by_type[i].append(k)
    ptr = [0] * n
    for p in range(len(ops)):
        i = ops[p]["i"]
        if is_base[i]:
            continue
        for j, k in mats[i]:
            for _ in range(k):
                if ptr[j] >= len(by_type[j]):
                    return None, None
                c = by_type[j][ptr[j]]
                ptr[j] += 1
                ops[p]["kids"].append(c)
                ops[c]["par"].append(p)
    return ops, by_type


# ------------------------------------------------------------------ 倒排一次
LAST_FAIL = {}


def backward_at(inst, T, pool_tie="cp", debug=False):
    """给定目标完工时刻 T，反向构造一张排程。失败返回 None。

    返回 dict(sched=[(type,start)], pools=[(pool,type,start)], buys=[(type,order_t)],
              mk=..., spread=子卡完工离散度统计)
    """
    n, dur, mats, need, is_base = _prep(inst)
    ops, by_type = _build_ops(n, dur, mats, need, is_base)
    if ops is None:
        LAST_FAIL.clear(); LAST_FAIL.update(where="build_ops")
        return None
    M = int(inst["machines"])
    S = int(inst.get("steal_cap") or 0)
    NM = max(1, M + S)
    LAG = float(inst.get("shop_lag") or 45.0)
    cp = inst.get("cp") or [0.0] * n
    free = [[(0.0, T)] for _ in range(NM)]

    # class 限制：mclass 含 2 的（40 面卡）可进偷炉；含 0 不含 2 的只能上主炉
    cls = [tuple(c) for c in inst["mclass"]]

    def _allowed(i):
        if S and 2 in cls[i]:
            return range(NM)
        return range(M)

    # 初始待排：没有父卡的 op（顶层 + 纯交付的收藏卡），deadline = T
    heap = []
    for k, op in enumerate(ops):
        if not op["par"]:
            op["dl"] = T
            i = op["i"]
            # 同 deadline 时先排「关键路径长 / 工期长」的，减少碎片
            heapq.heappush(heap, (-T, -float(cp[i]), -dur[i], k))

    buys = []
    n_ok = 0
    while heap:
        _ndl, _ncp, _nd, k = heapq.heappop(heap)
        op = ops[k]
        i = op["i"]
        dl = op["dl"]
        if is_base[i]:
            buys.append((i, dl))          # dl = 必须到货的时刻
            op["s"] = dl
            n_ok += 1
            continue
        d = dur[i]
        got = _best_slot(free, d, dl, _allowed(i))
        if got is None:
            LAST_FAIL.clear(); LAST_FAIL.update(where="slot", i=i, dl=dl / 60, d=d / 60,
                                                rest=sum(1 for o in ops if o["s"] is None))
            return None
        s, mi, li = got
        _take(free, mi, li, s, d)
        op["s"] = s
        op["m"] = mi
        for c in op["kids"]:
            cop = ops[c]
            if cop["dl"] is not None:
                continue
            cop["dl"] = s
            cj = cop["i"]
            heapq.heappush(heap, (-s, -float(cp[cj]), -dur[cj], c))
        n_ok += 1

    if n_ok != len(ops):
        return None

    # ---- 底卡下单：单机串行 45s，尽量晚（JIT），每张只需 到货 = 下单 + LAG <= dl
    sfree = [[(0.0, BIG)]]
    buys_sorted = sorted(buys, key=lambda x: -x[1])
    buys_out = []
    for i, arr in buys_sorted:
        latest = arr - LAG
        if latest < -EPS:
            LAST_FAIL.clear(); LAST_FAIL.update(where="buy_neg", i=i, arr=arr / 60)
            return None
        got = _best_slot(sfree, 45.0, latest)
        if got is None:
            LAST_FAIL.clear(); LAST_FAIL.update(where="buy_slot", i=i, latest=latest / 60)
            return None
        t, mi, li = got
        _take(sfree, mi, li, t, 45.0)
        buys_out.append((i, t))

    rows = sorted([(op["s"], op["i"], op.get("m", 0)) for op in ops
                   if not is_base[op["i"]]], key=lambda x: x[0])
    sched = [(i, s) for s, i, _m in rows]
    pools = [("main" if m < M else "steal", i, s) for s, i, m in rows]
    return {"sched": sched, "pools": pools, "buys": buys_out, "mk": T,
            "_ops": ops, "_is_base": is_base}


def _assign_pools(inst, sched):
    """按开工时刻贪心分配主炉/偷炉（与 solve 的 pools 口径一致，仅供 validate 用）。"""
    M = int(inst["machines"])
    S = int(inst.get("steal_cap") or 0)
    dur = [float(x) for x in inst["dur"]]
    cls = [tuple(c) for c in inst["mclass"]]
    mf = [0.0] * M
    sf = [0.0] * S
    out = []
    for i, t in sched:
        free_m = [k for k in range(M) if mf[k] <= t + EPS]
        if free_m:
            k = min(free_m, key=lambda x: mf[x])
            mf[k] = t + dur[i]
            out.append(("main", i, t))
            continue
        # 只有 mclass 含 2（40 面卡）才允许进偷炉
        if S and 2 in cls[i]:
            free_s = [k for k in range(S) if sf[k] <= t + EPS]
            if free_s:
                k = min(free_s, key=lambda x: sf[x])
                sf[k] = t + dur[i]
                out.append(("steal", i, t))
                continue
        out.append(("main", i, t))   # 交给 validate 报 machine_full
    return out


def _spread_stats(ops, dur, is_base):
    """子卡完工时刻相对父卡开工的离散度（齐套接近度）。"""
    gaps, area = [], 0.0
    for op in ops:
        if not op["kids"] or is_base[op["i"]]:
            continue
        fins = [ops[c]["s"] for c in op["kids"]]
        if any(f is None for f in fins):
            continue
        sp = op["s"] - min(fins)
        gaps.append(sp)
        area += sum(op["s"] - f for f in fins)
    if not gaps:
        return {"spread_med": 0.0, "spread_max": 0.0, "wait_area": 0.0}
    gaps.sort()
    return {"spread_med": gaps[len(gaps) // 2], "spread_max": gaps[-1],
            "wait_area": area}


# ------------------------------------------------------------------ 外层：找最小可行 T
def solve_backward(inst, T_lo=None, T_hi=None, step=None, max_try=40,
                   log_path=None, verbose=False):
    """从下界开始向上试探 T，找到第一个可行解，再在 [失败, 可行] 之间二分细化。

    返回 (mk, sched, meta, stats)，与 dp_plan3.solve 同口径：
      sched=[(type,start)]，meta={"buys":[(type,t)], "pools":[...]}，mk=校验后 makespan
    """
    from dp_plan3 import validate_pools

    dur = [float(x) for x in inst["dur"]]
    n0 = int(inst["n"])
    cp = inst.get("cp") or [0.0] * n0
    lo = float(T_lo if T_lo is not None else max(cp))
    hi = float(T_hi or 0.0) or None
    step = float(step or max(60.0, lo * 0.02))

    best = None
    fails = []
    T = lo
    tries = 0
    while tries < max_try:
        tries += 1
        r = backward_at(inst, T)
        if r is not None:
            pools = _assign_pools(inst, r["sched"])
            v = validate_pools(inst, r["sched"], pools, r["buys"])
            if v["ok"]:
                best = (v["makespan"], r, pools, v)
                break
            fails.append((T, v["viol"][:2]))
        else:
            fails.append((T, "construct_fail"))
        if hi and T >= hi:
            break
        T = T + step
        step *= 1.15
    if best is None:
        return None, None, {"fails": fails}, {"ok": False, "fails": fails}

    mk_ok, r_ok, pools_ok, v_ok = best
    # 二分：在 [最后失败 T, 成功的 T] 之间收窄
    lo_f = max([t for t, _ in fails if t < mk_ok], default=lo)
    hi_f = mk_ok
    for _ in range(14):
        if hi_f - lo_f <= 1.0:
            break
        mid = (lo_f + hi_f) / 2.0
        rr = backward_at(inst, mid)
        ok = False
        if rr is not None:
            pp = _assign_pools(inst, rr["sched"])
            vv = validate_pools(inst, rr["sched"], pp, rr["buys"])
            if vv["ok"]:
                ok = True
                mk_ok, r_ok, pools_ok, v_ok = vv["makespan"], rr, pp, vv
        if ok:
            hi_f = mid
        else:
            lo_f = mid
    ops = r_ok["_ops"]
    st = _spread_stats(ops, dur, r_ok["_is_base"])
    st.update({"ok": True, "makespan": mk_ok, "tries": tries,
               "peak_chg": v_ok["peak_chg"], "peak_ref": v_ok["peak_ref"],
               "T_target": hi_f, "buys": len(r_ok["buys"]),
               "ops": len(r_ok["sched"])})
    if verbose:
        print("[BWD] mk=%.2fmin  spread_med=%.2f  wait_area=%.1f格·min  peak_chg=%d"
              % (mk_ok / 60, st["spread_med"] / 60, st["wait_area"] / 60, st["peak_chg"]))
    return mk_ok, r_ok["sched"], {"buys": r_ok["buys"], "pools": pools_ok}, st
