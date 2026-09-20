"""挖地（card_user_dig）手动点格子 + 自动挖 —— 2026-09-20 HAR 抓包 + 真机实证。

端点：POST card_user_dig（card.show.qq.com/cgi-bin/ 下可直接调，与抓包的
      card.qzone.qq.com 同源）。响应是 JSON（不是 <QQHOME> XML）。

已实证动作（全部真机 code=0）：
- **act=1**  状态/网格（只读，无参）→ dig_info/page_index/dig_index/chutou/line_tnt/
             jgg_tnt/cash/kuangshi_bi/sui_jinshi/jd_macs/new_card/new_prop
- **act=2**  挖格子：line(=行号) col(=列号 j) dig_type
             dig_type=1 锄头   → 扣 chutou，可挖 type 2(土)/3(石)
             dig_type=2 一字型炸药 → 扣 line_tnt
             dig_type=3 九宫格炸药 → 扣 jgg_tnt（大范围，会把地层整屏往下推）
             ⚠️ 2/3 只能用在**已挖开的空格(type=0)**；对 type=2 的土返 **-2**，
                对 type=5 的岩石返 **-3**，对 type=0 用锄头返 **-1**。
- **act=18** 一键收获：把已露出（type=0/4 且带礼物）的格子里的奖励收进包
             → content = "大类_编号_数量,..."（同全局奖励串格式）
- **act=7**  收矿机产出：id(=矿机序号，实测只 0 号机有槽位) slot_id(=槽位 0..4)
             → content 奖励串
- **act=4**  启动矿机槽位：type(0/1/2) id(=矿机序号) → jd_macs 里该槽 begin_ts 被填上

格子 type 语义（2026-09-20 逐格实测，客户端 SWF 常量 CAN_DIG_TYPE/CAN_NOT_DIG_TYPE 佐证）：
  0 = 空地（已挖平）→ **带 gift_* 的就是「待收」，由 act=18 收走**
  1 = 特殊格（挖不动，返 -1）
  2 = 土（挖 1 次 → 变 0）
  3 = 石（挖 1 次 → 变 **4**；再挖 1 次 → 变 0。即石头要挖 2 次）
  4 = 石头挖了一半（**继续挖**，不是「待收」）
  5 = 岩石（不可挖，返 -3）
⚠️ **未探索格子的 gift_* 字段整个缺失**（不是 0）：fog 格 **挖不动**（返 -1），
   必须等相邻格被挖开把它"点亮"后才能挖 —— 自动挖只能挑 known 的格子。
   → 可挖 = `known and type in (2,3,4)`；待收 = `known and type==0 and gift_type≠0`。

限流：连续请求会返 -1 "system busy"（实测 3 秒间隔稳定通过，1.5 秒会挂）→ 统一节流 + busy 退避重试。

奖励编号（item_dict 实证）：44=金币 45=经验 46=五彩石 47=金粉 48=卡片(编号=卡 id)
  55=魔力 65=锄头 66=一字型炸药 68=九宫格炸药 69=挖地套卡 71=现金

铁律：只发已实证动作；自动循环默认**只用锄头**，炸药需显式开启。
"""
import asyncio
import time

from . import card_query as cq
from . import item_dict as iid

EP_DIG = "card_user_dig"

# 挖地工具 → 消耗的资源字段（实证：dig_type=1 扣 chutou / 2 扣 line_tnt / 3 扣 jgg_tnt）
DIG_TYPES = {
    1: {"key": "chutou", "name": "锄头", "need_open": False},
    2: {"key": "line_tnt", "name": "一字型炸药", "need_open": True},
    3: {"key": "jgg_tnt", "name": "九宫格炸药", "need_open": True},
}

# 可挖地形（锄头）：2=土 3=石 4=石头挖了一半（继续挖）
# ⚠️ 必须是**已探索**的格子（known）：fog 格挖不动，返 -1
CAN_DIG = (2, 3, 4)
# 不可挖
CAN_NOT_DIG = (1, 5)

# 请求最小间隔（秒）。实证：3s 稳过；1.5s 会被限流返 -1 system busy。
# ⚠️ 1.8 实测仍会偶发 busy（act=1 之后立刻 act=18 必挂一次），故取 2.4
_MIN_INTERVAL = 2.4
_BUSY_CODES = {-1, -2, -3, -6}
_last_req_ts = 0.0


async def _throttle():
    global _last_req_ts
    now = time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_req_ts)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_req_ts = time.monotonic()


def _code(r: dict):
    """取业务 code。⚠️ 不能 `int(x or -1)` —— 0 会被短路成 -1（项目已踩过）。"""
    d = r.get("data") if isinstance(r.get("data"), dict) else None
    for src in (d, r):
        if isinstance(src, dict) and src.get("code") is not None:
            try:
                return int(src["code"])
            except (TypeError, ValueError):
                continue
    return None


async def _act(session, retries: int = 3, **params) -> dict:
    """发 card_user_dig 动作：节流 + 对限流(busy)退避重试。

    ⚠️ 只对「系统繁忙」重试：被拒的响应**不带状态字段**（chutou/grid 都没有），
    说明动作没执行，重试不会重复扣道具。
    """
    last = None
    for i in range(retries):
        await _throttle()
        r = await session.post(EP_DIG, **params)
        code = _code(r)
        if code == 0:
            return r
        last = r
        msg = str((r.get("data") or {}).get("desc") or r.get("message") or "").lower()
        if code in _BUSY_CODES or "busy" in msg:
            await asyncio.sleep(1.5 * (i + 1))
            continue
        return r  # 非限流错误，不重试
    return last


# ───────────────────────── 状态解析 ─────────────────────────

def _gift_name(gift_type, gift_id):
    """礼物名。查不到名就退回编号显示，**绝不猜名**（项目铁律）。"""
    if not gift_type:
        return ""
    nm = iid.item_name(gift_type, gift_id)
    if nm:
        return nm
    if int(gift_type) == 48:  # 48 = 卡片，编号即卡 id
        try:
            return cq.cname(cq.indexes(), int(gift_id))
        except Exception:  # noqa: BLE001
            return f"卡{gift_id}"
    return f"#{gift_type}_{gift_id}"


def parse_grid(d: dict) -> list:
    """act=1 的 dig_info → 行列表（行号升序，每行 7 格，列号 j 升序）。"""
    di = d.get("dig_info") or {}
    rows = []
    for k in sorted(di, key=lambda x: int(x)):
        cells = []
        for c in sorted(di[k], key=lambda x: (x.get("j") or 0)):
            known = "gift_type" in c  # 未探索格子整个字段缺失
            t = c.get("type")
            gt = c.get("gift_type") if known else None
            gid = c.get("gift_id") if known else None
            gn = c.get("gift_num") if known else None
            cells.append({
                "j": c.get("j"),
                "type": t,
                "known": known,
                "gift_type": gt,
                "gift_id": gid,
                "gift_num": gn,
                "gift_name": _gift_name(gt, gid) if (known and gt) else "",
                "diggable": known and t in CAN_DIG,
                # 待收 = 已挖平(type 0) 且格子里有礼物 → act=18 才能收；
                # type 3/4 上"看得到"的礼物还没挖出来，收不到。
                "pending": bool(known and gt) and t == 0,
            })
        rows.append({"line": int(k), "cells": cells})
    return rows


def summarize_state(d: dict) -> dict:
    """给前端的形状：资源 + 网格 + 统计。"""
    rows = parse_grid(d)
    diggable = sum(1 for r in rows for c in r["cells"] if c["diggable"])
    pending = sum(1 for r in rows for c in r["cells"] if c["pending"])
    return {
        "rows": rows,
        "page_index": d.get("page_index"),
        "dig_index": d.get("dig_index"),
        "chutou": d.get("chutou"),
        "line_tnt": d.get("line_tnt"),
        "jgg_tnt": d.get("jgg_tnt"),
        "cash": d.get("cash"),
        "kuangshi_bi": d.get("kuangshi_bi"),
        "sui_jinshi": d.get("sui_jinshi"),
        "ksb_chou_cnt": d.get("ksb_chou_cnt"),
        "bag_size": d.get("bag_size"),
        "diggable_cnt": diggable,
        "pending_cnt": pending,
        "jd_macs": d.get("jd_macs") or [],
    }


async def get_state(session) -> dict | None:
    r = await _act(session, act=1)
    if _code(r) != 0:
        return None
    return summarize_state(r.get("data") or {})


async def dig_cell(session, line: int, col: int, dig_type: int = 1) -> dict:
    """挖一格。返回 {ok, code, text, state}；state 是新网格（来自响应或回读）。"""
    dt = int(dig_type)
    info = DIG_TYPES.get(dt, DIG_TYPES[1])
    r = await _act(session, act=2, line=int(line), col=int(col), dig_type=dt)
    data = r.get("data") or {}
    code = _code(r)
    out = {"ok": code == 0, "code": code, "tool": info["name"], "line": int(line), "col": int(col)}
    if code == 0:
        out["text"] = f"({line},{col}) 用{info['name']}挖 → 成功（余 {data.get(info['key'])}）"
        if "dig_info" in data:
            out["state"] = summarize_state(data)
    else:
        desc = data.get("desc") or r.get("message") or ""
        # ⚠️ code=-1 同时表示「限流 system busy」和「目标不可挖」，只能靠 desc 区分
        busy = "busy" in str(desc).lower()
        hint = "" if busy else {
            -3: "（岩石，挖不动）",
        }.get(code, "")
        if not busy and not hint:
            if dt == 1:
                hint = "（该格挖不了：已挖平 / 未探索 / 特殊格）"
            elif dt in (2, 3):
                hint = "（炸药只能扔在已挖开的空格上）"
        out["text"] = f"({line},{col}) 用{info['name']}挖 → 失败 code={code} {desc}{hint}"
    if "state" not in out:
        st = await get_state(session)
        if st:
            out["state"] = st
    return out


async def collect(session) -> dict:
    """act=18 一键收获：收走所有已露出的礼物。"""
    r = await _act(session, act=18)
    data = r.get("data") or {}
    code = _code(r)
    content = data.get("content") or ""
    return {
        "ok": code == 0,
        "code": code,
        "content": content,
        "text": iid.summarize(content) if content else "",
        "message": data.get("desc") or r.get("message") or "",
    }


async def collect_machines(session) -> dict:
    """act=7 收矿机：只处理 0 号机（实测只有它有 lv>0 和槽位），且只收到期的槽。

    jd_macs[i].slots[]：begin_ts>0 且 now >= begin_ts + cost_ts 才可收。
    """
    st = await get_state(session)
    if st is None:
        return {"ok": False, "text": "读状态失败"}
    now = int(time.time())
    items, ok_cnt = [], 0
    for mi, m in enumerate(st.get("jd_macs") or []):
        if not m or int(m.get("lv") or 0) <= 0:
            continue
        for s in (m.get("slots") or []):
            b = int(s.get("begin_ts") or 0)
            cost = int(s.get("cost_ts") or 0)
            if b <= 0 or now < b + cost:
                continue
            r = await _act(session, act=7, id=mi, slot_id=int(s.get("id")))
            data = r.get("data") or {}
            code = _code(r)
            txt = iid.summarize(data.get("content") or "") if code == 0 else (data.get("desc") or f"code={code}")
            items.append({"machine": mi, "slot": s.get("id"), "ok": code == 0, "text": txt})
            if code == 0:
                ok_cnt += 1
    if not items:
        return {"ok": True, "count": 0, "items": [], "text": "矿机暂无到期产出"}
    return {
        "ok": ok_cnt > 0, "count": ok_cnt, "items": items,
        "text": f"收矿机 {ok_cnt}/{len(items)} 槽：" + "、".join(f"{x['text']}" for x in items if x["ok"]),
    }


# ───────────────────────── 自动挖 ─────────────────────────

_auto = {
    "running": False,
    "stop": False,
    "digs": 0,
    "collected": 0,
    "cost": {"chutou": 0, "line_tnt": 0, "jgg_tnt": 0},
    "msgs": [],
    "started": 0.0,
    "max_digs": 0,
    "use_tnt": False,
    "finished": None,
}
_AUTO_LOG_MAX = 60


def auto_status() -> dict:
    return {
        "running": _auto["running"],
        "digs": _auto["digs"],
        "collected": _auto["collected"],
        "cost": dict(_auto["cost"]),
        "max_digs": _auto["max_digs"],
        "use_tnt": _auto["use_tnt"],
        "msgs": list(_auto["msgs"][-_AUTO_LOG_MAX:]),
        "finished": _auto["finished"],
    }


def _alog(text: str, kind: str = "info"):
    _auto["msgs"].append({"ts": int(time.time()), "text": text, "kind": kind})
    if len(_auto["msgs"]) > _AUTO_LOG_MAX:
        _auto["msgs"] = _auto["msgs"][-_AUTO_LOG_MAX:]


def _pick_cell(rows: list) -> tuple | None:
    """选下一格：**最深行优先**（往下推进地层才能出新区域），同深度优先带礼物的。"""
    cand = [(r["line"], c) for r in rows for c in r["cells"] if c["diggable"]]
    if not cand:
        return None
    cand.sort(key=lambda x: (-x[0], 0 if x[1].get("gift_type") else 1, x[1].get("j") or 0))
    line, c = cand[0]
    return line, c["j"]


def _pick_open_cell(rows: list) -> tuple | None:
    """炸药用：挑一个已挖开的空格（同样取最深行，炸出新区域概率最大）。"""
    cand = [(r["line"], c) for r in rows for c in r["cells"] if c["type"] == 0]
    if not cand:
        return None
    cand.sort(key=lambda x: (-x[0], x[1].get("j") or 0))
    return cand[0][0], cand[0][1]["j"]


async def auto_loop(session, max_digs: int = 50, use_tnt: bool = False,
                    collect_every: int = 8):
    """自动挖循环。默认只用锄头；use_tnt=True 才会在没土可挖时扔九宫格炸药推进。"""
    _auto.update({"running": True, "stop": False, "digs": 0, "collected": 0,
                  "cost": {"chutou": 0, "line_tnt": 0, "jgg_tnt": 0},
                  "msgs": [], "started": time.time(), "max_digs": max_digs,
                  "use_tnt": use_tnt, "finished": None})
    _alog(f"自动挖启动：上限 {max_digs} 次，炸药 {'开' if use_tnt else '关'}", "ok")
    try:
        while not _auto["stop"] and _auto["digs"] < max_digs:
            st = await get_state(session)
            if st is None:
                _alog("读状态失败，停止", "err")
                break
            # 1) 有露出的礼物先收（每 collect_every 次挖也强制收一次）
            if st["pending_cnt"] > 0 or (_auto["digs"] and _auto["digs"] % collect_every == 0):
                c = await collect(session)
                if c["ok"] and c["text"]:
                    _auto["collected"] += 1
                    _alog(f"一键收获：{c['text']}", "ok")
                elif not c["ok"]:
                    _alog(f"一键收获失败：{c['message']}", "err")
                if _auto["stop"]:
                    break
            # 2) 选格子
            pick = _pick_cell(st["rows"])
            if pick:
                line, col = pick
                if int(st.get("chutou") or 0) <= 0:
                    _alog("锄头用尽，停止", "err")
                    break
                r = await dig_cell(session, line, col, 1)
                if r["ok"]:
                    _auto["digs"] += 1
                    _auto["cost"]["chutou"] += 1
                else:
                    _alog(r["text"], "err")
                    # 换个格子继续；连续失败 3 次就停
                    _auto["_fail"] = _auto.get("_fail", 0) + 1
                    if _auto["_fail"] >= 3:
                        _alog("连续失败 3 次，停止", "err")
                        break
                    continue
                _auto["_fail"] = 0
                continue
            # 3) 没土可挖：可选扔九宫格炸药（只能炸已挖开的空格）
            if use_tnt and int(st.get("jgg_tnt") or 0) > 0:
                oc = _pick_open_cell(st["rows"])
                if oc:
                    r = await dig_cell(session, oc[0], oc[1], 3)
                    if r["ok"]:
                        _auto["digs"] += 1
                        _auto["cost"]["jgg_tnt"] += 1
                        _alog(f"扔九宫格炸药 ({oc[0]},{oc[1]}) → 地层推进（余 {r.get('state', {}).get('jgg_tnt')}）", "ok")
                        continue
                    _alog(r["text"], "err")
            _auto["finished"] = "没有可挖的格子了"
            _alog("没有可挖的格子了，停止", "info")
            break
    finally:
        _auto["running"] = False
        _alog(f"自动挖结束：共挖 {_auto['digs']} 次，收获 {_auto['collected']} 次", "ok")


def start_auto(session, max_digs: int = 50, use_tnt: bool = False) -> dict:
    if _auto["running"]:
        return {"ok": False, "message": "自动挖已在运行"}
    _auto["_fail"] = 0
    asyncio.ensure_future(auto_loop(session, max_digs, use_tnt))
    return {"ok": True, "message": f"已启动（上限 {max_digs} 次）"}


def stop_auto() -> dict:
    if not _auto["running"]:
        return {"ok": False, "message": "自动挖未在运行"}
    _auto["stop"] = True
    return {"ok": True, "message": "已发出停止信号"}
