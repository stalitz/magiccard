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
        furnaces: list = []
        for i, sv in enumerate(st.get("stoves", [])):
            furnaces.append(Furnace(slot=i, cid=int(sv.get("id", 0)),
                                    endtime=int(sv.get("endtime", 0)),
                                    steal=bool(sv.get("steal"))))
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
