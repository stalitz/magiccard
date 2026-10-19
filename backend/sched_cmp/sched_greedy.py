"""贪心基线（类型级）：关键路径 list scheduling，事件驱动无死循环。

优先级 = 关键路径 cp 降序，同分按时长降序、深度降序、类型序。
对树形/近树形优先级图 Horn 定理保证该 list schedule 即最优；本实例含汇聚
（孙膑喂 3 家）时会次优——这正是要与精确法对比的点。
独立实现，不引用 auto_refine，也不引用其它调度器。
"""
EPS = 1e-9


def schedule(inst):
    dur = inst["dur"]
    mats = inst["mats"]
    M = inst["machines"]
    cp = inst["cp"]
    depth = inst["depth"]
    n = inst["n"]
    stock = [0] * n
    need = list(inst["need0"])
    machine_free = [0.0] * M
    running = []               # (finish, type)
    out = []                   # (start, type)

    def prio(i):
        return (-cp[i], -dur[i], -depth[i], i)

    while any(need):
        t = min(machine_free)
        # 收卡
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
                       key=prio)
        if free and ready:
            for m in free:
                # 同批次内逐张重查素材（前面已扣的要在内）
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
                need[i] -= 1          # 开工即承诺
                out.append((i, t))
                running.append((t + dur[i], i))
                machine_free[m] = t + dur[i]
            continue
        nxt = [mf for mf in machine_free if mf > t + EPS]
        if not nxt:
            break
        nv = min(nxt)
        for m in range(M):
            if machine_free[m] <= t + EPS:
                machine_free[m] = nv
    return out
