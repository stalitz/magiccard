#!/usr/bin/env python3
"""把「每日比赛」页重做成纯粹的比赛页（2026-09-26）。

背景：上一版把猫窝日常 / 公会战 / 西游三国 / PVP 日榜 / 各玩法一键这些
**别的玩法**面板全搬进了每日比赛页，且工具栏上一排按钮绑的函数不存在
（feedAllCats 等），点了必 ReferenceError，等于「没有一键比赛」。

本次：
  · 删掉 msub-bar 二级 tab 与 msub-home/msub-pvp/msub-union/msub-gvg/msub-modes
    五个面板（属于猫窝 / 竞技场 / 工会 / 公会战 / 西游三国，与每日比赛无关）
  · 比赛页只留比赛本身：一键比赛 + 主题与最优穿装 + 我的比赛状态 +
    猫咪出战 + 领奖 + 出战记录
  · 右侧比赛专属日志栏（.mlog-col）原样保留
整块替换 pane-match，一次落盘，避免多处 Edit 互相覆盖。
"""
import io, os, re

ROOT = "/Users/stalitz/www/魔法卡片"
HTML = os.path.join(ROOT, "frontend/index.html")


def rd(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def wr(p, s):
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(s)
    print("  written:", p)


html = rd(HTML)

i = html.index('      <div id="pane-match"')
j = html.index('<script src="/app.js', i)
old = html[i:j]
print("  待替换长度:", len(old), "个 div:", old.count("<div"), "/", old.count("</div>"))

NEW = '''      <div id="pane-match" class="pane match-pane hidden">
        <div class="lib">
          <div class="lib-toolbar" style="gap:10px;flex-wrap:wrap;align-items:center">
            <span style="font-weight:600;font-size:13.5px">每日比赛：</span>
            <span id="match-res" style="font-size:12px;color:var(--muted)"></span>
            <span class="sep"></span>
            <button id="match-runall" class="btn sm primary">一键比赛（打满今日场次 + 领奖）</button>
            <button id="match-dressrace" class="btn sm primary">按最优装打一场</button>
            <button id="match-race" class="btn sm">打一场（当前装）</button>
            <button id="match-dress" class="btn sm">最优穿搭建议</button>
            <button id="match-claim-all" class="btn sm">一键领奖</button>
            <span class="sep"></span>
            <button id="match-equip" class="btn sm">装备详情</button>
            <button id="match-refresh" class="btn sm">刷新状态</button>
          </div>

          <div class="lib-body dg-cols">
            <div class="dg-col" style="flex:1.15">
              <div class="lst" style="max-height:none">
                <div class="lst-head"><span class="t">今日主题与最优穿装</span>
                  <span class="cnt" id="match-theme-cnt"></span></div>
                <div id="match-theme-box" class="state-box">
                  <span class="spinner"></span>正在读取今日主题…</div>
              </div>
            </div>
            <div class="dg-col" style="flex:1">
              <div class="lst" style="max-height:none">
                <div class="lst-head"><span class="t">我的比赛状态</span>
                  <span class="cnt" id="match-state-cnt"></span></div>
                <div id="match-state-box" class="state-box">
                  <span class="spinner"></span>正在读取比赛状态…</div>
              </div>
            </div>
          </div>

          <div class="lib-body dg-cols">
            <div class="dg-col" style="flex:1">
              <div class="lst" style="max-height:none">
                <div class="lst-head"><span class="t">出战猫咪（覆盖度 = 当前装对今日主题的加权覆盖）</span>
                  <span class="cnt" id="match-cats-cnt"></span></div>
                <div id="match-cats" class="lst" style="max-height:none;padding:8px"></div>
              </div>
            </div>
            <div class="dg-col" style="flex:1">
              <div class="lst" style="max-height:none">
                <div class="lst-head"><span class="t">出战记录（最近 10 场）</span>
                  <span class="cnt" id="match-log-cnt"></span></div>
                <div id="match-log-box"><div class="state-box">读取中…</div></div>
              </div>
            </div>
          </div>
        </div>
        <!-- 右侧常驻：只记每日比赛页内各玩法的操作与结果（与总览页全局日志互相独立） -->
        <div class="mlog-col">
          <div class="lpanel">
            <div class="lp-head">
              <span class="t">每日比赛 · 全局日志</span>
              <span class="cnt" id="mlog-cnt">0 条</span>
              <span class="spacer"></span>
              <button class="btn sm ghost" id="mlog-clear">清空</button>
            </div>
            <div id="match-mlog-box" class="lst" data-mlog>
              <div id="match-mlog-empty" class="empty-tip">记录本页各玩法的操作与结果（刷新页面仍在）。</div>
            </div>
          </div>
        </div>
      </div>

'''

html = html[:i] + NEW + html[j:]
wr(HTML, html)

# 版本 bump
m = re.search(r'(<script src="/app\.js\?v=)([0-9a-z]+)("\?>)', html)
if m:
    old_v = m.group(2)
    new_v = "20260926u"
    html = html[:m.start()] + f'<script src="/app.js?v={new_v}"' + html[m.end() - 1:]
    wr(HTML, html)
    print(f"  ?v= {old_v} -> {new_v}")
