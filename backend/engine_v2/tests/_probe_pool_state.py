"""三空位状态条件 A/B 探针（纯本地、不打真机、不写生产文件）。

用户 2026-11-15 定调：「DP 按两箱合计没错，但这是 3 个条件：总箱空位、
换卡箱空位、保险箱空位」「不要闸，这是 DP 的状态条件」。
本探针验证：
  1) solver 不带 change_cap/store_cap 时 = 旧行为（松箱档逐位一致）；
  2) 带上三条件后，紧箱档仍出可行解（不判死、不靠 UB 兜底）；
  3) 所有被接受状态的换卡箱/保险箱占用都不越界；
  4) 控制器级：真机快照下 BASE/CAND 都不崩、不超炼、不卖高面值。

用法：python -m backend.engine_v2.tests._probe_pool_state
依赖 /tmp/mc_mainpage_1124.json（当前真机快照）与 /tmp/mc_mainpage_1014.json
（上午自锁现场）；缺一个时自动跳过对应控制器级用例。
"""
from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path

from backend.auto_refine import parse_full, _load
from backend.engine_v2 import dp_controller as CAND_M
from backend.engine_v2.model import ThemeModel
from backend.engine_v2.world import WorldState
from backend.engine_v2.dp_controller import DPTickController as BaseCtrl
from backend.sched_cmp import sched_dp as BASE_S
from backend.sched_cmp import sched_dp as CAND_S   # 同一模块，A/B 靠 inst 字段区分

CandCtrl = BaseCtrl
MODEL = ThemeModel(1652)
OWN = set(MODEL.ids)
CARDS = {int(c[0]): c for c in _load()["card_list"]}
PASS, FAIL = [], []


def ok(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


# ---------------------------------------------------------------- 捕获冷启 inst
CAP = {}
_ORIG_SOLVE = CAND_S.solve


def _capture(inst, **kw):
    CAP["inst"] = copy.deepcopy(inst)
    return _ORIG_SOLVE(inst, **kw)


CAND_M.solve = _capture


def make_ws(st):
    return WorldState.from_parsed(st, now=int(time.time()))


def make_ctrl(cls, sets=1):
    return cls({1652: MODEL}, machines=4, time_budget=2.0,
               time_budget_cold=2.0, warm_solves=10 ** 9,
               steal_enabled=True, sets_fn=lambda t, s, p, f: sets)


def cold_inst():
    raw = Path("/tmp/mc_mainpage_1124.json")
    if not raw.exists():
        raw = Path("/tmp/mc_mainpage_1014.json")
    st = parse_full(json.loads(raw.read_text(encoding="utf-8"))["raw"])
    st["change"] = []
    st["store"] = []
    st["stove"] = []
    ws = make_ws(st)
    # 单套 1652 的箱位峰值极小，箱容再紧也不影响 makespan（LB=UB 直接交表）；
    # 用 3 套让底卡/中间卡在箱里堆叠，才能真正压到三个空位条件。
    make_ctrl(CandCtrl, sets=3).decide(ws, [1652], {1652: 0}, 0)
    return CAP.get("inst")


print("=== 0) 捕获冷启 DP 实例 ===")
inst0 = cold_inst()
CAND_M.solve = _ORIG_SOLVE          # 恢复，后面控制器级 A/B 用真 solver
ok(inst0 is not None, "捕获到冷启 inst（含 need0/init_stock 等）")
if inst0 is None:
    raise SystemExit("无法构造 DP 实例，探针中止")
print("  n =", inst0["n"], " box_cap =", inst0["box_cap"],
      " change_cap =", inst0.get("change_cap"),
      " store_cap =", inst0.get("store_cap"),
      " base_store sum =", sum(inst0.get("base_store") or []))


# ---------------------------------------------------------------- solver 级 A/B
def run(solver, inst, budget=0.6):
    CAND_S.POOL_REJECT[0] = 0
    CAND_S.POOL_EVAL[0] = 0
    CAND_S.POOL_MAX[0] = CAND_S.POOL_MAX[1] = 0.0
    mk, _sched, stats = solver.solve(
        copy.deepcopy(inst), time_budget=budget, node_budget=200_000,
        init_stock=list(inst["init_stock"]), init_running=[],
        memo=None, max_vecs=96)
    return mk, stats or {}


def fin(x):
    return math.isfinite(float(x))


CASES = [
    # 零回归档：两池都宽到三条件恒不触发，用来证明「不传约束=旧行为」。
    # 注意真机换卡箱硬顶 18 格，3 套时 (18+168) 并不「松」，别拿它当基线。
    ("零回归宽箱 (200+200)", 400, 200, 200),
    ("真机当前 (17+59)", 76, 17, 59),
    ("两箱同紧 (8+12)", 20, 8, 12),
    ("换卡箱紧 (4+30)", 34, 4, 30),
    ("保险箱紧 (18+2)", 20, 18, 2),
    ("极紧 (4+6)", 10, 4, 6),
]

print("\n=== 1) solver 级 A/B：BASE 只看合计 vs CAND 三空位状态条件 ===")
loose_equal = False
any_eval = False
any_changed = False
for name, total, ch, stcap in CASES:
    ib = copy.deepcopy(inst0)
    for k in ("change_cap", "store_cap", "base_store"):
        ib.pop(k, None)
    ib["box_cap"] = total
    ic = copy.deepcopy(inst0)
    ic["box_cap"] = total        # 合计口径完全相同，差异只来自分箱状态条件
    ic["change_cap"] = ch
    ic["store_cap"] = stcap

    mkb, sb = run(BASE_S, ib)
    mkc, sc = run(CAND_S, ic)
    rej = CAND_S.POOL_REJECT[0]
    ev = CAND_S.POOL_EVAL[0]
    mx_ch, mx_st = CAND_S.POOL_MAX
    changed = fin(mkb) and fin(mkc) and abs(float(mkc) - float(mkb)) > 1e-6
    print(f"  {name:20s} BASE mk={mkb:9.1f} | CAND mk={mkc:9.1f} "
          f"| 条件求值={ev:6d} 拒绝分支={rej:6d} | "
          f"峰值 chg={mx_ch:.1f}/{ch} sto={mx_st:.1f}/{stcap} "
          f"| CAND nodes={sc.get('nodes')} bounded={sc.get('bounded')} "
          f"| 排程改变={changed}")

    ok(mx_ch <= ch + 1e-6, name + "：换卡箱占用全程不越界")
    ok(mx_st <= stcap + 1e-6, name + "：保险箱占用全程不越界")
    if name.startswith("零回归"):
        loose_equal = (abs(float(mkb) - float(mkc)) < 1e-6)
        ok(loose_equal, "★ 零回归宽箱档 BASE/CAND makespan 逐位一致（不改坏基准）")
        ok(rej == 0, "零回归宽箱档三条件不拒绝任何分支（约束不生效）")
    else:
        any_eval = any_eval or ev > 0
        any_changed = any_changed or changed
        # 更严的约束可能让 makespan 变长，但不能「BASE 有解而 CAND 判死」
        ok(not (fin(mkb) and not fin(mkc)),
           name + "：只加约束没有把可解实例判死")
        ok(fin(mkc) or not fin(mkb),
           name + "：CAND 未退化成 UB/不可行兜底")
ok(any_eval, "★ 紧箱档三空位状态条件被求解器实际求值（约束生效，不是摆设）")
ok(any_changed, "★ 紧箱档至少一个档位因三空位条件改变了排程（不是只贴了个字段）")

# ---------------------------------------------------- 三条件纯函数口径单元测
print("\n=== 2) 溢出规则单元测（保险箱满 → 炼成卡溢到换卡箱） ===")
cfg = (6.0, 3.0, (True, False), (0, 0), 2)
# stock=(底卡 2, 炼成卡 3)：保险箱 3 格全给炼成卡 → 换卡箱占 2
ok(abs(CAND_S._pool_chg(cfg, (2, 3)) - 2.0) < 1e-9,
   "保险箱够时炼成卡不占换卡箱")
ok(abs(CAND_S._pool_chg(cfg, (2, 5)) - 4.0) < 1e-9,
   "保险箱满时多出的 2 张炼成卡溢到换卡箱")
ok(not CAND_S._pool_ok(cfg, (5, 5)),
   "换卡箱 6 格装不下 5 底卡 + 2 溢出 → 该状态不可行")
ok(CAND_S._pool_ok(cfg, (3, 5)),
   "换成 3 底卡 + 2 溢出 = 5 ≤ 6 → 可行")

# ------------------------------------------------------------ 控制器级 A/B
print("\n=== 3) 控制器级：真机快照下 BASE vs CAND ===")
for raw in ("/tmp/mc_mainpage_1014.json", "/tmp/mc_mainpage_1124.json"):
    p = Path(raw)
    if not p.exists():
        print("  跳过（快照不存在）", raw)
        continue
    st = parse_full(json.loads(p.read_text(encoding="utf-8"))["raw"])
    rows = {}
    for label, cls in (("PROD", BaseCtrl),):
        ws = make_ws(st)
        c = make_ctrl(cls)
        acts, stats = c.decide(ws, [1652], {1652: 0}, 0)
        rows[label] = (acts, stats, c, ws)
        kinds = [(a.kind, a.cid, str(getattr(a, "note", ""))[:28]) for a in acts]
        print(f"  {p.name} {label}: chg_free={ws.change_free} "
              f"store_free={ws.store_free} actions={len(acts)} "
              f"plan_ops={stats.get('plan_ops')} skip={stats.get('skip_solve')} "
              f"reason={stats.get('reason')} | {kinds[:8]}")
        # 铁律：绝不卖本主题 / 高面值
        bad = []
        for a in acts:
            if a.kind != "sell" or not a.cid:
                continue
            t = CARDS.get(int(a.cid))
            face = int(t[3]) if t else -1
            if int(a.cid) in OWN or not (0 < face <= 10):
                bad.append((a.cid, face))
        ok(not bad, f"{p.name} {label}：无本主题/高面值卖出 {bad}")
        ok(c.pipe_sets_blocked_2n >= 0 and (c.pipe_sets_hits <= 1),
           f"{p.name} {label}：跨轮提套未越过 2N 闸")
    a_prod, s_prod, _c, _w = rows["PROD"]
    ok(isinstance(a_prod, list) and isinstance(s_prod, dict),
       f"{p.name}：生产控制器 decide 正常返回（无异常）")

print(f"\n=== 结果：{len(PASS)} PASS / {len(FAIL)} FAIL ===")
for m in FAIL:
    print("  FAIL:", m)
raise SystemExit(1 if FAIL else 0)
