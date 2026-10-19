"""DP 精确求解器（类型级，自包含，零第三方依赖）。

状态 = (各类型待产数 need, 各类型库存 stock, 在炉 running=(类型,完成时刻)已排序, 当前时刻 t)
  - 同类型卡完全可互换 → 按「类型计数向量」分支既完整又无排列爆炸
  - 非延时：有空炉且有可开类型必开（可开数 total ∈ [1, min(空炉, 素材允许)]）
  - 开工即承诺（need 减 1、扣库存）；完成时入库存
  - makespan = 最后一张卡的完工时刻（叶子返回 max(t, 在炉最晚完成时刻)）

可采纳下界（均可采纳）：
  1) max(在炉完成时刻, t)
  2) t + 剩余总炉时 / M        （剩余炉时 = 未开工 need*dur + 在炉剩余）
  3) 链式：对每个未完工类型 i，t + dur[i] + max(素材最早可增量时刻)
UB = 内置关键路径列表调度；LB ≥ UB 剪枝；记忆化 state → 最优完工值。
路径随递归下行携带（不回放 memo），叶子改进 UB 时记录完整调度。
返回 (makespan, schedule, stats)。
"""
import time as _t

EPS = 1e-9
INF = float("inf")


def _ub_schedule(inst):
    """关键路径列表调度求上界（鲁棒：无进展即推进时间）。"""
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
    """所有可行开工向量：1 ≤ 总数 ≤ cap，每类型数 ≤ min(need, 素材允许)，
    且联合素材可行（不会把库存扣成负数）。
    maximal_only=True 时只保留「极大」向量（再也加不进任何一张）——
    非延时调度中还能加开却不开不会更优（空炉+库存素材是纯浪费），
    该限制把分支数降一个量级且不丢最优解。"""
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


def solve(inst, time_budget=120.0, node_budget=8_000_000, use_memo=True,
          maximal_only=True):
    dur = inst["dur"]
    mats = inst["mats"]
    M = inst["machines"]
    n = inst["n"]
    need0 = tuple(inst["need0"])

    ub_sched = _ub_schedule(inst)
    ub = max((s + dur[i] for i, s in ub_sched), default=0.0)
    best = {"val": ub, "sched": list(ub_sched), "from": "UB"}
    stats = {"nodes": 0, "time": 0.0, "bounded": False, "ub0": ub,
             "memo_hits": 0, "opt": None}
    t0 = _t.time()
    memo = {}

    def key_of(need, stock, running, t):
        return (need, stock, running, t)

    def chain_avail(j, k, stock, running, t, seen):
        """k 张 j 卡可用的最早增量时刻（下界，可采纳）。"""
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

    def path_to_sched(path):
        out = []
        for t, vec in path:
            for i, c in vec:
                for _ in range(c):
                    out.append((i, t))
        return out

    def dfs(need, stock, running, t, path):
        adv = []
        while True:
            # ---- 叶子：全部开工完 → makespan = 最后完工时刻 ----
            if not any(need):
                fin = max([t] + [f for _, f in running])
                if fin < best["val"] - 1e-9:
                    best["val"] = fin
                    best["sched"] = path_to_sched(path)
                    best["from"] = "DP"
                v = fin
                for k2 in adv:
                    memo[k2] = v
                return v

            key = key_of(need, stock, running, t)
            hit = memo.get(key) if use_memo else None
            if hit is not None:
                stats["memo_hits"] += 1
                for k2 in adv:
                    memo[k2] = hit
                return hit
            if stats["nodes"] > node_budget or (_t.time() - t0) > time_budget:
                stats["bounded"] = True
                return INF
            stats["nodes"] += 1

            # ---- 收卡：完成时刻 <= t 的入库存 ----
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
                continue

            startable = [i for i in range(n)
                         if need[i] > 0 and all(stock[j] >= kk for j, kk in mats[i])]
            idle = M - len(running)
            if idle <= 0 or not startable:
                if not running:
                    return INF          # 死路：无机在炉也无矿可开
                nt = min(f for _, f in running)
                adv.append(key)
                t = nt
                continue

            # ---- 可采纳下界 ----
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
            if lb >= best["val"] - 1e-9:
                return INF

            # ---- 分支：可行开工向量（总数 1..cap）----
            cap = min(idle, sum(need[i] for i in startable))
            vecs = _vectors(startable, mats, stock, need, cap, maximal_only)
            best_v = INF
            for vec in vecs:
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
                v = dfs(tuple(nn), tuple(ns), tuple(nr), nt, path + ((t, vec),))
                if v < best_v - 1e-9:
                    best_v = v
                if best_v <= lb + 1e-9:
                    break               # 已达下界，无需再试其它向量
            memo[key] = best_v
            for k2 in adv:
                memo[k2] = best_v
            return best_v

    v = dfs(need0, tuple([0] * n), (), 0.0, ())
    stats["time"] = _t.time() - t0
    stats["opt"] = v
    if v < INF and abs(v - best["val"]) > 1e-6:
        stats["note"] = f"root={v/3600:.2f}h best={best['val']/3600:.2f}h 不一致"
    return best["val"], best["sched"], stats
