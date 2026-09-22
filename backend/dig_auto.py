"""挖地（card_user_dig）手动点格子 + 自动挖 + 鉴定机 + 道具箱 —— 2026-09-20/21 抓包 + 真机实证。

端点：POST card_user_dig（card.show.qq.com/cgi-bin/ 下可直接调，与抓包的
      card.qzone.qq.com 同源）。响应是 JSON（不是 <QQHOME> XML）。

已实证动作（全部真机 code=0）：
- **act=1**  状态/网格（只读，无参）→ dig_info/page_index/dig_index/chutou/line_tnt/
             jgg_tnt/cash/kuangshi_bi/sui_jinshi/jd_macs/new_card/new_prop/vt_*
- **act=2**  挖格子：line(=行号) col(=列号 j) dig_type
- **act=4**  鉴定机**投晶石**：type(=石头 0/1/2 = 蓝/绿/红) id(=机器序号)
             → 自动占一个空槽；实测**不扣任何 act=1 可见资源**（晶石存量接口不暴露）
- **act=7**  收鉴定机产出：id(=机器序号) slot_id(=槽位 0..4) → content 奖励串
- **act=9**  领每日道具 → content（实测：锄头×10、一字型炸药×5、九宫格炸药×1）
- **act=10** 兑换卡片：id(=文物编号) num → content `69_<卡id>_<num>`（69=挖地套卡）
- **act=15** 领每日礼包 → content（同一天连领 3 次：锄头×4 / 魔力×100 / 现金×20）
- **act=18** 一键收获：收走已露出（type=0 且带礼物）格子里的奖励 → content 奖励串

⚠️ 未接入：act 3/5/6/8/11/12/13/16/17 无参一律返 "system busy"（服务端未实现或需
   未知参数）；act ≥19 返回空包体（XML 解析失败）。只接已实证动作（项目铁律）。

## dig_type（靠资源扣减 + 网格 diff 反推，实证）
  1 = 锄头   → 扣 `chutou`，可挖 type 2(土)/3(石)/4(半开石)
  2 = 一字型炸药 → 扣 `line_tnt`，**横向 5 格（中心±2）**，边缘只造成半伤（石 3→4）
  3 = 九宫格炸药 → 扣 `jgg_tnt`，**3×3（中心±1 行列）**，并把 `dig_index` 往下推
  ⚠️ 2/3 的**落点必须是已挖开的空格 type=0**：对土/石返 -2，对岩石返 -3，
     对空地用锄头返 -1。范围内没可炸土地时客户端提示「爆炸范围内没有可炸的土地哦」。

## 格子 type（逐格实测）
  0 = 空地（已挖平）→ **带 gift_* 的就是「待收」，act=18 收**
  1 = 特殊格（挖不动，返 -1）
  2 = 土（挖 1 次 → 0）      3 = 石（挖 2 次：3→4→0）
  4 = 石头挖了一半（继续挖）  5 = 岩石（不可挖，返 -3）
⚠️ **未探索格（fog）的 gift_* 字段整个缺失**（不是 0），fog 格**挖不动**（返 -1），
   必须等相邻格被挖开把它「点亮」—— 所以要往下推进只能挖 known 格或用炸药横扫。
   → 锄头可挖 = `known and type in (2,3,4)`；炸药落点 = `known and type==0`；
     待收 = `known and type==0 and gift_type≠0`。

## 地图窗口
  服务端只给 8 行（`dig_index-5 .. dig_index+2`），`page_index` 传参**无效**（实测被忽略）。
  往下推进 = 挖更深的行 / 用炸药把 fog 行炸开。

## 奖励编号
  44=金币 45=经验 46=五彩石 47=金粉 48=卡片(编号=卡 id) 55=魔力 65=锄头 66=一字型炸药
  68=九宫格炸药 69=挖地套卡(编号=卡 id) 71=现金
  ⚠️ 70_x / 76_0 在官方静态配置（resource / propCard / activity_info / act_addon）
     **都查不到**，只能按 `#70_5` 显示；前端提供「补名字」入口写进
     data/item_names_extra.json。已知线索：当前挖地活动（activity_info id=3
     name=wadihuodong，2026-09-09~09-22）挖地可额外获得「数字 1/5/10/50」积分，
     70/76 大概率是活动数字或古文物类 —— 未实证，不猜名。

限流：连续请求返 -1/-2/-6 "system busy" → 统一节流 2.4s + busy 退避重试
      （被拒响应不带状态字段，重试不会重复扣道具）。
"""
import asyncio
import time

from . import card_query as cq
from . import dig_plan as dplan
from . import item_dict as iid

EP_DIG = "card_user_dig"

# 挖地工具 → 消耗的资源字段 + 落点要求 + 作用范围（实证）
DIG_TYPES = {
    1: {"key": "chutou", "name": "锄头", "need_open": False, "shape": "单格"},
    2: {"key": "line_tnt", "name": "一字型炸药", "need_open": True, "shape": "横向 5 格"},
    3: {"key": "jgg_tnt", "name": "九宫格炸药", "need_open": True, "shape": "3×3 共 9 格"},
}

# 可挖地形（锄头）：2=土 3=石 4=石头挖了一半（继续挖）
# ⚠️ 必须是**已探索**的格子（known）：fog 格挖不动，返 -1
CAN_DIG = (2, 3, 4)
# 不可挖
CAN_NOT_DIG = (1, 5)

# 鉴定机石头。jd_type / act=4 的 type 取值 0/1/2。
# 顺序依据：客户端 SWF 内嵌文案「获得的蓝晶石、绿晶石、红晶石可用于鉴定」的列举顺序，
# 以及每日任务「鉴定蓝晶石/绿晶石/红晶石」的排列顺序 —— **未在游戏内逐条实证**，
# 若颜色对不上，改这里即可。
JD_STONES = ["蓝晶石", "绿晶石", "红晶石"]

# 请求最小间隔（秒）。实证：3s 稳过；1.5s 会被限流返 -1 system busy。
# ⚠️ 1.8 实测仍会偶发 busy（act=1 之后立刻 act=18 必挂一次），故取 2.4
_MIN_INTERVAL = 2.4
_BUSY_CODES = {-1, -2, -3, -6}
_last_req_ts = 0.0

# 本轮遇到的「查不到名字」的奖励编号（给前端做「补名字」入口）
_unknown: set = set()


def _note_unknown(t, i):
    try:
        t, i = int(t), int(i)
    except (TypeError, ValueError):
        return
    if not iid.item_name(t, i):
        _unknown.add(f"{t}_{i}")


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


# ───────────────────────── 挖地道具名字（大类 70） ─────────────────────────
# ⚠️ act=1 的 `new_prop[].id` 是**大类 70** 的类内编号，**不是** propCard 的道具卡 id。
# 2026-09-21 实证（此前用 propCard 取名 → 挖地「道具卡」名字全错，用户抓出）：
#   - new_prop id=4/5 与挖地网格礼物 `70_4`/`70_5` 同域（绿晶石/红晶石）；
#     用户仓库截图「蓝晶石x114、绿晶石x110、红晶石x78」↔ new_prop 3/4/5 的 num 逐条吻合。
#   - propCard_v_26.xml 的 id 2~13 是商店道具卡（升炉卡/开箱卡/小魔瓶/100面值卡…），
#     与挖地道具纯属**编号撞车**，不可复用。
# 名字来源：客户端 WaDiModule_v_32.swf 内嵌文案（蓝/绿/红晶石、钉子、铁锤、铁块、铜爵…）
# 按仓库 id 升序对齐，写进 data/item_names_extra.json（人工核实层，官方静态配置查不到 70 类）。
DIG_MAT_TYPE = 70


def dig_prop_name(pid) -> str:
    """挖地道具箱 `new_prop[].id` → 名字（大类 70）。查不到返回空串（前端回落 `道具#N`）。"""
    try:
        return iid.item_name(DIG_MAT_TYPE, int(pid))
    except (TypeError, ValueError):
        return ""


# 挖地活动（activity_info id=3 `wadihuodong`）里 **48_1148 是「积分」** —— 挖地额外获得的
# 数字 1/5/10/50（活动文案「参与挖地层挖地能够额外获得数字：1，5，10，50」实证；
# 客户端 WaDiModule_v_32.swf 内嵌文案含「积分」，activity_info 的 `jf_name="积分"` 同证）。
# item_dict 通用表把 48_1148 映射成其它名字，故在**挖地上下文**里显式覆盖为「积分」。
_DIG_GIFT_OVERRIDE = {(48, 1148): "积分"}


def _gift_name(gift_type, gift_id):
    """礼物名。查不到名就退回编号显示，**绝不猜名**（项目铁律）。"""
    if not gift_type:
        return ""
    try:
        _key = (int(gift_type), int(gift_id))
    except (TypeError, ValueError):
        _key = None
    if _key in _DIG_GIFT_OVERRIDE:
        return _DIG_GIFT_OVERRIDE[_key]
    nm = iid.item_name(gift_type, gift_id)
    if nm:
        return nm
    if int(gift_type) in (48, 69):  # 48=卡片 / 69=挖地套卡，编号即卡 id
        try:
            nm = cq.cname(cq.indexes(), int(gift_id))
            if nm:
                return nm
        except Exception:  # noqa: BLE001
            pass
    # 所有来源都查不到才记为「未知」（48/69 走卡名兜底的不算未知）
    _note_unknown(gift_type, gift_id)
    return f"#{gift_type}_{gift_id}"


def _bag_view(d: dict) -> dict:
    """act=1 的 new_prop / new_card → 道具箱视图。

    `new_prop[].id` 是**大类 70** 的类内编号（见 DIG_MAT_TYPE 段注释），不是 propCard id。
    """
    props = []
    for p in (d.get("new_prop") or []):
        try:
            pid = int(p.get("id"))
        except (TypeError, ValueError):
            continue
        props.append({"id": pid, "num": int(p.get("num") or 0),
                      "name": dig_prop_name(pid) or f"道具#{pid}"})
    cards = []
    try:
        idx = cq.indexes()
    except Exception:  # noqa: BLE001
        idx = None
    for c in (d.get("new_card") or []):
        try:
            cid = int(c.get("id"))
        except (TypeError, ValueError):
            continue
        nm = ""
        if idx is not None:
            try:
                nm = cq.cname(idx, cid)
            except Exception:  # noqa: BLE001
                nm = ""
        cards.append({"id": cid, "num": int(c.get("num") or 0), "name": nm or f"卡{cid}"})
    props.sort(key=lambda x: -x["num"])
    cards.sort(key=lambda x: (-x["num"], x["id"]))
    return {
        "size": d.get("bag_size"),
        "props": props,
        "cards": cards,
        "vt": {
            "cang": {"small": d.get("vt_cang_small"), "mid": d.get("vt_cang_mid"), "big": d.get("vt_cang_big")},
            "up": {"small": d.get("vt_up_small"), "mid": d.get("vt_up_mid"), "big": d.get("vt_up_big")},
        },
    }


def _jd_view(macs: list) -> list:
    """jd_macs → 鉴定机视图（含每个槽的状态与剩余秒数）。"""
    now = int(time.time())
    out = []
    for mi, m in enumerate(macs or []):
        lv = int(m.get("lv") or 0)
        slots = []
        for s in (m.get("slots") or []):
            b = int(s.get("begin_ts") or 0)
            cost = int(s.get("cost_ts") or 0)
            jt = s.get("jd_type")
            jt_i = int(jt) if str(jt).lstrip("-").isdigit() else None
            if b <= 0:
                state, left = "空", 0
            else:
                left = max(0, b + cost - now)
                state = "待收" if left <= 0 else "鉴定中"
            slots.append({
                "id": s.get("id"),
                "jd_type": jt_i,
                "stone": JD_STONES[jt_i] if (jt_i is not None and 0 <= jt_i < len(JD_STONES)) else f"石{jt}",
                "state": state,
                "left": left,
            })
        out.append({
            "machine": mi, "lv": lv, "slots": slots,
            "free": sum(1 for s in slots if s["state"] == "空"),
            "ready": sum(1 for s in slots if s["state"] == "待收"),
        })
    return out


# ───────────────────────── 状态解析 ─────────────────────────

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
            # 超级炸药 = **格子属性**（不是可收道具）。服务端 act=1 实测不下发该字段，
            # 这里防御式探测：任一候选字段为真，或 gift_type=67。
            is_super = (int(gt or 0) == dplan.SUPER_GIFT_TYPE
                        or any(c.get(k) for k in dplan.SUPER_KEYS))
            cells.append({
                "j": c.get("j"),
                "type": t,
                "known": known,
                "gift_type": gt,
                "gift_id": gid,
                "gift_num": gn,
                "gift_name": _gift_name(gt, gid) if (known and gt) else "",
                # 锄头可挖 = 已探索 且 是土/石/半开石
                "diggable": known and t in CAN_DIG,
                # 炸药落点 = 已探索 且 已挖平的空格
                "tntable": known and t == 0,
                # 待收 = 已挖平(type 0) 且格子里有礼物 → act=18 才能收
                # ⚠️ 超级炸药(67) 收不走，不算待收
                "pending": bool(known and gt) and t == 0 and not is_super,
                "super_tnt": is_super,
            })
        rows.append({"line": int(k), "cells": cells})
    return rows


def summarize_state(d: dict) -> dict:
    """给前端的形状：资源 + 网格 + 鉴定机 + 道具箱 + 统计。"""
    rows = parse_grid(d)
    diggable = sum(1 for r in rows for c in r["cells"] if c["diggable"])
    pending = sum(1 for r in rows for c in r["cells"] if c["pending"])
    tntable = sum(1 for r in rows for c in r["cells"] if c["tntable"])
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
        "tntable_cnt": tntable,
        "pending_cnt": pending,
        "jd": _jd_view(d.get("jd_macs") or []),
        "bag": _bag_view(d),
        "unknown": sorted(_unknown),
    }


async def get_state(session) -> dict | None:
    r = await _act(session, act=1)
    if _code(r) != 0:
        return None
    return summarize_state(r.get("data") or {})


async def dig_cell(session, line: int, col: int, dig_type: int = 1) -> dict:
    """挖一格 / 扔炸药。返回 {ok, code, text, state}。"""
    dt = int(dig_type)
    info = DIG_TYPES.get(dt, DIG_TYPES[1])
    r = await _act(session, act=2, line=int(line), col=int(col), dig_type=dt)
    data = r.get("data") or {}
    code = _code(r)
    out = {"ok": code == 0, "code": code, "tool": info["name"], "line": int(line), "col": int(col)}
    if code == 0:
        out["text"] = f"({line},{col}) 用{info['name']}（{info['shape']}）→ 成功（余 {data.get(info['key'])}）"
        if "dig_info" in data:
            out["state"] = summarize_state(data)
    else:
        desc = data.get("desc") or r.get("message") or ""
        # ⚠️ code=-1 同时表示「限流 system busy」和「目标不可挖」，只能靠 desc 区分
        busy = "busy" in str(desc).lower()
        hint = "" if busy else {-3: "（岩石，挖不动）",
                                -2: "（炸药落点必须是已挖开的空格）" if dt in (2, 3) else ""}.get(code, "")
        if not busy and not hint:
            if dt == 1:
                hint = "（该格挖不了：已挖平 / 未探索 / 特殊格）"
            else:
                hint = "（炸药只能扔在已挖开的空格上，且范围内要有可炸的土地）"
        out["text"] = f"({line},{col}) 用{info['name']} → 失败 code={code} {desc}{hint}"
    if "state" not in out:
        st = await get_state(session)
        if st:
            out["state"] = st
    return out


async def push_down(session, dig_type: int = 2) -> dict:
    """往下推进地层：在最深一行的空格上扔炸药（一字型优先：横扫 5 格，一次点亮一片）。

    fog 格挖不动，只能靠把上一行炸开把它点亮 —— 这是唯一能主动往下走的手段。
    """
    st = await get_state(session)
    if st is None:
        return {"ok": False, "text": "读状态失败"}
    dt = int(dig_type)
    info = DIG_TYPES.get(dt, DIG_TYPES[2])
    if int(st.get(info["key"]) or 0) <= 0:
        return {"ok": False, "text": f"{info['name']}不足，无法推进"}
    cand = [(r["line"], c) for r in st["rows"] for c in r["cells"] if c["tntable"]]
    if not cand:
        return {"ok": False, "text": "当前窗口没有已挖开的空格可作为落点"}
    cand.sort(key=lambda x: (-x[0], abs((x[1].get("j") or 0) - 3)))
    line, c = cand[0]
    before = st.get("dig_index")
    r = await dig_cell(session, line, c["j"], dt)
    after = (r.get("state") or {}).get("dig_index")
    r["text"] = (f"{info['name']}推进 ({line},{c['j']})：" + ("成功" if r["ok"] else "失败") +
                 f"（层 {before} → {after}）")
    return r


def reward_text(content: str) -> str:
    """奖励串 → 一行可读文本。

    ⚠️ 不用 `iid.summarize`：它对 48/69（编号就是卡 id）没有卡名兜底，
    会把挖出来的卡显示成「未知道具#48_1148」。
    """
    agg: dict = {}
    for part in str(content or "").split(","):
        f = part.strip().split("_")
        if len(f) < 3:
            continue
        try:
            t, i, n = int(f[0]), int(f[1]), int(f[2])
        except ValueError:
            continue
        agg[(t, i)] = agg.get((t, i), 0) + n
    out = []
    for (t, i), n in agg.items():
        nm = iid.item_name(t, i)
        if not nm and t in (48, 69):
            try:
                nm = cq.cname(cq.indexes(), i)
            except Exception:  # noqa: BLE001
                nm = ""
        if not nm:
            _note_unknown(t, i)
            nm = f"#{t}_{i}"
        out.append(f"{nm}×{n}")
    return "、".join(out)


async def collect(session) -> dict:
    """act=18 一键收获：收走所有已露出的礼物。"""
    r = await _act(session, act=18)
    data = r.get("data") or {}
    code = _code(r)
    content = data.get("content") or ""
    return {
        "ok": code == 0, "code": code, "content": content,
        "text": reward_text(content) if content else "",
        "message": data.get("desc") or r.get("message") or "",
    }


async def daily_tools(session) -> dict:
    """act=9 领每日道具（实测：锄头×10、一字型炸药×5、九宫格炸药×1）。"""
    r = await _act(session, act=9)
    data = r.get("data") or {}
    code = _code(r)
    content = data.get("content") or ""
    return {"ok": code == 0, "code": code, "content": content,
            "text": reward_text(content) if content else (data.get("desc") or "今天已领过"),
            "message": data.get("desc") or ""}


async def daily_gift(session) -> dict:
    """act=15 领每日礼包（同一天可连领多次，每次给一样，直到领完）。"""
    r = await _act(session, act=15)
    data = r.get("data") or {}
    code = _code(r)
    content = data.get("content") or ""
    return {"ok": code == 0, "code": code, "content": content,
            "text": reward_text(content) if content else (data.get("desc") or "已领完"),
            "message": data.get("desc") or ""}


async def jd_start(session, machine: int = 0, stone: int = 0) -> dict:
    """act=4 往鉴定机投一颗晶石。machine=机器序号，stone=0蓝/1绿/2红。"""
    machine, stone = int(machine), int(stone)
    r = await _act(session, act=4, type=stone, id=machine)
    data = r.get("data") or {}
    code = _code(r)
    nm = JD_STONES[stone] if 0 <= stone < len(JD_STONES) else f"石{stone}"
    return {
        "ok": code == 0, "code": code,
        "text": f"{machine} 号鉴定机投入{nm} → " + ("已开始鉴定" if code == 0 else f"失败 {data.get('desc') or ''}"),
        "message": data.get("desc") or "",
        "jd": _jd_view(data.get("jd_macs") or []),
    }


async def jd_collect(session) -> dict:
    """act=7 收鉴定机产出：只收到期的槽。"""
    st = await get_state(session)
    if st is None:
        return {"ok": False, "text": "读状态失败"}
    items, ok_cnt = [], 0
    for m in st.get("jd") or []:
        if int(m.get("lv") or 0) <= 0:
            continue
        for s in (m.get("slots") or []):
            if s["state"] != "待收":
                continue
            r = await _act(session, act=7, id=m["machine"], slot_id=int(s["id"]))
            data = r.get("data") or {}
            code = _code(r)
            content = data.get("content") or ""
            txt = reward_text(content) if code == 0 else (data.get("desc") or f"code={code}")
            items.append({"machine": m["machine"], "slot": s["id"], "stone": s["stone"],
                          "ok": code == 0, "text": txt})
            if code == 0:
                ok_cnt += 1
    if not items:
        return {"ok": True, "count": 0, "items": [], "text": "鉴定机暂无到期产出"}
    return {
        "ok": ok_cnt > 0, "count": ok_cnt, "items": items,
        "text": f"收获鉴定 {ok_cnt}/{len(items)} 槽：" + "、".join(x["text"] for x in items if x["ok"]),
    }


collect_machines = jd_collect  # 兼容旧名字


async def jd_auto(session, stone_order=(0, 1, 2)) -> dict:
    """鉴定机一轮：先收到期的，再给每个空槽按 stone_order 依次投石头。"""
    c = await jd_collect(session)
    st = await get_state(session)
    if st is None:
        return {"ok": False, "text": "读状态失败"}
    started = []
    for m in st.get("jd") or []:
        if int(m.get("lv") or 0) <= 0:
            continue
        for i in range(int(m.get("free") or 0)):
            stone = stone_order[i % len(stone_order)]
            r = await jd_start(session, m["machine"], stone)
            started.append({"machine": m["machine"], "stone": JD_STONES[stone],
                            "ok": r["ok"], "text": r["text"]})
            if not r["ok"]:
                break
    nstart = len([x for x in started if x["ok"]])
    got = c.get("count") or 0
    return {
        "ok": True, "collected": got, "started": nstart,
        "items": (c.get("items") or []) + started,
        "text": (f"收 {got} 槽" + (f"：{c['text'].split('：', 1)[-1]}" if got else "") + f"；投石 {nstart} 次"),
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


async def auto_loop(session, max_digs: int = 50, use_tnt: bool = True,
                    collect_every: int = 8, clear_margin: int = 1):
    """自动挖循环 —— 决策全部交给 `dig_plan.plan()`（纯函数、可离线验收）。

    每轮：
      1) 有待收（已挖平 + 有礼物）→ 先 act=18 全收。**推进前必收**：行滚出窗口后
         服务端会整行压平，其上没收的礼物永久收不回来（实测踩过一次）。
      2) 规划器算下一个动作（穷举所有合法落点 × 工具，按「收益/代价」贪心）：
         先引爆格子上的超级炸药（免费；菱形 13 格，但**横/纵被石头挡住、斜角能炸石头**：
         实际命中常 < 13）> 探明 > 挖平礼物格 > 下推；
         **会丢东西的动作硬性排除**。
      3) 执行；连续失败 3 次停。

    use_tnt=False 表示只用锄头（规划器不会再选一字型/九宫格）。
      ⚠️ 超级炸药格不受此开关影响 —— 它是格子属性、不耗库存，用户明确要求优先点掉。
    clear_margin = 推进时额外要求多干净几行（1 = 多留一行缓冲）。
    """
    _auto.update({"running": True, "stop": False, "digs": 0, "collected": 0,
                  "cost": {"chutou": 0, "line_tnt": 0, "jgg_tnt": 0},
                  "msgs": [], "started": time.time(), "max_digs": max_digs,
                  "use_tnt": use_tnt, "finished": None})
    _alog(f"自动挖启动：上限 {max_digs} 次，炸药 {'开' if use_tnt else '关（只锄头）'}，"
          f"清账缓冲 {clear_margin} 行", "ok")
    failed = set()      # 失败过的 (行,列,工具)，下一轮重规划时排除，避免死磕同一个被拒动作
    try:
        while not _auto["stop"] and _auto["digs"] < max_digs:
            st = await get_state(session)
            if st is None:
                _alog("读状态失败，停止", "err")
                break
            # 1) 收：只要有待收就先收干净（这是「不丢东西」的唯一保险）
            if st["pending_cnt"] > 0:
                c = await collect(session)
                if c["ok"] and c["text"]:
                    _auto["collected"] += 1
                    _alog(f"一键收获：{c['text']}", "ok")
                elif not c["ok"]:
                    _alog(f"一键收获失败：{c['message']}", "err")
                if _auto["stop"]:
                    break
            # 2) 规划（已排除失败动作 & 爆炸探出窗口的落点）
            tools = {"chutou": st.get("chutou"), "line_tnt": st.get("line_tnt"),
                     "jgg_tnt": st.get("jgg_tnt")}
            res = dplan.plan(st["rows"], tools, use_tnt=use_tnt,
                             clear_margin=clear_margin, exclude=failed)
            act = res.get("action")
            if not act:
                _auto["finished"] = res.get("reason") or "没事可做"
                _alog(f"停止：{_auto['finished']}", "info")
                break
            # 3) 执行
            dt, line, col = act["dig_type"], act["line"], act["col"]
            r = await dig_cell(session, line, col, dt)
            if r["ok"]:
                _auto["digs"] += 1
                cost = (res.get("eval") or {}).get("cost") or 1
                key = dplan.TOOL_KEY.get(dt)
                if key:
                    _auto["cost"][key] += cost
                _auto["_fail"] = 0
                new_idx = (r.get("state") or {}).get("dig_index")
                _alog(f"{dplan.TOOL_NAME[dt]}@({line},{col}) → {res['reason']}"
                      + (f"｜地层 D{res['dig_index']}→{new_idx}" if new_idx != res["dig_index"] else ""),
                      "ok")
            else:
                # 失败：记入排除集，下一轮重规划会选别的动作（不会原地重试同一个被拒动作）。
                # 服务端对「爆炸探出窗口」的落点返 code=-1（desc="system busy"），
                # 这是「目标非法」不是限流，重试无用 → 必须排除。
                failed.add((line, col, dt))
                _alog(f"{dplan.TOOL_NAME[dt]}@({line},{col}) 失败 code={r.get('code')} → 已排除，下轮换动作",
                      "warn" if act["tier"] == 0 else "err")
                _auto["_fail"] = _auto.get("_fail", 0) + 1
                if _auto["_fail"] >= 3:
                    _auto["finished"] = "连续 3 个不同动作失败，疑似被限流，停止"
                    _alog(_auto["finished"], "err")
                    break
                continue
    finally:
        _auto["running"] = False
        _alog(f"自动挖结束：共挖 {_auto['digs']} 次，收获 {_auto['collected']} 次", "ok")


def start_auto(session, max_digs: int = 50, use_tnt: bool = True,
               clear_margin: int = 1) -> dict:
    if _auto["running"]:
        return {"ok": False, "message": "自动挖已在运行"}
    _auto["_fail"] = 0
    asyncio.ensure_future(auto_loop(session, max_digs, use_tnt, 8, clear_margin))
    return {"ok": True, "message": f"已启动（上限 {max_digs} 次）"}



def stop_auto() -> dict:
    if not _auto["running"]:
        return {"ok": False, "message": "自动挖未在运行"}
    _auto["stop"] = True
    return {"ok": True, "message": "已发出停止信号"}
