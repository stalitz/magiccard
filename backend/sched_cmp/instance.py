"""类型级 RCPSP 实例（同类型卡完全可互换 → 状态按类型聚合）。

为什么是类型级而不是 job 级：
  游戏真实语义——炼一张 150 面卡消耗的是「同类卡任意几张」，素材卡可互换。
  job 级模型把素材卡编号后分给具体消费者，同类型 job 因消费者不同而不可互换，
  按类型计数分支会丢解（曾因此把 22h 最优漏成 23h）。
  类型级：状态 = (各类型待产数, 各类型库存, 在炉 (类型,完成时刻), 当前时刻)，
  分支 = 开哪些类型各几张——完整且状态空间小。

compute_runs 口径与 auto_refine.net_plan 一致（每张套内卡按 sets 算 1 份收藏需求）。
"""
import recipes


def build_instance(sets, machines=4):
    runs = recipes.compute_runs(sets)
    types = [c for c in recipes.CARDS if runs.get(c, 0) > 0]
    idx = {c: i for i, c in enumerate(types)}
    n = len(types)
    dur = [recipes.DUR[c] for c in types]
    mats = []
    for c in types:
        mc = recipes.mats_count(c)
        mats.append(tuple((idx[m], k) for m, k in sorted(mc.items())))
    need0 = [runs[c] for c in types]
    depth = [recipes.depth_of(c) for c in types]

    # 每张卡的关键路径（链式下界）
    cp = [0] * n

    def cpf(i):
        if cp[i]:
            return cp[i]
        cp[i] = dur[i] + max((cpf(j) for j, k in mats[i]), default=0)
        return cp[i]

    for i in range(n):
        cpf(i)

    # 最后一张 i 卡开工前必须完成的炉时 = Σ k*ess(素材)（不含收藏副本）
    ess_before = [sum(k * recipes.ess_work(types[j]) for j, k in mats[i])
                  for i in range(n)]

    total_dur = sum(need0[i] * dur[i] for i in range(n))
    lb_root = max([total_dur / machines]
                  + [ess_before[i] / machines + dur[i] for i in range(n)]
                  + cp)

    return {
        "sets": sets,
        "machines": machines,
        "types": types,
        "n": n,
        "idx": idx,
        "dur": dur,
        "mats": mats,
        "need0": need0,
        "depth": depth,
        "cp": cp,
        "ess_before": ess_before,
        "total_dur": total_dur,
        "lb_root": lb_root,
    }


def summary(inst):
    by_face = {}
    for i, c in enumerate(inst["types"]):
        f = recipes.CARDS[c][1]
        by_face[f] = by_face.get(f, 0) + inst["need0"][i]
    return (f"sets={inst['sets']} 机器={inst['machines']} 类型数={inst['n']} "
            f"总卡数={sum(inst['need0'])} 总炉时={inst['total_dur']//3600}h "
            f"根下界={inst['lb_root']/3600:.2f}h 按面额={by_face}")
