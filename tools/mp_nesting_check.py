# -*- coding: utf-8 -*-
"""静态检查：index.html 与 app.js 生成的子块模板 div 配平 + mode-card 结构正确。

2026-09-27 教训：光配平不够 —— msub-race 结尾多一个 </div> 会把 .lib 提前闭合，
配平总数仍然 0 错误，但后面所有 msub 塌出布局（工会页 tab 与内容全乱）。
所以下面追加「祖先链断言」：每个 #msub-* 必须是 pane-match>.lib 的子级。
"""
import pathlib
from html.parser import HTMLParser

html = pathlib.Path("frontend/index.html").read_text(encoding="utf-8")
appjs = pathlib.Path("frontend/app.js").read_text(encoding="utf-8")


class Chk(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errs = []
        self.msub_parent = {}   # id -> 打开时的直接父级描述

    def _describe(self):
        if not self.stack:
            return "(根)"
        t, pos = self.stack[-1]
        return f"<{t}> @{pos}"

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "img", "input", "meta", "hr"):
            return
        d = dict(attrs)
        did = d.get("id", "")
        if did.startswith("msub-"):
            self.msub_parent[did] = self._describe()
        self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if not self.stack:
            self.errs.append(f"多余的 </{tag}> @ {self.getpos()}")
            return
        t, pos = self.stack.pop()
        if t != tag:
            self.errs.append(f"不匹配：<{t}> @{pos} 被 </{tag}> @{self.getpos()} 关闭")


c = Chk()
c.feed(html)
print("index.html 未闭合：", [(t, p) for t, p in c.stack] or "无")
print("index.html 错误：", c.errs[:5] or "无")

# 祖先链断言：所有 msub-* 的直接父级必须是同一个元素（.lib 无 id，用一致性判断）
parents = set(c.msub_parent.values())
if len(parents) == 1:
    print(f"msub 祖先链一致：全部挂在 {next(iter(parents))} 下 ✓")
else:
    print("✗ msub 祖先链不一致（DOM 塌了，布局会乱）：")
    for k, v in c.msub_parent.items():
        print(f"   {k} -> {v}")
    c.errs.append("msub 祖先链不一致")

# app.js 里 modeCard 的模板：div 数量必须自洽
tmpl = appjs[appjs.index("function modeCard"):appjs.index("async function loadMatchModes")]
opens = tmpl.count("<div") + tmpl.count("<span") - tmpl.count("</div>") - tmpl.count("</span>")
print("modeCard 模板 opens-closes =", opens, "（其余为 title/cnt 插值，0 表示配平）")

for need in ['class="mp-tip"', 'class="mp-body"', 'id="mp-${mode}"', 'data-act="mm-props"', 'data-act="mm-reload"']:
    print(f"  含 {need}:", need in tmpl)
print("index.html 版本：", [l for l in html.splitlines() if "app.js?v=" in l])
