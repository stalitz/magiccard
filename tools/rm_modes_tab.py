"""串行落盘：删掉「各玩法一键」tab 的死代码 + 把动作后刷新改到对应 tab。

背景（2026-09-27 用户定调）：玩法都有独立 tab 了，「各玩法一键」汇总页没用了 ——
功能要么归到对应 tab，要么删掉。本脚本负责：
  1) 删重复的 arrLen/modeCard 死段 + MODE_RELOAD
  2) 删 modeCard(第二份) / loadMatchModes / renderMatchModes / loadModeProps / renderModeProps
  3) bindMatchModeActs 里删 mm-refresh / mm-props / mm-reload 分支，
     其余分支的 loadMatchModes() 改成刷新对应 tab 自己的视图
  4) 删 bind("match-modes-refresh") 与 matchModes 变量声明

改动量大且分散，按项目惯例用**单脚本一次落盘**（并行 Edit 会互相覆盖）。
"""
import io
import re

P = "/Users/stalitz/www/魔法卡片/frontend/app.js"
src = io.open(P, encoding="utf-8").read()
orig = src


def cut(start_anchor, end_anchor, label, count=1):
    """删除 [start_anchor, end_anchor) 之间的整段。"""
    global src
    i = src.find(start_anchor)
    if i < 0:
        raise SystemExit(f"[FAIL] 找不到起点锚点：{label}")
    j = src.find(end_anchor, i)
    if j < 0:
        raise SystemExit(f"[FAIL] 找不到终点锚点：{label}")
    src = src[:i] + src[j:]
    print(f"  删除 {label}: {len(src[:0]) or ''}{j - i} 字符")


def sub(old, new, label):
    global src
    if old not in src:
        raise SystemExit(f"[FAIL] 找不到：{label}")
    src = src.replace(old, new, 1)
    print(f"  替换 {label}")


# ---------- 1) 重复死段：arrLen(第一份) + modeCard(第一份) ----------
cut("function arrLen(x) {",
    "// 数组长度：接口各端点字段层级不一（data.list / data.data / ...），统一兜底",
    "重复的 arrLen/modeCard 死段（第一份）")

# ---------- 2) MODE_RELOAD 及其注释 ----------
cut("// 本玩法状态属性：每个子块单独展示自己玩法的字段（后端 /api/match/mode-props 分组返回）",
    "// ---- 以下函数自 5e57348 恢复",
    "MODE_RELOAD 与状态属性注释（随 modes tab 一起删）")

# ---------- 3) modeCard(第二份) + loadMatchModes + renderMatchModes ----------
i = src.rfind("function modeCard(title, cnt, rows, acts, mode) {")
if i < 0:
    raise SystemExit("[FAIL] 找不到 modeCard 第二份")
j = src.find("// 本玩法状态属性：每个子块单独展示自己玩法的字段", i)
if j < 0:
    raise SystemExit("[FAIL] 找不到 modes 段结束锚点")
src = src[:i] + src[j:]
print(f"  删除 modeCard/loadMatchModes/renderMatchModes: {j - i} 字符")

# ---------- 4) loadModeProps + renderModeProps ----------
cut("// 本玩法状态属性：每个子块单独展示自己玩法的字段（后端 /api/match/mode-props 分组返回）",
    "// 子块一键领奖：only 填空即全量（原「一键领奖（全玩法）」也走这里）",
    "loadModeProps / renderModeProps")

# ---------- 5) bindMatchModeActs 分支 ----------
sub("""        if (act === "mm-refresh") {
          await loadMatchModes(); await loadMatchLog();
          mlog("[每日比赛] 各玩法状态已刷新", "ok");
        } else if (act === "mm-claim") {
          await claimGroup(b.dataset.only || "");
          await loadMatchModes(); await loadMatchLog();""",
    """        if (act === "mm-claim") {
          await claimGroup(b.dataset.only || "");
          await refreshAfterClaim(b.dataset.only || "");
          await loadMatchLog();""",
    "mm-claim 改为刷新对应 tab（删 mm-refresh 分支）")

sub("""          await loadMatchModes(); await loadMatchPvp();""",
    """          await loadMatchPvpExtra(); await loadMatchPvp();""",
    "mm-flush 刷新竞技场状态")

sub("""          await pvpFight(); await loadMatchModes();""",
    """          await pvpFight();""",
    "mm-fight-pvp（pvpFight 内部已刷新）")

sub("""          await unionFight(); await loadMatchModes();""",
    """          await unionFight(); await loadMatchUnionExtra(); await loadMatchGvg();""",
    "mm-fight-union 刷新工会/公会战")

# mm-props / mm-reload 分支（整段删到 mm-props 分支结束）
i = src.find('        } else if (act === "mm-props") {')
if i < 0:
    raise SystemExit("[FAIL] 找不到 mm-props 分支")
j = src.find("      } finally {", i)
if j < 0:
    raise SystemExit("[FAIL] 找不到 mm 分支结束锚点")
src = src[:i] + "        }\n" + src[j:]
print("  删除 mm-props / mm-reload 分支")

# ---------- 6) claimAll：不再刷新已删的 modes ----------
sub("""async function claimAll() {
  await claimGroup("");
  await loadMatchModes();
}""",
    """async function claimAll() {
  await claimGroup("");
  await refreshAfterClaim("");
}

// 领奖后刷新对应玩法的视图（「各玩法一键」tab 已删，不能再去刷新它）
async function refreshAfterClaim(only) {
  const m = {
    comp: async () => { await loadMatchState(true); await loadMatchLog(); },
    pvp: async () => { await loadMatchPvp(); await loadMatchPvpExtra(); },
    union: async () => { await loadMatchUnion(); await loadMatchUnionExtra(); await loadMatchGvg(); },
    explore: async () => { await loadMatchXiyou(true); },
    sanguo: async () => { await loadMatchSanguo(true); await loadMatchSanguoLevels(); },
  };
  if (only && m[only]) { await m[only](); return; }
  // 全量领奖：刷新当前停留的子模块（其余切过去时会自己拉）
  const fn = MATCH_SUB_LOAD[window.__matchSub || "home"];
  if (fn) fn();
}""",
    "claimAll → refreshAfterClaim")

# ---------- 7) bind("match-modes-refresh") ----------
i = src.find('  bind("match-modes-refresh"')
if i < 0:
    raise SystemExit("[FAIL] 找不到 match-modes-refresh 绑定")
j = src.find("\n", src.find("restore();", i))
src = src[:i] + src[j + 1:]
print("  删除 bind(match-modes-refresh)")

# ---------- 8) matchModes 变量声明 ----------
src = src.replace("matchGold = null, matchGvg = null, matchSanguo = null, matchModes = null,",
                  "matchGold = null, matchGvg = null, matchSanguo = null,")
print("  删除 matchModes 变量声明")

# ---------- 收尾检查 ----------
left = re.findall(r"loadMatchModes|renderMatchModes|matchModes|modeCard|MODE_RELOAD|loadModeProps|renderModeProps", src)
print("  残留引用：", left or "无")
io.open(P, "w", encoding="utf-8").write(src)
print(f"落盘完成：{len(orig)} → {len(src)} 字符")
