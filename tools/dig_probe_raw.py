"""打 act=2 的原始响应，区分「限流 busy」还是「目标非法」。"""
import asyncio
import json
import sys

from backend.mc_api import LoginSession
import backend.dig_auto as dg


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录"); return
    st = await dg.get_state(s)
    # 试三类动作
    tests = [
        ("锄头@L16428,j2", 1, 16428, 2),
        ("九宫格@L16432,j3", 3, 16432, 3),
        ("一字型@L16432,j3", 2, 16432, 3),
        ("九宫格@L16427,j3(顶行)", 3, 16427, 3),
    ]
    for label, dt, line, col in tests:
        r = await s.post("card_user_dig", act=2, line=line, col=col, dig_type=dt)
        raw = r.get("raw", "")
        try:
            j = json.loads(raw)
        except Exception:
            j = raw
        code = dg._code(r)
        print(f"== {label} → code={code}")
        print("   data:", json.dumps(r.get("data"), ensure_ascii=False)[:400])
        print("   message:", r.get("message"))
        print("   raw:", raw[:300])
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
