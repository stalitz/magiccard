"""真机只读：打印换卡箱 / 保险箱 / 炉的真实 cur、max、卡数与剩余格位。

为什么单独立一个：容量口径是炼卡编排的地基（买卡落在换卡箱 → 换卡箱只有 18 格
就是硬顶），一旦游戏改版必须能一句话复核，而不是靠记忆或注释。

用法：python tools/live_boxes.py
"""
import asyncio
import re
import sys

sys.path.insert(0, ".")

from backend.constants import EP_MAINPAGE          # noqa: E402
from backend.main import _session                  # noqa: E402

LABEL = {"changebox": "换卡箱", "storebox": "保险箱", "stovebox": "炉"}


async def go() -> None:
    r = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
    print(f"mainpage code={r.get('code')} msg={r.get('message') or ''}")
    if r.get("code") != 0:
        return
    raw = r.get("raw", "")
    for tag in ("changebox", "storebox", "stovebox"):
        m = re.search(rf"<{tag}([^>]*)>(.*?)</{tag}>", raw, re.S)
        if not m:
            print(f"  {tag:<10} 缺失")
            continue
        head = dict(re.findall(r'([\w]+)="([^"]*)"', m.group(1)))
        used = len(re.findall(r"<card\b", m.group(2)))
        cur = int(head.get("cur") or 0)
        mx = int(head.get("max") or 0)
        print(f"  {LABEL[tag]:<6} {tag:<10} cur={cur:<4} max={mx:<4} "
              f"卡数={used:<4} 剩余可用={cur - used}")


if __name__ == "__main__":
    asyncio.run(go())
