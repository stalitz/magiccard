"""卡库查询：面值查卡 / 套卡浏览 / 合成路径。

数据源 data/card_lib.json 来自服务器在线版 card_info_v3.js（2026-09-07 抓取），
字段含义由源文件自带注释 + 实测交叉验证确认：

  card_list          [card_id, theme_id, CardName, Price(面值), Type, ...]
  theme_card_list    [theme_id, theme_name, 难度星级, 上架时间, ..., [card ids],
                      theme_type(类型值), version, time, 下线时间(offtime), ...]
  theme_compose_list [theme_id, state, target, mat1, mat2, mat3, 耗时秒]

分类标签（对齐游戏 UI「发行/绝版/闪卡」tab 实测计数，权威）：
  - 星级  = 主题难度字段（1-5 星，0 表示活动挖地等无星卡）
  - theme_type 是【类型值】不是位掩码。主卡箱三类（游戏实测 419/1171/13）：
        发行(在售) = type ∈ {0, 2}   （419 = 399 + 20）
        闪卡       = type == 9        （13，名字均带"(闪)"）
        下架(绝版) = type ∈ {1,5} 且非挖地非特殊（1171 = 215 + 992 − 34 − 2）
  - 独立模块（有独立卡册/卡箱，不在主卡箱显示，前端单独列表）：
        挖地      = idx18 == 1              （34 套，2026-09-09 已 35）
        猫咪旅行  = type6「一起去旅行」       （25 套）
        猫咪探险·西游记 / 三国战役 = type10/11（8 / 9 套，每日比赛子模块）
        特殊      = 道具卡(tid111) / 入学记(tid152)，非可收集卡册（2 套）
        合计 78 套 → 1681 − 78 = 1603 = 419 + 1171 + 13 ✓
  - 旧口径（位掩码 bit0=下架/bit3=闪卡）已被实测计数推翻：会得 444/1207/30。
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
def buyable_themes() -> frozenset[int]:
    """「商店能买到的套卡」集合 = 套卡内存在 10 面额基础卡的主题 tid。

    用户 2026-09-10 两次纠正后的判据：能否在商店买到是**套卡级**属性 —— 看这套里
    有没有 10 面额基础卡，与具体某张卡的自身面额无关。
    换卡时该限制只作用于这类套卡：换出它们的卡，其主题必须在对方「想收主题」(exch) 里；
    整套都没有 10 面额基础卡的（下架/绝版套，如纸寿千年、名家）商店买不到，
    **不受 exch 限制，同面额即可换**。

    手动换卡（exchange.py）与自动换卡（auto_exchange.py）共用本函数，
    避免两条链路的规则再次漂移。
    """
    idx = indexes()
    s: set[int] = set()
    for t in idx["themes"]:
        for cid in t["cards"]:
            ci = idx["card_by_id"].get(cid)
            if ci and int(ci[3]) == 10:
                s.add(t["tid"])
                break
    return frozenset(s)


@lru_cache(maxsize=1)
def indexes():
    """构建静态索引（进程内缓存一次）。"""
    lib = load()
    card_by_id: dict[int, list] = {}
    for c in lib["card_list"]:
        card_by_id[int(c[0])] = c

    def _cards_sorted(t) -> list[int]:
        """套卡内的卡按「面额降序」排列（2026-09-10 用户确认的展示规则）。

        原始数据 t[11] 只是「card id 降序」。对绝大多数套卡，id 是当年按层次连号
        录入的（150 终卡 → 40 中卡 → 10 基础卡），所以 id 降序碰巧等于面额降序；
        但后期追加过卡的套装会把 10 面额新卡插到最前面（典型：时尚男/女套装
        theme 40/45 追加了 id 64-69 的配饰卡），顺序就乱了。这里统一按
        (price 降序, id 降序) 重排，保证展示稳定符合游戏内习惯。
        """
        ids = [int(c) for c in t[11]] if isinstance(t[11], list) else []
        return sorted(
            ids,
            key=lambda cid: (-int(card_by_id.get(cid, [0, 0, "", 0])[3] or 0), -cid),
        )

    themes = []
    for t in lib["theme_card_list"]:
        suf = t[12:] or [0]
        name = str(t[1]).strip()
        ty = int(suf[0])
        tid = int(t[0])
        flag18 = int(suf[6]) if len(suf) > 6 and suf[6] is not None else 0
        # 独立模块（有独立卡册/卡箱，不在主卡箱显示，用户 2026-09-09 确认）
        #   挖地      = idx18 == 1（34 套，今日已 35）
        #   猫咪旅行  = type6「一起去旅行」（25 套）
        #   猫咪探险·西游记 / 三国战役 = type10 / type11（8/9 套，每日比赛子模块）
        #   特殊      = 道具卡(111) / 入学记(152)，非可收集卡册
        if flag18 == 1:
            module = "挖地"
        elif ty == 6:
            module = "猫咪旅行"
        elif ty == 10:
            module = "猫咪探险·西游记"
        elif ty == 11:
            module = "猫咪探险·三国战役"
        elif tid in (111, 152):
            module = "特殊"
        else:
            module = ""
        if module:
            kind = module
            flash = False
            offline = False
        else:
            # 主卡箱 发行/下架/闪卡 —— 对齐游戏 tab 实测 419/1171/13
            flash = ty == 9
            offline = ty not in (0, 2, 9)
            if flash:
                kind = "闪卡"
            elif offline:
                kind = "绝版"
            else:
                kind = "在售"
        # t[9] = 竖线分隔的 QQ秀/主题秀立绘模板 id 列表；其【项数 = 该套主题秀总数(立绘数)】。
        # 2026-10-09 真机+静态交叉验证：非 1 星套全库 = 4（1655 套）；1 星套 = 4（多数）或
        # 1（仅时尚男/女·经典男/女共 4 套）；道具卡(tid111)=0。这是「主题秀目录数」唯一可靠
        # 来源——收藏接口 card_collection_get 的 qqshow_str 是「当前生效立绘」列表（含过期
        # '0' 占位，会随 QQ秀有效期变化），total_cnt 是集齐次数 num，二者都不是目录数。
        # 必须在外层 for t 循环内用本行 t[9] 计算（下方 for th in themes 已脱离 t 作用域）。
        _show_raw = t[9] if len(t) > 9 and t[9] not in (None, "") else ""
        _show_total = len([x for x in str(_show_raw).split("|")
                            if str(x).strip() != ""])
        themes.append({
            "tid": int(t[0]),
            "name": name,
            "star": int(t[2]) if t[2] is not None else 0,
            "publish": int(t[3]) if t[3] is not None else 0,
            "flash": flash,
            "offline": offline,
            "kind": kind,
            "module": module,
            "type": ty,
            "cards": _cards_sorted(t),
            "show_total": _show_total,
        })
    # 主题内配方: theme -> {target: [theme, state, target, m1, m2, m3, secs]}
    comp_by_theme: dict[int, dict[int, list]] = {}
    # 全库配方: target -> row
    comp_by_target: dict[int, list] = {}
    for row in lib["theme_compose_list"]:
        tid, _, target, *_ = row
        tid, target = int(tid), int(target)
        comp_by_theme.setdefault(tid, {})[target] = row
        comp_by_target.setdefault(target, row)
    # 主题卡面值区间 + 可合成/可变标记
    for th in themes:
        prices = [card_by_id[c][3] for c in th["cards"]
                  if c in card_by_id and card_by_id[c][3] is not None]
        th["n"] = len(prices)
        th["pmin"] = min(prices) if prices else 0
        th["pmax"] = max(prices) if prices else 0
        # 可合成 = 该套在合成配方表(theme_compose_list)里有配方，能从炉子一层层炼出来；
        #          无配方 = 免合成，只能抽卡/收集/变卡/交换（实测与"全套面额相同"吻合 991/992）
        th["composable"] = len(comp_by_theme.get(th["tid"], {})) > 0
        # 可变 = 可以通过变卡得到（游戏源码 mfkp base.js: checkCanTransfer = !(theme[12]&0x04)）
        th["transferable"] = not (th["type"] & 4)
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
                offline: int = -1, module: str = "", transferable: int = -1,
                composable: int = -1, theme_show: int = -1,
                owned: dict | None = None, mini: dict | None = None,
                collection: dict | None = None,
                limit: int = 400):
    """套卡目录。

    module: "" = 全部；"main" = 仅主卡箱(module=="")；其他 = 具体独立模块名
            （挖地 / 猫咪旅行 / 猫咪探险·西游记 / 猫咪探险·三国战役 / 特殊）。
    transferable=1 → 只返回「能变卡得到」的套卡（变卡目标套卡候选，套卡级属性）。
    theme_show=1 → 只返回「主题秀没集满」的套卡：card_collection_get 里
          cnt(已收集立绘数) < 该套立绘总数 show_total（来自套卡静态数据 t[9] 的项数）。
          立绘总数按套不同——非 1 星全 4；1 星多数为 4、仅时尚男/女·经典男/女 4 套为 1；
          道具卡=0。故 1 星套不再一刀切排除，只有 show_total=0（道具卡等）或无
          收藏数据的模块套不参与。注意早期卡 QQ 秀分男女、用户常只选男立绘，重复选
          同一立绘只延长有效期不计数 → 老套 cnt 可能远小于 num，这是真实口径；
          但 cnt 是「已获得立绘数」（官方 UI「已获得」章），仍按 cnt < show_total 判定。
          不在 card_collection_get 里的套（旅行/挖地/三国/西游等模块套）无主题秀
          数据，show=None、不参与筛选。
    owned: 持有快照 {card_id: 数量} → 每套附 have（已持有的卡种数，用于标记「已有套卡」）。
    mini: 集卡册进度 {theme_id: (num, lv)}（来自 card_mini_get）→ 每套附
          sets（已炼 N 套；不在集卡册里的模块/特殊套 = 0 = 未炼，官方口径）。
    collection: 主题秀收集 {theme_id: (cnt, total_cnt)}（来自 card_collection_get）→
          每套附 show(已收集立绘数 cnt)。show_total(该套立绘总数) 取自套卡静态
          数据 t[9] 项数，仅在 tid 命中 collection 时为有效值。
    """
    idx = indexes()
    bt = buyable_themes()
    q = (q or "").strip().lower()
    matches = []
    for th in idx["themes"]:
        if q and q not in th["name"].lower():
            continue
        if star and th["star"] != star:
            continue
        if module:
            want = "" if module == "main" else module
            if th["module"] != want:
                continue
        if flash in (0, 1) and th["flash"] != bool(flash):
            continue
        if offline in (0, 1) and th["offline"] != bool(offline):
            continue
        if transferable in (0, 1) and th["transferable"] != bool(transferable):
            continue
        if composable in (0, 1) and th["composable"] != bool(composable):
            continue
        # 主题秀未集满筛选：在 card_collection_get 里有数据、且 cnt(已收集立绘) < 该套立绘总数
        # (show_total，来自 t[9] 项数)。立绘总数按套不同：非 1 星全 4，1 星多数为 4、仅时尚
        # 男/女·经典男/女 4 套为 1，道具卡=0。故不再以星级一刀切排除 1 星套。
        # 不在 card_collection_get 里的（模块套/无主题秀数据 show_total=0）不参与。
        if theme_show == 1:
            if th["tid"] not in (collection or {}) or th["show_total"] == 0:
                continue
            if (collection or {})[th["tid"]][0] >= th["show_total"]:
                continue
        matches.append(th)
    out = matches[:limit]

    def have_of(th):
        n = 0
        for cid in th["cards"]:
            if owned and owned.get(int(cid)):
                n += 1
        return n

    return {
        "total": len(matches),
        "shown": len(out),
        "themes": [{**{k: th[k] for k in
                    ("tid", "name", "star", "flash", "offline", "kind", "module",
                     "composable", "transferable", "n", "pmin", "pmax", "publish")},
                    "have": have_of(th),
                    "sets": (mini or {}).get(th["tid"], (0, 0))[0],
                    # show=None = 无主题秀数据（不在 card_collection_get，如模块套）
                    "show": ((collection or {}).get(th["tid"], (None,))[0]),
                    # show_total = 该套主题秀总数（t[9] 项数）；仅在 collection 里有数据时为
                    # 有效值，否则置 0（前端据此显示「主题秀 —」）
                    "show_total": (th["show_total"] if th["tid"] in (collection or {})
                                   else 0),
                    "theme_buyable": th["tid"] in bt} for th in out],
    }


def theme_detail(tid: int, owned: dict | None = None, mini: dict | None = None,
                collection: dict | None = None):
    """整套卡：每张的名称/面值/持有数/是否有配方(可炼)/被哪些卡用作素材。

    mini: 集卡册进度 {theme_id: 已炼成次数(num)} → sets（已炼 N 套 / 0=未炼）。
    collection: 主题秀收集 {theme_id: (cnt, total_cnt)} → show / show_total。
    """
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
                  ("tid", "name", "star", "flash", "offline", "kind", "module",
                   "composable", "transferable", "publish", "n")},
        # 套卡级「商店可买」标记：套内存在 10 面额基础卡 = 未下架在售套（buyable_themes 口径）
        "theme_buyable": tid in buyable_themes(),
        "sets": (mini or {}).get(tid, (0, 0))[0],
        # show=None = 无主题秀数据（不在 card_collection_get，如模块套）
        "show": ((collection or {}).get(tid, (None,))[0]),
        # show_total = 该套主题秀总数（t[9] 项数）；仅在 collection 里有数据时为有效值
        "show_total": (th["show_total"] if tid in (collection or {}) else 0),
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
                offline: int = -1, owned_only: int = 0, transferable: int = -1,
                owned: dict | None = None, limit: int = 300):
    """按面值（或关键词）全库查卡，附套卡信息与持有/可炼状态。

    transferable=1 → 只返回「能变卡得到」的卡（变卡目标候选）；=0 → 只返回不能变的。
    """
    idx = indexes()
    q = (q or "").strip().lower()
    themes_by_id = {t["tid"]: t for t in idx["themes"]}
    bt = buyable_themes()
    rows = []
    for c in idx["card_by_id"].values():
        cid = int(c[0])
        if price and int(c[3]) != price:
            continue
        if q:
            # 纯数字按卡 id 精确匹配（用户常直接搜 id，如 26188）；否则按卡名模糊匹配
            cid = int(q) if q.isdigit() else None
            if cid is not None:
                if int(c[0]) != cid:
                    continue
            elif q not in str(c[2]).lower():
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
        if transferable in (0, 1) and (not th["transferable"]) == (transferable == 1):
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
            "kind": th["kind"],
            "module": th["module"],
            "composable": th["composable"],
            "transferable": th["transferable"],
            "own": int(owned.get(cid, 0)) if owned else 0,
            "theme_buyable": th["tid"] in bt,
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
