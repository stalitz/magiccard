"""DP 控制器 v2：骨架（skeleton）+ 有限偏差执行（2026-10-24 重构）。

架构（用户定调，原话）：
  高星长前置 → DP 解出来的是一个「大致顺序」（骨架）
  物流延迟 / 人干预 → 在这个骨架内做局部重分配（反应式微调）
  不重解全局 → 不 churn
  骨架过时了 → 触发重解（低频）
  = branch-and-bound with limited discrepancy，不是「每 tick 全量重解 vs
    一次性静态排产」的二选一。

对应实现（对照旧版每 tick 全量重解的结构性缺陷逐条修）：
  1) 买卡入状态：底卡是 shop 机器类上的买卡类型（dur=LAG=1 tick 物流），DP 选卡
     时就知道「买这张底料要 1 tick」→ 骨架里的买卡动作天然排在消费者前 LAG。
     旧版 _jit_prebuy 是执行层外挂（DP 不知道物流），已删除。
  2) 跨 tick 状态继承：解算产出的是**带 start 时间的骨架动作表**，跨 tick 缓存
     （_plan），下一 tick 只做列表调度（微秒级），不再把「未来 20 tick 的计划」
     整个丢掉重算。旧版每 tick 重解 = rolling horizon 无 warm start。
  3) 骨架/微调分层：承诺点才解算（首次 / active 变 / 轮界 / 计划耗尽 / 偏差超限 /
     头动作连续被卡），解算带 init_stock + init_running（机器感知，start 诚实）；
     tick 内按「start<=elapsed 到期门 + 喂料在箱 + 炉空闲」列表调度。
  4) 有限偏差（局部重分配）：物流失败（缺底卡且骨架无到期买卡）→ emergency buy；
     头动作被卡时在 scan_window 内向后扫描可执行动作，但**绝不消费被阻塞动作
     的在箱喂料**（不偷关键路素材）；偏差计数超限 → 判骨架过时 → 重解。
  5) 低频重解 → 每次可给大预算（默认 2s，旧版每 tick 0.3s）：高星也更可能证最优。
     tick 内成本 = O(扫描窗口) ≈ 微秒级（满足「每 tick 毫秒级出结果」）。

箱容：box_cap（change+store）进 DP 状态，任何落箱超限即剪枝 → DP 自动 JIT。
专辑底卡（collect 优先级策略）保留控制器侧预买：DP 的 lazy 规则把专辑底卡排在
终局，提前买让 collect 早触发、轮次早翻页；执行层按计划去重不超买。

执行层两趟结构（2026-10-24 定型，tid=59 实测 75.49→70.79h）：
   pass 1 买卡 reactive + JIT 门：扫描到就买、不等计划时刻——JIT 严守 start 门时，
     消费者到点常差 1~2 tick 才等到落箱卡 → 空转 + streak 假过时 → 重解
     级联；解耦后消失。纪律：按 DP 顺序发 + 箱预算闸（box_free + collect
     释放 - 专辑预扣 - 在炉落位保留，Ⅴ 类防丢卡）+ 每 tick 速率闸（同贪心
     口径在炉数×3+6）+ 计划去重。★ JIT 门（2026-10-25 紧箱根治）：喂料底卡
     只买到 due（可炼前沿需求）——旧版无门按箱预算买齐整套 buy op → 囤货 →
     紧箱 60 格箱囤满 → 买卡闸吃光 → 头 op（buy）永发不出 → cursor 冻结饿死
     （done=0/4、槽占 21.5%，同代码两轨迹分化 93.6%/21.5%）。
   pass 2 精炼时序门：到期门 + 头动作提前发 + 阻塞保护 + 窗口扫描。
   腾箱（BoxCompaction，同贪心 Step4）：箱紧时卖多余 10 面底卡（保留 due，
   铁律只卖 10 面）+ 挪多余合成卡进保险箱，腾出的位当 tick 即可被买卡用上。
"""
from __future__ import annotations

import os
import time as _t
from collections import Counter
from typing import Dict, List, Tuple

from backend.sched_cmp.sched_dp import solve, tb_of
from .model import ThemeModel
from .plan import Action
from .world import WorldState

TICK_SECS = 45.0        # 生产/仿真 tick 粒度（买卡物流 LAG = 1 tick）
EPS = 1e-9


class DPTickController:
    def __init__(self, models: Dict[int, ThemeModel], machines: int = 3,
                 time_budget: float = 0.1, time_budget_cold: float = 2.0,
                 warm_solves: int = 8, node_budget: int = 8_000_000,
                 lag_secs: float = TICK_SECS, shop_cap: int = 15,
                 streak_limit: int = 4, scan_window: int = 6,
                 steal_enabled: bool = True,
                 cp_first: bool = False, cp_budget: float = 120.0,
                 sets_fn=None):
        self.models = models
        self.machines = machines
        # 每套本轮排几套（与贪心 `AutoRefine._calc_sets` 同口径）。None = 恒 1。
        # 生产/仿真都把生产那份 `_calc_sets` 传进来，保证「多套」不是两套口径。
        self.sets_fn = sets_fn
        # ★ 自适应分层预算（2026-10-26，用户定调「每 tick 快速出相对优的解」）：
        # 实测（_probe_speed.py / _probe_fast.py）：
        #  · iso 单次解算质量**与预算无关**——2★/3★/5★ 在 50ms 与 2s 的
        #    incumbent **完全相同**，4★ 仅差 0.68h（80.69 vs 80.01h）。
        #  · 但引擎级对「同值不同解」敏感：2★ 稳态 0.1s → 25.07h vs 2.0s
        #    → 23.10h（iso 同为 26.02h）。根因是「解算次数」这条链：2★ 全程
        #    只重解 5 次，某次降预算 → 轨迹分叉 → 后续不再触发（5→2 次）
        #    → 少掉滚动纠错 → +1.97h。注意 0.5/0.3/0.2/0.1s 结果全同，
        #    故这不是「预算大小」问题，而是「解的具体形状 → 触发链」问题。
        #  · 4★ 全程重解 **119 次**，2s×119=236s 占墙钟 99.6%；降到 0.1s
        #    后 makespan **一字不差**（77.45h），墙钟 237s → 28s。
        # ⇒ 按「解算次数」自适应：前 warm_solves 次给足预算（保触发链与骨架
        #   质量），之后（说明这是高频重解的大实例）自动切快预算。成本上界
        #   = warm×cold + 其余×steady；重解少的低星全程 cold（行为=旧版）。
        # 环境变量：MC_SOLVE_BUDGET（steady）/ MC_SOLVE_BUDGET_FIRST（cold）
        #           / MC_WARM_SOLVES（warm 次数）/ MC_MAXVECS（向量截断上限）
        self.time_budget = float(os.environ.get("MC_SOLVE_BUDGET") or time_budget)
        self.time_budget_cold = float(
            os.environ.get("MC_SOLVE_BUDGET_FIRST") or time_budget_cold)
        self.warm_solves = int(os.environ.get("MC_WARM_SOLVES") or warm_solves)
        self.max_vecs = int(os.environ.get("MC_MAXVECS") or 96)
        # 每 tick 都重解（默认关，仅实验入口）：实测 4★ 5767 次 → 82.72h
        # （+5.27h）——破坏 plan 连续性（cursor/买卡去重/精炼时序门都以
        # 「一份持续的计划」为前提），故**默认绝不开**。
        self.solve_every_tick = os.environ.get("MC_SOLVE_EVERY") == "1"
        self.node_budget = node_budget
        # ★ CP-SAT 冷启骨架（2026-10-25，A 步实验开关）：
        # sched_dp 实测差真最优 2★ 18% / 3★ 7.9% / 4★ 7.3%（CP-SAT 对照），
        # 但 CP-SAT 单次 100~240s，不能进每 tick 滚动循环。故只允许
        # **第一个承诺点（冷启：空箱+空炉+无 memo）**用它出一次全局骨架，
        # 之后所有偏差点仍走 sched_dp 2s 快路径。
        # 环境变量 MC_CP_FIRST=1 可开启（便于 bench 对照，不改调用方）。
        self.cp_first = bool(cp_first) or os.environ.get("MC_CP_FIRST") == "1"
        self.cp_budget = float(os.environ.get("MC_CP_BUDGET") or cp_budget)
        self._cp_used = False
        self.cp_stats: dict = None
        # ★ CP 顺序 tie-break（2026-10-25 option 2）：离线从 CP 最优轨迹抽出
        # 「谁该早上炉」的静态优先级（sched_dp.tb_hints），只改列表调度/向量
        # 的选卡顺序，**不动时刻口径**（执行层仍保留容差）。
        # 实测结论：solver 层有效（2★ iso DP 26.02→24.02h），但引擎级**负收益**
        # （23.10→24.07h，5 次重复确定复现）——更好的静态序让头动作更少被卡，
        # 重解 5→2 次，执行层自适应修正被浪费。故默认关闭，仅作实验入口：
        #   MC_TB=1      开启 tb tie-break
        #   MC_TB_FIRST=1 让 CP 顺序接管主序（实测更差，2★ UB 28.01h）
        #   MC_TB_SCOPE=first 只用于冷启首个承诺点（实测与全程同值）
        self.tb_on = os.environ.get("MC_TB") == "1"
        self.tb_first = os.environ.get("MC_TB_FIRST") == "1"
        self.tb_scope = os.environ.get("MC_TB_SCOPE", "all")
        self.lag_secs = float(lag_secs)     # 买卡物流延迟（1 tick）
        self.shop_cap = int(shop_cap)       # 每 tick 买卡并发上限
        # 头动作连续被卡 tick 数 → 骨架过时。配合 streak 判定的「等待过滤」
        # （喂料在炉即将落箱/本 tick 已买 = JIT 物流等待，不累积），4 tick
        # 足够灵敏——2026-10-24 前无过滤版在紧箱 60 格把等料当过时触发重解
        # 级联（500tick solves 69 次）。
        self.streak_limit = int(os.environ.get("MC_STREAK") or streak_limit)
        self.scan_window = int(os.environ.get("MC_SCAN") or scan_window)
        # ★ 2026-10-27 消融（用户定调「DP 算的是投炉顺序，不是第几 tick 投哪个炉」）：
        #   DP 的优化目标是 makespan，op["start"] 是「该最优序下最早可开工时刻」
        #   —— 下界，不是指令。旧执行层把它当「必须等到」用（未到期即 break），
        #   是下界当上界，料已齐/炉已空时也要空等。开关开 = 纯按顺序发，只看
        #   料齐+炉空，不看 start。
        self.order_only = os.environ.get("MC_ORDER_ONLY") == "1"
        # 「主炉只给不可偷炉的卡」（默认开）：40 面卡可走偷炉，4h/顶层只能主炉。
        #   旧逻辑按数组顺序抢主炉 → 40 面卡（1h 短链）把最后一个主炉占掉，
        #   到期且料齐的 4h 关键卡只能等它 1h 出炉（2★ 实测 +0.97h）。
        #   消融入口：MC_MAIN_RESERVE=0 关。
        self.main_reserve = os.environ.get("MC_MAIN_RESERVE", "1") == "1"
        # ★ 排队位（真机每炉 2 个卡位：1 在炼 + 1 排队，`<stovebox cur="7"
        #   stovenum="3">` = 3×2 + 偷炉 1）。
        #   ★★「能排队」≠「该排队」：提前投会把素材锁死在排队卡上（换卡箱 18 格
        #   下尤其致命），而且 DP 骨架只按 3 并发排期。1★ tid=85 旧时长实测：
        #   允许排队 8.12h vs 只按并发 7.08h（更慢 1.04h）→ 默认按「并发上限」收窄，
        #   `MC_MAIN_QUEUE=1` 才允许往已在炼的炉追加排队。
        self.allow_queue = os.environ.get("MC_MAIN_QUEUE") == "1"
        # （流水线一环 `pipe_sets` / `pipe_min_free` 的定义在下方 `buy_free` 段之后：
        #   该策略假设「买卡不占 tick」，与 buy_free 是同一口径，不能独立开。）
        # 逐 op 执行日志（默认关；排查「为什么这一炉没开」用）
        self.dbg_op = os.environ.get("MC_DBG_OP") == "1"
        self._oplog: List[tuple] = []
        #   （配套修复见 `_run_plan` 的 `_not_due` 分支：未到期且投不了 = 计划内
        #     等待，不应急买、不计 discrepancy。旧的 `MC_BUY_GUARD` 硬门已删 ——
        #     它让 4★ 买卡 191→204、收益归零，属错误修法。）
        self.steal_enabled = bool(steal_enabled)   # 偷炉可规划（class 2）
        # ★ 喂料底卡预买提前量（单位=tick）：买来的卡要下一 tick 才落箱/才知道
        #   slot，所以「下一 tick 会空」的炉必须在**本 tick** 就把料买进来，
        #   否则炉空那一 tick 才开始买 → 空转 1 tick（45s）× 每炉次。
        #   1 = 只看本 tick 内到点（会空转）；2 = 多看一个 tick（提前 45s 备料）。
        self.prebuy_look = int(os.environ.get("MC_PREBUY_LOOK") or 1)
        # 0 额度下限（旧行为 max(1,...)*3，炉满也保底一波）；仅供 ablation 对照
        #   None=默认（按箱余量自适应）、True=强制保底一波、False=强制不保底
        _fl = os.environ.get("MC_PREBUY_FLOOR")
        self.prebuy_floor = None if _fl is None else (_fl == "1")
        # ★★ 2026-10-27 用户定调（第二次）：「买卡不是瓶颈，所以买卡不应该单独占一个
        #   tick，也不应该算到单独的解」。
        #   现状确实是错的：pass 2 用 `f in bought_this_tick → blocked` 把本 tick 买的
        #   料全部排除在喂料之外，等于「买卡占一个 tick」。真机 `card_buy` code=0 即落
        #   换卡箱、仿真 `EP_BUY` 即 `_add_to_box` —— 两边都没有物理延迟。
        #   实测（3★ 空炉起步）：tick1 买 143~148 → 全被排他；tick2 又买 144~148（143
        #   因 planned_buy 用完跳过）→ 只剩 143 可用，单张凑不成配方；到 tick3 才投第
        #   1 炉。箱空 18→12→7 花掉 11 张料只开 1 炉，就是这条自造回环。
        #   开关打开 = 本 tick 买的卡用**虚拟槽**进 avail_slots，买完即投。
        self.buy_sametick = os.environ.get("MC_BUY_SAMETICK") == "1"
        # 底卡在 solver 里的耗时（45s = 1 tick）改为 0：物理上买卡即时落箱。
        # 必须与 buy_sametick 同时开——只解锁执行层会让执行比计划早 1 tick（乱序），
        # 只改 dur 则执行层仍被 `bought_this_tick` 挡住（两者都白改）。
        self.base_dur0 = os.environ.get("MC_BASE_DUR0") == "1"
        # ★★ 2026-10-27 用户定调：「10 面额底卡可以认为本身就存在，因为可以随时买、
        #   也不用花 tick」。把买卡从**解**里拿掉：solver 认为底卡即时可得（只受箱容），
        #   买卡不再占 shop 时间槽、也不再是独立排期节点。
        self.buy_free = os.environ.get("MC_BUY_FREE", "1") == "1"
        # ★★ 2026-10-28 转默认开（原默认关，卡在「多套亏」上；该缺陷已由
        #   事件驱动重解修掉）。今日同环境实测（18/200、3 主炉+偷炉、45s/tick）：
        #     BF  vs  AD：85×1 4.45/4.45、290×1 14.43/14.44、59×1 45.41/45.86、
        #                 60×1 50.83/50.85、178×2 3.79/3.84、290×2 21.90/21.93、
        #                 178×3 5.69/7.03、85×3 13.19/13.84
        #   → 全档不劣于 AD（最大差 0.01h，噪声级），多套明显更优。
        #   `MC_BUY_FREE=0` 可回旧路径（AD）做对照。
        # ★★ 2026-10-28 流水线一环（修「多轮里当前轮排满 → ops 空 → 执行层停手」）：
        #   多轮场景下 `_calc_sets` 常算出 1 套（1★ 按 `max(各 40 面持有)` 算，而
        #   一套顶层要 3 张**不同** 40 面 → 箱里刚好各 1 张时 max=1）。此时当前那套
        #   的顶层已在炉 → `net_plan(sets=1)` 净需求归零 → `_solve_plan` 返回空 ops
        #   → 执行层整 tick 停手（不是「解出来排不上」，是「根本没解出活」）。
        #   实锤 tid=178×3 + buy_free：1365 tick 里 **960 tick（70%）ops 为空**、
        #   957 tick 零动作；其中 878 tick 只有 1 个主炉在忙、换卡箱**空 15 格**、
        #   箱里 2370/2371/2372 三张 40 面已齐（本可立刻炼下一份顶层）→ 每轮 5.70h
        #   vs 默认路径 5.05h（+1.91h）。默认路径掩盖了它：ops 里有 buy op 撑着非空。
        #   修法 = 还有剩余轮次且换卡箱不紧时，sets 至少排 2（只多流水线一环，不铺满）。
        #   ★ 必须留箱容门槛：sets>1 叠满箱 → collect 不触发 → rounds_sig 不变 →
        #   永不重解 的历史死锁（见 `_union_need0` 注释）。
        # ★★ 必须与 buy_free 绑定（四列 A/B 实锤，2026-10-28）：
        #     BF  178×2 11.05→6.04 / 178×3 17.06→9.05 / 85×2 15.09→14.09 /
        #         85×3 22.09→21.10（全正）；
        #     但 AD 178×2 10.11→**11.10（+0.99 退化）**、178×3 15.15→11.11。
        #     机理：AD 下 buy 仍是 solve 里的排期节点、占 shop 串行槽，sets=2 会让
        #     两套共 6 张底卡同时排队 → 第一套的料反而到得更晚。buy_free 才使
        #     「多排一环」零成本。故默认 = 跟随 buy_free；`MC_PIPE_SETS=1/0` 可强制。
        _ps = os.environ.get("MC_PIPE_SETS")
        self.pipe_sets = (self.buy_free if _ps is None else _ps == "1")
        self.pipe_min_free = int(os.environ.get("MC_PIPE_MIN_FREE") or 6)
        if self.buy_free:
            # ★ 口径联动（2026-10-27）：solver 里 buy 与 refine 同拍（instant），
            #   执行层若还按「本 tick 买的料不能当喂料」就会系统性晚 1 tick 投炉，
            #   等于把计划改成不可执行。两处必须同口径 → 强制联动。
            self.buy_sametick = True
        # ★ 2026-10-27（多套退化定位，`tests/_probe_multi_bf.py`）：buy_free 把
        #   buy op 一起删掉是**过度**的，而且会造出真 bug。op 有两重身份：
        #   ① solver 的排期节点（该删：物理不存在）；② 执行层的「买卡需求清单」
        #   （不能删）。删掉后实测（tid85 三套）：非底卡需求归零时 `ops` 为空 →
        #   pass 1 的 `jit_need` 也从空 ops 算出空 → 有炉位却一张不买 → 空转；
        #   且 `exhausted`（0>=0）每 tick 触发一次空解，803 次空算：
        #     删 op 28.09h 炼42 买167 炉占 82.8%
        #     留 op 20.07h 炼33 买155 炉占 97.1%   ← 与 AD 21.11h 同量级
        #   故默认**保留** buy op（零耗时，只是需求清单）；`MC_BUY_KEEPOP=0`
        #   可复现病态档。
        # ★★ 2026-10-27 修正（`tests/_probe_bf_idle.py` 实测）：buy op **不能**留在
        #   `ops` 里。buy_free 让 solver 对买卡没有任何时间成本 → 连「终局收藏」
        #   的底卡也被排到 t=0；执行层的 JIT 闸（`cid not in jit_need`）正确地
        #   拒绝买它，但**不会把它标成已处理** → `while issued[cursor]` 永远卡在
        #   索引 0 → streak 每 tick +1 → 每 4 tick 重解一次（结果还是同一个），
        #   两个空主炉白转 2h。实测 3★ tid59：留 op 73.41h / 删 op 70.74h。
        #   故默认**不生成** buy op（`MC_BUY_KEEPOP=1` 可复现病态档）。
        #   注：删 op 后 pass 1 的需求清单来自 refine op 反推的 `jit_need`；
        #   `exhausted` 已加 `len(ops)>0` 守卫，空计划不会每 tick 空解。
        self.buy_keepop = os.environ.get("MC_BUY_KEEPOP", "0") == "1"
        # 仅供口径一致性判定：buy_free 时跳过 dfs、直接交 UB 解（dfs 仍是旧口径）
        self.ub_only = os.environ.get("MC_UB_ONLY") == "1"
        self._memo: dict = {}
        self._skel_sig: tuple = None
        self._skel: dict = None
        self._skel_box_cap: int = None
        # cid -> owner model（各套卡库不相交，安全）
        self._owner: Dict[int, ThemeModel] = {}
        for tid, m in models.items():
            for c in m.ids:
                self._owner[c] = m
        # 喂料底卡集合（某合成卡的素材）——JIT 门只门喂料卡；纯收藏底卡
        # （不喂任何合成卡）无精炼消费者，由专辑预买/计划终局 op 拉起。
        self._feeder_cids: set = set()
        self._base_cids: set = set()
        for tid, m in models.items():
            for c in m.comp:
                self._feeder_cids.update(m.feeders(c))
            for c in m.ids:
                if m.is_base(c):
                    self._base_cids.add(c)
        # ---- 骨架缓存（承诺式调度）----
        self._plan: dict = None
        self._plan_rounds_sig: tuple = None
        # profiling
        self.solve_count = 0
        self.solve_max_ms = 0.0
        self.solve_total_ms = 0.0
        self.bounded_count = 0
        self.buy_failed = 0
        self.resolves = 0               # 承诺点重解次数（骨架代数）
        self.discrepancy_total = 0      # 有限偏差累计（应急买 + 扫描跳过）
        self.emergency_total = 0        # 应急买卡累计张数
        self.opportunistic_total = 0    # 提前发累计次数（纯时机微调，不计偏差）
        self.streak_max = 0
        # JIT 门 + 腾箱统计（2026-10-25 紧箱根治，见 _jit_due/_box_compaction）
        self.jit_blocked = 0            # JIT 门拦下的买卡 op 数（未到需求不买）
        self.buy_fallback = 0           # 「ops 空、只剩底卡要买」时按 buy0_map 兜底的次数
        self.pipe_sets_hits = 0         # 「本轮排满但还有轮次」时把 sets 提到 2 的次数
        self.compact_sold = 0           # 腾箱卖卡累计张数（只卖 10 面底卡）
        self.compact_moved = 0          # 腾箱挪保险箱累计张数
        # ★★ 2026-10-28 事件驱动重解（默认开，`MC_EVENT_RESOLVE=0` 可关）：
        #   buy_free 下 ops 里没有 buy op，于是「骨架推进」这个唯一的重解信号源
        #   消失了 —— 实测 290×2：AD 178 次解 / BF 只有 6 次，骨架一次定终身。
        #   代价：首解把 46 张 0.625h 短活按 50tick/批 连排到 tick 551，2.5h 中间层
        #   被挤到 554/754/954（AD 是 204/652/654），顶层卡开工 1154 vs 853 → 慢
        #   1.28h。★加大预算无效（0.1/1/4s 三档同为 23.80h、4259 开工同为 1154），
        #   即首解就是这么排的，不是搜索深度问题；让它滚动重解即可修复
        #   （每 tick 解 21.29h，留 buy op 22.51h，基线 23.80h）。
        #   这里用「真正投出一炉」作为事件（比每 tick 解便宜得多：60 次量级）。
        self.event_resolve = os.environ.get("MC_EVENT_RESOLVE", "1") == "1"
        self._dirty = False             # 上次解之后是否又投出过炉（需重解）
        self._left_rounds = None        # 各套剩余轮数最小值（多轮判定，_union_need0 写）
        self.dirty_resolves = 0         # 事件驱动重解次数
        # streak 重解退避（2026-10-24 紧箱实测教训）：头动作因物理短缺
        # （等买卡落箱/等炉出货，JIT 正常等待）连续被卡 → streak 触发重解，
        # 但新骨架头动作往往不变（短缺不是顺序问题）→ 每 4 tick 一次 2s 解算
        # 的级联（60 格·各2轮实测 24 分钟没到 tick 5000，全程预估 5h+）。
        # 连续无进展重解累计到 cap 后暂停 streak 触发，等物理条件真实变化
        # （有精炼/买卡/应急动作即清零）——不改变调度决策质量，纯止血。
        self._no_progress_resolves = 0
        self.streak_resolve_cap = 16
        self._dbg: dict = {}            # 临时插桩（提前发失败原因），验收后删

    # ---- 静态骨架（类型级），active 套集合 / 箱容变化才重建 ----
    def _skeleton(self, active):
        """类型级骨架：非底卡 = 精炼类型；底卡 = 买卡类型（mclass [1]、
        dur=LAG、mats 空）——买卡是状态里的一等公民，DP 选卡即知物流延迟。

        机器类（mclass，2026-10-24 偷炉可规划）：40 面精炼 = [0,2]（主炉或偷炉
        均可，DP 逐作业选类）→ 骨架是 4 炉调度而非 3 炉；其余精炼 = [0]；
        偷炉关闭时全部退化为 [0]（3 炉）。
        """
        ut: List[int] = []
        seen = set()
        for t in active:
            m = self.models[t]
            for c in m.ids:
                if c not in seen:
                    seen.add(c)
                    ut.append(c)
        idx = {c: i for i, c in enumerate(ut)}
        n = len(ut)
        dur, mclass, mats = [], [], []
        for c in ut:
            m = self._owner[c]
            if m.is_base(c):
                dur.append(float(self.lag_secs))
                mclass.append((1,))
                mats.append(())
            else:
                dur.append(float(m.secs(c)))
                if self.steal_enabled and m.is_40(c):
                    mclass.append((0, 2))     # 40 面：主炉或偷炉
                else:
                    mclass.append((0,))
                mc = {}
                for f in m.feeders(c):
                    if f in idx:            # 喂料（含底卡）都在骨架内
                        mc[idx[f]] = mc.get(idx[f], 0) + 1
                mats.append(tuple(sorted(mc.items())))
        depth = [self._owner[c].depth.get(c, 0) for c in ut]
        cp = [0.0] * n

        def cpf(i: int) -> float:
            if cp[i]:
                return cp[i]
            cp[i] = dur[i] + max((cpf(j) for j, _ in mats[i]), default=0.0)
            return cp[i]

        for i in range(n):
            cpf(i)
        return {"types": ut, "idx": idx, "n": n, "dur": dur,
                "mats": mats, "mclass": mclass, "depth": depth, "cp": cp}

    def _union_need0(self, box_stock, pending, active, completed_rounds,
                     max_rounds, credits=None, sets_fn=None, free_main=0,
                     box_free=0):
        """净需求：(非底卡炼几次, 底卡买几张)。

        直接复用 net_plan(box, pending, sets)（与贪心 Planner 完全同口径）：
        每一层自顶向下扣减 box+pending，是正确的 MRP 净需求。底卡需求取 net_plan
        的 buy 字典（含集卡册收藏需求 + 喂料需求，已净减持有）。

        两个「与贪心同口径」的接口（2026-10-27 接入，此前 DP 都缺）：

        · `credits` = {tid: Counter(40面卡 -> 张数)}：**变卡环节接下来会产出的 40
          面卡**（生产 `AutoRefine._face40_credit`）。并进 avail 后，净需求就不会
          再排「拿底卡现造一张同款 40 面」—— 否则变卡白变、且白占一炉。
          与贪心 `_refine_all` 里 `p.net_plan(stock, pending, credit, sets)` 同一语义。
        · `sets_fn` = 每套本轮排几套（生产是 `AutoRefine._calc_sets`：1 星按囤的
          40 面算多套、其他星按未收集 qq秀 数）。默认 None → 恒 1（旧行为）。
          ⚠ sets>1 会一次性排多轮中间卡，紧箱下有「叠加占满箱 → collect 不触发 →
          rounds_sig 不变 → 永不重解」的死锁史（见下方注释），所以上限再按
          `max_rounds - completed` 收紧，且只有调用方显式给 sets_fn 才会 >1。
        """
        need, buy = Counter(), Counter()
        credits = credits or {}
        self._left_rounds = None        # 本次净需求计算里各套剩余轮数的最小值
        for t in active:
            m = self.models[t]
            # 变卡 credit：变卡环节即将交出的 40 面卡，先当已到货
            avail = dict(box_stock)
            for c, n in (credits.get(t) or {}).items():
                if int(n) > 0:
                    avail[int(c)] = avail.get(int(c), 0) + int(n)
            # 套数：默认 1；给了 sets_fn 就与贪心 `_calc_sets` 同口径。
            # 原写法 `sets = max_rounds - completed_rounds[t]` 会把多轮卡一次性全排进
            # 计划，两轮中间卡叠加占满箱 → collect 永不触发 → rounds_sig 不变 →
            # 永不重解 → 永久饿死（实测 done=0/4、箱满 60/60、槽占仅 22%）。
            # 所以这里是「每解排下一批」，且用剩余轮数封顶。
            sets = 1
            if sets_fn is not None:
                try:
                    sets = max(1, int(sets_fn(t, avail, pending, free_main)))
                except Exception:      # noqa: BLE001  回调异常不拖死调度
                    sets = 1
            if max_rounds:
                left = int(max_rounds) - int(completed_rounds.get(t, 0))
                if left > 0:
                    sets = min(sets, left)
            else:
                # ★ 生产默认 `max_rounds=0` = **不限轮次**（`AutoRefine.max_rounds`
                #   语义，见 auto_refine.py:575）。这里必须视作「还有无限轮」，
                #   否则流水线一环（pipe_sets）在生产恒不生效 —— bench 全用
                #   mr=1/2/3 会掩盖这个口径差。
                left = 99
            # 供事件驱动重解判定「是否多轮」（多套取最小剩余轮数 = 最保守）：
            # 跨轮才有「下一环要滚动规划」的需求。强制全局开时 290×1 退化
            # +1.22h（单轮静态序更好），故按多轮启用；多轮侧 290×2 −1.90h、
            # 85×3 AD −1.23h，单轮侧全档逐位不变（见 __init__ 注释）。
            self._left_rounds = (left if self._left_rounds is None
                                 else min(self._left_rounds, left))
            runs, buys = m.net_plan(avail, dict(pending), sets=sets)
            # ★★ 2026-10-28 流水线一环（见 `__init__` 的 `pipe_sets` 注释）：
            #   本轮的净需求（非底卡）已经归零 —— 顶层在炉/已炼完，但**还有剩余
            #   轮次**且换卡箱不紧 → 现在就把下一环排进来。否则 ops 为空、执行层
            #   整 tick 零动作，而箱里的 40 面恰好就是下一套的料（可立即开炉）。
            #   只提一格（sets=2），不铺满：铺满会叠箱 → collect 不触发 → 死锁。
            if (self.pipe_sets and left >= 2 and box_free >= self.pipe_min_free
                    and sets < 2):
                _nd = sum(n for c, n in runs.items() if not m.is_base(c))
                if _nd <= 0:
                    sets = 2
                    runs, buys = m.net_plan(avail, dict(pending), sets=sets)
                    self.pipe_sets_hits += 1
            for c, nd in runs.items():
                if not m.is_base(c):
                    need[c] += nd
            for c, nd in (buys or {}).items():
                buy[c] += nd
        return need, buy

    def _album_prebuy(self, ws, active, box_stock, pending, actions, bought,
                      extra_free: int = 0):
        """专辑底卡预买（collect 优先级策略，非物流补偿）。

        DP 骨架按 lazy 规则把专辑底卡排在终局（无精炼需求时才买）；但 collect 要等
        它们在箱。同贪心 Step5 口径：某套非底卡全在箱 → 只差专辑底卡 → 现在就买，
        collect 早触发、轮次早翻页（多轮流水）。执行层按计划去重，不超买。
        箱预算同贪心 Step5：box_free + collect 释放 - 在炉落位保留（Ⅴ 类：
        炼成卡落箱失败会被静默丢弃，必须给在炉卡留落位）。
        ★ 条件放宽到「在箱**或在炉**」（2026-10-25）：最后一个非底卡常是长时
        顶层卡（4★ 实测在炉 ~1400 tick），严条件（只在箱）会让收藏底卡迟买
        到顶卡落箱之后 → collect 白等；旧版 bulk 计划把终局 buy 前置掩盖了
        这一点，轮转买计划暴露（4★ 尾部 371→1439 tick、makespan +6h）。
        bought: Counter（cid -> 本 tick 已买张数，含在途未落箱）。
        """
        # ★ 箱预算必须用**换卡箱**空位（2026-10-26，与 _run_plan 买卡闸同一个坑）：
        # 买卡只能落换卡箱（硬顶 18 格），保险箱不能收买卡。旧版用双箱合计
        # （ws.box_free）→ 保险箱剩 199 格时判「有位」，把整套专辑底卡全买一遍，
        # 执行层 `_free_change <= 0` 逐张失败（4★ 实测 220 次「卡箱 0 空位」）。
        # reserve 同 _run_plan：只按「到点临近度」留落位（2026-10-26，见
        # _run_plan 里的实测记录：旧版「有炉就 +2」在紧箱时把预算清零）。
        reserve = sum(1 for f in ws.furnaces
                      if 0 < f.endtime - ws.now <= TICK_SECS + EPS)
        reserve += 1 if any(TICK_SECS + EPS < f.endtime - ws.now <= 2 * TICK_SECS
                            for f in ws.furnaces) else 0
        free = max(0, ws.change_free + extra_free - reserve)
        for t in active:
            m = self.models[t]
            # ★★ 2026-10-27 用户定调（真机纠正）：「买底卡的时机在除底卡外的所有卡
            #   都在**卡箱里**，现在买齐直接上交，而不是还在炼呢就要买齐」。
            #   旧版条件放宽成「在箱**或在炉**」→ 最后一个非底卡是长时顶层卡
            #   （4★ 实测在炉 ~1400 tick）时，5 张底卡提前买进换卡箱白占 5 格；
            #   换卡箱硬顶 18、紧箱时少一个空位就「请空出两个卡箱的位置，再取卡」。
            #   现要求：非底卡全部在**箱**，且该套在炉里一张都没有。
            if not all(box_stock.get(c, 0) >= 1
                       for c in m.ids if not m.is_base(c)):
                continue
            if any(pending.get(c, 0) > 0 for c in m.ids if not m.is_base(c)):
                continue
            for c in m.ids:
                if not m.is_base(c):
                    continue
                gap = 1 - box_stock.get(c, 0) - pending.get(c, 0)
                if gap > 0 and free > 0:
                    actions.append(Action(kind="buy", cid=c, n=1,
                                         note=f"DP专辑底卡 {c}"))
                    bought[c] += 1
                    free -= 1

    def _jit_due(self, ws, plan, box_stock, pending, window: int = 0,
                 active=None):
        """JIT 需求（due）：即将能炼的合成卡 → 其实例底卡喂料（同贪心口径）。
        根因（2026-10-25 紧箱 60 格·各2轮 DP done=0/4 根治）：pass 1 旧版
        reactive 把整套 buy op 按箱预算一次性买齐 → 囤货（4★ 峰值 201/218、
        94% 是底卡；紧箱 60 格箱被喂料囤到 60/60）→ 买卡闸 box_free-reserve
        被吃光 → 头 op（buy）永发不出 → cursor 冻结饿死（全程槽占 21.5%；
        同代码两次运行轨迹分化 93.6% 流动 / 21.5% 饿死，场景处死锁边缘）。
        due = 「非底卡喂料已齐（可炼）」的合成卡的底卡喂料毛需求；pass 1
        只买到 due-held，即买即用，箱内只存前沿管道量而非整套底卡。
        与贪心 Step-JIT 同口径（net_plan runs 的可炼判定），只是把 runs
        换成 DP 骨架的未发 refine op（feeders 已随 op 落盘，免重复查找）。

        ★ window（2026-10-25 二层根治）：只把**接下来 window 个**可炼 op
        计入 due。无窗口时整个可炼前沿都进门禁——紧箱 60 格·各2轮 可炼前沿
        base 需求 ≈276（远超箱容 60）→ 门限永不触发 → reactive 买卡把骨架
        前段 bulk buy 全量拉齐 → 箱囤满 58/60 → 买闸吃光 → 4 个长炉期间
        全程冻结（26000tick 实测 done=0/4、零精炼 tick 占 99.5%、t12000
        与 t25000 状态逐字节相同）。窗口 = 2 波炉次工作量（≈2×炉数），
        箱内只存前沿管道量（~2×window×3 张），随精炼消耗逐波滚动。

        ★ collect 缺口项（同贪心口径②）：某套非底卡全在箱**或在炉**（collect
        在即）→ 缺口（1 - 持有）进 due。缺了它，终局收藏底卡被 JIT 门拦死
        （due=0）→ 只能等专辑预买、而预买曾被最后一个长炉卡住 → 4★ 尾部
        白等 1439 tick（旧版 bulk 计划前置终局 buy 才掩盖，轮转买后暴露）。
        """
        due: Dict[int, int] = {}
        # protect = 窗口内可炼 op 的喂料毛需求（**不看是否底卡**）。腾箱用它当
        # 「必须留在换卡箱」的门槛：窗口外的半成品（40/80/120 面中间物）可以挪
        # 保险箱——素材在保险箱照样能投炉（payload 带 slottype），不影响精炼。
        # ★ 旧版腾箱用「全部未发 op 的毛需求」，18 格下 8 条链的半成品全在
        # 保护名单里 → 一张也挪不动 → 箱 18/18 永久、买卡全失败、4 炉全空
        # （4★ 实测 34h 起 refined=82 卡到 45h）。
        protect: Dict[int, int] = {}
        ops = plan["ops"]
        issued = plan["issued"]
        base_cids = self._base_cids
        n_ready = 0
        for j, op in enumerate(ops):
            if issued[j] or op["kind"] != "refine":
                continue
            feeders = op.get("feeders") or ()
            if not feeders:
                continue
            if any((f not in base_cids) and box_stock.get(f, 0) < 1
                   and pending.get(f, 0) < 1 for f in feeders):
                continue                # 非底卡喂料未齐 → 这张还炼不了
            if window and n_ready >= window:
                break                   # 窗口外的前沿需求不进门禁（不拉齐囤货）
            n_ready += 1
            for f in feeders:
                protect[f] = protect.get(f, 0) + 1
                if f in base_cids:
                    due[f] = due.get(f, 0) + 1
        # ---- collect 缺口（不走窗口：只 1 张/套，天然有界）----
        for t in (active or ()):
            m = self.models.get(t)
            if m is None:
                continue
            # ★ 2026-10-27 同 _album_prebuy：collect 缺口也只认「非底卡全在箱」
            #   （在炉不算），否则顶层还在炼就把底卡拉进 due → 提前买、占格。
            if not all(box_stock.get(c, 0) >= 1
                       for c in m.ids if not m.is_base(c)):
                continue                # 合成卡未全在箱 → collect 不在即
            if any(pending.get(c, 0) > 0 for c in m.ids if not m.is_base(c)):
                continue
            for c in m.ids:
                if not m.is_base(c):
                    continue
                gap = 1 - box_stock.get(c, 0) - pending.get(c, 0)
                if gap > 0:
                    due[c] = max(due.get(c, 0), gap)
                    protect[c] = max(protect.get(c, 0), gap)
        return due, protect

    def _box_compaction(self, ws, plan, due, actions, avail_slots,
                        protect=None):
        """腾箱（BoxCompaction，同贪心 Step4 口径）：箱紧时卖多余 10 面底卡
        + 挪多余合成卡进保险箱，给 JIT 买卡 / 落箱腾位。

        ★ 返回值 = **换卡箱**净腾出的空位数（2026-10-26 校正）。
        买卡只能落换卡箱（硬顶 18 格），所以调用方 `_run_plan` 把它加进
        `change_free` 预算。旧版返回 `n_sold`（**全部**卖出张数），其中
        「卖保险箱里的卡」腾的是保险箱位、对换卡箱毫无帮助 → 预算虚高 →
        每 tick 发出换卡箱装不下的买卡 → 执行层逐张失败。同理「挪保险箱」
        确实腾出换卡箱位，旧版却没算进去（保守方向，一并修正）。

        触发：change_free <= 2 或 box_free <= 4（同贪心）。安全边界：
        ① 只卖 is_base 且 price==10 的底卡（铁律：高面值卡永不自动卖；
          10 面卡 sell_card 只换回面值等额金币，卖+买无损，绝不动 40 面
          素材与合成卡—— tid=709 底卡 57 面也不卖，保守）；
        ② 保留量按 **JIT 窗口需求**（protect = 窗口内可炼 op 的喂料毛需求，
          不是全部未发 op 的总需求）：否则 18 格箱里 8 条链的半成品全在保护
          名单里 → 一张也挪不动 → 箱永久满、买不进 → 4 炉全空（4★ 实测）。
          卖超的卡同步回减 plan["bought"]（计划去重账），后续需求增长可经
          计划 op / 应急买重新拉起；
        ③ 挪箱把「窗口外的合成卡」挪进保险箱，目标箱要有空位（预测 sim 的
          首空位规则）。挪走后**必须把新槽位 `(dest, 1)` 补进 avail_slots**：
          素材在保险箱照样能投炉（payload 带 slottype），不补就会让 pass 2
          以为该卡没了 → 整条链饿死。
        ★ 口径纠正（2026-10-26）：3 合 1 不是「3 张同类」，是配方指定的 3 张
          （4★：40面 199←172+170+171；80面 197←198+172+169 混层混箱）。
          旧注释的「同类/trio」说法是错的，protect 按 op 的 feeders 逐张累加
          即可，不涉及同类假设。
        每次腾箱记 1 点 discrepancy（计划与现实偏差信号 → disc_over 重解）。
        """
        if not (ws.change_free <= 2 or ws.box_free <= 4):
            return 0
        n_sold = n_moved = 0
        n_free_change = 0        # 换卡箱净腾位：卖换卡箱的卡 + 挪箱
        # 保留门槛：窗口内需求（protect），缺省退回空（= 只保 due）
        keep_map: Dict[int, int] = dict(protect or {})
        # ---- 1) 卖多余 10 面底卡（换卡箱 + 保险箱；保留 due / protect）----
        # total_of[cid] = 双箱合计张数；每卖 1 张递减， remainder>keep 才卖
        total_of: Dict[int, int] = {}
        for cid, _s, _st in ws.slots:
            if cid > 0:
                total_of[cid] = total_of.get(cid, 0) + 1
        for cid, slot, slottype in list(ws.slots):
            if cid <= 0:
                continue
            m0 = self._owner.get(cid)
            if m0 is None or not m0.is_base(cid) or m0.price(cid) != 10:
                continue                    # 只卖 10 面底卡（铁律）
            keep = max(1, due.get(cid, 0), keep_map.get(cid, 0))
            if total_of.get(cid, 0) <= keep:
                continue                    # 已降到保留量 → 不再卖
            actions.append(Action(kind="sell", cid=cid, slot=slot,
                                  slottype=slottype,
                                  note=f"DP腾箱卖10面底卡{cid}"))
            lst = avail_slots.get(cid)
            if lst and (slot, slottype) in lst:
                lst.remove((slot, slottype))
            if plan["bought"][cid] > 0:
                plan["bought"][cid] -= 1     # 去重账回减（需求增长可再买）
            total_of[cid] -= 1
            n_sold += 1
            if slottype == 0:
                n_free_change += 1       # 只有卖换卡箱的卡才腾出买卡可用的位
        # ---- 2) 挪多余合成卡进保险箱（保留未发 op 的喂料需求）----
        if ws.store_free > 0 and ws.change_free <= 2:
            change_of: Dict[int, int] = {}
            for cid, _s, st in ws.slots:
                if cid > 0 and st == 0:
                    change_of[cid] = change_of.get(cid, 0) + 1
            store_used = {s for (_c, s, st) in ws.slots if st == 1}
            store_free_local = ws.store_free
            for cid, slot, slottype in list(ws.slots):
                if cid <= 0 or slottype != 0 or store_free_local <= 0:
                    continue
                m0 = self._owner.get(cid)
                if m0 is None or m0.is_base(cid):
                    continue                    # 底卡已在上面处理
                keep = keep_map.get(cid, 0)      # 窗口内要用的才留，其余可挪
                if change_of.get(cid, 0) <= keep:
                    continue
                dest = next((s for s in range(1, ws.store_cur + 1)
                             if s not in store_used), None)
                if dest is None:
                    break
                actions.append(Action(kind="move", cid=cid, slot=slot,
                                      slottype=0,
                                      note=f"DP腾箱挪保险箱{cid}"))
                lst = avail_slots.get(cid)
                if lst and (slot, 0) in lst:
                    lst.remove((slot, 0))
                # 挪到保险箱后槽位变了：素材在保险箱照样能投炉（payload 带
                # slottype），必须把新槽位补回去，否则 pass 2 判该卡缺失 →
                # 整条链饿死。
                avail_slots.setdefault(cid, []).append((dest, 1))
                store_used.add(dest)
                store_free_local -= 1
                change_of[cid] -= 1
                n_moved += 1
                n_free_change += 1       # 挪箱把卡从换卡箱搬走，腾出买卡位
        if n_sold or n_moved:
            self.compact_sold += n_sold
            self.compact_moved += n_moved
            plan["discrepancy"] += 1       # 腾箱 = 计划与现实偏差信号
        return n_free_change

    @staticmethod
    def _box_peak(inst, sched, init_sum):
        """模拟骨架调度的箱占用峰值（买卡/精炼落箱 +1、精炼开工 -喂料数）。

        用于兜底：DP 搜不到可行解（箱容装不下）时会回退乐观 UB，其调度可能超容 →
        调用方据此 relax（去掉箱容）重解，执行层真实箱检兜底。
        """
        dur = inst["dur"]
        mats = inst["mats"]
        mclass = inst["mclass"]
        ev = []
        for i, s in sched:
            if mclass[i] == 1:
                ev.append((s + dur[i], 1))
            else:
                ev.append((s, -sum(k for _j, k in mats[i])))
                ev.append((s + dur[i], 1))
        # 同 t 先落箱后消耗（保守，峰值取上界）
        ev.sort(key=lambda x: (x[0], -x[1]))
        occ = peak = init_sum
        for _t, d in ev:
            occ += d
            if occ > peak:
                peak = occ
        return peak

    def _solve_plan(self, ws, active, need0_map, buy0_map, box_cap, sk,
                    rounds_sig):
        """承诺点解算：从当前真实状态（箱内 + 在炉）求剩余问题最优骨架。"""
        idx = sk["idx"]
        types = sk["types"]
        box_stock = ws.stock
        pending = ws.pending
        present = {c: box_stock.get(c, 0) + pending.get(c, 0) for c in types}
        live = [c for c in types
                if need0_map.get(c, 0) > 0 or buy0_map.get(c, 0) > 0
                or present.get(c, 0) > 0]
        live_idx = {c: i for i, c in enumerate(live)}
        nL = len(live)
        durL = [sk["dur"][idx[c]] for c in live]
        mclassL = [sk["mclass"][idx[c]] for c in live]
        # ★ 底卡「买卡耗时」= 45s 是建模虚构（真机 code=0 即落箱、仿真即时入箱）。
        #   打开后 solver 认为买卡零耗时 → buy 的 start 与消费者同 tick，与执行层
        #   「买完即投」一致；关闭时 solver 以为买卡要 1 tick（旧口径）。
        if self.base_dur0:
            durL = [0.0 if 1 in mclassL[i] else d for i, d in enumerate(durL)]
        matsL = []
        for c in live:
            mc = {}
            for j, k in sk["mats"][idx[c]]:
                cj = types[j]
                if cj in live_idx:
                    mc[live_idx[cj]] = mc.get(live_idx[cj], 0) + k
            matsL.append(tuple(sorted(mc.items())))
        need0L = []
        for c in live:
            i = live_idx[c]
            need0L.append(max(0, buy0_map.get(c, 0)) if 1 in mclassL[i]
                          else max(0, need0_map.get(c, 0)))
        depthL = [sk["depth"][idx[c]] for c in live]
        cpL = [sk["cp"][idx[c]] for c in live]
        # ★ init_stock 只用箱内（不含炉内 pending）；在炉由 init_running 记产出
        init_stockL = [box_stock.get(c, 0) for c in live]
        running = []
        for f in ws.furnaces:
            if f.cid <= 0 or f.cid not in live_idx:
                continue
            rem = max(0.0, float(f.endtime) - float(ws.now))
            cls = 2 if getattr(f, "steal", False) else 0
            running.append((live_idx[f.cid], rem, cls))
        inst = {"machines": self.machines, "shop_cap": self.shop_cap,
                "box_cap": box_cap, "mclass": mclassL, "types": live, "n": nL,
                "dur": durL, "mats": matsL, "need0": need0L, "depth": depthL,
                "cp": cpL, "init_stock": init_stockL,
                "steal_cap": 1 if self.steal_enabled else 0}
        if self.buy_free:
            inst["buy_free"] = True
            if self.ub_only:
                inst["ub_only"] = True
        if not self.tb_on:
            inst["tb"] = None           # 显式关闭（对照实验用）
        else:
            inst["tb"] = tb_of(inst)    # 离线 CP 顺序表（cid → 最优开工 tick）
            if self.tb_first:
                inst["tb_first"] = True
        # scope=first：CP 顺序只用于**冷启首个承诺点**，后续滚动重解回基线
        # （2026-10-25 实测：全程开启会把引擎从 23.10 拖到 24.07h——更好的静态
        #  序让头动作更少被卡 → 重解次数 5→2 → 少掉了执行层的自适应修正）。
        if self.tb_scope == "first" and self.solve_count > 0:
            inst["tb"] = None
        t0 = _t.perf_counter()
        mk = sched = stats = None
        # 分层预算：首解决定整条执行轨迹（必须给足），后续重解只做纠错。
        _tb = (self.time_budget_cold if self.solve_count < self.warm_solves
               else self.time_budget)
        # ---- ★ CP-SAT 冷启骨架（2026-10-25，实验开关 MC_CP_FIRST=1）----
        # 条件：仅第一个承诺点 + 空箱空炉空 memo（CP 模型只支持冷启）
        #     + 箱容不瓶颈（Σneed0 ≤ box_cap：CP 不建模箱容，峰值上限=总张数）。
        # 用途：sched_dp 在宽 ready 集（2★~4★）实测差真最优 7~18%，用 CP 的
        # 精确/近优全局骨架换掉第一个骨架；后续偏差重解仍走 sched_dp 2s。
        if (self.cp_first and not self._cp_used and self.solve_count == 0
                and not running and not any(init_stockL)
                and (box_cap is None or sum(need0L) <= box_cap)):
            self._cp_used = True
            try:
                from backend.sched_cmp import cp_sat_plan
                mk, sched, stats = cp_sat_plan.solve(
                    inst, time_limit=self.cp_budget)
            except Exception as exc:      # noqa: BLE001 — 任何异常都回退
                mk = sched = stats = None
                self.cp_stats = {"status": f"ERROR:{type(exc).__name__}",
                                 "err": str(exc)[:200]}
            if sched is not None:
                self.cp_stats = dict(stats)
                stats["cp_used"] = True
                # CP 不建模箱容 → 保守复核峰值，超容即弃用回退 sched_dp
                if (box_cap is not None
                        and self._box_peak(inst, sched, sum(init_stockL))
                        > box_cap + EPS):
                    self.cp_stats["rejected"] = "box_peak"
                    mk = sched = stats = None
            else:
                self.cp_stats = self.cp_stats or dict(stats or {})
        if sched is None:
            mk, sched, stats = solve(inst, time_budget=_tb,
                                     node_budget=self.node_budget,
                                     init_stock=init_stockL, init_running=running,
                                     memo=self._memo, max_vecs=self.max_vecs)
        # 箱容兜底：回退 UB 可能超容（DP 搜不到可行解）→ relax 重解。
        # ⚠️ relax 必须用全新 memo：箱容约束下的 INF/有限值会毒化无约束重解
        # （LB 剪枝与箱容无关仍有效，但「落箱超容」分支在 relax 后变可行）。
        # relax 只要一个可行上界（_ub_schedule 初始 incumbent 本就是合法
        # 列表调度）→ 预算砍到 min(0.5s, 全预算)：2026-10-24 紧箱实测每次
        # 承诺点「有约束 2s + relax 2s」= 4s，500tick 墙钟 139s 里 solve 占
        # 139s；relax 半秒后 incumbent 不变（UB 兜底），墙钟对半砍。
        if self._box_peak(inst, sched, sum(init_stockL)) > box_cap + EPS:
            inst["box_cap"] = None
            mk, sched, stats = solve(inst, time_budget=min(0.5, _tb),
                                     node_budget=self.node_budget,
                                     init_stock=init_stockL, init_running=running,
                                     memo=None, max_vecs=self.max_vecs)
            stats["box_relaxed"] = True
        dt_ms = (_t.perf_counter() - t0) * 1000.0
        self.solve_count += 1
        self.solve_total_ms += dt_ms
        self.solve_max_ms = max(self.solve_max_ms, dt_ms)
        if stats.get("bounded"):
            self.bounded_count += 1
        stats = dict(stats)
        stats["n_types"] = nL
        stats["solve_ms"] = round(dt_ms, 2)
        # ---- 骨架动作表（按 start 排序；同 start 买卡优先）----
        # ★ start 向上取齐到 tick 边界：执行层按 tick 触发（elapsed 是 45 的整数
        # 倍、solved_at 必为 tick 边界），连续时间的 start 每拍系统性迟到 0~45s，
        # 买卡链上「buy 晚 1 tick 触发 → 落箱再晚 1 tick」放大成等料空转与
        # streak 假过时。取齐后「计划时刻 = 实际触发时刻」，买卡落箱时刻
        # (= 触发 tick + LAG) 与计划假设一致，消费者到点即有料。
        tick = float(self.lag_secs)
        ops = []
        planned_buy = Counter()
        for i, s in sched:
            st_tick = (int(s / tick + (1 - EPS)) * tick) if s > EPS else 0.0
            c = live[i]
            if 1 in mclassL[i]:
                if self.buy_free and not self.buy_keepop:
                    # 底卡不进解：不生成 buy op（执行层按 jit_need 即时补料）
                    continue
                ops.append({"kind": "buy", "cid": c, "start": st_tick})
                planned_buy[c] += 1
            else:
                ops.append({"kind": "refine", "cid": c, "start": st_tick,
                            "feeders": list(self._owner[c].feeders(c)),
                            "cp": float(cpL[live_idx[c]])})
        ops.sort(key=lambda o: (o["start"], 0 if o["kind"] == "buy" else 1,
                                -cpL[live_idx[o["cid"]]], o["cid"]))
        # 关键路径长度中位数：早发只给「长链」开口子（见 `_run_plan` 的 `_cp_ok`）
        _cps = sorted(o.get("cp", 0.0) for o in ops if o["kind"] == "refine")
        _cp_thr = _cps[len(_cps) // 2] if _cps else 0.0
        plan = {"ops": ops, "issued": [False] * len(ops), "cursor": 0,
                "solved_at": float(ws.now), "rounds_sig": rounds_sig,
                "planned_buy": planned_buy, "bought": Counter(),
                "streak": 0, "discrepancy": 0, "makespan": mk,
                "n_types": nL, "box_relaxed": bool(stats.get("box_relaxed")),
                "cp_thr": _cp_thr}
        return plan, stats

    def _main_cap(self, ws) -> int:
        """本 tick 可投的主炉张数。

        真机每炉 2 个卡位（1 在炼 + 1 排队；`<stovebox cur="7" stovenum="3">`
        = 3×2 + 偷炉 1），所以 `ws.main_free`（空卡位数）会大于并发炉数。
        ★ 但「能排队」≠「该排队」：提前投让素材长时间锁在排队卡上（换卡箱 18 格
        下会挤掉正要买的底卡），而 DP 骨架只按 `machines` 并发排期 —— 两者不一致
        时执行层会「比模型更早锁料、更晚出活」。
        实测（1★ tid=85，TIME_SCALE=1.0 即旧时长）：允许排队 8.12h、只按并发
        7.08h → **默认收窄到并发上限**；`MC_MAIN_QUEUE=1` 放开排队做消融。
        """
        if self.allow_queue:
            return ws.main_free
        running = sum(1 for f in ws.furnaces
                      if not f.steal and f.endtime - ws.now > EPS)
        return max(0, min(ws.main_free, self.machines - running))

    def _run_plan(self, ws, plan, sk, actions, bought_this_tick,
                  album_bought: int, collect_freed: int,
                  box_stock, pending, active=None, buy0_map=None):
        """有限偏差列表调度（两趟：买卡 reactive + JIT 门 + 精炼时序门）。

        pass 1 — 买卡（reactive + JIT 门，不过 start 时序门）：
          每 tick 扫描全部买卡 op、按 DP 顺序发；箱预算闸 + 每 tick 速率闸兜底。
          实测（tid=59）：JIT 买卡严守 start 门时，消费者到点常差 1~2 tick 才
          等到落箱卡 → 空转 + streak 假过时 → 重解级联（75.49h）；买卡与精炼
          扫描解耦后 70.79h / 槽占 85.5%（优于贪心 73.41h）。
          纪律：① 按 DP 顺序发（DP 已平衡关键路与箱压，不轮转打乱其决策）；
          ② 箱预算 = box_free + collect 释放 - 专辑预扣 - 在炉落位保留；
          ③ 每 tick 速率闸同贪心口径（在炉数×3+6，专辑预买计入额度）；
          ④ 与计划去重（plan["bought"] 已含专辑预买）→ 绝不超买；
          ⑤ ★ JIT 门（2026-10-25）：喂料底卡只买到 due（可炼前沿需求），
             即买即用不囤货（旧版无门 → 紧箱 60 格箱囤满 → 买卡闸吃光 →
             头 op 永发不出 → cursor 冻结饿死 done=0/4）。
        pass 2 — 精炼（时序门）：到期门 + 头动作提前发 + 阻塞保护 + 窗口扫描；
          买卡 op 在此趟透明跳过（pass 1 已处理）。
        腾箱（_box_compaction）：箱紧时卖多余 10 面底卡 + 挪多余合成卡，腾出
          的位当 tick 即可被 pass 1 买卡用上（sell/move 先于 buy 执行）。
        """
        ops = plan["ops"]
        issued = plan["issued"]
        elapsed = max(0.0, float(ws.now) - plan["solved_at"])
        # ★ 本 tick 可投的主炉张数（见 `_main_cap`：默认只按并发，不允许排队）
        main_cap = self._main_cap(ws)
        avail_slots: Dict[int, List[tuple]] = {}
        for cid, slot, slottype in ws.slots:
            if cid > 0:
                avail_slots.setdefault(cid, []).append((slot, slottype))
        taken: set = set()
        # ---- JIT 需求（due，窗口化）+ 腾箱（2026-10-25 紧箱根治）----
        # due = 可炼前沿的底卡需求（_jit_due，只计接下来 2 波炉次工作量）；
        # 腾箱在箱紧时卖多余 10 面底卡 / 挪合成卡（_box_compaction），腾出
        # 的位当 tick 即可被 pass 1 买卡用上（sell/move 动作排在 buy 前，
        # executor 按序执行）。
        base_window = 2 * (self.machines + (1 if self.steal_enabled else 0)) + 2
        # ★ 窗口还要受**箱容**约束（2026-10-26，用户定调「不要囤卡，提前买齐就是囤卡」）：
        # 一个可炼 op 平均吃 3 张喂料 → 窗口 W 等于让 3W 张毛需求同时在箱。
        # 18 格换卡箱下 W=10 → 30 张 → 8 条 d=2 链全铺开，13 张半成品各 1 张
        # 占满箱、每张只差 1~2 样，真缺的 4 张底卡买不进 → 4 炉全空（4★ 实测
        # 34h 起 refined=82 冻结）。改成 change_cur//4（18→4）后只推 4 条链。
        # 60/200 格箱仍是 min(10, 15/50) = 10 = 原行为，不影响旧场景。
        cap_window = max(2, ws.change_cur // 4)
        due_window = min(base_window, cap_window)
        due, protect = self._jit_due(ws, plan, box_stock, pending,
                                     due_window, active)
        freed = self._box_compaction(ws, plan, due, actions, avail_slots,
                                     protect)

        def take_slot(cid: int):
            lst = avail_slots.get(cid)
            if not lst:
                return None
            for slot, slottype in lst:
                if (cid, slot) not in taken:
                    taken.add((cid, slot))
                    return (slot, slottype)
            return None

        def has_slot(cid: int) -> bool:
            return any((cid, slot) not in taken
                       for slot, _st in avail_slots.get(cid, []))

        def _soon(cid: int) -> bool:
            # 该料还有 ≤2 tick 就出炉（在炉里，快到了）。用于「主炉让位」判据：
            #   2★ 实测 4.025h 唯一空出的主炉给了多余的 1h 王翦，而白起/信陵君
            #   缺的 3 张孙膑 4.05h（下一 tick）就到 → 关键 4h 卡被顶到 5.025h，
            #   顶层跟着晚 1h。把「即将到货」视同「在箱」，判据才看得见它们。
            for f in ws.furnaces:
                if f.cid == cid and 0 < f.endtime - ws.now <= 2 * TICK_SECS + EPS:
                    return True
            return False

        # ---- 箱预算（买卡闸；精炼腾出的位在 pass 2 汇入）----
        # Ⅴ 类约束：炼成卡落箱失败会被静默丢弃（get_refined_card 先 pop 炉再
        # _add_to_box，箱满即丢卡）→ 必须给在炉卡留落位，口径同贪心 Step5。
        # ★ 只保留「即将收卡」的炉位（endtime-now <= 1 tick）：旧版
        # reserve=len(furnaces)+2 把所有在炉卡的未来落位全预留——4 炉全忙
        # 长时任务（顶层 42.5h）时 reserve=6 吃光 box_free（60 格实测
        # box_free=6 恰被 reserve=6 清零）→ reactive 买卡全停、头 op 永远
        # 发不出 → cursor 冻结、系统饿死（done=0/4）。落箱安全性不受影响：
        # 精炼净腾 2 格（3 喂料出箱/1 成品落箱），只需保证 endtime 那一 tick
        # 箱有位；长时炉的产物远未到收卡时刻，提前几十 tick 占位纯属过虑。
        soon = sum(1 for f in ws.furnaces
                   if 0 < f.endtime - ws.now <= TICK_SECS + EPS)
        # ★ 2026-10-26 真机死锁（用户报「买 10 面把卡箱占满、取不出 40 面」）：
        #   `soon` 只数「**还没**到点」的炉；已到点（endtime <= now）却因箱满取不出的
        #   卡完全不计 → reserve=0 → 买卡预算拉满 → 箱越买越满 → 收卡永远失败 →
        #   炉位不释放 → 箱里的 10 面烧不掉 → 箱永远满。真机连续几十 tick 全是
        #   「您的卡片箱满了」而引擎一次都没腾箱。
        #   有卡已到点待收时，必须留 2 格取卡位（真机取卡要 2 格空位）。
        overdue = sum(1 for f in ws.furnaces if f.endtime - ws.now <= EPS)
        # 落箱预留：**只按「到点临近度」留**，不再「有炉就留 2 格」。
        # ★★ 2026-10-26 真机 1★ 再次实测（第二次踩同一个坑）：
        #   4 炉在炼、剩余 656~2275s（远未到点），change_free=2，
        #   旧式 reserve = soon(0) + 2 = 2 → 买卡预算 0 → **整 tick 零动作**
        #   （bought_now=0、refined_now=0），箱里囤着 687×4 + 688×3 共 7 张
        #   10 面却缺 684/685/686 一张都凑不出配方 → 用户看到「买了 7 张
        #   10 面还没开炼」。
        #   物理事实：只有 endtime 落在本 tick 的炉才会落箱，落 1 张占 1 格；
        #   2000s 后才到点的炉现在预留格位是纯浪费，而且它正是死锁点
        #   （change_free <= 2 时预算恒 0）。
        #   抖动容错：真机 tick 可能因网络超过 45s，对**下一 tick 内**会到点
        #   的炉再留 1 格，别的不留。
        # ★ 真机取卡要 **2 个**换卡箱空位（2026-10-26 实测原文：「亲，可能会出现
        # 幸运事件，请空出两个卡箱的位置，再取卡！」）。但**不在预算里预留 2 格**：
        # 实测 reserve = soon*2 会让 3★/4★ 直接 750h 卡死（预算被吃光 → 不买料 →
        # 不投炉 → 更腾不出位，恶性循环）。取卡失败的真机语义是「拒绝取卡、炉不
        # 消失、卡不丢」，下一 tick 重试即可（1★ 实测 2 次连拒后成功）。
        # 故：预留仍按 1 格/炉，取卡位不足交给执行层重试。
        near = sum(1 for f in ws.furnaces
                   if TICK_SECS + EPS < f.endtime - ws.now <= 2 * TICK_SECS)
        reserve = soon + (1 if near else 0)
        if overdue:
            # 只在「真有卡到点待收」时才抬到 2 —— 与历史 `reserve=soon*2`（让 3★/4★
            # 750h 卡死）不同：那时是无条件按炉数预留，这里只认「已经该收却收不到」。
            reserve = max(reserve, 2)
        # ★ 买卡闸必须用**换卡箱**空位（2026-10-26，仿真/生产统一后实测发现）：
        # 买卡只能落到换卡箱（硬顶 18 格），保险箱不能收买卡。旧版用双箱合计
        # （ws.box_free = change_free + store_free）→ 保险箱还剩 199 格时仍判「有位」，
        # 于是每 tick 发 6 张买卡，执行层 `_free_change(st) <= 0` 全部失败；
        # 更糟的是这些**幽灵买卡**已计入 bought_this_tick，pass 2 判喂料
        # 「本 tick 已被占用」→ 所有精炼被 blocked → 一张都不炼 → 换卡箱永久 18/18、
        # cursor 冻结（生产路径实测 1★/2★ 在 900 tick 内零进展，resolves=63 反复重解）。
        # reserve 同理按换卡箱算：炼成卡也落换卡箱。
        box_free_local = max(0, ws.change_free + collect_freed + freed
                             - album_bought - reserve)
        used_main = 0
        steal_avail = ws.steal_free
        n_ref = n_buy = n_emerg = n_early = 0
        disc0 = plan["discrepancy"]

        # ============ pass 1: 买卡 reactive + JIT 门（扫描到就买，不等计划时刻）============
        # ★ JIT 门（2026-10-25 紧箱根治）：喂料底卡只买到 due（可炼前沿需求）
        # ——旧版无门按箱预算把整套 buy op 一次性买齐 → 囤货（4★ 峰值 201/218、
        # 94% 底卡）→ 紧箱 60 格箱被囤到 60/60 → 买卡闸 box_free-reserve 吃光
        # → 头 op（buy）永发不出 → cursor 冻结饿死（done=0/4、槽占 21.5%，
        # 同代码两轨迹分化 93.6%/21.5%）。in_sys = 箱内 + 炉内 + 本 tick 在途；
        # < due 才买（即买即用）。纯收藏底卡（非喂料卡）无门——无精炼消费者，
        # 由专辑预买/计划终局 op 按 collect 在即拉起。op 不标记 issued：
        # 前沿需求后续增长（上层随中间卡落箱逐层获得）时再放行。
        buy_cap = max(0, len(ws.furnaces) * 3 + 6 - album_bought)
        # ★ 底卡买卡额度 = 本 tick **能吸收**的炼炉数 × 3（2026-10-26 真机实测）：
        # 底卡只能投主炉（偷炉只收 40 面），所以「没有空主炉/没有马上空出的主炉」
        # 时买的底卡 100% 只是躺在箱里——这就是用户说的囤卡。
        # 实测形态：4 炉在炼（剩 656~2275s）仍买 7 张 10 面，箱里 687×4+688×3
        # 缺 684/685/686 凑不出任何一炉。加额度后「买一组（≤3 张）就够开一炉」。
        # 额度 = max(1, 空主炉 + 本 tick 空出) × 3。下限 1 波（3 张 = 一炉的量）：
        # 炉全满时仍允许补齐「下一炉」，否则炉一空就要现等 LAG（仿真实测
        # 严格 0 额度 4★ 78.71→82.15h、放宽到 3 tick 反而 83.41h——「提前买」
        # 在这套引擎里无益，与「利用率不是目标」的既有结论一致，只保底 1 波）。
        soon_main = sum(1 for f in ws.furnaces
                        if not f.steal
                        and 0 < f.endtime - ws.now
                        <= self.prebuy_look * TICK_SECS + EPS)
        # ★ 2026-10-27 真机现象（用户：「买了超过 4 个 10 面还没开炼」，换卡箱里
        #   躺着 8 张 10 面而 4 个 40 面炉还要 20~95 分钟才出）—— 但把这 8 张
        #   归因到本额度是错的：本额度峰值只给 1 波（3 张），8 张里多出来的来自
        #   `_album_prebuy`/collect 的**整套上交底卡**预买（已在那三处收紧成
        #   「非底卡全在箱才买」，见 4b28aac）。此处的 3 张是「下一炉的料」。
        #   pass 2 的缺料补买不吃这个额度（缺料 ⇒ 有炉位 ⇒ abs_slots>0，
        #   且另给 base_quota_emerg 保底），不会把「有炉位但缺料」也闸死。
        abs_slots = self._main_cap(ws) + soon_main
        # ★★ 2026-10-27 消融（`tests/_probe_prebuy_ab.py`，18/200、单套 1 轮、45s/tick）：
        #   | 档 | 炉满不预买 | 保底一波 | 提前 2 tick+保底 |
        #   | 2★ | 23.12h | 23.07h | — |
        #   | 3★ | 74.33h/炼44 | 72.10h/炼42 | 76.19h/炼45 |
        #   | 4★ | 78.96h/炼44 | 81.47h/炼44 | 81.46h/炼47 |
        #   ① 「提前买」(向前多看 1 tick) 两档**都更慢** → 提前量无益，此前把
        #     「3★ +2.2h」定性成「必要代价」是错的：那 2.2h 只来自「炉满时手里
        #      有没有一波料」。② 保底一波在 3★ 赚 2.2h（炉占 83.9% vs 81.4%，
        #      且**少炼 2 次**——料断档时 DP 会绕冤枉路），在 4★ 亏 2.5h
        #      （4★ 瓶颈是格位而非缺料，炉占 92% 本就没空转余量）。
        #   取 3★ 侧（1★~3★ 是主战场，且「紧箱囤卡」的真机根因是 album 整套
        #   底卡预买，已在 `_album_prebuy`/collect 收紧，与喂料额度无关）。
        #   炉满时留在箱里的这一波 = **下一炉的料**，不是囤卡：买来就是为了投炉，
        #   没有它炉空出来要白等 1 tick 物流。
        base_quota = max(3, abs_slots * 3)
        if self.prebuy_floor is None:
            pass
        elif not self.prebuy_floor:
            base_quota = abs_slots * 3       # ablation：强制不保底（3★ 病态档 74.33h）
        base_quota_emerg = max(3, base_quota)   # 应急补料闸（有炉位缺料时至少一组）
        n_base = 0
        # ---- 预买目标：只备「骨架头几炉」的**配方缺口**，绝不按骨架买整套 buy op ----
        # 旧引擎 `_prepare_turn` 的 need_buy = 这一炉（3 张不同卡）缺的底卡，
        # 买几张用几张、买完立刻投炉。DP 执行层买来的卡要下一 tick 重解才知道
        # slot（不能同 tick 投），所以这里提前备「头几炉」：
        #   波数 = base_quota / 3 = 本 tick 能吸收的炉数（无空炉 → 0 → 完全不预买），
        #   每波取该 refine op 的 feeders 缺口并集 → 买来的必然凑得成一炉。
        # ★ 反例（2026-10-26 真机）：旧版按骨架顺序遍历**全部** buy op，买的是
        #   buy op 的原始顺序、可能全是同一张（684×3），也不保证这一 tick 投得
        #   出去 → bought=636 张 / refined=20 次，箱里堆满 10 面却凑不出任何配方
        #   （用户原话：「买了超过 4 个 10 面还没开炼」）。
        jit_need: Dict[int, int] = {}
        # ★ n_wave 的标定（唯一实测口径，别凭感觉放宽）：
        #   due_window(=min(2(主炉+偷炉)+2, max(2, change_cur//4))，18 格 → 4)
        #   2★ 23.06h / 3★ 72.09h / 4★ 81.46h；
        #   放到 2×due_window(8) → **3★ 277h done=0 死锁**（多预买 → 箱满 → 头 op
        #   发不出 → 零动作）；收到 base_quota//3(1~3) → 2★ 27.02h 退化（买不上料、
        #   炉空转）。故取 due_window：紧箱场景「多买」与「少买」两侧都有悬崖。
        n_wave = max(due_window, base_quota // 3)
        got = 0
        for j in range(plan["cursor"], len(ops)):
            if issued[j] or ops[j]["kind"] != "refine":
                continue
            mj = self._owner[ops[j]["cid"]]
            for f in mj.feeders(ops[j]["cid"]):
                if f in self._base_cids:
                    jit_need[f] = jit_need.get(f, 0) + 1
            got += 1
            if got >= n_wave:
                break
        # ★★ 2026-10-27 buy_free（用户定调「买卡不该算到单独的解」）：solver 里
        #   底卡即时可得，ops 里**没有** buy op → 这里直接按 jit_need（头 n_wave 炉
        #   的配方缺口）即时补料，买多少用多少。base_quota 仍是「投得出去的量」的
        #   上限（炉满 → 只保一波），箱空位仍是硬约束。
        if self.buy_free and not self.buy_keepop:
            _need = jit_need
            if not _need and buy0_map:
                # ★★ 2026-10-27 修（`tests/_probe_bf_idle.py` 实锤）：ops 里没有
                #   refine（只剩「买底卡」这件事）时，`jit_need` 从 refine op 反推
                #   出来就是空的 → 一张不买 → 空主炉干等（85 三套实测：t=20.0h
                #   起 ops=0 且 0 动作，连续 2.0h 才恢复）。
                # ★★ 2026-10-27 再修（`_probe_multi_bf` 矩阵实锤）：旧门栏用
                #   「炉全空（`not any(pending)`）」当「可以买整套收藏底卡」的判据
                #   —— 炉空只说明没料，不代表卡都齐了。结果兜底把 `buy0_map`
                #   （整套收藏底卡，10+ 张）按 `base_quota` 一波波买进箱 → 囤卡
                #   （85 两轮：买 104→128 张、炼 25→30、15.07→21.05h，慢 40%）。
                #   改按用户铁律：**非底卡全在箱（只差底卡就能上交）** 才买那套
                #   缺的底卡，各买 1 张 —— 与 `_album_prebuy` 同判据、同粒度。
                _lack: Dict[int, int] = {}
                for t in (active or ()):
                    m = self.models[t]
                    if not all(box_stock.get(c, 0) >= 1
                               for c in m.ids if not m.is_base(c)):
                        continue
                    for c in m.ids:
                        if not m.is_base(c):
                            continue
                        if (box_stock.get(c, 0) + pending.get(c, 0)
                                + bought_this_tick.get(c, 0)) < 1:
                            _lack[c] = _lack.get(c, 0) + 1
                if _lack:
                    _need = _lack
                    self.buy_fallback += 1
            for cid in sorted(_need, key=lambda c: (-_need[c], c)):
                if n_buy >= buy_cap or box_free_local <= 0 or n_base >= base_quota:
                    break
                have = (box_stock.get(cid, 0) + pending.get(cid, 0)
                        + bought_this_tick.get(cid, 0))
                want = _need[cid] - have
                while (want > 0 and n_buy < buy_cap and box_free_local > 0
                       and n_base < base_quota):
                    actions.append(Action(kind="buy", cid=cid, n=1,
                                         note=f"DP即时买底料 {cid}"))
                    bought_this_tick[cid] += 1
                    box_free_local -= 1
                    n_base += 1
                    n_buy += 1
                    want -= 1
        for i, op in enumerate(ops):
            if self.buy_free and not self.buy_keepop:
                break                   # 买卡已由 jit_need 通道处理（ops 里无 buy）
            if n_buy >= buy_cap or box_free_local <= 0:
                break                   # 速率闸 / 箱闸（紧急买也进不来）
            if issued[i] or op["kind"] != "buy":
                continue
            cid = op["cid"]
            if plan["bought"][cid] >= plan["planned_buy"][cid]:
                issued[i] = True        # 专辑预买/应急买已买到 → 去重跳过
                continue
            if cid in self._feeder_cids:
                # ★★ 2026-10-27 移植旧引擎铁律（auto_refine 顶部注释 2026-09-16）：
                #   「**就地为每一炉补买，绝不预先囤货** —— 只有『这一炉马上要入炉』
                #    的底卡才买，买几张用几张，没有任何一炉会用到时一张都不买。」
                #   判据从「全套前沿 due」换成「头 n_wave 炉的配方缺口 jit_need」：
                #   ① 不在头几炉配方里 → 买了也没有炉能吸收（纯囤）；
                #   ② 这一波配方在箱/在炉/在途已够 → 不超买；
                #   ③ 同一 tick 同一 cid 最多 1 张（一炉只需每种 1 张，买 684×3
                #      永远凑不出一炉——配方要的是 3 张**不同**底卡）。
                if cid not in jit_need:
                    self.jit_blocked += 1
                    if os.environ.get("MC_DBG_BUYBLOCK"):
                        print(f"[buyblk] t={ws.now:.0f} cid={cid} 不在jit_need "
                              f"(n_wave={n_wave} got={got}) jit={dict(jit_need)} "
                              f"box={box_stock.get(cid,0)} pend={pending.get(cid,0)}",
                              flush=True)
                    continue
                in_sys = (box_stock.get(cid, 0) + pending.get(cid, 0)
                          + bought_this_tick.get(cid, 0))
                if in_sys >= jit_need[cid]:
                    self.jit_blocked += 1
                    if os.environ.get("MC_DBG_BUYBLOCK"):
                        print(f"[buyblk] t={ws.now:.0f} cid={cid} 在箱已够 "
                              f"in_sys={in_sys} need={jit_need[cid]}", flush=True)
                    continue
                if n_base >= base_quota:
                    self.jit_blocked += 1
                    if os.environ.get("MC_DBG_BUYBLOCK"):
                        print(f"[buyblk] t={ws.now:.0f} cid={cid} base_quota "
                              f"{n_base}>={base_quota}", flush=True)
                    continue            # 无炉可投 → 买来只能躺箱（囤卡）
                if bought_this_tick.get(cid, 0) >= 1:
                    self.jit_blocked += 1
                    if os.environ.get("MC_DBG_BUYBLOCK"):
                        print(f"[buyblk] t={ws.now:.0f} cid={cid} 本tick已买过",
                              flush=True)
                    continue
                n_base += 1
            actions.append(Action(kind="buy", cid=cid, n=1,
                                 note=f"DP骨架买底料 {cid}"))
            plan["bought"][cid] += 1
            bought_this_tick[cid] += 1
            box_free_local -= 1
            issued[i] = True
            n_buy += 1

        # ============ pass 2: 精炼时序门 ============
        # ★ 本 tick 买入的料：给一个负号虚拟槽塞进 avail_slots，让 pass 2 能把它当
        #   喂料排上（执行层买完重读状态、按 cid 现查真实槽位）。虚拟槽不参与
        #   `_box_compaction`（那一步已在上面跑完）。
        if self.buy_sametick:
            _virt = 0
            for _c, _n in bought_this_tick.items():
                for _ in range(_n):
                    _virt += 1
                    avail_slots.setdefault(_c, []).append((-_virt, 0))
        i = plan["cursor"]
        scanned = 0
        first_refine = True             # 头动作（首个精炼）才允许提前发
        protected: set = set()          # 被阻塞动作的在箱喂料（后续不得消费）

        def avail_count(cid: int) -> int:
            """箱内该卡可用张数（未被 protected 征用）。"""
            if cid in protected:
                return 0
            return sum(1 for slot, _st in avail_slots.get(cid, [])
                       if (cid, slot) not in taken)

        def early_ok(idx: int, cid: int) -> bool:
            """有限偏差③（提前发）：op 未到期但喂料在箱、有空炉 → 现在就发。

            动机（tid=59 实测）：计划因机器投影把 op 排得很晚，现实里喂料已在箱、
            炉位空着（专辑预买/量化把箱填在计划投影之前）→ 严格执行 start 门会
            把炉子闲死（1165/1215 空转 tick 属此类）。按骨架**顺序**提前发只改
            时机不改序，是最轻型的微调。
            安全条件：
              1) 喂料按重数在箱（未被 protected / 本 tick 占用）；
              2) 有空炉（主炉优先；40 面可偷炉）；
              3) 预留：箱内每种喂料 ≥ 本 op 需求 + 所有「未到期且未发的前序 op」
                 对该料的需求（前序 start 更早/相等，不能抢它们的料）；
              4) 只发 refine（买卡不提前——提前买只占箱位，无收益）。
            """
            m = self._owner[cid]
            feeders = m.feeders(cid)
            my_need = Counter(feeders)
            # 3) 预留未到期前序 op 的喂料
            reserved: Counter = Counter()
            for j in range(plan["cursor"], idx):
                if issued[j]:
                    continue
                opj = ops[j]
                if opj["kind"] != "refine":
                    continue
                if opj["start"] <= elapsed + EPS:
                    continue      # 已到期未发 = 被卡，其喂料已在 protected
                reserved.update(Counter(self._owner[opj["cid"]].feeders(opj["cid"])))
            for f, k in my_need.items():
                if avail_count(f) < k + reserved.get(f, 0):
                    self._dbg.setdefault("喂料不足:" + str(f), 0)
                    self._dbg["喂料不足:" + str(f)] += 1
                    return False
            # 2) 空炉
            if used_main < main_cap:
                return True
            if steal_avail > 0 and m.is_40(cid):
                return True
            self._dbg.setdefault("无炉位", 0)
            self._dbg["无炉位"] += 1
            return False

        while i < len(ops) and scanned <= self.scan_window:
            op = ops[i]
            if issued[i]:
                i += 1
                continue
            if op["kind"] == "buy":
                i += 1
                continue               # 买卡已在 pass 1 处理（透明跳过）
            _t_dbg = round(elapsed / 3600, 3)
            early = False
            _not_due = op["start"] > elapsed + EPS
            if not self.order_only and _not_due:
                if first_refine and early_ok(i, op["cid"]):
                    # 头动作提前发（有限偏差③）。仅限头动作——实测：放过窗口内
                    # 任意未到期 op 提前发会占掉关键 op 的计划炉位（75.49→79.46h）。
                    early = True
                else:
                    break               # 未到期（start 有序，后面更晚）
            first_refine = False
            scanned += 1
            cid = op["cid"]
            m = self._owner[cid]
            feeders = m.feeders(cid)
            miss_buy: List[int] = []        # 缺且可紧急买的底卡
            blocked = False
            for f in feeders:
                # 开关关：本 tick 买的料一律不能当喂料（旧行为）；开关开：已给虚拟槽，
                # 只要槽没被 protected/taken 就能投（买完即投）。
                _excl = (f in bought_this_tick) and not self.buy_sametick
                if _excl or f in protected or not has_slot(f):
                    blocked = True
                    if (self._owner[f].is_base(f) and pending.get(f, 0) < 1
                            and not _excl and f not in protected):
                        miss_buy.append(f)
            # ★ 2026-10-28 纯顺序模式下的早发安全检查：
            #   绕过 start 门后，若直接发炉就会**抢走未到期前序 op 的喂料**（`early_ok`
            #   的 reserved 段本就是为了防这个，旧路径只有头动作才走到它）。实测漏掉
            #   这一段：2★ 炼次 20→22（多炼 2 次、仍慢 0.97h）、3★ −0.65→+1.35、
            #   4★ 收益 −4.06→−2.06。检查不过就按「计划内等待」静默跳过。
            _pre_ok = (not _not_due) or early_ok(i, cid)
            # ★ 2026-10-28 早发只给「长链」开口子（`MC_ORDER_ONLY` 时生效）：
            #   提前生产本身要付库存代价 —— 产出的中间层在箱里躺着，同时占炉占箱。
            #   长链（关键路径长）早开能直接缩短完工；短链早开纯粹是堆库存。实测支持：
            #   4★（每条链都长、箱紧）−3.41h；2★/3★（短链多、ready 集宽）+0.97/+1.35h。
            #   判据用骨架自己算的 cp（向上关键路径），阈值取 refine op 的 cp 中位数。
            _cp_ok = (not _not_due) or (op.get("cp", 0.0) >= plan.get("cp_thr", 0.0))
            # ⚠ 2026-10-28 已证伪并删除：「早发给到期头动作留 1 个炉」
            #   （`main_free-used_main < 2` 就不早发）。理由看似充分 —— 早发占满炉 →
            #   头动作到期无炉 → 重解把它往后排 —— 但实测把 4★ 的收益全毁掉并引入
            #   错误（4★ 78.06→82.10h 且 err=30、1★ +1.00h err=6）。原因：提前开满炉
            #   本身就是 4★（深链紧箱）收益的来源，留炉 = 把炉闲死。
            if not blocked and _pre_ok and _cp_ok:
                # ★★ 2026-10-28 修：**先拿炉位，再占喂料槽**。
                #   旧版先对全部 feeders 调 take_slot（把 (cid,slot) 记进 taken），
                #   再判断炉位；若炉位不够就 continue —— **这些槽位没有归还**。
                #   结果是本 tick 内后续 op 的 `has_slot(f)` 全部为假，明明箱里
                #   有料却被判「缺料」→ 同一 tick 的其余炉位全空转。
                #   这属于纯粹的记账 bug（动作没发出，料物理上没动）。
                # ★★「主炉只给不可偷炉的卡」：40 面卡（is_40）可以走偷炉，
                #   而 4h/顶层卡只能占主炉。若把最后一个主炉给了 40 面卡，随后
                #   到期且料齐的不可偷炉 op 就没炉可上 —— 2★ 实测：4.02h 王翦/白起/
                #   李牧 一次占满 3 个主炉，信陵君（4h，只能主炉）等到 5.025 才开，
                #   4h 炉 5.025→9.025，顶层西施跟着从 8.062 推到 9.025（完工 +0.97h）。
                #   判据：主炉余量 ≤ 「后面到期且料齐的不可偷炉 op 数」→ 40 面卡
                #   先用偷炉；偷炉也没有就让位（自己连料都不占，直接向后扫）。
                if (m.is_40(cid) and main_cap - used_main > 0
                        and self.main_reserve):
                    _need_main = 0
                    # 视野按**时间**而非数组下标：ops 里 buy/refine 交错，只看
                    # scan_window 个下标常漏掉 4~8 个 op 之后就到期的主炉卡
                    # （2★ 实测：王翦只看 6 个下标只数到白起、漏掉信陵君 → 让位判断失效）。
                    _horizon = elapsed + max(2 * TICK_SECS, 3600.0)
                    for j in range(i + 1, len(ops)):
                        opj = ops[j]
                        if opj["start"] > _horizon:
                            break         # start 有序，后面更晚
                        if issued[j] or opj["kind"] != "refine":
                            continue
                        cj = opj["cid"]
                        if self._owner[cj].is_40(cj):
                            continue
                        if all((f not in protected
                                and (has_slot(f) or _soon(f)))
                               for f in self._owner[cj].feeders(cj)):
                            _need_main += 1
                    if main_cap - used_main <= _need_main:
                        if steal_avail > 0:
                            steal_avail -= 1
                            picks = []
                            for f in feeders:
                                sl = take_slot(f)
                                picks.append((f, sl[0], sl[1]))
                            for _c, _s, _st in picks:
                                box_free_local += 1
                            _fc = ([_c for (_c, _s, _st) in picks]
                                   if any(_s < 0 for (_c, _s, _st) in picks) else [])
                            actions.append(Action(
                                kind="refine", target=cid,
                                feeders=[(s, st) for (_c, s, st) in picks],
                                feed_cids=_fc, steal=True,
                                note=f"DP精炼 {cid}（偷炉·让主炉）"))
                            self._dirty = True
                            issued[i] = True
                            n_ref += 1
                            if self.dbg_op:
                                self._oplog.append((_t_dbg, i, cid, "偷炉让主炉"))
                            i += 1
                            continue
                        if self.dbg_op:
                            self._oplog.append((_t_dbg, i, cid, "让主炉待命"))
                        i += 1
                        continue
                steal = False
                if used_main < main_cap:
                    used_main += 1
                    picks = []
                    for f in feeders:
                        sl = take_slot(f)
                        picks.append((f, sl[0], sl[1]))
                elif steal_avail > 0 and m.is_40(cid):
                    steal_avail -= 1
                    steal = True
                    picks = []
                    for f in feeders:
                        sl = take_slot(f)
                        picks.append((f, sl[0], sl[1]))
                else:
                    if self.dbg_op:
                        self._oplog.append((_t_dbg, i, cid, "无炉位"))
                    for f in feeders:        # 无炉位：保护喂料，向后扫描
                        if f in avail_slots:
                            protected.add(f)
                    i += 1
                    continue
                for _c, _s, _st in picks:
                    box_free_local += 1     # 喂料出箱 → 空位 +1（旧版误减）
                note = f"DP精炼 {cid}{'（偷炉）' if steal else ''}"
                if early:
                    note += "（提前发）"
                # 含虚拟槽（本 tick 刚买）→ 执行层必须按 cid 现查真实槽位
                _fc = ([_c for (_c, _s, _st) in picks]
                       if any(_s < 0 for (_c, _s, _st) in picks) else [])
                actions.append(Action(
                    kind="refine", target=cid,
                    feeders=[(s, st) for (_c, s, st) in picks],
                    feed_cids=_fc,
                    steal=steal,
                    note=note))
                self._dirty = True          # 投出一炉 = 骨架推进 → 下 tick 重解
                issued[i] = True
                n_ref += 1
                if early:
                    n_early += 1
                if self.dbg_op:
                    self._oplog.append((_t_dbg, i, cid, "偷炉" if steal else "开炉"))
                i += 1
                continue
            # ---- 阻塞 ----
            if early or _not_due:
                # early：提前发试探失败（喂料/炉位在二次检查时被占）→ 静默跳过。
                # _not_due（仅纯顺序模式可达）：op 未到期且当前投不了 = **计划内的
                #   等待**（料还没轮到 / 炉还没空），不是「物流失败」。此处若落到
                #   下面的应急买，就会把「还没轮到」误判成「运输出问题」（2★ 实测
                #   买卡 87→109、慢 2.99h）。也不计 discrepancy —— 未到期等待不是
                #   计划与现实的偏差，计了会假触发 disc_over 重解、打乱 DP 顺序。
                # ★★ 2026-10-28 修：**不得 protected**。protected 的语义是「已到期
                #   但投不了的 op 锁住自己的料，防止被抢」；未到期的 op 没有锁料权利，
                #   它只是该等。先前给这里加 protected → cursor 附近 7 个 op 互相把
                #   箱内料锁死 → 能发的 op 也发不出 → 每 tick 空转（实测 ORDER 日志
                #   从 38 行暴涨到 5066 行、2★ +0.97h、3★ +1.35h）。前序料由
                #   `early_ok` 的 reserved 段预留（按 start 覆盖 cursor..idx），够用。
                if _not_due and self.dbg_op:
                    self._oplog.append((_t_dbg, i, cid, "未到期跳过",
                                        tuple(miss_buy)))
                i += 1
                continue
            if (miss_buy
                    and box_free_local >= len(miss_buy)
                    and n_base + len(miss_buy) <= base_quota_emerg
                    and n_buy + n_emerg + len(miss_buy) <= buy_cap):
                # 有限偏差①：物流失败（缺底卡且骨架无到期买卡）→ 就地补买这一炉
                # 缺的底卡，下 tick 落箱即可投炉（本 tick 只买不炼 = LAG）。
                # ★ 2026-10-27：这是喂料底卡的**应急**买入点，与 pass 1 的「按配方组
                #   预买」互补。实测若把它改成「缺料不买、继续往后扫能发的 op」
                #   （旧引擎阶段一语义），4★ 78.08→89.09h（多炼 5 次、打乱 DP 顺序），
                #   故保留「缺料即买 + break」，扫描式两阶段在 DP 上不成立。
                for f in miss_buy:
                    if f in bought_this_tick:
                        continue        # 同 tick 同一 cid 只买 1 张
                    actions.append(Action(kind="buy", cid=f, n=1,
                                         note=f"DP应急买底料 {f}"))
                    bought_this_tick[f] += 1
                    box_free_local -= 1
                    plan["bought"][f] += 1
                    n_base += 1
                n_emerg += len(miss_buy)
                plan["discrepancy"] += len(miss_buy)
                self.emergency_total += len(miss_buy)
                if self.dbg_op:
                    self._oplog.append((_t_dbg, i, cid, "应急买", tuple(miss_buy)))
                break
            # 有限偏差②：等炉/等复合料 → 保护其在箱喂料，窗口内向后扫描。
            # 计 discrepancy：紧箱场景「等料扫描」高频是计划与现实偏差的信
            # 号——60 格·各2轮 实测：discrepancy 累计驱动 disc_over 重解
            # 正是系统生机（禁掉后槽占 93.6%→3.6%、进度冻结）；CPU 代价由
            # relax 0.5s + 小实例快解算摊薄（10000tick 墙钟 578s）。
            if self.dbg_op:
                self._oplog.append((_t_dbg, i, cid, "等料打不开",
                                    tuple(miss_buy), tuple(feeders)))
            for f in feeders:
                if f in avail_slots:
                    protected.add(f)
            plan["discrepancy"] += 1
            i += 1
        # ---- 死锁边缘兜底：本 tick 零精炼、但有空炉 + 箱内 trio 可炼 ----
        # 箱闸死锁（60 格·各2轮 实测 done=0/4、槽占 21.5%）：cursor 卡在
        # 成堆发不出的 buy op 上（box_free 被 reserve 吃光），pass 2 的
        # scan_window 全被「缺底卡的 refine」占满，而箱内 trio（如 157×6）
        # 对应的 refine op 在窗口外 → 每 tick 零动作饿死。此时全量扫第一
        # 个「到期 + 喂料齐 + 有空炉」的 refine op 发掉：消耗 trio 净腾 2
        # 格 → 箱闸松 → buy 恢复流动。语义安全：发的是计划内合法 op（只改
        # 时机不改序），每 tick 最多 1 个（防打乱 DP 顺序），记 discrepancy。
        if n_ref == 0 and n_emerg == 0:
            for j in range(plan["cursor"], len(ops)):
                opj = ops[j]
                if issued[j] or opj["kind"] != "refine":
                    continue
                if not self.order_only and opj["start"] > elapsed + EPS:
                    break           # 未到期（start 有序，后面更晚）
                mj = self._owner[opj["cid"]]
                feedersj = mj.feeders(opj["cid"])
                if not feedersj:
                    continue
                if any(f in protected or not has_slot(f) for f in feedersj):
                    continue
                if used_main >= main_cap:
                    if not (steal_avail > 0 and mj.is_40(opj["cid"])):
                        break       # 无炉位（后续 op 同样无炉）
                picks = []
                slots_ok = True
                for f in feedersj:
                    sl = take_slot(f)
                    if sl is None:
                        slots_ok = False
                        break
                    picks.append((f, sl[0], sl[1]))
                if not slots_ok:
                    continue
                used_main += 1
                for _c, _s, _st in picks:
                    box_free_local += 1
                actions.append(Action(
                    kind="refine", target=opj["cid"],
                    feeders=[(s, st) for (_c, s, st) in picks],
                    steal=False, note=f"DP精炼 {opj['cid']}（箱闸兜底）"))
                self._dirty = True
                issued[j] = True
                n_ref += 1
                plan["discrepancy"] += 1
                break               # 每 tick 最多兜底 1 个
        while plan["cursor"] < len(ops) and issued[plan["cursor"]]:
            plan["cursor"] += 1
        self.discrepancy_total += plan["discrepancy"] - disc0
        # ---- 卡死统计（骨架过时信号）----
        head_due = (plan["cursor"] < len(ops)
                    and ops[plan["cursor"]]["start"] <= elapsed + EPS)
        if head_due and n_ref == 0 and n_buy == 0 and n_emerg == 0:
            # 等待性卡住不算 streak：喂料正在炉里炼（即将落箱）或本 tick
            # 刚买（在途 LAG）→ JIT 正常物流延迟。只有「喂料既不在箱、不在
            # 炉、也不在途」的真短缺才累积。2026-10-24 紧箱 60 格实测：旧口径
            # 把 20+ tick 的等料当骨架过时（streak_limit=4 → 500tick 重解 69
            # 次、solve 占满 139s 墙钟）；=20 又让 4★ 单套 incumbent 退化
            # （78.81→84.14h，重解时机敏感）→ 必须按等待语义精准过滤。
            head_op = ops[plan["cursor"]]
            waiting = False
            if head_op["kind"] == "refine":
                for f in head_op["feeders"]:
                    if f in bought_this_tick:
                        waiting = True
                        break
                    for fr in ws.furnaces:
                        if fr.cid == f and fr.endtime <= ws.now + 2 * TICK_SECS:
                            waiting = True
                            break
                    if waiting:
                        break
            if not waiting:
                plan["streak"] += 1
        else:
            plan["streak"] = 0
        self.streak_max = max(self.streak_max, plan["streak"])
        self.opportunistic_total += n_early
        return {"refined": n_ref, "bought": n_buy, "emergency": n_emerg,
                "early": n_early}

    def decide(self, ws: WorldState, active: List[int],
               completed_rounds: Counter, max_rounds: int = 0,
               credits: dict | None = None
               ) -> Tuple[List[Action], dict]:
        actions: List[Action] = []
        box_stock = ws.stock
        pending = ws.pending

        # ---- 骨架缓存（active 套集合 / 箱容变化才重建 + memo 作废）----
        sig = tuple(sorted(active))
        box_cap = (ws.change_cur or 0) + (ws.store_cur or 0)
        if sig != self._skel_sig or box_cap != self._skel_box_cap:
            self._skel = self._skeleton(active)
            self._skel_sig = sig
            self._skel_box_cap = box_cap
            self._memo = {}
            self._plan = None

        # ---- 净需求（非底卡炼几次 / 底卡买几张）----
        need0_map, buy0_map = self._union_need0(box_stock, pending, active,
                                                completed_rounds, max_rounds,
                                                credits=credits,
                                                sets_fn=self.sets_fn,
                                                free_main=self._main_cap(ws),
                                                box_free=ws.change_free)

        # ---- 1) collect：整套在箱即上交（先于买卡，复用贪心语义）----
        # ★ collect_freed 只做统计，**不进买卡预算**（2026-10-26）：
        # 生产链路上交是 tick 第 3 步做的，`_dp_refine_all` 对 DP 返回的
        # collect 直接 continue（见 auto_refine.py:1241）。所以“这里存在一个
        # 可上交的整套” ≠ “本 tick 能腾出 N 格”——那些卡还实实在在占着箱位，
        # 把 len(m.ids) 记进预算就是幽灵空位，买卡必失败（4★ 实测 220 次
        # 「卡箱 0 空位」）。真正的释放量已体现在 ws.change_free 里。
        collect_freed = 0      # 进买卡预算的可释放量（见上：生产不能本 tick 上交 → 恒 0）
        collect_n = 0          # 本 tick「整套已在箱」的张数，仅作统计/日志
        for t in active:
            m = self.models[t]
            if m.complete_in_stock(box_stock):
                actions.append(Action(kind="collect", tid=t, target=m.top_cid,
                                     giftid=m.giftid, note="DP:凑齐整套→上交"))
                collect_n += len(m.ids)

        # ---- 1b) 专辑底卡预买（collect 优先级策略；执行层按计划去重）----
        bought_this_tick: Counter = Counter()   # cid -> 本 tick 已买（含在途）
        self._album_prebuy(ws, active, box_stock, pending, actions,
                           bought_this_tick, extra_free=collect_freed)

        # ---- 2) 无需求（等 collect / 等炉内到货）→ 不解算、不执行 ----
        if not need0_map and not buy0_map:
            self._plan = None
            return actions, {"skip_solve": True, "reason": "no_demand",
                             "n_types": self._skel["n"]}

        # ---- 3) 承诺点判定：仅在这些情况重解（低频），否则复用骨架 ----
        rounds_sig = tuple(sorted(completed_rounds.items()))
        plan = self._plan
        # ★ 空计划（只有底卡要买、没有 refine 可排）时，状态一变就必须重解：
        #   buy_free 下 buy op 已不在 ops 里，这种计划的 ops 长度 = 0 → 既不会
        #   exhausted（加了 len>0 守卫）也不会 head_due → 不重解就永远冻结
        #   （实测 85 三套：t=1201min（20.0h）起 nops=0，连续 2.0h 零动作）。
        #   用「箱内 + 在炉」签名做判据：只有真实事件（买卡落箱/出炉）才变，
        #   不会退化成每 tick 空解。
        state_sig = (tuple(sorted(box_stock.items())),
                     tuple(sorted((c, n) for c, n in pending.items() if n)),
                     tuple(sorted((f.cid, round(f.endtime)) for f in ws.furnaces)))
        empty_stale = (plan is not None and not plan["ops"]
                       and plan.get("state_sig") != state_sig)
        exhausted = (plan is not None and len(plan["ops"]) > 0
                     and plan["cursor"] >= len(plan["ops"]))
        # disc_over 不带退避：60 格·各2轮 实测高频重解（按当前库存重新投影）
        # 正是紧箱生机——禁掉后槽占 93.6%→3.6%、进度冻结。仅 streak 走退避
        # （纯等待信号，物理短缺下重解无新信息）；CPU 代价靠 relax 0.5s 摊薄。
        disc_over = (plan is not None
                     and plan["discrepancy"] > max(12, len(plan["ops"]) // 4))
        by_streak = plan is not None and plan["streak"] >= self.streak_limit
        streak_over = by_streak and self._no_progress_resolves < self.streak_resolve_cap
        if (plan is None or self._plan_rounds_sig != rounds_sig
                or exhausted or empty_stale or disc_over or streak_over
                or (self.event_resolve and self._dirty
                    and (self._left_rounds or 0) >= 2)
                or self.solve_every_tick):
            plan, stats = self._solve_plan(ws, active, need0_map, buy0_map,
                                           box_cap, self._skel, rounds_sig)
            plan["state_sig"] = state_sig
            self._plan = plan
            self._plan_rounds_sig = rounds_sig
            self.resolves += 1
            if self._dirty:
                self._dirty = False
                self.dirty_resolves += 1
            if by_streak:
                self._no_progress_resolves += 1
            else:
                self._no_progress_resolves = 0
        else:
            stats = {"skip_solve": True, "n_types": self._skel["n"]}

        # ---- 3.5) 专辑预买记入计划去重（防计划同卡买卡 op 重买 / 超买）----
        for cid in bought_this_tick:
            plan["bought"][cid] += 1

        # ---- 4) 有限偏差列表调度（两趟：买卡 reactive + 精炼时序门）----
        run_stats = self._run_plan(ws, plan, self._skel, actions,
                                   bought_this_tick,
                                   sum(bought_this_tick.values()),
                                   collect_freed, box_stock, pending, active,
                                   buy0_map=buy0_map)
        # 真实进展 = 本 tick 有真实精炼产出（n_ref>0）。**买卡/应急不算**——
        # 紧箱场景几乎每 tick 都有零星 reactive 买卡，若据此清零退避等于没
        # 退避（实测 500tick streak 重解 66 次 > cap 16）；**cursor 微推进
        # 也不算**——「重解一次挤出一个 op」正是要防的级联蠕动形态。
        if run_stats["refined"]:
            self._no_progress_resolves = 0
        stats = dict(stats)
        stats.update({"plan_ops": len(plan["ops"]), "cursor": plan["cursor"],
                      "streak": plan["streak"],
                      "discrepancy": plan["discrepancy"],
                      "resolves": self.resolves,
                      "refined_now": run_stats["refined"],
                      "bought_now": run_stats["bought"],
                      "emergency_now": run_stats["emergency"],
                      "early_now": run_stats["early"],
                      "jit_blocked_total": self.jit_blocked,
                      "compact_sold_total": self.compact_sold,
                      "compact_moved_total": self.compact_moved,
                      "collect_in_stock_n": collect_n,
                      "box_relaxed": plan["box_relaxed"]})
        return actions, stats
