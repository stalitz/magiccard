#!/usr/bin/env python3
"""批量枚举猫咪旅行可能的端点名。"""
import asyncio, json
from pathlib import Path
from backend.mc_api import LoginSession

CANDIDATES = [
    "card_cat_travel", "card_user_cat", "card_cat", "card_cat_explore",
    "card_cat_trip", "card_pet_travel", "card_user_pet", "card_pet",
    "card_cat_info", "card_cat_get", "card_cat_go", "card_cat_start",
    "card_cat_reward", "card_user_cat_travel", "card_user_cat_info",
    "card_travel", "card_user_travel", "card_trip", "card_cat_send",
    "card_cat_send_free", "card_user_cat_explore", "card_cat_explore_get",
    "card_user_spirite_explore", "card_spirite_explore", "card_spirite_start",
    "card_spirite_get", "card_spirite_go", "card_spirite_reward",
    "card_user_explore", "card_explore",
]


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    hits = []
    for ep in CANDIDATES:
        for kw in [{}, {"act": 1}, {"act": 191}]:
            try:
                r = await s.post(ep, **kw)
                code = r.get("code")
                msg = r.get("message", "")
                raw = r.get("raw", "")[:100]
                # 有效响应：code 不是 -1(解析失败) 且不是纯 "bad request"
                if raw and raw != "bad request" and code != -1:
                    hits.append({"ep": ep, "kw": kw, "code": code, "msg": msg, "raw": raw})
                    print(f"[HIT] {ep} {kw} -> code={code} {raw[:80]}")
                elif raw != "bad request":
                    print(f"       {ep} {kw} -> code={code} msg={msg[:40]} raw={raw[:60]}")
            except Exception as e:
                pass
    Path("/tmp/probe_cat_endpoints.json").write_text(json.dumps(hits, ensure_ascii=False, indent=2))
    print(f"hits={len(hits)}")


if __name__ == "__main__":
    asyncio.run(main())
