"""类型级仿真/校验执行器。

调度器统一输出 schedule = [(type_idx, start_time), ...]（按开始时间排序即可）。
本模块负责：
  - 事件驱动仿真：收卡（入库存）→ 扣素材 → 开炼，校验素材充足；
  - 校验机器容量（任意时刻在炉数 ≤ 机器数）；
  - 校验产量达标（各类型待产数归零）；
  - 计算 makespan、利用率，回填机器号出甘特。
与具体调度算法解耦——同一份 schedule 任何调度器都能进这里比对。
"""
EPS = 1e-9


def evaluate(inst, schedule):
    """校验 + 计算指标。返回 (ok, metrics)。"""
    M = inst["machines"]
    dur = inst["dur"]
    mats = inst["mats"]
    n = inst["n"]
    need = list(inst["need0"])
    stock = [0] * n
    running = []          # (finish, type_idx)
    machine_free = [0.0] * M
    assign = {}           # type_idx -> [(start, machine)]
    errors = []

    sched = sorted(schedule, key=lambda x: (x[1], x[0]))
    for i, s in sched:
        # 收卡
        still = []
        for f, t2 in running:
            if f <= s + EPS:
                stock[t2] += 1
            else:
                still.append((f, t2))
        running = still
        # 素材校验
        if need[i] <= 0:
            errors.append(f"类型{i} 超产")
            continue
        for j, k in mats[i]:
            if stock[j] < k:
                errors.append(f"类型{i} 素材{j} 不足(需{k},有{stock[j]})")
        for j, k in mats[i]:
            stock[j] -= k
        need[i] -= 1          # 开工即承诺产出 1 张
        # 机器分配
        cand = [m for m in range(M) if machine_free[m] <= s + EPS]
        if not cand:
            errors.append(f"类型{i} 时刻{s} 无空闲机器")
            m = min(range(M), key=lambda x: machine_free[x])
        else:
            m = min(cand, key=lambda x: machine_free[x])
        machine_free[m] = s + dur[i]
        running.append((s + dur[i], i))
        assign.setdefault(i, []).append((s, m))

    # 收尾：把所有在炉跑完
    t_end = max(machine_free) if machine_free else 0.0
    for f, t2 in running:
        if f > t_end + EPS:
            t_end = f
        stock[t2] += 1

    if any(need):
        errors.append(f"产量未达标: {[(inst['types'][i], need[i]) for i in range(n) if need[i]]}")

    ok = not errors
    metrics = {
        "ok": ok,
        "errors": errors[:8],
        "makespan": t_end,
        "makespan_h": t_end / 3600.0,
        "utilization": (inst["total_dur"] / (M * t_end)) if t_end else 0.0,
        "n_jobs": len(schedule),
        "machines": M,
    }
    return ok, metrics


def gantt(inst, schedule, max_lines=20):
    """紧凑甘特：每台机器一行。"""
    ok, m = evaluate(inst, schedule)
    if not ok:
        return "INFEASIBLE: " + "; ".join(m["errors"][:4])
    dur = inst["dur"]
    names = {i: recipes_name(inst, i) for i in range(inst["n"])}
    # 重新仿真拿机器分配
    M = inst["machines"]
    stock = [0] * inst["n"]
    need = list(inst["need0"])
    running = []
    machine_free = [0.0] * M
    by_m = {}
    for i, s in sorted(schedule, key=lambda x: (x[1], x[0])):
        still = []
        for f, t2 in running:
            if f <= s + EPS:
                stock[t2] += 1
            else:
                still.append((f, t2))
        running = still
        for j, k in inst["mats"][i]:
            stock[j] -= k
        need[i] -= 1
        cand = [mm for mm in range(M) if machine_free[mm] <= s + EPS]
        mm = min(cand, key=lambda x: machine_free[x]) if cand else min(
            range(M), key=lambda x: machine_free[x])
        machine_free[mm] = s + dur[i]
        running.append((s + dur[i], i))
        by_m.setdefault(mm, []).append((s, dur[i], names[i]))
    lines = []
    for mm in sorted(by_m):
        line = f"M{mm}: "
        cur = 0.0
        for s, d, name in sorted(by_m[mm]):
            gap = int((s - cur) / 3600)
            if gap > 0:
                line += f" .{gap}h "
            line += f"[{name[:3]}{int(d/3600)}h]"
            cur = s + d
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def recipes_name(inst, i):
    try:
        import recipes
        return recipes.CARDS[inst["types"][i]][0]
    except Exception:
        return f"T{inst['types'][i]}"
