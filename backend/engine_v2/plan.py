"""契约层：Planner 与 Executor 之间唯一的耦合点。

Action 是最小不可再分的操作单元；Plan 是有序的 Action 列表。
顺序即执行顺序——这是 Ⅴ 类「collect 先于 buy」「合成先于买」的硬保证：
Planner 在生成 Plan 时就排好序，Executor 照单执行、不重新决策。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Action:
    kind: str                       # 'refine' | 'buy' | 'collect' | 'move' | 'sell'
    tid: Optional[int] = None       # collect 用的套 id
    target: Optional[int] = None    # refine 的产出卡 id
    feeders: List[Tuple[int, int]] = field(default_factory=list)  # [(slot, slottype), ...]
    # 喂料的卡 id（与 feeders 同序）。非空时执行层**按 cid 现查真实槽位**，
    # 而不是反查 feeders 里的 slot —— 本 tick 刚买的卡还没有 slot（规划期用
    # 虚拟槽占位），必须买完重读状态才知道它在哪一格。
    feed_cids: List[int] = field(default_factory=list)
    cid: Optional[int] = None       # buy / move / sell 的卡 id
    n: int = 0                      # buy 张数
    steal: bool = False             # refine 是否走偷炉
    giftid: str = "0"               # collect 用的 qq秀 giftid
    slot: Optional[int] = None      # move / sell 的物理槽位
    slottype: int = 0               # move / sell 的箱类型 0=换卡箱 1=保险箱
    note: str = ""                  # 给人看的说明


Plan = List[Action]
