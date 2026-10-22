"""静态模型层：包装旧代码的 ThemePlan，提供 Planner 需要的纯查询接口。

只读取合成树 / 炉时 / 面额，不持有任何运行期状态（运行期状态在 WorldState）。
"""
from __future__ import annotations

from backend.auto_refine import ThemePlan, CHANGE_FACE


class ThemeModel:
    def __init__(self, tid: int):
        self.tid = int(tid)
        self.tp: ThemePlan = ThemePlan(tid)

    # ---- 透传静态属性 ----
    @property
    def ids(self) -> list:
        return self.tp.ids

    @property
    def depth(self) -> dict:
        return self.tp.depth

    @property
    def comp(self) -> dict:
        return self.tp.comp

    @property
    def cards(self) -> dict:
        return self.tp.cards

    @property
    def name(self) -> str:
        return self.tp.name

    @property
    def star(self) -> int:
        return self.tp.star

    @property
    def giftid(self) -> str:
        return self.tp.giftid

    # ---- 查询 ----
    def secs(self, cid: int) -> int:
        """炼一张 cid 需要的炉·秒（底卡不在 comp 里 = 0，买即用）。"""
        c = self.comp.get(cid)
        return int(c[6]) if c else 0

    def price(self, cid: int) -> int:
        c = self.cards.get(cid)
        return int(c[3]) if c and c[3] is not None else 0

    def is_base(self, cid: int) -> bool:
        """底卡 = 不在合成树里（必须买，不可由合成产出）。"""
        return cid not in self.comp

    def is_40(self, cid: int) -> bool:
        """面额 == 40：唯一允许进偷炉的素材。"""
        return self.price(cid) == CHANGE_FACE

    @property
    def top_cid(self) -> int:
        return max(self.ids, key=lambda c: self.depth.get(c, 0))

    def feeders(self, cid: int) -> list:
        """cid 的 3 个素材卡 id（合成 3 合 1 固定树）。"""
        c = self.comp.get(cid)
        if not c:
            return []
        return [int(m) for m in (c[3], c[4], c[5]) if m]

    def net_plan(self, stock, pending, sets: int = 1):
        """净需求 (runs, buy)，直接复用 ThemePlan。"""
        return self.tp.net_plan(stock, pending, sets=sets)

    def complete_in_stock(self, stock: dict) -> bool:
        """库存是否已凑齐一整套（每个套内卡 >= 1）。"""
        for cid in self.ids:
            if (stock.get(cid, 0) if hasattr(stock, "get") else stock.get(cid, 0)) < 1:
                return False
        return True
