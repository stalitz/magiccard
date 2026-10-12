"""只读探针：比对某套 t[9] 全量 qq秀 gift id 与 card_collection_get 的 qqshow_str 已收集 id，
确认两者是否同一命名空间（决定“挑未收集 qq秀上交”能否用 t[9] 的 id 直接当 giftid）。"""
import asyncio, re
from backend.mc_api import LoginSession
from backend.card_query import indexes


async def main():
    tids = [int(x) for x in __import__("sys").argv[1:]] or [203]
    s = LoginSession()
    if not s.restore():
        print("未登录（无 cookie）")
        return
    r = await s.post("card_collection_get")
    raw = r.get("raw", "")
    print("code=", r.get("code"), "raw_len=", len(raw))

    def field(spec, name):
        m = re.search(rf'{name}="([^"]*)"', spec)
        return m.group(1) if m else None

    coll: dict[int, str] = {}
    for spec in re.findall(r'<Node\s+([^>]*?)/?>', raw):
        tid = int(field(spec, "theme_id") or -1)
        coll[tid] = field(spec, "qqshow_str") or ""

    import json
    lib = json.load(open("data/card_lib.json"))
    themes = {int(t[0]): t for t in lib["theme_card_list"]}
    for tid in tids:
        t = themes.get(tid)
        if not t:
            print(f"tid={tid} 不在卡库"); continue
        all_gifts = [x for x in str(t[9] or "").split("|") if x]
        qs = coll.get(tid, "")
        collected = [x for x in re.split(r"[&#x2C;|,|\s]+", qs) if x and x.isdigit()]
        print(f"\n=== tid={tid} {t[1]} star={t[2]} ===")
        print(f"  t[9] 全量 gift id ({len(all_gifts)}): {all_gifts}")
        print(f"  qqshow_str 原始: {qs!r}")
        print(f"  qqshow_str 已收集 id ({len(collected)}): {collected}")
        print(f"  已收集∩全量 = {[g for g in collected if g in all_gifts]}")
        print(f"  全量中【未收集】= {[g for g in all_gifts if g not in set(collected)]}")
        print(f"  命名空间一致? {'是' if (set(collected).issubset(set(all_gifts)) or not collected) else '否（已收集有不在全量里的 id）'}")


if __name__ == "__main__":
    asyncio.run(main())
