#!/usr/bin/env python3
"""拉取猫咪旅行(act=191)完整数据，定位入口与任务结构。"""
import asyncio, json
from pathlib import Path
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    out = {}
    # 1) act=191 完整
    r = await s.post("card_act_send_free", act=191)
    out["act191"] = r.get("data", r.get("raw", ""))
    # 2) 宠物探险端点（常量 EP_SPIRITE = card_user_spirite）
    for ep in ["card_user_spirite", "card_user_cat_task", "card_act_cat_task", "card_cat_task",
               "card_user_spirite_get", "card_user_spirite_info"]:
        try:
            rr = await s.post(ep)
            out[ep] = rr.get("data", rr.get("raw", ""))[:800]
            print(f"{ep} -> code={rr.get('code')} {rr.get('message','')[:40]}")
        except Exception as e:
            out[ep] = f"error: {e}"
    Path("/tmp/probe_cat.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("saved /tmp/probe_cat.json")


if __name__ == "__main__":
    asyncio.run(main())
