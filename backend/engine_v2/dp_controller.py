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

from backend.sched_cmp.sched_dp import solve
from .model import ThemeModel
from .plan import Action
from .world import WorldState

TICK_SECS = 45.0        # 生产/仿真 tick 粒度（买卡物流 LAG = 1 tick）
EPS = 1e-9


class DPTickController:
    def __init__(self, models: Dict[int, ThemeModel], machines: int = 3,
                 time_budget: float = 2.0, node_budget: int = 8_000_000,
                 lag_secs: float = TICK_SECS, shop_cap: int = 15,
                 streak_limit: int = 4, scan_window: int = 6,
                 steal_enabled: bool = True,
                 cp_first: bool = False, cp_budget: float = 120.0):
        self.models = models
        self.machines = machines
        self.time_budget = time_budget
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
        self.lag_secs = float(lag_secs)     # 买卡物流延迟（1 tick）
        self.shop_cap = int(shop_cap)       # 每 tick 买卡并发上限
        # 头动作连续被卡 tick 数 → 骨架过时。配合 streak 判定的「等待过滤」
        # （喂料在炉即将落箱/本 tick 已买 = JIT 物流等待，不累积），4 tick
        # 足够灵敏——2026-10-24 前无过滤版在紧箱 60 格把等料当过时触发重解
        # 级联（500tick solves 69 次）。
        self.streak_limit = streak_limit
        self.scan_window = scan_window      # 有限偏差向后扫描窗口
        self.steal_enabled = bool(steal_enabled)   # 偷炉可规划（class 2）
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
        self.compact_sold = 0           # 腾箱卖卡累计张数（只卖 10 面底卡）
        self.compact_moved = 0          # 腾箱挪保险箱累计张数
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
                     max_rounds):
        """净需求：(非底卡炼几次, 底卡买几张)。

        直接复用 net_plan(box, pending, sets)（与贪心 Planner 完全同口径）：
        每一层自顶向下扣减 box+pending，是正确的 MRP 净需求。底卡需求取 net_plan
        的 buy 字典（含集卡册收藏需求 + 喂料需求，已净减持有）。
        """
        need, buy = Counter(), Counter()
        for t in active:
            m = self.models[t]
            # 每解只排「下一轮」（task#96 紧箱各2轮 FAIL 根治）：
            # 原 sets = max_rounds - completed_rounds[t] 会把多轮卡一次性全排进计划，
            # 两轮中间卡叠加占满箱 → collect 永不触发 → rounds_sig 不变 → 永不重解 →
            # 永久饿死（实测 done=0/4、箱满 60/60、槽占仅 22%）。改为每解只排下一轮，
            # 第一轮完成后 completed 变化触发重解下一轮。
            sets = 1
            runs, buys = m.net_plan(dict(box_stock), dict(pending), sets=sets)
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
        reserve = len(ws.furnaces) + 2
        free = max(0, ws.box_free + extra_free - reserve)
        for t in active:
            m = self.models[t]
            if not all(box_stock.get(c, 0) + pending.get(c, 0) >= 1
                       for c in m.ids if not m.is_base(c)):
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
                if f in base_cids:
                    due[f] = due.get(f, 0) + 1
        # ---- collect 缺口（不走窗口：只 1 张/套，天然有界）----
        for t in (active or ()):
            m = self.models.get(t)
            if m is None:
                continue
            if not all(box_stock.get(c, 0) + pending.get(c, 0) >= 1
                       for c in m.ids if not m.is_base(c)):
                continue                # 合成卡未齐（含在炉）→ collect 不在即
            for c in m.ids:
                if not m.is_base(c):
                    continue
                gap = 1 - box_stock.get(c, 0) - pending.get(c, 0)
                if gap > 0:
                    due[c] = max(due.get(c, 0), gap)
        return due

    def _box_compaction(self, ws, plan, due, actions, avail_slots):
        """腾箱（BoxCompaction，同贪心 Step4 口径）：箱紧时卖多余 10 面底卡
        + 挪多余合成卡进保险箱，给 JIT 买卡 / 落箱腾位。返回卖卡腾出的
        双箱合计空位（挪箱不改变合计，只腾换卡箱位）。

        触发：change_free <= 2 或 box_free <= 4（同贪心）。安全边界：
        ① 只卖 is_base 且 price==10 的底卡（铁律：高面值卡永不自动卖；
          10 面卡 sell_card 只换回面值等额金币，卖+买无损，绝不动 40 面
          素材与合成卡—— tid=709 底卡 57 面也不卖，保守）；
        ② 保留 max(1, due) 张（JIT 前沿需求，不是总需求——否则箱满时
          一张不敢卖，腾位形同虚设）；卖超的卡同步回减 plan["bought"]
          （计划去重账），后续需求增长可经计划 op / 应急买重新拉起；
        ③ 挪箱只挪「超出未发 op 喂料需求」的合成卡，目标箱要有空位
          （预测 sim 的首空位规则，同步修 avail_slots 防悬空槽位）。
        每次腾箱记 1 点 discrepancy（计划与现实偏差信号 → disc_over 重解）。
        """
        if not (ws.change_free <= 2 or ws.box_free <= 4):
            return 0
        n_sold = n_moved = 0
        # ---- 1) 卖多余 10 面底卡（换卡箱 + 保险箱；保留 due）----
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
            keep = max(1, due.get(cid, 0))
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
        # ---- 2) 挪多余合成卡进保险箱（保留未发 op 的喂料需求）----
        if ws.store_free > 0 and ws.change_free <= 2:
            need: Dict[int, int] = {}
            for j, op in enumerate(plan["ops"]):
                if plan["issued"][j] or op["kind"] != "refine":
                    continue
                for f in self._owner[op["cid"]].feeders(op["cid"]):
                    need[f] = need.get(f, 0) + 1
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
                keep = max(1, need.get(cid, 0))
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
                store_used.add(dest)
                store_free_local -= 1
                change_of[cid] -= 1
                n_moved += 1
        if n_sold or n_moved:
            self.compact_sold += n_sold
            self.compact_moved += n_moved
            plan["discrepancy"] += 1       # 腾箱 = 计划与现实偏差信号
        return n_sold

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
        t0 = _t.perf_counter()
        mk = sched = stats = None
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
            mk, sched, stats = solve(inst, time_budget=self.time_budget,
                                     node_budget=self.node_budget,
                                     init_stock=init_stockL, init_running=running,
                                     memo=self._memo)
        # 箱容兜底：回退 UB 可能超容（DP 搜不到可行解）→ relax 重解。
        # ⚠️ relax 必须用全新 memo：箱容约束下的 INF/有限值会毒化无约束重解
        # （LB 剪枝与箱容无关仍有效，但「落箱超容」分支在 relax 后变可行）。
        # relax 只要一个可行上界（_ub_schedule 初始 incumbent 本就是合法
        # 列表调度）→ 预算砍到 min(0.5s, 全预算)：2026-10-24 紧箱实测每次
        # 承诺点「有约束 2s + relax 2s」= 4s，500tick 墙钟 139s 里 solve 占
        # 139s；relax 半秒后 incumbent 不变（UB 兜底），墙钟对半砍。
        if self._box_peak(inst, sched, sum(init_stockL)) > box_cap + EPS:
            inst["box_cap"] = None
            mk, sched, stats = solve(inst, time_budget=min(0.5, self.time_budget),
                                     node_budget=self.node_budget,
                                     init_stock=init_stockL, init_running=running,
                                     memo=None)
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
                ops.append({"kind": "buy", "cid": c, "start": st_tick})
                planned_buy[c] += 1
            else:
                ops.append({"kind": "refine", "cid": c, "start": st_tick,
                            "feeders": list(self._owner[c].feeders(c))})
        ops.sort(key=lambda o: (o["start"], 0 if o["kind"] == "buy" else 1,
                                -cpL[live_idx[o["cid"]]], o["cid"]))
        plan = {"ops": ops, "issued": [False] * len(ops), "cursor": 0,
                "solved_at": float(ws.now), "rounds_sig": rounds_sig,
                "planned_buy": planned_buy, "bought": Counter(),
                "streak": 0, "discrepancy": 0, "makespan": mk,
                "n_types": nL, "box_relaxed": bool(stats.get("box_relaxed"))}
        return plan, stats

    def _run_plan(self, ws, plan, sk, actions, bought_this_tick,
                  album_bought: int, collect_freed: int,
                  box_stock, pending, active=None):
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
        due_window = 2 * (self.machines + (1 if self.steal_enabled else 0)) + 2
        due = self._jit_due(ws, plan, box_stock, pending, due_window, active)
        freed = self._box_compaction(ws, plan, due, actions, avail_slots)

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
        reserve = soon + 2
        box_free_local = max(0, ws.box_free + collect_freed + freed
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
        for i, op in enumerate(ops):
            if n_buy >= buy_cap or box_free_local <= 0:
                break                   # 速率闸 / 箱闸（紧急买也进不来）
            if issued[i] or op["kind"] != "buy":
                continue
            cid = op["cid"]
            if plan["bought"][cid] >= plan["planned_buy"][cid]:
                issued[i] = True        # 专辑预买/应急买已买到 → 去重跳过
                continue
            if cid in self._feeder_cids:
                in_sys = (box_stock.get(cid, 0) + pending.get(cid, 0)
                          + bought_this_tick.get(cid, 0))
                if in_sys >= due.get(cid, 0):
                    self.jit_blocked += 1
                    continue            # 前沿用量已够 → 不超买（op 保留）
            actions.append(Action(kind="buy", cid=cid, n=1,
                                 note=f"DP骨架买底料 {cid}"))
            plan["bought"][cid] += 1
            bought_this_tick[cid] += 1
            box_free_local -= 1
            issued[i] = True
            n_buy += 1

        # ============ pass 2: 精炼时序门 ============
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
            if used_main < ws.main_free:
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
            early = False
            if op["start"] > elapsed + EPS:
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
                if f in bought_this_tick or f in protected or not has_slot(f):
                    blocked = True
                    if (self._owner[f].is_base(f) and pending.get(f, 0) < 1
                            and f not in bought_this_tick and f not in protected):
                        miss_buy.append(f)
            if not blocked:
                picks = []
                for f in feeders:
                    sl = take_slot(f)
                    picks.append((f, sl[0], sl[1]))
                steal = False
                if used_main < ws.main_free:
                    used_main += 1
                elif steal_avail > 0 and m.is_40(cid):
                    steal_avail -= 1
                    steal = True
                else:
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
                actions.append(Action(
                    kind="refine", target=cid,
                    feeders=[(s, st) for (_c, s, st) in picks],
                    steal=steal,
                    note=note))
                issued[i] = True
                n_ref += 1
                if early:
                    n_early += 1
                i += 1
                continue
            # ---- 阻塞 ----
            if early:
                # 提前发试探失败（喂料/炉位在二次检查时被占）→ 静默跳过，
                # 不计偏差（本就没到计划时刻）。
                i += 1
                continue
            if miss_buy and box_free_local >= len(miss_buy):
                # 有限偏差①：物流失败（缺底卡且骨架无到期买卡）→ 应急补买，
                # 下 tick 落箱即可投炉。本 tick 只买不炼（LAG）。
                for f in miss_buy:
                    actions.append(Action(kind="buy", cid=f, n=1,
                                         note=f"DP应急买底料 {f}"))
                    bought_this_tick[f] += 1
                    box_free_local -= 1
                    plan["bought"][f] += 1
                n_emerg += len(miss_buy)
                plan["discrepancy"] += len(miss_buy)
                self.emergency_total += len(miss_buy)
                break
            # 有限偏差②：等炉/等复合料 → 保护其在箱喂料，窗口内向后扫描。
            # 计 discrepancy：紧箱场景「等料扫描」高频是计划与现实偏差的信
            # 号——60 格·各2轮 实测：discrepancy 累计驱动 disc_over 重解
            # 正是系统生机（禁掉后槽占 93.6%→3.6%、进度冻结）；CPU 代价由
            # relax 0.5s + 小实例快解算摊薄（10000tick 墙钟 578s）。
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
                if opj["start"] > elapsed + EPS:
                    break           # 未到期（start 有序，后面更晚）
                mj = self._owner[opj["cid"]]
                feedersj = mj.feeders(opj["cid"])
                if not feedersj:
                    continue
                if any(f in protected or not has_slot(f) for f in feedersj):
                    continue
                if used_main >= ws.main_free:
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
               completed_rounds: Counter, max_rounds: int = 0
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
                                                completed_rounds, max_rounds)

        # ---- 1) collect：整套在箱即上交（先于买卡，复用贪心语义）----
        # 上交整套 = 箱位释放（同贪心 Step5 口径：collect_freed 计入买卡预算）
        collect_freed = 0
        for t in active:
            m = self.models[t]
            if m.complete_in_stock(box_stock):
                actions.append(Action(kind="collect", tid=t, target=m.top_cid,
                                     giftid=m.giftid, note="DP:凑齐整套→上交"))
                collect_freed += len(m.ids)

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
        exhausted = plan is not None and plan["cursor"] >= len(plan["ops"])
        # disc_over 不带退避：60 格·各2轮 实测高频重解（按当前库存重新投影）
        # 正是紧箱生机——禁掉后槽占 93.6%→3.6%、进度冻结。仅 streak 走退避
        # （纯等待信号，物理短缺下重解无新信息）；CPU 代价靠 relax 0.5s 摊薄。
        disc_over = (plan is not None
                     and plan["discrepancy"] > max(12, len(plan["ops"]) // 4))
        by_streak = plan is not None and plan["streak"] >= self.streak_limit
        streak_over = by_streak and self._no_progress_resolves < self.streak_resolve_cap
        if (plan is None or self._plan_rounds_sig != rounds_sig
                or exhausted or disc_over or streak_over):
            plan, stats = self._solve_plan(ws, active, need0_map, buy0_map,
                                           box_cap, self._skel, rounds_sig)
            self._plan = plan
            self._plan_rounds_sig = rounds_sig
            self.resolves += 1
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
                                   collect_freed, box_stock, pending, active)
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
                      "box_relaxed": plan["box_relaxed"]})
        return actions, stats
