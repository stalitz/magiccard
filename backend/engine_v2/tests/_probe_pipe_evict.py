"""预腾位 + 「10 面杂卡优先直卖」A/B 探针（纯本地、只读、不打真机）。

历史背景（2026-11-15 真机 10708702）：change_free=5 < pipe_min_free=6
→ pipe_sets 不提套 → plan ops 空 → _box_compaction 又只在 change_free<=2 时
腾箱 → 换卡箱 12 张非本主题 10 面杂卡永不动 → 2.5h 零动作。

本轮口径（用户「10 面的为什么不卖」）：旧策略「先挪保险箱、挪不动才卖」
把 10 面垃圾搬进保险箱占掉炼成卡落点（真机空位 15→3）。改为
**非本主题 ≤10 面杂卡优先直接卖；>10 面高面值永不卖、只在保险箱有空位时挪**。

用法：python -m backend.engine_v2.tests._probe_pipe_evict
依赖 /tmp/mc_mainpage_1014.json（本机真机快照；丢失时换成任意 change_free≈5、
换卡箱含非本主题 10 面杂卡的 mainpage 快照即可）。BASE 直接引用生产
dp_controller，CAND 引用测试副本 `_dp_controller_candidate.py`；每次改动前
先 `cp dp_controller.py _dp_controller_candidate.py` 再打补丁。
"""
from __future__ import annotations

import copy
import importlib
import json
import time
from collections import Counter
from pathlib import Path

from backend.auto_refine import parse_full, _load
from backend.engine_v2.model import ThemeModel
from backend.engine_v2.world import WorldState
from backend.engine_v2.dp_controller import DPTickController as BaseCtrl

CandCtrl = importlib.import_module(
    "backend.engine_v2._dp_controller_candidate").DPTickController

RAW = Path("/tmp/mc_mainpage_1014.json")
CARDS = {int(c[0]): c for c in _load()["card_list"]}
MODEL = ThemeModel(1652)
OWN = set(MODEL.ids)
PASS, FAIL = [], []


def ok(cond, msg):
    (PASS if cond else FAIL).append(msg)


def make_ctrl(cls):
    c = cls({1652: MODEL}, machines=4, time_budget=2.0,
            time_budget_cold=2.0, warm_solves=10 ** 9,
            steal_enabled=True, sets_fn=lambda t, s, p, f: 1)
    return c


def decide(cls, st, label):
    ws = WorldState.from_parsed(st, now=int(time.time()))
    c = make_ctrl(cls)
    acts, stats = c.decide(ws, [1652], {1652: 1}, 0)
    rows = [(a.kind, a.cid, getattr(a, "n", 1), str(getattr(a, "note", "")))
            for a in acts]
    print(f"  {label:30s} chg_free={ws.change_free} store_free={ws.store_free} "
          f"actions={len(rows)} plan_ops={stats.get('plan_ops')} "
          f"skip={stats.get('skip_solve')} reason={stats.get('reason')} "
          f"pipe_hits={c.pipe_sets_hits}")
    for r in rows[:12]:
        print("     ", r)
    return ws, c, rows, stats


def compact(cls, st, label):
    """绕过 decide，直接验证 _box_compaction 第 3/4 段的卖/挪优先级。"""
    ws = WorldState.from_parsed(st, now=int(time.time()))
    c = make_ctrl(cls)
    c._owner = {cid: MODEL for cid in OWN}
    plan = {"bought": Counter(), "discrepancy": 0}
    avail = {}
    for cid, slot, slottype in ws.slots:
        avail.setdefault(cid, []).append((slot, slottype))
    actions = []
    freed = c._box_compaction(ws, plan, {}, actions, avail, protect={})
    rows = [(a.kind, a.cid, getattr(a, "n", 1), str(getattr(a, "note", "")))
            for a in actions]
    print(f"  {label:30s} chg_free={ws.change_free} store_free={ws.store_free} "
          f"freed={freed} rows={rows[:8]}")
    return ws, c, rows, freed


def sell_safe(rows):
    """铁律：只卖非本主题、0<面额<=10 的卡；挪的卡也必须非本主题。"""
    for kind, cid, _n, note in rows:
        if cid is None or int(cid) <= 0:
            continue                    # refine/collect 动作不带 cid
        t = CARDS.get(int(cid))
        face = int(t[3]) if t else -1
        if kind == "sell":
            if int(cid) in OWN or not (0 < face <= 10):
                return False, f"违规卖出 cid={cid} face={face} note={note}"
        if kind == "move" and int(cid) in OWN:
            return False, f"违规挪动本主题卡 cid={cid} note={note}"
    return True, ""


def main():
    st0 = parse_full(json.loads(RAW.read_text(encoding="utf-8"))["raw"])
    print("== 1. 真机原状（change_free=5，12 张非本主题 10 面杂卡）==")
    _, base_c, base_rows, base_stats = decide(BaseCtrl, st0, "BASE 生产（先挪后卖）")
    _, cand_c, cand_rows, cand_stats = decide(CandCtrl, st0, "CAND 候选（先卖）")
    ok(len(base_rows) > 0 and base_stats.get("reason") == "pre_evict_junk",
       "生产版预腾位仍能解开自锁（回归）")
    ok(all(k == "move" for k, _c, _n, _note in base_rows),
       "★ 生产版旧策略：保险箱有空位 → 全走挪卡、一张不卖")
    ok(len(cand_rows) > 0, "候选第一 tick 有动作（解开自锁）")
    ok(cand_stats.get("reason") == "pre_evict_junk",
       "候选走「预腾位」分支，未绕过排程")
    ok(all(k == "sell" for k, _c, _n, _note in cand_rows),
       "★ 候选新策略：≤10 面杂卡优先直接卖")
    ok(not any(k == "move" for k, _c, _n, _note in cand_rows),
       "★ 候选不再把 10 面垃圾搬进保险箱")
    ok(len(cand_rows) == cand_c.pipe_min_free - 5,
       f"只腾到 pipe_min_free=6 就停（发了 {len(cand_rows)} 个）")
    good, why = sell_safe(cand_rows)
    ok(good, "候选动作守住只卖非本主题 ≤10 面：" + (why or "OK"))

    print("== 2. 第二 tick：卖掉 1 张杂卡后（change_free=6）==")
    st1 = copy.deepcopy(st0)
    sold_cid = cand_rows[0][1]
    st1["change"] = [c for c in st1["change"]
                     if int(c["id"]) != int(sold_cid)]
    _, c1, rows1, stats1 = decide(CandCtrl, st1, "CAND 第二 tick")
    ok(c1.pipe_sets_hits >= 1, "第二 tick 流水线提套成功")
    ok((stats1.get("plan_ops") or 0) > 0, "第二 tick 计划非空")
    ok(any(k == "refine" for k, _c, _n, _note in rows1),
       "第二 tick 真的开始投炉炼卡")
    ok(any(k == "buy" for k, _c, _n, _note in rows1),
       "第二 tick 按 JIT 买底料")
    ok(c1.pipe_sets_blocked_2n == 0, "2N 闸未拦（峰值合规）")
    ok(stats1.get("reason") != "pre_evict_junk",
       "第二 tick 不再重复预腾位（无每 tick 卖卡震荡）")
    good, why = sell_safe(rows1)
    ok(good, "第二 tick 动作合规：" + (why or "OK"))

    print("== 3. 边界矩阵：不触发时行为必须与生产版完全一致 ==")
    # a) 物理空位充足（删 4 张杂卡 → change_free=9）
    st_a = copy.deepcopy(st0)
    st_a["change"] = st_a["change"][:len(st_a["change"]) - 4]
    _, _, base_rows_a, _ = decide(BaseCtrl, st_a, "a) BASE 对照")
    _, _, rows_a, stats_a = decide(CandCtrl, st_a, "a) 空位充足")
    ok(rows_a == base_rows_a, "a) 空位充足时与生产版动作完全一致")
    ok(stats_a.get("reason") != "pre_evict_junk" and
       not any(k in ("move", "sell") for k, _c, _n, _note in rows_a),
       "空位充足时不预腾位")

    # b) 换卡箱全是本主题 40 面卡（无可腾杂卡）→ 不得挪/卖
    st_b = copy.deepcopy(st0)
    st_b["change"] = [{"id": cid, "slot": i}
                      for i, cid in enumerate([23927, 23926, 23925,
                                               23924, 23923, 23922,
                                               23921, 23920, 23924,
                                               23923, 23922, 23921])]
    _, _, base_rows_b, _ = decide(BaseCtrl, st_b, "b) BASE 对照")
    _, _, rows_b, stats_b = decide(CandCtrl, st_b, "b) 全是本主题卡")
    ok(rows_b == base_rows_b, "b) 无可腾杂卡时与生产版动作完全一致")
    ok(not any(k in ("sell", "move") for k, _c, _n, _note in rows_b),
       "无可腾杂卡时不动本主题卡")
    ok(stats_b.get("reason") != "pre_evict_junk",
       "无可腾杂卡时不走预腾位分支")

    # c) 保险箱满 + 杂卡全是高面值 → 绝不卖高面值
    st_c = copy.deepcopy(st0)
    st_c["store"] = [{"id": 10139, "slot": i}
                     for i in range(59)]           # 保险箱塞满
    st_c["change"] = [{"id": 10139, "slot": i}
                      for i in range(12)]           # 588 面杂卡
    _, _, base_rows_c, _ = decide(BaseCtrl, st_c, "c) BASE 对照")
    _, _, rows_c, _ = decide(CandCtrl, st_c, "c) 满箱+高面值杂卡")
    ok(rows_c == base_rows_c, "c) 高面值不可卖时与生产版动作完全一致")
    ok(not any(k == "sell" for k, _c, _n, _note in rows_c),
       "保险箱满且只有高面值杂卡时绝不卖")
    good, why = sell_safe(rows_c)
    ok(good, "c) 动作合规：" + (why or "OK"))

    # d) 保险箱满 + 杂卡全是 10 面 → 允许卖 1 张解锁
    st_d = copy.deepcopy(st_c)
    st_d["change"] = [{"id": 7462, "slot": i}
                      for i in range(12)]           # 10 面非本主题
    _, _, rows_d, _ = decide(CandCtrl, st_d, "d) 满箱+10面杂卡")
    ok(any(k == "sell" for k, _c, _n, _note in rows_d),
       "保险箱满时直卖非本主题 10 面杂卡解锁")
    good, why = sell_safe(rows_d)
    ok(good, "d) 卖出合规：" + (why or "OK"))

    # e) 混合杂卡（10 面 + 588 面）：只卖 10 面，高面值只挪不卖
    st_e = copy.deepcopy(st0)
    st_e["change"] = ([{"id": 7462, "slot": i} for i in range(6)]
                      + [{"id": 10139, "slot": i + 6} for i in range(6)])
    _, _, rows_e, stats_e = decide(CandCtrl, st_e, "e) 混合杂卡")
    sells_e = [r for r in rows_e if r[0] == "sell"]
    ok(len(sells_e) > 0, "e) 混合杂卡场景仍优先卖 10 面")
    ok(all(CARDS[int(r[1])][3] == 10 for r in sells_e),
       "e) 卖出的全是 10 面卡")
    ok(not any(k == "move" for k, _c, _n, _note in rows_e),
       "e) 10 面够腾位时不动高面值杂卡、不占保险箱")
    good, why = sell_safe(rows_e)
    ok(good, "e) 动作合规：" + (why or "OK"))

    print("== 4. 直接验证 _box_compaction 第 3/4 段（箱紧 ≤2 格）==")
    # f) change_free=1、换卡箱 4 张 10 面杂卡 + 12 张 588 面杂卡、保险箱 15 空位
    #    两版都只需腾 3 格（want = 4-1），对照「挪 vs 卖」的选择。
    st_f = copy.deepcopy(st0)
    st_f["change"] = ([{"id": 7462, "slot": i} for i in range(4)]
                      + [{"id": 10139, "slot": 4 + i} for i in range(12)])
    _, _, rows_f, freed_f = compact(BaseCtrl, st_f, "f) BASE 腾箱")
    _, _, rows_g, freed_g = compact(CandCtrl, st_f, "f) CAND 腾箱")
    ok(rows_f and all(k == "move" for k, _c, _n, _note in rows_f),
       "★ 生产版箱紧时全是挪卡，一张不卖")
    ok(rows_g and all(k == "sell" for k, _c, _n, _note in rows_g),
       "★ 候选箱紧时 10 面杂卡改直卖、完全不占保险箱")
    ok(len(rows_f) == len(rows_g) == 3 and freed_g == freed_f == 3,
       f"两版腾位数一致（BASE={freed_f}/{len(rows_f)} "
       f"CAND={freed_g}/{len(rows_g)}，应为 3）")
    good, why = sell_safe(rows_g)
    ok(good, "f) 腾箱动作合规：" + (why or "OK"))

    # g) 保险箱满 + 全是高面值杂卡 + change_free=1 → 停手报告
    st_h = copy.deepcopy(st_c)
    st_h["change"] = [{"id": 10139, "slot": i} for i in range(16)]
    _, _, rows_h, freed_h = compact(CandCtrl, st_h, "g) 满箱+高面值")
    ok(freed_h == 0 and not any(k == "sell" for k, _c, _n, _note in rows_h),
       "g) 无解时停手报告，绝不卖高面值、不硬排")

    print("\n== 结果 ==")
    for m in PASS:
        print("  PASS", m)
    for m in FAIL:
        print("  FAIL", m)
    print(f"  {len(PASS)} PASS / {len(FAIL)} FAIL")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
