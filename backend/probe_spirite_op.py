#!/usr/bin/env python3
"""探测 card_user_spirite 的写操作参数（出发/领取）。"""
import asyncio, json
from pathlib import Path
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    out = {}
    # 先试"领取类" op（安全，当前没在旅行应返回无奖励）
    for op in [2, 3, 4, 5, "get", "recv", "draw"]:
        for extra in [{}, {"act": 1}]:
            kw = {"act": 1, "op": op, **extra}
            try:
                rr = await s.post("card_user_spirite", **kw)
                raw = rr.get("raw", "")[:200]
                key = str(kw)
                out[key] = {"code": rr.get("code"), "msg": rr.get("message", ""), "raw": raw}
                print(f"{key} -> code={rr.get('code')} {rr.get('message','')[:40]}")
            except Exception as e:
                out[str(kw)] = f"error: {e}"
    Path("/tmp/probe_spirite_op.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("saved /tmp/probe_spirite_op.json")


if __name__ == "__main__":
    asyncio.run(main())
