"""探测主界面加载时的端点（2026-09-15 用户抓包清单）。

只打**只读查询**类端点；card_act_get_gifts / card_act_send_free 有领奖副作用，不在此列。
无参数 / uin= / opuin= 三种参数各试一遍，打印 code 与内容前 400 字符。
"""
import asyncio
import json
import re

from backend.mc_api import LoginSession

EPS = [
    "card_run_base",
    "card_act_peak",
    "card_zhongkong_zk",
    "card_act_travel",
    "card_market_props_info",
    "card_user_memo",
    "card_badge_info",
    "card_user_temp_slot",
    "card_monthtask",
    "card_act_volume",
    "freegift/freegift_get_num",
]

PARAM_SETS = [
    ("(no param)", {}),
    ("uin", "uin"),
    ("opuin", "opuin"),
]


async def main():
    s = LoginSession()
    s.restore()
    uin = str(int(str(s.uin)))  # 去前导零（spirite 铁律）
    print(f"uin={uin} raw={s.uin}")

    out = []
    for ep in EPS:
        print("\n" + "=" * 70)
        print(f"### {ep}")
        for label, key in PARAM_SETS:
            params = {} if key == "" or label.startswith("(") else {key: uin}
            try:
                r = await s.post(ep, **params)
            except Exception as e:  # noqa: BLE001
                print(f"  [{label}] ERR {e}")
                continue
            raw = (r.get("raw") or "").strip()
            code = r.get("code")
            flat = re.sub(r"\s+", " ", raw)
            print(f"  [{label}={params}] code={code} len={len(raw)}")
            if raw:
                print("     " + flat[:400])
            out.append({"ep": ep, "label": label, "params": params,
                        "code": code, "len": len(raw), "raw": raw[:3000]})
        # 每个端点至少出一个有内容的就够，不必三种都留全量
    with open("/tmp/probe_home.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved -> /tmp/probe_home.json")


if __name__ == "__main__":
    asyncio.run(main())
