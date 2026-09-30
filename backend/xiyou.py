"""西游记探索（EP_WORLD_EXPLORE = card_worldexplore）——2026-09-28 由用户抓包
（xiyou.har，224 条请求）实证。

⚠️ 这是**独立于三国战役**（card_worldsanguo）的玩法，两者共用同族接口结构但
   端点、关卡体系、卡箱都不同，禁止合并显示、禁止互相抄参数。

## 端点 act 表（全部来自 xiyou.har 真实请求，没有一个是猜的）

  act=1  opuin=<纯数字uin>  → 主状态（只读）
          currLevelData[0] = 当前进行中的探索：levelid / origin_time(86400=24h) /
          startts(出发时刻) / stepData[24 条 收获点]
          logData[]        = 历次探索记录（24 步全 isget=1 表示已收完）
          ex_box_data / new_box_data / safe_box_data = 三个卡箱（元素只含 card_id）
          maxLevelId = 解锁到第几大关 ｜ shoplist 商店 ｜ freepowerbit 免费能量
  act=5  type=3&time=5&levelid=N&teamid=0 → **出发探索**（type=3 ＝高级探索，
          用户原话「需要选高级探索」；返回 levelData＝新一次 stepData）
  act=6  levelid=N&index=M&getall=0       → **收第 M 个收获点**，返回
          content="121_卡id_1" 奖励串、isgetall（最后一步=1）、levelData
  act=9  cards=2_i_cid&new_box_data=2_i_cid&new_box_num=N → **卖探索收获箱的卡**
          （2 ＝ new_box_data 箱号，i ＝箱格下标，cid ＝卡 id；快照参数防并发），
          返回 content + 三个卡箱的最新内容
  act=15 opuin=  → 每日任务 task_bits / task_detail[{cnt,id,type}]
  act=16 id=N    → **领任务奖**，返回 content="48_1599_10,92_32_1,…"
  act=24（无参）→ freepowerbit + vlb（免费能量相关，语义未完全证实，只透出）
  act=7  buyid=N → 商店购买（HAR 样本 buyid=2 / 4，产出 48_1599_500 之类材料卡）
  act=20 → weekgiftbit｜act=26 → 商店计数｜act=28 → 每日计数（都只透出）

## 大关 ↔ 套卡（实证）

  maxLevelId=2 时，当前大关 levelid=2，玩家缺的卡正好是套 **1605 天宫** 里的
  太上老君 / 玉帝王母 / 兜率宫 —— 与用户口述「第 2 大关天宫，缺兜率宫(游戏第2层)、
  玉帝王母和太上老君(游戏第3层)」逐条吻合。故 levelid=N ↔ theme 1603+N
  （1604 花果山 / 1605 天宫 / 1606 五庄观）。

## 层号口径（与三国模块同一个坑，2026-09-28 用户纠正）

  card_lib 的位置值千位＝**卡库层号**，与游戏界面显示的层号是**反的**：
  卡库第 5 层只有 1 张（天宫，boss，最后一关），卡库第 1 层要 8 张（第一关）。
  对外一律用 `game_layer = 6 - lib_layer`（*_xy_g()）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from .constants import EP_WORLD_EXPLORE
from .match import _json_of, _plain_uin, _upath, ensure_mood, MOOD_TARGET   # noqa: F401  （_upath 供落盘使用）

# ---------------------------------------------------------------- 卡库 / 层号

_XY_THEMES: Optional[dict] = None
_XY_LIB: Optional[dict] = None


def _xy_lib() -> dict:
    """card_lib.json 加载并缓存。返回 {card_id: {"theme","name","layer"}}。"""
    global _XY_LIB, _XY_THEMES
    if _XY_LIB is None:
        p = _upath(__file__).resolve().parent.parent / "data" / "card_lib.json"
        rows = []
        _XY_LIB = {}
        try:
            with open(p, encoding="utf-8") as f:
                lib = __import__("json").load(f)
            for c in lib.get("card_list") or []:
                cid = int(c[0])
                _XY_LIB[cid] = {"theme": int(c[1]), "name": str(c[2]),
                                "layer": int(c[3]) // 1000 if int(c[3]) else None}
            # 套名在 theme_card_list（1681 条，[0]=套id [1]=套名），theme_list 字段为空
            rows = lib.get("theme_card_list") or lib.get("theme_list") or []
        except Exception:  # noqa: BLE001
            _XY_LIB = {}
        _XY_THEMES = {int(r[0]): str(r[1]) for r in rows if len(r) > 1}
    return _XY_LIB


def theme_name(theme_id) -> str:
    """套名（天宫殿 之类）。查不到就返回「套 1605」，绝不编名字。"""
    _xy_lib()
    if not theme_id:
        return ""
    return _XY_THEMES.get(int(theme_id)) or f"套 {theme_id}"


def card_name(card_id) -> str:
    m = _xy_lib().get(int(card_id or 0))
    return m["name"] if m else f"卡{card_id}"


def _xy_g(layer):
    """卡库层号 → 游戏界面层号（6 减，与三国模块同一个映射）。查不到就原样返回。"""
    if not layer:
        return layer
    return 6 - int(layer)


# 大关号 → 套 id（实证：maxLevelId=2 ↔ 1605 天宫）
def level_theme(levelid):
    try:
        n = int(levelid)
    except (TypeError, ValueError):
        return None
    return 1603 + n if 1 <= n <= 8 else None


# ---------------------------------------------------------------- 只读

async def xiyou_state(session, want_task: bool = False) -> dict:
    """西游记探索主状态（act=1，只读）。返回 {ok, ...}。

    顺带把三个卡箱**完整内容**带出来（act=1 同一次响应里就有，省一次请求），
    这是算「还缺哪几张」和「重复卡能卖哪些」的唯一数据源。
    """
    try:
        r = await session.post(EP_WORLD_EXPLORE, act=1,
                               opuin=_plain_uin(session))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求西游记探索异常：{e}"}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "code": r.get("code"),
                "message": r.get("message") or "读取西游记探索失败"}
    out = {"ok": True, "code": r.get("code")}
    for k in ("currLevelData", "logData", "formationList", "freepowerbit",
              "luckydata", "shoplist", "maxLevelId", "ex_box_num",
              "new_box_num", "safe_box_num",
              "nlb", "sjb", "txd", "vlb", "guide", "newbiebit",
              "ex_box_data", "new_box_data", "safe_box_data",
              "tick", "weekgiftbit", "suitcard"):
        if k in d:
            out[k] = d[k]
    if want_task:
        try:
            t = await xiyou_task_list(session)
            if t.get("ok"):
                out["task_bits"] = t.get("task_bits")
                out["task_detail"] = t.get("task_detail")
        except Exception as e:  # noqa: BLE001
            out["task_err"] = str(e)
    return out


async def xiyou_task_list(session) -> dict:
    """每日任务（act=15 opuin）。零消耗只读。"""
    try:
        r = await session.post(EP_WORLD_EXPLORE, act=15,
                               opuin=_plain_uin(session))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
    d = await _json_of(r)
    if not d:
        return {"ok": False, "message": "读取任务失败"}
    return {"ok": True, "task_bits": d.get("task_bits"),
            "task_detail": d.get("task_detail") or []}


# ---------------------------------------------------------------- 纯函数规划

def _box_items(arr) -> list:
    """卡箱数组 → [{index, card_id, name, theme, layer, game_layer}]。"""
    lib = _xy_lib()
    out = []
    for i, x in enumerate(arr or []):
        cid = int(x.get("card_id") or 0) if isinstance(x, dict) else int(x or 0)
        if not cid:
            continue
        m = lib.get(cid, {})
        out.append({"index": i, "card_id": cid, "name": m.get("name", f"卡{cid}"),
                    "theme": m.get("theme"), "layer": m.get("layer"),
                    "game_layer": _xy_g(m.get("layer"))})
    return out


def plan_xiyou(state: dict) -> dict:
    """把 act=1 原始响应算成界面要的东西。纯函数，不联网。

    返回：
      level/theme/theme_name/maxlevel    当前大关 & 该大关的套
      run     本次探索（进行中 / 剩余秒 / 总步数 / 已收 / 未收 index 列表 / 每步奖励名）
      boxes   三个卡箱的张数与明细
      dupes   跨箱重复（同一张卡在多个箱里）＋ 单箱内同名重复 —— 这些才该卖
      gap     当前大关套还缺哪几张（跨箱去重：一张卡只算一份）
      chapters 已开启的各大关（套名 / 收集进度 / 缺口 / 是不是当前关）—— 供界面选大关
      tasks   每日任务与可领状态
    """
    lib = _xy_lib()
    level = None
    cld = state.get("currLevelData") or []
    if cld:
        level = cld[0].get("levelid")
    theme = level_theme(level or state.get("maxLevelId"))

    # ---- 探索进度
    run = {"running": False, "levelid": level, "startts": None,
           "origin_time": None, "remain": 0, "total": 0, "got": 0,
           "pending": [], "steps": []}
    if cld:
        L = cld[0]
        sd = L.get("stepData") or []
        now = int(time.time())
        start = int(L.get("startts") or 0)
        origin = int(L.get("origin_time") or 0)
        pend = []
        for s in sorted(sd, key=lambda x: int(x.get("index") or 0)):
            gifts = (s.get("gifts") or "").split("_")
            cid = int(gifts[1]) if len(gifts) > 1 and gifts[1].isdigit() else None
            step = {"index": int(s.get("index") or 0), "isget": int(s.get("isget") or 0),
                    "card_id": cid, "name": card_name(cid) if cid else gifts[0],
                    "raw": s.get("gifts")}
            run["steps"].append(step)
            if step["isget"]:
                run["got"] += 1
            else:
                pend.append(step["index"])
        run.update({"running": run["got"] < len(sd), "startts": start,
                    "origin_time": origin,
                    "remain": max(0, start + origin - now),
                    "total": len(sd), "pending": pend})
    # levelid 缺失时用顶层 maxLevelId 兜底（currLevelData 为空＝没在探索）
    if not level:
        level = state.get("maxLevelId")

    # ---- 卡箱
    keys = (("new", "new_box_data", "new_box_num"),
            ("safe", "safe_box_data", "safe_box_num"),
            ("ex", "ex_box_data", "ex_box_num"))
    boxes, all_items = {}, []
    for key, dk, nk in keys:
        items = _box_items(state.get(dk))
        cap = state.get(nk)
        boxes[key] = {"filled": len(items), "capacity": cap, "free": None,
                      "items": items}
        for it in items:
            it = dict(it, box=key)
            all_items.append(it)
    for key, _dk, nk in keys:
        n = boxes[key]["filled"]
        boxes[key]["free"] = (int(boxes[key]["capacity"]) - n) \
            if isinstance(boxes[key]["capacity"], int) else None

    # ---- 跨箱重复：同一张卡躺在不同箱里（一张只算一份，别把「凑齐」当成「两份」）
    by_cid: dict = {}
    for it in all_items:
        by_cid.setdefault(it["card_id"], []).append(it)
    dupes = [{"card_id": cid, "name": it[0]["name"],
              "boxes": [x["box"] for x in it], "count": len(it),
              "keep": {"box": it[0]["box"], "index": it[0]["index"]},
              "sell": [{"box": x["box"], "index": x["index"], "card_id": cid,
                        "name": x["name"]} for x in it[1:]]}
            for cid, it in by_cid.items() if len(it) > 1]
    # ⚠️ 不能按「名字」再判一次重复：跨箱那段已经按 **card_id** 聚合，同一张卡
    #    在同一箱里躺好几份的情况它也算进去了（探索会反复掉同一张卡），按名字
    #    再扫一遍会把 3 份的卡拆成 2 组重复、卖点指向也乱。

    # ---- 当前大关套的缺口（跨箱去重，一张卡只算一次）
    gap = {"theme": theme, "name": theme_name(theme), "have": 0, "need": 0,
           "missing": [], "layers": []}
    if theme:
        total = [c for c, m in lib.items() if m["theme"] == theme]
        have_cids = set(by_cid.keys())
        miss = []
        for cid in sorted(total):
            if cid in have_cids:
                continue
            m = lib[cid]
            miss.append({"card_id": cid, "name": m["name"],
                         "layer": m["layer"], "game_layer": _xy_g(m["layer"])})
        gap.update({"have": len(total) - len(miss), "need": len(total),
                    "missing": miss})
        lv: dict = {}
        for m in miss:
            if m["game_layer"] not in lv:
                lv[m["game_layer"]] = []
            lv[m["game_layer"]].append(m["name"])
        gap["layers"] = [{"game_layer": L, "missing": names}
                         for L, names in sorted(lv.items())]

    # ---- 大关列表（界面「大关可选」用）。西游的大关就是探索时选的 levelid：
    #      levelid=N ↔ 套 1603+N（实证）。已开启的大关 = 1..maxLevelId，全部可选；
    #      每个大关带上套名与缺口，用户可以主动「回去补第 1 大关」而不是只能打当前关。
    maxlv = 0
    try:
        maxlv = int(state.get("maxLevelId") or 0)
    except (TypeError, ValueError):
        maxlv = 0
    chapters = []
    for n in range(1, maxlv + 1):
        th = level_theme(n)
        cids = [c for c, m in lib.items() if m["theme"] == th]
        miss = [{"card_id": c, "name": lib[c]["name"], "layer": lib[c]["layer"],
                 "game_layer": _xy_g(lib[c]["layer"])}
                for c in sorted(cids) if c not in by_cid]
        chapters.append({
            "chapter": n, "levelid": n, "theme": th, "name": theme_name(th),
            "need": len(cids), "have": len(cids) - len(miss), "gap": len(miss),
            "done": bool(cids) and not miss, "current": n == level,
            "missing": miss})

    tasks = [{"id": t.get("id"), "type": t.get("type"), "cnt": t.get("cnt"),
              "done": int(t.get("cnt") or 0) > 0}
             for t in (state.get("task_detail") or [])]
    bits = int(state.get("task_bits") or 0)
    for i, t in enumerate(tasks):
        # ⚠️ bits = **已领掩码**（xiyou.har 实证：每成功领一个置一位，响应 bits
        # 1→5→7 递增）。2026-09-29 曾把它读反成「可领」——方向反了会漏领/误重领。
        t["claimed"] = bool(bits & (1 << i))
        t["claimable"] = t["done"] and not t["claimed"]

    # ⚠️ formation / logs 要跟着 plan 一起出来：探索布阵的猫和「最近探索」记录都在
    #    act=1 的 formationList / logData 里，只回 plan 的话前端这两个块永远是空的。
    return {"level": level, "theme": theme, "theme_name": theme_name(theme),
            "maxlevel": state.get("maxLevelId"), "run": run, "boxes": boxes,
            "dupes": dupes, "gap": gap, "tasks": tasks, "chapters": chapters,
            "formation": _formation_flat(state.get("formationList")),
            "logs": state.get("logData") or [],
            "power": {"freepowerbit": state.get("freepowerbit") or [],
                      "vlb": state.get("vlb")}}


# ---------------------------------------------------------------- 写操作

def _formation_flat(fl):
    """真机实证（2026-09-28）：act=1 的 formationList 是**两层嵌套** [[{catid,ce,eqlist,…},…]]，
    布阵条目在外层第 0 项里。直接透传会让前端 fl.length==1、f.catid 全 undefined。"""
    if not isinstance(fl, list) or not fl:
        return []
    return fl[0] if isinstance(fl[0], list) else fl


def _code_of(d: dict):
    if not isinstance(d, dict):
        return None
    for k in ("code", "errcode"):
        if k in d:
            try:
                return int(d[k])
            except (TypeError, ValueError):
                return None
    return None


async def _resp(session, **kw) -> dict:
    r = await session.post(EP_WORLD_EXPLORE, **kw)
    d = await _json_of(r) or {}
    # code 可能在响应顶层，也可能被 _json_of 提到「data」里 —— 两边都找一遍，
    # 只看一边会把所有成功响应判成失败（2026-09-28 踩）。
    code = _code_of(d)
    if code is None:
        code = _code_of(r if isinstance(r, dict) else {})
    body = d if code is not None else r
    return {"ok": code == 0, "data": d, "code": code,
            "desc": (body or {}).get("desc") or (body or {}).get("message"),
            "content": d.get("content") if isinstance(d, dict) else None}


async def xiyou_start(session, levelid: int, etype: int = 3,
                      etime: int = 5, teamid: int = 0) -> dict:
    """出发探索（act=5）。**消耗一次探索机会**，参数沿用 HAR 实证组合。

    ⚠️ HAR 里唯一的成功样本就是 type=3&time=5&levelid=N&teamid=0（type=3 ＝
    高级探索，用户要求「需要选高级探索」）。time 的确切含义（时长档？）未证实，
    所以沿用抓到的 5，不另造参数。
    """
    out = await _resp(session, act=5, type=etype, time=etime,
                      levelid=int(levelid), teamid=teamid)
    if out["ok"]:
        out["levelData"] = out["data"].get("levelData")
    return out


async def xiyou_get(session, levelid: int, index: int, getall: int = 0) -> dict:
    """收一个收获点（act=6）。返回 content＝奖励串（如 121_23208_1）。"""
    return await _resp(session, act=6, levelid=int(levelid),
                       index=int(index), getall=getall)


async def xiyou_sell(session, box: int, index: int, card_id: int,
                     snap: str = "", snap_num=None) -> dict:
    """卖探索卡箱里的卡（act=9）。

    box=2 ＝ 收获箱 new_box_data（HAR 实证：`cards=2_<格下标>_<卡id>`，同时要带上
    `new_box_data=2_<格下标>_<卡id>` 与 `new_box_num=<当前张数>` 两个快照参数，
    服务端拿它对卡箱做并发校验，不匹配会失败）。

    box=1 ＝ 保险箱 safe_box_data：HAR 里没有样本，按同构参数盲试
    （cards=1_位_id + safe_box_data/safe_box_num 快照）——用户 2026-09-29 明确授权
    「保险箱和收获箱加一起重复的要卖」。卖出不可逆，index/card_id 必须先读真实值。
    """
    tag = f"{int(box)}_{int(index)}_{int(card_id)}"
    kw = {"act": 9, "cards": tag}
    data_key = {2: "new_box_data", 1: "safe_box_data", 0: "ex_box_data"}.get(int(box))
    if snap:
        kw[data_key or "new_box_data"] = snap or tag
        if snap_num is not None:
            kw[{2: "new_box_num", 1: "safe_box_num", 0: "ex_box_num"}[int(box)]] = snap_num
    elif data_key and snap_num is not None:
        kw[data_key] = tag
        kw[{2: "new_box_num", 1: "safe_box_num", 0: "ex_box_num"}[int(box)]] = snap_num
    out = await _resp(session, **kw)
    if out["ok"]:
        d = out["data"]
        for k in ("ex_box_data", "ex_box_num", "new_box_data", "new_box_num",
                  "safe_box_data", "safe_box_num"):
            if k in d:
                out[k] = d[k]
    return out


async def xiyou_claim_task(session, tid: int) -> dict:
    """领每日任务奖（act=16 id=N）。零消耗，重复调用会如实失败。"""
    return await _resp(session, act=16, id=int(tid))


# ---------------------------------------------------------------- 一键

_XY_RUN_LOG = _upath(__file__).resolve().parent.parent / "data" / "xiyou_run.json"


def _save_xy_run(payload: dict) -> None:
    try:
        p = _XY_RUN_LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        arr = []
        if p.exists():
            try:
                arr = __import__("json").load(open(p, encoding="utf-8")) or []
            except Exception:  # noqa: BLE001
                arr = []
        arr.append(payload)
        arr = arr[-20:]
        with open(p, "w", encoding="utf-8") as f:
            __import__("json").dump(arr, f, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        pass


def _xy_read_box(arr, box_key: str):
    """把 act=9 响应里的卡箱数组转成 {index: card_id}。"""
    out = {}
    for i, x in enumerate(arr or []):
        cid = int(x.get("card_id") or 0) if isinstance(x, dict) else int(x or 0)
        if cid:
            out[i] = cid
    return out


async def run_xiyou(session, do_start: bool = True, do_get: bool = True,
                    do_task: bool = True, levelid=None, feed: bool = True) -> dict:
    """西游记一键：收满 24 个收获点（卡箱满先跨箱卖重复卡，低级卡先卖）
    → 出发一次新的 24h 探索 → 领每日任务奖。

    顺序按用户 2026-09-29 定调：先收完上一轮，再出发新一轮，最后领奖。
    每一步都看服务端脸色，失败即停并写明原因；结果落盘 data/xiyou_run.json。
    `levelid` = 界面上选的大关（1..maxLevelId），给了就用它出发；不给沿用当前大关。
    ⚠ 一次只能有一个进行中的探索：已在进行中的**不重复出发**（选大关也只对下次生效）。
    `feed=True`（默认）：出发前先把猫喂到 MOOD_TARGET（80）—— 西游出发属于「用到猫的一键
    操作」，按用户定调（2026-09-27「只要用到猫的一键操作都先补心情」+ 本条件）先补再出战。
    """
    st = await xiyou_state(session, want_task=True)
    if not st.get("ok"):
        return {"ok": False, "steps": [], "message": st.get("message")}
    plan = plan_xiyou(st)
    run = plan["run"]
    try:
        picked = int(levelid) if levelid else None
    except (TypeError, ValueError):
        picked = None
    level = picked or plan["level"] or plan.get("maxlevel")
    steps, notes = [], []

    # 0. 心情门槛：用到猫的一键操作前置，出发前先把猫喂到 80（不够才喂）
    if feed and do_start:
        f = await ensure_mood(session)
        if not f.get("ok"):
            msg = (f"喂猫到 {MOOD_TARGET} 失败，已中止出发（不消耗次数）："
                   f"{f.get('message') or '糖果可能不足'}")
            _save_xy_run({"ts": int(time.time()), "ok": False, "steps": steps,
                          "level": level, "message": msg})
            return {"ok": False, "steps": steps, "level": level, "message": msg}

    # 1. 收收获点（探索完成待收时；卡箱满 → 跨箱卖重复卡再继续）
    if do_get and run["pending"]:
        if run["remain"] > 0:
            # 收获点要等探索跑完才解锁（实证：探索中 act=6 返回「关卡未完成」）
            notes.append(f"探索进行中（剩约 {run['remain'] // 3600 + 1} 小时），"
                         "收获点等探索完成后的一键再收")
        else:
            idx_list = list(run["pending"])
            for i in range(len(idx_list)):
                idx = idx_list[i]
                # 收之前刷新一次当前卡箱（卖卡会改变它）
                cur = await xiyou_state(session)
                if not cur.get("ok"):
                    break
                p2 = plan_xiyou(cur)
                if idx not in [s["index"] for s in p2["run"]["steps"] if not s["isget"]]:
                    continue          # 这一步已被别人收了
                r = await xiyou_get(session, level or 0, idx)
                if r["ok"]:
                    steps.append({"step": f"收获 #{idx}", "ok": True,
                                  "note": r.get("content") or ""})
                    continue
                if r.get("code") == -30009 or "卡箱" in str(r.get("desc") or ""):
                    sold = await _xy_clear_dupes(session, p2)
                    steps.append({"step": f"收获 #{idx}（卡箱满，卖出重复 {sold} 张）",
                                  "ok": False, "note": r.get("desc") or "卡箱已满"})
                    if not sold:
                        notes.append("卡箱满了且没有可卖的重复卡，先手动清理再收")
                        break
                    r = await xiyou_get(session, level or 0, idx)
                    if r["ok"]:
                        steps.append({"step": f"收获 #{idx}（卖出后）", "ok": True,
                                      "note": r.get("content") or ""})
                    else:
                        steps.append({"step": f"收获 #{idx}", "ok": False,
                                      "note": r.get("desc") or ""})
                        break
                else:
                    # system busy ≠ 真失败（09-29 实证：#0 busy 后单发立刻成功）＝瞬时性
                    # 失败，等待重试，别一个 busy 就把整轮收获全停；连续 3 次仍 busy 才停。
                    note = str(r.get("desc") or f"code {r.get('code')}")
                    got = False
                    for _try in range(1, 4):
                        steps.append({"step": f"收获 #{idx}", "ok": False,
                                      "note": f"{note}，5s 后重试（{_try}/3）"})
                        await asyncio.sleep(5)
                        r = await xiyou_get(session, level or 0, idx)
                        if r["ok"]:
                            steps.append({"step": f"收获 #{idx}", "ok": True,
                                          "note": r.get("content") or ""})
                            got = True
                            break
                        note = str(r.get("desc") or f"code {r.get('code')}")
                    if r.get("code") == -30009 or "卡箱" in note:
                        break       # 交给上面的卡箱分支处理
                    if "未完成" in note:
                        break       # 探索没跑完、收获点没解锁，重试无意义
                    if got:
                        continue
                    steps.append({"step": f"收获 #{idx}", "ok": False, "note": note})
                    break

    # 2. 出发新的 24h 探索（收完 / 本来就没在探索时出发；进行中的不重复出发）
    if do_start:
        st2 = await xiyou_state(session)
        if st2.get("ok"):
            p2 = plan_xiyou(st2)
            if not p2["run"]["running"]:
                r = await xiyou_start(session, level or 0)
                if r["ok"]:
                    steps.append({"step": "出发探索", "ok": True,
                                  "note": r.get("content") or "已出发（高级探索，最长 24h）"})
                else:
                    steps.append({"step": "出发探索", "ok": False,
                                  "note": r.get("desc") or f"code {r.get('code')}"})
            elif p2["run"]["remain"] > 0 and not p2["run"]["pending"]:
                notes.append(f"探索进行中，约 {max(1, p2['run']['remain'] // 3600)} 小时后完成，本次不重复出发")

    # 3. 领任务奖
    if do_task:
        t = await xiyou_task_list(session)
        bits = int(t.get("task_bits") or 0)
        for i, t2 in enumerate(t.get("task_detail") or []):
            if not int(t2.get("cnt") or 0):
                continue
            if bits & (1 << i):
                continue      # bits=已领掩码（HAR 实证），领过的不再发 act=16
            r = await xiyou_claim_task(session, int(t2.get("id")))
            steps.append({"step": f"任务奖 {t2.get('id')}", "ok": r["ok"],
                          "note": r.get("content") or r.get("desc") or ""})
            # 单个任务领取失败（已领过等）继续领下一个，不因一个挡全部

    payload = {"ts": int(time.time()), "ok": any(s["ok"] for s in steps),
               "level": level, "steps": steps, "notes": notes}
    _save_xy_run(payload)
    return {"ok": True, "level": level, "steps": steps, "notes": notes,
            "log": str(_XY_RUN_LOG)}


async def _xy_clear_dupes(session, plan: dict, limit: int = 24) -> int:
    """跨箱卖重复卡腾卡箱：收获箱(new)+保险箱(safe) 合并计数，同一张卡只留 1 份，
    多余份卖掉；**低级卡先卖**（卡库层号小的先卖，用户 2026-09-29 定调）。

    - box=2（收获箱）卖法 HAR 实证；box=1（保险箱）按同构参数盲试，用户已授权。
    - 槽位卖出后原位清空不搬移（act=12 实证同构），index 不失效；张数变 →
      每卖一张本地自减 num，快照参数要跟真实张数对上。
    - 卖失败（如快照不匹配）即停本箱，调用方重读状态后会再次触发本函数。
    """
    lib = _xy_lib()
    boxes = plan.get("boxes") or {}
    sells: dict = {"new": [], "safe": []}
    for d in plan.get("dupes") or []:
        m = lib.get(int(d["card_id"])) or {}
        lv = m.get("layer")
        lv = int(lv) if isinstance(lv, int) and lv else 99   # 查不到层号排最后
        for s in d.get("sell") or []:
            bk = s["box"]
            if bk in sells:
                sells[bk].append((lv, int(s["index"]), int(s["card_id"])))
    sold = 0
    box_param = {"new": (2, "new_box_data", "new_box_num"),
                 "safe": (1, "safe_box_data", "safe_box_num")}
    for bk in ("new", "safe"):
        if not sells[bk]:
            continue
        bnum, dk, nk = box_param[bk]
        num = (boxes.get(bk) or {}).get("filled")   # 当前真实张数（快照用）
        for _lv, idx, cid in sorted(sells[bk]):     # 低级卡（层号小）先卖
            if sold >= limit:
                return sold
            r = await xiyou_sell(session, bnum, idx, cid,
                                 snap=f"{bnum}_{idx}_{cid}", snap_num=num)
            if r["ok"]:
                sold += 1
                if isinstance(num, int):
                    num -= 1
            else:
                break   # 本箱停（下轮触发会重读状态）
    return sold
