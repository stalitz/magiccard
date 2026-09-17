"""探测「套卡是否下架/能否抽到/商店在售」的数据源。"""
import asyncio
import json
from backend.mc_api import LoginSession


async def main():
    s = LoginSession()
    s.restore()
    uin = s.uin

    combos = [
        ("card_user_theme_list", {"opuin": uin}),
        ("card_user_theme_list", {"opuin": uin, "type": 0}),
        ("card_user_theme_list", {"opuin": uin, "type": 1}),
        ("card_user_theme_list", {"opuin": uin, "page": 0, "num": 20}),
        ("card_user_theme_list", {"type": 0}),
        ("card_shop_list", {}),
        ("card_user_shop", {}),
        ("card_market_list", {}),
        ("card_user_market", {}),
        ("card_theme_list", {}),
        ("card_user_theme", {}),
    ]
    out = []
    for ep, params in combos:
        try:
            r = await s.post(ep, **params)
            raw = (r.get("raw") or "").strip()
            out.append({"ep": ep, "params": params, "code": r.get("code"),
                        "len": len(raw), "head": raw[:400]})
            print(f"{ep} {params} -> code={r.get('code')} len={len(raw)}")
            if r.get("code") == 0 and len(raw) > 200:
                print("   ", raw[:400].replace("\n", " "))
        except Exception as e:  # noqa: BLE001
            print(f"{ep} {params} -> ERR {e}")
    with open("/tmp/probe_offline.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved -> /tmp/probe_offline.json")


if __name__ == "__main__":
    asyncio.run(main())
