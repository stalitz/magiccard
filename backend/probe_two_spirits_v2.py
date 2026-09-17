"""对照探针：normal 槽派第 1 只(颐和园) + hy 槽派第 2 只(颐和园)，
看 hy 第二路是否真的能派出去（hy_put_spirites / hy_explore_ts 是否被写入）。
-2 表示校验失败，不消耗次数，可安全反复试。
"""
import asyncio
import json

from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录"); return
    ou = str(int(str(s.uin)))
    eid = 608  # 北京颐和园 pass=6,section=8

    d = (await s.post("card_user_spirite", act=1, opuin=ou)).get("data") or {}
    print("当前 free =", d.get("free_explore_cnt"), "normal_ts =",
          (d.get("explore") or {}).get("explore_ts"), flush=True)

    # 两路都派到 北京颐和园：normal 用 ids=1，hy 用 hy_put_spirites=[2]
    print("\n=== 两路同地：ids=1(normal) + hy_put_spirites=[2](hy) ===", flush=True)
    r = await s.post("card_user_spirite", act=3, opuin=ou,
                     section_id=8, pass_id=6, ids="1", explore_pass_id=eid,
                     hy_section_id=8, hy_section_id2=6,
                     hy_put_spirites="[2]", hy_explore_pass_id=eid)
    print("act=3 code =", r.get("code"), "| msg =", r.get("message"), flush=True)
    d = (await s.post("card_user_spirite", act=1, opuin=ou)).get("data") or {}
    ex = d.get("explore") or {}
    print("free =", d.get("free_explore_cnt"), "| hy_cnt =", d.get("today_hy_explore_cnt"), flush=True)
    print("NORMAL put_spirites =", json.dumps(ex.get("put_spirites"), ensure_ascii=False), flush=True)
    print("HY     hy_put_spirites =", json.dumps(ex.get("hy_put_spirites"), ensure_ascii=False), flush=True)
    print("HY     hy_explore_ts =", ex.get("hy_explore_ts"), flush=True)
    print("HY     hy_explore_pass_id =", ex.get("hy_explore_pass_id"), flush=True)


asyncio.run(main())
