#!/usr/bin/env python3
"""app.js：把「每日比赛」改回纯粹的比赛模块（2026-09-26）。

与 index.html 的 rebuild_match_pane.py 配套，两侧必须一起改：

  · 删掉已删面板（猫窝 / PVP 日榜 / 工会 / 公会战 / 西游三国 / 各玩法一键）
    对应的孤儿函数：渲染与加载函数死引用不存在的 DOM，留着只会埋雷
  · 删掉 bind 到不存在函数的按钮（feedAllCats 全项目都没有定义 = 死按钮）
  · 重写 initMatchOnce：一个 init + 一个轮询，不再有子模块切换
  · 新增：一键比赛 runAllMatch() + 主题卡 renderMatchTheme()/loadMatchTheme()

按「行首顶层函数 + 大括号配平」删函数，避免正则误伤内部同名子串。
"""
import io, os

ROOT = "/Users/stalitz/www/魔法卡片"
JS = os.path.join(ROOT, "frontend/app.js")
SRC = io.open(JS, encoding="utf-8").read()
LINES = SRC.split("\n")


def find_block_end(start: int) -> int:
    """从函数头行（0-based）起，按大括号配平找到它的结束行（含）。"""
    depth = 0
    for i in range(start, len(LINES)):
        for ch in LINES[i]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
    return len(LINES) - 1


def index_of_decl(name: str) -> int:
    for i, ln in enumerate(LINES):
        if ln.startswith(f"function {name}(") or ln.startswith(f"async function {name}("):
            return i
    raise SystemExit(f"找不到声明: {name}")


def drop(names):
    """从后往前删，返回删掉的行数。"""
    for name in sorted(names, key=lambda n: -index_of_decl(n)):
        s = index_of_decl(name)
        e = find_block_end(s)
        del LINES[s:e + 1]
    return len(names)


# ---------------- 1) 删孤儿函数 ----------------
DROP = [
    "renderMatchPvp", "renderMatchUnion", "renderMatchGvg", "renderMatchSanguo",
    "renderMatchGold", "loadMatchPvp", "loadMatchUnion", "loadMatchGvg",
    "loadMatchSanguo", "loadMatchModes", "renderMatchModes", "loadModeProps",
    "renderModeProps", "claimGroup", "pvpFight", "unionFight", "sanguoRun",
    "bindMatchModeActs", "claimAll", "loadMatchGold", "switchMatchSub",
    "matchStopPoll",
]
print("  删除函数:", ", ".join(DROP))
drop(DROP)

# ---------------- 2) 重写 initMatchOnce ----------------
s = index_of_decl("initMatchOnce")
e = find_block_end(s)

INIT = '''function initMatchOnce() {
  if (window.__matchInited) return;
  window.__matchInited = true;
  const bind = (id, fn) => {
    const b = $(id);
    if (b) b.onclick = (ev) => { fn(b, ev); };
  };
  // 右栏日志：进页面就把上次留下的记录填回去（与总览页全局日志互不干扰）
  mlogHistory();
  bind("mlog-clear", () => mlogClear());
  // 手动按钮：点下立即禁用 + 「处理中」，完成立即刷新（项目铁律）
  bind("match-refresh", (b) => busyBtn(b, "读取中…", "刷新状态")(loadMatchState(true)));
  bind("match-equip", async (b) => {
    const restore = busyBtn(b, "读取中…", "装备详情");
    const r = await fetch("/api/match/state?want_equip=1").then((x) => x.json()).catch(() => null);
    matchState = r || matchState;
    renderMatchState();
    mlog("[每日比赛] 装备详情：" + (r && r.equip ? "已读取 " + Object.keys(r.equip).length + " 组属性" : (r && r.message) || "空"),
      r && r.equip ? "ok" : "warn");
    restore();
  });
  // 一键比赛：打满今日剩余场次 + 顺带领奖（会消耗 performe_num，函数内已有 confirm）
  bind("match-runall", (b) => busyBtn(b, "比赛进行中…", "一键比赛（打满今日场次 + 领奖）")(runAllMatch()));
  // 单步出战（不消耗额外次数，但 act=4 会扣 performe_num）
  bind("match-race", (b) => busyBtn(b, "出战中…", "打一场（当前装）")(raceOnce()));
  bind("match-dress", (b) => busyBtn(b, "计算中…", "最优穿搭建议")(equipBest()));
  bind("match-dressrace", (b) => busyBtn(b, "出战中…", "按最优装打一场")(equipThenRace()));
  bind("match-claim-all", (b) => busyBtn(b, "领奖中…", "一键领奖")(claimOnly()));
  // 首次进入即拉主题 + 状态 + 出战记录；之后 60s 兜底刷新
  loadMatchTheme();
  loadMatchState(true);
  loadMatchLog();
  if (!matchMainTimer) {
    matchMainTimer = setInterval(() => {
      const pane = $("pane-match");
      if (!pane || pane.classList.contains("hidden")) return;
      loadMatchState(true); loadMatchTheme(); loadMatchLog();
    }, 60000);
  }
}'''
LINES[s:e + 1] = INIT.split("\n")
print("  重写 initMatchOnce")

# ---------------- 3) 追加新函数（放在 raceOnce 之后） ----------------
r0 = index_of_decl("raceOnce")
r1 = find_block_end(r0)
NEW_FUNCS = '''
// ---------------- 一键比赛（2026-09-26）----------------
// 后端 run_match_all() 负责「连续打到次数用完」，前端这里补上领奖与状态刷新。
// 会消耗 performe_num，调用前必须 confirm（项目铁律：写操作让用户确认）。

function raceLine(r) {
  if (!r) return "-";
  if (r.err) return r.err;
  return `#${r.cat_id} · 总分 ${r.total_value ?? "-"} · 评级 ${r.level_score ?? "-"} · 覆盖 ${r.cover ?? "-"} · 余 ${r.left ?? "-"}`;
}

async function runAllMatch() {
  const before = (matchState && matchState.performe_num) || 0;
  if (!confirm(`一键比赛将连续打完今日剩余场次（当前剩余 ${before} 次），并顺带领取各玩法奖励。\\n`
    + "会真实消耗比赛次数，确认继续？")) return;
  mlog("[一键比赛] 开始：先读主题与最优穿装，再逐场出战…", "ok");
  const r = await fetch("/api/match/run-all", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }).then((x) => x.json()).catch(() => null);
  if (!r || r.ok === false) {
    mlog("[一键比赛] 失败：" + ((r && r.message) || "网络异常"), "err");
    await loadMatchState(true);
    return;
  }
  const races = r.races || [];
  if (!races.length) {
    mlog("[一键比赛] 一场没打成：" + (r.message || "无出战记录"), "warn");
  } else {
    for (const x of races) mlog("[一键比赛] 第 " + (races.indexOf(x) + 1) + " 场 " + raceLine(x), "ok");
  }
  mlog(`[一键比赛] 完成：打了 ${r.ran || races.length} 场，剩余次数 ${r.left_before ?? "-"} → ${r.left_after ?? "-"}`, "ok");
  // 领奖零消耗，打完顺手领
  await claimOnly();
  await loadMatchState(true);
  await loadMatchTheme();
}

// 只领奖：跑现成的 /api/match/claim-all（零消耗，各玩法独立，失败不影响其他项）
async function claimOnly() {
  const r = await fetch("/api/match/claim-all", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }).then((x) => x.json()).catch(() => null);
  const items = (r && r.items) || [];
  if (!items.length) { mlog("[一键领奖] 没有可领的奖励项", "warn"); return false; }
  let ok = 0;
  for (const it of items) {
    if (it.ok) { ok += 1; mlog("[领奖] " + it.k + " → " + (it.content || "成功"), "ok"); }
    else mlog("[领奖] " + it.k + " 失败：" + (it.err || it.desc || "失败"), "warn");
  }
  mlog("[一键领奖] 成功 " + ok + " / " + items.length, ok ? "ok" : "");
  return ok > 0;
}

// ---------------- 今日主题与最优穿装（比赛页新卡） ----------------

function renderMatchTheme() {
  const box = $("match-theme-box"), cnt = $("match-theme-cnt");
  if (!box) return;
  const d = matchTheme || {};
  if (!d.ok) {
    box.className = "state-box";
    box.innerHTML = `<span style="color:var(--danger)">${(d.message || "读取失败")
      + (d.code ? "（code=" + d.code + "）" : "")}</span>`;
    if (cnt) cnt.textContent = "失败";
    return;
  }
  const th = d.theme || [];
  box.className = "state-box";
  const themeTxt = th.length
    ? th.map((a) => `${a.name || ("属性" + a.id)}${a.value}%`).join(" · ")
    : "未读取到主题";
  const sug = d.suggest || {};
  const ids = sug.equip_ids || [];
  const det = Object.entries(sug.theme_cover || {})
    .map(([k, v]) => ("属性" + k + ":" + v)).join("  ");
  const cats = d.cats || [];
  const gap = cats.filter((c) => c.cover != null && sug.cover != null)
    .map((c) => `#${c.elf_id}(Lv${c.level || "-"}) ${c.cover}`).join("　");
  box.innerHTML =
    matchRow("今日主题", themeTxt) +
    matchRow("最优穿装", ids.length ? ids.join(" | ") : "-") +
    matchRow("加权覆盖", sug.cover != null ? sug.cover : "-") +
    matchRow("组合属性覆盖", det || "-") +
    matchRow("各猫当前覆盖", gap || "-") +
    matchRow("今日剩余次数", d.performe_num != null ? d.performe_num : "-");
  if (cnt) cnt.textContent = th.length ? `${th.length} 项主题` : "空";
}

async function loadMatchTheme() {
  const r = await fetch("/api/match/theme")
    .then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchTheme = r;
  renderMatchTheme();
  if (!r || r.ok === false) mlogFail("今日主题", r);
}
'''
LINES[r1 + 1:r1 + 1] = NEW_FUNCS.split("\n")
print("  追加 runAllMatch / claimOnly / renderMatchTheme / loadMatchTheme")

# ---------------- 4) 全局状态变量 ----------------
out = "\n".join(LINES)
old_decl = 'let matchState = null, matchPvp = null, matchUnion = null, matchGold = null;\nlet matchPvpTimer = null, matchUnionTimer = null;\n'
if old_decl in out:
    out = out.replace(old_decl,
                      'let matchState = null, matchTheme = null, matchMainTimer = null;\n')
    print("  状态变量已收敛")
else:
    print("  !! 状态变量声明没匹配上，手动检查")

io.open(JS, "w", encoding="utf-8").write(out)
print("  written:", JS)
