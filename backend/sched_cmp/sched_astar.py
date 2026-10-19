"""A* 精确求解器（类型级，自包含，零第三方依赖，不引用其它调度器）。

状态 = (need, stock, running=(类型,完成时刻), t)，与 DP 同问题模型；
f(state) = t + h_rem = 该状态完工时刻的可采纳下界（三者取大）：
  1) max(在炉完成时刻, t)
  2) t + 剩余总炉时 / M
  3) t + max_i( dur[i] + 素材最早可增量时刻 )     （链式，孙膑喂多家也正确）
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
    maximal_only=True 时只保留「极大」向量（再也加不进任何一张）——
    非延时调度中还能加开却不开不会更优（空炉+库存素材是纯浪费）。"""
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


def solve(inst, time_budget=120.0, max_queue=4_000_000, maximal_only=True):
    dur = inst["dur"]
    mats = inst["mats"]
    M = inst["machines"]
    n = inst["n"]
    need0 = tuple(inst["need0"])
    t0 = _t.time()

    ub_sched = _ub_schedule(inst)
    best_val = max((s + dur[i] for i, s in ub_sched), default=0.0)
    best_sched = list(ub_sched)
    stats = {"expanded": 0, "generated": 0, "time": 0.0, "bounded": False,
             "ub0": best_val, "opt": None}

    def chain_avail(j, k, stock, running, t, seen):
        if stock[j] >= k:
            return 0.0
        fins = [f for (j2, f) in running if j2 == j]
        if fins:
            if stock[j] + len(fins) >= k:
                return sorted(fins)[k - stock[j] - 1] - t
            return min(fins) - t
        if j in seen:
            return 0.0
        seen.add(j)
        return dur[j] + max((chain_avail(m, kk, stock, running, t, seen)
                             for m, kk in mats[j]), default=0.0)

    def h_of(need, stock, running, t):
        """完工时刻可采纳下界（= f 值）。"""
        lb = max([t] + [f for _, f in running])
        rem = sum(need[i] * dur[i] for i in range(n)) + \
            sum(max(0.0, f - t) for _, f in running)
        lb2 = t + rem / M
        if lb2 > lb:
            lb = lb2
        running_types = set(i for i, _ in running)
        for i in range(n):
            if need[i] <= 0 or i in running_types:
                continue
            ch = dur[i] + max((chain_avail(j, kk, stock, running, t, set())
                               for j, kk in mats[i]), default=0.0)
            if t + ch > lb:
                lb = t + ch
        return lb

    def successors(need, stock, running, t):
        """展开一个状态：先收卡，再生效后继（推进 / 开工向量）。"""
        if any(f <= t + EPS for _, f in running):
            ns = list(stock)
            nr = []
            for i2, f in running:
                if f <= t + EPS:
                    ns[i2] += 1
                else:
                    nr.append((i2, f))
            stock = tuple(ns)
            running = tuple(nr)
        startable = [i for i in range(n)
                     if need[i] > 0 and all(stock[j] >= kk for j, kk in mats[i])]
        idle = M - len(running)
        if idle <= 0 or not startable:
            if not running:
                return []                       # 死路
            nt = min(f for _, f in running)
            return [("adv", need, stock, running, nt)]
        cap = min(idle, sum(need[i] for i in startable))
        out = []
        for vec in _vectors(startable, mats, stock, need, cap, maximal_only):
            ns = list(stock)
            nn = list(need)
            nr = list(running)
            for i, c in vec:
                for _ in range(c):
                    for j, kk in mats[i]:
                        ns[j] -= kk
                    nn[i] -= 1
                    nr.append((i, t + dur[i]))
            nr.sort()
            nt = min(f for _, f in nr)
            out.append(("start", tuple(nn), tuple(ns), tuple(nr), nt, vec))
        return out

    # 初始状态；堆元素 (f, -depth, seq, key, parent_key, move) —— 同 f 偏向深层（下潜）
    start_key = (need0, tuple([0] * n), (), 0.0)
    counter = 0
    heap = [(h_of(need0, tuple([0] * n), (), 0.0), 0, counter, start_key, None, None)]
    expanded = set()
    seen = set()               # transposition 表：已生成过的 key 不重复入堆
    parent = {}
    depth_of = {start_key: 0}
    goal_key = None

    while heap:
        f, negd, _, key, pk, mv = heapq.heappop(heap)
        if key in expanded:
            continue
        expanded.add(key)
        if pk is not None:
            parent[key] = (pk, mv)
        need, stock, running, t = key
        d = -negd
        if not any(need):
            fin = max([t] + [q for _, q in running])
            if fin < best_val - 1e-9:
                best_val = fin
                goal_key = key
            stats["opt"] = fin
            break
        if f >= best_val - 1e-9:
            stats["opt"] = best_val
            break
        stats["expanded"] += 1
        if (_t.time() - t0) > time_budget:
            stats["bounded"] = True
            break
        for succ in successors(need, stock, running, t):
            if succ[0] == "adv":
                _, n2, s2, r2, t2 = succ
                skey = (n2, s2, r2, t2)
                smove = ("adv", None)
            else:
                _, n2, s2, r2, t2, vec = succ
                skey = (n2, s2, r2, t2)
                smove = ("start", (t, vec))
            if skey in expanded or skey in seen:
                continue
            fv = h_of(*skey)
            if fv >= best_val - 1e-9:
                continue
            seen.add(skey)
            depth_of[skey] = d + 1
            counter += 1
            stats["generated"] += 1
            heapq.heappush(heap, (fv, -(d + 1), counter, skey, key, smove))
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
                chain.append(mv[1])
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
