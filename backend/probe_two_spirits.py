"""后台探针：当前 normal 槽被 pet1 占着（约 5 分钟计时），等它自动结算后，
一次性同时测两种「两只灵宠」候选写法，看哪条能把两只写进 explore.put_spirites / hy_put_spirites。

用法：python -m backend.probe_two_spirits
"""
import asyncio
import json
import time

from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录"); return
    ou = str(int(str(s.uin)))
    eid = 608  # 北京颐和园 pass=6,section=8

    # 等当前 pet1 自动结算（>5 分钟）
    print("等待当前 pet1 探险自动结算（约 5 分钟）…", flush=True)
    for _ in range(80):  # 最多 ~13 分钟
        d = (await s.post("card_user_spirite", act=1, opuin=ou)).get("data") or {}
        ex = d.get("explore") or {}
        if not (ex.get("explore_ts") or 0):
            print("normal 槽已清空，free=", d.get("free_explore_cnt"), flush=True)
            break
        await asyncio.sleep(10)

    # 一次性组合：normal 用 ids JSON 数组 + hy 用 hy_put_spirites JSON 数组
    print("\n=== 组合测试：ids='[1,2]' + hy_put_spirites='[1,2]' ===", flush=True)
    r = await s.post("card_user_spirite", act=3, opuin=ou,
                     section_id=8, pass_id=6, ids="[1,2]", explore_pass_id=eid,
                     hy_section_id=8, hy_section_id2=6,
                     hy_put_spirites="[1,2]", hy_explore_pass_id=eid)
    print("act=3 code =", r.get("code"), "| msg =", r.get("message"), flush=True)
    d = (await s.post("card_user_spirite", act=1, opuin=ou)).get("data") or {}
    ex = d.get("explore") or {}
    print("free =", d.get("free_explore_cnt"), "| hy_cnt =", d.get("today_hy_explore_cnt"), flush=True)
    print("NORMAL put_spirites =", json.dumps(ex.get("put_spirites"), ensure_ascii=False), flush=True)
    print("HY     hy_put_spirites =", json.dumps(ex.get("hy_put_spirites"), ensure_ascii=False), flush=True)
    print("HY     hy_explore_ts =", ex.get("hy_explore_ts"), flush=True)
    print("NORMAL explore_pass_id =", ex.get("explore_pass_id"), flush=True)


asyncio.run(main())
