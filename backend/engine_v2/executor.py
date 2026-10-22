"""执行层：把 Plan 逐 Action 翻译成游戏后端调用。纯 API，不含任何决策逻辑。

Executor 只认 GameClient 协议（post / qzone / get_refined_card / get_steal_card /
open_pai / sell_card / move_card）。仿真时 GameClient=VirtualSession（忠实后端）；
接真机时 GameClient=对接 mc_api.Session 的适配器（见 engine.py 注释，本次不接）。
"""
from __future__ import annotations

from typing import List, Tuple

from backend.constants import (EP_REFINE, EP_STEAL, EP_BUY, EP_COLLECTION_ADD)

from .plan import Action


class Executor:
    async def execute(self, plan: List[Action], client) -> List[Tuple[Action, bool, str]]:
        results: List[Tuple[Action, bool, str]] = []
        for a in plan:
            if a.kind == "refine":
                res = await self._refine(client, a)
            elif a.kind == "buy":
                res = await self._buy(client, a)
            elif a.kind == "collect":
                res = await self._collect(client, a)
            elif a.kind == "move":
                res = await self._move(client, a)
            elif a.kind == "sell":
                res = await self._sell(client, a)
            else:
                res = (False, f"未知动作 {a.kind}")
            results.append((a, res[0], res[1]))
        return results

    async def _refine(self, client, a: Action):
        kw = {"targetid": a.target}
        for i, (slot, slottype) in enumerate(a.feeders, 1):
            kw[f"slot{i}"] = slot
            kw[f"slottype{i}"] = slottype
        ep = EP_STEAL if a.steal else EP_REFINE
        try:
            r = await client.post(ep, **kw)
        except Exception as e:  # 网络/接口异常不静默空转，回报错误
            return (False, f"refine 异常: {e}")
        code = r.get("code", -1) if isinstance(r, dict) else -1
        ok = code == 0
        return (ok, f"{'偷炉' if a.steal else '炼卡'} {a.target} code={code} {r.get('message','')}")

    async def _buy(self, client, a: Action):
        # 同一 buy Action 可能 n 张，逐张买（每张都要过容量校验，箱满服务端返回 -1）
        bought = 0
        fails = 0
        for _ in range(max(1, a.n)):
            try:
                r = await client.post(EP_BUY, card_id=a.cid)
            except Exception as e:
                return (False, f"buy 异常: {e}")
            code = r.get("code", -1) if isinstance(r, dict) else -1
            if code == 0:
                bought += 1
            else:
                fails += 1   # 箱满 → 服务端拒，绝不强制（Ⅴ 类：不溢出）
        ok = fails == 0
        return (ok, f"买 {a.cid} 成功{bought} 失败{fails}")

    async def _collect(self, client, a: Action):
        try:
            r = await client.post(EP_COLLECTION_ADD, theme=a.tid, giftid=a.giftid)
        except Exception as e:
            return (False, f"collect 异常: {e}")
        code = r.get("code", -1) if isinstance(r, dict) else -1
        return (code == 0, f"上交 {a.tid} code={code} {r.get('message','')}")

    async def _move(self, client, a: Action):
        # 挪箱（换卡箱<->保险箱，无损腾位）：仿真后端真实执行；真机对接 move_card。
        try:
            r = await client.move_card(a.cid, a.slot, a.slottype)
        except Exception as e:
            return (False, f"move 异常: {e}")
        ok = bool(r.get("ok")) if isinstance(r, dict) else True
        return (ok, f"挪 {a.cid} 槽{a.slot} 从{a.slottype}")

    async def _sell(self, client, a: Action):
        # 卖卡（只用于多余 10 面底卡）：仿真后端真实移除；真机对接 sell_card。
        try:
            r = await client.sell_card(a.slot, a.cid, a.slottype)
        except Exception as e:
            return (False, f"sell 异常: {e}")
        ok = bool(r.get("ok")) if isinstance(r, dict) else True
        return (ok, f"卖 {a.cid} 槽{a.slot}")
