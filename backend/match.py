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
        r1 = await session.post(EP_TRAVEL_MATCH, act=1, opuin=session.uin)
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
    # 实测（2026-09-25）：未玩过「猫咪旅行 / 比赛」的号 act=1 会返回空壳
    # （travel_elf_arr=[] 且没有 performe_num/clover_num），Unionid 仍会有值。
    # 不标出来前端会显示成「全部 0」，容易被误读成数据异常。
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
