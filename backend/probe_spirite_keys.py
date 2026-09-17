#!/usr/bin/env python3
"""判别 act=3 的主键：section_id 是「大地图」还是「小图位置」？

用户假设（2026-09-14 15:38）：
  section_id 1~6 可能是**当前大地图内的小图位置**（第 7 个还没开），
  而「大地图」本身是另一个参数，尚未找到。

判据（零消耗，ids=999 不存在的精灵）：
  服务端先校验地图、后校验精灵（实测：7+ 地图返 -5，1~6 返 -2 且与 pass 无关）
  → **-2 = 地图校验通过**，**-5 = 地图校验不通过**

决定性对照：
  E8: section_id=9 + explore_pass_id=101
      若返 -2（而非 -5）→ section_id 被忽略或非主键，explore_pass_id 才是关键，
      那 9 号大地图就藏在 explore_pass_id 的高位里。

必须串行请求；跑之前须确认当前没有探险在跑（否则全 -2）。
"""
import asyncio

from backend.mc_api import LoginSession
from backend.spirite_auto import plain_uin

# (说明, 额外参数) —— 统一带 ids=999
CASES = [
    ("基准 sec=1 pass=1 eid=101", dict(section_id=1, pass_id=1, explore_pass_id=101)),
    ("基准 sec=6 pass=1 eid=601", dict(section_id=6, pass_id=1, explore_pass_id=601)),
    ("E8  sec=9 eid=101", dict(section_id=9, pass_id=1, explore_pass_id=101)),
    ("    sec=1 eid=901", dict(section_id=1, pass_id=1, explore_pass_id=901)),
    ("    sec=1 eid=201", dict(section_id=1, pass_id=1, explore_pass_id=201)),
    ("    sec=9 pass=6 eid=906", dict(section_id=9, pass_id=6, explore_pass_id=906)),
    ("    sec=1 pass=6 eid=106", dict(section_id=1, pass_id=6, explore_pass_id=106)),
    ("    sec=1 pass=20 eid=120", dict(section_id=1, pass_id=20, explore_pass_id=120)),
    ("    sec=9 pass=1 无 eid", dict(section_id=9, pass_id=1)),
    ("    无 sec pass=1 eid=101", dict(pass_id=1, explore_pass_id=101)),
    ("    sec=1 pass=1 eid=101 hy=9", dict(section_id=1, pass_id=1,
                                         explore_pass_id=101, hy_section_id=9)),
    ("    sec=1 pass=999 eid=1", dict(section_id=1, pass_id=999, explore_pass_id=1)),
]


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    op = plain_uin(s)

    r = await s.post("card_user_spirite", act=1, opuin=op)
    d = r.get("data") or {}
    ts = (d.get("explore") or {}).get("explore_ts")
    print("前置检查：explore_ts=%s（必须为 0/空，否则全部返 -2 无信息）" % ts)
    if ts:
        print("已有探险在跑，终止。")
        return

    print("\n说明                          -> code")
    for label, kw in CASES:
        rr = await s.post("card_user_spirite", act=3, opuin=op, ids=999, **kw)
        code = rr.get("code")
        mark = "✔ 地图通过" if code == -2 else ("✘ 地图拒绝" if code == -5 else "?")
        print("%-30s -> %-6s %s" % (label, code, mark))

    r2 = await s.post("card_user_spirite", act=1, opuin=op)
    d2 = r2.get("data") or {}
    print("\n复查：free=%s permanent=%s explore_ts=%s"
          % (d2.get("free_explore_cnt"), d2.get("permanent"),
             (d2.get("explore") or {}).get("explore_ts")))


if __name__ == "__main__":
    asyncio.run(main())
