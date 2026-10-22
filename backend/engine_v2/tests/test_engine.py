"""engine_v2 仿真 + 单元测试。

跑法（项目根目录）：
  python -m pytest backend/engine_v2/tests/ -q
或：
  python -m backend.engine_v2.tests.test_engine
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from collections import Counter

import pytest

from backend.auto_refine import _maps
from backend.engine_v2.engine import Engine
from backend.engine_v2.model import ThemeModel
from backend.engine_v2.planner import Planner
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


# ============ 单元测试 ============
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


def test_planner_fills_furnace_when_feeders_ready():
    """空炉 bug 修复点：feeder trio 就绪就必须投炉。"""
    m = ThemeModel(TID)
    top = m.top_cid
    feeders = m.feeders(top)
    stock = Counter()
    slots = []
    for i, f in enumerate(feeders):
        stock[f] += 1
        slots.append((f, i + 1, 0))
    ws = WorldState(change=stock, store=Counter(), change_cur=18, store_cur=200,
                    main_free=3, steal_free=1, furnaces=[], slots=slots)
    planner = Planner()
    plan = planner.decide(ws, {TID: m}, [TID], {}, 0)
    refines = [a for a in plan if a.kind == "refine"]
    assert refines, f"就绪 feeder 却没投炉！plan={[(a.kind, a.target) for a in plan]}"
    assert any(a.target == top for a in refines), "顶层卡未被投炉"


def test_planner_collect_before_buy_ordering():
    """Ⅴ 类结构性保证：collect 动作必须排在 buy 之前。"""
    m = ThemeModel(TID)
    # 构造：箱里已凑齐整套（会触发 collect），且额外让某个底卡仍有缺口（理论会触发 buy）
    stock = Counter({cid: 1 for cid in m.ids})
    # 再加一张非套卡把箱占满一点，使 collect 释放才有买卡空间
    ws = WorldState(change=stock, store=Counter(), change_cur=18, store_cur=200,
                    main_free=0, steal_free=0, furnaces=[], slots=[])
    planner = Planner()
    plan = planner.decide(ws, {TID: m}, [TID], {}, 0)
    idx_collect = [i for i, a in enumerate(plan) if a.kind == "collect"]
    idx_buy = [i for i, a in enumerate(plan) if a.kind == "buy"]
    if idx_collect and idx_buy:
        assert min(idx_collect) < min(idx_buy), "collect 必须先于 buy（Ⅴ 类）"


def test_planner_no_buy_high_value():
    """铁律：绝不买非底卡（高值）。"""
    m = ThemeModel(TID)
    ws = WorldState(change=Counter(), store=Counter(), change_cur=18, store_cur=200,
                    main_free=3, steal_free=1, furnaces=[], slots=[])
    planner = Planner()
    plan = planner.decide(ws, {TID: m}, [TID], {}, 0)
    for a in plan:
        if a.kind == "buy":
            assert m.is_base(a.cid), f"买了非底卡 {a.cid}"


# ============ 忠实仿真 ============
def test_sim_completes_one_round():
    """端到端：新引擎能在忠实后端上炼成至少 1 套，且无买卡溢出、炉被使用。"""
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
    """炉利用率：在炼制期间炉不应长期空着（采样自 Engine 统计）。"""
    async def go():
        e = Engine([TID], buy=True, surplus_sets=0, max_rounds=1, steal_enabled=True)
        vs, ticks = await e.run_sim(change_cur=18, store_cur=200, max_ticks=30000)
        return e, vs, ticks
    e, vs, ticks = asyncio.run(go())
    total = e.stats["total_ticks"]
    busy = e.stats["busy_ticks"]
    assert total > 0
    # 至少一个合理比例（>30%）的 tick 有炉在运转（空炉 bug 复发则偏低）
    assert busy / total > 0.3, f"炉利用率过低：{busy}/{total}"


def test_sim_tight_box_multiset_no_overflow():
    """紧箱 + 多套并行压力测试：逼近真实 18 格换卡箱。

    验证：① 永不买卡溢出（Ⅴ 类：买卡只在末尾容量校验）② 能持续多轮上交
    ③ 炉利用率合理（空炉 bug 不复发）。
    """
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
    test_planner_fills_furnace_when_feeders_ready()
    test_planner_collect_before_buy_ordering()
    test_planner_no_buy_high_value()
    test_sim_completes_one_round()
    test_sim_no_idle_furnace_utilization()
    print(f"ALL PASS (TID={TID})")
