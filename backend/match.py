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
import json
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


_RACE_LOG = "/tmp/mc_match_race.jsonl"  # 注意：本文件 _Path 在后面才 import


def save_race_log(entry: dict) -> None:
    """追加一条出战记录（jsonl）。失败不影响主流程。"""
    try:
        entry = dict(entry)
        entry.setdefault("ts", _time.strftime("%Y-%m-%d %H:%M:%S"))
        with open(_RACE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def load_race_log(limit: int = 20) -> list:
    """最近 limit 条出战记录（新的在前）。"""
    try:
        with open(_RACE_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(_json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return list(reversed(out))


async def competition_race(session, cat_id: int, equip_ids: list,
                           race_id: int | None = None) -> dict:
    """打一场比赛（competition act=4）：catId=猫、ids=出战 4 件装备（'|' 连接）。

    2026-09-26 真机实证（推翻此前「id=猫 id / ids 必须是当前穿着」的错误结论）：

      · **id = 本场比赛 id**，由 competition act=2 实时返回，每场都变
        （HAR 那天 4、上一场 3、今天 8）。**绝不写死，也不等于 catId**——
        HAR 里二者同为 4 纯属巧合，害我误判了一整天。
      · **ids 可以现场自由搭配**，不必是猫当前穿着的：bisai1.har 那次真实请求的
        4 件里只有 1 件与 cat4 当前装相同 → 每日比赛**没有独立穿装接口**，
        是前端搭配好直接带 ids 上台（用户 2026-09-26 指出，实打验证通过）。
      · 参数错误只回 code=-1 且**不扣** performe_num，可安全重试。
      · act=4 响应里的 theme_attr 是**下一场**的主题，不是本场
        （本场主题看 race_value 的属性 id，与赛前 act=2 读到的完全一致）。
    """
    _last_theme_rows = []
    if race_id is None:
        th = await competition_theme(session)
        if not th.get("ok") or th.get("race_id") is None:
            return {"ok": False,
                    "message": f"读本场比赛 id 失败：{th.get('message') or th.get('code')}"}
        race_id = th["race_id"]
        _last_theme_rows = th.get("theme") or []
    if not _last_theme_rows:
        try:
            _th = await competition_theme(session)
            _last_theme_rows = _th.get("theme") or []
        except Exception:  # noqa: BLE001
            pass
    ids = "|".join(str(x) for x in equip_ids)
    try:
        r = await session.post(EP_TRAVEL_MATCH, act=4,
                               catId=int(cat_id), id=int(race_id), ids=ids)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求比赛异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "比赛请求未返回数据"}
    # theme_attr 是「下一场」主题，只作参考缓存，不参与本场建议
    if "theme_attr" in d:
        save_theme_attr(d.get("theme_attr"), d.get("theme_arr"))
    d["race_id_used"] = int(race_id)
    d["equip_used"] = ids
    if d.get("code") == 0:
        save_race_log({
            "cat_id": int(cat_id),
            "equip_ids": [int(x) for x in equip_ids],
            "race_id": int(race_id),
            "theme": _last_theme_rows,
            "total_value": d.get("total_value"),
            "level_score": d.get("level_score"),
            "race_value": d.get("race_value"),
            "performe_num": d.get("performe_num"),
            "content": d.get("content"),
            "next_theme": d.get("theme_attr"),
        })
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
import time as _time
import itertools as _itertools
from pathlib import Path as _Path

_THEME_CACHE = _Path("/tmp/mc_match_theme.json")
_DRESS_TABLES = (
    "cat_home_dress_head_attr", "cat_home_dress_hand_attr",
    "cat_home_dress_clothing_attr", "cat_home_dress_other_attr",
)


# ---------------- 今日主题（competition act=2，2026-09-26 用户截图+实读双实证）----------------
# act=2 零参数、赛前可读、零副作用：attr=[{id,value}...] 合计=100，就是 UI「今日主题」的百分比。
# 重要：**每场比赛主题独立随机**（09-25 一天三场三个主题），所以必须在每次「出发」前重新读。
# 属性名实证（装备数值与游戏截图逐一匹配）：1=唱歌 2=舞蹈 3=朗诵 4=演奏；5~12 名字待补。
ATTR_NAMES = {1: "唱歌", 2: "舞蹈", 3: "朗诵", 4: "演奏"}


def attr_name(aid: int) -> str:
    return ATTR_NAMES.get(int(aid), f"属性{aid}")


async def competition_theme(session) -> dict:
    """读今日主题（competition act=2）。返回 {ok, theme:[{id,value,name}], raw_id}。"""
    try:
        r = await session.post(EP_TRAVEL_MATCH, act=2)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求主题异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "主题请求未返回数据"}
    rows = d.get("attr") or []
    theme = [{"id": a.get("id"), "value": a.get("value"),
              "name": attr_name(a.get("id"))} for a in rows if isinstance(a, dict)]
    # 实时主题顺手入缓存（lineup / 兜底逻辑可用）
    if theme:
        save_theme_attr(rows)
    return {"ok": True, "code": r.get("code"), "theme": theme,
            "race_id": d.get("id")}


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
    """穷举「头/手/衣/各 一件」，按今日主题百分比给出最优组合（只读，不发请求）。

    theme 权威来源 = competition act=2 的 attr（[{id,value}]，value 合计 100，
    即 UI「今日主题」的百分比，2026-09-26 实读与用户截图完全一致）。
    评分 = Σ(组合提供的属性k × 主题百分比k/100)，同分再比总属性量。
    传省略时兜底用上次比赛的 theme_attr 缓存。
    注意：union act=23 battle_data 里的 theme_arr（10/14 基准权重）是公会战的表，
    与每日比赛主题无关，不再优先使用（2026-09-26 修正上一版的错误优先级）。
    """
    if theme is None:
        theme = last_theme_attr()
    if isinstance(theme, list):
        theme = {a.get("id"): a.get("val", a.get("value", 0)) for a in theme if isinstance(a, dict)}
    if not theme:
        return {}
    opts = []
    for key in _DRESS_TABLES:
        lst = equip.get(key) or []
        opts.append([(it.get("id"), {a.get("id"): a.get("value", 0) for a in (it.get("eq_attr") or [])})
                     for it in lst])
    if not all(opts) or len(opts) != 4:
        return {}
    best, best_score, best_tot = None, -1.0, 0
    for combo in _itertools.product(*opts):
        prov, tot = {}, 0
        for _eid, attrs in combo:
            for k, v in (attrs or {}).items():
                prov[k] = prov.get(k, 0) + v
            tot += sum((attrs or {}).values())
        cover = sum(prov.get(k, 0) * (theme.get(k, 0) / 100.0) for k in theme)
        if cover > best_score or (cover == best_score and tot > best_tot):
            best_score, best_tot, best = cover, tot, combo
    if best is None:
        return {}
    ids, attrs = [], {}
    for eid, c in best:
        ids.append(eid)
        for k, v in (c or {}).items():
            attrs[k] = attrs.get(k, 0) + v
    return {
        "equip_ids": ids,
        "cover": round(best_score, 1),
        "attrs": attrs,
        "theme_cover": {str(k): attrs.get(k, 0) for k in theme},
        "theme_src": "act2_live" if theme else "cache",
    }


def current_cover(equip: dict, worn: list, theme: dict) -> dict:
    """某只猫当前穿着对主题的覆盖（用于「当前 vs 最优」对比）。"""
    idx = {}
    for key in _DRESS_TABLES:
        for it in (equip.get(key) or []):
            idx[it.get("id")] = {a.get("id"): a.get("value", 0) for a in (it.get("eq_attr") or [])}
    prov = {}
    for i in (worn or []):
        for k, v in (idx.get(int(i)) or {}).items():
            prov[k] = prov.get(k, 0) + v
    return round(sum(prov.get(k, 0) * (theme.get(k, 0) / 100.0) for k in theme), 1)


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


# ==================== 其余玩法（2026-09-26 bisai2.har 实证）====================
# 端点常量：EP_TRAVEL_PVP / EP_TRAVEL_UNION / EP_WORLD_SANGUO / EP_WORLD_EXPLORE
# act 语义全部来自 HAR 真实请求与响应，未臆造。


async def _act(session, ep: str, act: int, **kw) -> dict:
    """通用：发一个 act 请求，返回 {ok, code, data}。"""
    try:
        r = await session.post(ep, act=act, **kw)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{ep} act={act} 异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or f"act={act} 未返回数据"}
    return {"ok": True, "code": r.get("code"), "data": d}


# ---------- 每日比赛：英雄榜 / 积分 / 领奖 / 抽奖 ----------

async def comp_hero(session) -> dict:
    """英雄榜（competition act=9，dayindex=0）。读。"""
    return await _act(session, EP_TRAVEL_MATCH, 9, dayindex=0)


async def comp_score(session) -> dict:
    """比赛积分与次数（competition act=16）。读：score / free_num / signleTimes / multiTimes。"""
    return await _act(session, EP_TRAVEL_MATCH, 16)


async def comp_claim(session, cid: int) -> dict:
    """领奖（competition act=17 {id}）。HAR 实证：返回 content + limit_times。"""
    return await _act(session, EP_TRAVEL_MATCH, 17, id=int(cid))


async def comp_claim2(session, cid: int) -> dict:
    """领奖（competition act=21 {id}）。HAR：content=92_32_8。"""
    return await _act(session, EP_TRAVEL_MATCH, 21, id=int(cid))


async def comp_draw(session, num: int = 1, idx: int = 1) -> dict:
    """抽奖（competition act=18 {num, idx}）。**消耗积分**（score 697 回落），谨慎。"""
    return await _act(session, EP_TRAVEL_MATCH, 18, num=int(num), idx=int(idx))


async def comp_zan(session, opuin: str, rank_idx: int = 0) -> dict:
    """英雄榜点赞（competition act=22 {opuin, rank_idx}）。HAR：content=44_0_3034。"""
    return await _act(session, EP_TRAVEL_MATCH, 22, opuin=str(opuin), rank_idx=int(rank_idx))


# ---------- 竞技场 PVP ----------

async def pvp_state(session) -> dict:
    """PVP 主页（pvp act=1）。读：pvp_num / free_pvp_num / enemy_list / battle_arr / week_theme。"""
    return await _act(session, EP_TRAVEL_PVP, 1)


async def pvp_flush(session) -> dict:
    """刷新对手（pvp act=7）。"""
    return await _act(session, EP_TRAVEL_PVP, 7)


async def pvp_fight(session, euin: str) -> dict:
    """挑战（pvp act=8 {type:'fight', euin}）。**消耗 pvp_num**。"""
    return await _act(session, EP_TRAVEL_PVP, 8, type="fight", euin=str(euin))


async def pvp_claim(session, cid: int = 1) -> dict:
    """领奖（pvp act=15 {id}）。HAR：content=48_1514_10,92_62_1,92_58_1。"""
    return await _act(session, EP_TRAVEL_PVP, 15, id=int(cid))


async def pvp_store(session) -> dict:
    """PVP 商店（pvp act=17）。读：list 8 项 + store_end_ts。"""
    return await _act(session, EP_TRAVEL_PVP, 17)


async def pvp_peek(session, opuin: str) -> dict:
    """查看对方阵容（pvp act=2 {opuin}）。读。"""
    return await _act(session, EP_TRAVEL_PVP, 2, opuin=str(opuin))


async def pvp_zan(session, rank_uin: str, rank_idx: int = 0) -> dict:
    """PVP 点赞（pvp act=22 {rank_uin, rank_idx}）。HAR：content=92_9_130。"""
    return await _act(session, EP_TRAVEL_PVP, 22, rank_uin=str(rank_uin), rank_idx=int(rank_idx))


# ---------- 工会 / 公会战 ----------

async def union_task(session) -> dict:
    """工会任务状态（union act=15）。读：num / resetnum / bits。"""
    return await _act(session, EP_TRAVEL_UNION, 15)


async def union_chip(session) -> dict:
    """工会碎片/贡献（union act=16）。读：data=22315。"""
    return await _act(session, EP_TRAVEL_UNION, 16)


async def union_claim(session) -> dict:
    """工会领奖（union act=17）。HAR：content=92_32_10,48_1543_1。"""
    return await _act(session, EP_TRAVEL_UNION, 17)


async def union_fight(session, optype: int = 4, uptype: int = 1) -> dict:
    """公会战出兵（union act=21 {optype, uptype}）。**消耗出兵次数**。"""
    return await _act(session, EP_TRAVEL_UNION, 21, optype=int(optype), uptype=int(uptype))


async def union_enemies(session, level: int = 8) -> dict:
    """公会战关卡对手名单（union act=27 {level}）。读。"""
    return await _act(session, EP_TRAVEL_UNION, 27, level=int(level))


async def union_shop(session) -> dict:
    """工会兑换商店（union act=30）。读：data 6 项 [{consume,id,maxnum,type}]。"""
    return await _act(session, EP_TRAVEL_UNION, 30)


async def union_zan(session, uins: list) -> dict:
    """工会批量点赞（union act=14 {uins:'逗号分隔'}）。HAR 里调了 17 次，零消耗。"""
    return await _act(session, EP_TRAVEL_UNION, 14, uins=",".join(str(int(u)) for u in uins))


# ---------- 世界探索 ----------

async def explore_team(session) -> dict:
    """探索队伍（explore act=11）。读：teamlist。"""
    return await _act(session, EP_WORLD_EXPLORE, 11)


async def explore_task(session) -> dict:
    """探索任务（explore act=15）。读：task_detail。"""
    return await _act(session, EP_WORLD_EXPLORE, 15)


async def explore_log(session) -> dict:
    """探索记录（explore act=22）。读：data 117 条。"""
    return await _act(session, EP_WORLD_EXPLORE, 22)


async def explore_claim(session, cid: int = 1) -> dict:
    """探索领奖（explore act=25 {id}）。HAR：content=48_1604_18。"""
    return await _act(session, EP_WORLD_EXPLORE, 25, id=int(cid))


async def explore_shop(session) -> dict:
    """探索商店（explore act=26）。读：data 8 项。"""
    return await _act(session, EP_WORLD_EXPLORE, 26)


async def explore_rank(session) -> dict:
    """探索排行榜（explore act=28）。读：data 4 项。"""
    return await _act(session, EP_WORLD_EXPLORE, 28)


# ---------- 西游三国 ----------

async def sanguo_task(session) -> dict:
    """三国任务（sanguo act=7）。读：task_detail。"""
    return await _act(session, EP_WORLD_SANGUO, 7)


async def sanguo_list(session) -> dict:
    """三国关卡/记录（sanguo act=9）。读：data 56 项。"""
    return await _act(session, EP_WORLD_SANGUO, 9)


async def sanguo_claim(session, cid: int = 0) -> dict:
    """三国领奖（sanguo act=8 {id}）。HAR：content=48_1614_10,92_32_1,92_58_1。"""
    return await _act(session, EP_WORLD_SANGUO, 8, id=int(cid))


async def sanguo_lineup(session, levelid: int) -> dict:
    """三国阵容（sanguo act=16 {levelid, cats:'0|0|0|0'}）。读。"""
    return await _act(session, EP_WORLD_SANGUO, 16, levelid=int(levelid), cats="0|0|0|0")


async def sanguo_run(session, levelid: int, cats: list, eqs: list, mode: int = 0) -> dict:
    """三国闯关（sanguo act=11 {mode, levelid, eqs, cats}）。

    HAR 实证：eqs 是 N×4 个装备 id 平铺（同 union act=23），cats='3|4|2|1|'（**末尾带 |**）。
    **消耗闯关次数** —— 只在用户主动点时才调。
    """
    return await _act(session, EP_WORLD_SANGUO, 11, mode=int(mode), levelid=int(levelid),
                      eqs="|".join(str(int(x)) for x in eqs) + "|",
                      cats="|".join(str(int(c)) for c in cats) + "|")


# ---------------------------------------------------------------------------
# 各玩法「状态属性」字段表（2026-09-26）
# 每个玩法子块单独取自己玩法的字段：来源接口 -> 分组 -> (中文名, 原 key)
# 未在此列出的 key 会归进「其他原始字段」，不会丢服务端返回的字段。
# ---------------------------------------------------------------------------
MODE_PROP_SOURCES: dict = {
    "comp": ["score", "hero"],
    "pvp": ["state", "store"],
    "union": ["task", "chip", "shop", "enemies"],
    "explore": ["team", "task", "shop", "rank"],
    "sanguo": ["task", "levels", "lineup"],
}

# 少数来源的 data 结构特殊（不是「扁平字段表」），单独抽字段
MODE_PROP_EXTRACT: dict = {
    "union:enemies": lambda d: {"对手串": d.get("list"), "对手数": len((d.get("list") or "").split(",")) if d.get("list") else 0},
    "explore:team": lambda d: {"队伍数": len(d.get("teamlist") or []), "队伍": d.get("teamlist")},
    # 关卡/商店这类接口的 data 还套一层 {code,desc,data:[...]}，取出来才好看懂
    "sanguo:levels": lambda d: {"关卡表": d.get("data")},
}

# 这些来源要带参数（levelid / level），fetch 调用时单独处理
MODE_PROP_ARGS: dict = {
    "union:enemies": ("level", 8),
    "sanguo:lineup": ("levelid", 0),
}

MODE_PROP_GROUPS: dict = {
    "comp": [
        ("积分与次数", [("score", "积分"), ("free_num", "免费次数"), ("free_times", "今日剩余次数"),
                        ("signleTimes", "单场报名次数"), ("multiTimes", "多人报名次数"),
                        ("magic_diamond", "魔钻")]),
        ("排行与荣誉", [("my_top_score", "我的最高分"), ("my_rankbits", "我的排名位"),
                        ("my_top_eq_attr", "我的顶级装备属性"), ("zan_bit", "点赞位"),
                        ("day_ts", "榜单日期 ts")]),
    ],
    "pvp": [
        ("次数", [("pvp_num", "挑战次数"), ("free_pvp_num", "免费次数"), ("flush_num", "刷新次数"),
                  ("revenge_num", "复仇次数"), ("surplus", "剩余次数")]),
        ("战绩与奖励", [("score", "积分"), ("daily_task", "日常任务"), ("season_task", "赛季任务"),
                        ("rank_gift_bit", "段位礼包位"), ("season_rank_gift", "赛季礼包"),
                        ("record_red", "记录红点"), ("zan", "点赞数")]),
        ("阵容与主题", [("battle_arr", "出战配置"), ("max_lv", "最高等级"), ("week_theme", "本周主题")]),
    ],
    "union": [
        ("任务", [("num", "任务次数"), ("resetnum", "重置次数"), ("bits", "任务位"),
                  ("data", "任务数据")]),
        ("工会商店", [("bit", "商店位"), ("bit2", "商店位2"), ("data", "商品列表")]),
        ("关卡对手", [("对手串", "对手串"), ("对手数", "对手数")]),
    ],
    "explore": [
        ("任务", [("task_bits", "任务位"), ("task_detail", "任务明细")]),
        ("探索队伍", [("队伍数", "队伍数"), ("队伍", "队伍")]),
        ("商店与排行", [("data", "商店列表")]),
    ],
    "sanguo": [
        ("任务", [("task_bits", "任务位"), ("task_detail", "任务明细")]),
        ("关卡", [("关卡表", "关卡表"), ("bit", "关卡位")]),
        ("阵容", [("elf_list", "猫咪阵容")]),
    ],
}

# 分组里出现的非原始 key（抽取器造出来的），不参与「其他字段」比对
_EXTRA_KEYS = {"对手串", "对手数", "队伍数", "队伍", "关卡表", "商品列表", "关卡数"}


def _fmt_v(v) -> str:
    """把服务端值压成一行展示文本。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v if len(v) <= 90 else v[:90] + "…"
    try:
        s = json.dumps(v, ensure_ascii=False)
        return f"{len(v)} 项 · {s[:110]}" + ("…" if len(s) > 110 else "")
    except Exception:
        s = repr(v)
        return s if len(s) <= 110 else s[:110] + "…"


def _merge_src(mode: str, src_name: str, data) -> dict:
    """把一个来源的 data 摊平成 {原始key: 值}（跳过 code/desc 之类噪声）。

    data 可能是 dict（扁平字段表）也可能是 list（如三国关卡 56 项），
    list 一律以「来源名」作 key 保留，避免服务端字段被丢弃。
    """
    key = f"{mode}:{src_name}"
    if key in MODE_PROP_EXTRACT:
        try:
            return MODE_PROP_EXTRACT[key](data or {})
        except Exception:
            return {}
    if isinstance(data, list):
        return {src_name: data}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if k in ("code", "desc", "need_callback", "ok"):
            continue
        out[k] = v
    return out


async def mode_props(session, mode: str, fetch: dict, levelid: int = 0) -> dict:
    """某玩法的状态属性表（分组 + 中文名 + 原始值），供前端子块单独展示。

    **不额外发写操作**：只读各玩法已有的 act（comp 9/16、pvp 1/17、union 15/30/27、
    explore 11/15/26/28、sanguo 7/9/16），语义与 `-extra` 端点完全一致。
    fetch 由 main.py 注入（{f"{mode}:{src}": 协程}），避免这里依赖具体函数名。
    """
    if mode not in MODE_PROP_SOURCES:
        return {"ok": False, "message": f"未知玩法 {mode}"}
    merged: dict = {}
    for s in MODE_PROP_SOURCES[mode]:
        fn = (fetch or {}).get(f"{mode}:{s}")
        if not fn:
            continue
        try:
            # s 是短名，带玩法前缀才能对上 ARGS / EXTRACT 的键
            sk = f"{mode}:{s}"
            if sk in MODE_PROP_ARGS:
                _arg, default = MODE_PROP_ARGS[sk]
                data = (await fn(session, levelid or default) or {}).get("data")
            else:
                data = (await fn(session) or {}).get("data")
        except Exception as e:
            return {"ok": False, "message": f"{s} 读取失败：{type(e).__name__}: {e}"}
        merged.update(_merge_src(mode, s, data))

    groups, seen = [], set()
    for gname, rows in MODE_PROP_GROUPS.get(mode, []):
        out = []
        for raw_key, label in rows:
            if raw_key not in merged:   # _EXTRA_KEYS 只在「其他字段」里排除，别挡住分组展示
                continue
            txt = _fmt_v(merged[raw_key])
            if txt == "":
                continue
            out.append({"k": label, "key": raw_key, "v": txt})
            seen.add(raw_key)
        if out:
            groups.append({"name": gname, "rows": out})

    rest = []
    for k, v in merged.items():
        if k in seen or k in _EXTRA_KEYS:
            continue
        txt = _fmt_v(v)
        if txt == "":
            continue
        rest.append({"k": k, "key": k, "v": txt})
    if rest:
        groups.append({"name": "其他原始字段", "rows": rest})

    total = sum(len(g["rows"]) for g in groups)
    return {"ok": True, "mode": mode, "groups": groups, "total": total}
