"""读状态层：把 parse_full() 的结果收敛成一个聚合的 WorldState。

聚合而非枚举（见架构文档 §5）：不记录每张卡的具体位置，只保留
维度聚合量——库存 Counter、空闲炉/箱、在制品(pending)。这样既能喂给
Planner 做决策，又不因对称性爆炸。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from backend.auto_refine import parse_full  # 复用真解析


@dataclass
class Furnace:
    slot: int = -1
    cid: int = 0
    endtime: int = 0
    steal: bool = False


@dataclass
class WorldState:
    now: int = 0
    change: Counter = field(default_factory=Counter)   # 换卡箱：cid -> 张数
    store: Counter = field(default_factory=Counter)    # 保险箱：cid -> 张数
    change_cur: int = 0   # 换卡箱已开通格数（硬顶）
    store_cur: int = 0    # 保险箱已开通格数
    main_free: int = 0    # 空闲主炉位（来自 parse_full cap.stove.free_main）
    steal_free: int = 0   # 空闲偷炉位
    furnaces: list = field(default_factory=list)       # list[Furnace]
    slots: list = field(default_factory=list)          # [(cid, slot, slottype), ...]
    randchance: int = 0

    # ---- 聚合派生量 ----
    @property
    def stock(self) -> Counter:
        """箱内真实库存（换卡箱 + 保险箱，不含炉内在炼）。"""
        s = Counter(self.change)
        s.update(self.store)
        return s

    @property
    def pending(self) -> Counter:
        """炉内在炼（卡不堆叠，1 张占 1 炉位）。"""
        p = Counter()
        for f in self.furnaces:
            if f.cid > 0:
                p[f.cid] += 1
        return p

    @property
    def change_free(self) -> int:
    # 换卡箱剩余可放 = 已开通 - 已用（不是 max - used！见 parse_full 注释）
        return max(0, self.change_cur - sum(self.change.values()))

    @property
    def store_free(self) -> int:
        return max(0, self.store_cur - sum(self.store.values()))

    @property
    def box_free(self) -> int:
        """双箱合计剩余可放格数。"""
        return self.change_free + self.store_free

    # ---- 从 parse_full 结果构造 ----
    @classmethod
    def from_parsed(cls, st: dict, now: int = 0) -> "WorldState":
        cap = st.get("cap", {})
        change_cap = cap.get("change", {})
        store_cap = cap.get("store", {})
        stove_cap = cap.get("stove", {})
        change = Counter(c["id"] for c in st.get("change", []) if c.get("id", 0) > 0)
        store = Counter(c["id"] for c in st.get("store", []) if c.get("id", 0) > 0)
        slots: list = []
        for c in st.get("change", []):
            if c.get("id", 0) > 0:
                slots.append((c["id"], c.get("slot", 0), 0))
        for c in st.get("store", []):
            if c.get("id", 0) > 0:
                slots.append((c["id"], c.get("slot", 0), 1))
        # ★ 炉位一律以 stovebox 的 <card> 为准（2026-10-26 真机实测纠正）。
        # 旧实现遍历 st["stoves"]（<stove> 节点）并把节点的 id 当卡 id——
        # 那是**仿真自己造的格式**：`_sim_faithful._serialize` 写
        # `<stove id="{card_id}">`，而真机写的是**炉序号**（id="0"/"1"/"2"，
        # 见 `<stovebox cur="7" stovenum="3">` 下的 `<stove id="0" endtime=… lv=9>`）。
        # 生产后果（1★ 真机实测，2026-10-26）：
        #   ① pending 变成 {1:1, 2:1} 幽灵在制卡（卡 id 是 684~696 这种量级）；
        #   ② `ws.furnaces` 永远非空 → `_run_plan` 的 `reserve = soon + (2 if
        #      ws.furnaces else 0)` 恒吃 2 格 → 换卡箱只剩 2 空位时买卡预算 = 0，
        #      计划头部的 buy op 永不发出（这正是代码注释里描述的那个 1★ 死锁，
        #      但注释里的修法 `2 if ws.furnaces else 0` 在真机永远生效不了）；
        #   ③ buy_cap = len(furnaces)*3+6 用的是炉位数而非在炼数（影响小）。
        # 真机 <stove> 节点的 endtime 仍要用：卡片属性里只有 btime+locktime
        # （locktime=炼制总秒数），没有 endtime/left。
        node_end: dict = {}
        for sv in st.get("stoves", []):
            try:
                node_end[int(sv.get("id", -1))] = int(sv.get("endtime", 0) or 0)
            except (TypeError, ValueError):
                continue
        furnaces: list = []
        for c in st.get("stove", []):
            cid = int(c.get("id", 0) or 0)
            if cid <= 0:
                continue
            slot = c.get("slot", -1)
            try:
                slot = int(slot)
            except (TypeError, ValueError):
                slot = -1
            end = 0
            for key in (cid, slot):        # 仿真按卡 id 对齐、真机按炉序号对齐
                if node_end.get(key):
                    end = node_end[key]
                    break
            if not end:                    # 真机回退：btime + locktime
                try:
                    end = int(c.get("btime") or 0) + int(c.get("locktime") or 0)
                except (TypeError, ValueError):
                    end = 0
            if not end and now:
                try:
                    end = now + int(c.get("left") or 0)
                except (TypeError, ValueError):
                    end = 0
            furnaces.append(Furnace(slot=slot, cid=cid, endtime=end,
                                    steal=str(c.get("slottype") or "") == "steal"))
        return cls(
            now=now,
            change=change, store=store,
            change_cur=int(change_cap.get("cur", 0)),
            store_cur=int(store_cap.get("cur", 0)),
            main_free=int(stove_cap.get("free_main", 0)),
            steal_free=int(stove_cap.get("free_steal", 0)),
            furnaces=furnaces, slots=slots,
            randchance=int(st.get("randchance", 0)),
        )

    @classmethod
    def from_raw(cls, raw: str, now: int = 0) -> "WorldState":
        return cls.from_parsed(parse_full(raw), now=now)
