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

from .spirite_maps import MAP_THEMES

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


# ---------------------------------------------------------------------------
# 套卡「出处」标注（2026-10-13 用户要求）：普通卡箱显示为 绝版·不可变·免合成、
# 实际来自特定商店渠道的套卡。
#
# 这些商店以「竞技币一次集齐整套」方式出售、整套价格统一 → 同一商店的套卡【总卡数 n 相同】
# （用户 2026-10-13 实测规律：天下卡每套总卡数都一样，因为商店有整套管选项、价格相同）。
# 据此用 n 分档（非游戏直接字段，是「统一价格」的推论，故对未点名套卡需用户复核）：
#
#   - 灵宠探险·大地图：灵宠探险大地图/荒岛/穿越丛林三子玩法碎片 → spirite_maps.MAP_THEMES
#        的 11 套（文明古国 … 悬崖之巅），与下方竞技币商店无关。
#   - 社团商店（每日比赛·公会/社团的社团商店，用户 2026-10-13 截图确认）：
#        n == 15 且 绝版·不可变·免合成·主卡箱 的套卡。本期套卡《幽冥玄境》(1802)，
#        上一期 西汉十五帝(1761)；同档还有 全能猫咪/新时代猫咪/猫咪圈子/健身猫咪
#        （用户确认同店）。历史约 10+ 套，每日 00:00 刷新，竞技币兑换。
#   - 自由市场（每日比赛里的自由市场，用户 2026-10-13 截图确认）：
#        n == 20 且 绝版·不可变·免合成·主卡箱 的套卡，支持「兑一套」（整套随机换）。
#        含 14 个「天下」系（神驹天下 1803 等）+ 百态猫咪/摩登猫咪
#        （用户确认同店）。在售「天下」套（儒学/雄霸/凤临/山水甲/落笔妙）n 各异，
#        不在此列；大明天下(n=8)/天下九州(n=9) 因 n 不符自然排除。
#
# ⚠️ n 分档是「整套管统一价格」的推论，非游戏字段。未点名的 n=15/n=20 套（城堡/神话/
#    凤鸣九天/元朝十五帝/中华美·悬鱼/协和万邦/仙山瑶界/拒绝内耗/权倾天下(下)）
#    若实际出自其他渠道，需用户确认后剔除。
#
#   - 活动限定套卡（ACTIVITY_THEMES）：非商店、非大地图，来自限时活动，部分【永久无法
#        100% 集齐】。例：世界杯冠军(710) = 世界杯「竞猜冠军」活动，猜对才能收集，是坑
#        活动（用户 2026-10-13 指出：一下导致有人不可能 100% 集齐），故从天下商店剥离单列。
#        注：tid=1263 实为「传奇场面」(n=10)，并非世界杯冠军（此前误称，已纠正）；其出处
#            未确认，暂不标记，待用户核实。全库仅 710 一套名为「世界杯冠军」。
# ---------------------------------------------------------------------------
# 灵宠探险·大地图的 11 套 theme_id（源自 spirite_maps.MAP_THEMES 的值，不是 pass_id）
MAP_THEME_IDS = frozenset(MAP_THEMES.values())

# 每日比赛两商店的产品线：社团商店/自由市场均以竞技币/星币整套兑换、同店套卡总卡数 n
# 相同（用户 2026-10-13 实测 + 截图：自由市场「兑一套」、社团商店本期幽冥玄境）。
SHOP_N_LINES = {15: "社团商店", 20: "自由市场"}

# 活动限定套卡：tid → 出处标签（非商店，来自限时活动，可能永久无法100%集齐）。
ACTIVITY_THEMES = {710: "世界杯竞猜活动"}


def _theme_source(tid, name, offline, transferable, composable, module, n):
    """返回套卡「出处」标签；普通套返回空串。"""
    if tid in MAP_THEME_IDS:
        return "灵宠探险·大地图"
    if tid in ACTIVITY_THEMES:
        return ACTIVITY_THEMES[tid]
    if (offline and not transferable and not composable and not module
            and n in SHOP_N_LINES):
        return SHOP_N_LINES[n]
    return ""


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
            # ★ 集齐一套的奖励（2026-11-06 真机实证，用户交「时尚女套装」(tid45) 实测
            #   user.money +100、user.exp +200，与卡库 t[6]=100 / t[7]=200 完全对上）：
            #   t[6] = 金币，t[7] = 经验。此前只当 t[6] 是「金币」用（economy.prize），
            #   t[7] 一直没解读过。
            "gold": int(t[6]) if t[6] is not None else 0,
            "exp": int(t[7]) if t[7] is not None else 0,
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
        # 特殊「出处」：这些套卡在普通卡箱显示为绝版·不可变·免合成，实际来自特定渠道
        th["source"] = _theme_source(th["tid"], th["name"], th["offline"],
                                     th["transferable"], th["composable"],
                                     th["module"], th["n"])
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


# ---- 主题秀「已获得立绘数 ob」计算（2026-10-17 实锤，唯一真源 = qqshow_str）----
# cnt(持有整套数/升级徽章) 与 num(集齐次数) 都不能判「主题秀是否集满」；只有把套卡 t[9]
# 的 giftid 经 gift_list 翻成 qq秀实例 id、比对 card_collection_get 的 qqshow_str 得到的
# ob 才是真源（与 auto_refine._collected_shows 同一口径）。
_GIFT_INST: dict | None = None
_THEME_GIFTS: dict | None = None


def _gift_inst_map() -> dict:
    """{giftid(str): qq秀实例id(str)}，来自 card_lib.json 的 gift_list（每项=[giftid,type,qq_show_id,name]）。"""
    global _GIFT_INST
    if _GIFT_INST is None:
        lib = load()
        m: dict = {}
        for g in (lib.get("gift_list") or []):
            try:
                m[str(g[0])] = str(g[2])
            except Exception:
                pass
        _GIFT_INST = m
    return _GIFT_INST


def _theme_gifts_map() -> dict:
    """{tid(int): [giftid(str), ...]}，来自 card_lib.json theme_card_list 的 t[9]（竖线分隔）。"""
    global _THEME_GIFTS
    if _THEME_GIFTS is None:
        lib = load()
        m: dict = {}
        for t in lib.get("theme_card_list", []):
            raw = t[9] if len(t) > 9 and t[9] not in (None, "") else ""
            m[int(t[0])] = [x.strip() for x in str(raw).split("|") if str(x).strip()]
        _THEME_GIFTS = m
    return _THEME_GIFTS


def _ob_of(tid: int, qqshow_str: str) -> int:
    """该套主题秀已获得立绘数（ob）。qqshow_str 为空 → 0（一套都未集）。"""
    if not qqshow_str:
        return 0
    got = {x.strip() for x in qqshow_str.replace("&#x2C;", ",").split(",") if x.strip()}
    gmap = _gift_inst_map()
    return sum(1 for g in _theme_gifts_map().get(tid, [])
               if (inst := gmap.get(g)) and inst in got)


def list_themes(q: str = "", star: int = 0, flash: int = -1,
                offline: int = -1, module: str = "", transferable: int = -1,
                composable: int = -1, theme_show: int = -1,
                owned: dict | None = None, mini: dict | None = None,
                collection: dict | None = None,
                source: str = "",
                limit: int = 400):
    """套卡目录。

    module: "" = 全部；"main" = 仅主卡箱(module=="")；其他 = 具体独立模块名
            （挖地 / 猫咪旅行 / 猫咪探险·西游记 / 猫咪探险·三国战役 / 特殊）。
    transferable=1 → 只返回「能变卡得到」的套卡（变卡目标套卡候选，套卡级属性）。
    theme_show=1 → 只返回「主题秀没集满」的套卡：card_collection_get 的 qqshow_str
          解析出的【已获得立绘数 ob】< 该套立绘总数 show_total（来自套卡静态数据 t[9] 的项数）。
          立绘总数按套不同——非 1 星全 4；1 星多数为 4、仅时尚男/女·经典男/女 4 套为 1；
          道具卡=0。故 1 星套不再一刀切排除，只有 show_total=0（道具卡等）无主题秀数据
          的套不参与。**一套都未集（从未收集过主题秀、不在 card_collection_get 响应里）的套
          也纳入**——不在 collection 里视为 ob=0，仍算「未集齐」。
          ⚠️ 判满唯一真源 = qqshow_str（t[9] 的 giftid 经 gift_list 翻实例 id 比对），
          绝不用 cnt（cnt=持有整套数/升级徽章，会随升级消耗下降）也不用 num（集齐次数）。
          2026-10-17 实锤：闹元宵 cnt=1 但 qqshow=4/4，城市车影 num=6 但 qqshow=3/4。
    owned: 持有快照 {card_id: 数量} → 每套附 have（已持有的卡种数，用于标记「已有套卡」）。
    mini: 集卡册进度 {theme_id: (num, lv)}（来自 card_mini_get）→ 每套附
          sets（已炼 N 套；不在集卡册里的模块/特殊套 = 0 = 未炼，官方口径）。
    collection: 主题秀收集 {theme_id: (cnt, total_cnt, latest_ts, early_ts, qqshow_str)}（来自
          card_collection_get）→ 每套附 show(主题秀已获得立绘数 ob，由 qqshow_str 经 gift_list
          比对算出，是判满唯一真源；绝不用 cnt/num)、show_total(该套立绘总数，
          取自套卡静态数据 t[9] 项数，始终有效，不依赖 collection 命中)、
          last_ts/early_ts(最后/最早收集时间戳)。不在 collection 里的套（一套都未集）
          show 取 0、时间戳取 0。
    """
    idx = indexes()
    bt = buyable_themes()
    q = (q or "").strip().lower()
    matches = []
    for th in idx["themes"]:
        if q:
            # 文本 → 名称模糊匹配；纯数字 → 同时匹配主题 id（tid），便于按 id 定位套卡
            if q.isdigit():
                if str(th["tid"]) != q and q not in th["name"].lower():
                    continue
            elif q not in th["name"].lower():
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
        if source and th["source"] != source:
            continue
        # 主题秀未集满筛选：cnt(已收集立绘) < 该套立绘总数 show_total（来自 t[9] 项数）。
        # 立绘总数按套不同：非 1 星全 4，1 星多数为 4、仅时尚男/女·经典男/女 4 套为 1，
        # 道具卡=0。故不再以星级一刀切排除 1 星套。
        # ★ 一套都未集（从未收集过主题秀、不在 card_collection_get 响应里）也纳入：
        #   不在 collection 里视为 cnt=0，仍算「未集齐」；只有 show_total==0（道具卡等
        #   无主题秀数据）才不参与。2026-10-13 修正旧逻辑 `tid not in collection` 漏掉这类套。
        if theme_show == 1:
            if th["show_total"] == 0:
                continue
            ob = _ob_of(th["tid"], (collection or {}).get(th["tid"], (0, 0, 0, 0, ""))[4])
            if ob >= th["show_total"]:
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
                     "source", "composable", "transferable", "n", "pmin", "pmax",
                     "publish", "gold", "exp")},
                    "have": have_of(th),
                    "sets": (mini or {}).get(th["tid"], (0, 0))[0],
                    # show = 主题秀已获得立绘数（ob，来自 qqshow_str）——2026-10-17 实锤：
                    # 绝不用 cnt（持有整套数/升级徽章）或 num（集齐次数）。不在 collection 里
                    # = 一套都未集 → 0。
                    "show": _ob_of(th["tid"], (collection or {}).get(th["tid"], (0, 0, 0, 0, ""))[4]),
                    # show_total = 该套主题秀总数（t[9] 项数），始终取静态值，不再因
                    # collection 命中与否置 0。
                    "show_total": th["show_total"],
                    # 最后/最早收集主题秀的时间戳（latest_ts/early_ts）；一套都未集 → 0。
                    "last_ts": ((collection or {}).get(th["tid"], (0, 0, 0, 0))[2]),
                    "early_ts": ((collection or {}).get(th["tid"], (0, 0, 0, 0))[3]),
                    "theme_buyable": th["tid"] in bt} for th in out],
    }


def theme_detail(tid: int, owned: dict | None = None, mini: dict | None = None,
                collection: dict | None = None):
    """整套卡：每张的名称/面值/持有数/是否有配方(可炼)/被哪些卡用作素材。

    mini: 集卡册进度 {theme_id: 已炼成次数(num)} → sets（已炼 N 套 / 0=未炼）。
    collection: 主题秀收集 {theme_id: (cnt, total_cnt, latest_ts, early_ts)} →
          show / show_total / last_ts / early_ts。
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
                   "source", "composable", "transferable", "publish", "n",
                   "gold", "exp")},
        # 套卡级「商店可买」标记：套内存在 10 面额基础卡 = 未下架在售套（buyable_themes 口径）
        "theme_buyable": tid in buyable_themes(),
        "sets": (mini or {}).get(tid, (0, 0))[0],
        # show = 主题秀已获得立绘数（ob，来自 qqshow_str）；不在 collection 里 = 一套都未集 → 0。
        "show": _ob_of(tid, (collection or {}).get(tid, (0, 0, 0, 0, ""))[4]),
        # show_total = 该套主题秀总数（t[9] 项数），始终取静态值。
        "show_total": th["show_total"],
        # 最后/最早收集主题秀时间戳；一套都未集 → 0。
        "last_ts": ((collection or {}).get(tid, (0, 0, 0, 0))[2]),
        "early_ts": ((collection or {}).get(tid, (0, 0, 0, 0))[3]),
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
    """全部面值及套数分布，供前端快捷筛选用。

    返回每个面值：
      price  : 面额
      n      : 卡库里该面额的卡总数（即「箱里有几张」的基数）
      themes : 有多少套卡的组成里包含该面额（衡量「常用程度」——
               被越多套卡用到越常用，10 面额几乎所有可合成套都有）
    """
    idx = indexes()
    from collections import Counter
    cnt = Counter(int(c[3]) for c in idx["card_by_id"].values()
                  if c[3] is not None)
    # 每个面额被多少套卡使用：遍历套卡组成（theme.cards）→ 面额去重后累加
    theme_use = Counter()
    cby = idx["card_by_id"]
    for th in idx["themes"]:
        seen = set()
        for cid in th.get("cards") or []:
            c = cby.get(int(cid))
            if c and c[3] is not None:
                seen.add(int(c[3]))
        for p in seen:
            theme_use[p] += 1
    return [{"price": p, "n": cnt[p], "themes": theme_use.get(p, 0)} for p in
            sorted(cnt, key=lambda p: -cnt[p])]
