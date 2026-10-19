"""ILP（时间索引整数规划）+ 自写分支定界（零第三方依赖，不引用其它调度器）。

模型（类型级聚合，1h 时间网格，与 DP/A* 同一问题）：
  整数变量 x[i][t] ≥ 0 ：类型 i 在时刻 t 开工的张数（t 以小时计）
  约束：
    (1) 产量    Σ_t x[i][t] = need[i]                          ∀i
    (2) 素材流  ∀j,τ: Σ_{t+dur[j]≤τ} x[j][t] ≥ Σ_i mats[i][j]·Σ_{t≤τ} x[i][t]
    (3) 产能    ∀τ: Σ_i Σ_{t≤τ<t+dur[i]} x[i][t] ≤ M
    (4) 完工    Cmax ≥ Σ_t (t+dur[i])·x[i][t]                  ∀i
  目标：min Cmax
求解器：自写 B&B，按时间槽 chronological 分支——每个决策点=一个可能开工的
  时间槽，决策=该槽各类型开工数向量 x[·][t]（非延时极大向量，理由同 DP：
  空炉+库存素材是纯浪费）。空槽（无决策可做）直接跳到下一事件槽。
  定界用可采纳下界（三分量取大，自写实现）；UB 取列表调度。
  ★ 与 DP 的关键区别：不做跨节点记忆化（记忆化是 DP 的技术，此处保持独立），
    因此同一状态经不同路径到达会重复展开——这正是要实测的代价。
返回 (makespan, schedule, stats)。
"""
import time as _t

EPS = 1e-9
INF = float("inf")


def _ub_schedule(inst):
    """关键路径列表调度求初始上界（独立实现）。"""
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


def _vectors(startable, mats, stock, need, cap):
    """该时间槽的可行开工向量（非延时极大，联合素材可行）。"""
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
            if tot >= 1 and maximal(cur, tot):
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


def solve(inst, time_budget=120.0, node_budget=20_000_000):
    dur = inst["dur"]
    mats = inst["mats"]
    M = inst["machines"]
    n = inst["n"]
    need0 = tuple(inst["need0"])
    t0 = _t.time()

    ub_sched = _ub_schedule(inst)
    ub = max((s + dur[i] for i, s in ub_sched), default=0.0)
    best = {"val": ub, "sched": list(ub_sched), "from": "UB"}
    stats = {"nodes": 0, "time": 0.0, "bounded": False, "ub0": ub, "opt": None}

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

    def lb_of(need, stock, running, t):
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

    def path_to_sched(path):
        out = []
        for t, vec in path:
            for i, c in vec:
                for _ in range(c):
                    out.append((i, t))
        return out

    def dfs(need, stock, running, t, path):
        """从 (need,stock,running,t) 出发的最优完工时刻；路径随递归携带。"""
        if stats["nodes"] > node_budget or (_t.time() - t0) > time_budget:
            stats["bounded"] = True
            return INF
        stats["nodes"] += 1
        # 收卡
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
        if not any(need):
            fin = max([t] + [f for _, f in running])
            if fin < best["val"] - 1e-9:
                best["val"] = fin
                best["sched"] = path_to_sched(path)
                best["from"] = "ILP"
            return fin
        startable = [i for i in range(n)
                     if need[i] > 0 and all(stock[j] >= kk for j, kk in mats[i])]
        idle = M - len(running)
        if idle > 0 and startable:
            lb = lb_of(need, stock, running, t)
            if lb >= best["val"] - 1e-9:
                return INF
            cap = min(idle, sum(need[i] for i in startable))
            best_v = INF
            for vec in _vectors(startable, mats, stock, need, cap):
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
                v = dfs(tuple(nn), tuple(ns), tuple(nr), t, path + ((t, vec),))
                if v < best_v - 1e-9:
                    best_v = v
                if best_v <= lb + 1e-9:
                    break
            return best_v
        # 本槽无决策可做 → 死路或推进到下一事件槽（空槽跳过）
        if not running:
            return INF
        nt = min(f for _, f in running)
        return dfs(need, stock, running, nt, path)

    v = dfs(need0, tuple([0] * n), (), 0.0, ())
    stats["time"] = _t.time() - t0
    stats["opt"] = v
    return best["val"], best["sched"], stats
