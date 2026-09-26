# -*- coding: utf-8 -*-
"""恢复比赛页子玩法 tab（猫窝日常=主界面）+ 清理比赛状态无关字段。

用户 2026-09-26 反馈：
1. 上一轮把子玩法 tab 删光了 —— 但正确结构是「猫窝日常才是主界面，比赛只是子玩法」。
2. 「我的比赛状态」里 talent_score / clover_num / dzs / season_rank_gift / unionid
   等字段与比赛玩法无关；且灰底下 key-value 堆在一起分不清。

本脚本从 5e57348 版本找回被 08f8db1 误删的函数与面板，合并回当前版本
（保留 08f8db1 新增的一键比赛 / 主题卡 / mlog 日志栏）。
"""
import re
import subprocess
import sys

OLD_COMMIT = "5e57348"


def git_show(path):
    return subprocess.run(
        ["git", "show", f"{OLD_COMMIT}:{path}"], capture_output=True, text=True, check=True
    ).stdout


def grab_fn(src, name):
    """按函数名从旧文件里截取完整函数（到下一个顶层 function / 顶层注释为止）。"""
    hits = list(re.finditer(r"\n((?:async )?function %s\()" % re.escape(name), src))
    assert hits, f"旧版本里找不到函数 {name}"
    m = hits[-1]                      # 同名取最后一个（旧文件 arrLen 定义了两次）
    start = m.start() + 1
    nxt = re.search(r"\n(?:(?:async )?function \w+\(|// ---|// ====)", src[m.end():])
    end = m.end() + nxt.start() if nxt else len(src)
    return src[start:end].rstrip() + "\n"


# ---------------- app.js ----------------
old_js = git_show("frontend/app.js")
cur_path = "frontend/app.js"
js = open(cur_path, encoding="utf-8").read()
orig = js

# 1) 全局变量：补回子模块状态
js = js.replace(
    "let matchState = null, matchTheme = null, matchMainTimer = null;",
    "let matchState = null, matchTheme = null, matchPvp = null, matchUnion = null,\n"
    "    matchGold = null, matchGvg = null, matchSanguo = null, matchModes = null;\n"
    "let matchSubTimer = null;",
)
assert "matchSubTimer" in js

# 2) 子模块调度表（默认子模块 = home 猫窝日常）
sub_tbl = (
    'const MATCH_SUB_KEYS = ["home", "race", "pvp", "union", "gvg", "modes"];\n'
    "const MATCH_SUB_LOAD = {\n"
    "  home: () => { loadHomeState(); },\n"
    "  race: () => { loadMatchTheme(); loadMatchState(true); },\n"
    "  pvp: () => { loadMatchPvp(); },\n"
    "  union: () => { loadMatchUnion(); loadMatchGold(); },\n"
    "  gvg: () => { loadMatchGvg(); loadMatchSanguo(); },\n"
    "  modes: () => { loadMatchModes(); loadMatchLog(); },\n"
    "};\n"
)

# 3) 从旧版本找回的函数（保持旧实现，逐个截取）
names = [
    "matchStopPoll", "switchMatchSub",
    "renderMatchPvp", "renderMatchUnion", "renderMatchGvg",
    "renderMatchSanguo", "renderMatchGold",
    "loadMatchPvp", "loadMatchUnion", "loadMatchGvg", "loadMatchSanguo",
    "loadMatchGold",
    "arrLen", "modeCard", "loadMatchModes", "renderMatchModes",
    "loadModeProps", "renderModeProps",
    "claimGroup", "claimAll", "pvpFight", "unionFight", "sanguoRun",
    "bindMatchModeActs",
    "homeRewardText", "loadHomeState", "homePost", "homeRunAll", "initHomeOnce",
]
restored = [sub_tbl]
m = re.search(r"^let homeInited = false;$", old_js, re.M)
assert m, "homeInited"
restored.append("let homeInited = false;\n")
for n in names:
    restored.append(grab_fn(old_js, n))
block = "\n// ---- 以下函数自 5e57348 恢复（08f8db1 误删；猫窝日常=主界面，比赛是子玩法）----\n\n" \
        + "\n".join(restored)

anchor = "// 切换每日比赛页的子模块：只显示目标子模块，并立刻拉取它的数据（手动操作即时可见）"
assert anchor in js
js = js.replace(anchor, block + "\n" + anchor, 1)

# 4) 重写 initMatchOnce：保留一键比赛等新绑定 + 找回各子模块按钮绑定与子模块轮询
new_init = """function initMatchOnce() {
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
  bind("match-race", (b) => busyBtn(b, "出战中…", "打一场（当前装）")(raceOnce()));
  bind("match-dress", (b) => busyBtn(b, "计算中…", "最优穿搭建议")(equipBest()));
  bind("match-dressrace", (b) => busyBtn(b, "出战中…", "按最优装打一场")(equipThenRace()));
  bind("match-claim-all", (b) => busyBtn(b, "领奖中…", "一键领奖")(claimOnly()));
  // 各子玩法的刷新按钮
  bind("match-pvp", (b) => busyBtn(b, "榜单中…", "刷新 PVP 日榜")(loadMatchPvp()));
  bind("match-union", (b) => busyBtn(b, "读取中…", "刷新工会")(loadMatchUnion()));
  bind("match-gold", (b) => busyBtn(b, "读取中…", "金卡限购")(loadMatchGold()));
  bind("match-gvg", (b) => busyBtn(b, "读取中…", "刷新公会战")(loadMatchGvg()));
  bind("match-sanguo", async (b) => {
    const restore = busyBtn(b, "读取中…", "刷新西游三国");
    const r = await fetch("/api/match/sanguo?want_task=1").then((x) => x.json()).catch(() => null);
    matchSanguo = r || matchSanguo;
    renderMatchSanguo();
    if (!r || r.ok === false) mlogFail("西游三国", r);
    else mlog("[每日比赛] 西游三国任务已刷新", "ok");
    restore();
  });
  bind("match-modes-refresh", async (b) => {
    const restore = busyBtn(b, "读取中…", "刷新其他玩法");
    await loadMatchModes(); await loadMatchLog(); restore();
  });
  bindMatchModeActs();
  // 子模块 tab：全局委托（按钮是页面里的静态元素）
  const subBar = $("match-sub-bar");
  if (subBar) subBar.onclick = (e) => {
    const t = e.target && e.target.closest ? e.target.closest(".tab") : null;
    if (!t) return;
    switchMatchSub(t.getAttribute("data-msub"));
    mlog("[每日比赛] 子模块：" + (t.textContent || "").trim() + " 已加载", "ok");
  };
  // 默认子模块 = 猫窝日常（主界面）；比赛只是子玩法之一
  switchMatchSub(window.__matchSub || "home");
  if (matchMainTimer) { clearInterval(matchMainTimer); matchMainTimer = null; }
  // 60s 兜底轮询只刷新当前停留的子模块（与手动操作解耦）
  if (!matchSubTimer) matchSubTimer = setInterval(() => {
    const pane = $("pane-match");
    if (!pane || pane.classList.contains("hidden")) return;
    const fn = MATCH_SUB_LOAD[window.__matchSub || "home"];
    if (fn) fn();
  }, 60000);
}
"""
i = js.index("function initMatchOnce() {")
j = js.index("// ---------- 一键喂食")
js = js[:i] + new_init + "\n" + js[j:]

# 5) switchPane：离开比赛页要停轮询；进比赛页初始化猫窝绑定
js = js.replace(
    '  // 猫窝（cathome）的每日玩法不挂在「每日比赛」页里了，比赛页只跑比赛本身\n'
    '  if (pane === "match") initMatchOnce();\n',
    '  if (pane === "match") { initMatchOnce(); initHomeOnce(); }\n'
    "  else matchStopPoll();\n",
)

# 6) renderMatchState：删掉与比赛无关的字段（talent/clover/dzs/season/unionid）
old_rows = """    box.innerHTML =
      matchRow("猫咪数量", n) +
      matchRow("天赋分 talent_score", d.talent_score) +
      matchRow("天赋率 talent_rate", d.talent_rate) +
      matchRow("每日期望次数 performe_num", d.performe_num) +
      matchRow("次数重置时间", d.perform_limit_ts ? matchFmtTs(d.perform_limit_ts) : "-") +
      matchRow("三叶草 clover_num", d.clover_num) +
      matchRow("dzs / hbs / ele / jhs",
        [d.dzs, d.hbs, d.ele, d.jhs].filter((x) => x !== undefined && x !== null).join(" / ") || "-") +
      matchRow("赛季历史分（season_rank_gift）",
        (d.season_rank_gift || []).length ? `${d.season_rank_gift.length} 条，最近 ${d.season_rank_gift[0] && d.season_rank_gift[0].score}` : "-") +
      matchRow("工会 unionid", d.unionid);"""
new_rows = """    // 只留比赛本身关心的字段；talent/clover/dzs/season/unionid 与比赛玩法无关，不展示
    box.innerHTML =
      matchRow("猫咪数量", n) +
      matchRow("每日期望次数 performe_num", d.performe_num) +
      matchRow("今日剩余次数", d.performe_num) +
      matchRow("次数重置时间", d.perform_limit_ts ? matchFmtTs(d.perform_limit_ts) : "-");"""
assert old_rows in js
js = js.replace(old_rows, new_rows, 1)

assert js != orig
open(cur_path, "w", encoding="utf-8").write(js)
print("app.js OK")

# ---------------- index.html ----------------
old_html = git_show("frontend/index.html")
html_path = "frontend/index.html"
html = open(html_path, encoding="utf-8").read()
orig_h = html


def grab_block(src, start_pat, end_pat):
    i = src.index(start_pat)
    j = src.index(end_pat, i)
    return src[i:j].rstrip() + "\n"


home_b = grab_block(old_html, '          <div id="msub-home" class="msub">', '          <div id="msub-pvp"')
pvp_b = grab_block(old_html, '          <div id="msub-pvp" class="msub">', '          <div id="msub-union"')
union_b = grab_block(old_html, '          <div id="msub-union" class="msub">', '          <div id="msub-gvg"')
gvg_b = grab_block(old_html, '          <div id="msub-gvg" class="msub">', '          <div id="msub-modes"')
modes_b = """          <div id="msub-modes" class="msub">
            <div class="lib-body dg-cols">
              <div class="dg-col" style="flex:1">
                <div class="lst" style="max-height:none">
                  <div class="lst-head"><span class="t">各玩法一键（每块独立操作）</span>
                    <span class="cnt" id="match-modes-cnt"></span></div>
                  <div id="match-modes-box" class="mode-grid">
                    <div class="state-box">读取中…</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
"""

# 1) CSS：msub 样式 + 全局 .rv 键值行样式（不再灰底堆字）
css_anchor = ".pane.match-pane{flex-direction:row;align-items:stretch;min-height:0;gap:12px;padding:12px 14px}"
assert css_anchor in html
css_add = css_anchor + """
  /* 比赛页子玩法 tab：猫窝日常是主界面（默认），比赛只是子玩法之一 */
  .msub{display:none}
  .msub.on{display:block}
  .msub-bar{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin:0 0 12px}
  .msub-bar .tab{flex:0 0 auto;padding:8px 16px;font-size:13px;border-bottom:none;
    border-right:1px solid var(--line);border-radius:0}
  .msub-bar .tab:last-child{border-right:0}
  .msub-bar .tab.active{background:#f0f7ff}
  .msub-hint{font-size:11.5px;color:var(--muted);margin:0 0 10px}
  /* 键值行：分行 + 分隔线，key 灰、value 加粗，不再堆在一起 */
  .rv{display:flex;align-items:baseline;gap:10px;padding:4px 2px;border-bottom:1px solid var(--line)}
  .rv:last-child{border-bottom:none}
  .rv .k{flex:0 0 150px;color:var(--muted);font-size:12px}
  .rv .v{flex:1 1 auto;font-weight:600;font-size:12.5px;word-break:break-all}
  /* 状态框里一行一行排，不走多列网格（多列会把键值挤成一团） */
  #match-state-box, #match-log-box .state-box{display:block}"""
html = html.replace(css_anchor, css_add, 1)

# 2) 工具栏补回各子玩法刷新按钮
btn_anchor = '<button id="match-refresh" class="btn sm">刷新状态</button>'
assert btn_anchor in html
html = html.replace(btn_anchor, btn_anchor + """
            <span class="sep"></span>
            <button id="match-pvp" class="btn sm">刷新 PVP 日榜</button>
            <button id="match-union" class="btn sm">刷新工会</button>
            <button id="match-gold" class="btn sm">金卡限购</button>
            <button id="match-gvg" class="btn sm">刷新公会战</button>
            <button id="match-sanguo" class="btn sm">刷新西游三国</button>
            <button id="match-modes-refresh" class="btn sm">刷新其他玩法</button>""", 1)

# 3) msub-bar + 猫窝日常面板（默认）+ 打开 msub-race
pm = html.index('id="pane-match"')
anchorA_idx = html.index('<div class="lib-body dg-cols">', pm)
tab_bar = """<div class="msub-bar" id="match-sub-bar">
            <button class="tab active" data-msub="home">猫窝日常</button>
            <button class="tab" data-msub="race">出战（比赛）</button>
            <button class="tab" data-msub="pvp">PVP 日榜</button>
            <button class="tab" data-msub="union">工会 · 金卡</button>
            <button class="tab" data-msub="gvg">公会战 · 西游三国</button>
            <button class="tab" data-msub="modes">各玩法一键</button>
          </div>
          <div class="msub-hint">猫窝日常是本页主界面；切到哪个子玩法才拉哪个的接口，停在哪个就刷新哪个。</div>

          """
html = html[:anchorA_idx] + tab_bar + home_b + '\n          <div id="msub-race" class="msub">\n          ' + html[anchorA_idx:]

# 4) 关闭 msub-race + 其余子玩法面板（插在右栏日志注释前）
anchorB = '        <!-- 右侧常驻：只记每日比赛页内各玩法的操作与结果（与总览页全局日志互相独立） -->'
assert anchorB in html
tail = '          </div>\n\n' + pvp_b + "\n" + union_b + "\n" + gvg_b + "\n" + modes_b + "\n"
html = html.replace(anchorB, tail + anchorB, 1)

assert html != orig_h
open(html_path, "w", encoding="utf-8").write(html)
print("index.html OK")
