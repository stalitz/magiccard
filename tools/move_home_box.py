"""把「每日玩法（猫窝）」整块从 pane-overview（练卡总览）搬到 pane-match（每日比赛）。

背景：用户明确指出总览是「练卡总览」，不是每日比赛页；
他要的玩法分块应在「每日比赛」页（pane-match）。本脚本原地搬运 + 还原总览页。
"""
import io, re, sys

P = "frontend/index.html"
s = io.open(P, encoding="utf-8").read()

# ---- 1. 定位总览页里待搬的那块（紧邻「游戏数据」stat-card 之前） ----
ov_head = '      <div id="pane-overview" class="pane">\n'
card_open = '          <div class="stat-card">\n            <h3>每日玩法（猫窝）</h3>\n'
game_data = '          </div>\n          <div class="stat-card">\n            <h3>游戏数据</h3>\n'

i = s.find(ov_head)
assert i >= 0, "找不到 pane-overview"
j = s.find(card_open, i)
assert j >= 0, "找不到「每日玩法（猫窝）」stat-card"
k = s.find(game_data, j)
assert k > j, "找不到紧随其后的「游戏数据」stat-card"

block = s[j + len(card_open):k]            # mode-grid 内部（含 mode-grid 的 </div>）
assert 'id="home-box"' in block, "搬移内容不含 #home-box"

# ---- 2. 从总览页移除，还原成仓库原样 ----
tpl = s[:j] + s[k:]

# ---- 3. 插进「每日比赛」页：lib-toolbar 之后、lib-body 之前 ----
m_head = '      <div id="pane-match" class="pane hidden">\n'
m_anchor = '\n          <div class="lib-body dg-cols">'
i = tpl.find(m_head)
assert i >= 0, "找不到 pane-match"
j = tpl.find(m_anchor, i)
assert j > i, "找不到 match 页 lib-body 锚点"

insert = (
    '          <div class="stat-card">\n'
    '            <h3>每日玩法（猫窝）</h3>\n'
    + block +
    '          </div>\n'
)
out = tpl[:j] + "\n" + insert + tpl[j:]

# ---- 4. 断言：总览页已还原、match 页含 home-box ----
seg_ov = out[out.find(ov_head):]
seg_ov = seg_ov[:seg_ov.find('      <!-- 子页：卡库查询 -->')]
assert 'home-box' not in seg_ov, "总览页仍残留 home-box"
assert out.count('id="home-box"') == 1, "home-box 数量异常"
assert out.count('每日玩法（猫窝）') == 1, "标题数量异常"

io.open(P, "w", encoding="utf-8").write(out)
print("搬移完成：pane-match 内含 #home-box，pane-overview 已还原")
