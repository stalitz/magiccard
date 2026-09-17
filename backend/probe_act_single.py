#!/usr/bin/env python3
"""单参数 act 快速扫描 card_act_send_free，找出 code=0 的有效活动。"""
import asyncio, json
from pathlib import Path
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    hits = []
    for act in range(1, 301):
        try:
            r = await s.post("card_act_send_free", act=act)
            code = r.get("code")
            msg = r.get("message", "")
            raw = r.get("raw", "")[:300]
            if code == 0 or code == -1:  # -1 可能是 JSON 返回被 XML 解析器误报
                hits.append({"act": act, "code": code, "msg": msg, "raw": raw})
                print(f"act={act} code={code} {raw[:120]}")
            elif code != -1110:
                print(f"act={act} code={code} msg={msg[:60]}")
        except Exception as e:
            print(f"act={act} error={e}")
    Path("/tmp/probe_act_single.json").write_text(json.dumps(hits, ensure_ascii=False, indent=2))
    print(f"hits={len(hits)}")


if __name__ == "__main__":
    asyncio.run(main())
