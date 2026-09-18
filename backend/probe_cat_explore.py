#!/usr/bin/env python3
"""探测「猫咪探险」（西游/三国大地图）端点。

手法（项目铁律 · 零副作用探活）：对候选端点发 act=999999——
  - 若返回 -1110 "activity is end"  ⇒ 端点存在，只是没这份配置
  - 若返回 -1 / "bad request" / 空   ⇒ 端点不存在
确认存在后再发 act=1 取真实数据判语义。
"""
import asyncio, json, sys
from backend.mc_api import LoginSession

CANDIDATES = [
    # 猫咪小屋 / 每日比赛 / 探险
    "card_user_cathome", "card_cathome", "card_cat_home", "card_user_cat_home",
    "card_user_catgame", "card_cat_game", "card_user_cat_game",
    "card_cat_adventure", "card_user_cat_adventure", "card_cat_adventure_info",
    "card_user_explore", "card_explore", "card_user_explore_info",
    "card_explore_map", "card_user_explore_map", "card_map", "card_user_map",
    # 旅行
    "card_user_travel", "card_travel", "card_user_travel_info", "card_travel_info",
    "card_pet_travel", "card_pet", "card_user_pet",
    # 比赛
    "card_user_match", "card_match", "card_user_dailymatch", "card_dailymatch",
    "card_user_race", "card_race",
    # 灵宠衍生
    "card_user_spirite_explore", "card_spirite_explore", "card_user_spirite_map",
    "card_user_spirite_adventure", "card_spirite_adventure",
]


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    hits = []
    for ep in CANDIDATES:
        row = {"ep": ep}
        for tag, kw in (("probe", {"act": 999999}), ("act1", {"act": 1})):
            try:
                r = await s.post(ep, **kw)
            except Exception as e:
                row[tag] = f"EXC {type(e).__name__}"
                continue
            code, msg, raw = r.get("code"), r.get("message", ""), (r.get("raw") or "")[:160]
            row[tag] = f"code={code} msg={msg[:30]} raw={raw[:90]}"
        alive = "-1110" in str(row.get("probe", "")) or (
            "code=0" in str(row.get("act1", "")) and "bad request" not in str(row.get("act1", "")))
        row["alive"] = alive
        flag = "[HIT] " if alive else "      "
        if alive:
            hits.append(row)
        print(f"{flag}{ep:<34} probe: {row.get('probe','')[:78]}")
        if alive:
            print(f"{' '*5}{'':<34} act=1: {row.get('act1','')[:120]}")
    print(f"\n命中 {len(hits)} 个：", [h["ep"] for h in hits])


if __name__ == "__main__":
    asyncio.run(main())
