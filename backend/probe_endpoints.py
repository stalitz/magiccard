#!/usr/bin/env python3
"""枚举可能的活动相关端点，找周年庆。"""
import asyncio, json
from pathlib import Path
from backend.mc_api import LoginSession

ENDPOINTS = [
    ("card_task_activity", {"act": 1}),
    ("card_task_activity", {"act": 92}),
    ("card_task_activity", {"act": 92, "id": 2}),
    ("card_act_get_gifts", {"act": 92}),
    ("card_act_get_free", {"act": 92}),
    ("card_act_pay", {"act": 92}),
    ("card_act_dig", {"act": 92}),
    ("card_user_share_info", {}),
    ("card_user_share_get", {"act": 92}),
    ("card_mission_complete", {"type": 1}),
    ("card_user_money_draw", {}),
    ("card_user_levelup_bonus", {}),
    ("card_pai_open", {}),
]

async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    out = []
    for ep, params in ENDPOINTS:
        try:
            r = await s.post(ep, **params)
            print(f"{ep} {params} -> code={r.get('code')} msg={r.get('message','')[:60]}")
            out.append({"ep": ep, "params": params, "code": r.get("code"), "msg": r.get("message", ""), "raw": r.get("raw", "")[:500]})
        except Exception as e:
            print(f"{ep} {params} -> error={e}")
            out.append({"ep": ep, "params": params, "error": str(e)})
    Path("/tmp/probe_endpoints.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
