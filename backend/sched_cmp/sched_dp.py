"""DP 精确求解器（类型级，自包含，零第三方依赖）。

状态 = (各类型待产数 need, 各类型库存 stock, 在炉 running=(类型,剩余时间,机器类)已排序)
  - 同类型卡完全可互换 → 按「类型计数向量」分支既完整又无排列爆炸
  - running 以「相对剩余时间」(finish - t) 表示 → 完成耗时只取决于 (need,stock,running_rel)，
    与绝对时刻 t 无关；memo 据此合并所有 t 平移的重复状态，大幅削减状态空间
  - 非延时：有空炉且有可开类型必开（可开数 total ∈ [1, min(空炉, 素材允许)]）
  - 开工即承诺（need 减 1、扣库存）；完成时入库存
  - makespan = 最后一张卡的完工时刻（叶子返回 max(0, 在炉最晚剩余) 作为「额外耗时」）

★ 2026-10-24 买卡入状态（用户定调：买卡必须是状态里的一等公民——"DP 选卡时不知道
  '买这张底料要 1 tick 物流'，所以选了一个需要底料的精炼但料没到 → 亏"）：
  · 底卡建模为 **shop 机器类上的买卡类型**：dur=LAG（1 tick=45s 物流）、mats 空、
    mclass=1。完工即 +1 库存（与精炼产出同构）；主炉（mclass=0，容量 M）与买卡
    （mclass=1，容量 shop_cap）容量分别限制、互不抢占 → DP 选卡时就知道买卡要
    1 tick，会把买卡动作排在消费者前 LAG，物流延迟不再是执行层外挂补偿。
  · 偷炉在炉作业（mclass 运行态 class=2）：容量 1、正确占用"不在主炉池里"且产出
    照记——避免旧版把 4 张在炉塞进 3 炉池造成的容量低估。
★ 2026-10-24 偷炉可规划（用户定调：骨架必须是好调度——"3 主炉计划本身 makespan
  就输给 4 炉贪心 20%，这是实现问题"）：
  · 40 面类型的 mclass 可以是**类表** [0,2]（主炉或偷炉均可）；DP 逐作业选类，
    偷炉（class 2，容量 steal_cap，默认 1）成为规划内第 M+1 台机器。
  · 非 40 面类型 [0]（只能主炉）、买卡类型 [1]；类表也接受旧 int 写法（等价单元素）。
  · 下界随机器类重算（保可采纳）：总炉时 W 拆「必须主炉的工作 (W-E)」与「偷炉可行
    的工作 E」→ LB = max(W/(M+steal_cap), (W-E)/M)；累资源下界分两子集各自算
    （非偷炉可行 / M 台，偷炉可行 / M+steal_cap 台）取 max。任何调度都分别满足
    两个子集的产能约束，故可采纳。
  · 实例无任何 class-2 类型/在炉时 steal_cap 自动取 0 → 完全退化为旧行为。
  · 卡箱容量 box_cap：任何落箱（买卡/精炼完工）后 sum(stock) 不得超限，超限分支
    剪枝 → DP 自动 JIT 买卡（不囤货撑箱），箱容成为状态内约束而非事后闸门。
  · 买卡 lazy 上界（_buy_demand）：只买「readyish 精炼」（除该底卡外喂料全在箱、
    或在炉必将到货）的底卡缺口 + 终局收藏需求。依据：买卡可推迟到消费者前 LAG 的
    最后一个决策点而不变差，故 lazy 不丢最优；同时把买卡分支从「shop_cap 张分到
    若干底卡类的组合数」压到 readyish 缺口（通常 1~3 张/类）。

可采纳下界（均可采纳，返回相对额外耗时 extra）：
  1) max(在炉剩余时间, 0)
  2) 剩余总炉时 / M        （仅主炉作业；买卡炉时由链式下界覆盖，避免高估）
  3) 链式：对每个未完工类型 i，dur[i] + max(素材最早可增量相对时刻)
  4) 累资源下界：考虑各类型最早开工(发布)时刻后的机器产能约束（≥ 2，永不劣化）
UB = 内置关键路径列表调度（含 lazy 买卡 + 死锁强买保底）；LB ≥ UB 剪枝；
记忆化 state → 相对最优额外耗时。
路径随递归下行携带（不回放 memo），叶子改进 UB 时记录完整调度。
返回 (makespan, schedule, stats)。
"""
import json as _json
import os as _os
import time as _t

EPS = 1e-9
INF = float("inf")

# ---- CP 最优轨迹 tie-break 表（2026-10-25 option 2）------------------------
# 背景：CP-SAT 的**精确时刻**接执行层是负收益（零容差紧排产，2★ 实测
# 24.12h vs sched_dp 骨架 23.10h）；但 CP 的**开工顺序**是对的（2★ 差分：
# 引擎 t166 先跑 240 的素材叶、把 233/239 推到 t328，CP t162 就让 233/239
# 上炉）。于是只抽「顺序」当静态优先级，时刻仍由 sched_dp 的列表调度/搜索
# 带余量重算，执行层保留容差 —— 搜索与执行解耦，各用其长。
_TB_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         "tb_hints.json")
_TB_CACHE = {"mtime": None, "map": {}}


def tb_hints():
    """cid → 最优开工 tick（升序优先）。文件缺失/损坏一律返回空表（= 不启用）。"""
    try:
        mtime = _os.path.getmtime(_TB_FILE)
    except OSError:
        return {}
    if _TB_CACHE["mtime"] != mtime:
        try:
            with open(_TB_FILE) as f:
                raw = _json.load(f)
            m = {}
            for k, d in raw.items():
                if str(k).startswith("__") or not isinstance(d, dict):
                    continue
                for c, v in d.items():
                    ci = int(c)
                    vi = float(v)
                    if ci not in m or vi < m[ci]:
                        m[ci] = vi
        except Exception:
            return {}
        _TB_CACHE.update(mtime=mtime, map=m)
    return _TB_CACHE["map"]


def tb_of(inst):
    """instance 的 tb 权重序列（与 inst["types"] 对齐）；无数据 → None。"""
    if inst.get("tb") is not None:
        return inst["tb"]
    m = tb_hints()
    types = inst.get("types") or []
    if not m or not types:
        return None
    return [m.get(int(c), 1e9) for c in types]


def _norm_running(init_running, n):
    """在炉作业规范化：(type_idx, 剩余秒, 机器类) 三元组、排序、越界剔除。

    兼容旧二元组 (type_idx, 剩余秒)（默认 class 0 = 主炉）。
    class：0=主炉（占 M 池）1=买卡在途（占 shop 池）2=偷炉（占 steal_cap 池）。
    """
    out = []
    for e in (init_running or []):
        if len(e) >= 3:
            i2, f, cls = int(e[0]), float(e[1]), int(e[2])
        else:
            i2, f, cls = int(e[0]), float(e[1]), 0
        if 0 <= i2 < n and f > -EPS:
            out.append((i2, f, cls))
    return tuple(sorted(out))


def _cls_of(mclass, i):
    """类型 i 的可用机器类（元组）。mclass[i] 可为 int（旧）或类表（新）。"""
    v = mclass[i]
    return v if isinstance(v, (list, tuple)) else (v,)


def _has_steal(mclass, n, run_norm):
    """实例是否涉及偷炉（class-2 类型或在炉）→ 决定 steal_cap 缺省值。"""
    if any(2 in _cls_of(mclass, i) for i in range(n)):
        return True
    return any(c == 2 for _i, _f, c in run_norm)


def _buy_demand(n, mclass, mats, stock, need, running_cnt):
    """各买卡类型的 lazy 需求（现在就该买的量）。

    readyish 精炼（need>0、除底卡外喂料全在箱）或 almost-readyish（缺的复合喂料
    在炉、必将到货）→ 其底卡缺口就是现在的购买需求。已无精炼需求时（只剩集卡册
    底卡）→ 需求 = need[b]（终局全买，防死锁）。
    """
    dem = {}
    ref_need = False
    # soon[j]：复合卡 j 即将到货（在炉，或其喂料已齐、本决策点即可开炼）
    soon = {}
    for j in range(n):
        if 0 not in _cls_of(mclass, j) or need[j] <= 0:
            continue
        if running_cnt.get(j, 0) > 0:
            soon[j] = True
        elif all(stock[m] >= k for m, k in mats[j]):
            soon[j] = True
    for i in range(n):
        if need[i] <= 0 or 0 not in _cls_of(mclass, i):
            continue
        ref_need = True
        ok = True
        add = {}
        for j, k in mats[i]:
            if stock[j] >= k:
                continue
            if 1 in _cls_of(mclass, j):
                add[j] = add.get(j, 0) + k * need[i]   # × 待炼次数
            elif soon.get(j):
                continue          # 在炉/可立即开炼的复合喂料：不挡 readyish
            else:
                ok = False        # 缺暂不能到货的复合喂料 → 现在买太早
                break
        if ok:
            for j, k in add.items():
                dem[j] = dem.get(j, 0) + k
    if not ref_need:
        for b in range(n):
            if 1 in _cls_of(mclass, b) and need[b] > 0:
                dem[b] = need[b]
    return dem

def _up_cp(dur, mats, n):
  """向上关键路径：dur + 消费者链（「有多少活等我」）。

  ★ RCPSP 经典 CP 口径，与骨架 `inst["cp"]`（dur + 喂料链，**向下**）相对。
  向下 cp 对叶子卡全部打平（叶子只等底卡，dur 相同就完全同值）→ 列表调度
  的 ready 排序退化成「按索引」，会把炉位先给非关键叶子（2026-10-25 CP 最优
  轨迹差分实锤：2★ 引擎先炼 238/234，而 241←233←{237,235,232} 才是关键链，
  233 因此延到 t328 才开工，顶层 241 晚 87t = 1.09h）。
  """
  consumers = [[] for _ in range(n)]
  for i in range(n):
    for j, _k in mats[i]:
      j = int(j)
      if 0 <= j < n:
        consumers[j].append(i)
  up = [0.0] * n

  def f(i):
    if up[i]:
      return up[i]
    up[i] = dur[i] + max((f(p) for p in consumers[i]), default=0.0)
    return up[i]

  for i in range(n):
    f(i)
  return up


def _ub_schedule(inst, init_stock=None, init_running=None,
                 init_machine_free=None):
  """关键路径列表调度求上界（含 shop 买卡类型，lazy 买卡；鲁棒：无进展即推进时间）。

  init_stock / init_running / init_machine_free 支持**滚动重规划**：
  传入当前库存、在炉剩余时间、炉位可用时刻，求「从现在起」的最优上界。
    · init_stock        [张数]×n，类型索引序
    · init_running      [(type_idx, 剩余秒[, 机器类])]，剩余秒 = 相对当前时刻
    · init_machine_free [秒]×M，各主炉下次可用时刻（相对当前时刻）
  默认全空 = 从零开始（与原行为完全一致）。"""
  dur = inst["dur"]
  mats = inst["mats"]
  M = inst["machines"]
  cp = inst["cp"]
  depth = inst["depth"]
  n = inst["n"]
  # ★ 注意：ready 排序**仍用向下 cp**（inst["cp"]）。2026-10-25 试过换「向上 cp」：
  # 3★/5★ UB 变好（78.68→76.01、178.82→173.08）但 2★ UB 变差（27.01→28.01）、
  # 引擎级 2★ 23.10→24.11h 回退 → 已回退。2★ 的 233/239/240 三条支路 ucp 完全相同
  # （都经 320t 卡喂 241），静态优先级无法区分，换口径只是换了个平局顺序。
  mclass = inst.get("mclass") or [0] * n
  # ★ CP 顺序 tie-break（2026-10-25 option 2）：tb[i] 升序 = CP 最优解里
  # 该类卡更早开工。tb_first=True 时 tb 作**主序**（CP 顺序完全接管列表调度
  # 的选卡），否则只作 cp 的同分 tie-break（对既有行为零影响）。
  tb = inst.get("tb")
  tb_first = bool(inst.get("tb_first"))
  if tb is None:
      tb = [0.0] * n
  def _skey(i):
      if tb_first:
          return (tb[i], -cp[i], -dur[i], -depth[i], i)
      return (-cp[i], tb[i], -dur[i], -depth[i], i)
  S = int(inst.get("shop_cap") or 0)
  box_cap = inst.get("box_cap")
  if box_cap is None:
      box_cap = INF
  # ★★ 2026-10-27 用户定调：「10 面额底卡可以认为本身就存在，因为可以随时买、
  #   也不用花 tick」。真机 `card_buy` code=0 即落换卡箱、仿真 `EP_BUY` 即
  #   `_add_to_box` —— 没有任何物理延迟。打开后买卡**不占 shop 时间槽**、
  #   当场进 stock（同一 t 的精炼就能用），只受箱容约束。
  buy_free = bool(inst.get("buy_free"))
  stock = list(init_stock) if init_stock is not None else [0] * n
  need = list(inst["need0"])
  run_norm = _norm_running(init_running, n)
  sc = inst.get("steal_cap")
  sc = int(sc) if sc is not None else (1 if _has_steal(mclass, n, run_norm) else 0)
  mfree = [[0.0] * M, [0.0] * S, [0.0] * sc]
  if init_machine_free is not None:
      mf = [float(x) for x in init_machine_free]
      mfree[0] = (mf + [0.0] * M)[:M]
  else:
      # 由在炉推导各类机器可用时刻（忙的炉 = 剩余时间降序，其余空闲）
      rem0 = sorted((f for _i, f, c in run_norm if c == 0), reverse=True)
      if rem0:
          mfree[0] = (rem0 + [0.0] * M)[:M]
      if S:
          rem1 = sorted((f for _i, f, c in run_norm if c == 1), reverse=True)
          if rem1:
              mfree[1] = (rem1 + [0.0] * S)[:S]
      if sc:
          rem2 = sorted((f for _i, f, c in run_norm if c == 2), reverse=True)
          if rem2:
              mfree[2] = (rem2 + [0.0] * sc)[:sc]
  running = [(f, i) for i, f, _c in run_norm]      # 各炉产出到账用
  buy_run = []                                       # [(finish, 底卡类型)] 在途买卡
  out = []
  guard = 0
  limit = 8 * sum(need) + 64
  while any(need):
    guard += 1
    if guard > limit:
      break
    all_free = list(mfree[0]) + (list(mfree[1]) if S else []) + list(mfree[2])
    t = min(all_free) if all_free else 0.0
    still = []
    for f, i in running:
      if f <= t + EPS:
        stock[i] += 1
      else:
        still.append((f, i))
    running = still
    still_b = []
    for f, b in buy_run:
      if f <= t + EPS:
        stock[b] += 1
      else:
        still_b.append((f, b))
    buy_run = still_b
    free_f = [m for m in range(M) if mfree[0][m] <= t + EPS]
    free_s = [m for m in range(S) if mfree[1][m] <= t + EPS] if S else []
    started = 0
    # ---- lazy 买卡：readyish/almost 精炼的底卡缺口（箱容闸门防撑爆）----
    if S and free_s:
      running_cnt = {}
      for _f, i in running:
        running_cnt[i] = running_cnt.get(i, 0) + 1
      dem = _buy_demand(n, mclass, mats, stock, need, running_cnt)
      box_used = sum(stock) + len(buy_run)
      # 每类型上限 = min(剩余总需求, readyish 需求) - 箱内（dem 封顶保 JIT）
      caps = {b: min(need[b], dem[b]) - stock[b]
              for b in sorted(dem) if dem[b] > stock[b]}
      # ★ 轮转买 + 落位保留（2026-10-25 紧箱根治，对齐 dfs 与贪心纪律）：
      # ① 每轮每种各买 1 张——旧版按类型序把一种买到上限 → 箱被单类型塞满
      #    → 3 合 1 trio 凑不齐 → 列表调度死锁 → 死锁保护强制买卡（无箱检）
      #    → UB 超容（紧箱 60 格实测静态峰 88/60 → 回退即超容 → relax）。
      # ② 不变量「箱内 + 在途 + 在炉产物 + 本 buy ≤ cap」——买前校验买后
      #    占用（旧版 proj=买前占用 ≤ cap 即放行 → 买后实占 cap+1，差一）。
      #    不变量被全部迁移保持 → UB 箱容可行（峰值 ≤ cap）。
      while free_s and box_used + len(running) + 1 <= box_cap + EPS:
        progressed = False
        for b in list(caps):
          if caps[b] <= 0:
            continue
          if not free_s or box_used + len(running) + 1 > box_cap + EPS:
            break
          m = free_s.pop(0)
          if buy_free:
              # 即时落箱：同一 t 的精炼段立刻能用（不写 buy_run、槽位立刻空出）
              land_t = t
              stock[b] += 1
          else:
              land_t = t + dur[b]
              buy_run.append((land_t, b))
          mfree[1][m] = land_t
          out.append((b, t))
          need[b] -= 1
          caps[b] -= 1
          box_used += 1
          started += 1
          progressed = True
        if not progressed:
          break
    # ---- 精炼：填满空主炉（cp 降序）----
    if free_f:
      ready = sorted([i for i in range(n)
                      if 0 in _cls_of(mclass, i) and need[i] > 0
                      and all(stock[j] >= k for j, k in mats[i])],
                     key=_skey)
      for m in free_f:
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
        mfree[0][m] = t + dur[i]
        started += 1
    # ---- 偷炉：主炉填完后用剩余偷炉位跑 40 面可开工卡（cp 降序）----
    free_st = [m for m in range(sc) if mfree[2][m] <= t + EPS]
    while free_st:
      ready_st = sorted([i for i in range(n)
                         if 2 in _cls_of(mclass, i) and need[i] > 0
                         and all(stock[j] >= k for j, k in mats[i])],
                        key=_skey)
      pick = None
      for i in ready_st:
        if all(stock[j] >= k for j, k in mats[i]):
          pick = i
          break
      if pick is None:
        break
      i = pick
      for j, k in mats[i]:
        stock[j] -= k
      need[i] -= 1
      out.append((i, t))
      running.append((t + dur[i], i))
      mfree[2][free_st.pop(0)] = t + dur[i]
      started += 1
    if started:
      continue
    nxt = [mf for mf in (mfree[0] + (mfree[1] if S else []) + mfree[2])
          if mf > t + EPS]
    nxt += [f for f, _ in running if f > t + EPS]
    nxt += [f for f, _ in buy_run if f > t + EPS]
    if not nxt:
      # 死锁保护（UB 必须完整，否则上界失效）：强买剩余底卡需求
      forced = False
      if S:
        fs2 = [m for m in range(S) if mfree[1][m] <= t + EPS]
        for b in range(n):
          if 1 not in _cls_of(mclass, b) or need[b] <= stock[b] or not fs2:
            continue
          m = fs2.pop()
          if buy_free:
              stock[b] += 1
              mfree[1][m] = t
          else:
              buy_run.append((t + dur[b], b))
              mfree[1][m] = t + dur[b]
          out.append((b, t))
          need[b] -= 1
          forced = True
      if not forced:
        break
      continue
    nv = min(nxt)
    for m in range(M):
      if mfree[0][m] <= t + EPS:
        mfree[0][m] = nv
    for m in range(S):
      if mfree[1][m] <= t + EPS:
        mfree[1][m] = nv
    for m in range(sc):
      if mfree[2][m] <= t + EPS:
        mfree[2][m] = nv
  return out


class _SearchAbort(Exception):
    """搜索预算耗尽（deadline / node_budget）：整体回卷到 solve 顶层。

    旧实现用「返回 INF」表达预算耗尽——父循环继续扫剩余向量、每子一次
    dfs 调用再 INF，实测 4★ 在超时后仍磨 1.9M 次空转 dfs（9.9s）。
    异常回卷 = 一次性 unwind， anytime 语义不变（best 已记录最优完整调度）。
    """


class _CapAbort(Exception):
    """_vectors 单次枚举达到 rec_budget / 收集上限：内部截断（不全量回卷）。"""


def _vectors(items, caps, mclass, mats, stock, need, maximal_only=True,
             deadline=None, max_vecs=None, rec_budget=None, prio=None,
             prio2=None, prio2_first=False):
    """所有可行开工向量：每类型数 ≤ items 里的上限，且**按机器类**不超容量。

    items: [(type_idx, 单类型上限)]——上限已按 need/素材压过（买卡已改为
    dfs 内 lazy 确定性执行、不进本枚举，见 solve 注释）
    caps:  [各类机器剩余容量]；mclass[i] 决定用哪一类——int（旧，单类）或类表
    （新，如 [0,2] = 该作业可上主炉或偷炉，逐作业选类、可拆到不同类）。
    maximal_only=True 时只保留「极大」向量（任一类都加不进任何一张）——
    非延时调度中还能加开却不开不会更优（空炉+库存素材是纯浪费），
    该限制把分支数降一个量级且不丢最优解。
    向量元素：(type_idx, 张数, 机器类)。

    deadline: perf_counter 绝对时刻守卫——超时抛 _SearchAbort 整体回卷
      （每 32 次 rec 检查一次；旧版 1024 粒度 + 只退当前帧，超时后仍以
      1023 步为粒度磨完整棵树，2s 预算实测跑 22s）。
    max_vecs / rec_budget: **有限偏差**（用户定调 LDS）——高星宽节点极大向量
      成千上万（4★ 单节点实测 5.4M 次 rec），全量枚举一个节点就吃光预算、
      DFS 永远停在第一层。截断到 top max_vecs（按 prio 权重降序）后搜索才
      能真正下沉； anytime 语义安全（只可能使 makespan 变差，绝不会更优；
      bounded 由 solve 统一记录，capped 节点数进 stats）。
    prio: 各类型启发式权重（默认 cp 降序 = 关键路径优先），仅影响截断后
      保留哪些向量与尝试顺序，不影响可行性/极大性。

    返回 (vectors, complete)——complete=False 表示被 max_vecs/rec_budget
    截断（调用方按上界语义使用，不可当作精确值）。
    """
    if not items or not any(c > 0 for c in caps):
        return [], True
    res = []
    ni = len(items)
    ncls = len(caps)
    cls_lists = [_cls_of(mclass, i) for i, _lim in items]
    _ck = [0]

    def maximal(counts, used, st):
        # 容量快路径：主炉/偷炉全占满 → 任何 item 都加不进（宽节点绝大多数
        # 叶子走此路，省掉逐 item 素材检查——旧版 3.29M 次 × 17.4s 热点）。
        if used[0] >= caps[0] and used[2] >= caps[2]:
            return True
        cnt = {}
        for i, c, _ci in counts:
            if c:
                cnt[i] = cnt.get(i, 0) + c
        for pos, (i, _lim) in enumerate(items):
            if cnt.get(i, 0) + 1 > need[i]:
                continue
            for j, k in mats[i]:
                if st[j] < k:
                    break
            else:
                for ci in cls_lists[pos]:
                    if used[ci] + 1 <= caps[ci]:
                        return False       # 还能加一张 → 非极大
        return True

    def rec(p, used, cur, st):
        _ck[0] += 1
        if not (_ck[0] & 31):           # 每 32 步一次（perf_counter ~50ns）
            if deadline is not None and _t.perf_counter() > deadline:
                raise _SearchAbort
            if rec_budget is not None and _ck[0] > rec_budget:
                raise _CapAbort
        if p == ni:
            if any(c > 0 for _i, c, _ci in cur) and \
                    (not maximal_only or maximal(cur, used, st)):
                res.append(tuple(cur))
                if max_vecs is not None and len(res) >= max_vecs * 4:
                    raise _CapAbort
            return
        i, lim = items[p]
        for ci in cls_lists[p]:
            top = min(lim, caps[ci] - used[ci])
            for c in range(top, -1, -1):
                ok = True
                for j, k in mats[i]:
                    if st[j] < c * k:
                        ok = False
                        break
                if not ok:
                    continue
                for j, k in mats[i]:
                    st[j] -= c * k
                cur.append((i, c, ci))
                used[ci] += c
                rec(p + 1, used, cur, st)
                used[ci] -= c
                cur.pop()
                for j, k in mats[i]:
                    st[j] += c * k     # ★ 必须与扣减同乘 c：旧版写 += k，
                                        #   c≥2（同类型起 2+ 张）时库存被污染 →
                                        #   后续分支少算素材、maximal 误判 →
                                        #   枚举不完整，「bounded=False 证完最优」
                                        #   是假证明（4★ 实测 82.05h"最优" vs
                                        #   截断运行实找到 78.05h）。

    complete = True
    try:
        rec(0, [0] * ncls, [], list(stock))
    except _CapAbort:
        complete = False
    if res:
        if prio is not None:
            if prio2 is not None and prio2_first:
                # CP 顺序作主序（2026-10-25 option 2）：先让「更该早上炉」的
                # 卡型占位，同分再按 cp。仅改尝试顺序/截断保留，不改可行性。
                res.sort(key=lambda v: (
                    sum(c * prio2[i] for i, c, _ci in v),
                    -sum(c * prio[i] for i, c, _ci in v)))
            elif prio2 is not None:
                res.sort(key=lambda v: (
                    -sum(c * prio[i] for i, c, _ci in v),
                    sum(c * prio2[i] for i, c, _ci in v)))
            else:
                res.sort(key=lambda v: -sum(c * prio[i] for i, c, _ci in v))
        else:
            res.sort(key=lambda v: -sum(c for _i, c, _ci in v))
        if max_vecs is not None and len(res) > max_vecs:
            res = res[:max_vecs]
            complete = False
    return res, complete


def _chain_avail_rel(j, k, stock, running_rel, seen):
    """k 张 j 卡可用的最早增量时刻（相对剩余时间，可采纳下界）。

    running_rel 为 (type, 剩余, 类) 三元组；买卡类型（dur=LAG）无素材 →
    最早可得时刻 = LAG（假设现在就买，可采纳）。"""
    if stock[j] >= k:
        return 0.0
    fins = [f for (j2, f, _c) in running_rel if j2 == j]
    if fins:
        if stock[j] + len(fins) >= k:
            return sorted(fins)[k - stock[j] - 1]
        return min(fins)
    if j in seen:
        return 0.0
    seen.add(j)
    return DUR[j] + max((_chain_avail_rel(m, kk, stock, running_rel, seen)
                         for m, kk in MATS[j]), default=0.0)


def _cum_lb(need, stock, running_rel, t, mclass, steal_cap=0):
    """累资源下界（相对额外耗时，可采纳，≥ 剩余总炉时/M）。

    各类型最早开工(发布)时刻 s_i 由链式可得；则最优 makespan ≥ max(
      剩余总炉时/M,          ← 仅主炉作业（买卡炉时计入会高估→不可采纳）
      max_i(s_i + dur_i),
      max_t  max( A(t)/M, t + B(t)/M )
    )  其中 A(t)=发布≤t 的炉时、B(t)=发布>t 的炉时。

    ★ 偷炉可规划时（steal_cap>0 且存在 class-2 类型）分子集算，两者都可采纳：
      · 非偷炉可行工作（2 不在类表）——只能上主炉 → M 台产能；
      · 偷炉可行工作（2 在类表）——可上主炉或偷炉 → M+steal_cap 台产能。
    任何调度分别满足两个子集的产能约束（主炉工作量 ≤ M·T、偷炉可行工作量
    ≤ (M+steal_cap)·T），取 max 不丢可采纳性；竞争关系忽略 → 偏松但安全。
    steal_cap=0 或无 class-2 类型时退化为原版（全部精炼工作 / M）。
    """
    def energetic(sel, mach, run_cls):
        starts = []
        running_types = set(i for i, _f, _c in running_rel)
        for i in range(N):
            if need[i] <= 0 or i in running_types or not sel(i):
                continue
            s = max((_chain_avail_rel(j, kk, stock, running_rel, set())
                     for j, kk in MATS[i]), default=0.0)
            starts.append((s, DUR[i], need[i]))
        rem_running = sum(f for _i, f, c in running_rel if c in run_cls)
        Wtot = sum(d * nd for _, d, nd in starts) + rem_running
        lb = Wtot / mach
        for s, d, nd in starts:
            if s + d > lb:
                lb = s + d
        pts = sorted(set([0.0] + [s for s, _, _ in starts]))
        for tt in pts:
            A = sum(d * nd for s, d, nd in starts if s <= tt) + (
                rem_running if tt >= 0 else 0)
            B = sum(d * nd for s, d, nd in starts if s > tt)
            if A / mach > lb:
                lb = A / mach
            if tt + B / mach > lb:
                lb = tt + B / mach
        return lb

    if steal_cap <= 0 or not any(2 in _cls_of(mclass, i) for i in range(N)):
        return energetic(lambda i: 0 in _cls_of(mclass, i), MACHINES, (0,))
    lb_main = energetic(lambda i: 2 not in _cls_of(mclass, i), MACHINES, (0,))
    lb_steal = energetic(lambda i: 2 in _cls_of(mclass, i),
                         MACHINES + steal_cap, (2,))
    return max(lb_main, lb_steal)


def solve(inst, time_budget=120.0, node_budget=8_000_000, use_memo=True,
          maximal_only=True, init_stock=None, init_running=None,
          init_machine_free=None, memo=None, max_vecs=96, rec_budget=50000):
    """求解 RCPSP 实例。

    ★ 滚动重规划入口（init_* 三参数）：从**当前真实状态**起算，而非从零开始。
      · init_stock      当前库存 {type_idx: 张数}
      · init_running    在炉 [(type_idx, 剩余秒[, 机器类])]（相对当前时刻的剩余时间；
                        机器类 0=主炉 1=买卡在途 2=偷炉，缺省 0）
      · init_machine_free 各主炉下次可用时刻（秒，相对当前时刻）
      传入后返回的 makespan 是「从当前时刻起还需多久」。

    ★ memo：允许传入外部 memo 字典实现跨 tick 热启动。调用方须在实例骨架
      （dur/mats/类型集/机器类/箱容）变化时重置 memo。

    ★ max_vecs / rec_budget（2026-10-24 有限偏差，用户定调 LDS）：宽节点极大
      向量截断参数。4★ 实测全量枚举单节点 5.4M 次 rec（48s）→ DFS 永远停在
      第一层（15 节点 / DP=UB 86.04h 输贪心 80.08h）。截断后搜索真正下沉；
      anytime 语义安全（bounded/capped 进 stats，best 始终是完整调度）。

    ★ 实例扩展字段（2026-10-24 买卡入状态 + 偷炉可规划，全部有默认值、缺省 = 旧行为）：
      · mclass   ×n  类型机器类：int（旧，0/1/2）或**类表**（新）——0=主炉精炼
        1=买卡(shop) 2=偷炉；[0,2] 表示 40 面卡可上主炉或偷炉（逐作业选类）
      · shop_cap int        买卡并发上限（每秒 tick 最多买几张）
      · box_cap  number     卡箱容量上限（任何落箱后 sum(stock) ≤ 上限）
      · steal_cap int       偷炉容量（缺省：有 class-2 类型/在炉→1，否则 0=不可用）
    """
    global DUR, MATS, MACHINES, N
    DUR = inst["dur"]
    MATS = inst["mats"]
    MACHINES = inst["machines"]
    N = inst["n"]
    dur = DUR
    mats = MATS
    M = MACHINES
    n = N
    mclass = inst.get("mclass") or [0] * n
    S = int(inst.get("shop_cap") or 0)
    box_cap = inst.get("box_cap")
    if box_cap is None:
        box_cap = INF
    need0 = tuple(inst["need0"])
    run_norm = _norm_running(init_running, n)
    sc = inst.get("steal_cap")
    sc = int(sc) if sc is not None else (1 if _has_steal(mclass, n, run_norm) else 0)

    # ★ 向上 cp 在此预计算并注入 inst["ucp"]：供 vec_prio（搜索指路）复用，
    # 外部也可直接传 inst["ucp"] 覆盖。注意 UB 的 ready 排序**不用**它
    # （2026-10-25 试过 → 2★ 回退，见 _ub_schedule 内注释）。
    if not inst.get("ucp"):
        inst["ucp"] = _up_cp(dur, mats, n)
    _up = inst["ucp"]
    # CP 顺序 tie-break（option 2）：**只认显式传入**，不自动加载——
    # 2026-10-25 引擎级实锤：自动启用会把 2★ 从 23.10h 拖到 24.07h
    # （更好的静态序让头动作更少被卡 → 重解 5→2 次 → 少掉执行层自适应修正），
    # 故默认关闭，由 dp_controller 按 MC_TB=1 显式注入。
    ub_sched = _ub_schedule(inst, init_stock, init_running, init_machine_free)
    _buyfree = bool(inst.get("buy_free"))
    if _buyfree:
        # 底卡不耗时 → 它的「完成时刻」不构成 makespan（否则 UB 白多 45s 尾巴）
        ub = max((s if 1 in _cls_of(mclass, i) else s + dur[i]
                  for i, s in ub_sched), default=0.0)
    else:
        ub = max((s + dur[i] for i, s in ub_sched), default=0.0)
    best = {"val": ub, "sched": list(ub_sched), "from": "UB"}
    stats = {"nodes": 0, "time": 0.0, "bounded": False, "ub0": ub,
             "memo_hits": 0, "opt": None, "capped": 0}
    t0 = _t.perf_counter()
    memo = memo if (use_memo and memo is not None) else {}
    # ★ buy_free 下 dfs 仍按「买卡要 45s」的口径搜索，与 UB 的新口径不一致 →
    #   best=min(UB, dfs) 会取到旧口径的解，执行层按新口径跑就错配。
    #   ub_only 直接交 UB 的解（仅供判定口径一致性，正常不开）。
    if _buyfree and inst.get("ub_only"):
        stats["time"] = 0.0
        stats["ub_only"] = True
        return ub, list(ub_sched), stats
    # 向量启发式权重 = 向上 cp（上面已算并注入 inst["ucp"]）。
    # 骨架 inst["cp"] 是**向下**口径（dur + 喂料链），所有叶子打平（4★ 实测全
    # = 7245）→ LDS 截断（max_vecs=96）按权重截向量时关键链向量与非关键叶向量
    # 同权 → 枚举序决定存亡 → 4★ 只能搜到 82.04h（旧代码 78.05h 是 bulk 买造成
    # 窄 ready 集的意外）。向上 cp = 有多少活等我（RCPSP 经典 CP 规则），只影响
    # 向量截断/排序与 UB ready 排序（搜索指路），不影响 DP 正确性。
    # 实测：4★ 引擎级 84.08h → 77.45h；2★ 关键链开工提前（见 _up_cp 注释）。
    vec_prio = _up

    def key_of(need, stock, running_rel):
        return (need, stock, running_rel)

    def path_to_sched(path):
        out = []
        for t, vec in path:
            for i, c, _ci in vec:
                for _ in range(c):
                    out.append((i, t))
        return out

    def cells_to_sched(head):
        """后缀链（delta 编码）→ 绝对时间调度表。root 入口 t=0 → delta 即绝对。"""
        out = []
        tr = 0.0
        while head is not None:
            delta, vec, tail = head
            tr += delta
            if vec is not None:
                for i, c, _ci in vec:
                    for _ in range(c):
                        out.append((i, tr))
            head = tail
        return out

    def chain_of(ops):
        """[(t_rel, vec)] 时间正序 → delta 编码 cons 链（头=最早 op）。"""
        cells = []
        prev = 0.0
        for tr, vv in ops:
            cells.append((tr - prev, vv))
            prev = tr
        head = None
        for d, vv in reversed(cells):
            head = (d, vv, head)
        return head

    def link(own_head, tail_head):
        """把 own_head 链的尾接到 tail_head 前（返回新链头）。

        ★ 必须从尾到头反接：最后单元直接链 tail_head。旧版先改 cells[-1]
        的 tail 再整体重建——重建循环第一轮（处理 cells[-1]）就把 tail
        重置成 None，子树后缀每次都被静默甩掉（1★ 实测重建调度 64→31 ops）。
        """
        if own_head is None:
            return tail_head
        cells = []
        c = own_head
        while c is not None:
            cells.append(c)
            c = c[2]
        head = tail_head
        for cc in reversed(cells):
            head = (cc[0], cc[1], head)
        return head

    def dfs(need, stock, running_rel, t, path):
        """返回 (从该状态的相对额外耗时, 最优后缀链)。

        后缀链：cons 单元 (delta, vec|None, tail)——delta = 距上一 op 的相对
        时间，vec=None 为纯延时单元。memo 存（值, 后缀）而非只存值：
        ★ 旧版只存值 → memo 命中跳过叶子访问 → 最优调度的叶子永不被记录 →
          DP 算出 root=6.03h 却只能返回 7.03h 的已访叶子 schedule（1★ 实测
          差 1h）。后缀链让「值最优」与「调度可重建」同时成立（delta 编码
          避免平移子树，内存 O(节点数)）。
        path 参数仍贯穿（绝对时间）：abort 时已访叶子的调度兜底。
        """
        cur_path = path          # 本次调用累计的绝对时间 op 路径（含买卡）
        t_rel = 0.0              # 相对本次入口已过去的时间
        own_ops = []             # 本次调用产生的 op [(t_rel, vec)]（买卡 bundle）
        while True:
            # ---- 叶子：全部开工完 → 额外耗时 = 在炉最晚剩余 ----
            if not any(need):
                fin = max([0.0] + [f for _i, f, _c in running_rel])
                if t + fin < best["val"] - 1e-9:
                    best["val"] = t + fin
                    best["sched"] = path_to_sched(cur_path)
                    best["from"] = "DP"
                # ★ 后缀必须包含本次调用已产生的 op（典型：终局专辑底卡买卡
                #   发生在叶子前一迭代）——旧版返 None 把它们全丢（1★ 实测
                #   丢 5 张终局买卡 → 重建调度缺 op）。
                # ★ 返回值必须加 t_rel：while 循环内「推进到下一完工」耗时的
                #   时间旧版没算进返回值（fin 只相对叶子当前 t）→ memo 值系统性
                #   偏小 → 幻影最优（1★ 实测 root=6.03h < 真实 7.03h，且违反
                #   自身总炉时下界 6.5h）。绝对值 t+fin 不受影响（旧代码答案
                #   正确纯属叶子赋值侥幸）。
                return t_rel + fin, chain_of(own_ops)

            key = key_of(need, stock, running_rel)
            hit = memo.get(key) if use_memo else None
            if hit is not None:
                stats["memo_hits"] += 1
                # 后缀 = 本次调用已做的 op + memo 态起的后缀。
                # ★ 两者之间可能推进过时间——「买卡后无向量可开→跳到下一完工」
                #   是 JIT 主路径（买底卡→等 LAG→收卡→炼卡），此时 own_ops
                #   非空且 t_rel 已前进 → 必须补一个纯延时单元衔接，否则
                #   memo 后缀的首个 op 会被提前到上一个 op 时刻，重建调度
                #   时序错乱（买卡时刻与到账时刻对不上）。
                # 值同理加 t_rel（hit 值相对 memo 态 = 当前时刻）。
                own_head = chain_of(own_ops)
                if own_head is None:
                    return t_rel + hit[0], hit[1]
                delay = t_rel - own_ops[-1][0]
                if delay > EPS:
                    return t_rel + hit[0], link(own_head, (delay, None, hit[1]))
                return t_rel + hit[0], link(own_head, hit[1])
            if stats["nodes"] > node_budget or (_t.perf_counter() - t0) > time_budget:
                stats["bounded"] = True
                raise _SearchAbort      # 预算耗尽：整体回卷（旧版返 INF 让父循环
                                        # 继续空转，4★ 实测多磨 1.9M 次/9.9s）
            stats["nodes"] += 1

            # ---- 收卡：剩余时间 <= 0 的入库存 + 箱容校验 ----
            if any(f <= EPS for _i, f, _c in running_rel):
                ns = list(stock)
                nr = []
                for i2, f, c2 in running_rel:
                    if f <= EPS:
                        ns[i2] += 1
                    else:
                        nr.append((i2, f, c2))
                if sum(ns) > box_cap + EPS:
                    return INF, None    # 落箱超容 → 该分支不可行（DP 改走 JIT）
                stock = tuple(ns)
                running_rel = tuple(nr)
                continue

            # ---- 可启动：精炼（素材齐）；买卡走下方 lazy 确定性规则 ----
            running_cnt = {}
            for i2, _f, _c in running_rel:
                running_cnt[i2] = running_cnt.get(i2, 0) + 1
            dem = _buy_demand(n, mclass, mats, stock, need, running_cnt)
            items = []
            for i in range(n):
                if need[i] <= 0:
                    continue
                if 0 in _cls_of(mclass, i):
                    if all(stock[j] >= kk for j, kk in mats[i]):
                        lim = need[i]
                        for j, kk in mats[i]:
                            lim = min(lim, stock[j] // kk)
                        if lim > 0:
                            items.append((i, lim))
            idle = [0, 0, 0]
            for _i2, _f2, c2 in running_rel:
                if 0 <= c2 < 3:
                    idle[c2] += 1
            idle[0] = M - idle[0]
            idle[1] = S - idle[1]
            idle[2] = sc - idle[2]

            # ---- 可采纳下界（相对额外耗时）----
            lb = max([0.0] + [f for _i, f, _c in running_rel])
            # 资源下界（偷炉可规划时）：总炉时 W 中最多 E 可上偷炉 →
            #   主炉工作量 ≥ W-E（M 台）、总工作量 ≤ (M+偷炉容量) 台 → 两者取 max。
            # 可采纳性：任何调度 W0≤M·T（主炉）、W2≤E（偷炉只接 eligible+在炉），
            #   故 T ≥ (W-E)/M 且 T ≥ W/(M+sc_lb)。
            W_fut = sum(need[i] * dur[i] for i in range(n)
                        if 0 in _cls_of(mclass, i))
            W_run0 = sum(f for _i, f, c in running_rel if c == 0)
            W_run2 = sum(f for _i, f, c in running_rel if c == 2)
            E_fut = (sum(need[i] * dur[i] for i in range(n)
                         if 2 in _cls_of(mclass, i)) if sc > 0 else 0)
            E = E_fut + W_run2
            sc_lb = sc if sc > 0 else (1 if W_run2 > 0 else 0)
            W = W_fut + W_run0 + W_run2
            lb2 = max(W / (M + sc_lb), (W - E) / M)
            if lb2 > lb:
                lb = lb2
            running_types = set(i for i, _f, _c in running_rel)
            for i in range(n):
                if need[i] <= 0 or i in running_types:
                    continue
                ch = dur[i] + max((_chain_avail_rel(j, kk, stock, running_rel, set())
                                   for j, kk in mats[i]), default=0.0)
                if ch > lb:
                    lb = ch
            cum = _cum_lb(need, stock, running_rel, t, mclass, sc)
            if cum > lb:
                lb = cum
            # 绝对下界 = t + lb；超过已知最优则剪枝（仅当前节点放弃）
            if t + lb >= best["val"] - 1e-9:
                return INF, None

            # ---- 买卡：lazy 规则确定性执行（状态内一等公民，但不分支）----
            # 买卡的 need/stock/箱容/LAG 全在建模里（完工进 running_rel class 1、
            # 落箱过箱容即 INF），DP 选精炼时知道料什么时候到；但「这一决策点
            # 买几张」不枚举——lazy 需求已由 _buy_demand 按消费者就绪度算好，
            # 逐类型买即可。枚举买卡张数组合会把每节点分支放大 ~10^3 倍
            # （4★ 实测：20s 只走 17 个节点，DP 实际退化成 UB 列表调度，
            # 86.04h 输给贪心 80.08h）。确定性化后每节点只剩精炼选择
            # （≤ M+偷炉 张），搜索才真正跑得动。
            # 本迭代 op 基线（买卡块前的 own_ops 长度）——分支段构建 memo
            # 后缀时只取本迭代的 op（相对迭代顶状态 delta 归零）；前序迭代
            # 的 op 属于更早的状态，混入会在 memo 命中时重放更早的买卡。
            iter_start = len(own_ops)
            if S > 0 and idle[1] > 0:
                need = list(need)
                # 箱占用保守口径：箱内 + 在途买卡（在途精炼产出由落箱时校验）
                box_now = sum(stock) + sum(1 for _i, _f, c in running_rel
                                           if c == 1)
                # ★ 两项紧箱根治（2026-10-25，对齐贪心 2026-10-23 实锤纪律）：
                # ① 在炉产物落位保留：不变量「箱内 + 在途买卡 + 在炉产物 ≤
                #    box_cap」——旧版只校验箱内+在途 < cap，确定性买卡把箱囤到
                #    顶 → 在炉产物落箱即超容 → 所有分支 INF → 约束解 15 节点
                #    即死（紧箱 60 格·各2轮实测：root 返 INF → relax 无约束 →
                #    静态投影 88/60 → 执行层囤死 done=0/4）。不变量被全部迁移
                #    保持（买卡买前校验、落箱 ±0、精炼开工 -(k-1)）。
                # ② 轮转买：每轮每种需求类型各买 1 张——旧版按类型序把一种
                #    底卡买到需求上限（如 231×24 买完才买 230）→ 箱被单类型
                #    塞满 → 3 合 1 trio 永远凑不齐 → 无精炼可开 → 死锁（正是
                #    贪心侧 2026-10-23 修掉的「多套死锁另一根因」，DP 同款）。
                #    平衡采购让 trio 尽快成型、被精炼及时消耗、箱位滚动释放。
                n_run = sum(1 for _i2, _f2, c2 in running_rel
                            if c2 in (0, 2))
                # 每类型本轮买量上限 = min(剩余总需求, readyish 需求) - 箱内
                # （dem 封顶保持 JIT：不囤超出「可炼前沿」的量）
                caps = {}
                for b in range(n):
                    if 1 in _cls_of(mclass, b) and dem.get(b, 0) > stock[b]:
                        caps[b] = min(need[b], dem[b]) - stock[b]
                bv = []
                while idle[1] > 0 \
                        and box_now + n_run + 1 <= box_cap + EPS:
                    progressed = False
                    for b, cap_left in caps.items():
                        if cap_left <= 0:
                            continue
                        if idle[1] <= 0 \
                                or box_now + n_run + 1 > box_cap + EPS:
                            break
                        need[b] -= 1
                        running_rel = running_rel + ((b, dur[b], 1),)
                        bv.append((b, 1, 1))
                        caps[b] = cap_left - 1
                        idle[1] -= 1
                        box_now += 1
                        progressed = True
                    if not progressed:
                        break
                if bv:
                    # ★ 买卡 op 必须落在**真实购买时刻**并贯穿 while 循环：
                    # 旧版只在分支点记 buy_path，而「无向量可开→推进时间」分支
                    # continue 时把它整体丢弃 → 所有买卡被记到下一次分支时刻
                    # （1★ 实测：t=0 的买卡全被记成 t=45s）→ 执行层时序全错、
                    # 素材晚到 → 空转+重解级联（DP 输贪心的隐藏根因之一）。
                    cur_path = cur_path + ((t, tuple(bv)),)
                    own_ops.append((t_rel, tuple(bv)))
                need = tuple(need)

            # ---- 分支：可行开工向量（按机器类限容量）----
            caps = [max(0, idle[0]), max(0, idle[1]), max(0, idle[2])]
            # _SearchAbort（deadline）直接向上抛到 solve 顶层；截断（max_vecs/
            # rec_budget）在 _vectors 内部消化、返回 partial + complete=False。
            vecs, complete = _vectors(items, caps, mclass, mats, stock, need,
                                      maximal_only, deadline=t0 + time_budget,
                                      max_vecs=max_vecs, rec_budget=rec_budget,
                                      prio=vec_prio,
                                      prio2=inst.get("tb"),
                                      prio2_first=bool(inst.get("tb_first")))
            if not complete:
                stats["capped"] += 1
            if not vecs:
                # 无可行开工向量：炉满/素材未到 → 必须推进到下一个完工
                # （买卡已在上面确定性执行，running_rel 非空即可推进）。
                # ⚠️ 旧版用 (idle[0]<=0 and idle[1]<=0) or not items 判定，买卡入状态后
                # S>0：主炉满+有可开工精炼+无买卡需求时 idle[1]>0 条件不成立 → 进分支 →
                # _vectors 返回空 → best_v=INF 假死路，父节点被迫回溯到「少开工」次优解。
                if not running_rel:
                    return INF, None    # 死路：无机在炉也无卡可开
                dt = min(f for _i, f, _c in running_rel)
                running_rel = tuple((i, f - dt, c) for i, f, c in running_rel)
                t = t + dt
                t_rel += dt
                continue
            best_v = INF
            best_suffix = None        # 相对本次调用入口（含跨迭代全部 own_ops）
            best_memo_suffix = None   # 相对本次迭代顶状态（仅本迭代 op）
            for vec in vecs:
                ns = list(stock)
                nn = list(need)
                # ★ 旧在炉作业必须带入分支（相对剩余时间不变）——买卡入状态重写时
                # 曾误写为 nr = []（只装新开工），旧作业完工永不到账 → makespan 被
                # 系统性低估、骨架 start 过早 → 执行层喂料未到 → 空转+重解。
                nr = [(i, f, c) for i, f, c in running_rel]
                for i, c, ci in vec:
                    for _ in range(c):
                        for j, kk in mats[i]:
                            ns[j] -= kk
                        nn[i] -= 1
                        nr.append((i, dur[i], ci))
                nr.sort()
                dt = min(f for _i, f, _c in nr)
                nrr = tuple((i, f - dt, c) for i, f, c in nr)
                v, child_suffix = dfs(tuple(nn), tuple(ns), nrr, t + dt,
                                      cur_path + ((t, vec),))
                if dt + v < best_v - 1e-9:
                    best_v = dt + v
                    # 返回链（相对调用入口）：本次调用累计 op（各自 t_rel 时刻）
                    # + 本向量（t_rel 时刻）+ 纯延时 dt + 子树后缀（子树入口
                    # = t_rel+dt，delta 天然对齐，无需平移子树）。
                    own = own_ops + [(t_rel, vec)]
                    head = chain_of(own)
                    if child_suffix is not None:
                        head = link(head, (dt, None, child_suffix))
                    best_suffix = head
                    # memo 链（相对本次迭代顶状态）：memo 键是迭代顶状态，故
                    # 后缀必须相对该时刻起算——本迭代买卡与本向量同在 t_rel
                    # （相对迭代顶 delta=0）。前序迭代的 op 混入会在 memo
                    # 命中时重放更早的买卡 → 时序错乱。
                    iter_own = [(0.0, vv) for _tr, vv in own_ops[iter_start:]]
                    iter_own.append((0.0, vec))
                    mhead = chain_of(iter_own)
                    if child_suffix is not None:
                        mhead = link(mhead, (dt, None, child_suffix))
                    best_memo_suffix = mhead
                if dt + v <= lb + 1e-9:
                    break               # 已达下界，无需再试其它向量
            # ★ INF 不落 memo：LB 剪枝产生的 INF 依赖当时 best["val"]（绝对
            # 时刻口径），不是状态的纯函数——存入会让后续以更紧 UB 访问同一
            # 状态时被幻影 INF 挡住而丢最优。结构性死路（无炉在炉/落箱超容）
            # 重探代价极小（立刻返回 INF），正确性优先。
            if best_v < INF:
                memo[key] = (best_v, best_memo_suffix)
            # ★ 返回值必须加 t_rel：while 循环内「收卡/推进到下一完工」耗掉的
            # 时间旧版没算进返回值（best_v 只相对本迭代顶）→ memo 值系统性
            # 偏小 → 幻影最优（1★ 实测 root=6.03h < 真实 7.03h，且违反自身
            # 总炉时下界 6.5h）。绝对值 t+fin 不受影响（旧代码答案正确纯属
            # 叶子赋值 best["val"]=t+fin 侥幸）。
            return t_rel + best_v, best_suffix

    # 初始状态：默认全空（从零开始）；传入 init_* 时从当前状态起算
    rg = run_norm
    st0 = tuple(init_stock) if init_stock is not None else tuple([0] * n)
    try:
        v, suffix = dfs(need0, st0, rg, 0.0, ())
    except _SearchAbort:
        v, suffix = INF, None
        stats["bounded"] = True
    stats["time"] = _t.perf_counter() - t0
    # 根 t=0，v 即绝对 makespan；后缀链给出可达它的完整调度表
    # （root 值 ≤ 任何已访叶子 makespan——memo 命中跳过叶子访问，故已访
    #  叶子调度可能次优；后缀链重建保证「返回值 = 返回调度」一致）。
    if v < INF:
        stats["opt"] = v
        if v < best["val"] - 1e-6:
            best["val"] = v
            best["sched"] = cells_to_sched(suffix)
            best["from"] = "DP"
        elif abs(v - best["val"]) > 1e-6:
            stats["note"] = f"root={v/3600:.2f}h best={best['val']/3600:.2f}h 不一致"
    else:
        stats["opt"] = best["val"]
        # 注意：根返 INF 只表示「搜不到优于 UB 的路径」（含被 LB 剪枝），
        # **不是**模型不可行；可行性由调用方模拟调度箱占用判定。
    return best["val"], best["sched"], stats
