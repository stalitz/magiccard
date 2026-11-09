"""跨轮重叠「峰值 < 2N」闸回归（2026-11-09 用户验收线）。

为什么闸必须放在 DP 的 `_union_need0`：`pipe_sets` 是在 `sets_fn`（生产是
`_calc_sets`）返回**之后**才把 sets 从 1 提到 2 的，`_calc_sets` 里的箱位闸
钳不到它 → 5★67 低炉档会越过 2N。这里用最小构造直接打 `_union_need0`：

  场景 A（越线）：箱内已有本套完整 N 张 → 再排一套 = 2N → 必须拦下；
  场景 B（放行）：箱内 N-1 张 + 顶层在途 → 再排一套 < 2N → 必须照常提套；
  场景 C（消融）：`pipe_2n=False` → 场景 A 应恢复提套（A/B 对照）。

用法：python -m backend.engine_v2.tests._verify_sets_2n
"""
from __future__ import annotations

import sys
from collections import Counter

from backend.engine_v2.dp_controller import DPTickController
from backend.engine_v2.model import ThemeModel


def _ctl(pipe_2n: bool = True) -> DPTickController:
    """不跑 __init__（要连一堆运行态），只造 `_union_need0` 用到的属性。"""
    c = object.__new__(DPTickController)
    m = ThemeModel(178)
    c.models = {178: m}
    c.pipe_sets = True
    c.pipe_min_free = 6
    c.pipe_2n = pipe_2n
    c.pipe_sets_hits = 0
    c.pipe_sets_blocked = 0
    c.pipe_sets_blocked_2n = 0
    c._left_rounds = None
    # 本用例只验证 2N 闸；「快集齐套不吃料」那条保护单独由既有回归覆盖
    c._pipe_consumes_set = lambda *a, **kw: False
    return c


def _run(c: DPTickController, box: dict, pending: Counter):
    return c._union_need0(
        box, pending, [178], {178: 0}, 3,
        credits={}, sets_fn=None, free_main=0, box_free=10,
    )


def main() -> int:
    m = ThemeModel(178)
    n = len(m.ids)
    top = m.top_cid
    box_full = {c: 1 for c in m.ids}                       # 箱内一整套 = N 张
    box_light = {c: 1 for c in m.ids if c != top}          # 箱内 N-1 张
    pending_top = Counter({top: 1})                        # 顶层在途（不算箱内占位）
    errs = []

    # A：越线必须拦
    c = _ctl(True)
    _run(c, box_full, Counter())
    print(f"A 箱内 {sum(box_full.values())} 张 / N={n} / 2N={2*n}："
          f"blocked_2n={c.pipe_sets_blocked_2n} hits={c.pipe_sets_hits}")
    if c.pipe_sets_blocked_2n != 1 or c.pipe_sets_hits != 0:
        errs.append("A：已占 + N >= 2N 时没有拦下提套")

    # B：未越线照常提套
    c = _ctl(True)
    _run(c, box_light, pending_top)
    print(f"B 箱内 {sum(box_light.values())} 张 + 顶层在途："
          f"blocked_2n={c.pipe_sets_blocked_2n} hits={c.pipe_sets_hits}")
    if c.pipe_sets_hits != 1 or c.pipe_sets_blocked_2n != 0:
        errs.append("B：峰 < 2N 却没提套（误伤跨轮重叠）")

    # C：消融开关
    c = _ctl(False)
    _run(c, box_full, Counter())
    print(f"C 消融(pipe_2n=False)：blocked_2n={c.pipe_sets_blocked_2n} hits={c.pipe_sets_hits}")
    if c.pipe_sets_hits != 1 or c.pipe_sets_blocked_2n != 0:
        errs.append("C：MC_PIPE_2N=0 消融未恢复旧行为")

    if errs:
        print("\n✗ 失败：")
        for e in errs:
            print("   -", e)
        return 1
    print("\n✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
