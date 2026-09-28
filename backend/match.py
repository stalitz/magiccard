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
玩法做了」），所以 `run_union()` 是三步真实写操作 —— 领奖(act=17) → 点赞(act=14) →
出兵(act=21)。三个 act 全部有实跑证据，参数与返回见各函数 doc；**每日出兵上限至今
未证实**，因此出兵只发一次、不循环。
"""
import json
from typing import Optional

from .cat_dress import (  # 装扮名 / 属性名（官方 catHome_Info 配置实证，见 cat_dress 模块 doc）
    attr_name as _attr_name, equip_name as _equip_name, summary as _equip_summary,
    slot_of as _equip_slot,
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


async def run_match_all(session, max_races: int = 0, stop_mood: int = 80) -> dict:
    """一键比赛：把今日能打的场次**连续打完**（每次都换最优装），返回逐场明细。

    这是「一键比赛」的后端编排，步骤全部走真实接口、参数均按 HAR/真机实证：

      1. competition act=2   读今日主题 + 本场 id（每场随机，必须打之前现读）
      2. competition act=6   读衣橱（best_equip 的输入）
      3. competition act=1   读猫列表 + performe_num（今日剩余次数）
      4. 循环 competition act=4 上台：
           · 每轮都重新算最优穿装（主题每场独立随机，不能沿用上轮组合）
           · 出战猫 = 心情达标 + 满 4 件装备 + 等级最高，没有就退而求「满 4 件装备」
           · 一次非 code=0 立即停（参数错误不扣次数，但成功返错会白丢次数）
           · performe_num 归零或到 max_races 上限即收工
      5. 收工回读 act=1 拿最终状态，供前端刷新

    **会消耗 performe_num**，由前端 confirm 后才调用；来一个 max_races 可限制场次。
    """
    # 心情门槛（用户 2026-09-27 定调）：一键比赛要用猫，出战前先把 4 只猫喂到 80，
    # 否则 stop_mood 过滤会把猫全筛掉、或上台直接被拒。喂失败即中止（不扣次数）。
    feed = await ensure_mood(session, stop_mood)
    if not feed.get("ok"):
        return {"ok": False, "message": f"喂猫到 {stop_mood} 失败，已中止一键比赛（不消耗次数）："
                                        f"{feed.get('message') or '糖果可能不足'}"}
    fed_n = len([s for s in (feed.get("steps") or []) if s.get("candy")])

    try:
        th = await competition_theme(session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"读主题异常：{e}"}
    if not th.get("ok") or th.get("race_id") is None:
        return {"ok": False, "message": f"读本场比赛 id 失败："
                                        f"{th.get('message') or th.get('code')}"}
    theme = {a["id"]: a["value"] for a in (th.get("theme") or [])}

    try:
        st = await match_state(session, want_equip=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"读比赛状态异常：{e}"}
    if not st.get("ok"):
        return {"ok": False, "message": st.get("message") or "读比赛状态失败"}

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
        return {"ok": False, "message": "没有满 4 件装备的猫，无法出战（不消耗次数）",
                "left": total0}

    races, theme_rows = [], (th.get("theme") or [])
    race_id = int(th["race_id"])
    n = 0
    while left > 0 and (max_races <= 0 or n < max_races):
        # 2026-09-26 修：每场都必须重读 act=2 拿**本场**的 race_id。
        # 此前沿用首轮 id，第 2 场起 race_id 过期 → act=4 回 code=-1 → 提前收工。
        th_next = await competition_theme(session)
        if th_next.get("ok") and th_next.get("race_id") is not None:
            race_id = int(th_next["race_id"])
            theme = {a["id"]: a["value"] for a in (th_next.get("theme") or [])}
            theme_rows = th_next.get("theme") or []
        sug = best_equip(equip, theme) if theme else {}
        ids = (sug.get("equip_ids") or [])
        if len(ids) != 4:
            races.append({"err": "算不出 4 件最优装，停止"})
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
            races.append({"err": r.get("message") or "请求异常"})
            break
        d = r.get("data") or {}
        if d.get("code") != 0:
            races.append({"cat_id": cat.get("elf_id"), "code": d.get("code"),
                          "err": d.get("desc") or d.get("message") or "失败",
                          "total_value": d.get("total_value")})
            break
        left = d.get("performe_num")
        try:
            left = int(left) if left is not None else 0
        except (TypeError, ValueError):
            left = 0
        n += 1
        races.append({
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
        })
        # 下一轮的主题/race_id 已在循环开头重读，这里不再重复拉

    try:
        after = await match_state(session)
    except Exception:  # noqa: BLE001
        after = {}
    return {
        "ok": True, "ran": n, "left_before": total0, "left_after": left,
        "races": races,
        "theme": theme_rows,
        "cats": (after.get("cats") if after.get("cats") else cats),
        "feed": {"ok": True, "candies": fed_n},   # 出战前把心情喂到 stop_mood 的结果
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
    """工会碎片/贡献（union act=16）。读：data=22315。"""
    return await _act(session, EP_TRAVEL_UNION, 16)


async def union_claim(session) -> dict:
    """工会领奖（union act=17）。零消耗。

    2026-09-27 实跑：`content=48_1542_10,48_1543_1`（与 HAR 的 92_32_10,48_1543_1
    同类，具体 id 每次可能不同）。奖励串按项目铁律统一走 prop_dict，不在别处裸译。
    """
    r = await _act(session, EP_TRAVEL_UNION, 17)
    if r.get("ok"):
        try:
            from .prop_dict import summarize_reward
            r["text"] = summarize_reward(str((r.get("data") or {}).get("content") or ""))
        except Exception:  # 取不到就留原始串，不阻断领奖
            pass
    return r


async def union_fight(session, optype: int = 4, uptype: int = 1,
                      feed: bool = True) -> dict:
    """出兵（union act=21 {optype, uptype}）。

    ⚠ 2026-09-28 用户实证：**这不是游戏内公会战的「挑战」（3/3 那个）** ——
    出兵跑了 3 次，游戏挑战次数仍 3/3 满的。实测效果：工会金币 +2、exp 增长，
    且 act=22 的 gift_record 里多次出现自己 uin（疑似助威/送礼类动作）。
    游戏内挑战的写接口未定位；要接它必须先抓包，禁止盲试写接口（会烧真实次数）。

    出兵带猫与装备（同族 act=23/act=11 的 cats/eqs 语义），所以出兵前按用户定调
    先把猫喂到 MOOD_TARGET。
    """
    if feed:
        f = await ensure_mood(session)
        if not f.get("ok"):
            msg = f"喂猫到 {MOOD_TARGET} 失败，已中止出兵：{f.get('message') or '糖果可能不足'}"
            return {"ok": False, "code": -1, "desc": msg, "message": msg}
    return await _act(session, EP_TRAVEL_UNION, 21, optype=int(optype), uptype=int(uptype))


async def union_enemies(session, level: int = 8) -> dict:
    """公会战关卡对手名单（union act=27 {level}）。读。"""
    return await _act(session, EP_TRAVEL_UNION, 27, level=int(level))


async def union_shop(session) -> dict:
    """工会兑换商店（union act=30）。读：data 6 项 [{consume,id,maxnum,type}]。"""
    return await _act(session, EP_TRAVEL_UNION, 30)


async def union_zan(session, uins: list) -> dict:
    """工会点赞（union act=14 {uins:'逗号分隔'}）。HAR 里调了 17 次，零消耗。

    2026-09-27 实跑：uins="1006409757" → {"ok":true,"code":0}。
    **逐个发**（对齐 HAR 的 17 次调用），避免一次传 29 个 uin 被服务端拒绝。
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


async def run_union(session, do_zan: bool = True, do_fight: bool = True,
                    zan_all: bool = False, feed: bool = False) -> dict:
    """「一键工会」：工会领奖(act=17) → 工会点赞(act=14) → 公会战出兵(act=21)。

    每一步都以**服务端返回**判定成败（项目铁律：不拿本地推测当结果），
    失败即停并记录 reason，不继续下一步。

    已知（2026-09-27 实跑，09-28 修正口径）：
      - 领奖 act=17 返回 ok/code/data；点赞 act=14 逐个发，零消耗；
      - 出兵 act=21 {optype:4, uptype:1} → {"code":0,"coin":4053,"exp":72580,"lv":0}，
        工会金币 coin 4051 → 4053；**act=21 不是游戏内公会战挑战（3/3 未消耗）**，
        act=15 `num` 口径存疑（可能跨日累计），所以**每日上限未知 → 不循环，一次为止**。
    """
    steps, fights = [], 0
    before = await union_info(session)
    coin0 = before.get("coin")
    chip0 = before.get("chip")

    # 1) 工会领奖（每日限额：第二次起服务端回 code=-2 system busy = 今天已领过）
    r = await union_claim(session)
    ccode = r.get("code")
    r_ok = bool(r.get("ok")) and ccode in (0, None)
    msg = r.get("text") or (r.get("data") or {}).get("content") or ""
    if not r_ok:
        msg = f"{msg}｜" if msg else ""
        msg += f"今天已领过（{(r.get('data') or {}).get('desc') or '未领取'}）"
    steps.append({"act": "claim", "ok": r_ok, "code": ccode,
                  "msg": msg or (r.get("message") or "领奖无返回")})

    # 2) 工会点赞（逐个发，对齐 HAR 的 17 次调用）
    if do_zan:
        mem = await union_members(session)
        targets = [m["uin"] for m in mem] if zan_all else [m["uin"] for m in mem[:3]]
        zan_ok = 0
        for u in targets:
            z = await union_zan(session, [u])
            if z.get("ok") and z.get("code") in (0, None):
                zan_ok += 1
        steps.append({"act": "zan", "ok": zan_ok > 0, "count": zan_ok,
                      "total": len(targets),
                      "msg": f"点赞成功 {zan_ok}/{len(targets)} 人"})
        if zan_ok == 0:
            steps[-1]["msg"] = f"点赞全部失败（{len(targets)} 人）：服务端未接受"

    # 3) 公会战出兵（消耗；次数以服务端为准，一次为止）
    if do_fight:
        f = await union_fight(session, feed=feed)
        if f.get("ok") and f.get("code") in (0, None):
            fights = 1
            steps.append({"act": "fight", "ok": True,
                          "msg": f"出兵成功（{_utime.strftime('%T')}）"})
        else:
            steps.append({"act": "fight", "ok": False,
                          "msg": f.get("message") or f.get("desc") or "出兵失败（可能次数已用完）"})

    after = await union_info(session)
    out = {"ok": True, "steps": steps, "fights": fights,
           "coin_before": coin0, "coin_after": after.get("coin"),
           "chip_before": chip0, "chip_after": after.get("chip"),
           "members": len(after.get("members") or {})}
    _save_union_run(out)
    return out


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


# ---------- 三国战役（card_worldsanguo，与西游记 card_worldexplore 是不同玩法）----------

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


SG_BOX_EX = 0        # 关卡卡箱（ex_box_data，15 格）
SG_BOX_SAFE = 1      # 保险箱（safe_box_data，12 格）

_SG_BOX_KEY = {"ex": ("ex_box_data", "ex_box_num"),
               "safe": ("safe_box_data", "safe_box_num")}

# 一套三国卡从低到高共 5 层（位置值 15xx/25xx/35xx/45xx/55xx 的第 1 位）
_SG_LAYERS = (1, 2, 3, 4, 5)


def plan_box(state: dict, box: str = "ex") -> dict:
    """只读推演某一个箱：哪些层齐了、缺哪层、哪些是同层里可以卖掉的多余卡。

    纯函数（不联网），便于离线验证。规则只有一条、且来自用户口述：
    **同一位置值（同一层）只留一张，其余算多余。**

    ⚠ 保险箱里所有卡的 `ts` 都是 0（2026-09-28 实测），同层多张时只能留**第一个**，
    不能假装「留最新」。
    """
    dkey, ckey = _SG_BOX_KEY[box]
    arr = state.get(dkey) or []
    cap = int(state.get(ckey) or len(arr) or 0)
    slots: dict = {}          # slot(位置值) -> [卡信息]
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
        slots.setdefault(m["slot"], []).append(m)
    by_slot = []
    spare = []
    for slot, a in sorted(slots.items(), key=lambda kv: -kv[1][0]["ts"]):
        a.sort(key=lambda m: -m["ts"])
        keep, redundant = a[0], a[1:]
        by_slot.append({"slot": slot, "layer": keep["layer"], "theme": keep["theme"],
                        "keep": keep, "cards": a, "count": len(a)})
        spare.extend(redundant)
    cards = [m for g in slots.values() for m in g]
    return {
        "box": box,
        "capacity": cap,
        "filled": len(cards),
        "free": max(0, cap - len(cards)),
        "slots": by_slot,               # 按「层」分组，保留的那张是 keep
        "spare": spare,                 # 同层多出来的（可卖，换三国币）
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
        themes.append({
            "theme": th, "present": present,
            # 整套视角（两箱合计）缺的层
            "missing": [L for L in _SG_LAYERS if L not in present],
            # 关卡卡箱里没有、但保险箱里有的层 —— 卡很可能就在保险箱里压着
            "in_safe_only": [L for L in ex_missing
                             if holders.get((th, L), {}).get("safe")],
        })
    return {"ex": ex, "safe": safe, "themes": themes,
            "free_total": ex["free"] + safe["free"]}


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
    if plan["free"] > keep_free:
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


# 三国每日闯关次数上限。**实证来源（2026-09-28 读 bisai2.har 真机序列）**：
#   act=1 的 `freenum` 是「**今日已闯关次数**」，随每次 act=11 成功后 +1，
#   0 → 1 → 2 → 3 后封顶不再涨（同批 `buynum` 恒 0）；用户口述「每天 3 次」。
#   所以「剩余次数 = SGUO_DAILY_MAX - freenum」，不能拿 freenum 当剩余量用。
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


async def run_sanguo_raid(session, levelid: int | None = None, feed: bool = True,
                          clear_box: bool = True) -> dict:
    """三国「一键出战」：用**真实阵容**连打到今日次数用尽（每天 SGUO_DAILY_MAX 次）。

    出战前先把关卡卡箱腾出空位（`clear_box`，用户口述：卡箱满不能出战，多余的层卡
    要卖掉换三国币、只留最新那张）。只在卡箱只剩很少空位时才动手，日志逐张记录。

    铁律落地（2026-09-28 补）：
      - `freenum` 是「今日已打次数」而非剩余量，所以剩余 = SGUO_DAILY_MAX - freenum；
      - 每轮都**重新读一次 act=16 阵容**再提交 act=11（HAR 序列：读阵容 → 闯关 →
        读主界面 → …），本地不自减次数；
      - 服务端返回失败即停并记 reason，不做「换一个继续」的猜测性重试；
      - 今日已打满 → 直接 skipped，一次请求都不发，不烧次数。

    HAR 实证（bisai2.har 真机，mode 恒 0）：
      - act=16 {levelid, cats:'0|0|0|0'} → elf_list 4 只猫（id + equip_list 4 件）；
      - act=11 {mode, levelid, eqs（N×4 平铺、尾随 |）, cats（猫 id 序、尾随 |）}
        → base_score / battle_data.attack_accounts{iswin,total_score,talent_rate} /
          attack_elf_list / boss_reduce_hp / percent / content。
    """
    st = await sanguo_state(session)
    if not st.get("ok"):
        return {"ok": False, "message": st.get("message") or "读取三国战役失败"}
    box_before = plan_ex_box(st)
    box_sold = []
    if clear_box and box_before["free"] <= 1 and box_before["spare"]:
        r = await auto_sell_spare(st, keep_free=1)
        box_sold = r.get("sold") or []
        st = await sanguo_state(session)   # 卖完立刻回读，后续步骤用真实卡箱
        box_before = plan_ex_box(st)
    used = int(st.get("freenum") or 0)
    cur_level = st.get("levelId")
    if levelid is None or levelid == 0:
        levelid = cur_level
    if not levelid:
        return {"ok": False, "message": "没读到当前关卡号，未做闯关"}
    left = SGUO_DAILY_MAX - used
    if left <= 0:
        return {"ok": True, "skipped": True,
                "message": f"今日已出战 {used}/{SGUO_DAILY_MAX} 次，不再消耗",
                "used": used, "rounds": 0}
    rounds, i = [], 0
    for i in range(left):
        lu = await sanguo_lineup(session, levelid)
        d = lu.get("data")
        d = d if isinstance(d, dict) else {}
        lcode = d.get("code") if "code" in d else lu.get("code")
        if lcode not in (0, None):
            rounds.append({"no": i + 1, "ok": False,
                           "msg": f"阵容读取失败 code={lcode} "
                                  f"{d.get('desc') or lu.get('desc') or ''}".strip()})
            break
        elves = d.get("elf_list") or []
        cats = [int(e.get("id")) for e in elves if e.get("id") is not None]
        eqs: list = []
        for e in elves:
            eqs.extend(int(x) for x in (e.get("equip_list") or []))
        if not cats or len(eqs) != len(cats) * 4:
            rounds.append({"no": i + 1, "ok": False,
                           "msg": f"阵容异常 cats={len(cats)} eqs={len(eqs)}，未提交"})
            break
        r = await sanguo_run(session, levelid, cats, eqs, mode=0,
                             feed=(feed and i == 0))
        rd = r.get("data") if isinstance(r.get("data"), dict) else {}
        ok = bool(r.get("ok")) and rd.get("code") in (0, None)
        rounds.append({"no": i + 1, "ok": ok,
                       "levelid": int(levelid), "cats": cats, "eq_count": len(eqs),
                       "text": _sanguo_battle_text(rd) if ok else
                               (rd.get("desc") or r.get("message") or f"code={rd.get('code')}"),
                       "code": rd.get("code") if "code" in rd else r.get("code")})
        if not ok:
            break
    out = {"ok": True, "levelid": int(levelid), "used_before": used,
           "used_after": (await sanguo_state(session)).get("freenum"),
           "daily_max": SGUO_DAILY_MAX, "rounds": rounds,
           "played": sum(1 for x in rounds if x.get("ok")),
           "box": {"free_before": box_before.get("free"),
                   "free_after": plan_ex_box(
                       await sanguo_state(session)).get("free"),
                   "sold": box_sold,
                   "note": ("同层多余的旧卡已卖掉腾位置" if box_sold else
                            "卡箱未清空位，未动卡" if clear_box else "未检查卡箱")}}
    _save_sanguo_raid(out)
    return out


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
