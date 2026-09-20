#!/usr/bin/env python3
"""挖地 card_user_dig 探活（只读）。

只发：act=1（状态，只读）、act=999999（探活，返 -1110 说明端点在）、
act=3/5/6/8（猜测的配置类 act，看返回是否只读配置）。
不发 act=2（真挖，消耗锄头）。
"""
import asyncio, json, sys
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录"); return
    acts = sys.argv[1:] or ["1", "999999", "3", "5", "6", "8", "9", "11", "13"]
    for a in acts:
        r = await s.post("card_user_dig", act=a)
        d = r.get("data")
        raw = json.dumps(d, ensure_ascii=False) if isinstance(d, dict) else str(r.get("raw", ""))[:200]
        print(f"--- act={a}  code={r.get('code')} msg={r.get('message','')[:60]}")
        print("   ", raw[:900])
        print()


if __name__ == "__main__":
    asyncio.run(main())
