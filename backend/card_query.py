"""卡库查询：面值查卡 / 套卡浏览 / 合成路径。

数据源 data/card_lib.json 来自服务器在线版 card_info_v3.js（2026-09-07 抓取），
字段含义由源文件自带注释 + 实测交叉验证确认：

  card_list          [card_id, theme_id, CardName, Price(面值), Type, ...]
  theme_card_list    [theme_id, theme_name, 难度星级, 上架时间, ..., [card ids],
                      theme_type(9=闪卡), version, time, 下线时间(offtime), ...]
  theme_compose_list [theme_id, state, target, mat1, mat2, mat3, 耗时秒]

分类标签：
  - 星级  = 主题难度字段（1-5 星，0 表示活动挖地等无星卡）
  - 闪卡  = theme_type==9（与主题名带(闪)完全一致，13 套）
  - 下线  = 主题 suffix 第 4 字段 > 0（限时活动套已下架时间戳；仅作参考标记，
            与游戏内「绝版/可变」口径是否一致待用户校准）
  - 可炼  = 该卡在合成配方表中作为目标出现（有 recipe 就能炼；否则是基础素材卡
            或只能通过获得渠道取得）
"""
import json
from pathlib import Path
from functools import lru_cache

DATA = Path(__file__).resolve().parent.parent / "data"

_loaded: dict | None = None


def load():
    global _loaded
    if _loaded is None:
        _loaded = json.loads((DATA / "card_lib.json").read_text(encoding="utf-8"))
    return _loaded


@lru_cache(maxsize=1)
def indexes():
    """构建静态索引（进程内缓存一次）。"""
    lib = load()
    themes = []
    for t in lib["theme_card_list"]:
        suf = t[12:] or [0]
        name = str(t[1]).strip()
        themes.append({
            "tid": int(t[0]),
            "name": name,
            "star": int(t[2]) if t[2] is not None else 0,
            "publish": int(t[3]) if t[3] is not None else 0,
            "flash": int(suf[0]) == 9,
            "offline": int(suf[3]) > 0 if len(suf) >= 4 else 0,
            "type": int(suf[0]),
            "cards": [int(c) for c in t[11]] if isinstance(t[11], list) else [],
        })
    card_by_id: dict[int, list] = {}
    for c in lib["card_list"]:
        card_by_id[int(c[0])] = c
    # 主题内配方: theme -> {target: [theme, state, target, m1, m2, m3, secs]}
    comp_by_theme: dict[int, dict[int, list]] = {}
    # 全库配方: target -> row
    comp_by_target: dict[int, list] = {}
    for row in lib["theme_compose_list"]:
        tid, _, target, *_ = row
        tid, target = int(tid), int(target)
        comp_by_theme.setdefault(tid, {})[target] = row
        comp_by_target.setdefault(target, row)
    # 主题卡面值区间
    for th in themes:
        prices = [card_by_id[c][3] for c in th["cards"]
                  if c in card_by_id and card_by_id[c][3] is not None]
        th["n"] = len(prices)
        th["pmin"] = min(prices) if prices else 0
        th["pmax"] = max(prices) if prices else 0
    themes.sort(key=lambda x: (-x["publish"], x["tid"]))
    return {
        "themes": themes,
        "card_by_id": card_by_id,
        "comp_by_theme": comp_by_theme,
        "comp_by_target": comp_by_target,
    }


def cname(idx, cid):
    c = idx["card_by_id"].get(cid)
    return str(c[2]).strip() if c else f"卡{cid}"


def cprice(idx, cid):
    c = idx["card_by_id"].get(cid)
    return c[3] if c else 0


def list_themes(q: str = "", star: int = 0, flash: int = -1,
                offline: int = -1, limit: int = 400):
    idx = indexes()
    q = (q or "").strip().lower()
    out = []
    for th in idx["themes"]:
        if q and q not in th["name"].lower():
            continue
        if star and th["star"] != star:
            continue
        if flash in (0, 1) and th["flash"] != bool(flash):
            continue
        if offline in (0, 1) and th["offline"] != bool(offline):
            continue
        out.append(th)
        if len(out) >= limit:
            break
    return {
        "total": len(idx["themes"]),
        "shown": len(out),
        "themes": [{k: th[k] for k in
                    ("tid", "name", "star", "flash", "offline",
                     "n", "pmin", "pmax", "publish")} for th in out],
    }


def theme_detail(tid: int, owned: dict | None = None):
    """整套卡：每张的名称/面值/持有数/是否有配方(可炼)/被哪些卡用作素材。"""
    idx = indexes()
    th = next((t for t in idx["themes"] if t["tid"] == tid), None)
    if th is None:
        return None
    comp = idx["comp_by_theme"].get(tid, {})
    used_by: dict[int, list[int]] = {}
    for row in comp.values():
        for m in row[3:6]:
            used_by.setdefault(int(m), []).append(int(row[2]))
    cards = []
    for cid in th["cards"]:
        c = idx["card_by_id"].get(cid)
        if c is None:
            continue
        row = comp.get(cid)
        cards.append({
            "id": cid,
            "name": str(c[2]).strip(),
            "price": c[3],
            "own": int(owned.get(cid, 0)) if owned else 0,
            "recipe": ({
                "mats": [{"id": int(m), "name": cname(idx, int(m)),
                          "price": cprice(idx, int(m))} for m in row[3:6]],
                "secs": int(row[6]),
            }) if row else None,
            "used_by": [{"id": t, "name": cname(idx, t),
                         "price": cprice(idx, t)} for t in used_by.get(cid, [])],
        })
    return {
        "theme": {k: th[k] for k in
                  ("tid", "name", "star", "flash", "offline", "publish", "n")},
        "cards": cards,
    }


@lru_cache(maxsize=4096)
def card_chain(cid: int, depth: int = 6):
    """向上追溯合成路径（本卡 ← 素材 ← 素材…直到基础素材卡）。"""
    if depth <= 0:
        return None
    idx = indexes()
    c = idx["card_by_id"].get(cid)
    if c is None:
        return {"id": cid, "name": f"卡{cid}", "price": None,
                "recipe": None, "is_base": True}
    node = {"id": cid, "name": str(c[2]).strip(), "price": c[3]}
    row = idx["comp_by_target"].get(cid)
    if row:
        node["recipe"] = [
            {"mat": card_chain(int(m), depth - 1), "secs": int(row[6])}
            for m in row[3:6]
        ]
    else:
        node["recipe"] = None
    return node


def query_cards(price: int = 0, q: str = "", star: int = 0, flash: int = -1,
                offline: int = -1, owned_only: int = 0,
                owned: dict | None = None, limit: int = 300):
    """按面值（或关键词）全库查卡，附套卡信息与持有/可炼状态。"""
    idx = indexes()
    q = (q or "").strip().lower()
    themes_by_id = {t["tid"]: t for t in idx["themes"]}
    rows = []
    for c in idx["card_by_id"].values():
        cid = int(c[0])
        if price and int(c[3]) != price:
            continue
        if q and q not in str(c[2]).lower():
            continue
        th = themes_by_id.get(int(c[1]))
        if th is None:
            continue
        if star and th["star"] != star:
            continue
        if flash in (0, 1) and th["flash"] != bool(flash):
            continue
        if offline in (0, 1) and th["offline"] != bool(offline):
            continue
        if owned_only and not (owned and owned.get(cid)):
            continue
        row = idx["comp_by_target"].get(cid)
        rows.append({
            "id": cid,
            "name": str(c[2]).strip(),
            "price": c[3],
            "theme_id": th["tid"],
            "theme_name": th["name"],
            "publish": th["publish"],
            "star": th["star"],
            "flash": th["flash"],
            "offline": th["offline"],
            "own": int(owned.get(cid, 0)) if owned else 0,
            "refinable": row is not None,
            "recipe": ({
                "mats": [cname(idx, int(m)) for m in row[3:6]],
                "secs": int(row[6]),
            }) if row else None,
        })
    total = len(rows)
    rows.sort(key=lambda r: (-r["own"], -r["publish"], r["name"]))
    shown = rows[:max(0, limit)]
    return {"total": total, "shown": len(shown), "cards": shown,
            "limited": total > len(shown)}


def price_stats():
    """全部面值及套数分布，供前端快捷筛选用。"""
    idx = indexes()
    from collections import Counter
    cnt = Counter(int(c[3]) for c in idx["card_by_id"].values()
                  if c[3] is not None)
    return [{"price": p, "n": cnt[p]} for p in
            sorted(cnt, key=lambda p: -cnt[p])[:16]]
