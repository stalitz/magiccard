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


def _earliest_slot(free, d, ready, deadline, allowed=None):
    """找「start 最早、start>=ready、finish<=deadline」的位置（前向压实用）。

    与 `_best_slot` 相反：那个是 latest-fit（尽量晚开工 → 前段留空），
    这个是 earliest-fit（能开就开 → 不空炉），deadline 仍是硬上界。
    """
    best = None
    rng = allowed if allowed is not None else range(len(free))
    for mi in rng:
        for li, (ls, le) in enumerate(free[mi]):
            s = ls if ls > ready + EPS else ready
            if s + d <= le + EPS and s + d <= deadline + EPS:
                if best is None or s < best[0] - EPS:
                    best = (s, mi, li)
                break          # 该机器区间升序，第一个放得下的就是最早
    return best


def _idle_area(free, horizon):
    """[0, horizon] 内的机位空闲面积（秒·台），越小 = 炉越满。"""
    tot = 0.0
    for ivs in free:
        for ls, le in ivs:
            a = max(ls, 0.0)
            b = min(le, horizon)
            if b > a:
                tot += b - a
    return tot


def _fwd_once(inst, ops, is_base, T, arr, wait=0.0, force=None):
    """一次前向 ASAP：底卡按 arr 到货，非底卡 EDF + earliest-fit。

    与倒排相反 —— 倒排的 `_best_slot` 是 latest-fit（尽量晚开工，前段留空炉），
    前向是「能开就开」。deadline 仍取倒排链，只当上界。
    wait>0 时允许「等到 deadline 前 wait 秒再开」（给箱位留余量）。
    force = {op: 最早开工时刻}，箱位闸要求被推迟的那几张。
    """
    n, dur, mats, need, _ib = _prep(inst)
    M = int(inst["machines"])
    S = int(inst.get("steal_cap") or 0)
    NM = max(1, M + S)
    cls = [tuple(c) for c in inst["mclass"]]

    def _allowed(i):
        if S and 2 in cls[i]:
            return range(NM)
        return range(M)

    free = [[(0.0, BIG)] for _ in range(NM)]
    end = [None] * len(ops)
    start = [None] * len(ops)
    for k, a in arr.items():
        end[k] = a
        start[k] = a
    late_n = [0]
    todo = [k for k, op in enumerate(ops) if not is_base[op["i"]]]
    while todo:
        pick = None
        for k in todo:
            op = ops[k]
            r = 0.0
            ok = True
            for c in op["kids"]:
                e = end[c]
                if e is None:
                    ok = False
                    break
                if e > r:
                    r = e
            if not ok:
                continue
            dl = op["dl"] if op["dl"] is not None else T
            f = r + dur[op["i"]]
            key = (dl, f, k)            # EDF：deadline 紧的先排
            if pick is None or key < pick[0]:
                pick = (key, k, r, dl)
        if pick is None:
            LAST_FAIL.clear(); LAST_FAIL.update(where="fwd_cycle")
            return None
        _, k, r, dl = pick
        i = ops[k]["i"]
        d = dur[i]
        if wait > 0.0:
            r = max(r, dl - wait)
        if force:
            r = max(r, force.get(k, 0.0))
        got = _earliest_slot(free, d, r, dl, _allowed(i))
        if got is None:                 # deadline 顶不住 → 放宽（记为 late）
            late_n[0] += 1
            got = _earliest_slot(free, d, r, BIG, _allowed(i))
            if got is None:
                LAST_FAIL.clear(); LAST_FAIL.update(where="fwd_slot", i=i, r=r / 60)
                return None
        s, mi, li = got
        _take(free, mi, li, s, d)
        end[k] = s + d
        start[k] = s
        ops[k]["s"] = s
        ops[k]["m"] = mi
        todo.remove(k)
    mk = max([e for k, e in enumerate(end) if not is_base[ops[k]["i"]]], default=0.0)
    return {"start": start, "end": end, "mk": mk, "late": late_n[0],
            "idle": _idle_area(free, mk)}


def _chg_conflict(ops, buys_idx, start, CHG, is_base):
    """换卡箱闸：底卡从「下单」到「父卡开工消耗」一直占格（与 validate 同口径）。

    返回超限时刻里**父卡开工最晚**的那张底卡的父卡 op（推迟它最省），
    不超限返回 None。
    """
    seg = []
    for k, t in buys_idx:
        par = ops[k]["par"]
        if not par:
            # 纯交付的底卡：买来就常驻换卡箱直到上交 → 一直占格
            seg.append((t, BIG, k))
            continue
        if start[par[0]] is None:
            continue
        seg.append((t, max(t, start[par[0]]), par[0]))
    if not seg:
        return None
    pts = sorted(set([a for a, _b, _p in seg] + [b for _a, b, _p in seg if b < BIG / 2]))
    for tt in pts:
        cur = [(b, p, is_base[ops[p]["i"]]) for a, b, p in seg if a <= tt < b]
        if len(cur) > CHG:
            cand = [x for x in cur if not x[2]]      # 只能推迟父卡（常驻底卡挪不动）
            if not cand:
                return None
            cand.sort(key=lambda x: -x[0])
            return cand[0][1]
    return None


def _forward_fill(inst, ops, is_base, T, iters=5, wait=0.0, rounds=40):
    """前向压实：把倒排算出的 deadline 链当上界，重排成「不空炉」的 ASAP 排程。

    两层迭代：
      内层 = 底卡到货与父卡开工的不动点（下单 JIT，受 shop_cap 排队）；
      外层 = 换卡箱 18 格闸（ASAP 会让十几个父卡挤在同一时刻要底卡 →
             同时到货爆箱），把冲突父卡推迟一个 tick 后重跑。
    """
    n, dur, mats, need, _ib = _prep(inst)
    LAG = float(inst.get("shop_lag") or 45.0)
    SHOP = max(1, int(inst.get("shop_cap") or 1))
    CHG = int(inst.get("change_cap") or 18)
    base_ids = [k for k, op in enumerate(ops) if is_base[op["i"]]]
    if not base_ids:
        return None
    force = {}
    best = None
    for _r in range(rounds):
        arr = {k: LAG for k in base_ids}
        for _it in range(iters):
            r = _fwd_once(inst, ops, is_base, T, arr, wait, force)
            if r is None:
                return None
            start = r["start"]
            want = {}
            for k in base_ids:
                par = ops[k]["par"]
                if par:
                    # 底卡最早到货 = LAG（t=0 下单），父卡开工再早也快不过它
                    want[k] = max([start[p] for p in par] + [LAG])
                else:
                    # 纯交付底卡：买来常驻换卡箱 → 尽量晚买（卡在交付时刻）
                    want[k] = max(float(ops[k]["dl"] or T), LAG)
            sfree = [[(0.0, BIG)] for _ in range(SHOP)]
            buys = []
            buys_idx = []
            new_arr = {}
            for k in sorted(base_ids, key=lambda x: -want[x]):
                got = _best_slot(sfree, LAG, want[k])   # 下单尽量晚（JIT）
                if got is None:                          # 这批排不下 → 顺延
                    got = _earliest_slot(sfree, LAG, 0.0, BIG)
                    if got is None:
                        LAST_FAIL.clear(); LAST_FAIL.update(where="buy_slot",
                                                            want=want[k] / 60)
                        return None
                t, mi, li = got
                _take(sfree, mi, li, t, LAG)
                buys.append((ops[k]["i"], t))
                buys_idx.append((k, t))
                new_arr[k] = t + LAG
            best = {"mk": r["mk"], "buys": buys, "late": r["late"],
                    "idle": r["idle"], "ops": len(base_ids), "chg_rounds": _r}
            if all(abs(new_arr[k] - arr[k]) < 1e-6 for k in base_ids):
                break
            arr = new_arr
        conf = _chg_conflict(ops, buys_idx, start, CHG, is_base)
        if conf is None:
            return best
        prev = force.get(conf)
        nxt = float(start[conf] or 0.0) + LAG
        if prev is not None and abs(nxt - prev) < 1e-6:
            nxt = prev + LAG
        force[conf] = nxt
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
    # ★★ 2026-11-22「多 deadline / 按套错峰」（第二版，承接「倒排是启发」定调）：
    #   旧版所有根 op 共用同一个 deadline T → 每套都压到同一条末尾链上，N 套
    #   在时间轴上完全重叠，箱里同时躺着 N 套的半成品。
    #   `MC_BWD_STAGGER=f`（f∈(0,1]）→ **按「顶层卡的型别」错峰**：depth 最大的
    #   那批根 op，每个**型别**一个 deadline（同型多张 = 同一主题的多套，共用
    #   同一个 dl），型别按关键路径降序（最长链先开工）取
    #   `dl = T - (k-1-o) * STAG`，STAG = f * max(cp) / k（k = 顶层型别数）。
    #   其余根 op（纯交付余料、收藏卡）仍取 dl = T。
    #   ⚠ 两处口径坑（都实测过）：
    #     ① 「全部无父 op 数」当 k 不行：3 主题实例 roots=73（= 类型数），
    #        错峰被摊到 73 个无关 op 上（STAG 11.6min），等于没按主题错峰；
    #     ② 「按 op 数」不行：同主题 3 套会把它三张顶层卡错成三个 deadline
    #        （954 三套 18.79→19.41h），同主题同配方没有错峰的必要。
    #   k=1（单主题）时 STAG 无意义 → 与旧口径逐位一致。f=0（默认）= 旧行为。
    _F = float(inst.get("_bwd_stag_f") or 0.0)
    _roots = [k for k, op in enumerate(ops) if not op["par"]]
    _depth = [int(x) for x in (inst.get("depth") or [0] * n)]
    _maxd = max(_depth) if _depth else 0
    _top_types = sorted({ops[k]["i"] for k in _roots
                         if _depth[ops[k]["i"]] >= _maxd})
    _stag = 0.0
    _wave: dict = {}
    if _F > 0.0 and len(_top_types) > 1:
        _top_types.sort(key=lambda i: (-float(cp[i]), -dur[i], i))
        _cp_max = max([float(x) for x in cp] or [0.0])
        _stag = _F * _cp_max / float(len(_top_types))
        for _o, _i in enumerate(_top_types):
            _wave[_i] = _o
    heap = []
    for k, op in enumerate(ops):
        if not op["par"]:
            i = op["i"]
            if _stag > 0.0 and i in _wave and _depth[i] >= _maxd:
                dl = T - (len(_top_types) - 1 - _wave[i]) * _stag
                if dl < float(cp[i]):       # 不早于这张自己的关键路径
                    dl = float(cp[i])
            else:
                dl = T
            op["dl"] = dl
            # 同 deadline 时先排「关键路径长 / 工期长」的，减少碎片
            heapq.heappush(heap, (-dl, -float(cp[i]), -dur[i], k))

    buys = []
    n_ok = 0
    # ★ 2026-11-22「取卡延迟」诊断开关（默认 0，**实测无效，保留作反例**）：
    #   立论 = 炼成卡要下一 tick 取出才能投新炉，倒排把「完工」当「可用」，
    #   每层少一个 tick → ops 越多越乐观。加 DRAW 后子卡 deadline 提前一个
    #   tick（寿司 hint 48.63→49.50h、蜻蜓 3.14→3.17h）。
    #   实测（451 引擎级，DRAW=45 vs 0）：执行 **79.06h → 79.06h 逐位相同**。
    #   → 「延迟累积」不是寿司劣化的主因（hint 只决定冷启首解的前几个动作，
    #     之后每 tick 滚动重解都与 hint 无关）。真因是首解的开炉节奏：
    #     倒排是 JIT 尽量晚开，执行层却需要「有空炉就开满」才追得上。
    import os as _os
    DRAW = float(_os.environ.get("MC_BWD_DRAW") or 0.0)
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
            cop["dl"] = s - DRAW
            cj = cop["i"]
            heapq.heappush(heap, (-s, -float(cp[cj]), -dur[cj], c))
        n_ok += 1

    if n_ok != len(ops):
        return None

    # ---- 底卡下单：尽量晚（JIT），每张只需 到货 = 下单 + LAG <= dl ----
    # ★★ 2026-11-22 口径修正：旧版把商店建成**单机串行**（一次只能买 1 张），
    #   而 DP/真机口径是 `shop_cap=15`（每 tick 最多买 15 张并行）→ 倒排的
    #   买卡时刻被凭空推迟、料到得更晚。makespan 不受影响（下单时刻只是
    #   「最晚可下单」，提前下不影响 sched），但 buys 时刻表整体偏晚，于是
    #   「让 DFS 按 hint 时刻买卡」的闸门把人卡死（实测 BUY_FOLLOW 下
    #   954 的 leaf 4→0）。这里改成 shop_cap 台并行机，与 DP 模型同口径。
    SHOP = max(1, int(inst.get("shop_cap") or 1))
    sfree = [[(0.0, BIG)] for _ in range(SHOP)]
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

    # ★★ 2026-11-22「前向压实」：倒排的 latest-fit 会把活儿全推到 deadline 前
    #   一刻 → 前段大量空炉（451 首解 t<90s 只开 6 张，生产 DP 开 22 张），
    #   执行层随即重解、计划漂移、反而慢 3.8%。空炉是**我的实现**造成的，
    #   不是「等子卡齐套」的必然 —— 人炼卡从不空炉，只在子卡快到期时才让位。
    #   `MC_BWD_FWD=1` → 拿倒排的 deadline 链当前向上界，重排成 ASAP。
    if _os.environ.get("MC_BWD_FWD") == "1":
        _wait = float(_os.environ.get("MC_BWD_WAIT") or 0.0)
        fw = _forward_fill(inst, ops, is_base, T, wait=_wait)
        if fw is not None:
            rows2 = sorted([(op["s"], op["i"], op.get("m", 0)) for op in ops
                            if not is_base[op["i"]]], key=lambda x: x[0])
            return {"sched": [(i, s) for s, i, _m in rows2],
                    "pools": [("main" if m < M else "steal", i, s)
                              for s, i, m in rows2],
                    "buys": fw["buys"], "mk": fw["mk"],
                    "_ops": ops, "_is_base": is_base,
                    "n_roots": len(_roots), "n_tops": len(_top_types), "stag": _stag,
                    "_fwd": {"late": fw["late"], "idle": fw["idle"]}}

    rows = sorted([(op["s"], op["i"], op.get("m", 0)) for op in ops
                   if not is_base[op["i"]]], key=lambda x: x[0])
    sched = [(i, s) for s, i, _m in rows]
    pools = [("main" if m < M else "steal", i, s) for s, i, m in rows]
    return {"sched": sched, "pools": pools, "buys": buys_out, "mk": T,
            "_ops": ops, "_is_base": is_base, "n_roots": len(_roots),
            "n_tops": len(_top_types), "stag": _stag}


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
    # dp_plan3 用的是同目录裸 import（`from dp_plan2 import ...`），生产进程的
    # sys.path 里通常没有 sched_cmp 目录，这里补一下（重复不会加载第二份：
    # 已有 sys.modules 命中就用现有的）。
    import os as _os
    import sys as _sys
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from dp_plan3 import validate_pools

    dur = [float(x) for x in inst["dur"]]
    n0 = int(inst["n"])
    cp = inst.get("cp") or [0.0] * n0
    lo = float(T_lo if T_lo is not None else max(cp))
    hi = float(T_hi or 0.0) or None
    step = float(step or max(60.0, lo * 0.02))

    # 前向压实模式下，ASAP 会挤爆换卡箱（底卡同时到货/消耗）→ 从 wait=0 起
    # 逐步放宽「等到 deadline 前多少秒再开」，取第一个过校验的。
    FWD = _os.environ.get("MC_BWD_FWD") == "1"
    WAITS = [0.0, 45.0, 90.0, 180.0, 360.0, 720.0, 1440.0, 2880.0] if FWD else [0.0]

    def _scan(F):
        """扫一个错峰系数 F（写 inst["_bwd_stag_f"]），返回 (best, fails, tries)。"""
        inst["_bwd_stag_f"] = float(F)
        best = None
        fails = []
        T = lo
        step_ = step
        tries = 0
        while tries < max_try:
            tries += 1
            r = None
            for w in WAITS:
                _os.environ["MC_BWD_WAIT"] = str(w)
                rr = backward_at(inst, T)
                if rr is None:
                    continue
                vv = validate_pools(inst, rr["sched"], rr["pools"], rr["buys"])
                if vv["ok"]:
                    r = rr
                    best = (vv["makespan"], rr, rr["pools"], vv)
                    break
                fails.append((T, "w%.0f:%s" % (w, vv["viol"][:1])))
            if best is not None:
                break
            if r is None and not any(t == T for t, _ in fails):
                fails.append((T, "construct_fail"))
            if hi and T >= hi:
                break
            T = T + step_
            step_ *= 1.15
        return best, fails, tries

    # ★ 2026-11-22 错峰（多 deadline）总开关：`MC_BWD_STAGGER=f`。
    #   默认 **0.5**（f=0 复现旧「全根共用 T」口径）。构造失败就退回 f=0。
    #   实测（同 sim / 同 seed，MC_BWD_FIRST=1）：
    #     · 3 主题（60/59/62）：f=0 106.41h → 0.25 104.75 → 0.5 **103.65** → 1.0 102.85；
    #       顶层投炉 25.9h→14.2h，投炉时箱存 59→17 张，40 面 avg 34.2→26.7；
    #     · 954 三套：f=0 18.79h → 0.25 **18.18** → 0.5 19.38 → 1.0 19.41（无 hint 20.01）；
    #     · 单套：逐位不变（48.75h）；1652 流水：逐位不变（ΣCi 1280.1）。
    #   → 0.25~1.0 在每个场景都不劣于 f=0，取中值 0.5 作默认。
    _F = float(_os.environ.get("MC_BWD_STAGGER") or 0.5)
    best, fails, tries = _scan(_F)
    stag_used = _F
    if best is None and _F > 0.0:
        b2, f2, t2 = _scan(0.0)
        fails = fails + f2
        tries += t2
        if b2 is not None:
            best, stag_used = b2, 0.0
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
        ok = False
        for w in WAITS:
            _os.environ["MC_BWD_WAIT"] = str(w)
            rr = backward_at(inst, mid)
            if rr is None:
                continue
            vv = validate_pools(inst, rr["sched"], rr["pools"], rr["buys"])
            if vv["ok"]:
                ok = True
                mk_ok, r_ok, pools_ok, v_ok = vv["makespan"], rr, rr["pools"], vv
                break
        if ok:
            hi_f = mid
        else:
            lo_f = mid
    ops = r_ok["_ops"]
    st = _spread_stats(ops, dur, r_ok["_is_base"])
    st.update({"ok": True, "makespan": mk_ok, "tries": tries,
               "peak_chg": v_ok["peak_chg"], "peak_ref": v_ok["peak_ref"],
               "T_target": hi_f, "buys": len(r_ok["buys"]), "stagger": stag_used,
               "roots": r_ok.get("n_roots"), "tops": r_ok.get("n_tops"),
               "stag_s": r_ok.get("stag"),
               "ops": len(r_ok["sched"])})
    if verbose:
        print("[BWD] mk=%.2fmin  spread_med=%.2f  wait_area=%.1f格·min  peak_chg=%d"
              % (mk_ok / 60, st["spread_med"] / 60, st["wait_area"] / 60, st["peak_chg"]))
    return mk_ok, r_ok["sched"], {"buys": r_ok["buys"], "pools": pools_ok}, st
