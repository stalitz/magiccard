"""engine_v2 测试：状态层单测 + 生产代码的端到端仿真。

跑法（项目根目录）：
  python -m pytest backend/engine_v2/tests/test_engine.py -q
或：
  python -m backend.engine_v2.tests.test_engine

★ 2026-10-26：删掉了对旧 Parallel Planner 的单测。仿真现在直接跑生产
`AutoRefine.tick()`（决策+执行同一套），所以「投炉/买卡顺序」这类断言
不再对着一份独立实现做，改由端到端不变量保证（不溢出、能上交、炉不空转）。
"""
from __future__ import annotations

import asyncio
from collections import Counter

from backend.auto_refine import _maps
from backend.engine_v2.engine import Engine
from backend.engine_v2.model import ThemeModel
from backend.engine_v2.world import WorldState


# ---- 自动挑一个 1 星套（合成树浅、仿真快） ----
def _find_one_star_tid():
    _cards, _comp, themes = _maps()
    for tid, t in themes.items():
        try:
            star = int(t[2])
        except (IndexError, TypeError, ValueError):
            continue
        if star != 1:
            continue
        ids = t[11] if len(t) > 11 else None
        if not ids:
            continue
        m = ThemeModel(tid)
        top = m.top_cid
        if not m.feeders(top):   # 顶层须有素材（可合成）
            continue
        return int(tid)
    raise RuntimeError("未找到合适的 1 星套")


TID = _find_one_star_tid()


# ============ 状态层单测 ============
def test_world_free_counts():
    ws = WorldState(change=Counter({1: 2, 2: 3}), store=Counter({5: 1}),
                    change_cur=18, store_cur=200, main_free=3, steal_free=1,
                    furnaces=[], slots=[])
    assert ws.stock[1] == 2 and ws.stock[5] == 1
    assert ws.change_free == 18 - 5            # 换卡箱 5 张已用
    assert ws.store_free == 200 - 1
    assert ws.box_free == (18 - 5) + (199)
    # pending 来自炉内
    from backend.engine_v2.world import Furnace
    ws2 = WorldState(furnaces=[Furnace(cid=99, endtime=9999)], slots=[])
    assert ws2.pending[99] == 1


# ============ 端到端：跑的就是生产代码 ============
def test_sim_drives_production_auto_refine():
    """红线：仿真必须驱动生产的 AutoRefine（不是另一份重写实现）。"""
    from backend.auto_refine import AutoRefine

    async def go():
        e = Engine([TID], buy=True, max_rounds=1, steal_enabled=True)
        vs, ticks = await e.run_sim(change_cur=18, store_cur=200, max_ticks=3000)
        return e, vs, ticks

    e, vs, ticks = asyncio.run(go())
    assert isinstance(e.ar, AutoRefine), "仿真没有跑生产 AutoRefine"
    assert vs.ar is e.ar, "VirtualSession 与 AutoRefine 未绑定"


def test_sim_completes_one_round():
    """端到端：能炼成至少 1 套，且无买卡溢出、炉被使用。"""
    async def go():
        e = Engine([TID], buy=True, surplus_sets=0, max_rounds=1, steal_enabled=True)
        vs, ticks = await e.run_sim(change_cur=18, store_cur=200, max_ticks=30000)
        return e, vs, ticks
    e, vs, ticks = asyncio.run(go())
    assert ticks < 30000, f"疑似卡死：跑了满 {ticks} tick"
    assert e.completed_rounds[TID] >= 1, f"未炼成：stats={e.stats}"
    assert e.stats["buy_fail"] == 0, f"买卡溢出（Ⅴ 类失败）：stats={e.stats}"
    assert e.stats["refined"] > 0, "炉从未被使用（空炉 bug 复发）"


def test_sim_no_idle_furnace_utilization():
    """炉利用率：在炼制期间炉不应长期空着。"""
    async def go():
        e = Engine([TID], buy=True, surplus_sets=0, max_rounds=1, steal_enabled=True)
        vs, ticks = await e.run_sim(change_cur=18, store_cur=200, max_ticks=30000)
        return e, vs, ticks
    e, vs, ticks = asyncio.run(go())
    total = e.stats["total_ticks"]
    busy = e.stats["busy_ticks"]
    assert total > 0
    assert busy / total > 0.3, f"炉利用率过低：{busy}/{total}"


def test_sim_tight_box_multiset_no_overflow():
    """紧箱 + 多套并行压力测试：逼近真实 18 格换卡箱。"""
    async def go():
        e = Engine([TID], buy=True, surplus_sets=2, max_rounds=2, steal_enabled=True)
        vs, ticks = await e.run_sim(change_cur=18, store_cur=25, max_ticks=60000)
        return e, vs, ticks
    e, vs, ticks = asyncio.run(go())
    assert ticks < 60000, f"疑似卡死：跑了满 {ticks} tick"
    assert e.completed_rounds[TID] >= 2, f"多套未持续上交：stats={e.stats}"
    assert e.stats["buy_fail"] == 0, f"紧箱下买卡溢出（Ⅴ 类失败）：stats={e.stats}"
    total = e.stats["total_ticks"]
    busy = e.stats["busy_ticks"]
    assert busy / total > 0.3, f"炉利用率过低：{busy}/{total}"


if __name__ == "__main__":
    test_world_free_counts()
    test_sim_drives_production_auto_refine()
    test_sim_completes_one_round()
    test_sim_no_idle_furnace_utilization()
    test_sim_tight_box_multiset_no_overflow()
    print(f"ALL PASS (TID={TID})")
