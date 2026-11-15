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

# ---- 三空位状态条件（2026-11-15 用户定调）----------------------------------
# 「DP 按两箱合计没错，但状态要同时满足三个空位条件：总箱空位、换卡箱空位、
#  保险箱空位」。两池占用做成 (stock, 在途买卡, 在炉产物) 的派生量，不新增
#  状态维度；inst 不带 change_cap/store_cap 时 = 完全旧行为（只看合计）。
# 落箱物理规则：买来的底卡一定进换卡箱；炼成卡优先保险箱、满则溢到换卡箱。
# 所以「保险箱空位」不是独立硬闸，而是决定有多少炼成卡要溢到换卡箱 ——
# 三个条件一起构成 DP 状态转移的可行性判据，不是执行层的静态闸门。


def _pool_cfg(inst, n):
    cc = inst.get("change_cap")
    sc = inst.get("store_cap")
    if cc is None or sc is None:
        return None
    is_base = tuple(1 in _cls_of(inst["mclass"], i) for i in range(n))
    bs = tuple(int(x or 0) for x in (inst.get("base_store") or [0] * n))
    return (float(cc), float(sc), is_base, bs, int(n))


def _pool_chg(cfg, stock, buy_inflight=0, run_out=0):
    """换卡箱被计划卡占用的格数（含保险箱溢出 + 在途买卡 + 在炉产物预留）。"""
    if cfg is None:
        return 0.0
    _cc, sc, is_base, bs, n = cfg
    b = sum(float(stock[i]) for i in range(n) if is_base[i])
    r = sum(float(stock[i]) for i in range(n) if not is_base[i])
    bst = float(sum(bs))
    free_s = max(0.0, sc - bst)
    r_eff = r + float(run_out)
    overflow = max(0.0, r_eff - free_s)
    return (b - bst) + float(buy_inflight) + overflow


def _pool_sto(cfg, stock, run_out=0):
    if cfg is None:
        return 0.0
    _cc, sc, is_base, bs, n = cfg
    r = sum(float(stock[i]) for i in range(n) if not is_base[i])
    bst = float(sum(bs))
    return bst + min(r + float(run_out), max(0.0, sc - bst))


def _pool_ok(cfg, stock, buy_inflight=0, run_out=0):
    if cfg is None:
        return True
    POOL_EVAL[0] += 1
    cc, sc, _ib, _bs, _n = cfg
    return (_pool_chg(cfg, stock, buy_inflight, run_out) <= cc + EPS
            and _pool_sto(cfg, stock, run_out) <= sc + EPS)


POOL_REJECT = [0]        # 被三空位状态条件拒绝的分支数（证明约束真的生效）
POOL_EVAL = [0]          # _pool_ok 被求值次数（UB / dfs 都算）
POOL_MAX = [0.0, 0.0]    # 已接受状态里换卡箱 / 保险箱的最大占用


def sched_peak(inst, sched, init_occ=0.0):
    """调度表的箱占用峰值，与 dfs 内 occ 完全同口径（2026-11-09 字典序目标）。

    occ = 箱内实卡 + 在炉/在途产物占位。每个**开工** op 的净增量：
      · 买卡（mats 空）：在途 +1 → d = +1（完工时 stock+1 / 在途-1，occ 不变）
      · 精炼（喂料共 k 张）：扣 k 张、产物占位 +1 → d = 1 - k
    完工事件不改变 occ，故只需扫开工事件的前缀和取最大值。与
    `DPTickController._box_peak` 的「落箱 +1 / 开工 -k」是不同口径：
    那个只算箱内实卡（保守取同刻先落箱），这个把在炉产物也计入占位。
    稳定排序保证同刻 op 顺序与 `path_to_sched` / `cells_to_sched` 一致。
    """
    mats = inst["mats"]
    ev = sorted(((float(s), 1 - sum(k for _j, k in mats[int(i)]))
                 for i, s in sched), key=lambda x: x[0])
    occ = peak = float(init_occ)
    for _s, d in ev:
        occ += d
        if occ > peak:
            peak = occ
    return peak

# ---- 「底卡随时可买」口径（buy_free）的模块级开关 ------------------------
# solve() 每次入口按 inst["buy_free"] 覆写；`_chain_avail_rel` 等纯函数靠它
# 判断「底卡最早可得时刻 = 0 还是 LAG」。默认关 = 旧行为（底卡占 1 tick 物流）。
BASEFREE = False
BASESET = frozenset()

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


_CLS_PRE = None   # solve() 入口预计算的「类型→机器类元组」表
DIVE = False      # solve() 内的 dive 阶段标志：不剪枝、只沿首个向量单路径下潜
DIVE_W = 1        # dive 时每层保留的候选向量数（1 = 纯单路径）
DIVE_PICK = 0     # dive 起手偏移：multi-start 时换第 k 个候选向量开头


def _cls_of(mclass, i):
    """类型 i 的可用机器类（元组）。mclass[i] 可为 int（旧）或类表（新）。

    ★ 2026-11-15：solve 是同步单线程、不可重入，机器类表在一次求解内恒定，
      故入口预算成 `_CLS_PRE`。3 套实测该函数被调 165 万次（每次都重建
      单元素元组）——纯开销。
    """
    if _CLS_PRE is not None:
        return _CLS_PRE[i]
    v = mclass[i]
    return v if isinstance(v, (list, tuple)) else (v,)


def _has_steal(mclass, n, run_norm):
    """实例是否涉及偷炉（class-2 类型或在炉）→ 决定 steal_cap 缺省值。"""
    if any(2 in _cls_of(mclass, i) for i in range(n)):
        return True
    return any(c == 2 for _i, _f, c in run_norm)


def _collect_ready(mclass, n, need, running_types):
    """「整套非底卡全部产出」时刻 = 允许买收藏底卡的就绪条件。

    ★ 2026-11-15 用户定调：上交前一张收藏底卡都不该囤；求不出是条件没给全，
      不是该在执行层加闸。此函数是 **DP 状态的派生量**（need + 在炉），随
      求解时间轴变化，与上一版证伪的 inst 静态布尔 collect_ready 完全不同：
      顶层还在炉时 False，产出落箱、待炼需求归零后 True，need 自然能完成。
    """
    for i in range(n):
        if (0 in _cls_of(mclass, i) or 2 in _cls_of(mclass, i)) \
                and need[i] > 0:
            return False
    for i in running_types:
        if 0 in _cls_of(mclass, i) or 2 in _cls_of(mclass, i):
            return False
    return True


def _buy_demand(n, mclass, mats, stock, need, running_cnt,
                collect_ready=True):
    """各买卡类型的 lazy 需求（现在就该买的量）。

    readyish 精炼（need>0、除底卡外喂料全在箱）或 almost-readyish（缺的复合喂料
    在炉、必将到货）→ 其底卡缺口就是现在的购买需求。已无精炼需求时（只剩集卡册
    底卡）→ 需求 = need[b]（终局全买，防死锁），但**仅在 collect_ready 时**：
    收藏底卡是上交瞬间才需要的，顶层仍在炉就买 = 白占箱位数小时。
    collect_ready 缺省 True = 旧函数行为；整体开关在 inst["collect_gate"]
    （缺省启用，False = 逐位复现改动前）。
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
    if not ref_need and collect_ready:
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
  POOL = _pool_cfg(inst, len(inst["dur"]))
  # ★★ 2026-10-27 用户定调：「10 面额底卡可以认为本身就存在，因为可以随时买、
  #   也不用花 tick」。真机 `card_buy` code=0 即落换卡箱、仿真 `EP_BUY` 即
  #   `_add_to_box` —— 没有任何物理延迟。打开后买卡**不占 shop 时间槽**、
  #   当场进 stock（同一 t 的精炼就能用），只受箱容约束。
  buy_free = bool(inst.get("buy_free"))
  # ★ collect_gate 缺省开（生产 controller 无需改）：只影响「终局收藏底卡」
  #   的购买时刻，喂料底卡仍按 readyish 口径即时备好。False = 旧行为（A/B 用）。
  GATE = bool(inst.get("collect_gate", True))
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
      # 收藏底卡的就绪条件随列表调度状态变化：顶层/中间卡还在炉就一张不买，
      # 等产出全部落箱、非底卡需求归零后再买齐（need 到那时自然完成）。
      gate = (not GATE) or _collect_ready(
          mclass, n, need, [i2 for _f2, i2 in running])
      dem = _buy_demand(n, mclass, mats, stock, need, running_cnt,
                        collect_ready=gate)
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
      while (free_s and box_used + len(running) + 1 <= box_cap + EPS
             and _pool_ok(POOL, stock, len(buy_run), len(running))):
        progressed = False
        for b in list(caps):
          if caps[b] <= 0:
            continue
          if (not free_s or box_used + len(running) + 1 > box_cap + EPS
                  or not _pool_ok(POOL, stock, len(buy_run), len(running))):
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

    def maximal(cnt, used, st):
        # 容量快路径：三类机器全占满 → 任何 item（必属某一类）都加不进。
        # ★ 2026-11-15：旧版只判 caps[0]/caps[2]，漏掉买卡类 caps[1]，宽节点
        #   上等于关掉了这条快路径。补齐后多数满炉叶子不再逐 item 扫素材。
        if ncls >= 3:
            if used[0] >= caps[0] and used[1] >= caps[1] and used[2] >= caps[2]:
                return True
        else:
            if all(used[c] >= caps[c] for c in range(ncls)):
                return True
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

    def rec(p, used, cur, st, cnt):
        _ck[0] += 1
        if not (_ck[0] & 31):           # 每 32 步一次（perf_counter ~50ns）
            if deadline is not None and _t.perf_counter() > deadline:
                raise _SearchAbort
            if rec_budget is not None and _ck[0] > rec_budget:
                raise _CapAbort
        # ★ 2026-11-15 满槽快路径：槽位已全占满 → 剩余 item 一律只能放 0，
        #   直接当作极大叶子记账，不必再逐层下探到 p==ni。
        #   3 套（73 类型）实测：枚举深度从 73 降到「填满槽位的张数」，这是
        #   纯 DP 能在 2s 内搜到叶子的关键（旧版 3s 只走 759 个 dfs 节点、
        #   枚举函数被调 125 万次，时间全耗在深度优先穷举上）。
        _full = True
        for ci in range(ncls):
            if used[ci] < caps[ci]:
                _full = False
                break
        if _full:
            if cur:
                res.append(tuple(cur))
                if max_vecs is not None and len(res) >= max_vecs * 2:
                    raise _CapAbort
            return
        if p == ni:
            if cur and (not maximal_only or maximal(cnt, used, st)):
                res.append(tuple(cur))
                if max_vecs is not None and len(res) >= max_vecs * 2:
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
                if c:
                    cnt[i] = cnt.get(i, 0) + c
                rec(p + 1, used, cur, st, cnt)
                if c:
                    _v = cnt[i] - c
                    if _v:
                        cnt[i] = _v
                    else:
                        del cnt[i]
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
        rec(0, [0] * ncls, [], list(stock), {})
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


def _chain_avail_rel(j, k, stock, running_rel, seen, fins_by=None):
    """k 张 j 卡可用的最早增量时刻（相对剩余时间，可采纳下界）。

    running_rel 为 (type, 剩余, 类) 三元组；买卡类型（dur=LAG）无素材 →
    最早可得时刻 = LAG（假设现在就买，可采纳）。

    ★ buy_free（`BASEFREE`）：底卡随时可买且即时落箱 → 最早可得时刻 = 0，
      不是 LAG。旧版把这个 LAG 带进了 LB 链（买卡 45s 被当成真实等待）→
      下界在本口径下**高估**，会剪掉真正的最优分支。
    """
    if BASEFREE and j in BASESET:
        return 0.0
    if stock[j] >= k:
        return 0.0
    # ★ fins_by：按类型分桶的在炉剩余（每节点建一次，O(在炉数)），避免在
    #   73 类型 × 递归里反复线性扫 running_rel（3 套实测该函数累计 1.05s/3s）。
    fins = (fins_by.get(j) if fins_by is not None
            else [f for (j2, f, _c) in running_rel if j2 == j])
    if fins:
        if stock[j] + len(fins) >= k:
            return sorted(fins)[k - stock[j] - 1]
        return min(fins)
    if j in seen:
        return 0.0
    seen.add(j)
    return DUR[j] + max((_chain_avail_rel(m, kk, stock, running_rel, seen,
                                          fins_by)
                         for m, kk in MATS[j]), default=0.0)


def _cum_lb(need, stock, running_rel, t, mclass, steal_cap=0, fins_by=None):
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

    ★ 2026-11-15：`starts` 只依赖 (need, stock, running_rel)，与 sel/mach 无关
      → 一次算好按 sel 过滤复用。旧版两组 energetic 各算一遍（每节点 2×73 次
      递归链计算）；3 套实测 `_chain_avail_rel` 累计 1.05s / `energetic` 0.84s
      占 3s 预算的 6 成。
    """
    running_types = set(i for i, _f, _c in running_rel)
    starts_all = []
    for i in range(N):
        if need[i] <= 0 or i in running_types:
            continue
        s = max((_chain_avail_rel(j, kk, stock, running_rel, set(), fins_by)
                 for j, kk in MATS[i]), default=0.0)
        starts_all.append((i, s, DUR[i], need[i]))

    def energetic(sel, mach, run_cls):
        starts = [(s, d, nd) for i, s, d, nd in starts_all if sel(i)]
        rem_running = sum(f for _i, f, c in running_rel if c in run_cls)
        if not starts:
            return rem_running / mach
        # ★ 2026-11-15：原实现对每个发布时刻 tt 重扫全部 starts（O(k²)，
        #   73 类型 → 5300 次乘加/次调用，是 3 套 1ms/节点主因）。排序 +
        #   前缀和 O(k log k)，判据逐项等价（A 含 s<=tt、B 为其补集）。
        starts.sort()
        tot = 0.0
        for _s, d, nd in starts:
            tot += d * nd
        lb = (tot + rem_running) / mach
        for s, d, _nd in starts:
            if s + d > lb:
                lb = s + d
        A = rem_running
        if A / mach > lb:
            lb = A / mach
        if tot / mach > lb:
            lb = tot / mach
        pref = 0.0
        i = 0
        ns = len(starts)
        while i < ns:
            s = starts[i][0]
            while i < ns and starts[i][0] <= s:
                pref += starts[i][1] * starts[i][2]
                i += 1
            A = pref + rem_running
            B = tot - pref
            if A / mach > lb:
                lb = A / mach
            if s + B / mach > lb:
                lb = s + B / mach
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

    ⚠️ **不可重入、非线程安全**：实例数据是按约定写进模块级全局（`DUR/MATS/
    MACHINES/N/BASEFREE/BASESET`，见下方 `global` 声明），dfs 内只读。当前调用方
    全是同步单线程（`DPTickController._solve_plan` 在事件循环里直接调），
    所以不会交错；但**不要**把 solve 放进线程池/多进程共享模块、也不要在
    solve 执行中回调再解一次 —— 会导致两次求解互相污染。
    （2026-10-30 评估确认：全仓 0 处 threading/Lock/executor，故暂不改实例字段
    ——动 6 个全局的每个读点收益为零、回归面却覆盖整个求解器。）

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
    global DUR, MATS, MACHINES, N, BASEFREE, BASESET, _CLS_PRE
    global DIVE, DIVE_W, DIVE_PICK
    # 候选实验：有界搜索（避免 dfs 复活后打满 2s 预算）
    _nb = _os.environ.get("MC_DP_NODE_BUDGET")
    if _nb:
        node_budget = int(_nb)
    DUR = inst["dur"]
    MATS = inst["mats"]
    MACHINES = inst["machines"]
    N = inst["n"]
    _mc0 = inst["mclass"]
    _CLS_PRE = tuple(
        (v if isinstance(v, (list, tuple)) else (v,)) for v in _mc0)
    dur = DUR
    mats = MATS
    M = MACHINES
    n = N
    mclass = inst.get("mclass") or [0] * n
    S = int(inst.get("shop_cap") or 0)
    box_cap = inst.get("box_cap")
    if box_cap is None:
        box_cap = INF
    POOL_D = _pool_cfg(inst, n)
    need0 = tuple(inst["need0"])
    run_norm = _norm_running(init_running, n)
    sc = inst.get("steal_cap")
    sc = int(sc) if sc is not None else (1 if _has_steal(mclass, n, run_norm) else 0)
    # ★★ 2026-10-27 buy_free 口径必须同时进 dfs / LB，不能只改 UB：
    #   只改 `_ub_schedule` 会让 UB 变紧 → `best["val"]` 变小 → dfs 的
    #   `t + lb >= best["val"]` 剪枝更狠 → 交付解退化成 UB 的贪心列表调度。
    #   实测（3★ tid59）buy_free 只改 UB：ub0 从 7530s 级降到 …, 交付解里
    #   13% 的分支 v≥ub0（= 直接交了贪心）。故三处口径必须一起翻：
    #   ① dfs 买卡：不占 shop 槽、无 LAG、当场入箱；
    #   ② `_chain_avail_rel`：底卡最早可得 = 0（否则 LB 高估 → 剪掉最优）；
    #   ③ 执行层同 tick 买投（buy_sametick，由 controller 联动）。
    BASEFREE = bool(inst.get("buy_free"))
    BASESET = frozenset(i for i in range(n) if 1 in _cls_of(mclass, i))
    GATE = bool(inst.get("collect_gate", True))

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
    # ★★ 纯 DP（MC_DP_PURE=1）：交付解**只允许来自 dfs 自己搜出来的后缀链**，
    #   UB 降级为纯剪枝上界（`BOUND_V`），永远不进交付。
    #   旧行为的退化点（2026-11-15 实测 408 冷启 35/35 次交付=贪心表）：
    #     ① `best` 初值就是 `_ub_schedule` 那张表；
    #     ② 剪枝 `t + lb >= best["val"]` 把「与 UB 打平」的分支全剪掉；
    #   于是 dfs 只有严格快于 UB 才能交付，打平就等于交贪心。
    #   纯 DP 把两件事拆开：
    #     · 剪枝仍用 UB 上界（含打平，用 `>` 而非 `>=`），搜索空间不膨胀；
    #     · `best` 从 INF/None 起，叶子把 `<= BOUND` 的打平解也记下来，
    #       交付 dfs 链 → `from` 恒为 "DP"。
    PURE = _os.environ.get("MC_DP_PURE", "1") == "1"
    # 主动等待分支：允许「有可开工的也不开，推进到下一完工」。见 dfs 内注释。
    # ★ 2026-11-15 实测**默认关**：它能被选为最优（3 套 503 次），但最终
    #   makespan 不变（105.34），而搜索面翻倍让仿真更差（3套 106.19→107.84、
    #   紧箱 54.99→56.64）。保留开关 `MC_DP_WAIT=1` 供后续带前瞻的版本用。
    WAIT_BRANCH = _os.environ.get("MC_DP_WAIT", "0") == "1"
    PURE_TOL = float(_os.environ.get("MC_DP_PURE_TOL", "0") or 0)
    # 宽度自适应（仅 PURE）：宽节点的向量枚举成本 ∝ 活跃类型数，固定 96 在
    # 3 套（73 类型）上会把预算全花在枚举（30s 走 30 万节点却到不了叶子）；
    # 固定小值在 1★（10 类型）上又浪费搜索面。按活跃类型数线性倒数取上限。
    if PURE and _os.environ.get("MC_DP_VEC_AUTO", "1") == "1":
        _kact = sum(1 for _x in inst["need0"] if _x > 0)
        _mv_cap = max(8, min(96, int(1200 / max(1, _kact))))
        if max_vecs > _mv_cap:
            max_vecs = _mv_cap
        _rb_cap = max(4000, min(50000, int(300000 / max(1, _kact))))
        if rec_budget > _rb_cap:
            rec_budget = _rb_cap
    BOUND_V = [ub + PURE_TOL if PURE else ub]
    if PURE:
        best = {"val": INF, "sched": None, "from": None}
    else:
        best = {"val": ub, "sched": list(ub_sched), "from": "UB"}
    stats = {"nodes": 0, "time": 0.0, "bounded": False, "ub0": ub,
             "memo_hits": 0, "opt": None, "capped": 0, "pure": PURE}
    _occ0 = float(sum(init_stock) if init_stock is not None else 0) + len(run_norm)
    stats["ub_peak0"] = sched_peak(inst, list(ub_sched), _occ0)
    # ★ 候选（默认关 = 与生产逐位一致）：让 dfs 真正搜索「同 makespan」的
    #   等价解空间，而不是被 `t+lb >= best` 一刀切回退到 UB 贪心初解。
    #   有界：EQ_NODE_CAP 计「原本会被剪掉的节点」，耗尽后立刻恢复旧剪枝。
    EQ_SEARCH = _os.environ.get("MC_LEX_EQ_SEARCH", "0") == "1"
    # dfs 的 lazy 买卡逐张过三空位约束（与 UB 买卡同口径）。
    # ★★ 2026-11-15 默认开启（用户定调「把条件给全」）：旧默认 0 时 dfs
    #   只按合计 box_cap 买卡，而 dfs 入口又用 `_pool_ok` 判三空位 →
    #   一次买爆换卡箱后每个分支都在入口被判 INF（5★/3套 冷启 6 节点全灭、
    #   纯 DP 交付空计划 → 仿真 3000 tick 零动作）。买卡与开工/落箱必须
    #   同一套条件，才谈得上「条件给全」。
    #   `MC_DP_POOL_FIX=0` 可复现旧行为（A/B 用）。
    POOL_FIX = _os.environ.get("MC_DP_POOL_FIX", "1") == "1"
    # 等价 makespan/peak 时交付 dfs 自己搜出来的后缀，而不是 UB 贪心初解。
    PREFER_DP = _os.environ.get("MC_DP_PREFER", "0") == "1"
    # 允许 dfs 比 UB 慢的容差（秒）：两模型买卡 LAG 口径差 45s。
    PREFER_TOL = float(_os.environ.get("MC_DP_EQ_TOL", "0") or 0)
    EQ_LEFT = [int(_os.environ.get("MC_LEX_EQ_NODES", "300") or 300)]
    DIAG = {"lb_prune": 0, "eq_allowed": 0, "eq_break": 0,
            "vec": 0, "leaf": 0, "inf_pool": 0, "inf_box": 0,
            "inf_dead": 0}
    stats["eq_search"] = EQ_SEARCH
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
    # ★★ 2026-11-09 字典序目标（用户 11-09 定调 / 11-09 模型复核结论）：
    #   31 个 op 存在大量 makespan 相同的排列，DP 只按 makespan 择优 → 挑中了
    #   库存峰值最大的那个，真机 200 格被铺开的 40 面中间卡吃满。改为
    #   min makespan → min 峰值库存：makespan 严格优先，仅在打平时比峰值。
    #   「箱位压力闸门」（箱紧才上溯）已被模型数据否掉（K=3 箱最紧时无条件
    #   上溯反而既快又省箱），执行层补丁历史上四次无效 → 只在求解器层改。
    # ⚠️ 2026-11-09 实测限制：真实承诺点实例上「同 makespan 异构」一次都没
    #   触发（lazy 买卡规则确定性、不分支 → 买卡时序不是决策变量，精炼顺序
    #   的等价解空间极小）。当前收益主要来自「多枚举几个等价向量」改善了
    #   搜索质量（890 仿真峰值 23→20、15.01→15.00h），不是字典序第二级
    #   本身生效。要让峰值目标真正生效，须把买卡量/时序纳入分支决策。
    lex_peak = _os.environ.get("MC_LEX_PEAK", "1") == "1"

    def occ_delta(i):
        """单个开工 op 的箱占用净增量（见 sched_peak）。"""
        return 1 - sum(k for _j, k in mats[int(i)])

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

    def _ready_items(stock_v, need_v):
        """素材齐的可开工精炼 [(type, 可开数上限)]。

        ★ buy_free 下买卡当场入箱 → 必须在买卡之后**重新算一次**，否则
          「买完当拍就能开工」的炉会白等下一迭代（= 又变成买卡占一拍）。
        """
        out_i = []
        for i3 in range(n):
            if need_v[i3] <= 0:
                continue
            if 0 in _cls_of(mclass, i3):
                if all(stock_v[j] >= kk for j, kk in mats[i3]):
                    lim = need_v[i3]
                    for j, kk in mats[i3]:
                        lim = min(lim, stock_v[j] // kk)
                    if lim > 0:
                        out_i.append((i3, lim))
        return out_i

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
        # ★ 三空位状态条件：在每次 dfs 入口统一判，覆盖买卡 / 收卡 / 精炼
        #   开工后的所有状态转移；违反「总箱（原有 box_cap 不变量）+
        #   换卡箱 + 保险箱」三条件之一的分支直接不可行，不再靠执行层兜底。
        if POOL_D is not None:
            _bi = sum(1 for _i, _f, _c in running_rel if _c == 1)
            _ro = sum(1 for _i, _f, _c in running_rel if _c in (0, 2))
            if not _pool_ok(POOL_D, stock, _bi, _ro):
                POOL_REJECT[0] += 1
                DIAG["inf_pool"] += 1
                return INF, None, INF
            POOL_MAX[0] = max(POOL_MAX[0],
                              _pool_chg(POOL_D, stock, _bi, _ro))
            POOL_MAX[1] = max(POOL_MAX[1], _pool_sto(POOL_D, stock, _ro))
        cur_path = path          # 本次调用累计的绝对时间 op 路径（含买卡）
        t_rel = 0.0              # 相对本次入口已过去的时间
        own_ops = []             # 本次调用产生的 op [(t_rel, vec)]（买卡 bundle）
        # 字典序第二级：本次调用沿途（公共前缀）的箱占用轨迹。occ 由状态唯一
        # 决定（sum(stock)+在炉数），故 memo key 相同则入口 occ 相同，后缀峰值
        # 是状态的纯函数 → 字典序具备最优子结构。
        cur_occ = float(sum(stock) + len(running_rel))
        run_peak = cur_occ
        while True:
            # ---- 叶子：全部开工完 → 额外耗时 = 在炉最晚剩余 ----
            if not any(need):
                DIAG["leaf"] += 1
                fin = max([0.0] + [f for _i, f, _c in running_rel])
                if PURE:
                    # 打平也算解：只要在剪枝上界内，就是 dfs 走出来的完整调度
                    if t + fin <= BOUND_V[0] + 1e-9 \
                            and t + fin < best["val"] - 1e-9:
                        best["val"] = t + fin
                        best["sched"] = path_to_sched(cur_path)
                        best["from"] = "DP"
                        DIAG["leaf"] += 1
                elif t + fin < best["val"] - 1e-9:
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
                return t_rel + fin, chain_of(own_ops), run_peak

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
                # 兼容外部传入的旧二元组 memo（跨 tick 热启动一般不跨版本）
                _hp = max(run_peak, hit[2] if len(hit) > 2 else run_peak)
                if own_head is None:
                    return t_rel + hit[0], hit[1], _hp
                delay = t_rel - own_ops[-1][0]
                if delay > EPS:
                    return t_rel + hit[0], link(own_head, (delay, None, hit[1])), _hp
                return t_rel + hit[0], link(own_head, hit[1]), _hp
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
                    DIAG["inf_box"] += 1
                    return INF, None, INF    # 落箱超容 → 该分支不可行（DP 改走 JIT）
                stock = tuple(ns)
                running_rel = tuple(nr)
                continue

            # ---- 可启动：精炼（素材齐）；买卡走下方 lazy 确定性规则 ----
            running_cnt = {}
            for i2, _f, _c in running_rel:
                running_cnt[i2] = running_cnt.get(i2, 0) + 1
            # 终局收藏底卡的就绪条件由本次 dfs 状态派生（need + 在炉产物）；
            # 未就绪时 dem 不含收藏需求，dfs 会推进到下一个完工再重判，
            # 不会因「少买」判死，也不需要执行层任何补偿闸。
            gate = (not GATE) or _collect_ready(
                mclass, n, need,
                [i2 for i2, _f2, c2 in running_rel if c2 in (0, 2)])
            dem = _buy_demand(n, mclass, mats, stock, need, running_cnt,
                              collect_ready=gate)
            items = _ready_items(stock, need)
            idle = [0, 0, 0]
            for _i2, _f2, c2 in running_rel:
                if 0 <= c2 < 3:
                    idle[c2] += 1
            idle[0] = M - idle[0]
            idle[1] = S - idle[1]
            idle[2] = sc - idle[2]

            # ---- 可采纳下界（相对额外耗时）----
            lb = max([0.0] + [f for _i, f, _c in running_rel])
            running_types = set(i for i, _f, _c in running_rel)
            _fins_by = {}
            for _i, _f, _c in running_rel:
                _fins_by.setdefault(_i, []).append(_f)
            # ★★ 2026-11-15 dive 阶段（`DIVE`）：不剪枝 → 下界一次都不用算。
            #   3 套实测下界链 + `_cum_lb` 占单节点成本的 6~7 成；dive 只走
            #   单路径（547 层），跳过它们后整个下潜在 0.1s 量级完成。
            if not DIVE:
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
                for i in range(n):
                    if need[i] <= 0 or i in running_types:
                        continue
                    ch = dur[i] + max(
                        (_chain_avail_rel(j, kk, stock, running_rel, set(),
                                          _fins_by)
                         for j, kk in mats[i]), default=0.0)
                    if ch > lb:
                        lb = ch
                cum = _cum_lb(need, stock, running_rel, t, mclass, sc, _fins_by)
                if cum > lb:
                    lb = cum
            # 绝对下界 = t + lb；超过已知最优则剪枝（仅当前节点放弃）
            # ★ 纯 DP（MC_DP_PURE）：上界取 `BOUND_V`（UB+容差）且用 `>` ——
            #   允许「与 UB 打平」的分支继续走。否则最优恰好等于 UB 时会把
            #   所有分支剪光（根返 INF → 只能退回交付贪心表）。
            # ★ 候选 EQ_SEARCH：>= 把「恰好等于已知最优 makespan」的分支也全剪了 →
            #   字典序第二级（peak）根本没机会被优化，交付解永远是 UB 初解。
            #   放开这些分支，但用 EQ_LEFT 硬限额，耗尽即恢复旧剪枝。
            if PURE:
                if not DIVE and t + lb > BOUND_V[0] - 1e-9:
                    DIAG["lb_prune"] += 1
                    return INF, None, INF
            elif t + lb >= best["val"] - 1e-9:
                DIAG["lb_prune"] += 1
                if EQ_SEARCH and EQ_LEFT[0] > 0:
                    EQ_LEFT[0] -= 1
                    DIAG["eq_allowed"] += 1
                else:
                    return INF, None, INF

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
            # ★★ buy_free：买卡不占 shop 槽（容量视为无限，真实上限是箱位/金币），
            #   且**当场入箱**（不建 45s 的 class-1 任务）。这样「买卡」不再是排期
            #   节点，solver 的决策变量只剩精炼顺序 —— 正是用户要的口径。
            _shop = 10 ** 9 if BASEFREE else S
            if _shop > 0 and (BASEFREE or idle[1] > 0):
                need = list(need)
                _sl = list(stock) if BASEFREE else None
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
                while (BASEFREE or idle[1] > 0) \
                        and box_now + n_run + 1 <= box_cap + EPS:
                    progressed = False
                    for b, cap_left in caps.items():
                        if cap_left <= 0:
                            continue
                        if (not BASEFREE and idle[1] <= 0) \
                                or box_now + n_run + 1 > box_cap + EPS:
                            break
                        # ★ 三空位一致性（让 dfs 真能搜的关键）：UB 的 lazy
                        #   买卡逐张过 _pool_ok，dfs 旧版只看合计 box_cap →
                        #   一次把换卡箱买爆，随后每个分支都在 dfs 入口被
                        #   _pool_ok 判 INF（408 冷启实测 96/96 分支全灭）。
                        #   这里按「再买 1 张」的投影状态逐张校验，两个模型
                        #   的可行域才一致，dfs 才有可行后缀可搜。
                        if POOL_FIX and POOL_D is not None:
                            _bi = sum(1 for _x, _f2, c2 in running_rel if c2 == 1)
                            _ro = sum(1 for _x, _f2, c2 in running_rel
                                      if c2 in (0, 2))
                            if not _pool_ok(POOL_D, stock, _bi + 1, _ro):
                                break
                        need[b] -= 1
                        if BASEFREE:
                            # ★★ 2026-10-27 修正（多套实测）：买卡**不占 shop 槽**
                            #   （不排队、不扣 idle[1]，这是「买卡不是瓶颈」的本义），
                            #   但仍要有 **1 个时间单位到货延迟** —— 旧版直接
                            #   `_sl[b] += 1` 当场入箱 + `_ready_items` 立刻开工，
                            #   等于假设「买完同一决策点就能投炉」。现实里引擎买卡
                            #   后要重读卡箱、下一 tick 才投得出去（同 tick 买投已
                            #   实测三档全负）。
                            #   骨架因此比现实乐观 45s/次，多套场景买卡上百次 → 漂移
                            #   累积 1~3h，执行层追不上 → 空转（85 两轮/178 三轮/
                            #   290 两轮实测均慢 1~3h）。
                            running_rel = running_rel + ((b, dur[b], 1),)
                        else:
                            running_rel = running_rel + ((b, dur[b], 1),)
                            idle[1] -= 1
                        bv.append((b, 1, 1))
                        # 字典序第二级：逐张记买卡的箱占用（在途 +1）
                        cur_occ += occ_delta(b)
                        if cur_occ > run_peak:
                            run_peak = cur_occ
                        caps[b] = cap_left - 1
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
                # ★ 2026-10-27：BASEFREE 不再「当场入箱」——买卡作为 dur=LAG 的
                #   class-1 任务在 running_rel 里滚动，到点才回 stock（物理正确：
                #   引擎买完要下一 tick 重读卡箱才投得出去）。故此处不做
                #   `_sl`/`_ready_items` 的即时结算。
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
                    DIAG["inf_dead"] += 1
                    return INF, None, INF    # 死路：无机在炉也无卡可开
                dt = min(f for _i, f, _c in running_rel)
                running_rel = tuple((i, f - dt, c) for i, f, c in running_rel)
                t = t + dt
                t_rel += dt
                continue
            best_v = INF
            best_suffix = None        # 相对本次调用入口（含跨迭代全部 own_ops）
            best_memo_suffix = None   # 相对本次迭代顶状态（仅本迭代 op）
            best_peak = INF           # 字典序第二级：所选后缀的箱占用峰值
            # 达到 makespan 下界后，旧版立刻 break；字典序需要再看几个等价
            # 向量找更省箱的排列。但**必须限量**：2026-11-09 实测全量枚举让
            # 890 的第 2 次求解 0.106s→2s（打满预算），骨架/重解轨迹全变，
            # 仿真反而 +1.87h。MC_LEX_EQ_VEC 控制等价解预算（0 = 仅下界即停）。
            # 扫描实测（8 档 A/B）：K=0 无收益；K=2 与 K=6 同为 890 峰值 −3、
            # 15.01→15.00h；K=16 峰值 −5 但求解打满预算、仿真 +1.87h。
            # 取 2 是保守值：收益相同、枚举量最小、对未测真机场景风险最低。
            if not lex_peak:
                eq_left = 0
            elif EQ_SEARCH:
                # 达下界后不再只看 2 个向量；上限 = 该节点向量数上限，
                # 真正让 dfs 在同一 makespan 的解集合里往下走。
                eq_left = int(_os.environ.get("MC_LEX_EQ_VEC",
                                             str(max_vecs)) or max_vecs)
            else:
                eq_left = int(_os.environ.get("MC_LEX_EQ_VEC", "2") or 0)
            lb_seen = False
            # ★ dive：只沿排序后的前 DIVE_W 个向量一路到底（单路径下潜）。
            if DIVE and len(vecs) > DIVE_W:
                _pk = DIVE_PICK
                vecs = vecs[_pk:_pk + DIVE_W] or vecs[:DIVE_W]
            for vec in vecs:
                DIAG["vec"] += 1
                if lb_seen and eq_left <= 0:
                    DIAG["eq_break"] += 1
                    break
                # 本向量开工事件的 occ 轨迹（逐张、同刻保序 → 与 sched_peak 同口径）
                vec_occ = cur_occ
                vec_peak = cur_occ
                for i, c, _ci in vec:
                    d = occ_delta(i)
                    for _ in range(c):
                        vec_occ += d
                        if vec_occ > vec_peak:
                            vec_peak = vec_occ
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
                v, child_suffix, child_peak = dfs(tuple(nn), tuple(ns), nrr, t + dt,
                                                  cur_path + ((t, vec),))
                cand_v = dt + v
                # 已知 makespan 下界达成后，更差的候选不可能改进字典序 → 跳过
                if lb_seen and cand_v > best_v + 1e-9:
                    eq_left -= 1
                    continue
                cand_peak = max(run_peak, vec_peak, child_peak)
                # 字典序：makespan 严格优先；打平时才比峰值库存。
                # 关闭 lex（MC_LEX_PEAK=0）时退化为原来的纯 makespan 比较。
                _better = (cand_v < best_v - 1e-9
                           or (lex_peak and abs(cand_v - best_v) <= 1e-9
                               and cand_peak < best_peak - 1e-9))
                if _better:
                    best_v = dt + v
                    best_peak = cand_peak
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
                # 达到 makespan 下界 → 旧版立刻 break；字典序开启后按
                # eq_left 预算再看几个等价向量（同 makespan 里挑更省箱的）。
                if cand_v <= lb + 1e-9:
                    lb_seen = True
                    if not lex_peak:
                        break
                    eq_left -= 1
                elif lb_seen:
                    eq_left -= 1
            # ★★ 主动等待分支（2026-11-15，`MC_DP_WAIT`，默认开）：
            #   `maximal_only` 的旧论证「能开却不开不会更优」只在**单机器容量**
            #   下成立；本问题炉位数固定 + 素材依赖 → 开一张低价值卡会占住炉位，
            #   让关键卡晚开工，等待更优。缺这个维度时 3 套（73 类型/547 op）
            #   实测：UB 上界下 10s / 2.6 万节点 `leaf=0`，上界必须放宽 +8h 才
            #   能走通 —— 即 dfs 的可行域根本不含 UB 的那条 100.75h 路径，
            #   最好只能到 105.34h。加上「不开新炉、推进到下一完工」这一支后
            #   dfs 才能表达「等料」。
            if WAIT_BRANCH and running_rel and not DIVE:
                _dw = min(f for _i, f, _c in running_rel)
                _nr = tuple((i, f - _dw, c) for i, f, c in running_rel)
                # 等待段的箱占用不变（无新产物入箱），occ 只随收卡增加
                _wv, _wsfx, _wpk = dfs(need, stock, _nr, t + _dw, cur_path)
                if _wv < INF:
                    _cv = _dw + _wv
                    _cpk = max(run_peak, _wpk)
                    if (_cv < best_v - 1e-9
                            or (lex_peak and abs(_cv - best_v) <= 1e-9
                                and _cpk < best_peak - 1e-9)):
                        best_v = _cv
                        best_peak = _cpk
                        own = own_ops
                        head = chain_of(own)
                        if _wsfx is not None:
                            head = link(head, (_dw, None, _wsfx))
                        best_suffix = head
                        iter_own = [(0.0, vv) for _tr, vv in own_ops[iter_start:]]
                        mhead = chain_of(iter_own)
                        if _wsfx is not None:
                            mhead = link(mhead, (_dw, None, _wsfx))
                        best_memo_suffix = mhead
                        DIAG["wait_best"] = DIAG.get("wait_best", 0) + 1
            # ★ INF 不落 memo：LB 剪枝产生的 INF 依赖当时 best["val"]（绝对
            # 时刻口径），不是状态的纯函数——存入会让后续以更紧 UB 访问同一
            # 状态时被幻影 INF 挡住而丢最优。结构性死路（无炉在炉/落箱超容）
            # 重探代价极小（立刻返回 INF），正确性优先。
            if best_v < INF:
                memo[key] = (best_v, best_memo_suffix, best_peak)
            # ★ 返回值必须加 t_rel：while 循环内「收卡/推进到下一完工」耗掉的
            # 时间旧版没算进返回值（best_v 只相对本迭代顶）→ memo 值系统性
            # 偏小 → 幻影最优（1★ 实测 root=6.03h < 真实 7.03h，且违反自身
            # 总炉时下界 6.5h）。绝对值 t+fin 不受影响（旧代码答案正确纯属
            # 叶子赋值 best["val"]=t+fin 侥幸）。
            return t_rel + best_v, best_suffix, best_peak

    # 初始状态：默认全空（从零开始）；传入 init_* 时从当前状态起算
    rg = run_norm
    st0 = tuple(init_stock) if init_stock is not None else tuple([0] * n)
    init_occ0 = float(sum(st0) + len(rg))
    # ---- 纯 DP 阶段一：dive（无剪枝单路径下潜，取 dfs 自己的可行解）----
    # ★★ 根因（2026-11-15 实证）：PURE 把 UB 当**硬**剪枝上界，而 3 套（73 类型/
    #   547 op）任何走偏的路径在深层下界都会超过 UB → 每条路径都被剪断。
    #   实测 30s / 30 万节点 `leaf=0`、`lb_prune=26221`、`from=None`：
    #   没有 incumbent 时「一条全程满足上界的路径」并不能保证存在，
    #   dfs 结构上就到不了叶子（这不是预算不足，是剪枝无解）。
    #   修法 = 标准 B&B 的 initial incumbent：先无剪枝地沿 prio 首个向量
    #   一路到底，得到 dfs 自己构造的完整调度；再拿它当上界做 B&B。
    #   交付物仍是 dfs 链（不是 UB 贪心表），符合「纯 DP」定调。
    if PURE:
        _mv0, _rb0 = max_vecs, rec_budget
        _memo0 = memo
        _bv0 = BOUND_V[0]
        _dw = max(1, int(_os.environ.get("MC_DP_PURE_DIVE_W", "1") or 1))
        # multi-start：dive 极快（547 层实测 ~0.1s），换不同的起手向量重复
        # 几次取最优，显著抬高 incumbent 质量（单次 dive 无回溯，起点决定一切）。
        _starts = int(_os.environ.get("MC_DP_PURE_DIVE_STARTS", "4") or 1)
        if time_budget < 0.5:
            _starts = 1              # warm 重解（0.1s 级）只够一站
        _dmv = max(2, int(_os.environ.get("MC_DP_PURE_DIVE_MV", "8") or 8))
        _drb = max(500, int(_os.environ.get("MC_DP_PURE_DIVE_RB", "4000") or 4000))
        try:
            DIVE = True
            DIVE_W = _dw
            for _k in range(max(1, _starts)):
                DIVE_PICK = _k
                max_vecs, rec_budget = _dmv, _drb
                memo = {}
                BOUND_V[0] = INF      # dive 不剪枝
                try:
                    dfs(need0, st0, rg, 0.0, ())
                except _SearchAbort:
                    stats["bounded"] = True
                    break
                stats["dive_starts"] = _k + 1
        finally:
            memo = _memo0
            max_vecs, rec_budget = _mv0, _rb0
            DIVE = False
            DIVE_W = 1
            DIVE_PICK = 0
        if best["val"] < INF:
            stats["dive_v"] = best["val"]
            stats["dive_from"] = best["from"]
            # 上界换成 dive 的可行解：保证这条路径自身永不被剪（PURE 用 `>`，
            # 允许打平），第二遍就一定走得到叶子，并在其内继续改进。
            # `MC_DP_DIVE_BOUND=0` 保持 UB 上界（诊断 lb 是否高估用）。
            if _os.environ.get("MC_DP_DIVE_BOUND", "1") == "1":
                BOUND_V[0] = best["val"]
            else:
                BOUND_V[0] = min(_bv0, best["val"])
        else:
            BOUND_V[0] = _bv0
    try:
        v, suffix, root_peak = dfs(need0, st0, rg, 0.0, ())
    except _SearchAbort:
        v, suffix, root_peak = INF, None, INF
        stats["bounded"] = True
    # ★ 纯 DP 兜底：UB 上界内一条完整路径都没走通（LB 偏紧 / UB 不可达）时，
    #   放开上界重搜一遍 —— 宁可慢、宁可爆预算，也不交付贪心表。
    #   `MC_DP_PURE_RETRY=0` 可关；第二遍节点预算 `MC_DP_PURE_RETRY_NODES`。
    if PURE and v >= INF and _os.environ.get("MC_DP_PURE_RETRY", "1") == "1":
        BOUND_V[0] = INF
        stats["pure_retry"] = True
        _nb0 = node_budget
        # ★ 时间预算必须给 retry 独立一份：第一遍撞满 time_budget 后
        #   `perf_counter() - t0 > time_budget` 恒真 → retry 一进 dfs 就 abort
        #   （实测 2/41 次「无解」就是这么来的）。这里是放宽上限而非改 t0，
        #   保证 stats["time"] 统计口径不变。
        _prior = _t.perf_counter() - t0
        time_budget = _prior + float(
            _os.environ.get("MC_DP_PURE_RETRY_TIME", "8.0") or 8.0)
        node_budget = max(
            node_budget,
            int(_os.environ.get("MC_DP_PURE_RETRY_NODES", "200000") or 0))
        # ★★ 2026-11-15 retry 必须**收窄宽度**（用户定调「不要退化」的落地）：
        #   默认 max_vecs=96 时宽节点向量枚举把预算全花在浅层 —— 3套(73 类型/
        #   547 op) 实测 40s 走 13.7 万节点、`capped=1480`，**连一个叶子都没
        #   到达**（`from=None` → 纯 DP 交付空计划 → 仿真 375h/0 轮报废）。
        #   同一实例 max_vecs=2/8/32 在 10s 内全部到达叶子并交付完整解
        #   （106.80/105.34/105.97h）。分级加宽在 2s 冷启预算内来不及（mv=2
        #   也要 ~5s 才到叶子），所以只在「一遍全灭」时用窄宽度重搜 —— 交付的
        #   仍是 dfs 自己的解，不引入贪心表。
        _mv0, _rb0 = max_vecs, rec_budget
        max_vecs = int(_os.environ.get("MC_DP_PURE_RETRY_MV", "8") or 8)
        rec_budget = int(_os.environ.get("MC_DP_PURE_RETRY_RB", "20000") or 20000)
        try:
            # memo 里可能存了「上界紧时」的剪枝结果，换上界后必须重探
            if isinstance(memo, dict):
                memo.clear()
        except Exception:
            pass
        try:
            v, suffix, root_peak = dfs(need0, st0, rg, 0.0, ())
            stats["retry_mv"] = max_vecs
        except _SearchAbort:
            v, suffix, root_peak = INF, None, INF
            stats["bounded"] = True
        finally:
            max_vecs, rec_budget = _mv0, _rb0
        node_budget = _nb0
    stats["time"] = _t.perf_counter() - t0
    # 根 t=0，v 即绝对 makespan；后缀链给出可达它的完整调度表
    # （root 值 ≤ 任何已访叶子 makespan——memo 命中跳过叶子访问，故已访
    #  叶子调度可能次优；后缀链重建保证「返回值 = 返回调度」一致）。
    if v < INF and PURE:
        # 纯 DP：交付的必须是从这条后缀链重建的调度，`from` 恒为 "DP"
        best["val"] = v
        best["sched"] = cells_to_sched(suffix)
        best["from"] = "DP"
        stats["opt"] = v
    elif v < INF:
        stats["opt"] = v
        if v < best["val"] - 1e-6:
            best["val"] = v
            best["sched"] = cells_to_sched(suffix)
            best["from"] = "DP"
        elif lex_peak and abs(v - best["val"]) <= 1e-6:
            # best["sched"] 可能来自 UB 或某个已访叶子；DP 后缀链是字典序
            # 最优（同 makespan 里峰值最小），打平时用它换掉更费箱的解。
            if root_peak < sched_peak(inst, best["sched"], init_occ0) - 1e-9:
                best["sched"] = cells_to_sched(suffix)
                best["from"] = "DP"
                stats["lex_peak_swap"] = True
        elif abs(v - best["val"]) > 1e-6:
            stats["note"] = f"root={v/3600:.2f}h best={best['val']/3600:.2f}h 不一致"
        # ★ 候选：同 makespan、同峰值时，交付「dfs 真搜出来的解」而不是
        #   `_ub_schedule` 的贪心初解（后者按类型序连排同卡，是 408
        #   150 面晚开的形状来源）。只在 EQ_SEARCH 已放开等价空间时生效。
        if PREFER_DP and best["from"] == "UB" \
                and 0 <= v - best["val"] <= PREFER_TOL + 1e-6 \
                and root_peak <= stats["ub_peak0"] + 1e-9:
            best["sched"] = cells_to_sched(suffix)
            best["from"] = "DP"
            stats["prefer_dp"] = True
    else:
        stats["opt"] = best["val"] if best["val"] < INF else None
        # 注意：根返 INF 只表示「搜不到优于 UB 的路径」（含被 LB 剪枝），
        # **不是**模型不可行；可行性由调用方模拟调度箱占用判定。
        if PURE and best["sched"] is not None:
            # 根链没走通（abort 时回卷），但途中已有叶子记下完整调度：
            # 交付它，且明确标成 DP（旧代码会留 from=None，调用方无法区分）。
            best["from"] = "DP_LEAF"
            stats["pure_leaf"] = True
        elif PURE and best["sched"] is None:
            # 纯 DP 且两遍搜索都没走通任何完整路径：如实报无解，不交贪心表。
            stats["no_solution"] = True
            stats["peak"] = None
            stats["lex_peak"] = lex_peak
            stats["diag"] = dict(DIAG)
            stats["eq_left"] = EQ_LEFT[0]
            stats["from"] = None
            stats["root_peak"] = None
            stats["root_v"] = None
            stats["best_v"] = None
            stats["note"] = "pure_no_solution"
            return INF, [], stats
    stats["peak"] = sched_peak(inst, best["sched"], init_occ0)
    stats["lex_peak"] = lex_peak
    stats["diag"] = dict(DIAG)
    stats["eq_left"] = EQ_LEFT[0]
    stats["from"] = best["from"]
    stats["root_peak"] = root_peak if v < INF else None
    stats["root_v"] = v if v < INF else None
    stats["best_v"] = best["val"]
    stats["note"] = stats.get("note")
    return best["val"], best["sched"], stats
