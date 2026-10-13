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

本模块**以只读聚合为主**：不猜 act 语义、不构造 HAR 里没有暴露的写操作（比赛侧
只有进页面 + 看装备 + 买金卡，没有「打一场比赛 / 提交成绩」的 act）。
字段语义一律以抓包里的原始 key 为准；含义不确定的**原样透出**，不做中文硬译
（避免把推测写成常量，见项目 MEMORY.md 铁律）。

**工会 / 公会战例外**：工会玩法需要真实可执行（2026-09-27 由用户要求「把社团公会
玩法做了」）。union 端点 act 语义 2026-09-29 经全部抓包逐条复核修正：
act=1 工会信息（读）/ act=14 按 uin 批量查公会归属（**只读，游戏里没有「全员点赞」**）/
act=15 任务计数（读）/ act=16 贡献（读）/ act=17 领奖（写，零消耗）/ act=19 抢红包列表（读，
**「抢」的写接口未抓到，不盲试**）/ act=21 摇水果（写，返 coin/exp，零消耗，不带 cats）/
act=22 公会战信息（读）/ act=23 公会战出战（写，{cats,eqs}→battle_data.iswin，**耗 3/3**）/
act=25 候选对手列表（读）/ act=27 对手名单（读）/ act=30 商店（读）/ act=39 读阵容（读）。
游戏里**没有「出兵」「全员点赞」这两个独立接口**（用户 09-29 明确纠正，此前误标已全部撤）。
"""
import json
from typing import Optional

from .cat_dress import (  # 装扮名 / 属性名（官方 catHome_Info 配置实证，见 cat_dress 模块 doc）
    attr_name as _attr_name, equip_name as _equip_name, summary as _equip_summary,
    slot_of as _equip_slot, equip_attrs as _equip_attrs,
)
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
            # 装扮中文名，与 equip_arr 一一对应（进阶款官方表无收录，
            # 会是空串，前端退回显示 id）
            "equip_names": [_equip_name(x) for x in (c.get("equip_arr") or [])
                            if x and str(x).strip() not in ("", "0")],
            # 官方名表没收录的进阶款，退回槽位名（头/手/衣/其他），界面上不留数字 id
            "equip_slots": [_equip_slot(x) for x in (c.get("equip_arr") or [])
                            if x and str(x).strip() not in ("", "0")],
            "skills": [
                # 实证（bisai2.har skill_data 完整四字段）：cat_skill_type=强化的才艺
                # （6=翻滚/8=游泳/1=唱歌/4=演奏/9=强健/10=艳丽，与 1..12 才艺同一编号
                # 空间，同一槽位不同猫 type 不同）；attribute=该猫此才艺当前属性值。
                {"level": s.get("cat_skill_level"), "type": s.get("cat_skill_type"),
                 "name": _attr_name(s.get("cat_skill_type")) if s.get("cat_skill_type") else "",
                 "value": s.get("cat_skill_attribute")}
                for s in (c.get("skill_data") or [])
            ],
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
                # 每只猫的可读穿装（名字 + 属性加成；名字表缺的件也带属性数值）
                for c in out["cats"]:
                    c["equip_texts"] = [
                        _fmt_eq_item(x, out["equip"])
                        for x in (c.get("equip") or [])
                        if x and str(x).strip() not in ("", "0")]
        except Exception as e:  # noqa: BLE001
            out["equip_err"] = str(e)
    return out


async def dress_info(session) -> dict:
    """衣橱全量（competition act=6，只读）。

    返回 {ok, data:{四张 cat_home_dress_*_attr 表}}（与 mode_props fetch 约定一致，
    取数层统一 .get("data")）；data 就是游戏「猫咪衣柜」的完整数据
    （bisai2.har 实证 30+31+30+36=127 件，每件 eq_attr=真实加成值）。
    供「我的顶级装备属性」等字段做可读化：名字表缺的 48 件也能显示属性数值。
    """
    try:
        r6 = await session.post(EP_TRAVEL_MATCH, act=6)
        d6 = await _json_of(r6)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求衣橱异常：{e}"}
    if not d6:
        return {"ok": False, "message": "衣橱返回空壳"}
    return {"ok": True,
            "data": {k: d6.get(k) for k in (
                "cat_home_dress_head_attr", "cat_home_dress_clothing_attr",
                "cat_home_dress_hand_attr", "cat_home_dress_other_attr")}}


async def pvp_rank(session, rank_id: int = 0, top: int = 100) -> dict:
    """PVP 竞技榜（card_travel_elf_pvp act=14，rank_id=0）。只读。

    2026-09-28 真机实测：list 共 1000 人，每行只有
    uin/nick/score/ce(战力)/ts/getzan/zan ——
      · **服务端不返回名次**，数组顺序也不是按 score 排的（前 12 条
        score＝2257/2969/2287/2407…完全乱序），而游戏客户端显示的榜单是
        按 score 降序的名次 → 这里在展示层按 score 降序后自行编号。
      · **zan 非空的约 10 人＝今日可点赞对象**（实测下标 18/85/171/211/278/
        400/543/683/703/797），且全部在第 50 名之外 —— 只取前 50 条会一条
        可赞的都看不到（这正是「和游戏里的列表对不上」的直接原因）。
    day_ts = 榜单所属日的 0:00。
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
    # ⚠️ 榜里存的 uin 是纯数字，session.uin 是补零格式（"0004821992"），
    #    直接字符串比对永远匹配不上（my_rank 恒为 None）
    me = _plain_uin(session)
    my_rank = None
    for i, x in enumerate(rows, 1):
        if str(x.get("uin")) == me:
            my_rank = i
            break
    ordered = sorted(rows, key=lambda x: int(x.get("score") or 0), reverse=True)
    lite = [{
        "rank": i, "uin": x.get("uin"), "nick": x.get("nick"),
        "score": x.get("score"), "ce": x.get("ce"), "ts": x.get("ts"),
        "zan": x.get("zan"),
    } for i, x in enumerate(ordered[: max(0, int(top or 100))], 1)]
    # 可赞对象明细（游戏「点赞」页看到的就是这批），带上榜内名次/昵称便于对照
    zan_targets = [{
        "rank": i, "uin": x.get("uin"), "nick": x.get("nick"),
        "score": x.get("score"), "ce": x.get("ce"),
    } for i, x in enumerate(ordered, 1) if x.get("zan") is not None]
    scores = [int(x.get("score") or 0) for x in rows]
    return {
        "ok": True, "code": r.get("code"),
        "day_ts": d.get("day_ts"),
        "total": len(rows),
        "list": lite,
        "rank_id": rank_id,
        "my_rank": my_rank,            # None = 我的分数没进这份 1000 人名单
        "my_score": d.get("my_score"),
        "my_ce": d.get("my_ce"),
        "my_rankbits": d.get("my_rankbits"),
        "my_zan_num": d.get("my_zan_num"),
        "zan_bit": d.get("zan_bit"),
        "zan_targets": zan_targets[:30],
        "min_score": min(scores) if scores else None,   # 榜内最低分
        "max_score": max(scores) if scores else None,
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
    """三国战役（card_worldsanguo act=1，约 18KB）。只读。

    与 explore_state（西游记 / card_worldexplore）是**两个独立玩法**，不要合并显示。
    这里 levelId 是**关卡号**（西游同字段是 maxLevelId 上限）。

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
        return {"ok": False, "message": f"请求三国战役异常：{e}"}
    d = await _json_of(r1)
    if not d:
        return {"ok": False, "code": r1.get("code"),
                "message": r1.get("message") or "读取三国战役失败"}
    out = {"ok": True, "code": r1.get("code")}
    for k in ("levelId", "passId", "freenum", "buynum", "talent_score", "talent_rate",
              "levelData", "formation", "logData", "travel_elf_arr", "img_items",
              "ex_box_num", "safe_box_num", "new_box_num", "shoplist", "ispoup",
              # 关卡卡箱内容（2026-09-28 补）：ex_box_data 15 位 = 当前大关攒的层卡，
              # safe_box_data 12 位 = 保险箱。只有 act=5 的响应也带这两项，但 act=1
              # 同一次返回里就有，省一次请求。
              "ex_box_data", "safe_box_data",
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


async def explore_state(session, want_task: bool = False) -> dict:
    """西游记大地图（card_worldexplore act=1）。只读。

    ⚠️ 命名纠偏（2026-09-27）：本端点 = **西游记**（XiYouModule，小地图＝套卡
    1604..1611），与下面的 `sanguo_state` = **三国战役**（SanGuoModule，套卡
    1745..1756）是**两个完全独立的玩法**，共用同族接口结构但端点和关卡体系不同，
    之前前端把两者合称「西游三国」是错的。

    act=1 只读：currLevelData(当前关卡 stepData) / logData / formationList(布阵,
    含 catid/ce/eqlist) / freepowerbit / luckydata / shoplist / 各类 box / 材料
    (nlb/sjb/txd) / maxLevelId。
    want_task=True 会顺带拉 act=15（task_detail 每日任务）。
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
    if want_task:
        try:
            t = await explore_task(session)
            dd = t.get("data") or {}
            if isinstance(dd, dict):
                out["task_bits"] = dd.get("task_bits")
                out["task_detail"] = dd.get("task_detail")
        except Exception as e:  # noqa: BLE001
            out["task_err"] = str(e)
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


# 用猫的一键操作前置阈值（与 cathome.MOOD_TARGET 同一口径，改动处同步）
MOOD_TARGET = 80


async def ensure_mood(session, threshold: int = MOOD_TARGET) -> dict:
    """【用猫的一键操作前置】把心情不足 threshold 的猫喂到 threshold（act=11）。

    用户 2026-09-27 定调：「只要用到猫的一键操作，都要先把心情补到 80，
    不只限于日常界面」。因此所有**会用到猫**的写操作（每日比赛上台、竞技
    挑战、公会战出兵、三国闯关…）在执行前都先走这里，不用每个前端按钮各写一遍。

    实现复用 cathome.feed_smart（那里是 act=11 编排的源头）。
    cathome 已在本模块顶层 import 了 _act，故这里**函数内 import** 避免循环依赖。

    返回：{"ok": True, "steps": [...], "fed": bool}；某只猫喂失败即 {"ok": False, ...}。
    """
    from .cathome import feed_smart          # 函数内 import：避开 cathome → match 的循环
    return await feed_smart(session, threshold)


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
                           race_id: int | None = None,
                           cat_level: int | None = None,
                           feed: bool = True,
                           dress: dict | None = None) -> dict:
    """打一场比赛（competition act=4）：catId=猫、ids=出战 4 件装备（'|' 连接）。

    feed=True（默认）：出战前先把心情补到 MOOD_TARGET —— 心情不够游戏里上不了台
    （act=11 的注释即「把心情喂到阈值才能做其他任务」），且一键比赛本身也要求
    心情达标才选得到猫。调用方若已在前面统一喂过，传 feed=False 跳过。

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
    # 心情门槛：用猫的一键操作前置（用户 2026-09-27 定调），单场出战同样先补到 80
    if feed:
        f = await ensure_mood(session)
        if not f.get("ok"):
            return {"ok": False, "code": -1,
                    "message": f"喂猫到 {MOOD_TARGET} 失败，已中止出战（不消耗次数）："
                               f"{f.get('message') or '糖果可能不足'}"}
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
    # 可读穿装（名字 / 属性加成 / 是否命中今日主题），供日志与出战记录展示。
    # dress = act=6 四张装扮表；不给也能出战，只是文本里没有加成数值。
    theme_map = {a.get("id"): a.get("value", 0) for a in (_last_theme_rows or [])}
    d["equip_text"] = equip_summary(equip_ids, dress, theme_map)
    if d.get("code") == 0:
        save_race_log({
            "cat_id": int(cat_id),
            "cat_level": cat_level,
            "equip_ids": [int(x) for x in equip_ids],
            "equip_text": d["equip_text"],
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


def attr_name(aid: int) -> str:
    """属性 id → 中文名（1=唱歌 … 12=灵活，见 cat_dress 的实证链）。"""
    return _attr_name(aid) or f"属性{aid}"


def equip_label(equip_id) -> str:
    """装备 id → 「槽位:名字」；官方名表未收录时返回「#id」。"""
    return f"{cat_dress_slot(equip_id)}:{_equip_name(equip_id)}" if _equip_name(equip_id) \
        else f"#{equip_id}"


def cat_dress_slot(equip_id) -> str:
    from .cat_dress import slot_of
    return slot_of(equip_id)


def equip_summary(equip_ids, dress: dict = None, theme: dict | None = None) -> str:
    """穿装可读文本（名字 + 属性加成 + 命中主题），日志与出战记录共用。"""
    return _equip_summary(equip_ids, dress, theme)


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
# 属性名不再写死：全 12 个名字由 cat_dress 从官方 catHome_Info 配置实证得出
# （entry 属性 id ↔ desc 才艺名投票，2026-09-27）：
#   1=唱歌 2=舞蹈 3=朗诵 4=演奏 5=跑动 6=翻滚 7=跳跃 8=游泳 9=强健 10=艳丽 11=优雅 12=灵活


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
        "labels": [equip_label(i) for i in ids],     # 「头:头巾」「#10493」
        "text": equip_summary(ids, equip, theme),    # 给用户看的一行穿装说明
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


async def run_match_all(session, max_races: int = 0, stop_mood: int = 80):
    """一键比赛（async generator，逐场 yield 便于前端实时显示）。

    步骤全部走真实接口、参数均按 HAR/真机实证：
      1. competition act=2   读今日主题 + 本场 id（每场随机，必须打之前现读）
      2. competition act=6   读衣橱（best_equip 的输入）
      3. competition act=1   读猫列表 + performe_num（今日剩余次数）
      4. 循环 competition act=4 上台（每轮重算最优穿装、重读 race_id）
      5. 收工回读 act=1 拿最终状态

    **会消耗 performe_num**，由前端 confirm 后才调用；max_races 可限制场次。
    每完成一步 yield 一条（feed / race / error），末尾 yield __done__ 带汇总。
    """
    # 心情门槛（用户 2026-09-27 定调）：一键比赛要用猫，出战前先把 4 只猫喂到 80，
    # 否则 stop_mood 过滤会把猫全筛掉、或上台直接被拒。喂失败即中止（不扣次数）。
    feed = await ensure_mood(session, stop_mood)
    if not feed.get("ok"):
        yield {"act": "__error__", "ok": False,
               "message": f"喂猫到 {stop_mood} 失败，已中止一键比赛（不消耗次数）："
                          f"{feed.get('message') or '糖果可能不足'}"}
        return
    fed_n = len([s for s in (feed.get("steps") or []) if s.get("candy")])
    yield {"act": "feed", "ok": True, "candies": fed_n}

    try:
        th = await competition_theme(session)
    except Exception as e:  # noqa: BLE001
        yield {"act": "__error__", "ok": False, "message": f"读主题异常：{e}"}
        return
    if not th.get("ok") or th.get("race_id") is None:
        yield {"act": "__error__", "ok": False, "message": f"读本场比赛 id 失败："
              f"{th.get('message') or th.get('code')}"}
        return
    theme = {a["id"]: a["value"] for a in (th.get("theme") or [])}

    try:
        st = await match_state(session, want_equip=True)
    except Exception as e:  # noqa: BLE001
        yield {"act": "__error__", "ok": False, "message": f"读比赛状态异常：{e}"}
        return
    if not st.get("ok"):
        yield {"act": "__error__", "ok": False, "message": st.get("message") or "读比赛状态失败"}
        return

    equip = st.get("equip") or {}
    cats = st.get("cats") or []
    left = st.get("performe_num")
    try:
        left = int(left) if left is not None else 0
    except (TypeError, ValueError):
        left = 0
    total0 = left

    ready = [c for c in cats
             if len([x for x in (c.get("equip") or []) if x]) == 4]
    if not ready:
        yield {"act": "__error__", "ok": False, "message": "没有满 4 件装备的猫，无法出战（不消耗次数）",
              "left": total0}
        return

    races, theme_rows = [], (th.get("theme") or [])
    race_id = int(th["race_id"])
    n = 0
    while left > 0 and (max_races <= 0 or n < max_races):
        # 2026-09-26 修：每场都必须重读 act=2 拿**本场**的 race_id。
        th_next = await competition_theme(session)
        if th_next.get("ok") and th_next.get("race_id") is not None:
            race_id = int(th_next["race_id"])
            theme = {a["id"]: a["value"] for a in (th_next.get("theme") or [])}
            theme_rows = th_next.get("theme") or []
        sug = best_equip(equip, theme) if theme else {}
        ids = (sug.get("equip_ids") or [])
        if len(ids) != 4:
            yield {"act": "race", "no": n + 1, "err": "算不出 4 件最优装，停止"}
            break
        pool = sorted(ready, key=lambda c: -(c.get("level") or 0))
        pool = [c for c in pool
                if (c.get("mood") or 0) >= stop_mood] or pool
        cat = pool[0]
        # 心情已在函数开头统一喂过，这里别再重复喂（省 act=1 读 + 糖果）
        r = await competition_race(session, int(cat["elf_id"]), ids,
                                   race_id=race_id,
                                   cat_level=int(cat.get("level") or 0),
                                   feed=False,
                                   dress=equip)
        if not r.get("ok"):
            yield {"act": "race", "no": n + 1, "err": r.get("message") or "请求异常"}
            break
        d = r.get("data") or {}
        if d.get("code") != 0:
            yield {"act": "race", "no": n + 1, "cat_id": cat.get("elf_id"),
                  "code": d.get("code"),
                  "err": d.get("desc") or d.get("message") or "失败",
                  "total_value": d.get("total_value")}
            break
        left = d.get("performe_num")
        try:
            left = int(left) if left is not None else 0
        except (TypeError, ValueError):
            left = 0
        n += 1
        row = {
            "cat_id": cat.get("elf_id"),
            "cat_level": cat.get("level"),
            "equip": ids,
            "equip_text": d.get("equip_text") or sug.get("text"),
            "cover": sug.get("cover"),
            "total_value": d.get("total_value"),
            "level_score": d.get("level_score"),
            "content": d.get("content"),
            "left": left,
            "theme": theme_rows,
        }
        races.append(row)
        yield {"act": "race", "no": n, **row}

    try:
        after = await match_state(session)
    except Exception:  # noqa: BLE001
        after = {}
    yield {"act": "__done__", "ok": True, "ran": n, "left_before": total0, "left_after": left,
          "races": races, "theme": theme_rows,
          "cats": (after.get("cats") if after.get("cats") else cats),
          "feed": {"ok": True, "candies": fed_n}}


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


async def pvp_fight(session, euin: str, feed: bool = True) -> dict:
    """挑战（pvp act=8 {type:'fight', euin}）。**消耗 pvp_num**。

    打的是猫（pvp act=8 用猫当前穿着，见 cathome.arena_auto 注释），所以按用户
    定调「用到猫的一键操作先补心情」：执行前先把猫喂到 MOOD_TARGET。
    """
    if feed:
        f = await ensure_mood(session)
        if not f.get("ok"):
            msg = f"喂猫到 {MOOD_TARGET} 失败，已中止挑战：{f.get('message') or '糖果可能不足'}"
            return {"ok": False, "code": -1, "desc": msg, "message": msg}
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
    """工会任务/出兵计数（union act=15）。读：num / resetnum / bits。

    num 口径**存疑**（2026-09-28 修正）：同日出兵前 0、出兵后 1 曾实证；但 09-28
    num=3 而当天自动出兵仅 1 次（union_run 落盘），说明它可能跨日累计、也可能含
    手动点击的出兵 —— **不能当「今日已出兵次数」用**，界面已改为只显示
    「出兵计数 N（非游戏内挑战 3/3）」。
    `resetnum` 恒 3、`bits` 恒 0。
    """
    return await _act(session, EP_TRAVEL_UNION, 15)


async def union_chip(session) -> dict:
    """工会贡献/碎片/资金（union act=1，只读）。

    ⚠️ 历史误标修复（2026-10-03 查 bisai2.har 实锤）：旧版调 act=16，但 act=16 真实返回
    的是**摇水果盘 5 位码**（22315/22224/55514/…每次不同=重摇），并非贡献；且每次调用会
    **副作用重摇水果盘**。真正贡献/资金/成员在 union act=1（union_info）里（chip/coin/
    mem_num）。故改调 union_info 取真值，消除「刷新工会页悄悄重摇水果盘」的副作用。
    act=16 现仅由 union_fruit_reset（摇水果重置）持有，编号撞车解除。
    """
    info = await union_info(session)
    if not info.get("ok"):
        return info
    return {"ok": True, "code": info.get("code"),
            "data": info.get("chip"), "chip": info.get("chip"),
            "coin": info.get("coin")}


async def union_fruit_reset(session) -> dict:
    """摇水果——重置（union act=16）。用户 09-29 口述机制 + bisai2.har 时间线佐证。

    机制（用户定调）：摇水果是 5 个水果，**每次摇之前可以重置一次**，重置有机会把
    3 个相同刷成 4 个相同、奖励提升。bisai2 里 act=16 与 act=17 严格交替
    （16,16,17 ×3 组），act=16 每次返回不同的 5 位数（22315/22224/55514/55542/
    22145/22443）——按 5 位=5 个水果、每位 1~5 解读（**语义未证实，仅如实显示**），
    22224=四个 2、55514=三个 5；第一次摇前 3 连刷成 4 连，那次奖励串也最大。
    act=16 当天 6 连发均成功 → 至少 6 次/日可行；流程固定每摇只重置 1 次
    （用户口述「一次重置」），不循环刷（次数上限未证实，不盲试）。
    """
    r = await _act(session, EP_TRAVEL_UNION, 16)
    d = (r.get("data") or {}) if r.get("ok") else {}
    raw = str(d.get("data") or "")
    fruits = "".join(ch for ch in raw if ch.isdigit())[:5]
    max_same = 0
    if len(fruits) == 5:
        max_same = max(fruits.count(c) for c in set(fruits))
    pretty = "·".join(fruits) if fruits else raw
    grade = {5: "五连！", 4: "四连", 3: "三连"}.get(max_same, "")
    text = f"重置完成：水果 {pretty}" + (f"（{grade}）" if grade else "")
    return {"ok": bool(r.get("ok")), "code": r.get("code"),
            "fruits": fruits or raw, "max_same": max_same,
            "text": text if r.get("ok") else (r.get("message") or "重置失败")}


async def union_shake_fruit(session, reset_first: bool = True) -> dict:
    """摇水果（union act=17，**每日 3 次**）—— 2026-09-29 由 union_run.json 落盘实锤。

    此前误标「工会领奖」。实锤链：09-29 12:32/12:33/12:34 三次 act=17 连续成功
    （奖励：纤维×3、红宝石×6、纤维×3——随机果实/材料），13:38 第 4 次 → 服务端
    `system busy` 拒绝；union act=15 的 num/resetnum 当日 = 3/3。与用户实证
    「游戏里摇水果每天 3 次、点完次数 -1」完全吻合。
    bisai2.har 里 act=17 恰好 3 连发、每次返回随机奖励串（92_32_10,48_1543_1）。
    奖励串按项目铁律统一走 prop_dict，不在别处裸译。
    ⚠ 本地硬闸 SHAKE_DAILY_LIMIT=3（服务端也会拒第 4 次，双保险）。
    `reset_first=True`（默认）：摇之前先重置一次（act=16，用户定调「每次摇水果前
    还有一次重置，有机会把 3 连刷成 4 连拉高奖励」），重置失败不阻断摇。
    """
    q = _union_quota_check("shake", SHAKE_DAILY_LIMIT)
    if q.get("blocked"):
        return q
    rst = await union_fruit_reset(session) if reset_first else None
    r = await _act(session, EP_TRAVEL_UNION, 17)
    if r.get("ok"):
        try:
            from .prop_dict import summarize_reward
            r["text"] = summarize_reward(str((r.get("data") or {}).get("content") or ""))
        except Exception:  # 取不到就留原始串，不阻断
            pass
        used = _union_quota_bump("shake").get("shake")
        r["used"] = used
        r["limit"] = SHAKE_DAILY_LIMIT
        r["fruits"] = (rst or {}).get("fruits")
        r["max_same"] = (rst or {}).get("max_same")
        head = f"{(rst or {}).get('text', '')} → " if rst else ""
        r["text"] = f"{head}摇水果成功（{r.get('text') or (r.get('data') or {}).get('content') or ''}，今日 {used}/{SHAKE_DAILY_LIMIT}）"
    else:
        r.setdefault("message", "摇水果失败（可能今日 3 次已用完）")
    return r


async def run_union_shake(session) -> dict:
    """一键工会内「摇水果」：循环打满每日次数（SHAKE_DAILY_LIMIT=3）。

    用户 10-03 重新定调机制（覆盖旧 bisai2 解读）：
      - 每一轮 = **先重置(act=16) 再开始(act=17)**，严格 reset→start，
        不存在 start→reset→start。3 轮 ×（1 重置 + 1 开始）= **6 次接口**
        （never 5-same 时）。
      - 若某一轮重置后已是「5 个相同（五连）」，则下一轮**跳过重置、直接开始**
        （重置只会把五连重新随机化、得不偿失）：即「已经是 5 个一样的就不用重置了」。
      - 开始(act=17) 受每日 3 次硬闸（本地 SHAKE_DAILY_LIMIT + 服务端 busy 双保险）；
        重置(act=16) 不限次（旧注释：当日 6 连发均成功）。
      - 今日已开始次数已用满（本地配额 blocked）则直接收尾，不再白烧重置。

    返回 rounds 明细，每轮含 reset_ok/reset_fruits/reset_skipped/start_ok/start_used。
    """
    rounds = []
    last_max = 0  # 上一轮重置后的最大相同数（5 连则跳过本轮重置）
    for i in range(SHAKE_DAILY_LIMIT):
        q = _union_quota_check("shake", SHAKE_DAILY_LIMIT)
        if q.get("blocked"):
            rounds.append({"round": i + 1, "reset_ok": None, "reset_fruits": None,
                           "reset_skipped": None, "start_ok": False,
                           "start_used": q.get("used"),
                           "msg": q.get("message") or "今日已开始次数已用完"})
            break  # 今日已开始次数已用完，不重置（避免白烧）
        # 5 连则跳过重置，直接开始
        if last_max >= 5:
            rst = None
        else:
            rst = await union_fruit_reset(session)
            last_max = rst.get("max_same", 0)
        s = await union_shake_fruit(session, reset_first=False)
        rounds.append({
            "round": i + 1,
            "reset_ok": (rst or {}).get("ok"),
            "reset_fruits": (rst or {}).get("fruits"),
            "reset_skipped": last_max >= 5,
            "start_ok": s.get("ok"),
            "start_used": s.get("used"),
            "msg": ((rst or {}).get("text", "") + " → " + (s.get("text") or s.get("message") or "")).strip(" →"),
        })
        if s.get("blocked") or not s.get("ok"):
            break
    return {"ok": any(r.get("start_ok") for r in rounds), "rounds": rounds,
            "count": len(rounds)}


async def union_exchange_redstone(session, optype: int = 4, uptype: int = 1) -> dict:
    """工会商店兑换（union act=21 {optype, uptype}）—— **每日 1 次策略闸**。

    2026-09-29 用户澄清真实机制：act=21 是**商店兑换**（对应 act=30 商店列表，
    花金币买商品如红包石）。商店**每周刷新 1 次、每件 maxnum=1，没有日限额**——
    这就是服务端从不拒绝的原因（周库存内随便兑）。每天只兑 1 次是**人为策略**：
    每日任务要求「商店兑换 1 次」，兑 1 次拿任务奖励即可，多兑纯属提前烧周库存。
    故本地硬闸 EXCHANGE_DAILY_LIMIT=1：当日已兑就不再发请求（只统计本工具，
    游戏内手动兑感知不到）。响应返 coin(工会资金+2/+5)/exp —— 兑换副产品展示，
    uptype 与商店商品 id 的对应关系语义未证实。
    """
    q = _union_quota_check("exchange", EXCHANGE_DAILY_LIMIT)
    if q.get("blocked"):
        return q
    r = await _act(session, EP_TRAVEL_UNION, 21, optype=int(optype), uptype=int(uptype))
    d = (r.get("data") or {}) if r.get("ok") else {}
    if r.get("ok"):
        used = _union_quota_bump("exchange").get("exchange")
        return {"ok": True, "code": 0, "used": used, "limit": EXCHANGE_DAILY_LIMIT,
                "coin": d.get("coin"), "exp": d.get("exp"),
                "text": f"商店兑换成功（工会资金 → {d.get('coin')}，今日 {used}/{EXCHANGE_DAILY_LIMIT}，本周库存省着用）"}
    return {"ok": False, "code": r.get("code"),
            "message": (r.get("message") or "商店兑换失败")}


async def union_enemy_list(session, level: int = None) -> dict:
    """公会战候选对手名单（union act=25 {level}）—— **只读**，不是写接口。

    2026-09-29 实证（shetuan.har）：REQ {level:8} → code:0 返回候选敌方公会 list
    （形如 `["10012_沧海月明_ts", ...]`）。它只是「取对手列表」，不消耗 3/3 次数，
    也不发起战斗。真正的出战写接口是 union_battle（act=23）。
    level 不传则从 act=22（union_gvg）实时读 `level` 字段（当前进行层）。
    """
    if level is None:
        g = await union_gvg(session)
        if not g.get("ok"):
            return {"ok": False, "code": g.get("code"),
                    "message": "读取公会战层失败：" + (g.get("message") or "")}
        try:
            level = int((g.get("data") or {}).get("level"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "公会战层号缺失"}
    r = await _act(session, EP_TRAVEL_UNION, 25, level=int(level))
    d = await _json_of(r)
    if r.get("ok") and r.get("code") in (0, None):
        lst = (d or {}).get("list") or []
        n = len(lst)
        return {"ok": True, "code": 0, "level": int(level), "enemies": n,
                "list": lst, "text": f"第 {level} 层候选敌方 {n} 个"}
    return {"ok": False, "code": r.get("code"),
            "message": ((d or {}).get("desc") or r.get("message") or "读取对手失败")}


async def union_enemies(session, level: int = 8) -> dict:
    """公会战另一份对手名单（union act=27 {level}，逗号分隔串）。读。"""
    return await _act(session, EP_TRAVEL_UNION, 27, level=int(level))


async def union_shop(session) -> dict:
    """工会兑换商店（union act=30）。读：data 6 项 [{consume,id,maxnum,type}]。"""
    return await _act(session, EP_TRAVEL_UNION, 30)


async def union_redpacket_list(session) -> dict:
    """抢红包——红包列表（union act=19）。读：redpkggetlist1/2/3 三份红包领取记录。

    2026-09-29 抓包实证（hongbao.har）：**3 个红包对应 list1/2/3**，每份是
    `uin_金额_时间戳_昵称` 逗号分隔的已领记录；**自己 uin 不在 listN 里 = 第 N 个红包未领**。
    pkgdata1/2/3 与 pget 语义未证实，原样透出。
    """
    r = await _act(session, EP_TRAVEL_UNION, 19)
    d = await _json_of(r)
    if not (r.get("ok") and r.get("code") in (0, None)):
        return {"ok": False, "code": r.get("code"),
                "message": ((d or {}).get("desc") or r.get("message") or "读取红包列表失败")}
    lists = {k: (d or {}).get(k) or "" for k in ("redpkggetlist1", "redpkggetlist2", "redpkggetlist3")}
    me = _plain_uin(session)
    claimed = {i: (me in [seg.split("_")[0] for seg in v.split(",") if seg])
               for i, v in (("1", lists["redpkggetlist1"]),
                            ("2", lists["redpkggetlist2"]),
                            ("3", lists["redpkggetlist3"]))}
    return {"ok": True, "code": 0, "lists": lists, "claimed": claimed, "me": me,
            "text": f"红包领取状态：{'、'.join(f'第{i}个已领' if c else f'第{i}个未领' for i, c in claimed.items())}"}


async def union_redpacket_grab(session) -> dict:
    """抢红包（union act=20 {id: 1|2|3}）—— hongbao.har 实证，只抢自己没领过的。

    实证序列：act=20 {id:2} → {"code":0,"content":"92_62_6"}，随后 act=19 的
    redpkggetlist2 里多出自己 uin（4821992_6）——即第 2 个红包领到 6。
    已领判定：自己 uin 已在 redpkggetlistN 里 → 跳过第 N 个（不盲发，避免烧次数/报错）。
    奖励串按项目铁律走 prop_dict，解不出就原样透出。
    """
    st = await union_redpacket_list(session)
    if not st.get("ok"):
        return {"ok": False, "message": st.get("message") or "读取红包列表失败"}
    claimed = st.get("claimed") or {}
    me = st.get("me")
    grabs, skipped = [], 0
    for i in ("1", "2", "3"):
        if claimed.get(i):
            skipped += 1
            continue
        r = await _act(session, EP_TRAVEL_UNION, 20, id=int(i))
        d = await _json_of(r)
        if r.get("ok") and r.get("code") in (0, None):
            content = (d or {}).get("content") or ""
            text = content
            try:
                from .prop_dict import summarize_reward
                text = summarize_reward(str(content)) or content
            except Exception:  # 解不出就保留原始串
                pass
            grabs.append({"id": int(i), "ok": True, "content": content, "text": text})
        else:
            grabs.append({"id": int(i), "ok": False,
                          "msg": (d or {}).get("desc") or r.get("message") or "领取失败"})
    if not grabs and skipped == 3:
        return {"ok": True, "code": 0, "grabs": [], "grabbed": 0, "skipped": 3,
                "text": "3 个红包今天都已领过，无待抢"}
    ok_n = sum(1 for g in grabs if g.get("ok"))
    fail_n = len(grabs) - ok_n
    text = "；".join(f"第{g['id']}个{'：' + g.get('text', '') if g.get('ok') else '失败：' + g.get('msg', '')}"
                     for g in grabs) or "无待抢红包"
    return {"ok": fail_n == 0, "code": 0 if fail_n == 0 else 1,
            "grabs": grabs, "grabbed": ok_n, "skipped": skipped, "me": me,
            "text": text}


async def union_battle(session, feed: bool = True) -> dict:
    """公会战真正出战（union act=23 {cats, eqs}）—— 消耗 3/3 次数，带猫出战。

    2026-09-29 抓包实证：REQ {cats:'3|4|2', eqs:'12个装备id'} → battle_data
    含 attack_accounts.iswin。这才是游戏内「挑战 3/3」那个写接口（此前误标成「换装」，
    又把 act=25 当成「发起挑战」——act=25 其实只是对手列表）。
    阵容从 union_lineup（act=39，cats 全 0）实时读，绝不写死。出战带猫，按用户定调
    先 ensure_mood 到 80；喂失败即中止、不烧次数。
    ⚠ 每日本地硬闸 BATTLE_DAILY_LIMIT=3：超限直接不发请求（不喂猫、不读阵容），
    避免替用户烧当日挑战机会。
    """
    q = _union_quota_check("battle", BATTLE_DAILY_LIMIT)
    if q.get("blocked"):
        return q
    if feed:
        f = await ensure_mood(session)
        if not f.get("ok"):
            msg = f"喂猫到 {MOOD_TARGET} 失败，已中止出战：{f.get('message') or '糖果可能不足'}"
            return {"ok": False, "code": -1, "desc": msg, "message": msg}
    ln = await union_lineup(session, cats=[0, 0, 0])
    elf = ((ln.get("data") or {}).get("elf_list") or [])
    cats, eqs = [], []
    for e in elf:
        cid = e.get("id")
        if not cid:
            continue
        cats.append(int(cid))
        eqs.extend(int(x) for x in (e.get("equip_list") or []))
    if not cats:
        return {"ok": False, "message": "读不到出战阵容（act=39 无 elf_list）"}
    r = await session.post(EP_TRAVEL_UNION, act=23,
                           cats="|".join(str(int(c)) for c in cats),
                           eqs="|".join(str(int(x)) for x in eqs))
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "出战请求未返回数据"}
    ba = (d.get("battle_data") or {}).get("attack_accounts") or {}
    win = ba.get("iswin")
    if r.get("code") in (0, None) and win == 1:
        used = _union_quota_bump("battle").get("battle")
        return {"ok": True, "code": 0, "win": True, "score": ba.get("total_score"),
                "used": used, "limit": BATTLE_DAILY_LIMIT,
                "text": f"公会战出战胜利（总分 {ba.get('total_score')}，今日 {used}/{BATTLE_DAILY_LIMIT}）"}
    if r.get("code") in (0, None):
        used = _union_quota_bump("battle").get("battle")
        return {"ok": True, "code": 0, "win": False, "score": ba.get("total_score"),
                "used": used, "limit": BATTLE_DAILY_LIMIT,
                "text": f"公会战出战结束（iswin={win}，总分 {ba.get('total_score')}，今日 {used}/{BATTLE_DAILY_LIMIT}）"}
    return {"ok": False, "code": r.get("code"),
            "message": (d.get("desc") or r.get("message") or "出战失败")}


async def union_unioninfo_query(session, uins: list) -> dict:
    """按 uin 批量查公会归属（union act=14 {uins:'逗号分隔'}）—— **只读查询，不是点赞**。

    2026-09-29 抓包实证：REQ {uins:'...'} → 返回 info:[{uin, unionid, unionname}]。
    游戏界面上**不存在「全员点赞」这种操作**（用户 09-29 明确纠正）；act=14 只是给
    一串 uin 回填它们所属公会名，用于显示。HAR 里出现多次是因为每拉一批成员就查一次。
    故它**不属于「一键工会」的写操作步骤**，只是一句查询。
    """
    return await _act(session, EP_TRAVEL_UNION, 14, uins=",".join(str(int(u)) for u in uins))


# ---------- 工会玩法：成员名册 ----------

def parse_union_members(members: dict | None) -> list:
    """工会成员名册（union act=1 的 members）。

    值形如 `"4,1790402280,紫旋の梦蝶,6213,96,1_81_1|2_49_1|3_49_1|4_43_1,202680,180,1790402552,3,35"`
    （29 名成员实测）。**只有第 2 段（昵称）与第 3 段（贡献）有实证**：
      - 第 2 段 = 昵称（成员昵称，形如「紫旋の梦蝶」「吸血鬼」）
      - 第 3 段 = 贡献（同批数值 6213 / 8692 / 21330，公会贡献量级）
    其余段（等级/时间戳/装扮串/其他数值）**语义未证实** → 一律原样保留在 raw，
    不做中文硬译（项目铁律：禁止把推测写成常量）。
    """
    out = []
    for uin, raw in (members or {}).items():
        segs = str(raw or "").split(",")
        out.append({"uin": str(uin),
                    "name": segs[2] if len(segs) > 2 else "",
                    "contribution": segs[3] if len(segs) > 3 else "",
                    "raw": raw})
    out.sort(key=lambda x: -(int(x["contribution"]) if str(x["contribution"]).isdigit() else 0))
    return out


async def union_members(session) -> list:
    """工会成员名册（act=1 + 解析）。只读。"""
    r = await union_info(session)
    if not r.get("ok"):
        return []
    return parse_union_members(r.get("members"))


# ---------- 工会玩法：一键工会（领奖 → 点赞 → 出兵） ----------

import time as _utime                                    # noqa: E402
import json as _ujson                                    # noqa: E402
from pathlib import Path as _upath                       # noqa: E402

_UNION_RUN_LOG = _upath(__file__).resolve().parent.parent / "data" / "union_run.json"
_UNION_QUOTA_FILE = _upath(__file__).resolve().parent.parent / "data" / "union_quota.json"

# 每日限次硬闸（本地计数，只统计本工具发起的请求；游戏内手动操作无法感知）：
# - 摇水果 act=17：每日 3 次（服务端第 4 次 busy 拒绝，双保险）。
# - 公会战出战 act=23：3/3 次数（用户实证）。
# - 商店兑换 act=21：**每日 1 次策略闸**（用户 2026-09-29 澄清：商店每周刷新 1 次、
#   每件 maxnum=1，无日限额；每日兑 1 次是为完成「商店兑换 1 次」每日任务，
#   多兑白烧周库存 —— 当日已兑就不再发）。
SHAKE_DAILY_LIMIT = 3
BATTLE_DAILY_LIMIT = 3
EXCHANGE_DAILY_LIMIT = 1


def _union_quota_today() -> dict:
    """读当日工会限次计数（跨日自动清零），形如 {"date":"2026-09-29","shake":1,"battle":0}。"""
    today = _utime.strftime("%F")
    try:
        d = _ujson.loads(_UNION_QUOTA_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("date") == today:
            return {"date": today, "shake": int(d.get("shake", 0)),
                    "battle": int(d.get("battle", 0)),
                    "exchange": int(d.get("exchange", 0))}
    except Exception:
        pass
    return {"date": today, "shake": 0, "battle": 0, "exchange": 0}


def _union_quota_bump(kind: str) -> dict:
    d = _union_quota_today()
    d[kind] = int(d.get(kind, 0)) + 1
    try:
        _UNION_QUOTA_FILE.write_text(_ujson.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:  # 落盘失败不影响主流程
        pass
    return d


def _union_quota_check(kind: str, limit: int) -> dict:
    """超限返回 {"blocked":True,...}（调用方直接 return，不发请求）；未超限返回当日计数。"""
    d = _union_quota_today()
    used = int(d.get(kind, 0))
    if used >= limit:
        name = {"shake": "摇水果", "battle": "公会战出战",
                "exchange": "商店兑换"}.get(kind, kind)
        return {"blocked": True, "ok": False, "code": -1, "used": used, "limit": limit,
                "message": f"{name}今日已 {used}/{limit}（本地限额，不再发请求；"
                           f"真实剩余以游戏内为准）"}
    return {"blocked": False, "used": used, "limit": limit, "date": d.get("date")}


def _save_union_run(payload: dict) -> None:
    """把最近一次「一键工会 / 工会动作」写进 data/union_run.json（只留最近 20 次）。"""
    try:
        hist = []
        if _UNION_RUN_LOG.exists():
            loaded = _ujson.loads(_UNION_RUN_LOG.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                hist = loaded
        row = dict(payload)
        row["ts"] = _utime.strftime("%F %T")
        hist.append(row)
        _UNION_RUN_LOG.write_text(_ujson.dumps(hist[-20:], ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    except Exception:  # 落盘失败不影响主流程
        pass


async def run_union(session, do_shake: bool = True, do_exchange: bool = True,
                    do_redpacket: bool = True, feed: bool = True,
                    do_battle: bool = True) -> dict:
    """「一键工会」：摇水果(act=16 重置 + act=17 开始，每日3轮×(重置+开始)=6接口) →
    商店兑换(act=21，每日1次策略闸) → 抢红包(act=19+20) → 公会战出战(act=23)。

    2026-09-29 三次实证修正 + 2026-10-03 摇水果循环定调：
      - 摇水果每轮 = **先 act=16 重置 再 act=17 开始**（严格 reset→start，非 start→reset）；
        3 轮共 6 次接口；若某轮重置后已五连(5 个相同)，下一轮**跳过重置直接开始**。
        act=17 每日 3 次（第 4 次服务端 busy 拒绝），act=16 重置不限次。
      - act=21 = **商店兑换**（每周刷新 1 次、每件 maxnum=1、无日限额；每日 1 次是
        任务策略闸，靠 EXCHANGE_DAILY_LIMIT=1 当日已兑就不再发）。
      - act=19+20 抢红包：自己 uin 不在 listN 里才发 act=20。
      - act=23 公会战出战：消耗挑战机会，出战前 ensure_mood(80)；09-30 起循环打满
        每日次数（BATTLE_DAILY_LIMIT=3），单场失败/本地闸即停。

    每一步以**服务端返回**判定成败，失败记录 reason 继续其余步骤（红包逐个抢互不影响）。
    """
    steps = []
    before = await union_info(session)
    coin0 = before.get("coin")
    chip0 = before.get("chip")

    # 1) 摇水果（act=17 开始 / act=16 重置，每日 3 轮 ×（重置+开始）= 6 接口；
    #    本地硬闸 + 服务端 busy 双保险；5 连跳过重置换直接开始）
    if do_shake:
        sh = await run_union_shake(session)
        step = {"act": "shake", "ok": sh.get("ok"),
                "rounds": sh.get("rounds"),
                "msg": "；".join(f"第{r['round']}轮：{r['msg']}"
                                for r in sh.get("rounds", [])) or "摇水果无返回"}
        steps.append(step)
        yield step

    # 2) 商店兑换（act=21，每日 1 次策略闸；当日已兑就跳过不发，省周库存）
    if do_exchange:
        x = await union_exchange_redstone(session)
        step = {"act": "exchange", "ok": x.get("ok"),
                "blocked": x.get("blocked"),
                "msg": x.get("text") or x.get("message") or "商店兑换无返回"}
        steps.append(step)
        yield step

    # 3) 抢红包（act=19 读列表 + act=20 {id:N} 逐个领，已领过的跳过）
    if do_redpacket:
        rp = await union_redpacket_grab(session)
        step = {"act": "redpacket", "ok": rp.get("ok"),
                "grabbed": rp.get("grabbed"), "skipped": rp.get("skipped"),
                "msg": rp.get("text") or rp.get("message") or "抢红包无返回"}
        steps.append(step)
        yield step

    # 4) 公会战出战（act=23，消耗挑战次数；本地硬闸 + 服务端限额双保险）
    #    2026-09-30 修正：原版只打 1 场（union_run.json 实证「今日 1/3」就停）；
    #    现循环打到每日上限（BATTLE_DAILY_LIMIT=3）为止。停止条件：
    #    本地闸 blocked / 单场失败（喂猫失败、读不到阵容、服务端拒绝）/ 打满上限。
    #    每场独立走 ensure_mood → 读阵容 → act=23，逐场落 rounds 明细。
    if do_battle:
        rounds = []
        while True:
            b = await union_battle(session, feed=feed)
            rounds.append({"ok": b.get("ok"), "win": b.get("win"),
                           "msg": b.get("text") or b.get("message") or "出战无返回"})
            if b.get("blocked") or not b.get("ok"):
                break
            if int(b.get("used") or 0) >= BATTLE_DAILY_LIMIT:
                break
        step = {"act": "battle",
                "ok": any(x.get("ok") for x in rounds),
                "win": sum(1 for x in rounds if x.get("win")),
                "rounds": rounds,
                "msg": "；".join(f"第{i+1}场：{x['msg']}"
                                for i, x in enumerate(rounds)) or "出战无返回"}
        steps.append(step)
        yield step

    after = await union_info(session)
    out = {"ok": True, "steps": steps,
           "coin_before": coin0, "coin_after": after.get("coin"),
           "chip_before": chip0, "chip_after": after.get("chip"),
           "members": len(after.get("members") or {})}
    _save_union_run(out)
    yield {"act": "__done__", **out}


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


async def explore_shop_buy(session, buyid: int) -> dict:
    """探索商店购买（explore act=7 buyid=N）。

    2026-09-30 HAR 实证（xiyou.har）：buyid=4 → content=48_1599_60、buyid=2 →
    content=48_1599_500（1599=出发消耗的能量道具）；价格由服务端扣（响应不含成本），
    shoplist[].cnt 为已购计数。出发前能量不足时由 run_xiyou 反应式调用。
    """
    return await _act(session, EP_WORLD_EXPLORE, 7, buyid=int(buyid))


async def explore_rank(session) -> dict:
    """探索排行榜（explore act=28）。读：data 4 项。"""
    return await _act(session, EP_WORLD_EXPLORE, 28)


# ---------- 三国战役（card_worldsanguo，与西游记 card_worldexplore 是不同玩法）----------

async def sanguo_task(session) -> dict:
    """三国任务（sanguo act=7）。读：task_detail。"""
    return await _act(session, EP_WORLD_SANGUO, 7)


async def sanguo_list(session) -> dict:
    """三国关卡/记录（sanguo act=9）。读：data 56 项。"""
    return await _act(session, EP_WORLD_SANGUO, 9)


async def sanguo_claim(session, levelid: int = 0) -> dict:
    """三国领通关奖励（sanguo act=2 {levelid}）。

    2026-10-03 sanguolingka.har 实证（SanGuoModule_v_14.swf 真机序列）：
      - act=1 主界面 levelData 每条带 `percent_hp`（通关进度，100=已通关待领奖）、
        `passData.isget`（0=奖励未领）；
      - 领奖 = `act=2 {levelid}`，返回 content=48_..._N（掉落卡）；领完后该关从
        levelData 消失，奖励卡进 ex_box（下次 act=1 拉取才可见）；
      - act=2 **不消耗 freenum**（每日出战次数由 act=11 战斗计，与本领奖无关）。

    旧代码写的 act=8 是错的：act=8 来自前端注释 / union 的以讹传讹；bisai2.har 是
    工会（card_travel_elf_union，act=16=水果重摇），并不能证明三国 act=11/16/8 的语义。
    levelid=0 → 自动领取 levelData 里所有 isget=0 的已通关关；传具体关号 → 只领那一关。
    """
    if levelid:
        return await _act(session, EP_WORLD_SANGUO, 2, levelid=int(levelid))
    # 领掉全部「已通关但奖励未领」的关；每领一张回读一次，避免重复/越界。
    # ⚠ 只认「通关进度 100% 且未领(isget=0)」的关：关卡奖励(act=2)是独立接口，
    #    只有真正通关的关才发奖；进度不满 100% 的关调 act=2 会失败，别去碰。
    claimed, failed = [], []
    for _ in range(20):
        st = await sanguo_state(session)
        levels = [lv for lv in (st.get("levelData") or [])
                  if (lv.get("passData") or {}).get("isget") == 0
                  and int(lv.get("percent_hp") or 0) >= 100]
        if not levels:
            break
        did = False
        for lv in levels:
            lid = int(lv.get("levelid") or lv.get("bossid") or 0)
            if not lid:
                continue
            r = await _act(session, EP_WORLD_SANGUO, 2, levelid=lid)
            rd = r.get("data") if isinstance(r.get("data"), dict) else {}
            if r.get("ok") and rd.get("code") in (0, None):
                c = rd.get("content")
                claimed.append({"levelid": lid, "content": c,
                                "name": _sg_reward_text(c)})
                did = True
            else:
                failed.append({"levelid": lid,
                               "msg": rd.get("desc") or r.get("message") or f"code={rd.get('code')}"})
        if not did:
            break
    contents = [c.get("content") for c in claimed if c.get("content")]
    content_all = ",".join(contents)
    return {"ok": True, "claimed": claimed, "failed": failed,
            "content": content_all,
            "summary": _sg_reward_text(content_all) if content_all else "",
            "message": f"已领 {len(claimed)} 关，失败 {len(failed)} 关"}


async def sanguo_claim_tasks(session) -> dict:
    """三国每日任务领奖（sanguo act=7 查 + act=8 {id} 领）。

    2026-10-03 jiangli.har 实证（SanGuoModule_v_14.swf 真机）：
      - act=7 返回 task_bits（**已领掩码**，bit i 置位=第 i 个任务已领，与
        travel_elf_auto.py:183 同义）+ task_detail[{cnt,id,type}]；
      - act=8 {id} 领对应任务奖励，返回 content=48_..._N（掉落卡），领完该任务
        从「可领」变「已领」（task_bits 对应位置位）；
      - 典型任务 id=1(type=161)=「每日出战 3 次」，打满 3 次后 cnt=3 方能领
        （此时 task_bits 仍只有 id=0 置位），漏领就少拿一份奖励。
        jiangli.har 时序：出手前 act=7 task_bits=1 → 手动 act=8 id=1 领奖 →
        再查 act=7 task_bits=3，证实掩码语义。

    规则：对每个任务，若 task_bits 未置位（没领过）且 cnt>0（有进度，可能已达标），
    调 act=8 {id}；达标才发奖（code=0+content），否则 code!=0 跳过。不臆测阈值
    （响应无 limit 字段），以服务端判定为准，避免重复领/误领。
    """
    r7 = await _act(session, EP_WORLD_SANGUO, 7)
    d7 = r7.get("data") if isinstance(r7.get("data"), dict) else {}
    if not r7.get("ok") or d7.get("code") not in (0, None):
        return {"ok": False, "message": d7.get("desc") or r7.get("message") or "查任务失败",
                "task_bits": d7.get("task_bits"), "task_detail": d7.get("task_detail")}
    bits = int(d7.get("task_bits") or 0)
    claimed, skipped = [], []
    for i, t in enumerate(d7.get("task_detail") or []):
        tid = int(t.get("id") or 0)
        cnt = int(t.get("cnt") or 0)
        if bits & (1 << i):          # 已领过，跳过
            continue
        if cnt <= 0:                 # 无进度，肯定未达标
            continue
        r = await _act(session, EP_WORLD_SANGUO, 8, id=tid)
        rd = r.get("data") if isinstance(r.get("data"), dict) else {}
        if r.get("ok") and rd.get("code") in (0, None) and rd.get("content"):
            c = rd.get("content")
            claimed.append({"id": tid, "type": t.get("type"), "content": c,
                            "name": _sg_reward_text(c)})
        else:
            skipped.append({"id": tid, "type": t.get("type"),
                             "msg": rd.get("desc") or r.get("message") or f"code={rd.get('code')}"})
    contents = [c.get("content") for c in claimed if c.get("content")]
    content_all = ",".join(contents)
    return {"ok": True, "task_bits": bits, "task_detail": d7.get("task_detail"),
            "claimed": claimed, "skipped": skipped,
            "content": content_all,
            "summary": _sg_reward_text(content_all) if content_all else "",
            "message": f"已领 {len(claimed)} 个任务，跳过 {len(skipped)} 个"}


async def sanguo_lineup(session, levelid: int) -> dict:
    """三国阵容（sanguo act=16 {levelid, cats:'0|0|0|0'}）。读。"""
    return await _act(session, EP_WORLD_SANGUO, 16, levelid=int(levelid), cats="0|0|0|0")


# ---------------------------------------------------------------------------
# 三国「关卡卡箱」= ex_box（15 格，装当前大关攒的层卡）
#
# 机制（2026-09-28 用户口述 + bisai2.har 真机实证）：
#   - 每一小关打过 → 随机给一张**层卡**进 ex_box；
#   - ex_box 满了就打不了（出战前必须先腾位）；
#   - 同一「层」（card_list 第 4 字段，如 1502／2502／3502）只需要留最新的一张，
#     旧的要卖掉换三国币（act=5），否则新卡没地方放；
#   - 某套的每一层都齐了才「上交」进下一大关（上交接口未实证，见 _sg_handin TODO）；
#   - 还有第二个箱子「**保险箱**」safe_box_data（12 格，2026-09-28 用户指出）：
#     一样装层卡、也能卖（箱子编号 1），但**不与关卡卡箱互通**这一条没有实证，
#     所以默认只显示、只手动卖，不进自动流程。
# ---------------------------------------------------------------------------

_SG_CARDS: dict | None = None
_SG_THEME_NAMES: dict | None = None      # 套 id → 套名（theme_card_list），见 _sg_theme_name()


def _sg_cards() -> dict:
    """卡库快照：{card_id: [id, theme, 名字, 位置值, ...]}。只认 data/card_lib.json。"""
    global _SG_CARDS
    if _SG_CARDS is None:
        lib = _ujson.loads((_upath(__file__).resolve().parent.parent / "data"
                            / "card_lib.json").read_text(encoding="utf-8"))
        _SG_CARDS = {int(c[0]): c for c in lib["card_list"]}
    return _SG_CARDS


def _sg_meta(card_id) -> dict:
    """一张层卡的可读信息：名字 / 所属套 / 位置值 / 层号。查不到就如实留空。"""
    cid = int(card_id)
    c = _sg_cards().get(cid)
    if not c:
        return {"card_id": cid, "theme": None, "name": None, "slot": None,
                "layer": None}
    slot = int(c[3])
    return {"card_id": cid, "theme": int(c[1]), "name": str(c[2]), "slot": slot,
            "layer": slot // 1000}


def _sg_reward_text(content: str) -> str:
    """三国领奖 content（形如 ``48_1614_10`` 或 ``69_25097_1``）→ 可读文本。

    类型 69＝真实层卡（查卡库名，如「黄巾力士」）；其余（类型 48＝三国币 /
    探索点等通用物品）走 ``prop_dict.reward_name``。同款合并、查不到不编造名字
    （回退「类型_编号」，绝不臆造），原始 content 始终在别处保留。

    ⚠️ 类型 48 的中段**不是**卡 id（翻牌 ``48_1415`` 曾误译成卡「比利亚」，
    实为气球），故类型 48 一律走 prop_dict，绝不用卡库反查 —— 只在类型 69 才查卡名。
    """
    from . import prop_dict as _pd
    agg: dict = {}
    for part in str(content or "").split(","):
        part = part.strip()
        if not part:
            continue
        f = part.split("_")
        if len(f) < 3:
            return part  # 非标准三段格式，原样返回，不丢信息
        try:
            t, i, n = int(f[0]), int(f[1]), int(f[2])
        except ValueError:
            return part
        if t == 69:
            row = _sg_cards().get(i)
            nm = str(row[2]) if row else ""
        else:
            nm = _pd.reward_name(t, i)
        if not nm or (isinstance(nm, str) and nm.startswith("未知道具")):
            nm = f"{t}_{i}"
        agg[nm] = agg.get(nm, 0) + n
    return "、".join(f"{nm}×{n}" for nm, n in agg.items())


SG_BOX_EX = 0        # 关卡卡箱（ex_box_data，15 格）
SG_BOX_SAFE = 1      # 保险箱（safe_box_data，12 格）

_SG_BOX_KEY = {"ex": ("ex_box_data", "ex_box_num"),
               "safe": ("safe_box_data", "safe_box_num")}

# 一套三国卡从低到高共 5 层（位置值 15xx/25xx/35xx/45xx/55xx 的第 1 位）。
# ⚠⚠ 这个「层」是卡库编号（卡位置值千位），**不是游戏界面上的层号，两者是反的**：
#   游戏里「第 1 层」＝本文件第 5 层，只有 1 张「黄巾起义」（张角关＝最后一关出的）；
#   游戏里「第 5 层」＝本文件第 1 层，共 8 张（张曼成＝第一关出的 8 张里的卡）。
#   用户口述的「第 4 层的太平道」＝本文件第 2 层，「第 3 层的天下大吉」＝本文件第 3 层。
#   → 对外（接口/界面）一律用 `_SG_G` 的游戏层号，别把 _SG_LAYERS 的编号直接显示出去。
_SG_LAYERS = (1, 2, 3, 4, 5)
_SG_G = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}      # 卡库层号 -> 游戏界面层号


def _sg_g(layer):
    """卡库层号 → 游戏界面层号。查不到就原样返回，不编。"""
    return _SG_G.get(layer) if layer else layer


def _sg_chapter(levelid) -> int:
    """当前所在大关的百位（204 → 200）。推小关号必须留在同一大关里。"""
    n = int(levelid or 0)
    return (n // 100) * 100 if n >= 100 else 100


# ---- 三国大关 ↔ 套卡（2026-09-29 真机实证，改推荐前必读）----
# **关号 = 大关号 × 100 + 卡库层号**，不是「100 + 层号」：
#   102 掉套 1745 的层 2 卡（25107 黄巾力士，logData 的 passData.sitem 与卡箱 ts 同秒）；
#   203 掉套 1746 的层 3 卡（25131 吕布投董）。每个大关只有 4 个小关
#   （act=9 的 id 表：101-104 / 201-204 / … / 901-904）。
# 9 张大关按 theme 升序取「有层卡」的三国套：1752 杏林春满 / 1753 塞上敕勒 /
# 1754 十二花信是无层的活动套，不在战役地图里，必须跳过（否则大关号整体错位）。
# 实证交叉：第 1 大关 = 1745「黄巾之乱」、第 2 大关 = 1746「虎牢关」（吕布线，与用户
# 口述「第 2 大关第 4 小关」一致）、第 3 大关 = 1747「界桥之战」。
_SG_CHAPTER_THEMES = (1745, 1746, 1747, 1748, 1749, 1750, 1751, 1755, 1756)
_SG_MAX_LAYER = 4         # 小关只到 4；卡库层 5 是 boss 层，没有对应小关号（不臆造 105/205）


def _sg_chapter_theme(chapter) -> int | None:
    """大关号 → 套卡 id（第 1 大关 = 1745）。越界返回 None，不编。"""
    n = int(chapter or 0)
    return _SG_CHAPTER_THEMES[n - 1] if 1 <= n <= len(_SG_CHAPTER_THEMES) else None


def _sg_theme_chapter(theme) -> int | None:
    """套卡 id → 大关号。不在战役列表里（活动套）返回 None。"""
    try:
        return _SG_CHAPTER_THEMES.index(int(theme)) + 1
    except (ValueError, TypeError):
        return None


def _sg_levelid(chapter, layer) -> int | None:
    """关号 = 大关号 × 100 + 卡库层号（第 2 大关的层 2 → 202，层 4 → 204）。"""
    if not chapter or not layer:
        return None
    return int(chapter) * 100 + int(layer)


def _sg_theme_name(theme) -> str | None:
    """套名（data/card_lib.json 的 theme_card_list）。查不到返回 None，不编名字。"""
    global _SG_THEME_NAMES
    if _SG_THEME_NAMES is None:
        lib = _ujson.loads((_upath(__file__).resolve().parent.parent / "data"
                            / "card_lib.json").read_text(encoding="utf-8"))
        rows = lib.get("theme_card_list") or []
        _SG_THEME_NAMES = {int(r[0]): str(r[1]) for r in rows if len(r) > 1}
    return _SG_THEME_NAMES.get(int(theme)) if theme else None


def plan_box(state: dict, box: str = "ex") -> dict:
    """只读推演某一个箱：哪些卡重复了（可卖）、各层收集进度。

    纯函数（不联网），便于离线验证。

    **「多余」的判据是同一张卡重复，不是同层**（2026-09-28 晚修正，之前判错了）：
    card_lib 里三国套的位置值只编码层号（第 3 层 4 张全是 3502、第 4 层 3 张全是
    4502），同层内每张是**不同的卡**、都要收集 —— 只有拿到 2 张「黄天当立」这种
    **同名同 id** 的重复卡才该卖。用户口述「小关过了随机给一张层卡，把多余的买掉，
    留下新卡」指的正是重复卡，不是同层旧卡。

    ⚠ 保险箱里所有卡的 `ts` 都是 0（2026-09-28 实测），重复时只能留**第一个**。
    """
    dkey, ckey = _SG_BOX_KEY[box]
    arr = state.get(dkey) or []
    cap = int(state.get(ckey) or len(arr) or 0)
    slots: dict = {}          # slot(位置值=层) -> [卡信息]
    for i, x in enumerate(arr):
        cid = int(x.get("card_id") or 0)
        if not cid:
            continue
        m = _sg_meta(cid)
        m["index"] = i        # 卡位下标（act=5 卖卡要按它定位）
        if m["slot"] is None:      # 卡库里没有（不该发生）就按原样归到 0
            m["slot"] = 0
            m["layer"] = None
            m["name"] = f"卡{cid}"
        m["ts"] = int(x.get("ts") or 0)
        m["chapter"] = _sg_theme_chapter(m["theme"])
        m["chapter_name"] = _sg_theme_name(m["theme"])
        slots.setdefault(m["slot"], []).append(m)
    by_slot, spare = [], []
    for slot, a in sorted(slots.items(), key=lambda kv: -kv[1][0]["ts"]):
        # 同 slot = 同层。层内按卡 id 再分：同 id 重复的才是多余，不同的卡都要留
        seen: dict = {}
        for m in sorted(a, key=lambda m: -m["ts"]):
            g = seen.setdefault(m["card_id"],
                                {"slot": slot, "layer": m["layer"],
                                 "g": _sg_g(m["layer"]),   # 游戏界面的层号（1=boss层）
                                 "theme": m["theme"],
                                 "chapter": m["chapter"], "chapter_name": m["chapter_name"],
                                 "keep": m, "cards": [], "count": 0})
            g["cards"].append(m)
            g["count"] += 1
            if len(g["cards"]) > 1:
                spare.append(m)
        by_slot.extend(sorted(seen.values(),
                              key=lambda g: -g["keep"]["ts"]))
    cards = [m for g in slots.values() for m in g]
    return {
        "box": box,
        "capacity": cap,
        "filled": len(cards),
        "free": max(0, cap - len(cards)),
        "slots": by_slot,               # 按「层」再按卡分组，每张不同的卡一个组
        "spare": spare,                 # 同一张卡的第 2+ 张（可卖，换三国币）
        "themes": sorted({m["theme"] for m in cards if m["theme"]}),
    }


def plan_ex_box(state: dict) -> dict:
    """关卡卡箱（ex_box）的只读推演。"""
    return plan_box(state, "ex")


def plan_sg_boxes(state: dict) -> dict:
    """关卡卡箱 + 保险箱一起看：每套缺哪几层，缺的层卡现在躺在哪个箱里。"""
    ex, safe = plan_ex_box(state), plan_box(state, "safe")
    holders: dict = {}          # (theme, layer) -> {"ex": n, "safe": n}
    for bx, plan in (("ex", ex), ("safe", safe)):
        for s in plan["slots"]:
            h = holders.setdefault((s["theme"], s["layer"]), {"ex": 0, "safe": 0})
            h[bx] += s["count"]
    themes = []
    for th in sorted({k[0] for k in holders}):
        present = sorted({k[1] for k in holders if k[0] == th})
        ex_missing = [L for L in _SG_LAYERS if L not in
                      {s["layer"] for s in ex["slots"] if s["theme"] == th}]
        # 对外一律给游戏层号（1=boss 层 1 张，5=8 张），别把卡库编号漏出去
        themes.append({
            "theme": th, "present": sorted(_SG_G[L] for L in present),
            # 整套视角（两箱合计）缺的层
            "missing": sorted(_SG_G[L] for L in _SG_LAYERS if L not in present),
            # 关卡卡箱里没有、但保险箱里有的层 —— 卡很可能就在保险箱里压着
            "in_safe_only": sorted(_SG_G[L] for L in ex_missing
                                   if holders.get((th, L), {}).get("safe")),
        })
    return {"ex": ex, "safe": safe, "themes": themes,
            "free_total": ex["free"] + safe["free"]}


def plan_sg_layers(state: dict) -> dict:
    """层进度 + **该打哪一关**（小关 ↔ 层的对应关系）。

    实证（2026-09-28，真机 act=1 的 logData：每条通关记录带 levelid 与
    passData.sitem，形如 `126_25107_1` ＝ 掉了 25107 这张层卡）：
      小关 101 通关给的卡 = 第 1 层（波才 / 白波军 / 张曼成 / 黄巾军 … 共 8 张）；
      小关 102 给的 = 第 2 层（黄巾力士 / 三十六方渠帅，5 张）；
      小关 103 给的 = 第 3 层（岁在甲子），小关 203 给的 = 第 3 层（吕布投董）。
    配合 card_lib 里每层张数 8/5/4/3/1（三国每套都是 5 层），规律即
    **小关号 = 大关号 × 100 + 卡库层号**。
    2026-09-29 真机交叉实证（act=1 logData × ex_box_data 的时间戳）：
      小关 102 掉 25107「黄巾力士」＝ 套 1745 的层 2；小关 203 掉 25131「吕布投董」＝
      套 1746 的层 3。⇒ 第 N 大关的层 L 要打 `N*100 + L`。
    ⚠ 正因为关号里带着大关号，**不能拿「当前大关」去套所有套的层号**：2026-09-29 实测
      旧逻辑把第 1 大关（1745）的缺口推荐成「202」（第 2 大关的关），而 202 掉的是
      1746 的卡、永远补不到「太平道」—— 每个套的关号必须按**该套所属大关**算。

    ⚠ **卡库层号 ≠ 游戏界面的层号，是反的**（2026-09-28 用户纠正）：
      游戏里「第 1 层」＝卡库第 5 层，全层只有 1 张「黄巾起义」——那是张角关
      （最后一关）掉的 boss 层卡；游戏里「第 5 层」＝卡库第 1 层，要攒 8 张。
      所以对外显示、给用户看的层号一律走 `_sg_g()`（见 `_SG_G`）。

    所以「还卡在哪儿」其实是「**还差哪几层的卡没打出来**」；游戏层号越小
    （第 1 层 1 张、第 2 层 3 张）就越快集齐 —— 用户原话：
    「小关和层少的卡有关，要打少卡的小关，这样才能集齐」。

    纯函数（不联网），便于离线验证。已持有张数按「套」分别算，不做两套混算，
    免得「一套齐了」被另一套的缺张掩盖。
    """
    ex, safe = plan_ex_box(state), plan_box(state, "safe")
    lib = _sg_cards()
    # (theme, layer) -> {need, cards}
    spec: dict = {}
    for cid, c in lib.items():
        th, pos = int(c[1]), int(c[3])
        if th in _SG_CHAPTER_THEMES:        # 9 张大关的套；活动套（1752-1754）不进战役表
            s = spec.setdefault((th, pos // 1000), {"need": 0, "cards": []})
            s["need"] += 1
            s["cards"].append({"card_id": cid, "name": str(c[2]), "theme": th,
                               "layer": pos // 1000})
    for s in spec.values():
        s["cards"].sort(key=lambda m: m["card_id"])

    def ids_of(plan, th, layer) -> set:
        """该箱里属于 (套, 层) 的卡 **id 集合**（一张卡只算一次）。

        ⚠ 不能拿「组数」当张数：同一张卡可能同时在关卡卡箱和保险箱里各躺一份，
        直接相加会把缺的卡顶掉 —— 2026-09-28 实测层 2 就是被 25106 双算成 5/5
        「已齐」，真正缺的 25108「太平道」反而被藏起来了（用户当场纠正）。
        """
        return {s["keep"]["card_id"] for s in plan["slots"]
                if s["theme"] == th and s["layer"] == layer}

    # ⚠ 只认顶层 levelId：真机实测顶层 `levelId=204` 而 `levelData[0].levelid=102`
    #   是上一关的旧残留记录（isget=0 也没通过），拿它当「当前关」会错推一整层。
    levelid_now = state.get("levelId")
    now_chapter = int(levelid_now or 0) // 100 or None    # 204 → 2（第 2 大关）
    chapter = _sg_chapter(levelid_now)                    # 204 → 200（旧字段用）
    levels, open_gaps, setgaps = [], [], []
    for layer in _SG_LAYERS:
        # 按层摊表时**只列已解锁大关的套**：9 套全列会刷出一堆「0/8」把真正
        # 该看的缺口盖住（用户对「显示杂」敏感），未解锁的大关另有 chapters 列表展示。
        ths = sorted({k[0] for k in spec if k[1] == layer
                      and (now_chapter is None or (_sg_theme_chapter(k[0]) or 99) <= now_chapter)})
        rows = []
        for th in ths:
            s = spec.get((th, layer)) or {"need": 0, "cards": []}
            id_ex, id_safe = ids_of(ex, th, layer), ids_of(safe, th, layer)
            have_ids = id_ex | id_safe      # 同一张卡两箱各一份只算一次
            have = len(have_ids)
            ch = _sg_theme_chapter(th)
            rows.append({"theme": th, "chapter": ch,
                         "chapter_name": _sg_theme_name(th),
                         # ★ 这一套的这一层该打哪个小关：关号 = **该套所属大关** ×100 + 层。
                         #   2026-09-29 修：以前一律用「当前大关」推，1745（第 1 大关）的缺口
                         #   会被推成 202（第 2 大关），而 202 掉的是 1746 的卡、永远补不到。
                         "levelid": _sg_levelid(ch, layer),
                         "need": s["need"], "have": have, "g": _sg_g(layer),
                         "have_ex": len(id_ex), "have_safe": len(id_safe),
                         "done": have >= s["need"],
                         "cards": s["cards"],
                         # 还差哪几张（按卡名，前端直接列出来，不让人猜）
                         "missing": [{"card_id": c["card_id"], "name": c["name"]}
                                     for c in s["cards"]
                                     if c["card_id"] not in have_ids],
                         # 一张都没有、且既不在关卡卡箱也不在保险箱 = 只能靠打出来
                         "in_boxes": len(have_ids)})
        levels.append({
            "layer": layer, "g": _sg_g(layer),          # g = 游戏界面的层号
            # ⚠ 这里只是「当前大关」同一层的小关号（旧字段，前端兼容用）；
            #   每套真正该打的关号在 by_theme[].levelid（按套所属大关算）。
            "levelid": chapter + layer,
            "by_theme": rows,
            "need": sum(r["need"] for r in rows),
            "have": sum(r["have"] for r in rows),
            "done": all(r["done"] for r in rows),
            "names": [c["name"] for r in rows for c in r["cards"]],
        })
        gap = sum(r["need"] - r["have"] for r in rows if not r["done"])
        # 「按层汇总两套」会掩盖「哪套真快齐了」—— 2026-09-28 实测层 4 表面只差 3 张，
        # 那 3 张全是 1746 套的（1745 层 4 已齐），1745 真缺的层 2「太平道」/ 层 3
        # 「天下大吉」反而排不上号。所以推荐再按「单套 + 层」摊一层。
        for th in ths:
            ms = [c for c in (spec.get((th, layer)) or {}).get("cards", [])
                  if c["card_id"] not in (ids_of(ex, th, layer) | ids_of(safe, th, layer))]
            if ms:
                ch = _sg_theme_chapter(th)
                setgaps.append({"theme": th, "chapter": ch, "layer": layer, "g": _sg_g(layer),
                                "levelid": _sg_levelid(ch, layer), "gap": len(ms),
                                "theme_layer_cards": ms})
        if gap:
            open_gaps.append({"layer": layer, "g": _sg_g(layer),
                              "levelid": chapter + layer,
                              "gap": gap, "done": False})
    # 推荐打哪一关：
    #   ① 只在**已解锁大关**里挑（跨大关的小关未必开着，推了也打不了）；
    #   ② 排除 boss 层（卡库层 5 没有对应小关号，推荐它等于推一个不存在的关）；
    #   ③ gap 相同时**先补靠前的大关** —— 用户「第一大关还没过」时不该被推去第 2 大关。
    rec_pool = [g for g in setgaps
                if 1 <= g["layer"] <= _SG_MAX_LAYER
                and (now_chapter is None or (g["chapter"] or 99) <= now_chapter)]
    recommend = None
    if rec_pool:
        pick = min(rec_pool, key=lambda g: (g["gap"], g["chapter"] or 99, g["layer"]))
        recommend = {k: pick[k] for k in ("theme", "chapter", "layer", "g", "levelid", "gap")}
        # 直接告诉用户「补齐还差这几张」，别只给个层号让他自己猜
        recommend["missing"] = recommend["names"] = \
            [c["name"] for c in pick["theme_layer_cards"]]
        names = "、".join(recommend["missing"]) or "这张"
        tname = _sg_theme_name(pick["theme"]) or ""
        recommend["why"] = (f"第 {pick['chapter']} 大关{tname}的游戏第 {pick['g']} 层"
                            f"还只差 {pick['gap']} 张（缺 {names}），打这一档的小关就能补齐")
        recommend["from_now"] = pick["levelid"] == int(levelid_now or 0)
        recommend["why"] += ("—— 正是服务端给的当前关（%s）。" % levelid_now
                             if recommend["from_now"] else "。")
    # 大关列表（界面「大关可选」用）：一关一套，缺口按卡 id 跨两箱去重。
    # unlocked = 大关号 ≤ 当前大关（act=9 的 id 表里每大关只有 4 个小关）。
    chapters = []
    for n, th in enumerate(_SG_CHAPTER_THEMES, start=1):
        need, have = 0, set()
        for L in _SG_LAYERS:
            s = spec.get((th, L))
            if not s:
                continue
            need += s["need"]
            have |= ids_of(ex, th, L) | ids_of(safe, th, L)
        chg = [g for g in setgaps if g["theme"] == th and 1 <= g["layer"] <= _SG_MAX_LAYER]
        pick2 = min(chg, key=lambda g: (g["gap"], g["layer"])) if chg else None
        chapters.append({
            "chapter": n, "theme": th, "name": _sg_theme_name(th),
            "need": need, "have": len(have), "gap": max(0, need - len(have)),
            "done": need > 0 and len(have) >= need,
            "unlocked": now_chapter is None or n <= now_chapter,
            "levelid": pick2["levelid"] if pick2 else _sg_levelid(n, _SG_MAX_LAYER),
            "layer": pick2["layer"] if pick2 else None,
        })
    return {"levels": levels, "open": open_gaps, "recommend": recommend,
            "chapters": chapters, "levelid_now": levelid_now}


def pick_sg_levelid(state: dict, chapter) -> int | None:
    """在**指定大关**里挑一个该打的小关号（缺口最小的未齐层）。

    界面上的「大关可选」走这里：用户选哪一大关就打哪一大关，是手动覆盖自动推荐的
    入口（例：当前关 204 在第 2 大关，但第一大关还缺「太平道」→ 选第 1 大关 → 102）。
    该大关全齐时回落到该大关的最后一层，不越界到别的大关，也不编不存在的关号。
    """
    n = int(chapter or 0)
    if not n or not _sg_chapter_theme(n):
        return None          # 不在战役的 9 张地图里 → 不编关号
    for c in plan_sg_layers(state).get("chapters") or []:
        if c.get("chapter") == n:
            return c.get("levelid") or _sg_levelid(n, _SG_MAX_LAYER)
    return _sg_levelid(n, _SG_MAX_LAYER)


async def sanguo_sell(session, box: int, index: int, card_id: int) -> dict:
    """卖掉某箱里某一格的层卡（sanguo act=5 {cards:'{箱子}_{卡位}_{卡id}'}）。

    **箱子编号实证（2026-09-28 真机，各发一次）**：
      `cards=0_5_25097` → 关卡卡箱第 5 格清空，`content=48_1614_100`；
      `cards=1_2_25111` → 保险箱第 2 格清空，`content=48_1614_300`。
    所以 **0 = 关卡卡箱 ex_box_data，1 = 保险箱 safe_box_data**。
    参数必须和当前卡箱对得上，否则服务端回 `code -311 卡牌有变化,交易失败~`
    （卡纹丝不动，可安全重试；换了三国币的卖单才会真的成交）。
    """
    return await _act(session, EP_WORLD_SANGUO, 5,
                      cards=f"{int(box)}_{int(index)}_{int(card_id)}")


async def sanguo_sell_ex(session, index: int, card_id: int) -> dict:
    """卖关卡卡箱（箱子 0）。"""
    return await sanguo_sell(session, SG_BOX_EX, index, card_id)


async def auto_sell_spare(session, state: dict, box: str = "ex",
                          keep_free: int = 1) -> dict:
    """腾位：把某一箱里同层的多余卡卖掉，至少留 `keep_free` 格。

    只在「卡箱将满」时才动手（free <= keep_free），绝不在还有空位时主动卖卡。
    默认只动关卡卡箱 —— 保险箱里可能压着还缺的层卡（实测：第 4 层三张全在保险箱），
    而「保险箱 → 关卡卡箱」的搬运接口没有实证，自动卖保险箱的卡很容易把需要的卡卖掉。
    返回 {ok, box, sold:[…], failed, free_after, skipped}。
    """
    plan = plan_box(state, box)
    if plan["free"] > keep_free or not plan["spare"]:
        return {"ok": True, "box": box, "skipped": True, "sold": [],
                "free_after": plan["free"]}
    # 卡位 index 要回查该箱 real 下标（card_list 里没有 index 字段）
    real = state.get(_SG_BOX_KEY[box][0]) or []
    idx = {int(x.get("card_id") or 0): i for i, x in enumerate(real)}
    bno = SG_BOX_EX if box == "ex" else SG_BOX_SAFE
    sold, failed = [], None
    for m in plan["spare"]:
        i = idx.get(m["card_id"], -1)
        if i < 0:
            continue
        r = await sanguo_sell(session, bno, i, m["card_id"])
        d = r.get("data")
        okc = bool(r.get("ok")) and (d.get("code") if isinstance(d, dict) else None) in (0, None)
        sold.append({"index": i, "card_id": m["card_id"], "name": m["name"],
                     "slot": m["slot"], "ok": okc,
                     "content": (d or {}).get("content") if isinstance(d, dict) else None,
                     "desc": (d or {}).get("desc") if isinstance(d, dict) else
                     r.get("message")})
        if not okc:
            failed = sold[-1]
            break
    after = plan_box(await sanguo_state(session), box)["free"]
    return {"ok": True, "box": box, "sold": sold, "failed": failed,
            "free_after": after}


async def sanguo_run(session, levelid: int, cats: list, eqs: list, mode: int = 0,
                     feed: bool = True) -> dict:
    """三国闯关（sanguo act=11 {mode, levelid, eqs, cats}）。

    HAR 实证：eqs 是 N×4 个装备 id 平铺（同 union act=23），cats='3|4|2|1|'（**末尾带 |**）。
    **消耗闯关次数** —— 只在用户主动点时才调。
    闯关带 cats，属于「用到猫的一键操作」：出兵前先把心情喂到 MOOD_TARGET。
    """
    if feed:
        f = await ensure_mood(session)
        if not f.get("ok"):
            msg = f"喂猫到 {MOOD_TARGET} 失败，已中止闯关：{f.get('message') or '糖果可能不足'}"
            return {"ok": False, "code": -1, "desc": msg, "message": msg}
    return await _act(session, EP_WORLD_SANGUO, 11, mode=int(mode), levelid=int(levelid),
                      eqs="|".join(str(int(x)) for x in eqs) + "|",
                      cats="|".join(str(int(c)) for c in cats) + "|")


# 三国每日闯关次数上限。**语义（2026-09-28 用户口述「每天 3 次」+ sanguolingka.har
# 实测 freenum 字段存在且 act=2 领奖不改变它）**：
#   `freenum` 当作「今日已闯关次数」用，剩余 = SGUO_DAILY_MAX - freenum。
#   注意：act=11 战斗是否真让 freenum +1 尚无三国 HAR 实证（bisai2.har 是工会，
#   不能拿来证明三国 act=11 语义）；本常量仅作安全上限，不靠它裁决成败。
SGUO_DAILY_MAX = 3

_SGUO_RUN_LOG = _upath(__file__).resolve().parent.parent / "data" / "sanguo_raid.json"


def _save_sanguo_raid(payload: dict) -> None:
    """把最近一次「三国一键出战」写进 data/sanguo_raid.json（只留最近 20 次）。"""
    try:
        hist = []
        if _SGUO_RUN_LOG.exists():
            loaded = _ujson.loads(_SGUO_RUN_LOG.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                hist = loaded
        row = dict(payload)
        row["ts"] = _utime.strftime("%F %T")
        hist.append(row)
        _SGUO_RUN_LOG.write_text(_ujson.dumps(hist[-20:], ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    except Exception:  # 落盘失败不影响主流程
        pass


def _sanguo_battle_text(d: dict) -> str:
    """把 act=11 的战斗结果压成一句中文（字段缺失就留空，不编造）。"""
    bd = (d.get("battle_data") or {}).get("attack_accounts") or {}
    win = bd.get("iswin")
    bits = [f"{'胜' if win == 1 else '负' if win is not None else '?'}"
            if win is not None else ""]
    if bd.get("total_score") is not None:
        bits.append(f"总分 {bd.get('total_score')}")
    if d.get("boss_reduce_hp"):
        bits.append(f"boss 掉血 {d.get('boss_reduce_hp')}")
    if d.get("percent"):
        bits.append(f"进度 {d.get('percent')}%")
    if d.get("content"):
        bits.append(f"奖励 {d.get('content')}")
    return "；".join(x for x in bits if x)


async def run_sanguo_raid(session, levelid: int | None = None, chapter: int | None = None,
                          feed: bool = True, clear_box: bool = True):
    """三国「一键出战」：用**真实阵容**连打到今日次数用尽（每天 SGUO_DAILY_MAX 次）。

    `chapter` = 界面上选的「打第几大关」（1..9，见 `_SG_CHAPTER_THEMES`）：给了它就
    在该大关内挑该打的小关（缺口最小的未齐层），不给则沿用服务端给的当前关。

    出战前先把关卡卡箱腾出空位（`clear_box`，用户口述：卡箱满不能出战，多余的层卡
    要卖掉换三国币、只留最新那张）。只在卡箱只剩很少空位时才动手，日志逐张记录。

    铁律落地（2026-09-28 补）：
      - `freenum` 是「今日已打次数」而非剩余量，所以剩余 = SGUO_DAILY_MAX - freenum；
      - 每轮都**重新读一次 act=16 阵容**再提交 act=11（HAR 序列：读阵容 → 闯关 →
        读主界面 → …），本地不自减次数；
      - 服务端返回失败即停并记 reason，不做「换一个继续」的猜测性重试；
      - 今日已打满 → 直接 skipped，一次请求都不发，不烧次数。

    HAR 实证（sanguolingka.har 真机）：
      - act=2 {levelid} = **领通关奖励**，返回 content（掉落卡），领完该关从 levelData
        消失、奖励卡进 ex_box；**零消耗、不烧次数**；
      - act=16（读阵容）/ act=11（出战）的语义来自早期推断，三国无 HAR 真证；本函数
        先尽量用 act=2 领掉「已通关未领奖」的关，出战环节保留 act=11 但失败时即停。
    """
    st = await sanguo_state(session)
    if not st.get("ok"):
        yield {"act": "__error__", "ok": False, "message": st.get("message") or "读取三国战役失败"}
        return
    box_before = plan_ex_box(st)
    box_sold = []
    if clear_box and box_before["free"] <= 1 and box_before["spare"]:
        r = await auto_sell_spare(session, st, keep_free=1)
        box_sold = r.get("sold") or []
        st = await sanguo_state(session)   # 卖完立刻回读，后续步骤用真实卡箱
        box_before = plan_ex_box(st)
    # 先领掉所有「已通关但未领奖」的关（act=2，零消耗、不烧次数）。
    # 这正是「剩 N 次不能进行」的根因：关卡清了奖励没领，levelData 卡着后续关出不来。
    pre_claim = await sanguo_claim(session, 0)
    pre_claimed = (pre_claim.get("claimed") or []) if isinstance(pre_claim, dict) else []
    if pre_claimed:
        txt = f"【关卡奖励】预领已通关未领奖励 {len(pre_claimed)} 关"
        if pre_claim.get("summary"):
            txt += f"：{pre_claim['summary']}"
        yield {"act": "pre_claim", "ok": True, "claimed": pre_claimed,
               "summary": pre_claim.get("summary", ""),
               "text": txt}
    # 顺带领掉「开局就可领的每日任务」（act=7 查 + act=8 领），避免漏领（jiangli.har）。
    pre_tasks = await sanguo_claim_tasks(session)
    pre_task_claimed = (pre_tasks.get("claimed") or []) if isinstance(pre_tasks, dict) else []
    if pre_task_claimed:
        txt = f"预领每日任务奖励 {len(pre_task_claimed)} 项"
        if pre_tasks.get("summary"):
            txt += f"：{pre_tasks['summary']}"
        yield {"act": "pre_task", "ok": True, "claimed": pre_task_claimed,
               "summary": pre_tasks.get("summary", ""),
               "text": txt}
    used = int(st.get("freenum") or 0)
    cur_level = st.get("levelId")
    # 界面上选了「打第几大关」（大关可选）→ 在该大关内挑该打的小关，手动覆盖自动推荐。
    # 关号口径：大关号 × 100 + 卡库层号（见 _sg_levelid / _SG_CHAPTER_THEMES）。
    picked_chapter = None
    if chapter:
        picked_chapter = int(chapter)
        lv2 = pick_sg_levelid(st, picked_chapter)
        if lv2:
            levelid = lv2
    if levelid is None or levelid == 0:
        levelid = cur_level
    if not levelid:
        yield {"act": "__error__", "ok": False, "message": "没读到当前关卡号，未做闯关"}
        return
    left = SGUO_DAILY_MAX - used
    if left <= 0:
        yield {"act": "__done__", "ok": True, "skipped": True,
               "message": f"今日已出战 {used}/{SGUO_DAILY_MAX} 次，不再消耗",
               "used": used, "rounds": 0}
        return
    rounds, i = [], 0
    for i in range(left):
        lu = await sanguo_lineup(session, levelid)
        d = lu.get("data")
        d = d if isinstance(d, dict) else {}
        lcode = d.get("code") if "code" in d else lu.get("code")
        if lcode not in (0, None):
            row = {"no": i + 1, "ok": False,
                   "msg": f"阵容读取失败 code={lcode} "
                          f"{d.get('desc') or lu.get('desc') or ''}".strip()}
            rounds.append(row)
            yield {"act": "round", **row}
            break
        elves = d.get("elf_list") or []
        cats = [int(e.get("id")) for e in elves if e.get("id") is not None]
        eqs: list = []
        for e in elves:
            eqs.extend(int(x) for x in (e.get("equip_list") or []))
        if not cats or len(eqs) != len(cats) * 4:
            row = {"no": i + 1, "ok": False,
                   "msg": f"阵容异常 cats={len(cats)} eqs={len(eqs)}，未提交"}
            rounds.append(row)
            yield {"act": "round", **row}
            break
        r = await sanguo_run(session, levelid, cats, eqs, mode=0,
                             feed=(feed and i == 0))
        rd = r.get("data") if isinstance(r.get("data"), dict) else {}
        ok = bool(r.get("ok")) and rd.get("code") in (0, None)
        row = {"no": i + 1, "ok": ok,
               "levelid": int(levelid), "cats": cats, "eq_count": len(eqs),
               "text": _sanguo_battle_text(rd) if ok else
                       (rd.get("desc") or r.get("message") or f"code={rd.get('code')}"),
               "code": rd.get("code") if "code" in rd else r.get("code")}
        rounds.append(row)
        yield {"act": "round", **row}
        if not ok:
            break
    # 关卡奖励是「单独的接口」(act=2)，必须显式领取，不是战斗即时掉落
    # （每场战斗掉的 92_... 才是即时奖励）。本次闯关把 L{levelid} 打到 100% 通关后，
    # 这里单独走一遍 act=2，把所有「已通关未领」的关领掉，作为**独立日志步**，
    # 与战斗奖励明显区分（用户 2026-10-13 反馈：关卡奖励没在日志体现）。
    post_claim = await sanguo_claim(session, 0)
    post_claimed = (post_claim.get("claimed") or []) if isinstance(post_claim, dict) else []
    if post_claimed:
        txt = f"【关卡奖励】本次闯关达成，领取通关关卡 {len(post_claimed)} 关"
        if post_claim.get("summary"):
            txt += f"：{post_claim['summary']}"
        yield {"act": "level_reward", "ok": True, "claimed": post_claimed,
               "summary": post_claim.get("summary", ""),
               "text": txt}
    # 战后补领每日任务奖励（act=7 查 + act=8 领）：打满 3 次的任务(id=1)此刻才达标。
    post_tasks = await sanguo_claim_tasks(session)
    post_task_claimed = (post_tasks.get("claimed") or []) if isinstance(post_tasks, dict) else []
    if post_task_claimed:
        txt = f"战后补领每日任务奖励 {len(post_task_claimed)} 项"
        if post_tasks.get("summary"):
            txt += f"：{post_tasks['summary']}"
        yield {"act": "post_task", "ok": True, "claimed": post_task_claimed,
               "summary": post_tasks.get("summary", ""),
               "text": txt}
    out = {"ok": True, "levelid": int(levelid), "chapter": picked_chapter,
           "used_before": used,
           "used_after": (await sanguo_state(session)).get("freenum"),
           "daily_max": SGUO_DAILY_MAX, "rounds": rounds,
           "pre_claimed": pre_claimed,
           "post_claimed": post_claimed,
           "pre_task_claimed": pre_task_claimed,
           "post_task_claimed": post_task_claimed,
           "played": sum(1 for x in rounds if x.get("ok")),
           "box": {"free_before": box_before.get("free"),
                   "free_after": plan_ex_box(
                       await sanguo_state(session)).get("free"),
                   "sold": box_sold,
                   "note": ("同层多余的旧卡已卖掉腾位置" if box_sold else
                            "卡箱未清空位，未动卡" if clear_box else "未检查卡箱")}}
    _save_sanguo_raid(out)
    yield {"act": "__done__", **out}


# ---------------------------------------------------------------------------
# 各玩法「状态属性」字段表（2026-09-26）
# 每个玩法子块单独取自己玩法的字段：来源接口 -> 分组 -> (中文名, 原 key)
# 未在此列出的 key 会归进「其他原始字段」，不会丢服务端返回的字段。
# ---------------------------------------------------------------------------
MODE_PROP_SOURCES: dict = {
    "comp": ["score", "hero", "dress"],   # dress=act=6 衣橱，供「顶级装备属性」显示属性数值
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


def _fmt_week_theme(rows) -> str:
    """pvp act=1 week_theme：[{"id":"1","val":"20"},…] → 「唱歌 20% · …」（bisai2.har 实证 6 项）。"""
    parts = []
    for a in rows or []:
        if not isinstance(a, dict):
            continue
        try:
            parts.append(f"{_attr_name(int(a.get('id')))} {a.get('val')}%")
        except (TypeError, ValueError):
            continue
    return " · ".join(parts)


def _fmt_eq_item(eid, dress: dict | None) -> str:
    """单件装扮 → 「头:魔法帽(演奏+266)」；无名（官方表缺 48 件）→ 「头:#10663(唱歌+262 舞蹈+72)」。

    属性加成只从衣橱 act=6 的 eq_attr 取（玩家真实数值，见 cat_dress.equip_attrs）。
    """
    try:
        eid = int(eid)
    except (TypeError, ValueError):
        return ""
    nm = _equip_name(eid)
    slot = _equip_slot(eid)
    if nm:
        label = f"{slot}:{nm}" if slot else nm
    else:
        label = f"{slot}:#{eid}" if slot else f"#{eid}"
    attrs = _equip_attrs(eid, dress or {})
    if attrs:
        label += "(" + " ".join(f"{_attr_name(k)}+{v}" for k, v in sorted(attrs.items())) + ")"
    return label


def _fmt_top_eq(rows, dress: dict | None) -> str:
    """comp act=9 my_top_eq_attr：[{"id":N},…] → 「头:魔法帽(演奏+266) ｜ …」。

    名字来自官方表；缺名的用槽位/#id，**属性加成从衣橱 act=6 的 eq_attr 取**
    （衣橱里都有，名字表缺的 48 件也能显示数值而不是裸编号）。
    """
    parts = []
    for a in rows or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        parts.append(_fmt_eq_item(a["id"], dress))
    return " ｜ ".join(p for p in parts if p)


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
    dress = None                       # comp 的 act=6 衣橱（只供 _fmt_top_eq 用，不进 merged）
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
        if sk == "comp:dress":
            dress = data               # dress_info 已裁成四张表，直接用
            continue
        merged.update(_merge_src(mode, s, data))

    groups, seen = [], set()
    for gname, rows in MODE_PROP_GROUPS.get(mode, []):
        out = []
        for raw_key, label in rows:
            if raw_key not in merged:   # _EXTRA_KEYS 只在「其他字段」里排除，别挡住分组展示
                continue
            val = merged[raw_key]
            if raw_key == "my_top_eq_attr" and isinstance(val, list) and val:
                txt = _fmt_top_eq(val, dress) or _fmt_v(val)
            elif raw_key == "week_theme" and isinstance(val, list) and val:
                txt = _fmt_week_theme(val) or _fmt_v(val)
            else:
                txt = _fmt_v(val)
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
