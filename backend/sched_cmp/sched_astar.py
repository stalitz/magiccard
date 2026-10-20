"""A* 精确求解器（类型级，自包含，零第三方依赖，不引用其它调度器）。

状态 = (need, stock, running_rel=(类型,剩余时间)已排序)，与 DP 同问题模型；
running 以「相对剩余时间」(finish - t) 表示 → 完成耗时只取决于 (need,stock,running_rel)，
与绝对时刻 t 无关；去重(transposition)据此合并所有 t 平移的重复状态。
f(state) = t + h_rel = 该状态完工时刻的可采纳下界（相对 h_rel 取三条 + 累资源）：
  1) max(在炉剩余时间, 0)
  2) 剩余总炉时 / M
  3) 链式：dur[i] + max(素材最早可增量相对时刻)
  4) 累资源下界（≥ 2，永不劣化）
最佳优先弹出 f 最小状态；第一个弹出的「目标状态」（need 全 0）即最优：
  最优调度路径上每个状态的 f ≤ OPT，且目标状态 f = 精确完工时刻，
  故第一个出队目标的 f = OPT。
后继 = 开工向量分支（总数 1..cap，联合素材可行）+ 时间推进；
剪枝 f ≥ 已找到目标值；每状态仅展开一次（expanded 集）。
返回 (makespan, schedule, stats)。
"""
import heapq
import time as _t

EPS = 1e-9
INF = float("inf")


def _ub_schedule(inst):
    """内置关键路径列表调度求初始上界（独立实现）。"""
    dur = inst["dur"]
    mats = inst["mats"]
    M = inst["machines"]
    cp = inst["cp"]
    depth = inst["depth"]
    n = inst["n"]
    stock = [0] * n
    need = list(inst["need0"])
    machine_free = [0.0] * M
    running = []
    out = []
    guard = 0
    limit = 8 * sum(need) + 64
    while any(need):
        guard += 1
        if guard > limit:
            break
        t = min(machine_free)
        still = []
        for f, i in running:
            if f <= t + EPS:
                stock[i] += 1
            else:
                still.append((f, i))
        running = still
        free = [m for m in range(M) if machine_free[m] <= t + EPS]
        ready = sorted([i for i in range(n)
                        if need[i] > 0 and all(stock[j] >= k for j, k in mats[i])],
                       key=lambda i: (-cp[i], -dur[i], -depth[i], i))
        started = 0
        if free and ready:
            for m in free:
                pick = None
                for pos, i in enumerate(ready):
                    if all(stock[j] >= k for j, k in mats[i]):
                        pick = ready.pop(pos)
                        break
                if pick is None:
                    break
                i = pick
                for j, k in mats[i]:
                    stock[j] -= k
                need[i] -= 1
                out.append((i, t))
                running.append((t + dur[i], i))
                machine_free[m] = t + dur[i]
                started += 1
        if started:
            continue
        nxt = [mf for mf in machine_free if mf > t + EPS]
        if not nxt:
            break
        nv = min(nxt)
        for m in range(M):
            if machine_free[m] <= t + EPS:
                machine_free[m] = nv
    return out


def _vectors(startable, mats, stock, need, cap, maximal_only=True):
    """所有可行开工向量：1 ≤ 总数 ≤ cap，每类型 ≤ min(need, 素材允许)，联合素材可行。
    maximal_only=True 时只保留「极大」向量（再也加不进任何一张）。"""
    items = []
    for i in startable:
        lim = need[i]
        for j, k in mats[i]:
            lim = min(lim, stock[j] // k)
        if lim > 0:
            items.append((i, lim))
    if not items or cap <= 0:
        return []
    res = []
    ni = len(items)

    def maximal(counts, tot):
        if tot >= cap:
            return True
        st = list(stock)
        for i, c in counts:
            for j, k in mats[i]:
                st[j] -= c * k
        cnt = dict(counts)
        for i in startable:
            if cnt.get(i, 0) + 1 > need[i]:
                continue
            if all(st[j] >= k for j, k in mats[i]):
                return False
        return True

    def rec(p, remaining, cur, st):
        if p == ni:
            tot = sum(c for _, c in cur)
            if tot >= 1 and (not maximal_only or maximal(cur, tot)):
                res.append(tuple(cur))
            return
        i, lim = items[p]
        for c in range(min(lim, remaining), -1, -1):
            ok = True
            for j, k in mats[i]:
                if st[j] < c * k:
                    ok = False
                    break
            if not ok:
                continue
            for j, k in mats[i]:
                st[j] -= c * k
            cur.append((i, c))
            rec(p + 1, remaining - c, cur, st)
            cur.pop()
            for j, k in mats[i]:
                st[j] += c * k

    rec(0, cap, [], list(stock))
    return res


def _chain_avail_rel(j, k, stock, running_rel, seen):
    """k 张 j 卡可用的最早增量时刻（相对剩余时间，可采纳下界）。"""
    if stock[j] >= k:
        return 0.0
    fins = [f for (j2, f) in running_rel if j2 == j]
    if fins:
        if stock[j] + len(fins) >= k:
            return sorted(fins)[k - stock[j] - 1]
        return min(fins)
    if j in seen:
        return 0.0
    seen.add(j)
    return DUR[j] + max((_chain_avail_rel(m, kk, stock, running_rel, seen)
                         for m, kk in MATS[j]), default=0.0)


def _cum_lb(need, stock, running_rel, t):
    """累资源下界（相对额外耗时，可采纳，≥ 剩余总炉时/M）。"""
    starts = []
    running_types = set(i for i, _ in running_rel)
    for i in range(N):
        if need[i] <= 0:
            continue
        if i in running_types:
            s = 0.0
        else:
            s = max((_chain_avail_rel(j, kk, stock, running_rel, set())
                     for j, kk in MATS[i]), default=0.0)
        starts.append((s, DUR[i], need[i]))
    rem_running = sum(f for _, f in running_rel)
    Wtot = sum(d * nd for _, d, nd in starts) + rem_running
    lb = Wtot / MACHINES
    for s, d, nd in starts:
        if s + d > lb:
            lb = s + d
    pts = sorted(set([0.0] + [s for s, _, _ in starts]))
    for tt in pts:
        A = sum(d * nd for s, d, nd in starts if s <= tt) + (
            rem_running if tt >= 0 else 0)
        B = sum(d * nd for s, d, nd in starts if s > tt)
        if A / MACHINES > lb:
            lb = A / MACHINES
        if tt + B / MACHINES > lb:
            lb = tt + B / MACHINES
    return lb


def _lb_rel(need, stock, running_rel):
    """可采纳下界（相对额外耗时）。"""
    lb = max([0.0] + [f for _, f in running_rel])
    rem = sum(need[i] * DUR[i] for i in range(N)) + sum(f for _, f in running_rel)
    lb2 = rem / MACHINES
    if lb2 > lb:
        lb = lb2
    running_types = set(i for i, _ in running_rel)
    for i in range(N):
        if need[i] <= 0 or i in running_types:
            continue
        ch = DUR[i] + max((_chain_avail_rel(j, kk, stock, running_rel, set())
                           for j, kk in MATS[i]), default=0.0)
        if ch > lb:
            lb = ch
    cum = _cum_lb(need, stock, running_rel, 0.0)
    if cum > lb:
        lb = cum
    return lb


def solve(inst, time_budget=120.0, max_queue=4_000_000, maximal_only=True):
    global DUR, MATS, MACHINES, N
    DUR = inst["dur"]
    MATS = inst["mats"]
    MACHINES = inst["machines"]
    N = inst["n"]
    dur = DUR
    mats = MATS
    M = MACHINES
    n = N
    need0 = tuple(inst["need0"])
    t0 = _t.time()

    ub_sched = _ub_schedule(inst)
    best_val = max((s + dur[i] for i, s in ub_sched), default=0.0)
    best_sched = list(ub_sched)
    stats = {"expanded": 0, "generated": 0, "time": 0.0, "bounded": False,
             "ub0": best_val, "opt": None}

    def successors(need, stock, running_rel, t):
        """展开一个状态：先收卡，再生效后继（推进 / 开工向量）。
        返回 [(n_need, n_stock, n_running_rel, n_t, move), ...]，move=("adv",) 或 ("start", t, vec)。"""
        out = []
        if any(f <= EPS for _, f in running_rel):
            ns = list(stock)
            nr = []
            for i2, f in running_rel:
                if f <= EPS:
                    ns[i2] += 1
                else:
                    nr.append((i2, f))
            out.append((tuple(need), tuple(ns), tuple(nr), t, ("adv",)))
            return out
        startable = [i for i in range(n)
                     if need[i] > 0 and all(stock[j] >= kk for j, kk in mats[i])]
        idle = M - len(running_rel)
        if idle <= 0 or not startable:
            if not running_rel:
                return []
            dt = min(f for _, f in running_rel)
            nrr = tuple((i, f - dt) for i, f in running_rel)
            out.append((tuple(need), tuple(stock), nrr, t + dt, ("adv",)))
            return out
        cap = min(idle, sum(need[i] for i in startable))
        for vec in _vectors(startable, mats, stock, need, cap, maximal_only):
            ns = list(stock)
            nn = list(need)
            nr = list(running_rel)
            for i, c in vec:
                for _ in range(c):
                    for j, kk in mats[i]:
                        ns[j] -= kk
                    nn[i] -= 1
                    nr.append((i, dur[i]))
            nr.sort()
            dt = min(f for _, f in nr)
            nrr = tuple((i, f - dt) for i, f in nr)
            out.append((tuple(nn), tuple(ns), nrr, t + dt, ("start", t, vec)))
        return out

    def key_of(need, stock, running_rel):
        return (need, stock, running_rel)

    start_key = key_of(need0, tuple([0] * n), ())
    counter = 0
    # 堆元素 (f_abs, -depth, seq, key, parent_key, move, t)
    f0 = _lb_rel(need0, tuple([0] * n), ())
    heap = [(f0, 0, counter, start_key, None, None, 0.0)]
    expanded = set()
    seen = set()
    parent = {}
    depth_of = {start_key: 0}
    goal_key = None

    while heap:
        f_abs, negd, _, key, pk, mv, t = heapq.heappop(heap)
        if key in expanded:
            continue
        expanded.add(key)
        if pk is not None:
            parent[key] = (pk, mv)
        need, stock, running_rel = key
        d = -negd
        if not any(need):
            fin = max([0.0] + [f for _, f in running_rel])
            if t + fin < best_val - 1e-9:
                best_val = t + fin
                goal_key = key
            stats["opt"] = best_val
            break
        if f_abs >= best_val - 1e-9:
            stats["opt"] = best_val
            break
        stats["expanded"] += 1
        if (_t.time() - t0) > time_budget:
            stats["bounded"] = True
            break
        for succ in successors(need, stock, running_rel, t):
            n2, s2, r2, t2, smv = succ
            skey = key_of(n2, s2, r2)
            if skey in expanded or skey in seen:
                continue
            fv = t2 + _lb_rel(n2, s2, r2)
            if fv >= best_val - 1e-9:
                continue
            seen.add(skey)
            depth_of[skey] = d + 1
            counter += 1
            stats["generated"] += 1
            heapq.heappush(heap, (fv, -(d + 1), counter, skey, key, smv, t2))
        if len(heap) > max_queue:
            stats["bounded"] = True
            stats["note"] = f"heap>{max_queue}"
            break

    stats["time"] = _t.time() - t0
    sched = None
    if goal_key is not None:
        chain = []
        k = goal_key
        while k in parent:
            pk, mv = parent[k]
            if mv[0] == "start":
                chain.append((mv[1], mv[2]))
            k = pk
        chain.reverse()
        out = []
        for t, vec in chain:
            for i, c in vec:
                for _ in range(c):
                    out.append((i, t))
        sched = out
    if sched is None:
        return best_val, best_sched, stats
    return best_val, sched, stats
