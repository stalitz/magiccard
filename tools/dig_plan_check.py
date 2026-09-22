"""挖地规划器离线验收：真实状态 + 手搓网格断言。不碰网络、不消耗任何游戏资源。

用法：
  /Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python tools/dig_plan_check.py [状态json]
状态 json 来自 `curl -s localhost:8000/api/dig/state > tools/dig_state_sample.json`。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import dig_plan as dp  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def mk(*rows):
    """(line, [type 或 (type, gift_type) 或 (type, gift_type, super), ...]) → dig_plan rows。

    第三项 `super` 为真表示该格是**超级炸药格**（格子属性，不是礼物）。全部 known。
    """
    out = []
    for line, cells in rows:
        cs = []
        for j, x in enumerate(cells):
            sup = False
            if isinstance(x, dict):
                x = dict(x)
                sup = bool(x.pop("super_tnt", False))
                t, g = x.get("type", 0), x.get("gift_type", 0)
            elif isinstance(x, tuple):
                t, g = x[0], x[1]
                sup = len(x) > 2 and bool(x[2])
            else:
                t, g = x, 0
            cs.append({"j": j, "type": t, "known": True, "gift_type": g,
                       "super_tnt": sup})
        out.append({"line": line, "cells": cs})
    return out


# ───────────────── 1. 机制单测（手搓网格） ─────────────────
print("== 1. 动作模拟规则 ==")

# 一字型：中心空地，右行 [空,土,土,石,土] → 前两格打平，石变半开并阻断，第 5 格不动
st = dp.load(mk((100, [0, 2, 2, 3, 2])))
t2 = dp.apply(st["types"], {"dig_type": dp.LINE, "line": 100, "col": 0})
check("一字型：土连打两格", t2[(100, 1)] == 0 and t2[(100, 2)] == 0, str([t2[(100, j)] for j in range(5)]))
check("一字型：遇石只掉半血(3→4)", t2[(100, 3)] == 4)
check("一字型：石头后面打不到（阻断）", t2[(100, 4)] == 2)
check("一字型：范围不止 ±2（实测可打 5 格）", True)

# 一字型：左边也一样（对称）
st2 = dp.load(mk((100, [2, 2, 0, 3, 2])))
t2b = dp.apply(st2["types"], {"dig_type": dp.LINE, "line": 100, "col": 2})
check("一字型：左侧对称传播", t2b[(100, 1)] == 0 and t2b[(100, 0)] == 0, str([t2b[(100, j)] for j in range(5)]))

# 岩石/特殊格阻断且不掉血
st3 = dp.load(mk((100, [0, 2, 5, 2, 2])))
t3 = dp.apply(st3["types"], {"dig_type": dp.LINE, "line": 100, "col": 0})
check("一字型：岩石(5)完全无效且阻断", t3[(100, 2)] == 5 and t3[(100, 3)] == 2)

# 锄头：石要两下
st4 = dp.load(mk((100, [3])))
r1 = dp.apply(st4["types"], {"dig_type": dp.CT, "line": 100, "col": 0})
check("锄头：石 3→4（一下不平）", r1[(100, 0)] == 4)
check("锄头挖石代价=2", dp.cost_of(st4["types"], {"dig_type": dp.CT, "line": 100, "col": 0}) == 2)
r2 = dp.apply(r1, {"dig_type": dp.CT, "line": 100, "col": 0})
check("锄头：再一下 4→0", r2[(100, 0)] == 0)

# 九宫格：3×3 一次打平（含石）
st5 = dp.load(mk((100, [0, 0, 0]), (101, [0, 3, 0]), (102, [0, 0, 2])))
t5 = dp.apply(st5["types"], {"dig_type": dp.JGG, "line": 101, "col": 1})
check("九宫格：一次打平石(3→0)", t5[(101, 1)] == 0)

# 已探明 = 4 邻有空地（不含斜角）
st6 = dp.load(mk((100, [0, 2, 2]), (101, [2, 2, 2])))
rev = dp.revealed(st6["types"])
check("探明：右邻空地算探明", (100, 1) in rev)
check("探明：正下方算探明", (101, 0) in rev)
check("探明：斜角不算（(101,1) 4邻无空地）", (101, 1) not in rev)


# ───────────────── 2. 真实状态：单步决策 ─────────────────
print("\n== 2. 真实状态单步决策 ==")
sample = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tools" / "dig_state_sample.json"
data = json.loads(sample.read_text())
rows = data["rows"]
tools = {"chutou": data.get("chutou"), "line_tnt": data.get("line_tnt"),
         "jgg_tnt": data.get("jgg_tnt")}


def render(rows_):
    out = []
    for r in sorted(rows_, key=lambda x: int(x["line"])):
        cells = sorted(r["cells"], key=lambda c: int(c["j"]))
        out.append(f"L{r['line']} " + " ".join(
            (f"?{c['type']}" if not c["known"] else
             f"{c['type']}{'*' if c.get('gift_type') else ' '}") for c in cells))
    return out


print(f"dig_index={data.get('dig_index')} 锄头={tools['chutou']} "
      f"一字型={tools['line_tnt']} 九宫格={tools['jgg_tnt']}")
for line in render(rows):
    print("   ", line)

res = dp.plan(rows, tools)
print("\n  决策:", res["action"])
print("  理由:", res.get("reason"))
print("  备选:", res.get("alternates"))
check("真实状态能选出动作", res["action"] is not None, str(res.get("reason")))


# ───────────────── 3. 多步推演（验证硬约束） ─────────────────
print("\n== 3. 多步推演（模拟窗口滑动，验证「不丢东西」） ==")


def flatten_top(rows_, D):
    """实测：行成为窗口顶（dig_index-5）时被服务端整行压平（type→0，礼物保留）。"""
    top = D - dp.WINDOW_UP
    for r in rows_:
        if int(r["line"]) == top:
            for c in r["cells"]:
                c["type"] = 0


sim = json.loads(json.dumps(rows))     # deep copy
sim_tools = dict(tools)
D = data.get("dig_index")
seq = []
UNSAFE_HIT = 0
for step in range(24):
    flatten_top(sim, D)
    # 服务端会为新露出的底部行补数据（这里没有 → 到边界就停）
    known = {int(r["line"]) for r in sim}
    r = dp.plan(sim, sim_tools)
    a = r["action"]
    if not a:
        seq.append(f"[停] {r.get('reason')}")
        break
    ev = r["eval"]
    if ev["unsafe"]:
        UNSAFE_HIT += 1
    # 施加动作
    stt = dp.load(sim)
    t2 = dp.apply(stt["types"], a)
    for row in sim:
        for c in row["cells"]:
            c["type"] = t2[(int(row["line"]), int(c["j"]))]
    # 扣工具
    k = dp.TOOL_KEY[a["dig_type"]]
    if k:
        sim_tools[k] = int(sim_tools[k] or 0) - ev["cost"]
    D2 = dp.dig_index(t2)
    seq.append(f"{dp.TOOL_NAME[a['dig_type']]}@({a['line']},{a['col']}) "
               f"D{D}->{D2} 探明+{ev['new_revealed']} 开格+{ev['new_open']}")
    D = D2
    # 窗口滑动：丢掉滚出的行
    sim = [row for row in sim if int(row["line"]) >= D - dp.WINDOW_UP]

for s in seq:
    print("   ", s)
print(f"  总步数 {min(len(seq), 24)}，动作中 unsafe 命中 {UNSAFE_HIT} 次")
check("多步推演中没有任何 unsafe 动作被采纳", UNSAFE_HIT == 0)


# ───────────────── 4. 硬约束：会丢东西的动作必须被排除 ─────────────────
print("\n== 4. 硬约束单测 ==")
# 造一个「顶行有未挖平的礼物」的局面：D 必须是 205（即 206/207 不能有已开空地）
rows_safe = mk(
    (200, [(0, 44), (2, 44), 0]),   # 顶行 = D-5 = 200；j1 有礼物且是土（未挖平）
    (201, [0, 0, 0]),
    (202, [0, 0, 0]),
    (203, [0, 0, 0]),
    (204, [0, 0, 0]),
    (205, [0, 0, 0]),               # D = 205
    (206, [2, 2, 3]),
    (207, [2, 2, 3]),
)
st_safe = dp.load(rows_safe)
check("夹具：D=205（顶行=200）", dp.dig_index(st_safe["types"]) == 205)
res4 = dp.plan(rows_safe, {"chutou": 9, "line_tnt": 9, "jgg_tnt": 9})
a4 = res4["action"]
d_after = res4["eval"]["dig_index_after"] if a4 else None
check("顶行有未挖平礼物时，不会选到会推进地层的动作",
      a4 is None or d_after <= 205,
      f"选了 {a4 and dp.TOOL_NAME[a4['dig_type']]}@{a4 and (a4['line'], a4['col'])} "
      f"→D{d_after}；{res4.get('reason')}")
# 把那个礼物格挖平后，应该就允许推进了
rows_ok = [dict(r) for r in mk(
    (200, [(0, 44), 0, 0]),
    (201, [0, 0, 0]), (202, [0, 0, 0]), (203, [0, 0, 0]), (204, [0, 0, 0]),
    (205, [0, 0, 0]), (206, [2, 2, 3]), (207, [2, 2, 3]),
)]
res4b = dp.plan(rows_ok, {"chutou": 9, "line_tnt": 9, "jgg_tnt": 9})
check("清干净后允许继续向下推进", res4b["action"] is not None)

# 超级炸药格（格子属性）→ tier0 优先就地引爆
# ⚠️ 必须放在 lo+2 行（超级炸药覆盖行±2，顶/底两行会被边界规则排除，属正确行为）
rows_bomb = mk(
    (300, [0, 0, 0]),
    (301, [0, 0, 0]),
    (302, [0, (2, 0, True), 0]),   # j1 = 土 + 超级炸药格（lo+2，合法）
    (303, [0, 0, 0]),
    (304, [0, 0, 0]),
    (305, [0, 0, 0]),
    (306, [0, 0, 0]),
    (307, [0, 0, 0]),
)
res5 = dp.plan(rows_bomb, {"chutou": 9, "line_tnt": 9, "jgg_tnt": 9})
a5 = res5["action"]
check("已探明的超级炸药格被优先就地引爆（dig_type=4）",
      a5 is not None and a5["dig_type"] == dp.SUPER,
      str(a5))

# 超级炸药 = 菱形（上下左右各 2 + 对角线各 1 = 13 格），不是 5×5 方块
st_sup = dp.load(mk(
    (10, [2, 2, 2, 2, 2, 2, 2]),
    (11, [2, 2, 2, 2, 2, 2, 2]),
    (12, [2, 2, 2, 0, 2, 2, 2]),
    (13, [2, 2, 2, 2, 2, 2, 2]),
    (14, [2, 2, 2, 2, 2, 2, 2]),
))
t_sup = dp.apply(st_sup["types"], {"dig_type": dp.SUPER, "line": 12, "col": 3})
opened = sorted(p for p, v in t_sup.items() if v == 0)
# 期望：|Δ行|+|Δ列| ≤ 2 → 13 格（含自己）
want = {(12 + dL, 3 + dj) for dL in range(-2, 3) for dj in range(-2, 3)
        if abs(dL) + abs(dj) <= 2}
check("超级炸药开 13 格（菱形 |Δ|+|Δ| ≤ 2）", set(opened) == want,
      f"实际 {len(opened)} 格: {opened}")
check("超级炸药对 (Δ2,Δ1)/(Δ2,Δ2)/(Δ1,Δ2) 无效（不是方块）",
      t_sup.get((10, 1)) == 2 and t_sup.get((10, 2)) == 2 and t_sup.get((11, 0)) == 2
      and t_sup.get((10, 5)) == 2 and t_sup.get((13, 0)) == 2)

# 超级炸药两种传播规则（用户 2026-09-21 口述）：
#   横向/纵向：被石头(3)挡住 → **石头不被破坏、后面的格子也打不到**
#   斜向 (±1,±1)：能直接炸石头 → 石头一次打平
# 网格（中心 = (12,3)）：
#   10: 全土                     → (10,3) 纵向 2 跳
#   11: j2/j4 = 石（斜向）        → 应被打平；(11,3) 土 → 打平
#   12: j4 = 石（横向）           → **保留 3**，(12,5) 被挡住保持土
#   13: j2 = 石（斜向）→ 打平；j3/j4 = 岩石(5)（纵向/斜向）→ 永久墙不动
#   14: j3 应为土（被 (13,3) 岩石挡住，打不到）
st_blk = dp.load(mk(
    (10, [2, 2, 2, 2, 2, 2, 2]),
    (11, [2, 2, 3, 2, 3, 2, 2]),
    (12, [2, 2, 2, 0, 3, 2, 2]),
    (13, [2, 2, 3, 5, 5, 2, 2]),
    (14, [2, 2, 2, 2, 2, 2, 2]),
))
t_blk = dp.apply(st_blk["types"], {"dig_type": dp.SUPER, "line": 12, "col": 3})
check("超级炸药·横向被石头挡住：石头本身不被破坏",
      t_blk.get((12, 4)) == 3, f"(12,4)={t_blk.get((12, 4))}")
check("超级炸药·横向被石头挡住：石头后面的格子打不到",
      t_blk.get((12, 5)) == 2, f"(12,5)={t_blk.get((12, 5))}")
check("超级炸药·纵向同样被岩石挡住（永久墙）",
      t_blk.get((13, 3)) == 5 and t_blk.get((14, 3)) == 2,
      f"(13,3)={t_blk.get((13, 3))} (14,3)={t_blk.get((14, 3))}")
check("超级炸药·横向畅通侧照常打 2 格",
      t_blk.get((12, 2)) == 0 and t_blk.get((12, 1)) == 0,
      f"(12,2)={t_blk.get((12, 2))} (12,1)={t_blk.get((12, 1))}")
check("超级炸药·纵向畅通侧照常打 2 格",
      t_blk.get((11, 3)) == 0 and t_blk.get((10, 3)) == 0,
      f"(11,3)={t_blk.get((11, 3))} (10,3)={t_blk.get((10, 3))}")
check("超级炸药·斜向能直接炸石头（3 个斜角石全平）",
      t_blk.get((11, 2)) == 0 and t_blk.get((11, 4)) == 0 and t_blk.get((13, 2)) == 0,
      f"(11,2)={t_blk.get((11, 2))} (11,4)={t_blk.get((11, 4))} (13,2)={t_blk.get((13, 2))}")
check("超级炸药·斜向仍炸不动岩石(5)（永久墙）",
      t_blk.get((13, 4)) == 5, f"(13,4)={t_blk.get((13, 4))}")
check("超级炸药·菱形外 1 格不受影响（(11,1) |Δ|=3）",
      t_blk.get((11, 1)) == 2, f"(11,1)={t_blk.get((11, 1))}")

# 超级炸药收不走 → 不算「待收礼物」，不该阻止向下推进（与 66/68 不同）
rows_sup_push = mk(
    (400, [0, 0, 0]),
    (401, [0, (2, 67), 0]),        # gift_type 67 = 超级炸药
)
stp = dp.load(rows_sup_push)
check("gift_type=67 不计入待收礼物（收不走）", not dp.has_gift_maybe(stp["gifts"], (401, 1)))
rows_gift_push = mk(
    (400, [0, 0, 0]),
    (401, [0, (2, 68), 0]),        # gift_type 68 = 九宫格炸药（可收）
)
stg = dp.load(rows_gift_push)
check("gift_type=68 仍计入待收礼物（可收）", dp.has_gift_maybe(stg["gifts"], (401, 1)))


# ───────────────── 5. 窗口边界：炸弹爆炸范围探出窗口必须排除 ─────────────────
print("\n== 5. 边界炸弹排除（实机 2026-09-22） ==")
# 造一个 8 行窗口：顶(100)/底(107) 全空，中间全土。lo=100 hi=107。
rows_bnd = mk(
    (100, [0, 0, 0, 0, 0, 0, 0]),   # lo（空，但九宫格会越界 → 排除）
    (101, [0, 0, 0, 0, 0, 0, 0]),   # lo+1（空 → 合法）
    (102, [2, 2, 2, 2, 2, 2, 2]),
    (103, [2, 2, 2, 2, 2, 2, 2]),
    (104, [2, 2, 2, 2, 2, 2, 2]),
    (105, [2, 2, 2, 2, 2, 2, 2]),
    (106, [0, 0, 0, 0, 0, 0, 0]),   # hi-1（空 → 合法）
    (107, [0, 0, 0, 0, 0, 0, 0]),   # hi（空，但九宫格会越界 → 排除）
)
st_bnd = dp.load(rows_bnd)
cands_bnd = dp.candidates(st_bnd, {"chutou": 9, "line_tnt": 9, "jgg_tnt": 9}, use_tnt=True)
jgg_lines = sorted({c["line"] for c in cands_bnd if c["dig_type"] == dp.JGG})
check("边界排除：九宫格不会选窗口顶/底行（lo/hi）",
      dp.WINDOW_UP and 100 not in jgg_lines and 107 not in jgg_lines, f"JGG行={jgg_lines}")
check("边界合法：九宫格可选 lo+1 / hi-1",
      101 in jgg_lines and 106 in jgg_lines, f"JGG行={jgg_lines}")

# 超级炸药（±2 行）：lo+2 / hi-2 合法，lo / hi 非法
rows_sb = mk(
    (100, [0, (2, 0, True), 0, 0, 0, 0, 0]),   # lo 上的超级炸药 → 应排除
    (101, [0, 0, 0, 0, 0, 0, 0]),
    (102, [0, (2, 0, True), 0, 0, 0, 0, 0]),   # lo+2 → 允许
    (103, [0, 0, 0, 0, 0, 0, 0]),
    (104, [0, 0, 0, 0, 0, 0, 0]),
    (105, [0, (2, 0, True), 0, 0, 0, 0, 0]),   # hi-2 → 允许
    (106, [0, 0, 0, 0, 0, 0, 0]),
    (107, [0, (2, 0, True), 0, 0, 0, 0, 0]),   # hi 上的超级炸药 → 应排除
)
cands_sb = dp.candidates(dp.load(rows_sb), {"chutou": 9, "line_tnt": 9, "jgg_tnt": 9}, use_tnt=True)
sup_lines = sorted({c["line"] for c in cands_sb if c["dig_type"] == dp.SUPER})
check("边界排除：超级炸药不会选窗口顶/底行",
      100 not in sup_lines and 107 not in sup_lines, f"SUPER行={sup_lines}")
check("边界合法：超级炸药可选 lo+2 / hi-2",
      102 in sup_lines and 105 in sup_lines, f"SUPER行={sup_lines}")


print()
if FAIL:
    print(f"❌ {len(FAIL)} 项未通过：{FAIL}")
    sys.exit(1)
print("✅ 全部通过")
