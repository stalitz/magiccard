"""DP 每 tick 控制器：把 DP 作为**调度控制器**，每个 tick 用真实 WorldState 重解，
取最优调度里 start_time==0 的动作执行。DP 即控制器 → 静态/滚动 gap 消失。

状态空间（有限离散，类型级聚合，sched_dp 同构）：
  · 每类非底卡 c：need0[c]（净需求，按**箱内+炉内**持有量净减）、
    stock[c]（箱内持有，作为即时可用喂料）、running_rel[cid,剩余]（炉内）。
  · 底卡从 mats 剥离（买即来、无限供应、0 炉时——真实游戏买卡瞬时），
    建模为无限前置；买卡由「JIT 拉动 + 箱容闸门」实现（不囤货撑箱）。
  · 炉 = 3 主炉（DP 建模容量）；偷炉作 40 面专属 bonus（不进 DP 容量，引擎执行时
    在主炉满时兜底 40 面卡，绝不改变 DP 的可行性）。

相对 dp_adapter.joint_dp_order 的修正（bug fix）：
  · init_stock 只用**箱内**持有，不含炉内在炼（pending）——炉内卡正在炼、不可当喂料，
    须等其 finish（由 init_running + collect 转移处理）；旧版 `+pd` 会把在炉卡当可用
    喂料误消费，是接滚动引擎错位的根之一。

性能：跨 tick 热启动 memo（骨架不变则记忆有效）；无空闲炉时不重解（此时无可启动动作）。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Tuple

from backend.sched_cmp.sched_dp import solve
from .model import ThemeModel
from .plan import Action
from .world import WorldState


class DPTickController:
    def __init__(self, models: Dict[int, ThemeModel], machines: int = 3,
                 time_budget: float = 0.3, node_budget: int = 8_000_000):
        self.models = models
        self.machines = machines
        self.time_budget = time_budget
        self.node_budget = node_budget
        self._memo: dict = {}
        self._skel_sig: tuple = None
        self._skel: dict = None
        # cid -> owner model（各套卡库不相交，安全）
        self._owner: Dict[int, ThemeModel] = {}
        for tid, m in models.items():
            for c in m.ids:
                self._owner[c] = m
        # profiling
        self.solve_count = 0
        self.solve_max_ms = 0.0
        self.solve_total_ms = 0.0
        self.bounded_count = 0          # solve 返回 bounded=True（未证最优）的次数
        self.buy_failed = 0

    # ---- 静态骨架（类型级），active 套集合不变时复用 ----
    def _skeleton(self, active):
        types = []
        for t in active:
            m = self.models[t]
            for c in m.ids:
                if not m.is_base(c):
                    types.append(c)
        seen = set()
        ut = []
        for c in types:
            if c not in seen:
                seen.add(c)
                ut.append(c)
        idx = {c: i for i, c in enumerate(ut)}
        n = len(ut)
        dur = [self._owner[c].secs(c) for c in ut]
        mats = []
        for c in ut:
            mc = {}
            for f in self._owner[c].feeders(c):
                if (not self._owner[f].is_base(f)) and f in idx:
                    mc[idx[f]] = mc.get(idx[f], 0) + 1
            mats.append(tuple(sorted(mc.items())))
        depth = [self._owner[c].depth.get(c, 0) for c in ut]
        cp = [0] * n

        def cpf(i: int) -> int:
            if cp[i]:
                return cp[i]
            cp[i] = dur[i] + max((cpf(j) for j, _ in mats[i]), default=0)
            return cp[i]

        for i in range(n):
            cpf(i)
        return {"types": ut, "idx": idx, "n": n, "dur": dur,
                "mats": mats, "depth": depth, "cp": cp}

    def _union_need0(self, box_stock, pending, active, completed_rounds, max_rounds):
        """净需求（每张非底卡还需炼几次）。

        ★ 必须直接复用 net_plan(box, pending, sets)（与贪心 Planner 完全同口径）：
        它在**每一层**自顶向下扣减 box+pending（底层卡被高层消费后，其上层需求同步
        减少），是正确的 MRP 净需求。
        旧实现先 net_plan({},{},1) 取「全集需求」再在末尾平减一次 box+pending —— 与
        逐层扣减不等价，会**净需求被放大**（实测 cid168 2 vs 1、cid164 3 vs 2），导致
        DP 每 tick 多排精炼 → 全作业约翻倍（112 vs 55 炼）→ 耗时从 73h 涨到 119h。
        """
        total = Counter()
        for t in active:
            m = self.models[t]
            sets = (max_rounds - completed_rounds.get(t, 0)) if max_rounds else 1
            sets = max(1, sets)
            runs, _buy = m.net_plan(dict(box_stock), dict(pending), sets=sets)
            for c, nd in runs.items():
                if not m.is_base(c):        # 底卡买即用、不进 DP 合成需求
                    total[c] += nd
        return total

    def _jit_prebuy(self, ws, active, need0_map, box_stock, pending,
                    actions, bought):
        """JIT 预买底卡（与贪心 Planner Step5 同口径，2026-10-23 修 DP 慢于贪心）。

        现象：旧版 DP 控制器只在 t0 提取时买「本 tick 正在炼的精炼」的底料喂料，且其
        他无炉位时连这也不买 → 底料总是「要用才买、下 tick 才到」→ 精炼被迫顺延、炉空闲
        等待底料。贪心靠 `due` 前瞻预买（每 tick 最多 总炉数×3+6 张、箱容闸门兜底）让喂料
        提前到位，炉不空等。

        修法：本步按 net_plan 需求算 `due`（即将能炼的合成卡实例底卡喂料 + collect 在即的
        收藏底卡），轮转预买、箱容闸门防撑爆。底卡建模为无限即时供应，预买即兑现该假设，
        使 t0 提取几乎不再因缺底料顺延。与贪心 Step5 完全同口径 → DP 炉占向贪心对齐。
        """
        due = Counter()
        # ① 合成卡喂料底卡：非底卡喂料全在箱/炉的合成卡 → 拉其实例底卡喂料
        for cid, nd in need0_map.items():
            if nd <= 0:
                continue
            m = self._owner[cid]
            feeders = m.feeders(cid)
            if any((not m.is_base(f)) and box_stock.get(f, 0) < 1
                   and pending.get(f, 0) < 1 for f in feeders):
                continue
            for f in feeders:
                if m.is_base(f):
                    due[f] += 1
        # ② 收藏底卡：该套合成卡全在箱（只差底卡即可 collect）补缺口
        for t in active:
            m = self.models[t]
            if all(box_stock.get(c, 0) >= 1 for c in m.ids
                   if not m.is_base(c)):
                for c in m.ids:
                    if m.is_base(c):
                        gap = 1 - box_stock.get(c, 0) - pending.get(c, 0)
                        if gap > 0:
                            due[c] = max(due.get(c, 0), gap)
        # 箱容闸门：当前箱位 + collect 释放位 - 预留（在炉卡落地位）
        collect_freed = sum(len(self.models[a.tid].ids)
                            for a in actions if a.kind == "collect")
        free_after = ws.box_free + collect_freed
        reserve = len(ws.furnaces) + 2
        if free_after > reserve:
            free_after -= reserve
        else:
            free_after = 0
        buy_cap = len(ws.furnaces) * 3 + 6
        emit = {}
        for c, n in due.items():
            held = (box_stock.get(c, 0) + pending.get(c, 0)
                    + (1 if c in bought else 0))
            g = n - held
            if g > 0:
                emit[c] = g
        order_cids = sorted(emit)
        remaining = dict(emit)
        b = 0
        while b < buy_cap and free_after > 0:
            progressed = False
            for cid in order_cids:
                if remaining.get(cid, 0) <= 0:
                    continue
                if free_after <= 0 or b >= buy_cap:
                    break
                actions.append(Action(kind="buy", cid=cid, n=1,
                                     note=f"DP-JIT买底料 {cid}"))
                remaining[cid] -= 1
                free_after -= 1
                b += 1
                bought.add(cid)
                progressed = True
            if not progressed:
                break

    def decide(self, ws: WorldState, active: List[int],
               completed_rounds: Counter, max_rounds: int = 0
               ) -> Tuple[List[Action], dict]:
        actions: List[Action] = []
        box_stock = ws.stock
        pending = ws.pending

        # ---- 骨架缓存（active 套集合变化才重建，否则复用 + memo 热启动）----
        sig = tuple(sorted(active))
        if sig != self._skel_sig:
            self._skel = self._skeleton(active)
            self._skel_sig = sig
            self._memo = {}
        sk = self._skel
        idx = sk["idx"]
        types = sk["types"]

        # ---- 净需求（每张非底卡还需炼几次，套内 × 剩余轮数）----
        # 需在 collect / 预买 / 建实例前算出（预买按 due 拉底料依赖它）。
        need0_map = self._union_need0(box_stock, pending, active,
                                       completed_rounds, max_rounds)

        # ---- 1) collect：整套在箱即上交（先于买卡，复用贪心语义）----
        for t in active:
            m = self.models[t]
            if m.complete_in_stock(box_stock):
                actions.append(Action(kind="collect", tid=t, target=m.top_cid,
                                     giftid=m.giftid, note="DP:凑齐整套→上交"))

        # ---- 1b) JIT 预买底卡（与贪心 Planner Step5 同口径，2026-10-23 修 DP 慢于贪心）----
        # 放 skip-solve 检查之前：即便 3 主炉+偷炉全忙，也持续预买喂料，炉一空即有料可投。
        bought_this_tick: set = set()
        self._jit_prebuy(ws, active, need0_map, box_stock, pending, actions,
                         bought_this_tick)

        # ---- 2) 无空闲炉（主+偷）则无需重解：此时无可启动精炼 ----
        # DP 建模 3 主炉；若 3 主炉全忙且偷炉也满，直接返回（collect 已处理）。
        if ws.main_free <= 0 and ws.steal_free <= 0:
            return actions, {"skip_solve": True, "n_types": len(types)}

        # ---- 3) 建实例 + 每 tick 重解（reacting to true state，即「DP 即控制器」）----
        # 每 tick 用真实 box+pending 重解 → 始终最优序。实测（2026-10-23）：比「承诺式
        # 调度（解一次+列表调度）」更适合浅树/快卡（3★/4★）——JIT 买卡有 1 tick 物流延迟，
        # 承诺静态序会因喂料未落箱而炉空闲（3★ 88h/68.7% vs 每 tick 重解 72h/84%）；深树
        # 慢卡（5★）静态序虽再快 ~17h，但那是「全知假设」换的、且以浅树回归为代价，故取
        # 每 tick 重解的均衡形态（赢低星/多套低星，见 bench_focused）。不传 init_running
        # （实测退化到 800 万节点/53s 仍 bounded；在炉卡由 need0 净减 + 喂料在箱过滤覆盖）。
        present = {c: box_stock.get(c, 0) + pending.get(c, 0) for c in types}
        live = [c for c in types if need0_map.get(c, 0) > 0 or present.get(c, 0) > 0]
        live_idx = {c: i for i, c in enumerate(live)}
        nL = len(live)
        durL = [sk["dur"][idx[c]] for c in live]
        matsL = []
        for c in live:
            mc = {}
            for j, k in sk["mats"][idx[c]]:
                cj = types[j]
                if cj in live_idx:
                    mc[live_idx[cj]] = mc.get(live_idx[cj], 0) + k
            matsL.append(tuple(sorted(mc.items())))
        need0L = [need0_map.get(c, 0) for c in live]
        depthL = [sk["depth"][idx[c]] for c in live]
        cpL = [sk["cp"][idx[c]] for c in live]
        # ★ init_stock 只用箱内（不含炉内 pending）
        init_stockL = [box_stock.get(c, 0) for c in live]
        M = self.machines
        inst = {"machines": M, "types": live, "n": nL, "dur": durL,
                "mats": matsL, "need0": need0L, "depth": depthL, "cp": cpL,
                "init_stock": init_stockL}

        import time as _t
        t0 = _t.perf_counter()
        mk, sched, stats = solve(inst, time_budget=self.time_budget,
                                 node_budget=self.node_budget,
                                 init_stock=init_stockL,
                                 memo=self._memo)
        dt_ms = (_t.perf_counter() - t0) * 1000.0
        self.solve_count += 1
        self.solve_total_ms += dt_ms
        self.solve_max_ms = max(self.solve_max_ms, dt_ms)
        if stats.get("bounded"):
            self.bounded_count += 1
        stats = dict(stats)
        stats["n_types"] = nL
        stats["solve_ms"] = round(dt_ms, 2)

        # ---- 4) 取 t=0 动作（最优调度里 start==0 的精炼）----
        avail_slots: Dict[int, List[tuple]] = {}
        for cid, slot, slottype in ws.slots:
            if cid > 0:
                avail_slots.setdefault(cid, []).append((slot, slottype))
        taken: set = set()

        def take_slot(cid: int):
            lst = avail_slots.get(cid)
            if not lst:
                return None
            for slot, slottype in lst:
                if (cid, slot) not in taken:
                    taken.add((cid, slot))
                    return (slot, slottype)
            return None

        box_free_local = ws.box_free

        t0starts = [(live[i], s) for (i, s) in sched if abs(s) < 1e-6]
        # 长链优先（与 DP cp 一致）
        t0starts.sort(key=lambda cv: (-cpL[live_idx[cv[0]]], cv[0]))

        used_main = 0
        steal_avail = ws.steal_free

        for cid, _ in t0starts:
            m = self._owner[cid]
            feeders = m.feeders(cid)
            picks: List[tuple] = []
            ok = True
            for f in feeders:
                if f in avail_slots:                 # 在箱（含底卡）→ 取槽消耗
                    sl = take_slot(f)
                    if sl is None:
                        ok = False
                        break
                    picks.append((f, sl[0], sl[1]))
                else:
                    # 喂料不在箱（底卡未落箱 / 复合卡在炉或缺失）：本 tick 顺延，
                    # 下 tick 重解 reacting（底料由 1b 预买提前拉好，落箱即可投）。
                    ok = False
                    break
            if not ok:
                for cid2, slot2, _st2 in picks:
                    taken.discard((cid2, slot2))
                continue
            # 全部喂料已在箱 → 精炼（主炉优先；满则 40 面走偷炉 bonus）
            steal = False
            if used_main < ws.main_free:
                used_main += 1
            elif steal_avail > 0 and m.is_40(cid):
                steal_avail -= 1
                steal = True
            else:
                for cid2, slot2, _st2 in picks:
                    taken.discard((cid2, slot2))
                continue                                # 无炉位 → 本 tick 不炼（喂料在箱，下 tick 再投）
            for _c, _s, _st in picks:
                box_free_local -= 1
            actions.append(Action(
                kind="refine", target=cid,
                feeders=[(slot, slottype) for (_c, slot, slottype) in picks],
                steal=steal,
                note=f"DP精炼 {cid}{'（偷炉）' if steal else ''}"))

        return actions, stats
