"""离线生成「CP 最优轨迹 tie-break 表」（cid → 最优开工 tick，升序优先）。

用途（2026-10-25 option 2）：
  CP-SAT 的**精确时刻**接执行层是负收益（零容差紧排产，2★ 实测 24.12h vs
  sched_dp 骨架 23.10h）。但 CP 的**开工顺序**是对的——2★ 差分实锤引擎在
  t166 先跑 240 的素材叶、把 233/239 推到 t328，而 CP 在 t162 就让 233/239
  上炉。于是把「顺序」抽出来当静态优先级（`tb`），由 sched_dp 的列表调度
  重新算**带余量的时刻**，执行层保留容差。

输出：backend/sched_cmp/tb_hints.json
  {"<tid>": {"<cid>": <start_tick>, ...}, "__meta__": {...}}

用法：
  PYTHONPATH=. python3 backend/engine_v2/tests/_gen_tb_hints.py [budget] [tid...]
"""
import json
import sys

sys.path.insert(0, ".")

from backend.engine_v2.tests._verify_sched import build
from backend.sched_cmp import cp_sat_plan

TICK = 45.0
OUT = "backend/sched_cmp/tb_hints.json"
STARS = [(1, 85), (2, 62), (3, 59), (4, 60), (5, 709)]


def main():
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    only = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else None
    try:
        with open(OUT) as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("__meta__", {})
    for star, tid in STARS:
        if only and tid not in only:
            continue
        inst = build(tid)
        print(f"[{star}★ tid={tid}] CP 建模 n={inst['n']} "
              f"Σneed0={sum(inst['need0'])} budget={budget}s", flush=True)
        mk, sched, st = cp_sat_plan.solve(inst, time_limit=budget)
        if sched is None:
            print(f"  ✗ 无解 status={st.get('status')}", flush=True)
            continue
        unit = st["unit"]
        types = inst["types"]
        best = {}
        for (i, _u, s, d, _w) in st["detail"]:
            c = int(types[i])
            t = round(s * unit / TICK)
            if c not in best or t < best[c]:
                best[c] = t
        data[str(tid)] = {str(c): int(t) for c, t in sorted(best.items())}
        data["__meta__"][str(tid)] = {
            "star": star, "mk_h": round(mk / 3600.0, 2),
            "bound_h": round(st["bound"] * unit / 3600.0, 2),
            "status": st["status"], "unit_s": unit, "budget": budget,
            "n_types": len(best)}
        with open(OUT, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        print(f"  ✓ {st['status']} mk={mk/3600:.2f}h bound="
              f"{st['bound']*unit/3600:.2f}h n_types={len(best)} → {OUT}",
              flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
