#!/usr/bin/env python3
"""收获当前探险（act=4），并对比收获前后的资源字段。

用途：确认 act=3 出发消耗的是哪一种次数（free_explore_cnt / today_fri_explore_cnt /
pay_explore_cnt / permanent…），因为实测 free_explore_cnt=0 时仍可能出发成功。
"""
import asyncio
import json
import time

from backend.mc_api import LoginSession
from backend.spirite_auto import SPIRITE_MIN_WAIT, plain_uin

KEYS = ("free_explore_cnt", "today_fri_explore_cnt", "today_hy_explore_cnt",
        "pay_explore_cnt", "permanent", "treasure_map", "yinding", "gold_key",
        "power", "section_id", "pass_id", "baibiandan", "vt52")


async def snap(s, op) -> dict:
    r = await s.post("card_user_spirite", act=1, opuin=op)
    d = r.get("data") or {}
    ex = d.get("explore") or {}
    out = {k: d.get(k) for k in KEYS}
    out["explore_ts"] = ex.get("explore_ts")
    out["explore_pass_id"] = ex.get("explore_pass_id")
    out["put_spirites"] = ex.get("put_spirites")
    return out


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    op = plain_uin(s)
    a = await snap(s, op)
    print("当前状态:", json.dumps(a, ensure_ascii=False))

    ts = int(a.get("explore_ts") or 0)
    if ts:
        wait = SPIRITE_MIN_WAIT + 20 - (time.time() - ts)
        if wait > 0:
            print("等待 %.0f 秒后可收 …" % wait, flush=True)
            await asyncio.sleep(wait)

    r = await s.post("card_user_spirite", act=4, opuin=op)
    print("act=4 -> code=%s %s" % (r.get("code"), str(r.get("message"))[:40]))
    print("结算:", json.dumps(r.get("data"), ensure_ascii=False)[:700])

    b = await snap(s, op)
    print("\n收获后:", json.dumps(b, ensure_ascii=False))
    print("\n变化:")
    for k in a:
        if a[k] != b[k]:
            print("  %-18s %s -> %s" % (k, a[k], b[k]))


if __name__ == "__main__":
    asyncio.run(main())
