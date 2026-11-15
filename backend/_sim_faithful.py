"""忠实 tick 仿真器 —— 验证 auto_refine 的真实调度逻辑，不重写任何决策代码。

做法（关键）：只替换最外层的「网络 + 时钟」，让 AutoRefine.tick() 原封不动地跑。
  · VirtualSession 模拟 QQ 服务端：维护虚拟卡箱/卡炉状态，按真实端点语义改变状态；
    EP_MAINPAGE 返回的 raw 用**真实的 parse_full / parse_user_cards** 解析，所以连
    状态解析都是真代码（不是我另写一套）。
  · 虚拟时钟：每跑一个 tick，now += interval(45s)。卡片按真实 secs(=theme_compose_list[6])
    在到时后 flag=2，下一 tick 的收卡环节自动收进箱 —— 与真机一致。
  · 变卡 / 炼卡 / 买卡 / 收卡 / 上交 / 偷炉 全部由虚拟后端按真实端点语义执行。

可加参数：
  --tid 290        （默认 290 七雄Q传，2 星 4 层）
  --gift 20        （卡箱初始塞几张 48 面礼物卡，用于触发 box_gift_change 分布变卡）
  --steal on|off   （偷炉开关，默认 on：1 个偷炉位）
  --sets N         （>0 = 固定每轮排 N 套，即 ar.surplus_sets；0 = 默认按未收集 qq秀数自动）
  --uncollected N  （seed 已收集 qq秀，使自动模式按 N 个未收集排套；0=不seed=全未收集。
                     配合 --rounds N 可精确复刻 sched_cmp 的 sets=N 实例：
                     自动模式每上交 1 轮未收集数 -1，排产目标自动收缩，不超产）
  --rounds N       （max_rounds：每套上交 N 轮后停，默认 1）
  --steady         关掉所有扰动（变卡必成功），复现旧 bench 数字用
                   默认档 = SAFE：变卡约 50% 失败（真机实测），失败也烧源卡
                   环境变量 MC_SIM_CHAOS=1 = 压力档（网络失败/提前收卡/
                   偷炉卡被好友收走/箱卡被换走），非实测值，只作鲁棒性验证
  --seed 0         随机种子
跑法：python -m backend._sim_faithful

对比口径（与 backend/sched_cmp 对齐）：--sets N --rounds N --gift 0 --steal on
  → 空箱、无变卡、3 主炉+1 偷炉=4 台机器、净需求与 sched_cmp.compute_runs(N) 同口径
  （每张套内卡含 +1 收藏需求 → 30N 个 smelt 作业）。
  循环结束打印 `=== SUMMARY {json} ===`：最后完工时刻/顶层卡各次完工/上交时刻/产量/残留。
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import random
import time
from collections import Counter
from pathlib import Path

from backend import auto_refine as ar_mod
from backend.auto_refine import AutoRefine, CHANGE_FACE, card_name, _gift_info_map
from backend import gift as gf
from backend.constants import (EP_MAINPAGE, EP_REFINE, EP_BUY, EP_COLLECTION_ADD,
                              EP_COLLECTION_GET, EP_USER_LIST, EP_STEAL, EP_TRANSFER,
                              EP_FREEGIFT_LIST, EP_FREEGIFT_TAKE,
                              scale_secs, TIME_SCALE)

# 收礼产物：真机 freegift_take 的 itemid 由服务端按 (freegift_id, action) 决定。
# 已实证组合（gift.py 注释，来自 songli.har）：
#   81 + action=2 → 7773 蚕丝；82 + action=2 → 7775 金丝线；84 + action=1 → 7774 五彩线。
#   三者都是 **48 面礼物卡**（theme=487）。
# 未实证的 freegift_id 一律按 7773 计 —— 同样是 48 面卡，只影响 cid 不影响面额，
# 对本模块关心的「能不能变出 40 面」无影响（★ 不写成「未知就失败」，那会低估）。
GIFT_TAKE_ITEM = {81: 7773, 82: 7775, 84: 7774}
GIFT_TAKE_FALLBACK = 7773
GIFT_BOX_DEFAULT_ID = 81        # 真机实测 46 件里 34 件是 81


# ---- 把 asyncio.sleep 变成瞬时（虚拟时钟自己控制，不需要真等） ----
async def _no_sleep(*a, **k):
    return None
asyncio.sleep = _no_sleep

# ---- 把 time.time 接到虚拟时钟（否则 _collected_shows 的 20s 缓存永远不刷新） ----
_VT = {"now": 0}
time.time = lambda: _VT["now"]


def _load_card_lib():
    p = Path(__file__).resolve().parent.parent / "data" / "card_lib.json"
    return json.loads(p.read_text(encoding="utf-8"))


LIB = _load_card_lib()
# 卡 id -> 炼制秒数（theme_compose_list[c][6]）★ 必须乘真机校准系数：
#   真机 `<card locktime>` = floor(表值 × TIME_SCALE)，炉等级 + 精灵加速的合计
#   （2026-10-28 四张在炉卡逐秒吻合）。`MC_TIME_SCALE=1.0` 回到旧口径做对照。
SECS = {}
# 卡 id -> 面额（card_list[c][3]，与 ThemePlan.cards[cid][3] / cards_price 同源）
FACE = {}
# 卡 id -> 3 张配方素材（theme_compose_list[c][3..5]），真实服务端会校验
COMPOSE = {}
for c in LIB["theme_compose_list"]:
    SECS[int(c[2])] = scale_secs(int(c[6]))
    COMPOSE[int(c[2])] = (int(c[3]), int(c[4]), int(c[5]))
for _c in LIB["card_list"]:
    FACE[int(_c[0])] = int(_c[3] or 0)


# 取卡所需换卡箱空位：真机 **2 格**（默认，与真机同口径）；`MC_SIM_TAKE2=0` 回旧 1 格口径
# ★ 2026-10-30 由默认 1 格改为默认 2 格：真机硬约束是 2 格，仿真按 1 格跑等于把
#   「拒绝取卡」掩盖成一路顺畅（旧 bench 数字偏乐观）。切换前实测全档无回归，见 _can_take_card。
TAKE2 = os.environ.get("MC_SIM_TAKE2", "1") != "0"


def secs_of(cid: int) -> int:
    return SECS.get(int(cid), scale_secs(3600))


# 「用户自己炼了别的卡」用的外部卡池：任何能炼的卡都行（只占炉位，产出不属本主题）
_OTHER_CIDS = sorted(COMPOSE.keys())


def face_of(cid: int) -> int:
    return FACE.get(int(cid), 0)


# ---- 扰动档位（仿真要复现真机的坑，不是复现「理想服务端」）----
# SAFE：只含**有据可依**的真实行为——变卡成功率约 50%（gift.py:change_card 注释
#   「成功 = code=0 且 result="1"；成功率约 50%」），失败也烧源卡。
# STRESS：其余扰动**没有实测依据**，只作鲁棒性压力档（MC_SIM_CHAOS=1 打开），
#   绝不当真实概率外推（禁把推测写成常量）。
#   buy_net_fail 买卡网络失败 / mainpage_fail 主页面读抖动 /
#   early_collect 用户抢先手动收卡 / steal_taken 偷炉产物被好友收走 /
#   box_lost 换卡箱里的卡被换走（好友换卡·系统回收）。
# ★ 2026-11-14 新增「用户手点」类扰动（用户定调：tick 之间状态会被用户改，
#   引擎不重解就没法做决定）。默认全 0：这些是**存在性**扰动，不是概率实测值，
#   只在 `MC_E_NOISE`/`MC_SIM_CHAOS` 跑对照时打开。
CHAOS_SAFE = {"change_fail": 0.5, "buy_net_fail": 0.0, "mainpage_fail": 0.0,
              "early_collect": 0.0, "steal_taken": 0.0, "box_lost": 0.0,
              "user_cancel": 0.0, "user_accel": 0.0, "user_other_refine": 0.0,
              "stove_expire": 0.0, "box_resize": 0.0}
CHAOS_STRESS = {"change_fail": 0.5, "buy_net_fail": 0.03, "mainpage_fail": 0.01,
                "early_collect": 0.02, "steal_taken": 0.01, "box_lost": 0.002,
                "user_cancel": 0.004, "user_accel": 0.006,
                "user_other_refine": 0.004, "stove_expire": 0.001,
                "box_resize": 0.002}


# ---- 虚拟服务端 ----
class VirtualSession:
    # ★ 炉位（2026-10-28 真机实测纠正）：`<stovebox cur="7" max="7" stovenum="3">`
    #   = **3 个主炉 + 每炉 2 个卡位（1 在炼 + 1 排队）+ 1 个偷炉位** = 7。
    #   旧口径 `MAIN_SLOTS=[1,2,3]`（一槽一张、无排队）会让第 4 张直接「炉满」，
    #   与真机「2370 结束 1793117671 == 2372 开始 1793117671」的链式排队不符。
    STOVES = 3                       # 主炉数（stovenum）
    SLOTS_PER_STOVE = 2              # 每炉卡位：第 2 个是排队位
    STEAL_SLOT = 6                   # 偷炉位（独立，1 个）
    MAIN_SLOTS = [s for s in range(STOVES * SLOTS_PER_STOVE)]   # 0..5
    STOVE_LV = 9                     # 炉等级（真机 <stove lv="9">）
    STOVE_STRONG_LV = 30             # 强化等级（真机 strong_lv="30"）

    def __init__(self, uin: int = 123456, change_cur: int = 80, store_cur: int = 80,
                 chaos: dict | None = None, seed: int = 0):
        self.uin = uin
        self.now = 0
        self.change = {}            # slot -> cid  (slottype 0)
        self.store = {}             # slot -> cid  (slottype 1)
        self.change_cur = change_cur
        self.store_cur = store_cur
        self.stoves = {}            # slot -> {"cid","opuin","endtime","steal"}
        self.friend_uin = "999000001"
        self.ar = None              # 运行期注入
        self.rng = random.Random(seed)
        # 外部事件独立随机流（见 `_rand_evt`）：保证 A/B 两档遇到同一串扰动
        self.rng_evt = random.Random(seed * 7919 + 13)
        self._evt_seed = seed * 7919 + 13
        self._evt_n = 0
        self.change_rng = False
        # 扰动档位：默认 SAFE（含真实的变卡 50% 失败）；MC_SIM_CHAOS=1 走压力档
        base = dict(CHAOS_STRESS if os.environ.get("MC_SIM_CHAOS") == "1"
                    else CHAOS_SAFE)
        if chaos:
            base.update(chaos)
        self.chaos = base
        self.collected: dict[int, set] = {}   # tid -> 已收集 qq秀 实例 id（模拟真实上交反馈）
        self.completions: list[tuple] = []    # (完工时刻endtime, cid, 是否偷炉)——精确完工记录
        self.perturb_log: list[tuple] = []    # (时刻, 类型, 细节) —— 外部扰动留痕
        # 「用户炼了别的卡」要排除的 cid（= 本主题表内卡，由 harness 填）
        self.other_exclude: set[int] = set()
        self.stove_shrunk = 0                 # 炉过期只允许发生一次（真机不会连环缩）
        # ★ 2026-11-14 扰动审计：把「扰动白送了多少产能」单独记账。
        #   user_accel 直接把 endtime 归零 = **凭空产生炉时**；box_resize 扩容 =
        #   **凭空产生箱位**。两者都不是「引擎在扰动下做对了什么」，不能算红利，
        #   必须从产出里扣掉才能判断调度质量。旧版没有这两笔账，导致误以为
        #   「扰动让产出 +60%」是用户行为红利。
        self.accel_saved = 0.0                # 累加被加速消除的炉时（小时）
        self.accel_n = 0
        self.box_grow = 0                     # 累计扩容的换卡箱格数
        self.stove_shrink = 0                 # 累计缩掉的炉台数
        # ---- 礼物箱（freegift_get_list，与卡箱无关）----
        # 真机实测 2026-10-28：count=46/50，其中 freegift_id=81 占 34 件。
        # 旧版没有这个模拟 → `gift_inbox()` 读到静默 code=0 的空响应 → count=0 →
        # `gift_drain`（收礼变卡）在仿真里**永不触发**，这条路径等于从未被测过。
        # 条目：{index_id, freegift_id, sender, time}（与 gift.gift_inbox 解析同形）。
        self.gift_box: list[dict] = []
        self.gift_taken = 0                   # 已收件数（统计用）
        self.gift_item_lost = 0               # 收到但卡箱塞不下而丢掉的件数

    # ---- 槽位分配 ----
    def _free_change_slot(self):
        used = set(self.change)
        for s in range(1, self.change_cur + 1):
            if s not in used:
                return s
        return None

    def _free_store_slot(self):
        used = set(self.store)
        for s in range(1, self.store_cur + 1):
            if s not in used:
                return s
        return None

    def _free_main_stove(self):
        """返回 (卡位 slot, 炉号 stove)；每炉最多 SLOTS_PER_STOVE 张（1 炼 + 1 排）。

        ★★ 2026-11-14 修正（扰动有害的实现根因之一）：旧版**按炉号从小到大**
        取第一个有空位的炉。空炉优先（`want` 0→1）时这没错，但所有炉都已有卡时
        它会把新卡排进「炉号最小」那台的队列 —— 那台可能正好排着用户/外部的
        一张长卡（几小时），而后一张的 `btime = max(now, 前一张 endtime)` 是
        链式预计算的（真机实测 2370 结束 == 2372 开始）→ 引擎自己的卡被白等
        几小时，表现为「炉里 7.2 张卡、平均剩余 5.68h、产出只有一半」。
        正确：**取最早可用的那台**（min 队列末端），空炉天然最早可用。
        """
        best = None
        for stove in range(self.STOVES):
            slots = [stove * self.SLOTS_PER_STOVE + i
                     for i in range(self.SLOTS_PER_STOVE)]
            free = [s for s in slots if s not in self.stoves]
            if not free:
                continue
            used = [s for s in slots if s in self.stoves]
            # 空炉 = 立刻可用；有卡的炉 = 该炉队列末端（链式 btime）
            ready = self.now if not used else max(self.now, self._queue_end(stove))
            key = (ready, len(used), stove)   # 最早可用 → 占位最少 → 炉号
            if best is None or key < best[0]:
                best = (key, free[0], stove)
        if best is None:
            return None, None
        return best[1], best[2]

    def _queue_end(self, stove: int) -> int:
        """该炉队列的最后一个 endtime（空炉 = 0）。"""
        ends = [st["endtime"] for s, st in self.stoves.items()
                if st.get("stove") == stove]
        return max(ends) if ends else 0

    def _free_steal_stove(self):
        if self.STEAL_SLOT not in self.stoves:
            return self.STEAL_SLOT
        return None

    def _add_to_box(self, cid: int):
        """买卡落箱：**只有买的底卡一定进换卡箱**，换卡箱满才溢到保险箱。"""
        s = self._free_change_slot()
        if s is not None:
            self.change[s] = cid
            return True
        s = self._free_store_slot()
        if s is not None:
            self.store[s] = cid
            return True
        return False

    def _add_refined_to_box(self, cid: int):
        """取卡落箱：★**炼成卡优先进保险箱**，保险箱满才溢到换卡箱。

        ★ 2026-11-14 保真度修正（此前一律走 `_add_to_box` = 换卡箱优先）：
          真机规则已由多处独立确认（`auto_refine.py` 落箱注释、
          `tools/sim_tight_box.py:add_any`「用户 2026-11-05 定调」）——
          「炼成的卡优先进保险箱，满了才溢到换卡箱；只有买的底卡一定进换卡箱」。
          旧口径把炼成卡塞进 18 格换卡箱 → 换卡箱 2~3 套就被炼成卡占满 →
          「取卡需换卡箱空 2 格」门槛永久失效 → 假死锁（实测松箱 200 格保险箱
          几乎全空，换卡箱却 18/18 满、取卡被拒）。
        """
        s = self._free_store_slot()
        if s is not None:
            self.store[s] = cid
            return True
        s = self._free_change_slot()
        if s is not None:
            self.change[s] = cid
            return True
        return False

    def _remove_mat(self, slot: int, slottype: int):
        box = self.change if slottype == 0 else self.store
        return box.pop(slot, None)

    # ---- 序列化 mainpage（被真实 parse_full 解析） ----
    def _serialize(self) -> str:
        now = self.now
        # 卡箱
        def box_xml(key, d, cur):
            cards = "".join(
                f'<card slot="{s}" id="{c}" type="0" st="{1 if key=="store" else 0}"/>'
                for s, c in sorted(d.items()))
            return f'<{key} cur="{cur}" max="{cur*2}">{cards}</{key}>'
        change_xml = box_xml("changebox", self.change, self.change_cur)
        store_xml = box_xml("storebox", self.store, self.store_cur)
        # 卡炉：按「炉 × 卡位」逐位输出（空位也输出 id=0，真机就是这个形态）
        stove_cards = []
        for stove in range(self.STOVES):
            for i in range(self.SLOTS_PER_STOVE):
                s = stove * self.SLOTS_PER_STOVE + i
                st = self.stoves.get(s)
                if st:
                    flag = 2 if now >= st["endtime"] else 1
                    stove_cards.append(
                        f'<card slot="{s}" stove="{stove}" id="{st["cid"]}" '
                        f'flag="{flag}" btime="{st["btime"]}" '
                        f'locktime="{st["locktime"]}" opuin="{st["opuin"]}"/>')
                else:
                    stove_cards.append(
                        f'<card slot="{s}" stove="{stove}" id="0"/>')
        steal = self.stoves.get(self.STEAL_SLOT)
        if steal:
            flag = 2 if now >= steal["endtime"] else 1
            stove_cards.append(
                f'<card slot="{self.STEAL_SLOT}" stove="-1" id="{steal["cid"]}" '
                f'flag="{flag}" btime="{steal["btime"]}" '
                f'locktime="{steal["locktime"]}" opuin="{steal["opuin"]}" '
                f'slottype="steal"/>')
        else:
            stove_cards.append(
                f'<card slot="{self.STEAL_SLOT}" stove="-1" id="0" slottype="steal"/>')
        n_slots = len(self.MAIN_SLOTS) + 1
        stovebox = (
            f'<stovebox cur="{n_slots}" max="{n_slots}" stovenum="{self.STOVES}">'
            + "".join(stove_cards) + "</stovebox>")
        # `<stove>` 的 id = **炉序号**（真机如此；旧仿真错写成卡 id）。endtime = 该炉
        # 队列最后一个的结束时刻（真机炉 1 有「2370 已完成 + 2372 在炼」时报的是 2372）。
        stoves_xml = "".join(
            f'<stove id="{stove}" endtime="{self._queue_end(stove)}" '
            f'lv="{self.STOVE_LV}" strong_lv="{self.STOVE_STRONG_LV}"/>'
            for stove in range(self.STOVES))
        if self.STEAL_SLOT in self.stoves:
            st = self.stoves[self.STEAL_SLOT]
            stoves_xml += (f'<stove id="{self.STOVES}" endtime="{st["endtime"]}" '
                           f'slottype="steal"/>')
        return (f'<user randchance="0" gold_fen="0" color_stone="0">'
                f'<hello time="{now}"/>'
                + change_xml + store_xml + stovebox + stoves_xml +
                f'</user>')

    # ---- 端点 ----
    async def post(self, endpoint: str, **kw):
        if endpoint == EP_MAINPAGE:
            # 读好友主页（偷炉前探测空炉）：必须返回**对方**的炉况，不能返回自己的。
            # 旧版忽略 opuin 一律返回自己 → `_friend_stove_empty` 读到的是自己的
            # free_main，好友永远「有空炉」、偷炉永不 -33084（假顺利）。
            opu = str(kw.get("opuin") or "")
            if opu and opu != str(self.uin):
                if self._rand("mainpage_fail"):
                    return {"code": -1, "message": "网络超时"}
                # 好友主页：3 个主炉全空（每炉 2 个卡位）
                empty = len(self.MAIN_SLOTS)
                cards = "".join(f'<card slot="{s}" stove="{s // self.SLOTS_PER_STOVE}" id="0"/>'
                                for s in self.MAIN_SLOTS)
                stoves_xml = "".join(
                    f'<stove id="{stove}" endtime="0" lv="{self.STOVE_LV}" '
                    f'strong_lv="{self.STOVE_STRONG_LV}"/>'
                    for stove in range(self.STOVES))
                return {"code": 0, "raw":
                        f'<user randchance="0"><changebox cur="18" max="36"/>'
                        f'<storebox cur="200" max="400"/>'
                        f'<stovebox cur="{empty}" max="{empty}" '
                        f'stovenum="{self.STOVES}">{cards}</stovebox>'
                        f'{stoves_xml}'
                        f'<cap><stove free_main="{empty}"/></cap></user>'}
            if self._rand("mainpage_fail"):
                return {"code": -1, "message": "网络超时"}
            return {"code": 0, "raw": self._serialize()}
        if endpoint == EP_REFINE:
            return self._do_refine(kw, steal=False)
        if endpoint == EP_STEAL:
            return self._do_refine(kw, steal=True)
        if endpoint == EP_BUY:
            if self._rand("buy_net_fail"):
                return {"code": -1, "message": "网络超时（买卡未到账）"}
            cid = int(kw.get("card_id") or 0)
            ok = self._add_to_box(cid)
            return {"code": 0 if ok else -1, "message": "ok" if ok else "箱满"}
        if endpoint == EP_COLLECTION_ADD:
            tid = int(kw.get("theme") or 0)
            plan = self.ar.plans.get(tid) if self.ar else None
            if plan:
                # 真实服务端会校验「整套是否真在箱」：每种至少 1 张。
                # 旧版无条件删卡并返回成功 → 引擎误判集齐时也"成功"，
                # 把「其实没集齐」掩盖成「上交通过」。
                stock = Counter(self.change.values()) + Counter(self.store.values())
                lack = [c for c in plan.ids if stock.get(c, 0) < 1]
                if lack:
                    return {"code": -1, "raw": "",
                            "message": f"卡未集齐（缺 {len(lack)} 种）"}
                for cid in plan.ids:
                    # 从卡箱每种去掉 1 张（上交消耗整套）
                    for box in (self.change, self.store):
                        for s, c in list(box.items()):
                            if c == cid:
                                box.pop(s)
                                break
                # 记录刚上交的 qq秀 实例（giftid -> 实例 id，与真实 card_collection_get 同口径）
                gmap = _gift_info_map()
                gid = str(kw.get("giftid") or plan.giftid)
                inst = gmap.get(gid, (None, ""))[0]
                if inst:
                    self.collected.setdefault(tid, set()).add(inst)
            return {"code": 0, "raw": ""}
        if endpoint == EP_COLLECTION_GET:
            # 返回已收集 qq秀（与真实响应结构一致：<Node theme_id qqshow_str(逗号分隔实例 id)>）
            nodes = []
            for tid, insts in self.collected.items():
                if insts:
                    nodes.append(f'<Node theme_id="{tid}" qqshow_str="{",".join(sorted(insts))}"/>')
            return {"code": 0, "raw": "".join(nodes)}
        if endpoint == EP_USER_LIST:
            return {"code": 0,
                    "raw": f'<user uin="{self.uin}"><users>'
                           f'<user uin="{self.friend_uin}" nick="好友A" exp="200"/>'
                           f'</users></user>'}
        return {"code": 0, "raw": ""}

    def _rand(self, key: str) -> bool:
        """按 chaos 概率抛一次判定（概率 0 时零开销）。"""
        p = self.chaos.get(key, 0.0)
        return bool(p) and self.rng.random() < p

    def _rand_evt(self, key: str) -> bool:
        """外部事件专用随机流（`rng_evt`，与动作流 `rng` 分开）。

        ★ 2026-11-14：扰动序列必须**与引擎动作无关**。若共用一个 rng，两个档位
        （每 tick 重解 vs 事件式）动作次数不同 → rng 消耗不同 → 扰动序列分叉，
        A/B 就变成「两组不同的天气」，对照失效。独立流保证同 tick 数下扰动逐位相同。
        """
        p = self.chaos.get(key, 0.0)
        return bool(p) and self.rng_evt.random() < p

    def external_tick(self) -> None:
        """人工/外部扰动：每个 tick 推进后由驱动器调用（仿真 = 生产的一部分）。

        真实账号在 45s 的 tick 之间会发生用户手点、好友动作，引擎看不见过程，
        只看到结果。三类：
          · early_collect：用户抢在引擎前手动收了已完成的炉 → 卡直接进箱，
            引擎下一 tick 看到空炉（refined 少记一次，进度不变）；
          · steal_taken：偷炉产物被好友收走 → 卡丢失，引擎取卡失败；
          · box_lost：换卡箱里的卡被换走/回收 → 随机消失一张。
        默认全 0（无实测依据，见 CHAOS_STRESS 注释）；MC_SIM_CHAOS=1 走压力档。
        """
        # ★★ 2026-11-14 对照实验保真：扰动随机流**每 tick 重置为「种子 + tick 号」**。
        #   旧版是一条连续流，而很多扰动分支带前置条件（`and self.change`、
        #   有闲炉才抽卡），条件不满足时**不消费随机数** → 两条对照线的随机流
        #   越走越偏，A/B 实际在比较「两种不同的天气」（实测两组扰动数 103 vs 124）。
        #   按 tick 派生种子后，第 n 个 tick 的扰动决策只由 n 决定，与引擎动作无关。
        self._evt_n = getattr(self, "_evt_n", 0) + 1
        self.rng_evt = random.Random(
            int(getattr(self, "_evt_seed", 0)) * 1000003 + self._evt_n)
        if self._rand_evt("early_collect"):
            done = [s for s, v in self.stoves.items()
                    if self.now >= v["endtime"] and not v["steal"]]
            if done:
                s = self.rng_evt.choice(done)
                v = self.stoves.pop(s)
                self._add_refined_to_box(v["cid"])
                self.perturb_log.append((self.now, "提前收卡", v["cid"]))
        if self._rand_evt("steal_taken"):
            done = [s for s, v in self.stoves.items()
                    if self.now >= v["endtime"] and v["steal"]]
            if done:
                s = self.rng_evt.choice(done)
                v = self.stoves.pop(s)
                self.perturb_log.append((self.now, "偷炉卡被好友收走", v["cid"]))
        if self._rand_evt("box_lost") and self.change:
            s = self.rng_evt.choice(list(self.change))
            cid = self.change.pop(s)
            self.perturb_log.append((self.now, "换卡箱卡被换走", cid))
        # ---- 用户手点类（2026-11-14 新增，默认概率 0）----
        #   user_cancel       用户取消一张在炼/排队的炉卡（卡没了，炉位释放）
        #   user_accel        用户加速一张在炼卡（endtime 立刻到，可取）
        #   user_other_refine 用户炼别的主题的卡（占掉一台空主炉）
        #   stove_expire      炉过期（stovenum 减 1，超界卡位随之消失）
        #   box_resize        卡箱容量变化（扩容/租期满），超容部分丢弃
        if self._rand_evt("user_cancel") and self.stoves:
            s = self.rng_evt.choice(list(self.stoves))
            v = self.stoves.pop(s)
            # ★★ 2026-11-14 保真度（关键）：真机「取消炼卡」是**把卡退回卡箱**，
            #   不是把卡销毁。旧版直接 `pop` 丢掉 → 每取消一次白丢一张素材，
            #   把「取消扰动一定有害」做成了物理损失（7 套 vs 11 套的差额里有
            #   一部分是它贡献的），这与 DP 决策质量无关。
            #   退回顺序与真机一致：换卡箱优先，满了再进保险箱；两箱都满才丢。
            slot = self._free_change_slot()
            if slot is not None:
                self.change[slot] = v["cid"]
            else:
                slot = self._free_store_slot()
                if slot is not None:
                    self.store[slot] = v["cid"]
            self.perturb_log.append(
                (self.now, "用户取消炼卡" + ("" if slot is not None else "·箱满丢弃"),
                 v["cid"]))
        if self._rand_evt("user_accel"):
            live = [s for s, v in self.stoves.items() if v["endtime"] > self.now]
            if live:
                s = self.rng_evt.choice(live)
                # ★ 审计：记下这一下白送了多少炉时（= 剩余时长）
                self.accel_saved += max(0.0, self.stoves[s]["endtime"] - self.now) / 3600.0
                self.accel_n += 1
                self.stoves[s]["endtime"] = self.now
                self.perturb_log.append((self.now, "用户加速", self.stoves[s]["cid"]))
        if self._rand_evt("user_other_refine") and self.STOVES >= 1:
            # ★★★ 2026-11-14（保真度 bug）：旧版 `free = [最小空 slot]` + `btime = now`
            #   → 若取到的 slot 与某炉**正在炼**的那张同炉，就会造出「一炉两张同时
            #   在炼」——真机不可能（后一张 btime = 前一张 endtime，链式）。实测
            #   被产能校验直接抓出来：松箱 200 tick 有 85 个 tick「同炉 >1 在炼」。
            #   现在改走与引擎同一套排队语义（`_free_main_stove` + 队列末端）。
            s, stove = self._free_main_stove()
            if s is not None:
                # 「别的卡」必须**不是本主题的卡**（否则等于用户白送一张本套卡，
                #  进度虚增），由 harness 在开跑时填 other_exclude。
                # ★★ 2026-11-14 保真度（关键）：旧版从**整个卡库**（11181 张）随机
                #   抽，时长中位 3.74h、均值 7.94h、最长 192h，而事件率是每 37 分钟
                #   一张 → 15~25 张 × 平均 8h ≈ 120~200 炉时，**超过总产能**
                #   （4 主炉 × 22.5h = 90 炉时）。于是炉位被「用户的长卡」永久锁死，
                #   引擎自己的卡排在其后干等（实测在炉卡平均剩余 0.72h → 5.5h、
                #   产出 90 → 42），被误当成「DP 怕扰动」。
                #   真实用户随手炼的卡与本主题同级，通常在 1~2h；限幅后才是有意义
                #   的扰动强度。
                _maxh = float(os.environ.get("MC_E_OTHER_MAXH", "2.0"))
                pool = [c for c in _OTHER_CIDS if c not in self.other_exclude
                        and secs_of(c) <= _maxh * 3600]
                cid = self.rng_evt.choice(pool) if pool else 0
                lock = secs_of(cid) if cid else 1800
                btime = max(self.now, self._queue_end(stove))
                self.stoves[s] = {"cid": cid, "opuin": str(self.uin),
                                  "stove": stove, "btime": btime,
                                  "locktime": lock, "endtime": btime + lock,
                                  "steal": False, "external": True}
                self.perturb_log.append((self.now, "用户炼了别的卡", cid))
        # 用户自己炼的卡到期后他自己取走 —— 否则会永久占住炉位（仿真退化）
        for s in [s for s, v in self.stoves.items()
                  if v.get("external") and self.now >= v["endtime"]]:
            v = self.stoves.pop(s)
            self.perturb_log.append((self.now, "用户取走外主题卡", v["cid"]))
        if self._rand_evt("stove_expire") and self.STOVES > 1 \
                and self.stove_shrunk < 1:
            # 炉过期（stovenum 5→4→3…）。★ 用**实例属性**改，不动类属性 ——
            #   类属性是全局的，一个档位改小会让同进程后续档位全部变少炉。
            self.STOVES -= 1
            self.stove_shrunk += 1
            self.MAIN_SLOTS = list(range(self.STOVES * self.SLOTS_PER_STOVE))
            self.STEAL_SLOT = self.STOVES * self.SLOTS_PER_STOVE
            drop = [s for s in self.stoves
                    if not self.stoves[s]["steal"]
                    and s >= self.STOVES * self.SLOTS_PER_STOVE]
            for s in drop:
                v = self.stoves.pop(s)
                self.perturb_log.append((self.now, "炉过期·炉卡丢失", v["cid"]))
        if self._rand_evt("box_resize"):
            # ★ 2026-11-14 保真度：真机箱容是用户/系统**开通**的，不会自己变小
            #   （容量只会「变够用」，缩小只出现在系统到期回收，本次不建模）。
            #   旧版允许 −5/−8 → 腾箱把保险箱填满后再遇缩容就丢卡，
            #   把「腾箱有害」这个结论做出来了（实测紧箱 6 套掉到 1 套）。
            delta = self.rng_evt.choice((0, 5, 10))
            # ★ 限幅：真机箱容不会一夜之间翻倍。超额放大（旧版 +10/+20 累积）
            #   会把「紧箱 18/58」的场景悄悄变成 300+ 格 → 紧箱档失去意义。
            _chg_before = int(self.change_cur)
            self.change_cur = min(24, max(16, _chg_before + delta))
            self.box_grow += max(0, int(self.change_cur) - _chg_before)
            over = [s for s in self.change if s > self.change_cur]
            for s in over:
                self.perturb_log.append((self.now, "换卡箱缩容·丢卡",
                                         self.change.pop(s)))
            self.store_cur = min(200, max(40, int(self.store_cur)
                                 + self.rng_evt.choice((0, 8, 16))))
            over = [s for s in self.store if s > self.store_cur]
            for s in over:
                self.perturb_log.append((self.now, "保险箱缩容·丢卡",
                                         self.store.pop(s)))

    def _do_refine(self, kw, steal: bool):
        target = int(kw.get("targetid") or 0)
        # ---- 先校验、再判炉位、最后才扣素材（真实服务端拒绝时不消耗）----
        mats = []
        for i in range(1, 4):
            sk = kw.get(f"slot{i}")
            if sk is None:
                continue
            st = int(kw.get(f"slottype{i}", 0))
            box = self.change if st == 0 else self.store
            cid = box.get(int(sk))
            if cid is None:
                return {"code": -32002, "message": "找不到相应的记录（素材槽位失效）"}
            mats.append((cid, int(sk), st))
        # 配方校验：投入的 3 张必须与 theme_compose_list 的 feeders 完全一致
        # （旧版随便 3 张都能炼出 target → 引擎参数错也被判成功）
        need = list(COMPOSE.get(target, ()))
        if need and sorted(c for c, _s, _st in mats) != sorted(need):
            return {"code": -1, "message": "素材不匹配配方"}
        # 偷炉只能炼 40 面卡（真实限制）
        if steal and face_of(target) != CHANGE_FACE:
            return {"code": -1, "message": "偷炉只能炼40面卡"}
        if steal:
            slot = self._free_steal_stove()
            stove = -1
            opuin = str(kw.get("opuin") or self.friend_uin)
        else:
            slot, stove = self._free_main_stove()
            opuin = str(self.uin)
        if slot is None:
            return {"code": -33003, "message": "炉满"}
        for _cid, s, st in mats:          # 校验 + 炉位都通过才扣素材
            self._remove_mat(s, st)
        # ★ 排队：真机同炉可放多张，后一张的 btime = 前一张的 endtime（链式预计算，
        #   实测 2370 结束 1793117671 == 2372 开始 1793117671）。所以投下去那刻，
        #   排队卡的 endtime 已经定了，取不取前面那张都不影响它。
        lock = secs_of(target)
        btime = max(self.now, self._queue_end(stove)) if stove >= 0 else self.now
        self.stoves[slot] = {"cid": target, "opuin": opuin, "stove": stove,
                             "btime": btime, "locktime": lock,
                             "endtime": btime + lock, "steal": steal}
        # ★ 2026-11-14 产能审计：累计「真正投进炉里的炉时」。利用率 =
        #   ref_mh / (cap_mh + accel_mh)，> 100% 就说明记账有重复计算。
        self.ref_mh = getattr(self, "ref_mh", 0.0) + lock / 3600.0
        return {"code": 0, "message": "ok"}

    async def qzone(self, method: str, endpoint: str, **kw):
        # ---- 礼物箱：列表 / 收礼（真机 freegift/freegift_get_list、freegift_take）----
        # 收礼产物直接进卡箱（changebox 优先、其次 storebox）—— 依据 gift.py:locate_card
        # 注释「HAR 里 7773 位于 changebox slot=1」，即礼物卡是**在箱里**被定位后变卡的，
        # 不是独立道具袋。所以这里复用 _add_to_box，箱满即丢（与真机一致）。
        if endpoint == EP_FREEGIFT_LIST:
            return {"code": 0, "data": {"freegifts": [
                {"index_id": g["index_id"], "freegift_id": g["freegift_id"],
                 "sender": g["sender"], "nick": g.get("nick") or "", "time": g["time"]}
                for g in sorted(self.gift_box, key=lambda x: x["time"])]}}
        if endpoint == EP_FREEGIFT_TAKE:
            idx = int(kw.get("index_id") or 0)
            for i, g in enumerate(self.gift_box):
                if int(g["index_id"]) != idx:
                    continue
                self.gift_box.pop(i)
                itemid = GIFT_TAKE_ITEM.get(int(g["freegift_id"]), GIFT_TAKE_FALLBACK)
                if not self._add_to_box(itemid):
                    # 箱满：礼物已从箱子扣掉，但卡没地方放 → 真机同样丢
                    self.gift_item_lost += 1
                self.gift_taken += 1
                return {"code": 0, "data": {"change": {"bag": [
                    {"itemid": itemid, "num": 1, "type": 1}]}}}
            return {"code": -1, "message": "礼物不存在或已被收取"}
        # 变卡（card_user_transfer_card）：消耗源卡 -> 产出目标卡
        if endpoint == EP_TRANSFER:
            src_id = int(kw.get("srcid") or 0)
            src_slot = int(kw.get("slotid") or 0)
            src_st = int(kw.get("slottype") or 0)
            dst = int(kw.get("dstid") or 0)
            # ① 源卡必须真在该槽（否则 -32002，不烧卡）
            box = self.change if src_st == 0 else self.store
            if box.get(src_slot) != src_id:
                return {"code": -32002, "raw": "",
                        "message": "找不到相应的记录（源卡槽位失效）"}
            # ② 面额规则：材料卡面额 >= 目标面额；百变卡 1162 万能
            if src_id != 1162 and face_of(src_id) < face_of(dst):
                return {"code": -1, "raw": "",
                        "message": "材料卡面额不足"}
            box.pop(src_slot)               # ③ 源卡先烧（失败也不退，真机行为）
            # ④ 概率失败：不产出目标
            if self._rand("change_fail"):
                return {"code": 0, "raw": '<QQHOME code="0" result="0">'}
            self._add_to_box(dst)           # ⑤ 成功：目标进箱（箱满即丢，真机同）
            return {"code": 0, "raw": '<QQHOME code="0" result="1">'}
        return {"code": 0, "raw": ""}

    def _can_take_card(self) -> bool:
        """取卡所需换卡箱空位。

        ★ 真机硬约束（2026-10-26 实测原文）："亲，可能会出现幸运事件，请**空出
        两个卡箱的位置**，再取卡！" —— 真机要 **2 格**，仿真旧版只要 1 格。
        **默认已切到真机的 2 格**（2026-10-30）；`MC_SIM_TAKE2=0` 回旧 1 格口径，
        仅供与历史 bench 数字对照。

        ★ 这里曾写着「按 2 格跑 3★/4★ 会 750h 卡死（引擎腾箱不保取卡位）」——
        **该结论已过期**。满箱死锁链（43f0b38：收卡被拒立刻 `_make_room(2)` 重试）
        修好后重测（DP、mr=1、18/200）：
          85x1 4.45 / 290x1 14.43 / 59x1 45.41 / 60x1 50.83 / 178x2 3.79 /
          178x3 5.69 / 290x2 21.26 —— 与 1 格口径**逐位相同**；
          709x1 102.84→104.45（+1.61h，全程只被拒 1 次，代价来自那一次拒绝后的
          计划偏差）；85x3 13.82→13.18。3★/4★ 全程被拒 1~2 次、err=0、无死锁。
        结论：执行层「拒绝→腾箱→下 tick 重试」已足够，不需要在买卡预算里预留第二格
        （那会让预算归零，才是 750h 真因，见 dp_controller `reserve` 注释）。
        """
        if not TAKE2:
            # 旧口径：箱满时 _add_to_box 静默丢卡（这把真机的「拒绝取卡」掩盖成
            # 一路顺畅 —— 正是它让旧 bench 高星数字偏乐观，见文档 §9.4）
            return True
        return self._free_change_slot() is not None and \
            len(self.change) + 1 < self.change_cur

    async def get_refined_card(self, slot):
        st = self.stoves.get(int(slot))
        if not st:
            return {"ok": False, "message": "无此炉"}
        if self.now < st["endtime"]:        # 未到点（flag=1）取卡必失败
            return {"ok": False, "code": -32001, "message": "卡还没炼好"}
        if not self._can_take_card():
            # 炉不消失、卡不丢（真机是「拒绝取卡」，不是「取出后丢弃」）
            self.take_rejected = getattr(self, "take_rejected", 0) + 1
            return {"ok": False, "code": -1,
                    "message": "亲，可能会出现幸运事件，请空出两个卡箱的位置，再取卡！"}
        self.stoves.pop(int(slot))
        self.completions.append((st["endtime"], st["cid"], bool(st["steal"])))
        self._add_refined_to_box(st["cid"])
        return {"ok": True, "message": "成功"}

    async def get_steal_card(self, slot, opuin):
        st = self.stoves.get(int(slot))
        if not st:
            return {"ok": False, "message": "无此炉"}
        if self.now < st["endtime"]:
            return {"ok": False, "code": -32001, "message": "卡还没炼好"}
        if not self._can_take_card():
            self.take_rejected = getattr(self, "take_rejected", 0) + 1
            return {"ok": False, "code": -1,
                    "message": "亲，可能会出现幸运事件，请空出两个卡箱的位置，再取卡！"}
        self.stoves.pop(int(slot))
        self.completions.append((st["endtime"], st["cid"], bool(st["steal"])))
        self._add_refined_to_box(st["cid"])
        return {"ok": True, "message": "成功"}

    async def open_pai(self):
        return {"ok": True, "message": "ok", "raw": "", "items": []}

    # 腾位路径（BoxCompaction 触发）
    async def sell_card(self, slot, cid, box_type=1):
        """卖卡：从指定箱移除一张卡（仅用于多余 10 面底卡，绝不卖高值）。

        ★ box_type 语义必须与真实 API 一致（2026-10-26 修正）：mc_api.sell_card
        注释与调用点都是 `type = 1 换卡箱 / 2 保险箱`。旧版虚拟层写反
        （1 → self.store），于是**换卡箱里卖卡一律「槽位不匹配」**：DP 腾箱
        705 次全部失败、greedy `_make_room` 卖不出卡 → 箱满后整条链路死锁
        （4★ DP 实测 220 次「卡箱 0 空位」买不进，就是 705 次假腾箱造成的
        幽灵空位）。仿真要能暴露真机的坑，不能自己造一个反的语义。
        """
        box = self.change if box_type == 1 else self.store
        if slot in box and box[slot] == cid:
            box.pop(slot)
            return {"ok": True, "message": "ok"}
        return {"ok": False, "message": "槽位不匹配"}

    async def move_card(self, cid, slot, from_box=0):
        """挪箱：换卡箱(from_box=0)->保险箱，或反向。目标箱需有空位。"""
        src = self.change if from_box == 0 else self.store
        dst = self.store if from_box == 0 else self.change
        if slot not in src or src[slot] != cid:
            return {"ok": False, "message": "源槽不匹配"}
        if from_box == 0:
            s = self._free_store_slot()
        else:
            s = self._free_change_slot()
        if s is None:
            return {"ok": False, "message": "目标箱满"}
        cidval = src.pop(slot)
        dst[s] = cidval
        return {"ok": True, "message": "ok"}


# ---- 快照 ----
def snapshot(vs: VirtualSession, ar: AutoRefine):
    lines = []
    now = vs.now
    hh = now // 3600
    # 卡炉
    furn = []
    for s in sorted(vs.stoves):
        st = vs.stoves[s]
        remain = max(0, st["endtime"] - now)
        tag = "偷" if st["steal"] else f"主{s}"
        furn.append(f"{tag}炉:{card_name(st['cid'])}[{st['cid']}](剩{remain/3600:.2f}h)")
    # 关键卡库存
    plan = ar.plans[ar.queue[0]] if ar.queue else None
    if plan:
        stock = Counter()
        for c in vs.change.values():
            stock[c] += 1
        for c in vs.store.values():
            stock[c] += 1
        key_ids = sorted(plan.ids)
        # 按面额分组展示
        by_face = {}
        for cid in key_ids:
            f = int(plan.cards[cid][3] or 0)
            by_face.setdefault(f, []).append(cid)
        box_lines = []
        for f in sorted(by_face, reverse=True):
            parts = []
            for cid in by_face[f]:
                parts.append(f"{card_name(cid)}[{cid}]×{stock.get(cid,0)}")
            box_lines.append(f"{f}面: " + " ".join(parts))
        free_ch = vs.change_cur - len(vs.change)
        free_st = vs.store_cur - len(vs.store)
        lines.append(f"  [卡炉] " + ("；".join(furn) if furn else "全空"))
        lines.append(f"  [箱存] " + "  ".join(box_lines))
        lines.append(f"  [箱位] 换卡箱空{free_ch} 保险箱空{free_st}")
    return hh, lines


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tid", type=int, default=290)
    ap.add_argument("--gift", type=int, default=20)
    ap.add_argument("--steal", default="on")
    ap.add_argument("--sets", type=int, default=0,
                    help=">0 = 固定每轮排 N 套（surplus_sets）；0 = 按未收集 qq秀自动")
    ap.add_argument("--uncollected", type=int, default=0,
                    help="seed 已收集 qq秀，使自动模式只剩 N 个未收集；0=不seed")
    ap.add_argument("--rounds", type=int, default=1, help="max_rounds：每套上交 N 轮后停")
    ap.add_argument("--random", action="store_true",
                    help="（兼容旧参数，现已默认）变卡 50% 概率；默认档已含")
    ap.add_argument("--steady", action="store_true",
                    help="关掉所有扰动（变卡必成功），复现旧 bench 数字用")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dp", default="off",
                    help="on = 炼卡环节走 engine_v2 DPTickController（DP 调度）")
    args = ap.parse_args()

    vs = VirtualSession(chaos=({"change_fail": 0.0} if args.steady else None),
                        seed=args.seed)
    vs.change_rng = args.random       # 兼容旧字段：现由 chaos["change_fail"] 决定
    # 初始塞礼物卡（48 面，theme=487），用于触发 box_gift_change 分布变卡
    gift_ids = sorted(gf._face48_ids())
    gid = gift_ids[0] if gift_ids else 7773
    for i in range(args.gift):
        s = vs._free_change_slot()
        if s is None:
            break
        vs.change[s] = gid

    ar = AutoRefine(vs, [args.tid], buy=True, max_rounds=max(1, args.rounds))
    if args.sets > 0:
        ar.surplus_sets = args.sets        # 固定套数，对齐 sched_cmp 实例口径
    ar.steal_enabled = (args.steal == "on")
    ar.dp_mode = (args.dp == "on")         # DP 调度开关（2026-10-24 界面同款）
    vs.ar = ar

    # seed 已收集 qq秀：自动模式的「未收集数」= 总秀数 - 已seed数。
    # 页面默认逻辑就是按未收集 qq秀数定套数（用户账号状态），seed 后即可精确
    # 复刻 sched_cmp 的 sets=N 实例，且每上交 1 轮目标自动收缩、不超产。
    if args.uncollected > 0:
        plan0 = ar.plans[args.tid]
        gmap0 = _gift_info_map()
        insts = [gmap0[g][0] for g in plan0.gifts
                 if gmap0.get(g) and gmap0[g][0]]
        for inst in insts[:max(0, len(insts) - args.uncollected)]:
            vs.collected.setdefault(args.tid, set()).add(inst)
        print(f"seed 已收集 qq秀 {len(vs.collected.get(args.tid, ()))}/{len(insts)} 个"
              f" → 自动模式按 {args.uncollected} 个未收集排套")

    # 包装 _log：把每次实际操作无损捕获到独立列表（绕过引擎自身 200 条裁剪）
    all_actions = []
    now_to_tick = {}
    orig_log = ar._log

    def _wrap_log(action, ok, detail):
        all_actions.append((_VT["now"], action, ok, detail))
        orig_log(action, ok, detail)
    ar._log = _wrap_log

    print(f"=== 忠实 tick 仿真：tid={args.tid} {ar.plans[args.tid].name} "
          f"初始礼物卡{args.gift}张 变卡{'关闭(steady)' if args.steady else '约50%失败(真实)'} "
          f"偷炉={'开' if ar.steal_enabled else '关'} "
          f"调度={'DP' if ar.dp_mode else '贪心轮转'} ===")
    print(f"主炉={vs.STOVES}个 偷炉={'1个' if ar.steal_enabled else '0个'} "
          f"interval=45s 卡炉秒数来自 theme_compose_list[6]（真实）")
    print(f"套数模式={'固定%d套/轮' % args.sets if args.sets > 0 else '自动(未收集qq秀数)'} "
          f"轮数上限={max(1, args.rounds)}"
          + (f" seed后未收集={args.uncollected}" if args.uncollected > 0 else ""))

    # 只看「实际操作」类，排除纯读取/状态变化噪声
    ACTION_OPS = {
        "礼物卡变卡", "礼物变卡", "合成", "炼卡", "合成跳过", "暂缓",
        "收卡", "买卡", "上交", "偷炉", "腾位",
    }

    INTERVAL = 45.0
    done_hh = None
    max_ticks = 300000                       # 深度主题(如3星)长工期也可能数万 tick
    for tick_n in range(max_ticks):
        vs.now += int(INTERVAL)
        _VT["now"] = vs.now            # 让 time.time 走虚拟时钟（刷新 _collected_shows 缓存）
        now_to_tick[vs.now] = tick_n + 1     # 第 1 个 tick 对应 now=45s
        vs.external_tick()              # 人工/外部扰动（与 Engine.run_sim 同一口径）
        await ar.tick()
        if not ar.queue and done_hh is None:
            done_hh = vs.now // 3600
            print(f">>> 队列空：第一套卡炼成于 模拟{done_hh}h（第 {tick_n + 1} tick）")
            break
    if done_hh is None:
        print(f">>> 未在 {max_ticks} tick 内完成（可能卡死，需检查逻辑）")

    # ---- 生成「有实际操作的 tick」流水，供人工审核 ----
    groups = {}
    for ts, action, ok, detail in all_actions:
        if action in ACTION_OPS:
            groups.setdefault(ts, []).append((action, ok, detail))

    out = []
    out.append(f"=== 有操作的 tick 流水：tid={args.tid} {ar.plans[args.tid].name} "
               f"初始礼物卡{args.gift}张 变卡{'关' if args.steady else '50%失败'} "
               f"偷炉={'开' if ar.steal_enabled else '关'} ===")
    out.append(f"主炉={vs.STOVES} 偷炉={'1' if ar.steal_enabled else '0'} "
               f"interval=45s 卡炉秒数来自 theme_compose_list[6]（真实）")
    out.append(f"完成于 模拟{done_hh}h（第{tick_n + 1} tick）；有实际操作的 tick 共 {len(groups)} 个 / 总计 {tick_n + 1} tick")
    out.append("")
    for ts in sorted(groups):
        h = ts // 3600
        m = (ts % 3600) // 60
        tk = now_to_tick.get(ts, "?")
        out.append(f"[模拟 {h}h{m:02d}m · tick{tk}]")
        for action, ok, detail in groups[ts]:
            out.append(f"    [{'✓' if ok else '✗'}] {action}：{detail}")
    text = "\n".join(out)
    trace_path = Path(__file__).resolve().parent.parent / "data" / f"sim_trace_tid{args.tid}.txt"
    trace_path.write_text(text, encoding="utf-8")
    print(text)

    # ---- 机器可读摘要（供 sched_cmp/run_prod_compare.py 对比，不影响任何决策）----
    plan = ar.plans[args.tid]
    top_cid = max(plan.ids, key=lambda c: plan.depth.get(c, 0))
    machines = vs.STOVES + (1 if ar.steal_enabled else 0)
    done_cnt = Counter(cid for _, cid, _ in vs.completions)
    t_last = max((e for e, _, _ in vs.completions), default=0)
    leftover = [(st["cid"], st["endtime"]) for st in vs.stoves.values()]
    t_all = max([t_last] + [e for _, e in leftover] + [0])
    work_s = sum(secs_of(cid) for _, cid, _ in vs.completions)
    submit_times = [ts for ts, a, ok, _d in all_actions if a == "上交" and ok]
    summary = {
        "tid": args.tid, "sets": args.sets, "uncollected": args.uncollected,
        "rounds": max(1, args.rounds),
        "gift": args.gift, "steal": bool(ar.steal_enabled), "machines": machines,
        "top_cid": top_cid, "ticks": tick_n + 1,
        "t_last": t_last, "t_last_h": round(t_last / 3600, 3),
        "t_all_h": round(t_all / 3600, 3),
        "top_times_h": [round(e / 3600, 3) for e in
                        sorted(e for e, c, _ in vs.completions if c == top_cid)],
        "submit_times_h": [round(t / 3600, 3) for t in submit_times],
        "done": {str(k): v for k, v in sorted(done_cnt.items())},
        "n_done": sum(done_cnt.values()),
        "leftover": [[c, round(e / 3600, 3)] for c, e in leftover],
        "work_h": round(work_s / 3600, 3),
        "util": round(work_s / (machines * t_all), 4) if t_all else 0,
    }
    print("=== SUMMARY " + json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
