#!/usr/bin/env python3
"""猫咪旅行 card_travel_elf 零副作用探活。

只发只读/探活请求，绝不发真实动作：
- card_travel_elf_main : 配置/状态加载（x-javascript，读）
- card_travel_elf act=1 : 通用查询动词（灵宠同款，零副作用）
- card_travel_elf act=999999 : 探活惯用值，读错误码
打印原始响应前若干字符，定位参数风格与数据形状。
"""
import asyncio
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return

    async def probe(ep, **kw):
        try:
            r = await s.post(ep, **kw)
        except Exception as e:
            print(f"  EXC {type(e).__name__}: {e}")
            return
        code = r.get("code")
        msg = r.get("message", "")
        raw = r.get("raw", "") or r.get("data", "")
        if isinstance(raw, (dict, list)):
            raw = str(raw)
        print(f"  -> code={code} msg={msg[:50]!r}")
        print(f"     raw[:1500]={raw[:1500]!r}")
        print()

    print("=== card_travel_elf_main (no params) ===")
    await probe("card_travel_elf_main")

    print("=== card_travel_elf act=1 ===")
    await probe("card_travel_elf", act=1)

    print("=== card_travel_elf act=999999 (probe) ===")
    await probe("card_travel_elf", act=999999)


if __name__ == "__main__":
    asyncio.run(main())
