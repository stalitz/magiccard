#!/usr/bin/env python3
"""挖地商店（现金/金币/魔力买工具）接口侦查（只读）。

阶段一：从 card_run_base 的 version_list 找与 shop/wadi/dig 相关的模块与配置 URL。
阶段二：对候选端点做**零副作用探活**（act=999999 → -1110 说明端点在、无配置）。
"""
import asyncio
import re
import sys

from backend.mc_api import LoginSession

PAT = re.compile(r'[A-Za-z_]{3,40}\s*[=:>]\s*"?[^"<>,\s]{4,150}')


async def stage1(s):
    r = await s.post("card_run_base")
    raw = r.get("raw", "") or ""
    print(f"run_base raw len={len(raw)}")
    swfs = sorted(set(re.findall(r"[A-Za-z_0-9]+\.swf", raw)))
    print(f"modules={len(swfs)}")
    for u in swfs:
        mark = ""
        if re.search(r"(?i)shop|store|market|wadi|dig", u):
            mark = "  <<<"
        print("   ", u, mark)
    print("--- keys mentioning shop/store/market/wadi/dig ---")
    for m in PAT.finditer(raw):
        seg = m.group(0)
        if re.search(r"(?i)shop|store|market|wadi|dig", seg):
            print("   ", seg[:150])


async def stage2(s, eps):
    for ep in eps:
        try:
            r = await s.post(ep, act=999999)
            d = r.get("data")
            raw = str(r.get("raw", ""))[:160]
            print(f"--- {ep}: code={r.get('code')} msg={str(r.get('message',''))[:50]}")
            print("    data=", str(d)[:200])
            print("    raw =", raw)
        except Exception as e:  # noqa: BLE001
            print(f"--- {ep}: EXC {e}")


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    stage = sys.argv[1] if len(sys.argv) > 1 else "1"
    if stage == "1":
        await stage1(s)
    elif stage == "3":
        await stage3(s)
    else:
        await stage2(s, sys.argv[2:] or [
            "card_market_mana_buy", "card_market_magic_diamond_buy",
            "card_market_midas_mana_buy", "card_shop_1", "card_shop_2",
        ])


# ───────────────────── 阶段三：SWF / act_addon 里的商店线索 ─────────────────────
import urllib.request  # noqa: E402
import zlib  # noqa: E402
import pathlib  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0"}


def _fetch(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()


def _swf_strings(b: bytes):
    if b[:3] == b"CWS":
        b = b[:8] + zlib.decompress(b[8:])
    return sorted(set(re.findall(rb"[a-zA-Z_][a-zA-Z0-9_]{3,44}", b))), b


async def stage3(s):
    r = await s.post("card_run_base")
    raw = r.get("raw", "") or ""
    urls = {}
    for m in re.finditer(r"([A-Za-z_0-9]+)\s*=\s*\"?(http[^\"<>\s]+?\.(?:swf|xml))", raw):
        urls[m.group(1)] = m.group(2)
    for key in re.finditer(r"\"?([A-Za-z_]{3,40})\"?\s*[:=]\s*\"?(http[^\"<>\s]+)\"?", raw):
        urls.setdefault(key.group(1), key.group(2))
    shop_swf = [u for u in urls.values() if re.search(r"(?i)shop", u)]
    print("shop urls:", shop_swf)

    for u in shop_swf:
        try:
            b = _fetch(u)
        except Exception as e:  # noqa: BLE001
            print("  fetch fail", u, e)
            continue
        strs, body = _swf_strings(b)
        hit = [x.decode() for x in strs
               if re.search(rb"(?i)cash|mana|jinshi|gold|buy|price|cost|shop|tool|dig", x)]
        print(f"== {u.split('/')[-1]} strings={len(strs)} hits={len(hit)}")
        for h in hit:
            print("   ", h)

    # act_addon：活动附加配置（商店表常在这里）
    print("\n=== act_addon ===")
    aurl = urls.get("actAddon") or re.search(r"(http[^\"<>\s]+act_addon[^\"<>\s]+)", raw).group(1)
    try:
        x = _fetch(aurl).decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print("  act_addon fetch fail", e)
        return
    print("  len", len(x))
    for kw in ("锄头", "一字型", "九宫格", "现金", "金币", "魔力", "商店", "shop", "cash", "mana"):
        n = x.count(kw)
        if n:
            print(f"   {kw}: {n}")
    for m in re.finditer(r"<([A-Za-z_]*[Ss]hop[A-Za-z_]*)[^>]*>", x):
        print("   tag:", m.group(0)[:120])



if __name__ == "__main__":
    asyncio.run(main())
