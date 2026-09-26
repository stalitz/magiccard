"""把「每日玩法」块从 6 个手动按钮改成：状态概览 + 一个「一键全部」主入口。

用户明确：要的是一键全部；分开的按钮属于手动操作，不是他要的。
手动按钮（home-like / home-gacha / home-candy / home-feed / home-arena）全部移除，
只留 #home-all；状态 span 保留（用户最早就要求「不用列榜单，只显示状态」）。
"""
import io

P = "frontend/index.html"
s = io.open(P, encoding="utf-8").read()

head = '            <h3>每日玩法（猫窝）</h3>\n            <div class="mode-grid" id="home-box">\n'
i = s.find(head)
assert i >= 0, "找不到每日玩法块开头"
j = s.find("            </div>\n          </div>\n          <div class=\"stat-card\">\n            <h3>每日比赛状态</h3>", i)
if j < 0:  # 兼容：找 mode-grid 收尾
    j = s.find("            </div>\n          </div>\n", i)
    j = s.find("          </div>\n", j + 4)
assert j > i, "找不到每日玩法块结尾"

new = '''            <h3>每日玩法（猫窝）· 一键全部</h3>
            <div class="mode-grid" id="home-box">
              <div class="lst mode-card">
                <div class="lst-head"><span class="t">点赞</span></div>
                <div class="state-box"><span id="home-like-st" style="grid-column:1/-1">读取中…</span></div>
              </div>
              <div class="lst mode-card">
                <div class="lst-head"><span class="t">扭蛋机</span></div>
                <div class="state-box"><span id="home-gacha-st" style="grid-column:1/-1">读取中…</span></div>
              </div>
              <div class="lst mode-card">
                <div class="lst-head"><span class="t">猫咪玩耍</span></div>
                <div class="state-box"><span id="home-cat-st" style="grid-column:1/-1">读取中…</span></div>
              </div>
              <div class="lst mode-card">
                <div class="lst-head"><span class="t">竞技比赛</span></div>
                <div class="state-box"><span id="home-arena-st" style="grid-column:1/-1">读取中…</span></div>
              </div>
              <div class="lst mode-card">
                <div class="lst-head"><span class="t">金币福利</span></div>
                <div class="state-box"><span id="home-gold-st" style="grid-column:1/-1">读取中…</span></div>
              </div>
              <div class="lst mode-card">
                <div class="lst-head"><span class="t">衣柜锻造</span></div>
                <div class="state-box"><span id="home-forge-st" style="grid-column:1/-1">读取中…</span></div>
              </div>
            </div>
            <div class="lib-toolbar" style="padding:8px 12px">
              <button id="home-all" class="btn sm ok">一键全部（自动跑完）</button>
              <span id="home-all-st" class="hint"></span>
            </div>
            <div class="hint" style="padding:0 12px 10px">顺序由程序保证：<b>点赞 → 扭蛋 → 喂猫到 80 → 竞技自动</b>
              （玩耍严格排在竞技之前）；糖果不够会先用积分换大糖果再喂。
              今天的赞若已点过、免费扭蛋已抽过，会自动跳过。<br>
              <b>金币兑红宝石 / 衣柜锻造 / 领段位奖励</b>的写接口还没抓到包，一键时跳过并在日志里说明。</div>
          </div>
          <div class="stat-card">
            <h3>每日比赛状态</h3>'''

out = s[:i] + new + s[j + len('            </div>\n          </div>\n          <div class="stat-card">\n            <h3>每日比赛状态</h3>') :]
io.open(P, "w", encoding="utf-8").write(out)

# 校验
assert 'id="home-all"' in out, "缺少一键全部按钮"
for old in ('id="home-like"', 'id="home-gacha"', 'id="home-candy"', 'id="home-feed"', 'id="home-arena"'):
    assert old not in out, f"手动按钮 {old} 未删干净"
assert out.count('id="home-box"') == 1
print("已改为一键全部；手动按钮已全部移除")
