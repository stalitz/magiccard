"""决策层：Planner.decide() 五步流水线（纯函数，无网络、无副作用）。

顺序铁律（见架构文档 §4）：
  ① PriorityResolver 先定 P0 → ② AdmissionControl 按 P0 预留准入
  → ④ BoxCompaction（箱紧腾位：卖多余10面底卡 / 挪保险箱，绝不卖高值）
  → ④a collect 先于 buy（Ⅴ 类：条件性动作先于条件性买卡）
  → ③ ScheduleStrategy 时间感知 + **按套公平分配炉位**（修一套独占饿死另一套）
  → ⑤ 买卡只在末尾、容量校验、只买底卡、near-ready 节流（防囤卡撑爆箱）。

关键修复（相对旧引擎 / 上一版）：
  · 空炉 bug：每个 tick 都对「当前库存 + 在炼」重算净需求并填满空闲炉，
    只要任何 feeder  trio 就绪就投炉，不再因为 round2 净需求算空而永久空转。
  · 提前买死锁（Ⅴ 类）：buy 动作一律排在 collect / 合成之后，且只在
    「当前箱位 + collect 释放位」够用时才生成，绝不溢出、绝不买重复底卡。
  · 一套独占饿死（多套公平性）：上一版把全部空闲炉位按「全局最深优先」分给
    P0 套，导致长卡（如 4★93h / 5★184h）占满 4 炉、另一套顶层永远凑不齐。
    本版改为**按套 round-robin 公平分配**：每个准入套至少在每轮分配里轮到一次，
    集内仍按「深层优先」走关键路径，集间不互饿。
  · 箱满丢卡（BoxCompaction）：箱将满（换卡箱 ≤2 空位 或 双箱 ≤4 空位）时，
    先卖多余 10 面底卡（保留 ≥1、绝不卖在炉中当素材的卡、绝不卖高值），
    再把换卡箱里多余的合成卡挪保险箱，给「炼成卡落箱」预留位，杜绝静默丢卡。
  · 囤卡撑爆箱（JIT 拉动买卡）：只买「即将能炼的合成卡」的实例底卡喂料 + collect
    在即的收藏底卡；depth-1 全底卡喂料首 tick 即拉动、上层随中间卡落箱逐层获得。
    2026-10-22 修：旧版把整套底卡需求一次性列入 emit_set（只限速不限总量）→ 箱里
    囤齐全部底卡等炼（4★ 峰值 166~201、94% 是底卡）→ 已改为按 due 拉动。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

from backend.auto_refine import CHANGE_FACE

from .model import ThemeModel
from .plan import Action
from .world import WorldState


class Planner:
    def __init__(self, steal_enabled: bool = True):
        self.steal_enabled = steal_enabled

    def decide(self, ws: WorldState, models: Dict[int, ThemeModel],
               queue: List[int], completed_rounds: Dict[int, int],
               surplus_sets: int = 0, dp_plan: list = None) -> List[Action]:
        actions: List[Action] = []
        stock = ws.stock
        pending = ws.pending

        # ============ Step 1 — PriorityResolver（反转优先级，先定 P0） ============
        p0 = queue[0]
        for tid in queue:
            if models[tid].top_cid in stock:
                p0 = tid
                break

        # ============ 每套净需求 ============
        plans: Dict[int, tuple] = {}
        for tid in queue:
            m = models[tid]
            sets = surplus_sets if (surplus_sets and surplus_sets > 0) else 1
            runs, buy = m.net_plan(stock, pending, sets=sets)
            plans[tid] = (m, Counter(runs), dict(buy))

        # ============ Step 2 — AdmissionControl（多套并行：凡有活即准入）============
        # 铁律修正（3套实测死锁根因）：旧版按 sum(runs) 给 P0 静态预留箱位，但
        # **精炼净腾 2 格**（耗 3 喂料 / 落 1 张）根本不长期占箱 → 预留了个不存在的
        # 压力，把其余套全 defer；而 P0 自己缺底卡又炼不动 → 全局停转（[60,59,62]
        # 完成 1 轮后卡死 74553 tick）。箱位安全本应由 Step5 买卡节流 + 精炼净负
        # 占用保证，不该用 run 计数在准入处静态卡人。现在：只要该套还有净需求
        # （runs>0）或顶层已在箱（可 collect）就准入；炉位优先级交 Step3 Pivot 排序。
        admitted = [p0]
        for tid in queue:
            if tid == p0:
                continue
            m, runs, buy = plans[tid]
            if sum(runs.values()) > 0 or m.top_cid in stock:
                admitted.append(tid)

        # 槽位索引（feeder 的物理位置）
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

        # ============ JIT 需求（due）：即将能炼的合成卡 → 其实例底卡喂料 ============
        # 2026-10-22 峰值虚高根因修复：旧版 Step5 把 net_plan 的整套底卡需求
        # （4★ 高达 176 张）一次性列入 emit_set，只限每 tick 买速、不限总量 →
        # 箱里早早囤齐全部底卡等炼（实测峰值 166~201 张、其中 94% 是底卡），
        # 这是仿真假象。真实现场底卡即买即用，箱峰值 = 合成卡等待队列
        # （4★ 理论 26 张）+ 少量在途底卡，与「套内 distinct 卡数」同量级。
        # 拉动规则：① 非底卡喂料全在箱/炉的合成卡（即将能炼）→ 按实例买其底卡喂料；
        #          ② 纯收藏底卡 → 仅当该套合成卡全在箱（collect 在即）才按缺口补买。
        # depth-1 喂料全底卡 → 首 tick 即拉动 → 上层随中间卡落箱逐层获得，天然 JIT。
        due: Dict[int, int] = {}
        for t in admitted:
            m, runs, buy = plans[t]
            for cid in runs:
                feeders = m.feeders(cid)
                if any((not m.is_base(f)) and stock.get(f, 0) < 1
                       and pending.get(f, 0) < 1 for f in feeders):
                    continue                    # 非底卡喂料未齐 → 这张还炼不了
                for f in feeders:
                    if m.is_base(f):
                        due[f] = due.get(f, 0) + 1
            # 纯收藏底卡：该套合成卡全部在箱（只差底卡即可 collect）才补缺口
            if all(stock.get(c, 0) >= 1 for c in m.ids if not m.is_base(c)):
                for cid, n in buy.items():
                    if m.is_base(cid):
                        gap = n - stock.get(cid, 0) - pending.get(cid, 0)
                        if gap > 0:
                            due[cid] = max(due.get(cid, 0), gap)

        # ============ Step 4 — BoxCompaction（箱紧腾位，先于 collect/refine 执行） ============
        actions += self._box_compaction(ws, models, admitted, plans, due)

        # ============ Step 4a — 先 collect（Ⅴ 类：确定性动作先于条件性买卡） ============
        collect_freed = 0
        for tid in admitted:
            m, _runs, _buy = plans[tid]
            if m.complete_in_stock(stock):
                actions.append(Action(kind="collect", tid=tid,
                                     target=m.top_cid, giftid=m.giftid,
                                     note="凑齐整套→上交(先于买卡)"))
                collect_freed += len(m.ids)

        # ============ Step 3 — 炉位分配 + 投炉 ============
        stock_avail = Counter(stock)        # 随投炉递减（本 tick 箱内可用）
        free_main = ws.main_free
        free_steal = ws.steal_free

        if dp_plan is not None and len(queue) == 1:
            # ---- 单套高星：DP 优先级列表调度（买/collect/压实仍走通用逻辑）----
            # dp_plan = 按 DP 最优 start time 升序的 cid 列表；本步只决定「现在投哪几张」：
            # 对已存在（箱 or 炉中）的卡跳过，对其余按 DP 优先序、feeder 就绪即投炉。
            t = queue[0]
            m = models[t]
            for cid in dp_plan:
                if stock.get(cid, 0) + pending.get(cid, 0) >= 1:
                    continue                    # 已有一张（箱/炉），无需再炼
                feeders = m.feeders(cid)
                picks: List[tuple] = []
                used: List[tuple] = []          # [(cid, slot)] 供半途失败回滚
                ok = True
                for f in feeders:
                    # 底卡同样占箱位、同样被炼卡消耗（2026-10-22 修）：旧版在此
                    # `if m.is_base(f): continue` 跳过底卡 slot 分配 → _do_refine 收不到
                    # 底卡 slot → 底卡永不进炉、全部泄漏囤积箱中（实测 4★DP@800tick
                    # 残留 164 张未耗底卡）。与贪心分支口径对齐：三种喂料一视同仁。
                    if stock_avail.get(f, 0) < 1:
                        ok = False
                        break
                    s = take_slot(f)
                    if s is None:
                        ok = False
                        break
                    picks.append(s)
                    used.append((f, s[0]))
                    stock_avail[f] -= 1
                if not ok:
                    for f, sl in used:          # 回滚半途消耗，同 tick 后续投炉要看见
                        stock_avail[f] += 1
                        taken.discard((f, sl))
                    continue                    # feeder 未齐 → 留待后续 tick
                if free_main > 0:
                    free_main -= 1
                    steal = False
                elif free_steal > 0 and m.is_40(cid) and self.steal_enabled:
                    free_steal -= 1
                    steal = True
                else:
                    continue                    # 炉满 → 不投（绝不溢出）
                actions.append(Action(
                    kind="refine", target=cid, feeders=picks, steal=steal,
                    note=f"DP合成 {m.cards.get(cid, ['', cid])[1] if m.cards.get(cid) else cid}"
                          f"{'（偷炉）' if steal else ''}"))
        else:
            # ---- 多套 / 低星单套：贪心 + Pivot 公平分配（原逻辑）----
            # 当前每套在炉并发数（参考）
            cur_per_set = Counter()
            for f in ws.furnaces:
                if f.cid > 0:
                    for t in admitted:
                        if f.cid in models[t].comp:
                            cur_per_set[t] += 1
                            break

            # 每套待炼候选（深层优先）。排序键改动曾两度实测否决（_exp_rules.py 有全量数据）：
            # ① cp 关键路序：仅单套 3/4/5★ 有效（76/84/173 vs 82.7/95.4/184.5），
            #   多套共享箱时把单链卡前置→互抢枢纽喂料（同 tick slot 争用 ×9、完全空炉
            #   tick ×3.5，2套·各3轮 412→430h）；
            # ② 按套 DP 序（dp_orders 接线）：共享段同样负（2套 +8h、3套 +27h），
            #   且 DP 模型按 3 炉独占求解，与共享 reality 不符。
            # 正解已在 engine.run_sim：`len(active)==1` 时最后剩下的套独占资源，
            # 自动激活 DP 分支（3套·各3轮 475.8→467.8h、2套 424.9→412.2h）。
            # 多套共享段的正确杠杆是跨套分配策略（P0 反转），不是套内排序。
            ready_by_set: Dict[int, List[int]] = {t: [] for t in admitted}
            for t in admitted:
                m, runs, _buy = plans[t]
                for cid, n in runs.items():
                    ready_by_set[t].extend([cid] * n)
            for t in ready_by_set:
                ready_by_set[t].sort(
                    key=lambda c: (-models[t].depth.get(c, 0), -models[t].secs(c), c))

            # 排序：缺卡最少的套优先（Pivot——先让最接近完成的套凑齐 collect 释放空间），
            # 但每轮仍给所有套轮到一次，避免远端套被永久饿死。
            def missing_count(t: int) -> int:
                m = models[t]
                return sum(1 for c in m.ids if stock.get(c, 0) < 1)
            admitted_by_progress = sorted(admitted, key=missing_count)

            order: List[tuple] = []
            progressed = True
            while progressed:
                progressed = False
                for t in admitted_by_progress:
                    if ready_by_set[t]:
                        order.append((t, ready_by_set[t].pop(0)))
                        progressed = True

            # 精炼只受炉位限制即可：每投一张净腾 2 格（耗 3 喂料 / 落 1 张），永远不会撑爆箱，
            # 因此此处不再做箱容上限护栏（那是买卡端要管的）。
            for t, cid in order:
                feeders = models[t].feeders(cid)
                picks: List[tuple] = []
                used: List[tuple] = []          # [(cid, slot)] 供半途失败回滚
                ok = True
                for f in feeders:
                    if stock_avail.get(f, 0) < 1:
                        ok = False
                        break
                    s = take_slot(f)
                    if s is None:
                        ok = False
                        break
                    picks.append(s)
                    used.append((f, s[0]))
                    stock_avail[f] -= 1
                if not ok:
                    for f, sl in used:          # 回滚半途消耗，同 tick 后续投炉要看见
                        stock_avail[f] += 1
                        taken.discard((f, sl))
                    continue                        # 本 tick feeder 不齐 → 留待后续
                if free_main > 0:
                    free_main -= 1
                    steal = False
                elif free_steal > 0 and models[t].is_40(cid) and self.steal_enabled:
                    free_steal -= 1
                    steal = True
                else:
                    continue                        # 炉满 → 不投（绝不溢出）
                actions.append(Action(
                    kind="refine", target=cid, feeders=picks, steal=steal,
                    note=f"合成 {models[t].cards.get(cid, ['', cid])[1] if models[t].cards.get(cid) else cid}"
                          f"{'（偷炉）' if steal else ''}"))

        # ============ Step 5 — 买卡：JIT 拉动（只买 due：即将能炼的底卡喂料 + collect 在即的收藏底卡）============
        # 容量口径：当前箱位 + collect 释放位，并给在炉卡预留落地位（否则不买，杜绝买卡撑爆 + 落箱丢卡）。
        free_after = ws.box_free + collect_freed
        reserve = len(ws.furnaces) + 2
        if free_after > reserve:
            free_after -= reserve
        else:
            free_after = 0                      # 箱紧 → 本 tick 不买

        # 缺口 = due（现在就要） - 已持有（箱 + 炉）。due 在 Step4 前已按套算好。
        emit_set: Dict[int, int] = {}
        for c, n in due.items():
            held = stock.get(c, 0) + pending.get(c, 0)
            if n > held:
                emit_set[c] = n - held
        # 每 tick 买量上限：只买「够喂当前空闲炉」的底卡（每炉约 3 喂料），避免空箱时
        # 一次性买光全部需求把箱撑满、制造病态（之前多套死锁根因之一）。
        buy_cap = (ws.main_free + (ws.steal_free if self.steal_enabled else 0)) * 3 + 3
        # 底卡名查找（修旧版误用循环残留变量 t 取卡名的潜在 bug）
        cid_name: Dict[int, str] = {}
        for tt in admitted:
            for cid, card in models[tt].cards.items():
                cid_name.setdefault(cid, card[1])
        # 轮转买：每轮每种底卡各买 1 张，保持各类平衡。旧版按 cid 顺序把一种底卡
        # 一口气买到需求上限（如 30+），其他类型凑不齐 3 合 1 的 trio → 永远炼不动、
        # 箱还被囤爆（多套死锁另一根因）。平衡采购让 trio 尽快成型、被精炼及时消耗。
        order_cids = sorted(emit_set)
        remaining = dict(emit_set)
        bought = 0
        while bought < buy_cap and free_after > 0:
            progressed = False
            for cid in order_cids:
                if remaining.get(cid, 0) <= 0:
                    continue
                if free_after <= 0 or bought >= buy_cap:
                    break
                actions.append(Action(
                    kind="buy", cid=cid, n=1,
                    note=f"买底卡 {cid_name.get(cid, cid)}"))
                remaining[cid] -= 1
                free_after -= 1
                bought += 1
                progressed = True
            if not progressed:
                break

        return actions

    # ---- BoxCompaction：箱将满时腾位，杜绝炼成卡落箱失败被静默丢弃 ----
    def _box_compaction(self, ws: WorldState, models: Dict[int, ThemeModel],
                        admitted: List[int], plans: Dict[int, tuple],
                        due: Dict[int, int] = None) -> List[Action]:
        acts: List[Action] = []
        if not (ws.change_free <= 2 or ws.box_free <= 4):
            return acts
        due = due or {}

        # 在炉中当素材的卡（绝不卖/挪，避免打断进行中的合成）
        pending_feeder: set = set()
        for f in ws.furnaces:
            if f.cid > 0:
                for t in admitted:
                    if f.cid in models[t].comp:
                        pending_feeder.update(models[t].feeders(f.cid))

        # 每套净需求（runs=合成待炼 / buy=底卡待买），跨套取 max 作为「至少需持有」
        need: Dict[int, int] = {}
        for t in admitted:
            m, runs, buy = plans[t]
            for cid, n in runs.items():
                need[cid] = max(need.get(cid, 0), n)
            for cid, n in buy.items():
                need[cid] = max(need.get(cid, 0), n)

        # 1) 卖多余 10 面底卡（换卡箱里）：保留 ≥1、绝不动在炉素材、绝不卖高值
        #    保留量按 due（JIT：现在就要的量），不是 net_plan 总需求——否则箱满时
        #    也一张不敢卖（总需求 176 > 任何持有量），腾位形同虚设。
        sell_slots: List[tuple] = []
        for cid, _cnt in ws.change.items():
            if cid <= 0:
                continue
            m0 = next((models[t] for t in admitted if cid in models[t].ids), None)
            if m0 is None or not (m0.is_base(cid) and m0.price(cid) == CHANGE_FACE):
                continue
            if cid in pending_feeder:
                continue
            keep = max(1, due.get(cid, 1))
            slots = [s for (c, s, st) in ws.slots if c == cid and st == 0]
            surplus = len(slots) - keep
            for s in slots[:surplus]:
                sell_slots.append((s, cid))
        for s, cid in sell_slots:
            acts.append(Action(kind="sell", cid=cid, slot=s, slottype=0,
                               note=f"卖多余10面底卡{cid}"))

        # 2) 挪保险箱：把换卡箱里多余的「合成卡」移到保险箱，释放换卡箱位
        #    （换卡箱仅 18 格、是炼成卡落箱第一落点；保险箱 200 格，collect 同样认）
        store_free = ws.store_free
        move_slots: List[tuple] = []
        for cid, _cnt in ws.change.items():
            if cid <= 0 or store_free <= 0:
                continue
            m0 = next((models[t] for t in admitted if cid in models[t].ids), None)
            if m0 is None or m0.is_base(cid):
                continue                    # 底卡已在上面处理
            if cid in pending_feeder:
                continue
            keep = max(1, need.get(cid, 1))
            slots = [s for (c, s, st) in ws.slots if c == cid and st == 0]
            surplus = len(slots) - keep
            for s in slots[:surplus]:
                move_slots.append((s, cid))
                store_free -= 1
        for s, cid in move_slots:
            acts.append(Action(kind="move", cid=cid, slot=s, slottype=0,
                               note=f"挪保险箱{cid}"))
        return acts
