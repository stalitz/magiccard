#!/usr/bin/env python3
"""拉取 card_travel_elf 的渲染 HTML（只读，不加动作参数），
在 HTML 里搜表单字段名 / 拼参 JS，定位真实 POST 变量。"""
import asyncio, re
from pathlib import Path
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    # 加 ver/code 看齐其它端点，但不带任何动作参数 → 应返回默认渲染 HTML（只读）
    r = await s.post("card_travel_elf", ver=1, code="")
    raw = r.get("raw") or ""
    print("len", len(raw), "code", r.get("code"))
    Path("/tmp/travel_elf.html").write_text(raw, encoding="utf-8", errors="ignore")
    # 搜可能的参数名
    kws = ["travel_elf", "elf_id", "depart", "recall", "back", "explore", "reward",
           "claim", "go", "petid", "toolid", "food_id", "act", "cmd", "op", "type",
           "place", "scene", "card_travel_elf", "input", "name=", "addVariable",
           "LoadVars", "sendAndLoad", "post", "goBack", "take", "dui", "water"]
    low = raw.lower()
    found = {}
    for kw in kws:
        c = low.count(kw.lower())
        if c:
            found[kw] = c
    print("keyword hits:", found)
    # 打印含 travel_elf / elf_id / 动作词的上下文片段
    for m in re.finditer(r".{40}(travel_elf|elf_id|depart|recall|explore|reward|go_back|take_elf|travel_dui|travel_end)[A-Za-z_]{0,20}.{40}", raw, re.I):
        print("  >>", m.group(0)[:120].replace("\n", " "))


if __name__ == "__main__":
    asyncio.run(main())
