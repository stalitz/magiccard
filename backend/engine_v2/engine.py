"""编排层：Engine 对外门面 + 忠实仿真循环（run_sim）。

run_sim 复用 _sim_faithful.VirtualSession 作忠实游戏后端（不联网、状态真解析），
每个 tick：推进虚拟时钟 → 收回到时卡炉 → 解析成 WorldState → Planner.decide →
Executor.execute → 统计。这正是「新引擎能否正确调度」的闭环验证。

接真机时：把 GameClient 换成对接 mc_api.Session 的适配器（本次不接），
run_sim 换成 run_live（每 45s 真实 tick + 重登/重读），Engine 本体不变。
"""
from __future__ import annotations

import re
from collections import Counter

from .executor import Executor
from .model import ThemeModel
from .planner import Planner
from .world import WorldState


class Engine:
    def __init__(self, tids, buy: bool = True, surplus_sets: int = 0,
                 max_rounds: int = 0, steal_enabled: bool = True,
                 dp_single: bool = True):
        self.models = {int(t): ThemeModel(t) for t in tids}
        self.queue = [int(t) for t in tids]
        self.buy = buy
        self.surplus_sets = surplus_sets
        self.max_rounds = max_rounds
        self.completed_rounds: Counter = Counter()
        self.planner = Planner(steal_enabled=steal_enabled)
        self.executor = Executor()
        # DP 开关：单套高星（star>=3）走 DP 最优调度；多套走联合 DP（2026-10-23）；
        # 低星单套 / 无限炼仍走贪心。
        self.dp_single = dp_single
        self._dp_sched = None          # DP 精炼优先序（单套或联合，全局 cid 列表）
        self._dp_round = -1            # 已解算对应的轮次（滚动重规划：轮变重解）
        self._dp_stats = None          # 最近一次 sched_dp 统计
        self.stats = {"refined": 0, "bought": 0, "buy_fail": 0,
                      "collected": 0, "errors": 0, "busy_ticks": 0, "total_ticks": 0,
                      "slot_busy_ticks": 0, "slot_total_ticks": 0,
                      "peak_change_used": 0, "peak_store_used": 0,
                      "peak_box_used": 0}
        # VirtualSession 的 EP_COLLECTION_ADD 需要 ar.plans[tid].ids / .giftid
        self.plans = {t: m.tp for t, m in self.models.items()}

    def decide(self, ws: WorldState, dp_plan: list = None, queue: list = None):
        q = self.queue if queue is None else queue
        plan = self.planner.decide(ws, self.models, q,
                                   self.completed_rounds, self.surplus_sets,
                                   dp_plan=dp_plan)
        if not self.buy:                      # 不允许买 → 剥离买卡动作
            plan = [a for a in plan if a.kind != "buy"]
        return plan

    async def run_sim(self, change_cur: int = 18, store_cur: int = 200,
                     max_ticks: int = 20000, interval: int = 45):
        from backend._sim_faithful import VirtualSession
        from backend.auto_refine import parse_full

        vs = VirtualSession(change_cur=change_cur, store_cur=store_cur)
        vs.ar = self
        for n in range(max_ticks):
            vs.now += interval
            # 收卡：把到时的炉内卡取回箱（与真机「每 tick 收卡」一致）
            for slot, stt in list(vs.stoves.items()):
                if vs.now >= stt["endtime"]:
                    await vs.get_refined_card(slot)
            st = parse_full(vs._serialize())
            ws = WorldState.from_parsed(st, now=vs.now)
            # 峰值箱位占用（验证不溢出 / Ⅴ 类约束）
            cu = sum(ws.change.values())
            su = sum(ws.store.values())
            self.stats["peak_change_used"] = max(self.stats["peak_change_used"], cu)
            self.stats["peak_store_used"] = max(self.stats["peak_store_used"], su)
            self.stats["peak_box_used"] = max(self.stats["peak_box_used"], cu + su)
            # ---- 单套已达标的套退出调度（2026-10-22 修）：旧版只查「全部达标」才停，
            #      已达标的套会继续炼下一轮 → 占炉/囤箱、拖慢未达标套，还虚增
            #      bought/refined 统计（实测 2套·各3轮：4★ 被多炼 40 次 = 部分第4轮）----
            active = [t for t in self.queue
                      if not (self.max_rounds and
                              self.completed_rounds.get(t, 0) >= self.max_rounds)]
            if not active:
                return vs, n + 1
            # ---- 单套高星：DP 精炼顺序。滚动重规划（2026-10-22）：轮次边界
            #      （completed_rounds 变化 = 库存已变）带真实持有量重解；轮内不重解
            #      （每 tick 重解 2s 太贵；列表调度对静态序已近优）。
            #      多套联合 DP 接线实验（2026-10-23，回应「你是不是在反对 DP」）：
            #      先重做了被推翻两条论证的公平对比——同 3 炉（禁偷炉）下联合 DP
            #      静态解 2套 532.7h / 3套 595.7h（均证完最优，253/315 节点 0.1/0.3s），
            #      比同炉数引擎（540.9/602.9）快 7~8h。旧 reject 论证①「3 炉 DP 界
            #      595.7h > 4 槽引擎 467.8h」是拿被砍偷炉的 DP 跟完整引擎赛跑，不公
            #      平；②「56+ 类型 2s 解不动」是事实错误。但接线实测：滚动引擎全面
            #      变慢（2套 411.5→433.6h、3套 459.9→500.2h，炉占 -5~8pt、峰值箱
            #      +35~56）——DP 的 8h 静态优势是全知假设换的，滚动现实（JIT 物流
            #      延迟、跨套料争抢、轮内序过时）把它放大成 22~40h 劣势。已回退，
            #      联合序代码与探针留在 dp_adapter.joint_dp_order / probe_fair_dp.py，
            #      后续可试「更频重解 / DP 序仅作 tie-break / 物流延迟建进 DP 模型」。
            dp_plan = None
            if self.dp_single and len(active) == 1:
                tid = active[0]
                if self.models[tid].star >= 3:
                    cr = self.completed_rounds.get(tid, 0)
                    if self._dp_sched is None or cr != self._dp_round:
                        self._dp_round = cr
                        from .dp_adapter import dp_order
                        self._dp_sched, self._dp_stats = dp_order(
                            self.models[tid], sets=1, machines=3,
                            stock=dict(ws.stock), pending=dict(ws.pending))
                    dp_plan = self._dp_sched
            plan = self.decide(ws, dp_plan=dp_plan, queue=active)
            results = await self.executor.execute(plan, vs)
            self._tally(results)
            # 炉利用率抽样：
            #  · busy_ticks/total_ticks = 本 tick 是否有≥1 炉在炼（非槽填满率）
            #  · slot_busy/slot_total  = 真实炉位填满率（按建模的 3主+1偷=4 槽）
            occ = len(vs.stoves)
            self.stats["total_ticks"] += 1
            if occ > 0:
                self.stats["busy_ticks"] += 1
            self.stats["slot_busy_ticks"] += occ
            self.stats["slot_total_ticks"] += (len(vs.MAIN_SLOTS) + 1)
            if self.max_rounds and all(
                    self.completed_rounds.get(t, 0) >= self.max_rounds
                    for t in self.queue):
                return vs, n + 1
        return vs, max_ticks

    def _tally(self, results):
        for a, ok, detail in results:
            if a.kind == "refine":
                self.stats["refined"] += 1
                if not ok:
                    self.stats["errors"] += 1
            elif a.kind == "collect":
                if ok:
                    self.completed_rounds[a.tid] += 1
                    self.stats["collected"] += 1
                else:
                    self.stats["errors"] += 1
            elif a.kind == "buy":
                mm = re.search(r"成功(\d+)", detail)
                succ = int(mm.group(1)) if mm else 0
                mf = re.search(r"失败(\d+)", detail)
                fail = int(mf.group(1)) if mf else 0
                self.stats["bought"] += succ
                self.stats["buy_fail"] += fail
