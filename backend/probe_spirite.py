#!/usr/bin/env python3
"""枚举 card_user_spirite 的参数，找猫咪旅行入口。"""
import asyncio, json
from pathlib import Path
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    out = {}
    # spirite 可能参数
    trials = [
        {}, {"act": 191}, {"act": 1}, {"type": 1}, {"type": 1, "act": 191},
        {"op": 1}, {"cmd": "info"}, {"cmd": "get"}, {"id": 1},
        {"act": 191, "id": 1}, {"act": 191, "type": 88}, {"index": 1},
    ]
    for kw in trials:
        try:
            rr = await s.post("card_user_spirite", **kw)
            raw = rr.get("raw", "")[:300]
            out[str(kw)] = {"code": rr.get("code"), "msg": rr.get("message", ""), "raw": raw}
            print(f"{kw} -> code={rr.get('code')} {rr.get('message','')[:40]} {raw[:60]}")
        except Exception as e:
            out[str(kw)] = f"error: {e}"
    Path("/tmp/probe_spirite.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("saved")


if __name__ == "__main__":
    asyncio.run(main())
