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

    def net_plan(self, stock, pending, sets: int = 1, mid_collect: bool = True):
        """净需求 (runs, buy)，直接复用 ThemePlan。

        `mid_collect`（2026-11-16，DP 专用口径）：False = 中间层（40 面卡）
        不额外算一份「进集卡册」的收藏需求，只按顶层合成消耗传导 →
        一套 150 面正好 3 张 40 面（各 1 张），底卡 9 张。
        True = 旧口径（中间层也按 sets 收藏一份 → 一套 6 张 40 面、23 张底卡）。
        贪心侧因 `_spare` 死保必须用旧口径；DP 无该机制，用精确口径后
        紧箱（9 格）才能排出多套并行（实测 1652 三套 9 格 4.40h）。
        """
        return self.tp.net_plan(stock, pending, sets=sets,
                                mid_collect=mid_collect)

    def cp_order(self) -> list:
        """关键路优先序（RCPSP 经典 CP 规则）：cp(c)=secs(c)+max(cp(父卡))，
        父卡只在本套合成卡内找（comp 是全卡库，不能直接遍历）。按 cp 降序。
        实例级缓存——静态量与运行期状态无关。
        实测（单套×1轮，同 JIT/箱容/炉）：比深层优先快 6~11h
        （3★ 76.06 vs 82.74 / 4★ 84.06 vs 95.39 / 5★ 173.11 vs 184.51）。"""
        cached = getattr(self, "_cp_order_cache", None)
        if cached is not None:
            return cached
        comp = [c for c in self.ids if not self.is_base(c)]
        cache: dict = {}

        def cp_of(c: int) -> int:
            v = cache.get(c)
            if v is not None:
                return v
            parents = [p for p in comp if c in self.feeders(p)]
            v = self.secs(c) + max((cp_of(p) for p in parents), default=0)
            cache[c] = v
            return v

        order = sorted(comp, key=lambda c: (-cp_of(c), c))
        self._cp_order_cache = order
        return order

    def complete_in_stock(self, stock: dict) -> bool:
        """库存是否已凑齐一整套（每个套内卡 >= 1）。"""
        for cid in self.ids:
            if (stock.get(cid, 0) if hasattr(stock, "get") else stock.get(cid, 0)) < 1:
                return False
        return True
