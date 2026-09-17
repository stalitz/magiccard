#!/usr/bin/env python3
"""扫描 (section_id, pass_id) 的**有效组合矩阵** —— 推翻「section 只有 1~6」的旧结论。

依据（probe_spirite_keys 实测）：
  (sec=9, pass=1) -> -5 拒绝 ；(sec=9, pass=6) -> -2 通过
  → 有效性取决于**组合**（哪张图的哪一关已解锁），不是 section 的固定范围。
  → act=1 返回的 section_id/pass_id 就是「玩家当前所在的那个组合」。

判据（零消耗，ids=999 不存在的精灵）：-2 = 组合有效，-5 = 组合无效。
必须串行；跑前须确认 explore_ts=0。

用法：
  python -m backend.probe_spirite_grid            # 两阶段全跑
  python -m backend.probe_spirite_grid --sec      # 只跑阶段1（扫 section）
"""
import asyncio
import sys

from backend.mc_api import LoginSession
from backend.spirite_auto import plain_uin

MAX_SEC = 20
MAX_PASS = 8


async def probe(s, op, sec, p) -> int | None:
    r = await s.post("card_user_spirite", act=3, opuin=op, ids=999,
                     section_id=sec, pass_id=p, explore_pass_id=sec * 100 + p)
    return r.get("code")


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    op = plain_uin(s)

    r = await s.post("card_user_spirite", act=1, opuin=op)
    d = r.get("data") or {}
    cur_sec = int(d.get("section_id") or 0)
    cur_pass = int(d.get("pass_id") or 0)
    ts = (d.get("explore") or {}).get("explore_ts")
    print("当前进度 sec=%s pass=%s  explore_ts=%s" % (cur_sec, cur_pass, ts))
    if ts:
        print("已有探险在跑 → 全部会返 -2，无信息，终止。")
        return

    print("\n=== 阶段1：固定 pass=%s，扫 section 1..%s ===" % (cur_pass, MAX_SEC))
    ok_secs = []
    for sec in range(1, MAX_SEC + 1):
        c = await probe(s, op, sec, cur_pass)
        tag = "✔" if c == -2 else ("✘" if c == -5 else "?")
        if c == -2:
            ok_secs.append(sec)
        print("  sec %-3s -> %-6s %s" % (sec, c, tag))
    print("  通过的 section：", ok_secs)

    if "--sec" in sys.argv:
        return

    print("\n=== 阶段2：矩阵扫描 (section × pass)，. = -2 通过，空 = -5 拒绝 ===")
    scan = ok_secs
    print("        pass= " + "".join("%-3d" % p for p in range(1, MAX_PASS + 1)))
    for sec in scan:
        row = []
        for p in range(1, MAX_PASS + 1):
            c = await probe(s, op, sec, p)
            row.append(" .  " if c == -2 else (" -5 " if c == -5 else " %-3s" % c))
        print("  sec %-3s |%s" % (sec, "".join(row)))

    print("\n=== 边界用例 ===")
    for label, sec, p in (("sec=99999 pass=1", 99999, 1),
                          ("sec=0 pass=1", 0, 1),
                          ("sec=1 pass=0", 1, 0),
                          ("sec=1 pass=-1", 1, -1),
                          ("sec=100 pass=6", 100, 6)):
        c = await probe(s, op, sec, p)
        print("  %-22s -> %s" % (label, c))


if __name__ == "__main__":
    asyncio.run(main())
