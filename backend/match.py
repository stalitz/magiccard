"""每日比赛只读看板（2026-09-25 由 bisai.har 抓包实证）。

来源：用户提供的 bisai.har（32 条请求，仅部分＝每日比赛相关模块）。
HAR 中实证到的端点与参数：

  card_travel_elf_competition  POST act=1,opuin=<uin>  → 比赛主页状态（3.3KB）
                               POST act=6               → 装备 / 属性（39KB）
                               POST act=20              → 金卡每日购买量（0.3KB）
  card_travel_elf_pvp          POST act=14,rank_id=0    → PVP **日**榜（78KB / 1000 人）
  card_travel_elf_union        POST act=1（工会）/ act=22（远征关卡）/ act=14(uins=…)（查成员）
  card_msg_board               POST op=1,type=0,ts,endts→ 留言板（好友祝福）
  card_zhongkong_zk            POST id=2,osSystem=windows（中控埋点，与玩法无关）

本模块**只做只读聚合**：不猜 act 语义、不构造任何写操作（HAR 里没有暴露
「打一场比赛 / 提交成绩」的 act，抓包只有进页面 + 看装备 + 买金卡）。
字段语义一律以 HAR 里的原始 key 为准；含义不确定的**原样透出**，不做中文硬译
（避免把推测写成常量，见项目 MEMORY.md 铁律）。
"""
from typing import Optional

from .constants import (
    EP_TRAVEL_MATCH, EP_TRAVEL_PVP, EP_TRAVEL_UNION,
    EP_WORLD_SANGUO, EP_WORLD_EXPLORE,
)

# HAR 实测返回的字段（不确定的与中文猜测并列，宁可原样透出也不瞎译）
_KEEP_SCALARS = (
    "clover_num", "dzs", "ele", "hbs", "jhs",
    "freegift", "gvg_sign", "gvg_cheer",
    "talent_score", "talent_rate", "tf_exp",
    "performe_num", "perform_limit_ts",
    "i_1514", "i_1515", "unionid", "union_token", "ch_name", "guide",
    "formation", "season_rank_gift", "tadd", "img_items",
)


def _pick(data: dict) -> dict:
    """取出要透出的标量字段（未知 key 一律不猜，缺失就给 None）。"""
    out = {}
    for k in _KEEP_SCALARS:
        if k in data:
            out[k] = data[k]
    return out


def _cats(rows: list) -> list:
    """travel_elf_arr → 前端要的猫列表（只挑 HAR 里确实存在的 key）。"""
    out = []
    for c in rows or []:
        out.append({
            "elf_id": c.get("elf_id"),
            "level": c.get("cat_level"),
            "exp": c.get("cat_exp"),
            "star": c.get("cat_star"),
            "star_new": c.get("cat_star_new"),
            "mood": c.get("cat_mood"),
            "motion": c.get("cat_motion"),
            "perform": c.get("cat_perform"),
            "exp2": c.get("ce"),
            "appearance": c.get("cat_appearance"),
            "equip": c.get("equip_arr"),
            "skills": c.get("skill_data"),
            "talent": c.get("talent_arr"),
            "tease": c.get("tease"),
        })
    return out


def _plain_uin(session) -> str:
    """opuin 必须传**不带前导零**的纯数字 uin（2026-09-25 实测踩坑）。

    session.uin 是前端补零格式（如 "0004821992"），直接拿去当 opuin 传，
    服务端照样回 code=0，但 data 只剩 11 个 key、travel_elf_arr 为空
    （talent_score=0 / performe_num 缺失）——极易被误读成「该号未开通」。
    传 "4821992" 则完整返回 4 只精灵 + talent_score=650。
    """
    u = str(session.uin or "").strip()
    try:
        return str(int(u))
    except (TypeError, ValueError):
        return u


async def _json_of(r: dict) -> dict:
    """响应统一解成 dict：qzone 类响应把 JSON 放在 data / raw 里。"""
    if not isinstance(r, dict):
        return {}
    d = r.get("data")
    if isinstance(d, dict) and d:
        return d
    raw = r.get("raw")
    if isinstance(raw, str) and raw.strip():
        try:
            import json
            j = json.loads(raw)
            return j if isinstance(j, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


async def match_state(session, want_equip: bool = False) -> dict:
    """比赛主页状态（competition act=1）。只读。

    want_equip=True 会顺带拉 act=6（39KB 装备/属性包），供「装备详情」展示。
    """
    try:
        r1 = await session.post(EP_TRAVEL_MATCH, act=1, opuin=_plain_uin(session))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求比赛主页异常：{e}"}
    d = await _json_of(r1)
    if not d and (r1.get("code") not in (0, None)):
        return {"ok": False, "code": r1.get("code"),
                "message": r1.get("message") or "读取比赛主页失败"}
    out = {"ok": bool(d), "code": r1.get("code"),
           "message": r1.get("message") or ""}
    out.update(_pick(d))
    rows = d.get("travel_elf_arr") or []
    out["cats"] = _cats(rows)
    # 空态标记：只有 opuin 传对（见 _plain_uin）之后，仍返回空壳才可能是
    # 「未开通比赛模块」。此前误把 opuin 前导零导致的空壳当成未开通，已修正。
    out["bound"] = bool(rows) or "performe_num" in d or "clover_num" in d
    if want_equip:
        try:
            r6 = await session.post(EP_TRAVEL_MATCH, act=6)
            d6 = await _json_of(r6)
            if d6:
                out["equip"] = {k: d6.get(k) for k in (
                    "cat_home_chip_vt", "cat_home_race_vt_attr",
                    "cat_home_dress_clothing_attr", "cat_home_dress_hand_attr",
                    "cat_home_dress_head_attr", "cat_home_dress_other_attr")}
                out["equip_code"] = r6.get("code")
        except Exception as e:  # noqa: BLE001
            out["equip_err"] = str(e)
    return out


async def pvp_rank(session, rank_id: int = 0, top: int = 50) -> dict:
    """PVP 日榜（card_travel_elf_pvp act=14，rank_id=0）。只读。

    HAR 实测：list 共 1000 人，字段 uin/nick/score/ce(战力)/ts/getzan/zan。
    zan 非空的 ~10 人＝**今日可点赞对象**；zan/getzan 只为这 10 人返回。
    day_ts = 榜单所属日的 0:00（HAR 中为当天）。
    """
    try:
        r = await session.post(EP_TRAVEL_PVP, act=14, rank_id=rank_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求 PVP 榜异常：{e}"}
    d = await _json_of(r)
    rows = d.get("list")
    if not isinstance(rows, list):
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "读取 PVP 榜失败"}
    me = str(session.uin or "")
    my_rank = None
    for i, x in enumerate(rows, 1):
        if str(x.get("uin")) == me:
            my_rank = i
            break
    lite = []
    for x in rows[: max(0, int(top or 50))]:
        lite.append({
            "rank": None, "uin": x.get("uin"), "nick": x.get("nick"),
            "score": x.get("score"), "ce": x.get("ce"), "ts": x.get("ts"),
        })
    # 榜单里的 position 只是取了前 N 条的序号，补上真实名次
    for i in range(len(lite)):
        if my_rank and (i + 1) <= my_rank:
            lite[i]["rank"] = i + 1
    return {
        "ok": True, "code": r.get("code"),
        "day_ts": d.get("day_ts"),
        "total": len(rows),
        "list": lite,
        "my_rank": my_rank,
        "my_score": d.get("my_score"),
        "my_ce": d.get("my_ce"),
        "my_rankbits": d.get("my_rankbits"),
        "my_zan_num": d.get("my_zan_num"),
        "zan_bit": d.get("zan_bit"),
        "zan_targets": [x.get("uin") for x in rows if x.get("zan") is not None][:20],
        "rank_id": rank_id,
    }


async def union_info(session) -> dict:
    """工会信息（card_travel_elf_union act=1）。只读。

    HAR 实测返回 41 个字段；members 是 {uin: "等级,时间,昵称,贡献,…"} 的逗号串，
    各段含义未证实 → 原样透出，不做拆分命名。
    """
    try:
        r = await session.post(EP_TRAVEL_UNION, act=1)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求工会信息异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "读取工会信息失败"}
    keep = ("union_name", "unionid", "union_dec", "union_icon",
            "union_create_ts", "union_condition", "union_activity",
            "patriarch_name", "lv", "exp", "mem_num", "max_num",
            "coin", "chip", "bit", "sciencetimes", "dismiss_ts")
    out = {"ok": True, "code": r.get("code")}
    for k in keep:
        if k in d:
            out[k] = d[k]
    out["members"] = d.get("members") or {}
    return out


async def union_gvg(session) -> dict:
    """公会战（card_travel_elf_union act=22）。只读。

    HAR 实测返回 17 个字段，语义较明确的是等级/关卡/进度/敌方工会 + 送礼记录：
      level / inlv / e_levelid   己方等级、敌方等级（e_ 前缀＝敌方）
      progress / e_progress      己方与敌方总进度
      enemy_uinon / e_name       敌方工会 id 与名称
      level_rank_bit / levelgetbits  关卡领取位
      gift_record                 已领/已送记录（逗号串，含自己 uin → 今日已参与）
      season_gift / ts
    gvg_sign / gvg_cheer 在 competition act=1 里也有（0/1），含义与公会战相关。
    """
    try:
        r = await session.post(EP_TRAVEL_UNION, act=22)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求公会战异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "读取公会战失败"}
    return {"ok": True, "code": r.get("code"), "data": d}


async def sanguo_state(session, want_task: bool = False) -> dict:
    """西游三国（card_worldsanguo act=1，约 18KB）。只读。

    act=1 主界面：levelId / freenum(每日免费次数) / passId / talent_score /
    talent_rate / formation(12 条技能等级) / logData(86 条关卡记录，含
    bossid/levelid/percent_hp/passData) / travel_elf_arr(4 只精灵，与
    competition 同源) / img_items / ex_box_num / safe_box_num。
    want_task=True 会顺带拉 act=7（task_detail 每日任务）。
    act=9（data 56 条）暂未纳入，等抓包确认语义。
    """
    try:
        r1 = await session.post(EP_WORLD_SANGUO, act=1, opuin=_plain_uin(session))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求西游三国异常：{e}"}
    d = await _json_of(r1)
    if not d:
        return {"ok": False, "code": r1.get("code"),
                "message": r1.get("message") or "读取西游三国失败"}
    out = {"ok": True, "code": r1.get("code")}
    for k in ("levelId", "passId", "freenum", "buynum", "talent_score", "talent_rate",
              "levelData", "formation", "logData", "travel_elf_arr", "img_items",
              "ex_box_num", "safe_box_num", "new_box_num", "shoplist", "ispoup",
              "tick", "sjb", "nlb", "txd"):
        if k in d:
            out[k] = d[k]
    if want_task:
        try:
            r7 = await session.post(EP_WORLD_SANGUO, act=7, opuin=_plain_uin(session))
            d7 = await _json_of(r7)
            if d7:
                out["task_bits"] = d7.get("task_bits")
                out["task_detail"] = d7.get("task_detail")
        except Exception as e:  # noqa: BLE001
            out["task_err"] = str(e)
    return out


async def explore_state(session) -> dict:
    """世界探索（card_worldexplore act=1）。只读。

    HAR 实测：currLevelData(当前关卡 stepData) / logData / formationList(布阵,
    含 catid/ce/eqlist) / freepowerbit / luckydata / shoplist / 各类 box 数据。
    act=15 是每日任务（task_detail），act=22 返回 117 条 data。
    """
    try:
        r1 = await session.post(EP_WORLD_EXPLORE, act=1, opuin=_plain_uin(session))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求世界探索异常：{e}"}
    d = await _json_of(r1)
    if not d:
        return {"ok": False, "code": r1.get("code"),
                "message": r1.get("message") or "读取世界探索失败"}
    out = {"ok": True, "code": r1.get("code")}
    for k in ("levelId", "currLevelData", "logData", "formationList", "freepowerbit",
              "luckydata", "shoplist", "ex_box_num", "new_box_num", "safe_box_num",
              "maxLevelId", "nlb", "sjb", "txd", "guide", "newbiebit", "suitcard"):
        if k in d:
            out[k] = d[k]
    return out


async def gold_buy(session) -> dict:
    """金卡每日购买状态（competition act=20）。只读。

    HAR 实测返回 gold_buy_id1..6 + gold_buy_id_ts1..6：第 2 个（gold_buy_id2=5）
    今日已买了 5 张，其余为 0 —— 推断是**每日限购分组**，未完全证实。
    """
    try:
        r = await session.post(EP_TRAVEL_MATCH, act=20)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求金卡状态异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "读取金卡状态失败"}
    return {"ok": True, "code": r.get("code"), "data": d}


# ===================== 写操作（2026-09-26 bisai1.har 实证）=====================
# 此前 bisai.har 里没有写操作，所以整个模块只读；bisai1.har 补上了，语义如下：
#   competition act=11  {candy_type, elf_id} → {cat_mood, elfid, force0/1/2}  喂猫（心情 +1）
#   competition act=4   {catId, id, ids}     → {total_value, race_value, level_score,
#                                               theme_attr, content, performe_num(-1),
#                                               cat_star, cat_star_new, skill_use_bit}  打一场
# HAR 唯一样本：act=4 {catId:4, id:4, ids:'10663|20463|30122|40141'}（id 与 catId 同值）。


async def competition_feed(session, elf_id: int, candy_type: int = 1) -> dict:
    """喂猫（competition act=11）—— 把心情喂到阈值才能做其他任务。

    HAR 实证：act=11 + candy_type=1 + elf_id=<猫 id>，返回 cat_mood 上升
    （82→83 / 82→82 / 82→82 / 85 一档），并带 force0/1/2（推测为三条属性线，未证实）。
    """
    try:
        r = await session.post(EP_TRAVEL_MATCH, act=11,
                               elf_id=int(elf_id), candy_type=int(candy_type))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求喂食异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "喂食请求未返回数据"}
    return {"ok": True, "code": r.get("code"), "data": d}


async def competition_race(session, cat_id: int, equip_ids: list) -> dict:
    """打一场比赛（competition act=4）：catId=猫、ids=出战 4 件装备（'|' 连接）。

    params 里 HAR 的 id 与 catId 同值，这里照抄；ids 必须取**当前真实装备**
    （项目铁律：坐标/槽位类参数绝不用固定值）。
    """
    ids = "|".join(str(x) for x in equip_ids)
    try:
        r = await session.post(EP_TRAVEL_MATCH, act=4,
                               catId=int(cat_id), id=int(cat_id), ids=ids)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求比赛异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "比赛请求未返回数据"}
    if "theme_attr" in d:
        save_theme_attr(d.get("theme_attr"), d.get("theme_arr"))
    return {"ok": True, "code": r.get("code"), "data": d}


# ---------------- 主题适配：最优穿装建议（只读计算）----------------
# 依据：真机实打两次（2026-09-26）
#   ① cat4 当前装 → theme=[{4,48},{8,31},{9,11},{12,10}] race=[{1,500},{5,140},{9,234},{10,85}]
#      total=1054 level_score=60.23   （对 theme 覆盖 9/12，得分低）
#   ② bisai.har 那次（HAR 记录）→ theme=[{1,51},{5,11},{9,27},{10,11}] race=[{1,872},{2,106},{6,230},{10,185}]
#      total=1532 level_score=75.04   （对 theme 覆盖 1/10，得分高）
# 结论：得分与「装备属性命中 theme_attr 的种类/含量」强相关 —— 这就是"最适配比赛需求"。
# 注意：theme_attr 只有打过才返回，所以取上次实打缓存（last_theme_attr）。

import json as _json
import itertools as _itertools
from pathlib import Path as _Path

_THEME_CACHE = _Path("/tmp/mc_match_theme.json")
_DRESS_TABLES = (
    "cat_home_dress_head_attr", "cat_home_dress_hand_attr",
    "cat_home_dress_clothing_attr", "cat_home_dress_other_attr",
)


def last_theme_attr() -> dict:
    """上一次实打比赛拿到的 theme_attr（打完才有，故取缓存）。"""
    try:
        return _json.loads(_THEME_CACHE.read_text(encoding="utf-8")).get("theme_attr") or {}
    except Exception:  # noqa: BLE001
        return {}


def save_theme_attr(theme: list, theme_arr: list = None) -> None:
    """act=4 / act=23 成功后回写主题，供下次比赛前算建议。"""
    try:
        payload = {"theme_attr": theme or []}
        if theme_arr:
            payload["theme_arr"] = theme_arr
        cur = {}
        try:
            cur = _json.loads(_THEME_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        cur.update(payload)
        _THEME_CACHE.write_text(_json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _load_theme_arr() -> dict:
    """union act=23 返回的 12 项主题权重表（bisai2.har 实证：4/8/12 权重 14，其余 10）。

    这张表是「本场主题需求」的完整分布，比 competition act=4 的 theme_attr（只有 4 项加成量）
    更完整，优先用它算最优穿装。
    """
    try:
        rows = _json.loads(_THEME_CACHE.read_text(encoding="utf-8")).get("theme_arr") or []
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(rows, list):
        return {a.get("id"): a.get("val", a.get("value", 0)) for a in rows if isinstance(a, dict)}
    return rows or {}


def best_equip(equip: dict, theme=None) -> dict:
    # 接口返回的是 [{"id":4,"value":48}, ...]，函数内部按 {属性id: 要求值} 使用，
    # 两种形态都收下（2026-09-26 实测 500 就是这里只收 dict 导致的）。
    # theme 兼容三种来源：① list[{id,val}] ② dict{属性id: 权重} ③ 省略 → 取 12 项主题权重表
    # 12 项主题权重表（union act=23 的 theme_arr）比 competition act=4 的 4 项 theme_attr
    # 更完整，只要缓存里有就优先用它；theme_attr 仅作兜底（其 value 是「加成量」不是权重，
    # 直接当权重会让覆盖度虚高，2026-09-26 实测就是这个坑）。
    arr = _load_theme_arr()
    if arr:
        theme = arr
    elif theme is None:
        return {}
    elif isinstance(theme, list):
        theme = {a.get("id"): a.get("val", a.get("value", 0)) for a in theme if isinstance(a, dict)}
    if not theme:
        return {}
    """穷举「头/手/衣/各 一件」，给出对 theme 覆盖最大的组合（只读，不发请求）。

    评分 = 命中 theme 的属性含量 × 1000 + 全部属性总量（同分时取属性更多的组合）。
    """
    opts = []
    for key in _DRESS_TABLES:
        lst = equip.get(key) or []
        opts.append([(it.get("id"), {a.get("id"): a.get("value", 0) for a in (it.get("eq_attr") or [])})
                     for it in lst])
    if not all(opts) or len(opts) != 4:
        return {}
    best, best_score = None, -1
    for combo in _itertools.product(*opts):
        prov, tot = {}, 0
        for _eid, attrs in combo:
            for k, v in (attrs or {}).items():
                prov[k] = prov.get(k, 0) + v
            tot += sum((attrs or {}).values())
        # 加权覆盖：theme_arr 里权重 14 的属性（4/8/12）比权重 10 的更吃重
        cover = sum(prov.get(k, 0) * (theme.get(k, 0) / 10.0) for k in theme)
        score = cover * 1000 + tot
        if score > best_score:
            best_score, best = score, combo
    if best is None:
        return {}
    ids, attrs = [], {}
    for eid, c in best:
        ids.append(eid)
        for k, v in (c or {}).items():
            attrs[k] = attrs.get(k, 0) + v
    return {
        "equip_ids": ids,
        "cover": round(sum(attrs.get(k, 0) * (theme.get(k, 0) / 10.0) for k in theme), 1),
        "attrs": attrs,
        "theme_cover": {k: attrs.get(k, 0) for k in theme},
        "theme_src": "theme_arr" if _load_theme_arr() else "theme_attr",
    }


# ---------------- 换装（2026-09-26 bisai2.har 实证，补齐了此前缺失的一环）----------------
# union act=23  {cats:'3|4|2', eqs:'id|id|id|id|...'}  → battle_data
#     · **cats 有 N 项，eqs 就是 N×4 个装备 id，顺序按 cats 平铺**（cat3 拿前 4 个，cat4 接后 4 个…）
#     · 装备 id 首位即槽位：1=头 2=手 3=衣 4=各，所以合法组合天然是每槽各一件
#     · **不扣 performe_num**（HAR 全程 performe_num 恒为 2，#89→#92→#96 读回证实换装立即生效）
#     · 返回 battle_data：attack_accounts/total_score、attack_elf_list（每只猫 ce/equiparr/talentarr）、
#       theme_arr（12 项主题权重，4/8/12 权重 14，其余 10）
# union act=39  {cats:'0|0|0'}                        → elf_list[{id, equip_list}]
#     · cats 传 0 就是「读」当前阵容，用来校验 act=23 是否生效
# pvp act=3     {ids:'3,4,2'}（逗号分隔，不是 |）      → elf_list   PVP 出战阵容设置

async def union_lineup(session, cats: list) -> dict:
    """读当前出战阵容（union act=39）：cats 传全 0 即为只读。"""
    try:
        r = await session.post(EP_TRAVEL_UNION, act=39,
                               cats="|".join(str(int(c)) for c in cats))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求阵容异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "请求阵容未返回数据"}
    return {"ok": True, "code": r.get("code"), "data": d}


async def union_equip(session, cats: list, equip_flat: list) -> dict:
    """一键换装（union act=23）：cats=['3','4','2'] + equip_flat=12 个装备 id 平铺。

    HAR 实证 #90：cats='3|4|2', eqs='10493|20162|30203|40273|10663|20463|30383|40353|10353|20353|30122|40141'
    """
    if len(cats) != len(equip_flat) // 4:
        return {"ok": False, "message": f"装备数量应为猫数的 4 倍（当前 {len(cats)} 猫 / "
                                       f"{len(equip_flat)} 件）"}
    try:
        r = await session.post(EP_TRAVEL_UNION, act=23,
                               cats="|".join(str(int(c)) for c in cats),
                               eqs="|".join(str(int(x)) for x in equip_flat))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求换装异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "换装请求未返回数据"}
    return {"ok": True, "code": r.get("code"), "data": d}


async def pvp_lineup(session, cat_ids: list) -> dict:
    """设置 PVP 出战阵容（pvp act=3）：ids 用逗号分隔（不是 |），如 '3,4,2'。"""
    try:
        r = await session.post(EP_TRAVEL_PVP, act=3,
                               ids=",".join(str(int(c)) for c in cat_ids))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求 PVP 阵容异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "PVP 阵容请求未返回数据"}
    return {"ok": True, "code": r.get("code"), "data": d}
