"""刷新魔法卡片卡库：从官方在线 card_info_v3.js 重新拉取并重建 data/card_lib.json。

官方数据源：https://appimg.qq.com/card/mk/card_info_v3.js
  - 2.3MB 静态 JS，顶层为若干全局变量数组字面量（单引号字符串、0x 十六进制、undefined）。
  - 本机快照为 2026-09-07 抓取，之后新套/新卡（如卡 26188「饭否」）不会进本地库，
    导致炼卡/卡箱/礼物/卡库查询缺数据。

本模块复刻当时的落地逻辑：拉取 -> 提取 card_list/theme_card_list/theme_compose_list/gift_list
-> 写回 data/card_lib.json（与旧 schema 完全一致）。纯公开数据，不需要登录态。
"""
import re
import ast
import json
from pathlib import Path

CARD_LIB_URL = "https://appimg.qq.com/card/mk/card_info_v3.js"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_FILE = DATA_DIR / "card_lib.json"
FIELDS = ("card_list", "theme_card_list", "theme_compose_list", "gift_list")


def _extract_array(js: str, name: str):
    """从全局 `var NAME = [...];` 提取值，返回 Python list（无法解析返回 None）。"""
    m = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*\[", js)
    if not m:
        return None
    i = m.end() - 1  # 指向 '['
    depth = 0
    n = len(js)
    k = i
    while k < n:
        ch = js[k]
        if ch == "'":
            # 跳过字符串（处理转义 \'），不计入括号深度
            k += 1
            while k < n:
                if js[k] == "\\":
                    k += 2
                    continue
                if js[k] == "'":
                    k += 1
                    break
                k += 1
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return _parse_array(js[i:k + 1])
        k += 1
    return None


def _parse_array(raw: str):
    """JS 数组字面量 -> Python list。card_info_v3.js 是数据，字段用单引号、含 0x 十六进制、
    undefined、偶有 true/false/NaN。Python ast 原生认单引号与 0x，只需清洗少量 JS 专属 token。"""
    s = raw
    s = s.replace("undefined", "None")
    s = re.sub(r"\bNaN\b", "0", s)
    s = re.sub(r"\btrue\b", "True", s)
    s = re.sub(r"\bfalse\b", "False", s)
    # 去掉尾随逗号（, ]  / , }）
    s = re.sub(r",(\s*[\]}])", r"\1", s)
    return ast.literal_eval(s)


async def refresh_card_lib() -> dict:
    """下载官方 JS 并重建 card_lib.json。返回统计。"""
    import httpx

    old = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    old_ids = {int(c[0]) for c in old.get("card_list", []) if c and len(c) > 0}
    old_themes = {int(t[0]) for t in old.get("theme_card_list", []) if t and len(t) > 0}

    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0"}) as c:
        r = await c.get(CARD_LIB_URL)
        r.raise_for_status()
        js = r.text

    lib = {}
    for f in FIELDS:
        arr = _extract_array(js, f)
        if arr is not None:
            lib[f] = arr
    if not lib.get("card_list"):
        raise RuntimeError("未能从官方源解析出 card_list（源结构可能已变）")

    OUT_FILE.write_text(json.dumps(lib, ensure_ascii=False), encoding="utf-8")
    # 写文件后清除所有持有 card_lib 进程内缓存的模块，使刷新立即对查询/西游等生效
    # （否则运行中的后端进程一直用启动时的旧快照，刷新文件也不生效）
    _purge_card_lib_caches()

    new_ids = {int(c[0]) for c in lib.get("card_list", []) if c and len(c) > 0}
    new_themes = {int(t[0]) for t in lib.get("theme_card_list", []) if t and len(t) > 0}
    return {
        "ok": True,
        "added_cards": sorted(new_ids - old_ids),
        "added_themes": sorted(new_themes - old_themes),
        "total_cards": len(new_ids),
        "total_themes": len(new_themes),
        "size": OUT_FILE.stat().st_size,
    }


def _purge_card_lib_caches() -> None:
    """写文件后清除进程内 card_lib 缓存，使刷新即时生效（无需重启）。

    持缓存的模块：
      - card_query：模块级 _loaded + indexes/buyable_themes/query_cards/card_chain 的 lru_cache
      - xiyou：模块级 _XY_LIB / _XY_THEMES
    其余模块（match/auto_refine/card_plan/gift/economy）每次调用重读文件，无需清。
    """
    # card_query：卡库查询接口的数据源
    try:
        from . import card_query as _cq
        try:
            _cq._loaded = None
        except Exception:  # noqa: BLE001
            pass
        for fn in ("indexes", "buyable_themes", "query_cards", "card_chain"):
            f = getattr(_cq, fn, None)
            if f is not None and hasattr(f, "cache_clear"):
                try:
                    f.cache_clear()
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    # xiyou：西游/主题名查询的卡库缓存
    try:
        from . import xiyou as _xy
        try:
            _xy._XY_LIB = None
        except Exception:  # noqa: BLE001
            pass
        try:
            _xy._XY_THEMES = None
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(refresh_card_lib()))
