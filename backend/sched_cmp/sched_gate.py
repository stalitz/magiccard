"""门槛优先 list scheduling（2026-11-21，用户定调的「人肉炼法」）。

用户原话（2026-11-21）：
    「先看第一个 540 需要哪个卡，定位下面的 150 的卡，然后看 150 需要哪些 40 卡，
      直接炼 6 个不同的；等些都收了，直接先炼 150；只要有 1 个 150 需要的 40 够了，
      就直接开炼 150；150 都齐了就直接开 540，然后开下一个 540 需要的 150 的 40。」

抽象成一条优先级规则——**门槛（gate）优先**：
  1. 交付门槛 = 顶层卡自己的 BOM 展开（不含集卡册「每型各 1 张」那份）。
     顶层卡一落箱就能上交，所以要让它尽早开工；集卡册多出来的同型卡
     （`need0 - gate`）不在任何交付关键路上，**全部排到门槛链之后**。
  2. 门槛内按层级深度降序（540 > 150 > 40），同层按 cp 降序、类型序。
  3. 事件驱动：谁空出炉位就按序开工，素材不足就跳过，不预留、不囤积。

为什么这条规则能赢过 DP 的 cp 优先：cp 把「集卡册第 2 张干脆面」和
「门槛链上那张干脆面」算成同一优先级 → 顶层要等全套中层都齐才开工。
954 实测（5 主炉 + 1 偷炉）：门槛优先 16.09h / DP 17.47h / CP-SAT 可行 15.07h。

机器口径：`inst["mclass"]` 存在时按机器类建模（0=主炉、1=商店串行、2=偷炉），
容量取 `inst["machines"] / steal_cap / shop（默认 1）`；不存在时退化为
`machines` 台同构机器（与 sched_greedy 同口径）。
"""
EPS = 1e-9


def gate_need(inst):
    """交付门槛需求：顶层卡 BOM 展开，逐层受 need0 截断。

    顶层 = depth 最大的类型（通常 1~2 个）。need0 里非顶层每型都有
    「集卡册 1 张」的额外份数，门槛只保留被上层真正消耗掉的那部分。
    """
    n = inst["n"]
    need0 = inst["need0"]
    mats = inst["mats"]
    depth = inst["depth"]
    gate = [0] * n
    order = sorted(range(n), key=lambda i: -depth[i])
    parents = [[] for _ in range(n)]
    for p in range(n):
        for j, k in mats[p]:
            parents[j].append((p, k))
    for i in order:
        ps = parents[i]
        if not ps:
            gate[i] = need0[i]                 # 顶层（或孤立卡）= 直接交付
            continue
        want = sum(gate[p] * k for p, k in ps)
        gate[i] = min(want, need0[i])
    return gate


def schedule(inst):
    """返回 [(type_idx, start_time)]，可直接进 executor.evaluate。"""
    n = inst["n"]
    dur = inst["dur"]
    mats = inst["mats"]
    need0 = inst["need0"]
    depth = inst["depth"]
    cp = inst["cp"]
    gate = gate_need(inst)

    mclass = inst.get("mclass")
    if mclass is None:
        pools = {0: [0.0] * int(inst["machines"])}
        allow = [(0,)] * n
    else:
        allow = [tuple(c) for c in mclass]
        pools = {0: [0.0] * int(inst["machines"])}
        if any(1 in c for c in allow):
            pools[1] = [0.0] * int(inst.get("shop_cap_n", 1))
        if any(2 in c for c in allow):
            pools[2] = [0.0] * int(inst.get("steal_cap", 1))

    need = list(need0)
    started = [0] * n
    stock = [0] * n
    running = []                      # [(finish, idx)]
    out = []
    t = 0.0

    def prio(i):
        gated = gate[i] - started[i] > 0
        return (0 if gated else 1, -depth[i], -cp[i], need[i], i)

    def ready(i):
        return all(stock[j] >= k for j, k in mats[i])

    while any(need) or running:
        # 收卡
        still = []
        for f, i in running:
            if f <= t + EPS:
                stock[i] += 1
            else:
                still.append((f, i))
        running = still

        progressed = False
        for cls, slots in pools.items():
            for si, sf in enumerate(slots):
                if sf > t + EPS:
                    continue
                cand = [i for i in range(n) if need[i] > 0 and cls in allow[i] and ready(i)]
                if not cand:
                    continue
                i = min(cand, key=prio)
                for j, k in mats[i]:
                    stock[j] -= k
                need[i] -= 1
                started[i] += 1
                out.append((i, t))
                slots[si] = t + dur[i]
                running.append((t + dur[i], i))
                progressed = True

        if not any(need) and not running:
            break
        if progressed:
            continue
        nxt = [f for f, _ in running]
        for slots in pools.values():
            nxt += [f for f in slots if f > t + EPS]
        if not nxt:
            break                     # 素材/炉位死锁：交回上层处理
        t = min(nxt)
    return out
