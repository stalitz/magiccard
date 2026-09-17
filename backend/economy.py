"""炼卡经济模型（v6）。修正 v5 两个错误：

  BUG1 配方去重: 素材在炼卡时被**消耗**，同一张 40 卡若既是套内卡又是 150 的素材，
       必须炼两次。v5 用 set 去重导致炉时/成本双双低估（七夕 3h 偷位 -> 实际 6h）。
  BUG2 偷位定位: 用户说的"偷位只能炼 40"是**偷位的能力上限**，不是"40 只能偷位炼"。
       主炉同样能炼 40 面值的卡。正确资源模型:
         4 个可并行位 = 3 主炉 + 1 偷位
         产物面值 40 的配方: 4 个位都能做
         产物面值 !=40 的配方: 只能 3 主炉做
       → 约束: Σx*H_oth <= 3 且 Σx*(H_40+H_oth) <= 4

硬参数（用户 2026-09-09 确认）:
  - 卡面额 = 卖价（面值10卖10金）：买 20 卖 10，卖卡必亏，唯一正向路径是集套领奖
  - 商店只卖面值10的卡，20 金币/张，无限量，只看金币够不够
  - 3 主炉 + 2 排队位（排队位不增加并行度）+ 1 偷位
  - 偷位 1 小时/次、不限次数，只能炼产物面值 40 的卡
  - 每集齐全套领一次 theme_Prize，可重复领
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
BUY_PRICE = 20          # 商店面值10卡单价
SELL_FACE = True        # 卖价 = 面额
STEAL_PRICE = 40        # 偷位只能炼产物面值 40
N_MAIN = 3              # 主炉
N_TOTAL = 4             # 主炉 + 偷位

_lib: dict | None = None


def _load() -> dict:
    global _lib
    if _lib is None:
        _lib = json.loads((DATA / "card_lib.json").read_text(encoding="utf-8"))
    return _lib


def kind_of(t) -> str:
    ty = t[12]
    return "发行" if ty in (0, 2) else ("闪卡" if ty == 9 else "下架")


def mrp(ids, comp_by_target, price_of):
    """物料需求展开: 返回 (底层卡计数, 配方执行次数{target: times}, 全部买得到?, 总需求)。

    总需求 demand[cid] = 套内 1 张 + 作为其它配方素材被消耗的次数（自动炼卡调度用）。

    套内每张卡需求 1 张；有配方的卡按需求次数执行配方，素材被消耗 -> 需求累加。
    """
    demand: Counter = Counter({int(c): 1 for c in ids})
    depth: dict[int, int] = {}

    def d(cid: int, stack: set) -> int:
        if cid in depth:
            return depth[cid]
        c = comp_by_target.get(cid)
        if not c or cid in stack:
            depth[cid] = 0
            return 0
        stack.add(cid)
        v = 1 + max((d(int(m), stack) for m in (c[3], c[4], c[5]) if m), default=0)
        stack.discard(cid)
        depth[cid] = v
        return v

    for c in list(demand):
        d(c, set())

    runs: Counter = Counter()
    for cid in sorted(demand, key=lambda x: -depth[x]):
        n = demand[cid]
        if n <= 0:
            continue
        c = comp_by_target.get(cid)
        if not c:
            continue
        runs[cid] += n
        for m in (c[3], c[4], c[5]):
            if m:
                demand[int(m)] += n
    base = Counter({c: n for c, n in demand.items() if c not in comp_by_target})
    ok = all(price_of(c) == 10 for c in base)
    return base, runs, ok, demand


def analyze(include_offline: bool = False):
    lib = _load()
    cards = {int(c[0]): c for c in lib["card_list"]}
    comp = {int(c[2]): c for c in lib["theme_compose_list"]}

    def price_of(cid: int) -> int:
        c = cards.get(cid)
        return int(c[3]) if c and c[3] is not None else 0

    rows = []
    for t in lib["theme_card_list"]:
        ty = t[12]
        if ty == 9:
            continue
        kind = kind_of(t)
        if kind == "下架" and not include_offline:
            continue
        ids = [int(x) for x in (t[11] or []) if x]
        if not ids:
            continue
        try:
            base, runs, ok, _ = mrp(ids, comp, price_of)
        except RecursionError:
            continue
        if not runs or not ok:
            continue
        h40 = sum(n * comp[c][6] / 3600 for c, n in runs.items() if price_of(c) == STEAL_PRICE)
        hot = sum(n * comp[c][6] / 3600 for c, n in runs.items() if price_of(c) != STEAL_PRICE)
        n_base = sum(base.values())
        prize = int(t[6] or 0)
        r = {
            "tid": int(t[0]), "name": str(t[1]).strip(), "star": int(t[2] or 0),
            "kind": kind, "prize": prize, "n_base": n_base,
            "h40": round(h40, 2), "hoth": round(hot, 2),
            "cost": n_base * BUY_PRICE, "profit": prize - n_base * BUY_PRICE,
        }
        # 稳态节拍（LP 单套）: 受 主炉<=3 与 总工时<=4 双重约束
        r["x_max"] = min(3 / r["hoth"] if r["hoth"] > 0 else 9e9,
                         4 / (r["h40"] + r["hoth"]))
        r["rate"] = round(r["profit"] * r["x_max"], 1)
        r["span"] = round(1 / r["x_max"], 2) if r["x_max"] else 0
        rows.append(r)
    return rows


def lp_best(rows, top=12):
    """max Σx*p  s.t. Σx*hoth<=3, Σx*(h40+hoth)<=4。2 约束 -> 极点最多 2 套，枚举。"""
    cand = [r for r in rows if r["profit"] > 0 and r["h40"] + r["hoth"] > 0]
    out = []
    for a in cand:
        out.append((a["rate"], [(a, a["x_max"])]))
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            det = a["hoth"] * (b["h40"] + b["hoth"]) - b["hoth"] * (a["h40"] + a["hoth"])
            if abs(det) < 1e-9:
                continue
            xa = (3 * (b["h40"] + b["hoth"]) - 4 * b["hoth"]) / det
            xb = (4 * a["hoth"] - 3 * (a["h40"] + a["hoth"])) / det
            if xa < -1e-9 or xb < -1e-9:
                continue
            out.append((xa * a["profit"] + xb * b["profit"], [(a, xa), (b, xb)]))
    out.sort(key=lambda z: -z[0])
    return out[:top]


def collect_only(top=15):
    """纯收集套：套内所有卡**都没有配方**，炼不出来，只能靠抽卡/活动/开箱/换卡获得。

    这类套（多数 type=5 下架）单套奖励 5000~19999，是金币的真正大头，
    也解释了"别人几万几万涨"——他们集的是这种套，不是靠炉子炼出来的。
    """
    lib = _load()
    cards = {int(c[0]): c for c in lib["card_list"]}
    comp = {int(c[2]): c for c in lib["theme_compose_list"]}
    pure, mixed = [], []
    for t in lib["theme_card_list"]:
        ids = [int(x) for x in (t[11] or []) if x]
        if not ids:
            continue
        nocomp = sum(1 for c in ids if c not in comp)
        prize = int(t[6] or 0)
        face = int(cards[ids[0]][3]) if ids[0] in cards else 0
        rec = {"tid": int(t[0]), "name": str(t[1]).strip(), "star": int(t[2] or 0),
               "prize": prize, "n": len(ids), "face": face, "kind": kind_of(t)}
        if nocomp == len(ids):
            pure.append(rec)
        elif nocomp:
            mixed.append(rec)
    pure.sort(key=lambda r: -r["prize"])
    return pure, mixed


if __name__ == "__main__":
    rows = analyze()
    print(f"可持续刷的发行套: {len(rows)}（底层卡全为面值10、有配方）")
    by: dict = {}
    for r in rows:
        by.setdefault(r["star"], []).append(r)
    print("\n== 按星级（均值）==")
    print(f"{'星':<3}{'套':<5}{'均奖励':<8}{'均底卡':<8}{'40工时':<8}{'其它工时':<9}{'均净利':<8}{'最优金币/h':<10}")
    for s in sorted(by):
        g = by[s]
        n = len(g)
        f = lambda k: sum(x[k] for x in g) / n
        print(f"{s:<3}{n:<5}{f('prize'):<8.0f}{f('n_base'):<8.0f}{f('h40'):<8.1f}{f('hoth'):<9.1f}"
              f"{f('profit'):<8.0f}{max(x['rate'] for x in g):<10.1f}")

    pos = sorted([r for r in rows if r["profit"] > 0], key=lambda x: -x["rate"])
    print(f"\n== 买卡场景（20金/张）正收益 {len(pos)} 套，按 金币/小时 Top12 ==")
    print(f"{'套卡':<13}{'星':<3}{'奖励':<7}{'底卡':<6}{'成本':<7}{'净利':<7}{'40h':<7}{'其它h':<7}{'节拍':<7}{'金币/h':<8}{'日收益':<7}")
    for r in pos[:12]:
        print(f"{r['name'][:12]:<13}{r['star']:<3}{r['prize']:<7}{r['n_base']:<6}{r['cost']:<7}"
              f"{r['profit']:<7}{r['h40']:<7}{r['hoth']:<7}{r['span']:<7}{r['rate']:<8}{r['rate']*24:<7.0f}")

    print("\n== LP 最优组合（3主炉+1偷位，40卡可放任意位）Top8 ==")
    for rate, combo in lp_best(rows):
        desc = " + ".join(f"{c['name']}({c['star']}★)×{x:.3f}轮/h" for c, x in combo)
        cap = sum(c["cost"] * x for c, x in combo)
        print(f"{rate:7.1f}/h → {rate*24:6.0f}/天  {desc}   [占用本金 {cap:.0f}金/轮]")

    pure, mixed = collect_only()
    print(f"\n== 纯收集套（全卡无配方 -> 炼不出，只能抽/换/活动）{len(pure)} 套，"
          f"Prize 合计 {sum(r['prize'] for r in pure):,} ==")
    print(f"{'套卡':<14}{'星':<3}{'奖励':<8}{'卡数':<6}{'卡面值':<8}{'类型':<6}")
    for r in pure[:10]:
        print(f"{r['name'][:13]:<14}{r['star']:<3}{r['prize']:<8}{r['n']:<6}{r['face']:<8}{r['kind']:<6}")
    print(f"部分卡无配方（需抽/换补齐）: {len(mixed)} 套")
    print(f"反推: 日入1万需 {10000/58.8/24:.1f} 个并行炉位，日入3万需 {30000/58.8/24:.1f} 个（当前 4 个）")

    print("\n== 免费卡场景（底卡不花钱，只比奖励）Top8 ==")
    for r in sorted(rows, key=lambda x: -(x["prize"] * x["x_max"]))[:8]:
        print(f"{r['prize']*r['x_max']:7.1f}/h → {r['prize']*r['x_max']*24:6.0f}/天  "
              f"{r['name']}({r['star']}★) 奖励{r['prize']} 节拍{r['span']}h 底卡{r['n_base']}张")
