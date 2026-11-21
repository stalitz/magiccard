"""倒排构造器（dp_backward）测试。

跑法（项目根目录，跑法二选一）：
  ~/.workbuddy/binaries/python/envs/mc/bin/python3 -m pytest \
      backend/sched_cmp/tests/test_dp_backward.py -q
  python3 backend/sched_cmp/tests/test_dp_backward.py        # 纯 stdlib

★ 这组测试守的是三类真事故：
  1. **机位 class 越权**：150/540 面卡（mclass=(0,)）只能上主炉，只有 40 面卡
     （mclass 含 2）能进偷炉。漏这条会让构造器凭空多出一台炉 → 954 上排出
     836.84min「比 CP-SAT 证明的最优 898.13 还快」的假解（实测过）。
  2. **拿到解不校验就当结论**：构造器是启发式，必须过 validate_pools。
     `_best_slot` 的 deadline / 区间切割写错时，肉眼看不出来。
  3. **齐套约束**：父卡开工必须晚于每个子卡完工（倒排的全部意义）。
"""
from __future__ import annotations

import os
import pickle
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CMP = os.path.dirname(_HERE)
if _CMP not in sys.path:
    sys.path.insert(0, _CMP)

from dp_backward import (EPS, _assign_pools, _best_slot, _take, backward_at,
                         _spread_stats)
from dp_plan3 import validate_pools

INST954 = "/tmp/inst954.pkl"

try:                                        # pragma: no cover
    import pytest
except Exception:                           # noqa: BLE001
    pytest = None


def _inst954():
    inst = pickle.load(open(INST954, "rb"))
    inst = dict(inst)
    inst["machines"] = 5
    return inst


# ---------------------------------------------------------------- _best_slot
def test_best_slot_respects_deadline():
    free = [[(0.0, 100.0)]]
    got = _best_slot(free, 30.0, 50.0)
    assert got is not None
    s, mi, li = got
    assert abs((s + 30.0) - 50.0) < 1e-6      # 尽量贴 deadline
    assert s >= 0.0


def test_best_slot_rejects_negative_start():
    free = [[(0.0, 10.0)]]
    assert _best_slot(free, 30.0, 20.0) is None   # 放不下就是放不下


def test_best_slot_honours_allowed_machines():
    free = [[(0.0, 10.0)], [(0.0, 100.0)]]
    # 只允许 0 号机 → 放不下（10 太短）
    assert _best_slot(free, 30.0, 100.0, allowed=range(1)) is None
    got = _best_slot(free, 30.0, 100.0, allowed=range(2))
    assert got is not None and got[1] == 1


def test_take_splits_intervals_without_overlap():
    free = [[(0.0, 100.0)]]
    _take(free, 0, 0, 20.0, 30.0)
    ivs = free[0]
    assert len(ivs) == 2
    assert ivs[0] == (0.0, 20.0) and ivs[1] == (50.0, 100.0)
    # 再放一块必须落在空隙里，且不与前一块重叠
    s, _mi, _li = _best_slot(free, 25.0, 100.0)
    assert s + 25.0 <= 100.0 + EPS
    assert s >= 50.0 - EPS or s + 25.0 <= 20.0 + EPS


# ---------------------------------------------------------------- 954 端到端
def test_954_backward_feasible_and_checked():
    inst = _inst954()
    r = backward_at(inst, 905.0 * 60)
    assert r is not None
    v = validate_pools(inst, r["sched"], r["pools"], r["buys"])
    assert v["ok"], v["viol"][:4]
    # 157 张卡全部产出、110 张底卡全部下单
    assert len(r["sched"]) == sum(1 for i in range(inst["n"])
                                  if 1 not in tuple(inst["mclass"][i])
                                  for _ in range(inst["need0"][i]))
    assert len(r["buys"]) == sum(inst["need0"][i] for i in range(inst["n"])
                                 if 1 in tuple(inst["mclass"][i]))


def test_954_backward_rejects_impossible_T():
    inst = _inst954()
    assert backward_at(inst, 890.0 * 60) is None     # 机位塞不下


def test_954_no_class_violation():
    """150/540 面卡不得被派进偷炉（这条一旦破，解会比 CP-SAT 最优还好）。"""
    inst = _inst954()
    r = backward_at(inst, 905.0 * 60)
    assert r is not None
    cls = [tuple(c) for c in inst["mclass"]]
    for pool, i, _t in r["pools"]:
        if pool == "steal":
            assert 2 in cls[i], "type %d 的 mclass=%s 不该进偷炉" % (i, cls[i])


def test_954_kit_order_holds():
    """齐套：父卡开工 >= 每个子卡完工（倒排的全部意义）。"""
    inst = _inst954()
    r = backward_at(inst, 905.0 * 60)
    assert r is not None
    ops = r["_ops"]
    is_base = r["_is_base"]
    for op in ops:
        if op["s"] is None:
            continue
        for c in op["kids"]:
            cop = ops[c]
            if is_base[cop["i"]]:
                # 底卡不进炉：它的 s 是「到货时刻」，LAG 已在下单时刻扣掉，
                # 只要求 到货 <= 父开工（不叠工期）。
                assert op["s"] >= cop["s"] - EPS
                continue
            assert op["s"] >= cop["s"] + float(inst["dur"][cop["i"]]) - EPS


def test_954_spread_is_zero_median():
    """倒排的意义就在「子卡贴着父卡开工」：中位数必须是 0（不是小就行）。"""
    inst = _inst954()
    r = backward_at(inst, 905.0 * 60)
    sp = _spread_stats(r["_ops"], [float(x) for x in inst["dur"]], r["_is_base"])
    assert sp["spread_med"] < 1e-6, sp


# ---------------------------------------------------------------- stdlib runner
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ok   %s" % name)
        except AssertionError as exc:
            bad += 1
            print("  FAIL %s: %s" % (name, exc))
        except Exception as exc:                      # noqa: BLE001
            bad += 1
            print("  ERR  %s: %r" % (name, exc))
    print("\n%d passed, %d failed" % (len(fns) - bad, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
