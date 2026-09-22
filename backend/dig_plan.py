"""挖地规划器 —— 纯函数，不碰网络，可离线拿真实状态跑断言验收。

## 实测机制（2026-09-21 定向探针，见 .workbuddy/memory/REFERENCE.md）

**格子 type**：0=空地｜1=特殊(永久墙)｜2=土｜3=石｜4=半开石｜5=岩石(永久墙)

**已探明** ⟺ 上下左右 4 邻（**不含斜角**）中有 ≥1 格空地，精确 1 跳。
  ⚠️ 不能靠「礼物字段有无」判断已探明：顶行（dig_index-5 附近）服务端会抽掉礼物字段，
  但格子其实已探明、礼物也还在。**必须自己按 4 邻规则算**。
  ⚠️ 雾格的真实 type 服务端照样下发 —— 可以拿它**提前规划绕开岩石列**。

**工具**（dig_type）：
  1 锄头：落点须**已探明**且是土/石/半开石。土→空 1 下、半开石→空 1 下、石→半开石（共 2 下）。
         **能推进 dig_index**（挖开 dig_index+1 行的已探明格即可）。
  2 一字型：落点须**空地**。从中心向左右**逐格 1 点伤害**，**遇到「打完仍不是空地」的格子即中断**
        （石 3→4 属未打开 → 阻断；雾格按其真实 type 结算）。可炸开雾格。**不推进 dig_index**。
        ⚠️ 范围**不是固定 ±2**：实测中心右行连续打平 5 格，直到遇石才停 —— 按「逐格传播直到阻断」建模。
  3 九宫格：落点须**空地**。3×3 每格**一次打平**（土/石/半开石/雾 全变空地）。**会推进 dig_index**。
  4 超级炸药：**是格子属性，不是可收的道具**（用户 2026-09-21 口述）。挖开后格子里出现，
        点它就炸开周围，**菱形范围 = 上下左右各 2 格 + 对角线各 1 格**（即 |Δ行|+|Δ列| ≤ 2，共 13 格）。
        ⚠️ 两种传播规则不同（同一用户口述）：
          · **横向/纵向**（每向 2 格）：**逐格传播，遇到石头(3)/半开石(4) 就被挡住**
            （不破坏石头，后面的格子打不到）→ 实际命中数常常 < 13；
          · **斜向**（±1,±1 四格）：**能直接炸石头** —— 石头/半开石也一次打平。
        土(2)/雾 会被打平，岩石(5)/特殊(1) 仍是永久墙。**未实证 act 调用**（没有真实炸弹格可试），
        plan 里作为第一档，失败由调用方回退。**从已探明格就能看出是不是它**。

**永久墙**：type 1 与 5 —— **任何工具都无效**，只能绕行（它们永远变不成空地，也就永远无法被「打开」）。

**地图窗口边界**（实测 2026-09-22）：服务端只下发 8 行 `dig_index-5 .. dig_index+2`。
  炸弹爆炸范围若**探出这 8 行**，服务端返 `code=-1`（desc="system busy"，但属「目标非法」非限流），
  动作不执行、重试无用。故候选落点须满足：九宫格(覆盖行±1) 行∈`[lo+1,hi-1]`、
  超级炸药(覆盖行±2) 行∈`[lo+2,hi-2]`；一字型(横向不出行)/锄头(单格) 不受限。`candidates()` 已据此排除。

**网格礼物 gift_type**（= 大类，gift_id = 类内编号）：65=锄头 66=一字型炸药 68=九宫格炸药。
  这三类**可收**（act=18 收进库存，实测 `65_0_2` → chutou +2），也可**就地引爆**。
  ⚠️ **67=超级炸药不在此列**：它是格子属性，act=18 收不走（act=1 里也没有它的计数器），
  所以它**不算「待收礼物」**，不会触发「先清账再推进」的硬约束。

**硬约束**：行滚出窗口（dig_index-5 之上）会被服务端压平，其上**没挖平/没收的礼物收不回来**。
  实测踩过：推 dig_index 前没收 金币×150 / 经验×30，滚出后永久丢失。
"""

from __future__ import annotations

# ── 格子 type ──
EMPTY, BLOCK, DIRT, ROCK, HALF, GRANITE = 0, 1, 2, 3, 4, 5
DIGGABLE = {DIRT, ROCK, HALF}      # 锄头可挖
OPENABLE = {DIRT, ROCK, HALF}      # 炸药能打开的（含雾格按其真实 type）
WALL = {BLOCK, GRANITE}            # 永久墙

# ── 工具 dig_type ──
CT, LINE, JGG, SUPER = 1, 2, 3, 4
TOOL_NAME = {CT: "锄头", LINE: "一字型炸药", JGG: "九宫格炸药", SUPER: "超级炸药"}
TOOL_KEY = {CT: "chutou", LINE: "line_tnt", JGG: "jgg_tnt", SUPER: None}

# 地里挖出的炸药礼物 → 就地引爆用的 dig_type
BOMB_GIFT_TO_TYPE = {66: LINE, 67: SUPER, 68: JGG}
#: 可被 act=18 收进库存的工具礼物（**67 超级炸药不在内** —— 它是格子属性，收不走）
COLLECTIBLE_TOOL_GIFT = {65: CT, 66: LINE, 68: JGG}
#: 超级炸药的外形半径（菱形）：|Δ行| + |Δ列| ≤ SUPER_R
SUPER_R = 2
#: 超级炸药的**横向/纵向**射线（各 2 格）遇到这些 type 会被「挡住」：
#: 石头(3)/半开石(4) → **阻断且不破坏**（用户 2026-09-21 口述「横向会被石头挡住」，
#: 与斜向的「直接炸石头」正好形成对比 —— 所以横向不吃石头、只被它挡）。
#: 土(2)/雾 会被打平并可继续传播；岩石(5)/特殊(1) 是永久墙，同样阻断。
SUPER_BLOCK_AXIAL = {ROCK, HALF}
#: 超级炸药的**斜向**（±1,±1 四格）：能**直接炸石头** → 石头(3)/半开石(4) 也一次打平。
#: （岩石(5)/特殊(1) 仍是永久墙，任何工具都无效）
SUPER_DIAG = ((-1, -1), (-1, 1), (1, -1), (1, 1))

#: 单元格上「这是超级炸药格」可能出现的字段名。
#: ⚠️ 服务端 act=1 的 `dig_info` 实测**只下发** {j,type,gift_type,gift_id,gift_num}，
#: 没有炸弹标记字段（可能是「没有时才不下发」）。SWF `WaDiGridVO` 的字段名是
#: `isSuperTnt`；这里做**防御式**探测（含 gift_type=67 这条有实证的路径），
#: 未命中就是没有 —— 绝不假设一定存在。
SUPER_KEYS = ("isSuperTnt", "is_super_tnt", "superTnt", "super_tnt", "super")
SUPER_GIFT_TYPE = 67

WINDOW_UP = 5      # 窗口 = dig_index-5 .. dig_index+2
WINDOW_DOWN = 2

# 打分权重（按用户给的目标优先级：探明 > 挖道具 > 收取 > 下推）
W_REVEAL = 1000    # 新探明格
W_GIFT = 500       # 新挖平的「有礼物」格（不挖平就收不走）
W_TOOL_GIFT = 300  # 挖平的礼物本身是工具/炸弹 → 回本
W_OPEN = 150       # 新开空地（推进价值）


# ───────────────────────── 状态 ─────────────────────────

def load(rows: list) -> dict:
    """把 dig_auto.parse_grid() 的 rows 转成规划用的扁平结构。

    `known`（服务端下发了礼物字段）**只用来判断「有没有礼物」**，不参与「是否已探明」。
    `supers` = 超级炸药格（格子属性，点了就地炸，菱形半径 2）。
    """
    types, gifts, supers, lines = {}, {}, set(), []
    for r in rows or []:
        L = int(r["line"])
        lines.append(L)
        for c in r.get("cells") or []:
            j = int(c.get("j") or 0)
            t = int(c.get("type") or 0)
            types[(L, j)] = t
            if c.get("super_tnt"):
                supers.add((L, j))
            if c.get("known"):
                # 已探明格：礼物字段就是真相（gift_type=0 表示没礼物）
                gt = int(c.get("gift_type") or 0)
                # 超级炸药不是可收道具 → 不当礼物记账，免得把它算进「先清账再推进」
                gifts[(L, j)] = 0 if gt == SUPER_GIFT_TYPE else gt
            else:
                gifts[(L, j)] = None      # 未知
    return {"types": types, "gifts": gifts, "supers": supers, "lines": sorted(lines)}


def revealed(types: dict) -> set:
    """已探明 = 上下左右 4 邻中有 ≥1 格空地。"""
    out = set()
    for (L, j) in types:
        for dL, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if types.get((L + dL, j + dj)) == EMPTY:
                out.add((L, j))
                break
    return out


def dig_index(types: dict) -> int:
    """dig_index = 最深的「含至少 1 格空地」的行（实测：一格空地就能把地层推下去）。"""
    return max((L for (L, j), t in types.items() if t == EMPTY), default=-1)


def has_gift_maybe(gifts: dict, p) -> bool:
    """(L,j) 可能藏礼物？None=字段缺失（未知）→ 保守当「可能有」。"""
    g = gifts.get(p)
    if g is None:
        return p in gifts
    return g != 0


# ───────────────────────── 动作模拟 ─────────────────────────

def apply(types: dict, act: dict) -> dict:
    """按实测规则把这个动作施加到 type 网格上，返回新网格。"""
    t = dict(types)
    dt, L, j = act["dig_type"], act["line"], act["col"]

    if dt == CT:
        cur = t.get((L, j))
        if cur in (DIRT, HALF):
            t[(L, j)] = EMPTY
        elif cur == ROCK:
            t[(L, j)] = HALF

    elif dt == LINE:
        for step in (1, -1):
            k = 1
            while True:
                p = (L, j + step * k)
                if p not in t:
                    break
                cur = t[p]
                if cur == EMPTY:
                    k += 1
                    continue
                if cur == ROCK:
                    t[p] = HALF        # 石只掉半血 → 阻断，后面的打不到
                    break
                if cur in OPENABLE:
                    t[p] = EMPTY
                    k += 1
                    continue
                break                  # 岩石(5)/特殊(1) → 墙，阻断

    elif dt == JGG:
        for dL in range(-1, 2):
            for dj in range(-1, 2):
                p = (L + dL, j + dj)
                if p in t and t[p] in OPENABLE:
                    t[p] = EMPTY

    elif dt == SUPER:
        # 菱形：上下左右各 2 格 + 对角线各 1 格。⚠️ 两种传播规则不同（用户 2026-09-21 口述）：
        #   斜向 (±1,±1)：**能直接炸石头** —— 石头/半开石也一次打平；
        #   横向/纵向（各 2 格）：**逐格传播，遇到石头即被挡住**（不破坏、后面的打不到）。
        if t.get((L, j)) in OPENABLE:
            t[(L, j)] = EMPTY          # 被引爆的那格自己也开
        for dL, dj in SUPER_DIAG:
            p = (L + dL, j + dj)
            if p in t and t[p] in OPENABLE:
                t[p] = EMPTY           # 斜向：石头也照炸
        for dL, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            for k in range(1, SUPER_R + 1):
                p = (L + dL * k, j + dj * k)
                if p not in t:
                    break
                cur = t[p]
                if cur in SUPER_BLOCK_AXIAL or cur in WALL:
                    break              # 石头挡住 / 永久墙 → 该方向到此为止
                if cur == EMPTY:
                    continue
                if cur in OPENABLE:
                    t[p] = EMPTY
                    continue
                break
    return t


def cost_of(types: dict, act: dict) -> int:
    """代价 = 消耗的工具个数（锄头挖石要 2 下）。"""
    if act["dig_type"] == CT and types.get((act["line"], act["col"])) == ROCK:
        return 2
    return 1


def _must_clear(lines: list, types: dict, gifts: dict, rev: set, upto: int) -> list:
    """行 ≤ upto 里「滚出去会丢东西」的例外清单（空 = 干净可推进）。"""
    bad = []
    for (L, j) in sorted(types):
        if L > upto:
            continue
        t = types[(L, j)]
        if t in WALL:
            continue                      # 永久墙：不可能有礼物，也永远开不了
        if (L, j) not in rev:
            bad.append(f"L{L}j{j} 未探明(type{t})")
        elif t != EMPTY and has_gift_maybe(gifts, (L, j)):
            bad.append(f"L{L}j{j} 有礼物未挖平(type{t})")
    return bad


# ───────────────────────── 候选动作 ─────────────────────────

def candidates(st: dict, tools: dict, use_tnt: bool = True, exclude: set | None = None) -> list:
    """穷举合法动作：每个合法落点 × 每种还有库存的工具。

    exclude = 不再尝试的 (行,列,工具) / (行,列) 元组（自动循环把失败动作记进来）。

    ⚠️ **窗口边界约束**（实机 2026-09-22 实证）：服务端只下发 8 行窗口
    `dig_index-5 .. dig_index+2`。炸弹的爆炸范围若**探出这个窗口**，服务端直接
    返 `code=-1`（desc="system busy"，但这是「目标非法」不是限流），动作根本不执行。
    所以候选必须保证爆炸范围落在 [lo, hi] 内：
      · 九宫格(±1 行) → 行必须在 [lo+1, hi-1]
      · 超级炸药(±2 行) → 行必须在 [lo+2, hi-2]
      · 一字型(横向，不出行) / 锄头(单格) → 无此约束
    """
    types, gifts, supers = st["types"], st["gifts"], st.get("supers") or set()
    rev = revealed(types)
    exclude = exclude or set()
    lines = st.get("lines") or []
    lo = min(lines) if lines else None
    hi = max(lines) if lines else None

    def in_win(L: int) -> bool:
        return lo is None or (lo <= L <= hi)

    def blast_ok(dt: int, L: int) -> bool:
        """炸弹 (行,dt) 的爆炸范围是否全在可见窗口内。"""
        if dt == JGG:
            return in_win(L - 1) and in_win(L + 1)
        if dt == SUPER:
            return in_win(L - 2) and in_win(L + 2)
        return True  # 锄头 / 一字型 不探出窗口

    def skipped(p, dt):
        return p in exclude or (p[0], p[1], dt) in exclude

    out = []

    # ① 超级炸药格 → 就地引爆（用户要求「优先使用」；不耗库存）
    #    条件：该格已探明（用户：从已探明格就能看出是不是它）+ 爆炸不出窗口
    for p in sorted(supers):
        if skipped(p, SUPER) or p not in rev or not blast_ok(SUPER, p[0]):
            continue
        out.append({"dig_type": SUPER, "line": p[0], "col": p[1],
                    "tier": 0, "why": "引爆已探明的超级炸药格"})

    # ② 地里挖出的炸药礼物（66/68）→ 就地引爆
    for p, g in sorted(gifts.items()):
        if g not in BOMB_GIFT_TO_TYPE or types.get(p) == EMPTY:
            continue
        dt = BOMB_GIFT_TO_TYPE[g]
        if skipped(p, dt) or not blast_ok(dt, p[0]):
            continue
        out.append({"dig_type": dt, "line": p[0], "col": p[1],
                    "tier": 0, "why": f"引爆地里的炸弹礼物 {g}"})

    for p, t in sorted(types.items()):
        L, j = p
        if skipped(p, CT):
            continue
        # ③ 锄头：已探明 且 是土/石/半开石（单格，不探出窗口）
        if t in DIGGABLE and p in rev and int(tools.get("chutou") or 0) > 0:
            out.append({"dig_type": CT, "line": L, "col": j, "tier": 1, "why": "锄头"})
        # ④ 炸药：落点须空地
        if t == EMPTY and use_tnt:
            if int(tools.get("line_tnt") or 0) > 0:
                out.append({"dig_type": LINE, "line": L, "col": j, "tier": 1, "why": "一字型"})
            if int(tools.get("jgg_tnt") or 0) > 0 and blast_ok(JGG, L):
                out.append({"dig_type": JGG, "line": L, "col": j, "tier": 1, "why": "九宫格"})
    return out


def score_action(st: dict, act: dict, clear_margin: int = 1) -> dict:
    """返回 {ok, score, unsafe, gains...}。unsafe 非空表示会丢东西，直接排除。"""
    types, gifts = st["types"], st["gifts"]
    rev0 = revealed(types)
    t2 = apply(types, act)
    rev1 = revealed(t2)

    D2 = dig_index(t2)
    # 动作后窗口 = D2-5 .. D2+2；行 ≤ D2-5 就是「刚滚出/马上滚出」的行
    upto = D2 - WINDOW_UP + (clear_margin - 1)
    unsafe = _must_clear(st["lines"], t2, gifts, rev1, upto)

    new_rev = len(rev1 - rev0)
    new_open = sum(1 for p, v in t2.items() if v == EMPTY and types.get(p) != EMPTY)
    new_gift = 0
    tool_gift = 0
    for p, v in t2.items():
        if v == EMPTY and types.get(p) != EMPTY:
            g = gifts.get(p)
            if g:
                new_gift += 1
                if g in COLLECTIBLE_TOOL_GIFT:
                    tool_gift += 1

    cost = cost_of(types, act)
    raw = (W_REVEAL * new_rev + W_GIFT * new_gift
           + W_TOOL_GIFT * tool_gift + W_OPEN * max(0, new_open - new_gift))
    return {
        "ok": True, "unsafe": unsafe, "cost": cost,
        "score": raw / cost, "raw": raw, "dig_index_after": D2,
        "new_revealed": new_rev, "new_open": new_open, "new_gift": new_gift,
        "tool_gift": tool_gift,
    }


def plan(rows: list, tools: dict, use_tnt: bool = True, clear_margin: int = 1,
         exclude: set | None = None) -> dict:
    """算下一个动作。

    返回 {action, score, reason, alternates, unsafe_blocked}；action=None 表示没事可做。
    选择逻辑：
      1. 炸弹引爆（tier 0）永远第一档，取分最高者；
      2. 其余动作按「收益/代价」贪心；
      3. **硬约束优先**：会丢东西的动作（unsafe 非空）一律排除；若因此全被排除，
         退而求其次只允许「不推进 dig_index」的动作（不推进就绝不丢）。
    """
    if not rows:
        return {"action": None, "reason": "无网格数据", "score": 0, "alternates": []}

    st = load(rows)
    cands = candidates(st, tools, use_tnt, exclude)
    if not cands:
        return {"action": None, "reason": "没有任何合法动作（工具不足或无可挖格）",
                "score": 0, "alternates": []}

    D0 = dig_index(st["types"])
    evald = []
    for a in cands:
        r = score_action(st, a, clear_margin)
        # 零收益动作（打不出任何变化，如全空行扔一字型）直接丢，否则会原地空转
        if r["raw"] <= 0 and a["tier"] != 0:
            continue
        evald.append((a, r))
    if not evald:
        return {"action": None, "reason": "没有能改变局面的动作（可挖格/雾格都没了）",
                "score": 0, "alternates": []}

    safe = [(a, r) for a, r in evald if not r["unsafe"]]
    blocked = len(evald) - len(safe)

    def pick(pool):
        if not pool:
            return None
        # tier 小的优先（炸弹第一档），同档比 score
        pool = sorted(pool, key=lambda x: (x[0]["tier"], -x[1]["score"]))
        return pool[0]

    note = ""
    # 第一档：地里的炸弹，无条件优先
    chosen = pick([(a, r) for a, r in safe if a["tier"] == 0])
    # 第二档：所有「不会丢东西」的动作里取最优（推进与否由分数决定）
    if chosen is None:
        chosen = pick(safe)
    # 第三档：安全池空了（任何动作都会让未清地块滚出窗口）→ 只做不推进地层的
    if chosen is None:
        nopush = [(a, r) for a, r in evald if r["dig_index_after"] <= D0]
        chosen = pick(nopush)
        if chosen is not None:
            note = "（推进动作都会丢东西，先只清窗口内）"
    if chosen is None:
        worst = min(evald, key=lambda x: len(x[1]["unsafe"]))
        return {"action": None, "score": 0, "alternates": [],
                "reason": f"所有动作都会让未清地块滚出窗口（例：{worst[1]['unsafe'][:2]}），"
                          f"手动清一下或放开 clear_margin",
                "unsafe_blocked": blocked}

    a, r = chosen
    alts = sorted([x for x in safe if x[0] is not a],
                  key=lambda x: -x[1]["score"])[:3]
    return {
        "action": a,
        "score": round(r["score"], 1),
        "reason": (f"{TOOL_NAME[a['dig_type']]}@({a['line']},{a['col']}) "
                   f"探明+{r['new_revealed']} 开格+{r['new_open']} "
                   f"（其中礼物格 {r['new_gift']}，工具格 {r['tool_gift']}）"
                   f" 代价{r['cost']} 分{r['score']:.0f} {note}"),
        "eval": r,
        "alternates": [{"action": x[0], "score": round(x[1]["score"], 1),
                        "new_revealed": x[1]["new_revealed"]} for x in alts],
        "unsafe_blocked": blocked,
        "dig_index": D0,
    }
