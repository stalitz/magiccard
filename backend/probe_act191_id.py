#!/usr/bin/env python3
"""探测 act=191（猫咪任务）带 id 参数的完整结构，定位猫咪旅行出发机制。"""
import asyncio, json
from pathlib import Path
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    out = {}
    # act=191 带不同 id
    for i in range(0, 31):
        r = await s.post("card_act_send_free", act=191, id=i)
        d = r.get("data")
        code = r.get("code")
        if code == 0:
            out[f"id={i}"] = d
            print(f"id={i} -> {json.dumps(d, ensure_ascii=False)[:300]}")
        elif code != -1110:
            print(f"id={i} -> code={code} {r.get('message','')[:40]}")
    Path("/tmp/probe_act191_id.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"saved {len(out)} ids")


if __name__ == "__main__":
    asyncio.run(main())
