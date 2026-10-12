"""FastAPI 入口：薄代理 + 扫码登录 + 静态前端。

职责（刻意保持轻量，不是重型后端）：
1. 提供静态前端（纯 HTML/JS，QQ 空间风格登录界面）
2. 代理 ptlogin 扫码登录（绕开浏览器 CORS）
3. 代理魔法卡片接口（带 cookie + g_tk 签名，待探明真实端点）

不使用任何 Web 框架的重量级特性，仅 FastAPI + StaticFiles。
"""
import asyncio
import base64
import html
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse

from .auth_qr import QRLogin
from .mc_api import LoginSession
from . import card_query as cq
from . import card_plan
from . import economy as ec
from . import item_dict as iid
from . import prop_dict as pd
from . import spirite_maps as spm
from . import spirite_progress as spg
from .auto_refine import AutoRefine, parse_full
from . import daily
from . import gift as gf
from . import travel_elf_auto as te
from . import dig_auto as dg
from . import match as mt
from . import xiyou as xy
from . import cathome as ch
from . import zcg as zcg_mod
from . import cat_dress as cd
from . import buylog
from .constants import (EP_BUY, EP_COLLECTION_GET, EP_MAINPAGE, EP_MINI_GET, EP_PROPS_INFO,
                        EP_RANDOM_GET, EP_REGISTER, EP_SPIRITE, EP_USER_LIST,
                        EP_WORLD_EXPLORE, EP_WORLD_SANGUO)
from .spirite_auto import SPIRITE_MAPS, get_engine, plain_uin, MODES

# 复用 mc_api 里已配好 handler 的 logger（那边 propagate=False，root 不带 handler）
logger = logging.getLogger("mc.api")

app = FastAPI(title="魔法卡片助手")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# 单用户 demo：登录态持久化到本地文件，重启不丢
_qr = QRLogin()
_session = LoginSession()
_session.restore()

# 自动炼卡引擎（单例：同一时刻只跑一套卡）
_auto: AutoRefine | None = None

# 礼物箱→变卡 自动循环（单例）


# 二维码缓存：避免页面刷新/多端取码互相作废
_qr_cache: dict = {"b64": "", "ts": 0.0}
QR_TTL = 100.0
# 单飞锁：避免并发 login_qr 同时调 get_qr 产生两个不同 qrsig，导致前端显示的码与 poll 用的码错位
_qr_gen_lock = asyncio.Lock()


@app.get("/api/login/qr")
async def login_qr(force: int = 0):
    """取二维码。

    关键修复（2026-09-11，用户报告「一扫就失效但手机已登录」）：
    - force=1 **始终**重新生成新码，不复用缓存。
      之前用 `(force and _qr.used)` 短路复用——但当上一会话的 qrsig 已被手机确认
      （腾讯侧标记为 consumed）、而我们漏掉了 code 0 窗口导致 `_qr.used` 仍是 False
      时，跨页面复用的就是一张"已死"的码，用户扫它必然 67 → 误报"已失效"。
    - 加 _qr_gen_lock 单飞：连续点击刷新按钮 / 初始化 + forceLogin 并发触发时，
      串行化 get_qr，确保前端拿到的码与 poll 用的 _qr.qrsig 始终一致。
    - 响应里带 qrsig_tail，前端可与 poll 响应对照排错。
    """
    async with _qr_gen_lock:
        now = time.time()
        valid = _qr_cache["b64"] and (now - _qr_cache["ts"]) <= QR_TTL
        need_new = (not valid) or bool(force)
        if need_new:
            png = await _qr.get_qr()
            _qr_cache["b64"] = base64.b64encode(png).decode()
            _qr_cache["ts"] = now
            logger.info("login_qr new qrsig=%s force=%s", (_qr.qrsig or "")[-8:], force)
    return {
        "qr": f"data:image/png;base64,{_qr_cache['b64']}",
        "age": round(time.time() - _qr_cache["ts"], 1),
        "qrsig_tail": (_qr.qrsig or "")[-8:],
    }


@app.get("/api/login/poll")
async def login_poll():
    """轮询扫码状态；确认后把 cookie 载入会话。"""
    res = await _qr.poll()
    if res.get("status") == "ok":
        _session.load_cookies(res["cookies"])
        # 登录成功后作废二维码缓存，避免下次进登录页复用已扫/已确认过的旧 sig
        _qr_cache["b64"] = ""
        skey_domains = sorted({k for k in res["cookies"] if "skey" in k.lower()})
        return {"status": "ok", "uin": res.get("uin"), "skey_domains": skey_domains,
                "qrsig_tail": (_qr.qrsig or "")[-8:]}
    # 非 ok 也带 qrsig_tail，前端可与 login_qr 响应对照，确认前后端用的是同一张码
    res["qrsig_tail"] = (_qr.qrsig or "")[-8:]
    return res


@app.get("/api/login/quick")
async def login_quick():
    """本机 QQ 快速登录（best-effort）。

    浏览器网页读不到 qq.com 的登录 cookie（跨源隔离），真正的一键令牌交换
    依赖腾讯客户端私有协议 / 扩展，纯 web + 薄代理在 macOS 上通常不可用。
    这里诚实返回可用性，前端据此展示「唤起本机 QQ」或降级提示。
    """
    return {
        "available": False,
        "reason": "本机QQ一键登录依赖腾讯客户端私有本地协议（主要为 Windows QQ 客户端），"
                  "纯 web + 薄代理在 macOS 上无法读取其登录令牌。可点击「唤起本机 QQ」尝试，"
                  "但登录仍需在腾讯自己的登录页完成。当前环境建议使用扫码登录。",
    }


def _session_expired(d: dict) -> bool:
    """腾讯侧会话失效（skey 过期）的判据：返回码 -1001 或提示「重新登陆」。"""
    if not isinstance(d, dict) or d.get("ok"):
        return False
    return d.get("code") == -1001 or "重新登陆" in str(d.get("message", "")) \
        or "重新登录" in str(d.get("message", ""))


@app.get("/api/me")
async def me():
    """返回真实登录态：内存标记为真不代表腾讯会话仍有效（skey 会过期）。

    关键：严格区分两类失败，避免把「本地连接断了」误判成「会话过期」——
    前者只应提示重试、保持登录态，后者才强制重新扫码。
      - 探测时连接层抛异常（本地连接断了 / 腾讯不可达）→ probe=conn_error，保持登录态
      - 腾讯正常响应但业务码 -1001 / 请重新登陆 → probe=expired，清登录态
    """
    if not _session.logged_in:
        return {"logged_in": False}
    try:
        st = await _session.get_state()
    except Exception as e:  # noqa: BLE001
        # 连接层失败：保持登录态，仅本次不校验（用户 2026-09-11：连接断了≠过期）
        return {"logged_in": True, "uin": _session.uin, "probe": "conn_error",
                "note": f"会话探测失败（连接/网络），保持原登录态：{e}"}
    if _session_expired(st):
        _session.clear()
        return {"logged_in": False, "expired": True, "probe": "expired",
                "code": st.get("code"), "raw": str(st.get("raw", ""))[:120]}
    return {"logged_in": True, "uin": _session.uin, "probe": "ok"}


@app.api_route("/api/logout", methods=["GET", "POST"])
async def logout():
    _session.clear()
    return {"ok": True}


def _require_login():
    if not _session.logged_in:
        # need_login 让前端明确区分「服务端无登录态」与「连接错误」，直接弹码而非误判为网络抖动
        return JSONResponse({"code": -1, "message": "未登录", "need_login": True}, status_code=401)
    return None


# ---- 魔法卡片接口代理（端点为 2014 参考值，登录后需抓包校准）----
@app.get("/api/refine/get")
async def refine_get(slotid: int):
    """取出指定炉位已炼好的卡。取卡成功后炉位清空、卡进入卡箱。

    两种炉位走不同端点（2026-09-09 实测结论）：
    - 普通炉（自己炼）：card_stove_refinedcard_get，slottype=1。
    - 偷炉（用好友空炉炼自己的卡，card@slottype="steal" 且带 opuin=好友uin）：
      card_stove_stealcard_get，且**必须带 opuin=好友uin**，否则服务端拒。
      普通 refinedcard_get 端点对偷炉永远返回 -32001，不可混用。
    """
    if err := _require_login():
        return err
    try:
        st = await _session.get_state()
        hit = next((c for c in (st.get("stovebox") or {}).get("cards") or []
                    if c.get("slot") == slotid), None)
        if not hit or not hit.get("id"):
            logger.info("取卡 slot=%s 失败：炉位没有已炼好的卡", slotid)
            return {"ok": False, "code": -1, "message": f"炉位 {slotid} 没有已炼好的卡"}
        if hit.get("flag") != 2:
            logger.info("取卡 slot=%s 失败：还在炼制中（flag=%s）", slotid, hit.get("flag"))
            return {"ok": False, "code": -1, "message": "该卡还在炼制中，未到可取时间"}
        is_steal = (hit.get("slottype") == "steal"
                    or (hit.get("opuin") and str(hit.get("opuin")) != str(_session.uin or "")))
        if is_steal:
            # 偷炉：必须带好友 opuin
            r = await _session.get_steal_card(slotid, hit.get("opuin"))
        else:
            r = await _session.get_refined_card(slotid)
        logger.info("取卡 slot=%s 分支=%s card_id=%s → ok=%s msg=%s",
                    slotid, "steal" if is_steal else "self", hit.get("id"),
                    r.get("ok"), r.get("message"))
        # 翻牌：取自己炼的卡会派生 1 次翻牌机会，且是**临时态**——必须紧接着取卡就翻，
        # 过一会儿再翻服务端只会返回 system busy（机会白白丢掉）。
        # 偷炉是否同样派生机会未经实测，故两条分支都试一次：没有待翻状态时只会拿到
        # system busy，无副作用、不会误消耗。
        if r.get("ok"):
            pa = await _session.open_pai()
            pmsg = pa.get("message") or ""
            r["pai"] = pa.get("ok")
            r["pai_message"] = pmsg
            r["pai_items"] = pa.get("items") or []
            logger.info("取卡后自动翻牌 slot=%s pai_ok=%s code=%s msg=%s",
                        slotid, pa.get("ok"), pa.get("code"), pmsg)
            if pa.get("ok"):
                r["message"] = (r.get("message") or "") + f"；翻牌成功{pmsg and '（' + pmsg + '）' or ''}"
        return r
    except Exception as e:  # noqa: BLE001
        logger.exception("取卡异常 slotid=%s", slotid)
        return {"ok": False, "message": f"取卡异常：{e}"}


@app.get("/api/random")
async def random_get():
    """随机抽卡：抽一次（type=2，一次减1）。"""
    if err := _require_login():
        return err
    return await _session.draw_random_cards(fetch_type=2)


@app.post("/api/random/draw")
async def random_draw_all():
    """一键抽完所有随机抽卡次数，并自动卖掉 10 面额卡（SSE 流式）。"""
    if err := _require_login():
        return err

    async def gen():
        try:
            async for step in _session.auto_random_draw_and_sell():
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'运行异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/pai/open")
async def pai_open():
    """手动翻牌一次（card_pai_open type=1）。"""
    if err := _require_login():
        return err
    return await _session.open_pai()


@app.post("/api/box/sell")
async def box_sell(card_id: int, slot: int, box_type: int = 1):
    """卖卡：card_id + slot + box_type（1=换卡箱 / 2=保险箱）。

    保险箱（2）的卡不能直接卖，必须先移回换卡箱（2026-09-10 用户确认的业务规则），
    这里做后端拦截，防止绕过前端直接发请求。
    """
    if err := _require_login():
        return err
    if box_type == 2:
        return {"ok": False, "code": -1,
                "message": "保险箱的卡不能直接卖，请先移回换卡箱"}
    return await _session.sell_card(slot, card_id, box_type)


@app.post("/api/box/move")
async def box_move(card_id: int, slot: int, from_box: int):
    """移动卡：from_box 0=换卡箱 / 1=保险箱，移动到另一个箱。"""
    if err := _require_login():
        return err
    return await _session.move_card(card_id, slot, from_box)



# TODO: 开始炼卡端点需抓包确认（EP_REFINE="card_stove_refine"）
@app.get("/api/refine/start")
async def refine_start():
    return {"code": -1, "message": "开始炼卡端点待抓包确认（见 README）"}


# ---- 自动炼卡（后台循环：买卡 → 炼卡 → 收卡 → 集齐上交）----
async def _free_slots() -> dict | None:
    """抓一次主页面，返回换卡箱/保险箱各自剩余可放格数（cur - used）。"""
    mp = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
    if mp.get("code") != 0:
        return None
    st = parse_full(mp.get("raw", ""))
    return {k: max(0, st["cap"].get(k, {}).get("cur", 0) - st["cap"].get(k, {}).get("used", 0))
            for k in ("change", "store")}


@app.get("/api/auto/status")
async def auto_status():
    if err := _require_login():
        return err
    if _auto is None:
        return {"running": False}
    return _auto.status()


@app.post("/api/auto/start")
async def auto_start(tid: int = 0, tids: str = "", buy: int = 1, rounds: int = 0,
                    steal: int = 1, steal_friend: str = "",
                    gift_drain: int = 0, gift_dst: int = 0,
                    gift_upper: int = 0, gift_lower: int = 0,
                    sets: int = 0):
    """启动自动炼卡（**可多套并行**）。先按**实时库存算净需求**做启动预检。

    · `tids` = 逗号分隔的套卡 id 列表（队列，按序轮转，空着的炉位给各套分着用）；
      兼容旧参数 `tid`（单套）。
    · `rounds` = **每套**集齐上限，0 = 不限。某套达标后自动移出队列，队列空即停止。
      队列里有几套就炼几套 —— 长炉时（如魏书 10h）炉位不会闲着。
    · `sets` = **套数设置**（每轮排几套，2026-10-03 用户定调）：
      0 = 默认，**一直炼、不空卡炉** —— 1 星套按「当前囤的 40 面卡能炼几套顶层卡」
      自动算（炉位能排多少排多少；缺料引擎自己变卡/买底卡补）；其他星仍按 1 套。
      >0 = 固定每轮排这么多套。

    净需求口径（2026-09-16 改）：只补「凑齐一套还缺的卡」，已有的成品卡直接抵扣，
    不再像旧版那样按「全套从零」展开——旧口径会把已持有卡那份素材需求全额压到
    最底层，补缺口时多炼近 10 倍。
    """
    global _auto  # 必须声明：下面会给模块级单例赋值，否则读取时 UnboundLocalError
    if err := _require_login():
        return err
    ids = [int(x) for x in re.split(r"[,\s]+", (tids or "").strip()) if x.strip().isdigit()]
    if tid:
        ids.insert(0, int(tid))
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {"ok": False, "message": "请至少选一套卡（tid 或 tids）"}
    try:
        eng = AutoRefine(_session, ids, buy=bool(buy), max_rounds=rounds)
    except Exception as exc:  # noqa: BLE001  (tid 不存在等)
        return {"ok": False, "message": f"无法加载该套卡计划：{exc}"}
    eng.surplus_sets = max(0, int(sets or 0))   # 套数设置：0=自动（1星按囤卡多套）
    eng.steal_enabled = bool(steal)
    if steal_friend.strip():
        eng.preferred_steal_friend = steal_friend.strip()
    # 礼物箱 → 变卡（可选环节）：勾选后，每 tick 在「缺素材、准备买卡」之前先看礼物箱，
    # 箱件数 ≥ gift_upper 就收礼+变卡到 gift_lower，变出的 40 面卡直接当素材（省掉买卡+炼卡）。
    eng.gift_drain = bool(gift_drain)
    if gift_dst:
        eng.gift_dst = int(gift_dst)
    if gift_upper:
        eng.gift_upper = int(gift_upper)
    if gift_lower:
        eng.gift_lower = int(gift_lower)

    mp = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
    if mp.get("code") != 0:
        return {"ok": False, "message": "读取卡箱状态失败，稍后重试"}
    st = parse_full(mp.get("raw", ""))
    stock: Counter = Counter()
    for c in st["change"] + st["store"]:
        stock[c["id"]] += 1
    pending: Counter = Counter(c["id"] for c in st["stove"] if c["id"] > 0)
    # ★ 套数设置：预检必须按同一个 sets 口径算，否则囤了多套也判「已凑齐」硬挡，
    #   sets 参数形同虚设（用户要「按囤卡炼多套、不空炉」，就得以多套口径启动）。
    #   sets=0(自动/1星按囤卡) 时按引擎实际会排的套数算（_calc_sets）。
    pre_sets = eng.surplus_sets if eng.surplus_sets > 0 else 1
    if eng.surplus_sets <= 0:
        # sets=0(自动)：按引擎真实口径算（1星按囤卡/炉位·h 排几套），
        # 否则预检按固定 1 套判「已凑齐」会把自动多套硬挡在门外。
        nfree = st["cap"].get("stove", {}).get("free_main", 0)
        pre_sets = max(
            (eng._calc_sets(eng.plans[t], stock, pending, nfree) for t in ids),
            default=1)
    plans = [eng.plan_summary(stock, pending, t, sets=pre_sets) for t in ids]
    bad = [p for p in plans if not p["ok"]]
    if bad:
        return {"ok": False, "plans": plans,
                "message": "以下套卡底卡并非全是商店面值10卡，无法纯买卡刷："
                           + "、".join(p["name"] for p in bad)}
    noth = [p for p in plans if not p["recipes"] and not p["base_cards"]]
    if len(noth) == len(plans):
        return {"ok": False, "plans": plans,
                "message": f"{noth[0]['name']} 等 {len(noth)} 套已凑齐（卡箱内每张都在），可直接上交集卡册"}
    plan = plans[0]

    free = {k: max(0, st["cap"].get(k, {}).get("cur", 0)
                   - st["cap"].get(k, {}).get("used", 0)) for k in ("change", "store")}
    # 引擎是「就地买卡」（只买这一炉马上要入炉的几张，买完立刻消耗 3 张，
    # 净释放 2 格），所以只要换卡箱还有 1 格能进货就能跑，不再要求一次腾出 6 格
    # （2026-09-16 用户要求：别预先囤底卡；真不够是收尾「买齐底卡进集卡册」那一刻，
    #  那时引擎会明确报出来）。
    need = 1
    if free["change"] < need:
        # ★ 2026-10-03：换卡箱满时不再直接拒绝启动，而是**先腾位**（优先移保险箱、
        #   10 面底卡卖掉；见 AutoRefine._make_room）再重试。真机复现：收尾补集卡册
        #   底卡时换卡箱 0 空位 → 启动预检直接拒绝，引擎根本起不来。
        freed = await eng._make_room(ids[0], need - free["change"], st, [])
        if freed > 0:
            mp = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
            if mp.get("code") == 0:
                st = parse_full(mp.get("raw", ""))
                free = {k: max(0, st["cap"].get(k, {}).get("cur", 0)
                               - st["cap"].get(k, {}).get("used", 0))
                        for k in ("change", "store")}
    if free["change"] < need:
        return {"ok": False, "plan": plan, "plans": plans, "free": free, "need": need,
                "message": (f"换卡箱 0 空位，连 1 张底卡都买不进来："
                            f"请先从换卡箱挪走一些卡（保险箱还剩 {free['store']} 格）")}
    if _auto is not None and _auto.running:
        _auto.stop()
    _auto = eng
    eng.seed_state(stock, pending)   # 面板先显示启动时的实时口径，tick 后会刷新
    eng.start()
    return {"ok": True, "plan": plan, "plans": plans, "free": free, "need": need, **eng.status()}


@app.post("/api/auto/queue/add")
async def auto_queue_add(tid: int):
    """运行中往队列追加一套卡（不用停引擎）。"""
    if err := _require_login():
        return err
    if _auto is None or not _auto.running:
        return {"ok": False, "message": "引擎未在运行，请先用「启动」把第一套跑起来"}
    try:
        plan = _auto.add_theme(tid)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"无法加载该套卡计划：{exc}"}
    return {"ok": True, "plan": plan, "queue": _auto.queue, ** _auto.status()}


@app.get("/api/auto/plan")
async def auto_plan(tid: int):
    """只读：按当前库存算该套的净需求计划（缺什么、要炼几次、买几张、多少炉时）。"""
    if err := _require_login():
        return err
    try:
        eng = AutoRefine(_session, tid)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}
    mp = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
    if mp.get("code") != 0:
        return {"ok": False, "message": "读取卡箱状态失败"}
    st = parse_full(mp.get("raw", ""))
    stock: Counter = Counter()
    for c in st["change"] + st["store"]:
        stock[c["id"]] += 1
    pending: Counter = Counter(c["id"] for c in st["stove"] if c["id"] > 0)
    # 缺哪几张（箱内一张都没有的套内卡）
    from .auto_refine import _load as _load_lib
    cards = {int(c[0]): c for c in _load_lib()["card_list"]}
    missing = [{"id": c, "name": str(cards[c][2]),
                "price": int(cards[c][3] or 0)}
               for c in eng.ids if stock.get(c, 0) + pending.get(c, 0) == 0]
    return {"ok": True, "plan": eng.plan_summary(stock, pending), "missing": missing,
            "inventory": {str(c): n for c, n in stock.items() if c in set(eng.ids)}}


@app.post("/api/auto/stop")
async def auto_stop():
    if _auto is not None:
        _auto.stop()
    return {"ok": True, "running": False}


@app.get("/api/auto/options")
async def auto_options(profit_only: int = 1):
    """可自动刷的发行套（底层全为商店面值10卡）。profit_only=1 只列净赚>0 的。"""
    rows = [r for r in ec.analyze() if not profit_only or r["profit"] > 0]
    rows.sort(key=lambda r: -r["rate"])
    return {"items": rows}


@app.get("/api/probe")
async def probe():
    """探测魔法卡片真实接口域名/端点（2026 迁移后定位用）。"""
    if err := _require_login():
        return err
    return await _session.probe_api()


@app.get("/api/game/login")
async def game_login():
    """空间游戏授权握手：拿游戏票据 ticket（真实会话仍需进一步定位）。"""
    if err := _require_login():
        return err
    return await _session.enter_game()


@app.get("/api/state")
async def state():
    """游戏全量状态（主页面解析后），前端主面板直接渲染。每次实时请求。"""
    if err := _require_login():
        return err
    try:
        d = await _session.get_state()
    except httpx.ConnectError:
        return {"ok": False, "conn_error": True, "code": -1,
                "message": "连接腾讯服务器失败（本地连接断开/网络抖动），保持登录态，请稍后点击刷新重试"}
    except httpx.TimeoutException:
        return {"ok": False, "conn_error": True, "code": -1,
                "message": "连接腾讯服务器超时（本地连接断开/网络抖动），保持登录态，请稍后点击刷新重试"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "conn_error": True, "code": -1, "message": f"读取状态异常：{e}"}
    # 炉位只有 card_id，补上卡名/套卡/星级/面值/下架状态供前端显示
    try:
        cards = (d.get("stovebox") or {}).get("cards") or []
        ids = [c["id"] for c in cards if c.get("id")]
        meta = card_plan.card_details(ids) if ids else {}
        for c in cards:
            m = meta.get(c["id"]) if c.get("id") else None
            c["name"] = (m or {}).get("name", "")
            c["theme"] = (m or {}).get("theme", "")
            c["star"] = (m or {}).get("star", 0)
            c["price"] = (m or {}).get("price", 0)
            c["publish"] = (m or {}).get("publish", 0)
            c["mats"] = (m or {}).get("mats", [])
            c["starter"] = (m or {}).get("starter", 0)
            c["flash"] = (m or {}).get("flash", 0)
    except Exception:  # noqa: BLE001
        pass
    # 换卡箱/保险箱同样补卡名/面值（卡箱页要展示）
    try:
        for box_key in ("changebox", "storebox"):
            bcards = (d.get(box_key) or {}).get("cards") or []
            bids = [c["id"] for c in bcards if c.get("id")]
            bmeta = card_plan.card_details(bids) if bids else {}
            for c in bcards:
                m = bmeta.get(c["id"]) if c.get("id") else None
                c["name"] = (m or {}).get("name", "")
                c["price"] = (m or {}).get("price", 0)
                c["theme"] = (m or {}).get("theme", "")
                c["star"] = (m or {}).get("star", 0)
                c["publish"] = (m or {}).get("publish", 0)
    except Exception:  # noqa: BLE001
        pass
    # 腾讯会话失效（skey 过期）：清掉死的登录态，让前端自动回到扫码界面
    if _session_expired(d):
        _session.clear()
        d = {**d, "need_login": True}
    return d


@app.get("/api/card/plan")
async def refine_plan(max_plans: int = 10):
    """生成「现在可以炼什么」的推荐（素材在手、目标未收集），不消耗任何资源。"""
    if err := _require_login():
        return err
    try:
        return await _session.get_refine_plan(max_plans=max_plans)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"生成方案异常：{e}"}


@app.post("/api/card/refine")
async def card_refine(target: int):
    """开始炼卡：消耗三张素材卡合成目标卡。会真实消耗素材，谨慎调用。"""
    if err := _require_login():
        return err
    try:
        r = await _session.refine(target)
        code = r.get("code") if isinstance(r, dict) else None
        if code == 0:
            _inv_cache["data"] = None   # 炼卡消耗素材+入炉，立即使持有快照失效
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"炼卡请求异常：{e}"}


@app.post("/api/card/buy")
async def card_buy(card_id: int, theme_id: int):
    """从商店直接买卡（card_market_npc_buy）。会真实扣金币，谨慎调用。"""
    if err := _require_login():
        return err
    try:
        r = await _session.post(EP_BUY, card_id=card_id, theme_id=theme_id)
        code = r.get("code")
        ok = code == 0
        buylog.log_buy("card_buy", card_id, theme_id, code=code, ok=ok,
                       note=r.get("message") or "")
        if ok:
            _inv_cache["data"] = None   # 买卡后卡箱有变，立即使持有快照失效
        return {"ok": ok, "code": code,
                "message": r.get("message") or ("" if ok else f"错误码 {code}")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"买卡请求异常：{e}"}


@app.get("/api/card/synthesis")
async def card_synthesis(card_id: int):
    """给定某张卡，返回其所属套卡的合成路径（配方树）+ 实时持有情况。

    前端卡箱点开任意一张卡时调用：展示该套卡所有「可合成目标」及其下级卡，
    标注每张下级卡是否已持有、目标卡当前能否直接炼（3 张下级卡齐全且有空炉）。
    """
    if err := _require_login():
        return err
    try:
        mp = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
        raw = mp.get("raw", "")
        return card_plan.synthesis_for_card(card_id, raw)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"合成路径生成异常：{e}"}


@app.post("/api/card/cancel")
async def card_cancel(slot: int, stove: int = -1):
    """取消指定炉位的炼卡，素材卡退回卡箱。

    stove 留空则自动从当前炉位状态查该 slot 的真实 stove 值
    （stove 值各炉位不同，恒传 0 会失败）。
    """
    if err := _require_login():
        return err
    try:
        if stove < 0:
            st = await _session.get_state()
            hit = next((c for c in (st.get("stovebox") or {}).get("cards") or []
                        if c.get("slot") == slot), None)
            if not hit or not hit.get("id"):
                return {"ok": False, "code": -1, "message": f"炉位 {slot} 没有在炼的卡"}
            if hit.get("flag") == 2:
                return {"ok": False, "code": -1, "message": "该卡已炼制完成，请先取卡（无需取消）"}
            stove = hit.get("stove") or 0
        return await _session.cancel_refine(slot, stove)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"取消请求异常：{e}"}


@app.get("/api/card/mainpage")
async def card_mainpage(method: str = "post"):
    """主页面/卡箱入口。magicCard 用 POST，也提供 GET 变体便于对比。"""
    if err := _require_login():
        return err
    fn = _session.post if method == "post" else _session.get
    return await fn(EP_MAINPAGE, code="", opuin=_session.uin)


@app.get("/api/card/register")
async def card_register():
    """开通魔法卡片（未开通时接口一律拒绝）。"""
    if err := _require_login():
        return err
    return await _session.get(EP_REGISTER)


# ---- 卡库查询（静态卡库 + 实时持有数）----
# 持有快照 8 秒内复用，避免每次点击都打一次 mainpage
_inv_cache: dict = {"ts": 0.0, "data": None}
INV_TTL = 8.0


async def _owned_snapshot() -> dict | None:
    """实时读取一次卡箱，返回 {card_id: 持有数} 与炉内目标。失败返回 None。"""
    import time as _t
    from .card_plan import parse_user_cards

    now = _t.time()
    if _inv_cache["data"] and now - _inv_cache["ts"] < INV_TTL:
        return _inv_cache["data"]
    try:
        r = await _session.post(EP_MAINPAGE, code="")
        if r.get("code") != 0:
            return None
        u = parse_user_cards(r.get("raw", ""))
        avail: dict[int, int] = {}
        for bname in ("changebox", "storebox"):
            for _slot, cid in u[bname]:
                if cid > 0:
                    avail[cid] = avail.get(cid, 0) + 1
        _inv_cache["data"] = {
            "avail": avail,
            "furnace": [cid for _s, cid in u["stovebox"] if cid > 0],
        }
        _inv_cache["ts"] = now
        return _inv_cache["data"]
    except Exception:  # noqa: BLE001
        return None


# ---- 集卡册进度（card_mini_get）：每套「已炼成(上交)次数」num，覆盖全库主套卡 ----
# 这是游戏官方记录的「已炼套数」口径（不是按卡箱持有数估算）。变化频率低，短时缓存。
import time as _tmod
_mini_cache: dict = {"ts": 0.0, "data": None}
MINI_TTL = 60.0


async def _mini_sets_map() -> dict[int, tuple[int, int]]:
    """{theme_id: (num, lv)}，来自 card_mini_get 的 <Node theme_id num lv>。

    num = 集齐套卡次数（官方「已炼成套数」口径，即列表/明细的 sets 字段）；
    lv  = 集卡册等级（与「主题秀收集数」无关，主题是另一维度，见 _collection_map）。
    """
    now = _tmod.time()
    if _mini_cache["data"] is not None and now - _mini_cache["ts"] < MINI_TTL:
        return _mini_cache["data"]
    try:
        r = await _session.post(EP_MINI_GET)
        raw = r.get("raw", "") or ""
        mp = {int(a): (int(b), int(c)) for a, b, c in
              re.findall(r'<Node\s+theme_id="(\d+)"\s+num="(\d+)"\s+lv="(\d+)"', raw)}
        _mini_cache["data"] = mp
        _mini_cache["ts"] = now
        return mp
    except Exception:  # noqa: BLE001
        return _mini_cache["data"] or {}


# ---- 主题秀（QQ秀立绘）收集状态：card_collection_get 才是真源 ----
# 每套 <Node theme_id ... cnt 已收集立绘数 total_cnt 该套立绘总数 qqshow_str 已收集立绘id>。
# 早期卡 QQ秀分男女，用户常只选男立绘，所以「集齐多次(num 大)」≠「立绘集满(cnt=total)」。
# 不能用 card_mini_get 的 num 估算主题秀，必须读这里。变化频率低，短时缓存。
_collection_cache: dict = {"ts": 0.0, "data": None}
COLL_TTL = 120.0


async def _collection_map() -> dict[int, tuple[int, int]]:
    """{theme_id: (cnt, total_cnt)}，来自 card_collection_get 的 <Node ...>。

    实测语义（2026-10-08 jikace.har + 真机对照；2026-10-09 修正 show_total 来源）：
    - cnt = 已收集（已获得）的主题秀立绘数——游戏集卡册 UI 的「已获得」章数量，
      是「主题秀是否集满」的判定依据（与 num 无关）；cnt 可因重复选同立绘而 > 立绘总数。
    - total_cnt ≡ 集齐次数（与 card_mini_get 的 num 全量相等，会随收集增长），
      不是立绘总数，本服务不再用它作 show_total。
    - 该套【立绘总数】取自套卡静态数据 theme_card_list 的 t[9] 项数（card_query.indexes
      的 show_total 字段）：非 1 星全 4；1 星多数 4、仅时尚男/女·经典男/女 4 套为 1；
      道具卡=0。list_themes / theme_detail 的 show_total 即此值。
    - 早期 QQ 秀时代重复选同一立绘只延长有效期不计数 → 老套 cnt<num（真实口径）。
    - 不在响应里的套（旅行/挖地/三国/西游等模块套）无主题秀数据。
    """
    now = _tmod.time()
    if _collection_cache["data"] is not None and now - _collection_cache["ts"] < COLL_TTL:
        return _collection_cache["data"]
    try:
        r = await _session.post(EP_COLLECTION_GET)
        raw = r.get("raw", "") or ""
        mp = {int(a): (int(b), int(c)) for a, b, c in
              re.findall(r'theme_id="(\d+)"[^>]*cnt="(\d+)"[^>]*total_cnt="(\d+)"', raw)}
        _collection_cache["data"] = mp
        _collection_cache["ts"] = now
        return mp
    except Exception:  # noqa: BLE001
        return _collection_cache["data"] or {}


@app.get("/api/cards/q")
async def cards_q(price: int = 0, kw: str = "", star: int = 0,
                  flash: int = -1, offline: int = -1, transferable: int = -1,
                  owned_only: int = 0, limit: int = 300):
    """按面值/关键词全库查卡（含持有数、可炼、素材）。只读。

    transferable=1 → 只返回「能变卡得到」的卡（手动变卡的目标卡候选）。
    """
    if err := _require_login():
        return err
    inv = await _owned_snapshot() or {"avail": {}}
    out = cq.query_cards(
        price=price, q=kw, star=star, flash=flash, offline=offline,
        transferable=transferable,
        owned_only=owned_only, owned=inv["avail"], limit=min(limit, 500))
    out["ok"] = True
    out["furnace"] = inv.get("furnace", [])
    return out


@app.get("/api/cards/themes")
async def cards_themes(q: str = "", star: int = 0, flash: int = -1,
                       offline: int = -1, module: str = "", transferable: int = -1,
                       composable: int = -1, theme_show: int = -1,
                       limit: int = 500):
    """套卡目录（卡册 module / 星级 / 闪卡 / 关键词过滤），按上架时间倒序。只读。

    transferable=1 → 只返回「能变卡得到」的套卡；composable=1 → 只返回「有合成配方」的套卡。
    theme_show=1 → 只返回「主题秀没集满」的套卡（card_collection_get 中 cnt<total_cnt）。
    每套附 have（已持有卡种数）/ sets（已炼 N 套 = 集卡册进度 num，官方口径）/
          show / show_total（主题秀已集/总数，真源）/ n（总卡数）。
    """
    if err := _require_login():
        return err
    inv = await _owned_snapshot() or {"avail": {}}
    mini = await _mini_sets_map()
    coll = await _collection_map()
    return cq.list_themes(q=q, star=star, flash=flash, offline=offline, module=module,
                          transferable=transferable, composable=composable,
                          theme_show=theme_show,
                          owned=inv["avail"], mini=mini, collection=coll,
                          limit=min(limit, 2000))


@app.get("/api/cards/theme/{tid}")
async def cards_theme(tid: int):
    """某套卡全部卡：名称/面值/持有/配方(素材+耗时)/被用作哪些卡素材。"""
    if err := _require_login():
        return err
    inv = await _owned_snapshot() or {"avail": {}}
    mini = await _mini_sets_map()
    coll = await _collection_map()
    d = cq.theme_detail(tid, owned=inv["avail"], mini=mini, collection=coll)
    if d is None:
        return {"ok": False, "message": f"未找到主题 {tid}"}
    d["ok"] = True
    return d


@app.get("/api/cards/card/{cid}")
async def cards_card(cid: int):
    """单卡合成路径（本卡←素材←素材…到基础卡）。"""
    if err := _require_login():
        return err
    return {"ok": True, "chain": cq.card_chain(cid)}


@app.get("/api/cards/prices")
async def cards_prices():
    """常见面值及其卡数（快捷筛选）。"""
    return {"ok": True, "prices": cq.price_stats()}


@app.get("/api/card/diagnose")
async def card_diagnose():
    """一键诊断：主页面 → 抽卡；若 -1100/-1005 自动开通后复测。"""
    if err := _require_login():
        return err
    out: dict = {"uin": _session.uin, "steps": []}

    async def call(label, fn, **kw):
        try:
            r = await fn(**kw) if kw else await fn()
        except Exception as e:  # 网络/解析异常不中断整条链
            r = {"code": -999, "message": f"异常: {e}"}
        out["steps"].append({"step": label, **r})
        return r

    r1 = await call("mainpage(GET)", _session.get, endpoint=EP_MAINPAGE, code="")
    out["mainpage_code"] = r1.get("code")
    if r1.get("code") not in (0,):
        r1b = await call("mainpage(POST)", _session.post, endpoint=EP_MAINPAGE, code="")
        out["mainpage_post_code"] = r1b.get("code")

    codes = {s["code"] for s in out["steps"]}
    if codes & {-1100, -1005, -1006}:
        reg = await call("register(开通)", _session.get, endpoint=EP_REGISTER)
        out["register_code"] = reg.get("code")
        r2 = await call("mainpage after register", _session.get,
                        endpoint=EP_MAINPAGE, code="")
        out["mainpage_after_register"] = r2.get("code")

    await call("random_get(抽卡)", _session.get, endpoint=EP_RANDOM_GET, type=1)
    out["ok"] = any(s.get("code") not in (-1100, -1005, -1006, -999)
                    for s in out["steps"])
    return out


# ---- 换卡（手动模式：设定目标套 → 搜卡友 → 扫换卡箱 → 手动换）----

@app.get("/api/exchange/peers")
async def exchange_peers(tid: int = 0, tids: str = ""):
    """按主题搜「在玩该主题的卡友」（下架套也能搜），并列出手动名单。只读。

    tids 可给逗号分隔的多个套（如 "353,475"），返回这些套卡友的**并集去重**——
    多套用于「在炼某套的卡友，换卡箱里也有别的套的卡」这种场景。
    """
    if err := _require_login():
        return err
    from . import exchange as ex
    tids_list: list[int] = []
    for x in (tids or "").split(","):
        x = x.strip()
        if x.isdigit():
            tids_list.append(int(x))
    if tid and tid not in tids_list:
        tids_list.append(tid)
    api: list[str] = []
    for t in tids_list:
        for u in await ex.theme_peers(_session, t):
            if u not in api:
                api.append(u)
    manual = ex.load_manual_peers()
    return {"ok": True, "tid": tid, "tids": tids_list, "api_peers": api,
            "manual_peers": manual, "api_n": len(api), "manual_n": len(manual)}


@app.post("/api/exchange/manual")
async def exchange_manual(body: dict):
    """保存手动卡友 QQ 名单（持久化）。body: {"peers": ["qq", ...]}"""
    if err := _require_login():
        return err
    from . import exchange as ex
    peers = ex.save_manual_peers(body.get("peers") or [])
    return {"ok": True, "manual_peers": peers}


@app.post("/api/exchange/scan")
async def exchange_scan(body: dict):
    """扫描卡友换卡箱，找「目标套卡」中对方未锁的卡，并匹配我方应付出的同面值卡。

    body: {theme_id | theme_ids:[..], price?, card_id?, card_ids?,
           peers?:[qq..], use_api?:bool, manual_peers?:[qq..]}
    会并发请求多个卡友主页，耗时随卡友数线性增长（80 人约 10~30 秒）。

    theme_ids = 多套一起扫（2026-09-13 加）：因为在炼某套卡的卡友，换卡箱里也会有
    别的套的卡。玩家池取这些套各自的 theme_peers 并集去重，命中判定为「卡属于任一目标套」。

    card_ids = 「指定卡」多选，可跨套，只列出换卡箱里正放着这些卡的人；
    留空 = 目标套全部卡都算命中。注意腾讯无「按卡搜人」接口，筛选池始终是
    「正在玩这些套的玩家」（theme_peers），指定的卡也只在此池内被搜到。
    """
    if err := _require_login():
        return err
    from . import exchange as ex
    # 目标套：兼容单套 theme_id，也支持 theme_ids 数组（多套一起扫）
    extra = [int(t) for t in (body.get("theme_ids") or []) if str(t).strip()]
    theme_id = int(body.get("theme_id") or 0)
    if not theme_id and extra:
        theme_id = extra[0]
    targets = sorted(set(([theme_id] if theme_id else []) + extra))
    # peers 类型防御：若有人传成逗号分隔字符串，逐字符遍历会拆出一堆单个数字，
    # 然后对着这些「QQ」真发请求（静默的垃圾流量）。这里统一归一化成列表。
    def _as_list(v) -> list:
        if isinstance(v, str):
            return [x.strip() for x in v.replace("\n", ",").split(",")]
        return list(v or [])

    peers = [str(p).strip() for p in _as_list(body.get("peers")) if str(p).strip().isdigit()]
    if not targets:
        return {"ok": False, "message": "请先选择目标套卡"}
    # 玩家池 = 各目标套「在玩该套」玩家的并集（去重）。
    # 多套的动机：在炼某套的卡友，换卡箱里往往也放着别的套的卡，一次扫描可以都捞出来。
    if body.get("use_api", True):
        for t in targets:
            for u in await ex.theme_peers(_session, t):
                if u not in peers:
                    peers.append(u)
    peers += [str(u).strip() for u in _as_list(body.get("manual_peers"))
              if str(u).strip().isdigit()]
    # 好友补充池（2026-09-14 加）：theme_peers 按套只给 ≤100 人，
    # card_user_list 给登录者的好友名单（实测 194 人），与套卡池零交集，可互补。
    # 名单里含自己（要排除）以及若干小号/僵尸号，小号同样是有效换卡对象。
    friends_added = 0
    if body.get("include_friends") or body.get("include_all_players"):
        try:
            r_all = await _session.post(EP_USER_LIST)
            me = str(getattr(_session, "uin", "") or "").lstrip("0")
            for m in re.finditer(r'uin="(\d+)"', r_all.get("raw", "")):
                u = m.group(1)
                if me and u.lstrip("0") == me:
                    continue  # 跳过自己
                peers.append(u)
                friends_added += 1
        except Exception:  # noqa: BLE001
            pass  # 补充池失败不影响主流程
    peers = list(dict.fromkeys(peers))
    if not peers:
        return {"ok": False, "message": "没有可扫描的卡友（这些套都没人玩或名单为空）"}
    try:
        return await ex.scan_peers(
            _session, theme_id, peers,
            price=int(body.get("price") or 0),
            card_id=int(body.get("card_id") or 0),
            card_ids=body.get("card_ids") or [],
            theme_ids=extra,
            concurrency=int(body.get("concurrency") or 5),
            stop_after=int(body.get("stop_after") or 0))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"扫描异常：{e}"}


@app.post("/api/exchange/do")
async def exchange_do(body: dict):
    """执行一次换卡（手动确认后调用）。会真实消耗 src 卡、换入 dst 卡。

    body: {friend, dst_slot, dst_id, src_slot, src_id, src_type?}
    """
    if err := _require_login():
        return err
    from . import exchange as ex
    try:
        return await ex.do_exchange(
            _session, str(body["friend"]), int(body["dst_slot"]),
            int(body["dst_id"]), int(body["src_slot"]), int(body["src_id"]),
            int(body.get("src_type") or 1))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"换卡请求异常：{e}"}


# ---- 自动换卡 ----
@app.post("/api/exchange/auto/start")
async def exchange_auto_start(body: dict):
    """启动自动换卡：从「在玩该套卡的卡友」换卡箱里自动换入指定卡。

    body: {
      tid,                 # 主套卡 ID（选填；不填则取 tids[0]）
      tids?: [..],         # 多选源套：从这些套的「在玩」玩家池里扫（可跨套换指定卡）
      card_ids?: [..],     # 只换这些卡（可跨套、可多张；不填=主套全部，再按 price 过滤）
      price?: 0,           # 只换该面值的卡（如基础卡面值）
      want?: 1,            # 每张目标卡换到几张
      pay_card_ids?: [..], # 指定「用作付出」的卡：只从这些卡里付，且可全部换完
                           # （不填=自动挑「多余的」卡，且每种至少保留 1 张）
      max_peers?: 0,       # 最多查几位卡友，0=不限
      peers?: ["qq",..],   # 指定卡友；不填则按 tids 自动搜
      dry_run?: false,     # true=只模拟，不真发换卡请求
    }
    中止条件：换够指定数量 / 卡友查完 / 手动 stop。
    """
    if err := _require_login():
        return err
    from .auto_exchange import get_engine
    tids = [int(x) for x in (body.get("tids") or []) if str(x).isdigit()]
    tid = int(body.get("tid") or 0) or (tids[0] if tids else 0)
    if not tid:
        return {"ok": False, "message": "请先填目标套卡 ID（或 tids）"}
    eng = get_engine(_session)
    r = eng.start(
        tid=tid,
        tids=[int(x) for x in (body.get("tids") or []) if str(x).isdigit()],
        card_ids=body.get("card_ids") or [],
        price=int(body.get("price") or 0),
        want=int(body.get("want") or 1),
        pay_card_ids=[int(x) for x in (body.get("pay_card_ids") or [])
                      if str(x).strip().isdigit()],
        max_peers=int(body.get("max_peers") or 0),
        peers=[str(p) for p in (body.get("peers") or []) if str(p).isdigit()],
        dry_run=bool(body.get("dry_run")),
    )
    return r


@app.post("/api/exchange/auto/stop")
async def exchange_auto_stop():
    if err := _require_login():
        return err
    from .auto_exchange import get_engine
    return get_engine(_session).stop()


@app.get("/api/exchange/auto/status")
async def exchange_auto_status():
    if err := _require_login():
        return err
    from .auto_exchange import get_engine
    return get_engine(_session).status()


# ---- 活动中心（只读查询） ----
KNOWN_ACTIVITIES: list[dict] = [
    # 17 周年大作战：客户端左侧活动面板显示 7 天 100 任务且可完成；
    # 但 card_act_send_free/card_task_activity/card_act_get_* 以 act=92 调用均返回
    # activity is end。说明周年庆要么使用其他 act，要么走独立端点，需抓包确认。
    {"act": 91, "name": "七日签到/任务(模板)", "desc": "H5 custom_act_seven_day_query 硬编码 act=91；已 end", "ended": True},
    {"act": 92, "name": "疑似旧7日任务(非周年庆/已结束)", "desc": "card_act_* 全族返回 activity is end，与客户端可完成的周年庆不符", "ended": True},
    {"act": 11, "name": "任务活动/累计登录", "desc": "H5任务活动查询（10个子任务）"},
    {"act": 141, "name": "收集卡活动", "desc": "集卡兑换奖励"},
    {"act": 185, "name": "连续登录", "desc": "连续登录统计"},
    {"act": 191, "name": "猫咪任务/进度活动", "desc": "cat_task_score进度系统"},
    {"act": 221, "name": "扭蛋/抽卡活动", "desc": "score/star/freecount"},
    {"act": 241, "name": "未知消耗活动", "desc": "consume/times/get_num"},
    {"act": 251, "name": "每日签到", "desc": "cnt/day_ts"},
    {"act": 281, "name": "排行榜", "desc": "玩家经验榜"},
]


@app.get("/api/activities")
async def activities():
    """返回当前已探测到的活动 ID 列表（只读）。"""
    if err := _require_login():
        return err
    return {"ok": True, "activities": KNOWN_ACTIVITIES}


@app.get("/api/activity/{act}")
async def activity(act: int):
    """查询某个活动 ID 的实时进度（只读）。"""
    if err := _require_login():
        return err
    try:
        r = await _session.post("card_act_send_free", act=act)
        raw = r.get("raw", "")
        try:
            import json as _json
            data = _json.loads(raw)
        except Exception:
            data = {"raw": raw[:500]}
        return {"ok": True, "act": act, "data": data}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.post("/api/activity/raw")
async def activity_raw(body: dict):
    """通用活动接口探测：用户抓到包后可直接复现。

    body: {"endpoint": "card_xxx", "method": "post|get", "params": {...}}
    """
    if err := _require_login():
        return err
    ep = body.get("endpoint", "")
    if not ep or "/" in ep:
        return {"ok": False, "message": "非法 endpoint"}
    method = body.get("method", "post")
    params = body.get("params") or {}
    try:
        fn = _session.post if method == "post" else _session.get
        r = await fn(ep, **params)
        return {"ok": True, "endpoint": ep, "method": method, "params": params, "result": r}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求异常：{e}"}


@app.get("/api/cat/travel")
async def cat_travel():
    """灵宠探险状态查询（灵宠探险系统）。

    接口：card_user_spirite act=1。返回 explore 结构（探险状态）、
    free_explore_cnt（免费次数），以及**灵宠专用资源**（见 wallet / pieces）。
    只读，不消耗任何资源。

    `wallet` = 灵宠探险专属道具，官方 `soul_elf` 的奖励清单原文是
    「卡片碎片｜银锭、百变丹、金钥匙、藏宝图、灵力果等」，对应 act=1 的
    顶层字段 yinding / baibiandan / gold_key / treasure_map / power。
    注意这些**不在** `card_market_props_info`（那个只有炼卡道具，type=1）。
    """
    if err := _require_login():
        return err
    try:
        # opuin 必须传「去掉前导零」的 uin，否则服务端静默返回空数据
        r = await _session.post(EP_SPIRITE, act=1, opuin=plain_uin(_session))
        data = r.get("data") or {}
        wallet = []
        for key, nm in (("gold_key", "金钥匙"), ("treasure_map", "藏宝图"),
                        ("baibiandan", "百变丹"), ("yinding", "银锭"),
                        ("power", "灵力")):
            v = data.get(key)
            try:
                wallet.append({"key": key, "name": nm, "count": int(v)})
            except (TypeError, ValueError):
                continue
        pc = data.get("pieces") or []
        ex = data.get("explore") or {}
        return {
            "ok": r.get("code") == 0,
            "code": r.get("code"),
            "message": r.get("message", ""),
            "free_explore_cnt": data.get("free_explore_cnt"),
            "explore": ex,
            "spirites": data.get("spirites", []),
            "section_id": data.get("section_id"),
            "pass_id": data.get("pass_id"),
            "power": data.get("power"),
            # 灵宠专用资源（碎片 + wallet）。与炼卡道具（card_market_props_info）无关。
            "wallet": wallet,
            "pieces": {"kinds": sum(1 for x in pc if int(x.get("num") or 0) > 0),
                       "total": sum(int(x.get("num") or 0) for x in pc)},
            "permanent": data.get("permanent"),
            "pay_explore_cnt": data.get("pay_explore_cnt"),
            # 探险槽（hy_）= **荒岛玩法**（官方 c_island：8 章 × 6 关，每关 elf_num 3~8 只、
            # 带 elf_hp 战斗、奖励含五彩石/金粉），与大地图（pass_id/section_id）是两套玩法，
            # **不是**「派两只的第二路」（两只走 explore.put_spirites）。
            # 证据（2026-09-15，两条独立吻合）：① hy_put_spirites=[1,3,5,7,9]（5 只）↔ 荒岛是唯一
            # 存在 elf_num=5 的玩法；② hy_explore_pass_id=16 ↔ 荒岛连续编号第 16 关
            # 「戴尔得荒岛4」，其 elf_num 正好 5。另 zj_bits=255(8 位) ↔ 荒岛 8 章、首章名「荒野孤岛」。
            # ⚠️ 关卡号只认 hy_explore_pass_id（**连续编号 1..48**）；hy_section_id=65535 是无效值。
            "island": {
                "seq": ex.get("hy_explore_pass_id") or 0,
                "place": spm.island_place_text(ex.get("hy_explore_pass_id")),
                "stage": spm.island_place(ex.get("hy_explore_pass_id")),
                "spirites": ex.get("hy_put_spirites") or [],
                "explore_ts": ex.get("hy_explore_ts") or 0,
                "today_cnt": data.get("today_hy_explore_cnt"),
                # 每天次数上限（普通 5 次，VIP 更多）。act=1 不下发，这里给默认值用于「还剩几次」。
                "daily_max": 5,
            },
            # 穿越丛林（官方 c_across，11 章 62 关）= **即时历练**，没有收获步骤。
            # 计数器是 `torrid_zone_explore_cnt`（实测 act=11 成功一次就 +1：1→2→3→4），
            # 与 `today_fri_explore_cnt`（免费探索计数，不随 act=11 变）**不是一回事**。
            # 参数：id = 1 级地图编号 - 1、sub_id = 2 级地图编号 - 1（都 0-based，
            # 见 spirite_maps.across_param）。⚠️ **别与 open_c/open_s 混**：
            # 那两个是**解锁条件**（需大地图第 open_c 章第 open_s 关已通过），不是本关坐标。
            "across": {
                "used": data.get("torrid_zone_explore_cnt"),
                "ts": data.get("fri_explore_ts"),
                # 每天历练上限（普通 10 次，VIP 更多）。同理给默认值用于「还剩几次」。
                "daily_max": 10,
            },
            # 界面「默认选中」用的进度 —— **全部由规则推导，不存本地值**（见 spirite_progress）：
            #   map    = act=1 直接下发的当前关（pass_id/section_id，权威；vt52 是它的全局序号）
            #   across / island = 用官方每关的 open_c/open_s（需大地图第 X 章第 Y 关已过）
            #                     + 灵宠等级条件逐关判定，「能去的最新关」= 解锁集合里 seq 最大的
            #   另外返回该模式**已解锁的 seq 列表**（前端给未解锁的关卡选项加标注）。
            "recent": spg.derive(data),
            "today_fri_explore_cnt": data.get("today_fri_explore_cnt"),
            # 原样透出的 hy_ 原始字段（调试用，语义见上）
            "hy_section_id": data.get("hy_section_id"),
            "hy_explore_pass_id": ex.get("hy_explore_pass_id"),
            "maps": list(SPIRITE_MAPS),
            "raw": data,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.middleware("http")
async def no_cache_middleware(request, call_next):
    """所有响应禁用缓存。

    /api/state 等是 GET 且无缓存头，Chrome 会按启发式策略缓存，
    导致页面刷新后仍渲染旧数据（改完后端却"没生效"）。
    """
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ---- 好友列表 / 集卡册进度 / 灵宠探险（2026-09-14 新探明端点） ----


@app.post("/api/players")
async def players():
    """好友列表（card_user_list，只读）。

    **不是全服玩家**：固定返回 194 人＝当前登录者的 QQ 好友里玩过本游戏的人
    （含自己、含 exp=0 的僵尸号）；忽略分页参数，换 uin/opuin 请求者结果不变。

    用途：换卡扫描的**补充玩家池**（与 theme_peers 的套卡池零交集，可互补）。
    """
    if err := _require_login():
        return err
    try:
        r = await _session.post(EP_USER_LIST)
        raw = r.get("raw", "")
        rows = []
        for m in re.finditer(r'<user\s+uin="(\d+)"\s+nick="([^"]*)"([^>]*)>', raw):
            exp = re.search(r'exp="(\d+)"', m.group(3) or "")
            rows.append({
                "uin": m.group(1),
                "nick": html.unescape(m.group(2)),
                "exp": int(exp.group(1)) if exp else 0,
            })
        return {"ok": r.get("code") == 0, "code": r.get("code"),
                "total": len(rows), "players": rows}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}

@app.post("/api/daily/run")
async def daily_run(body: dict | None = None):
    """一键日常（SSE 流式）：跑 daily.ACTIONS 里**已实证**的每日领取动作。

    body 可选 {"keys": ["gift"]} 只跑指定项；不传 = 全跑。
    每完成一项推一行（item 字典），前端边收边显示；末尾推 __done__ 带汇总。
    ⚠️ 只有 daily.py 里写明实证依据的动作才会执行 —— 需要抓包确认的（签到 /
    连续登录领奖 / 猫咪旅行等）宁缺勿滥，不在这里凭猜测补。
    """
    if err := _require_login():
        return err
    keys = (body or {}).get("keys") or None

    async def gen():
        items: list[dict] = []
        try:
            async for item in daily.run(_session, keys):
                items.append(item)
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'act': '__done__',
                                      'ok': bool(items) and all(i['ok'] for i in items),
                                      'items': items}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False,
                                      'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- 免费礼物 + 变卡（card.qzone.qq.com，songli.har 实证） ----
# 送礼进日常（daily.sendgift）；收礼箱读取/收礼→变卡；手动变卡；礼物箱→变卡自动循环。

@app.get("/api/gift/friends")
async def gift_friends_api():
    """好友列表（送礼 uin 池）。只读。"""
    if err := _require_login():
        return err
    try:
        return await gf.gift_friends(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.post("/api/gift/send")
async def gift_send_api(body: dict | None = None):
    """送礼（送 48 面卡 freegift_id=81）。会真实发出。

    body 可选：{"friends":["qq",...]} 指定收礼人；不传 = 自动拉好友列表全送。
    """
    if err := _require_login():
        return err
    friends = (body or {}).get("friends")
    try:
        return await gf.gift_send(_session,
                                 friends=[str(x) for x in friends] if friends else None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"送礼异常：{e}"}


@app.get("/api/gift/inbox")
async def gift_inbox_api():
    """礼物箱列表（freegift_get_list）。只读。count=当前件数（上限 50）。"""
    if err := _require_login():
        return err
    try:
        return await gf.gift_inbox(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.get("/api/gift/box_cards")
async def gift_box_cards_api():
    """只读：列出卡箱/保险箱里**同礼物套（theme=487）的 48 面卡**。

    就是「卡箱优先变卡」那一步的取源（2026-09-26 用户反馈：以前箱里躺着礼物送的
    48 面卡一张都没被用）。非零 count 即说明变卡时可以直接拿它们当 srcid，
    不必先 freegift_take 收礼。无副作用。
    """
    if err := _require_login():
        return err
    try:
        cards = await gf.find_box_gift_cards(_session)
        return {"code": 0, "count": len(cards), "cards": cards,
                "theme": gf.GIFT_THEME, "face": sorted(gf._face48_ids())}
    except Exception as e:  # noqa: BLE001
        return {"code": -1, "message": f"查询异常：{e}"}


@app.post("/api/gift/take")
async def gift_take_api(body: dict):
    """收一条礼物（freegift_take）→ 得到礼物卡 7773/7774。会真实消耗该条。

    body: {"index_id": <id>, "action": 2}  action=2→7773(蚕丝) / 1→7774(五彩线)
    """
    if err := _require_login():
        return err
    try:
        return await gf.gift_take(_session, int(body.get("index_id") or 0),
                                 int(body.get("action") or 2))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"收礼异常：{e}"}


@app.get("/api/gift/change_target")
async def gift_change_target_api(tids: str = ""):
    """只读：按「在炼队列（或 tids 指定的套卡）+ 实时库存」算下一步**会自动变哪张卡**。

    面板用它显示自动变卡目标（`_pick_change_dst` 同一口径），**不消耗任何卡**。
    tids 省略时用当前引擎队列；引擎没启动也没关系（这里只做纯计算）。
    """
    if err := _require_login():
        return err
    try:
        from .auto_refine import face40_gap, pick_change_target
        ids = [int(x) for x in re.split(r"[,\s]+", (tids or "").strip()) if x.strip().isdigit()]
        if not ids and _auto:
            ids = list(_auto.queue)
        if not ids:
            return {"code": 0, "ok": False, "tids": [], "pick": None,
                    "message": "没有在炼的套卡队列（启动自动炼卡，或传 tids=1652,1810）"}
        eng = AutoRefine(None, ids)        # 只为纯计算挑目标，不需要登录态
        mp = await _session.post(EP_MAINPAGE, code="", opuin=_session.uin)
        if mp.get("code") != 0:
            return {"code": -1, "message": "读取卡箱状态失败"}
        st = parse_full(mp.get("raw", ""))
        stock: Counter = Counter()
        for c in st["change"] + st["store"]:
            stock[c["id"]] += 1
        pending: Counter = Counter(c["id"] for c in st["stove"] if c["id"] > 0)
        pick = pick_change_target(eng.plans, eng.queue, stock, pending, set(eng._keep))
        gaps = [{"tid": t, "theme": eng.plans[t].name,
                 **face40_gap(eng.plans[t], stock, pending)} for t in eng.queue]
        return {"code": 0, "ok": bool(pick), "tids": ids, "queue": list(eng.queue),
                "pick": pick, "face40": gaps,
                "message": (pick or {}).get("target",
                                            "这一套的 40 面卡已经够了，本轮不动箱里的礼物卡")}
    except Exception as ex:  # noqa: BLE001
        return {"code": -1, "message": f"计算异常：{ex}"}


@app.post("/api/gift/box_change")
async def gift_box_change_api(body: dict | None = None):
    """用**卡箱里已有的**礼物套 48 面卡就地变卡（不收礼）。会消耗 1 张源卡。

    这就是自动炼卡里「卡箱优先变卡」那一步的手动入口（2026-09-26 用户反馈：以前
    箱里躺着礼物送的 48 面卡一张都没被用，变卡只认 freegift_take 收出来的那张）。
    body: {"dstid": 目标卡}。省略 / 填 0 = **自动挑**「当前在炼的套卡里缺的那张 40 面卡」
    （同一个口径见 auto_refine.pick_change_target），与自动流程保持一致 —— 写死某个卡
    很容易变成「变出来的卡不属于在炼的那套」，白烧一张源卡。变卡约 50% 成功。
    """
    if err := _require_login():
        return err
    b = body or {}
    try:
        dstid = int(b.get("dstid") or 0)
        if not dstid:
            from .auto_refine import pick_change_target
            eng = _auto
            pick = (pick_change_target(eng.plans, eng.queue, eng._stock, eng._pending,
                                       set(eng._keep)) if eng else None)
            if not pick:
                return {"ok": False, "skipped": True, "message": (
                    "这一套的 40 面卡已经够了（或没有 40 面候选），不动手" if _auto else
                    "自动炼卡没启动，没有在炼的套卡队列 —— 不知道该变哪张卡；"
                    "先启动自动炼卡，或手动传 dstid")}
            dstid = int(pick["dst"])
        return await gf.drain_box_gift_cards(_session, dstid)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"变卡异常：{e}"}


@app.post("/api/gift/take_change")
async def gift_take_change_api(body: dict):
    """收一条礼物 → 自动定位槽位 → 变卡（一步完成，前端按钮用）。

    body: {index_id, action?, dstid?}  action=2 默认；dstid 默认 26117（40 面卡）
    """
    if err := _require_login():
        return err
    b = body or {}
    try:
        return await gf.take_and_change(
            _session, int(b.get("index_id") or 0),
            int(b.get("dstid") or gf.DEFAULT_CHANGE_DST),
            int(b.get("action") or 2))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"收礼变卡异常：{e}"}


@app.post("/api/gift/change")
async def gift_change_api(body: dict):
    """手动变卡（card_user_transfer_card）。

    body: {srcid, dstid, prop?, slotid?, slottype?}
      srcid = 源：48 面礼物卡(7773/7774/7775…) / 卡箱卡 id / 道具卡 id（100/200/600 面额）
      dstid = 目标卡 id（默认 26117 = 40 面卡）。**只要是面额比源卡小的卡都行**，
              不必和自动炼卡同一套；某些套卡本身不支持变 → 服务端直接拒（按返回码报错）。
      prop  > 0 → 走「道具卡变卡」形态（HAR 实证：prop 与 srcid 都填道具 subtype，
              如 prop=7&srcid=7=100面值卡；此时道具卡不在卡箱里，跳过槽位定位、直接发）。
    普通卡箱卡：服务端要**源卡的真实槽位**，这里自动读 mainpage 定位
      （固定槽位会返 -32002「找不到相应的记录」，2026-09-21 实证）。
    成功率约 50%，失败按返回判定。会真实消耗 srcid。
    """
    if err := _require_login():
        return err
    b = body or {}
    srcid = int(b.get("srcid") or 0)
    dstid = int(b.get("dstid") or gf.DEFAULT_CHANGE_DST)
    prop = int(b.get("prop") or 0)
    try:
        if prop > 0:
            # 道具卡变卡：道具卡在道具箱、不占卡箱槽位，slotid/slottype 传 0 即可（见 HAR）
            return await gf.change_card(
                _session, srcid, dstid,
                slotid=int(b.get("slotid") or 0), kind=int(b.get("kind") or 0),
                type_=int(b.get("type") or 2),
                slottype=int(b.get("slottype") or 0), prop=prop)
        return await gf.change_any(_session, srcid, dstid)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"变卡异常：{e}"}


@app.get("/api/world/status")
async def world_status(w: str = "xiyou"):
    """猫咪探险（「每日比赛」下的两个大地图）状态，只读。

    w=xiyou  → `card_worldexplore`  act=1（西游；小地图＝套卡，如花果山）
    w=sanguo → `card_worldsanguo`   act=1（三国；小地图＝套卡，如官渡之战）

    端点名的来源：card_run_base.version_list 里 XiYouModule_v_41.swf /
    SanGuoModule_v_14.swf，解压后在其字符串常量中找到这两个 cgi 名（另有
    card_travel_elf_competition = 每日比赛）。

    ⚠️ **只做状态查询**：「出发探索」的 act 尚未定位（西游 act=2/4 恒返
    -30002「队伍忙碌中」、三国 act=2 levelid=101 返 -302「未达到领取条件」），
    所以这里不提供任何写操作 —— 需要抓一次真实点击的包才能定。
    """
    if err := _require_login():
        return err
    ep = EP_WORLD_SANGUO if w == "sanguo" else EP_WORLD_EXPLORE
    try:
        r = await _session.post(ep, act=1)
        data = r.get("data") or {}
        if not data and r.get("raw"):
            try:
                data = json.loads(r["raw"])
            except (ValueError, TypeError):
                data = {}
        lv = data.get("levelId") if w == "sanguo" else data.get("maxLevelId")
        return {
            "ok": r.get("code") == 0,
            "code": r.get("code"),
            "message": r.get("message", ""),
            "world": w,
            "endpoint": ep,
            "level": lv,
            # 关键计数：三国 freenum/buynum = 免费次数/已购次数；
            # 西游 nlb/vlb/txd/sjb/jjb/zxb = 各类能量包与材料（实测本号全为 0）
            "counts": {k: data.get(k) for k in
                       ("freenum", "buynum", "nlb", "vlb", "txd", "sjb", "jjb", "zxb")
                       if k in data},
            "raw": data,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.get("/api/world/levels")
async def world_levels(w: str = "sanguo"):
    """猫咪探险关卡进度表（三国 act=9 / 西游同族 act），只读。"""
    if err := _require_login():
        return err
    ep = EP_WORLD_SANGUO if w == "sanguo" else EP_WORLD_EXPLORE
    try:
        r = await _session.post(ep, act=9)
        # parse_response 会把整个响应体塞进 r["data"]，真正的关卡数组是 data["data"]
        body = r.get("data") or {}
        if not isinstance(body, dict):
            try:
                body = json.loads(r.get("raw") or "{}")
            except (ValueError, TypeError):
                body = {}
        data = body.get("data") or []
        return {"ok": r.get("code") == 0, "code": r.get("code"),
                "world": w, "total": len(data), "levels": data}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.post("/api/collection/progress")
async def collection_progress():
    """集卡册进度（card_mini_get，只读）。

    返回全库套卡的 {theme_id, 持有数 num, 集卡册等级 lv}，按持有数倒序。
    """
    if err := _require_login():
        return err
    try:
        r = await _session.post(EP_MINI_GET)
        raw = r.get("raw", "")
        tmap = {t["tid"]: t for t in cq.indexes()["themes"]}
        rows = []
        for m in re.finditer(r'<Node\s+theme_id="(\d+)"\s+num="(\d+)"\s+lv="(\d+)"', raw):
            tid = int(m.group(1))
            t = tmap.get(tid) or {}
            rows.append({
                "theme_id": tid,
                "name": t.get("name") or f"#{tid}",
                "num": int(m.group(2)),
                "lv": int(m.group(3)),
                "kind": t.get("kind") or "",
                "size": len(t.get("cards") or []),
            })
        rows.sort(key=lambda x: (-x["num"], x["theme_id"]))
        return {"ok": r.get("code") == 0, "code": r.get("code"),
                "total": len(rows), "themes": rows}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.post("/api/cat/harvest")
async def cat_harvest():
    """灵宠探险：收获宝箱（card_user_spirite act=4）。

    实测可真实获得道具（碎片 / 灵力果）。**探索不足约 5 分钟返回 -1 系统繁忙**，
    要等满 5 分钟再收（自动探险会自动等到）。这是当前可直接自动化的活动操作。
    """
    if err := _require_login():
        return err
    try:
        r = await _session.post(EP_SPIRITE, act=4, opuin=plain_uin(_session))
        res = r.get("data") or r.get("raw", "")
        send = []
        if isinstance(res, dict):
            send = res.get("send") or (res.get("data") or {}).get("send") or []
        # items：把 send 里的道具编号翻译成「灵力果×2」这样的可读文本（见 prop_dict）
        return {"ok": r.get("code") == 0, "code": r.get("code"),
                "message": r.get("message", ""),
                "items": pd.describe_all(send),
                "result": res}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求异常：{e}"}


@app.post("/api/items/name")
async def items_set_name(key: str, name: str):
    """把一条**已核实**的道具名写进覆盖表（data/item_names_extra.json）并热生效。

    官方静态配置里查不到的大类（已知 78）只能这样补 —— 名字必须来自实证：
    游戏界面实际显示的、或 server 响应里带的。禁止凭猜（项目铁律）。
    例：POST /api/items/name?key=78_0&name=XXX
    """
    try:
        return {"ok": True, **iid.set_extra(key, name)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}


@app.get("/api/items/unknown")
async def items_unknown():
    """当前覆盖表里已核实的名字（用于确认写入生效）。"""
    extra = iid.load_extra()
    return {"ok": True, "count": len(extra), "items": extra,
            "total": iid.count()}


@app.get("/api/props")
async def props_info():
    """我的道具清单（card_market_props_info，无参数）。

    服务端返回 `<prop type="1" subtype="N" count="X">`，`subtype` 即道具卡 id
    （升炉卡/开箱卡/面值卡/租炉卡…），名字来自 propCard_v_26.xml（见 prop_dict）。
    """
    if err := _require_login():
        return err
    try:
        r = await _session.post(EP_PROPS_INFO)
        raw = r.get("raw") or ""
        items = []
        for m in re.finditer(r"<prop\b([^>]*)>", raw):
            a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            try:
                pid = int(a.get("subtype") or 0)
                cnt = int(a.get("count") or 0)
            except ValueError:
                continue
            items.append({"id": pid, "count": cnt,
                          "name": pd.prop_name(pid) or f"道具{pid}"})
        items.sort(key=lambda x: -x["count"])
        return {"ok": r.get("code") == 0, "code": r.get("code"), "props": items}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求异常：{e}"}


@app.post("/api/cat/explore")
async def cat_explore(body: dict):
    """灵宠探险：出发（card_user_spirite act=3）。

    body: {section_id(**小关**序号), ids(精灵 id), pass_id(**大关**序号)} —— 三者**必须齐全**，
    缺一即 -2；explore_pass_id = section_id*100 + pass_id（服务端似不校验它本身）；
    每次出发扣 1 次 `free_explore_cnt`，**free=0 时会改扣 `permanent`，不会报错**。

    ⚠️ 参数语义（2026-09-14 用户实证校正，此前注释写反了）：
      - `pass_id` = **大关**（大地图）序号：1=文明古国 … 6=世界古遗迹 … 11=悬崖之巅（未开）
      - `section_id` = **大关内的小关**（小地图）序号
      依据：用户在大关 6 第 9 小关时，`act=1` 返回 `section_id=9 / pass_id=6`，两项各自吻合。

    注：ids 支持逗号分隔多只（真机抓包确认 `ids=9,10` 一次派两只，玩法上限 2 只）；
    服务端按逗号串解析，'|' 分隔只会取首值、JSON 数组会 -2，故统一用逗号。
    """
    if err := _require_login():
        return err
    try:
        r = await _session.post(
            EP_SPIRITE, act=3, opuin=plain_uin(_session),
            section_id=int(body.get("section_id") or 1),
            ids=body.get("ids") or 1,
            pass_id=int(body.get("pass_id") or 1),
        )
        return {"ok": r.get("code") == 0, "code": r.get("code"),
                "message": r.get("message", ""),
                "result": r.get("data") or r.get("raw", "")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"请求异常：{e}"}


@app.get("/api/spirite/maps")
async def spirite_maps_api():
    """灵宠探险：大地图 / 小关 / 灵宠名表（本地静态，无需登录）。

    数据源是**官方客户端配置** `data/spirite_pass.json`（由公开静态 XML
    `appimg2.qq.com/card/mk/soul_elf_v_78.xml` 导出，见 `spirite_maps` 模块说明）：
      - `table`：大关 → 小关（官方顺序，含 max_spirite_num / 金钥匙 / 藏宝图 / 血量消耗）
      - `maps` ：大关编号列表（官方 11 章）
      - `spirits`：灵宠官方名表（16 只：金木水火土风光电 × 雄雌）
    """
    # 官方 XML 里灵宠名有笔误式空格（如 id=9 =「土灵宠 （雄）」），统一去掉所有空白
    return {"ok": True, "maps": list(spm.MAP_IDS), "table": spm.stage_table(),
            # 挂在同一个「灵宠探险」入口下的另外两套玩法（界面同页切换，不单独开页）：
            #   islands = 荒岛     (c_island)  8 章 48 关，elf_num 3~8 + elf_hp（战斗）
            #   across  = 穿越丛林 (c_across) 11 章 62 关，elf_num 1~6 + elf_lv（等级门槛）
            # 两者关卡号均为「连续编号」（跨章不重置），键是 chapter_id。
            "islands": spm.island_table(),
            "across": spm.across_table(),
            "spirits": [{"id": int(s["id"]),
                         "name": re.sub(r"\s+", "", str(s["name"]))}
                        for s in spm.spirits()]}


# ---- 荒岛 / 穿越丛林 单次操作（2026-09-15 用户抓包 + 真调确定） ----


@app.post("/api/spirite/island/dispatch")
async def island_dispatch(body: dict):
    """荒岛（官方 c_island）出发：`card_user_spirite act=14`。

    真机抓包：`type=1&act=14&id=16&ids=1,3,5,7,9`

      - `id` = **连续编号（1..48）**，不是章内序号 —— 16 恰好是「戴尔得荒岛4」
        （第 3 章第 4 关），与该关 elf_num=5 一起逐项对上了用户截图。
      - `ids` = 逗号串灵宠 id；本关需要几只由官方 `elf_num` 决定（3~8）。
      - `type=1` = 出发。收获另走 act=15（见 `/api/spirite/island/harvest`）。

    body: {
      seq?: 16            连续编号（优先）
      chapter?: 3         或给 章号 + index，后端换算成连续编号
      index?: 4
      ids?: [1,3,5,7,9]   不填=按该关 elf_num 自动挑（优先高 id）
      dry_run?: false     只算参数不发请求
    }
    ⚠️ 会消耗**今日荒岛探险次数**（游戏内 5 次/天，VIP 更多）。
    """
    if err := _require_login():
        return err
    b = body or {}
    seq = int(b.get("seq") or 0)
    if not seq and b.get("chapter") and b.get("index"):
        seq = spm.island_seq(b.get("chapter"), b.get("index"))
    if not seq:
        return {"ok": False, "message": "需要 seq（连续编号），或 chapter + index"}
    place = spm.island_place(seq)
    if not place:
        return {"ok": False, "message": f"荒岛没有编号 {seq} 的关卡（官方配置 1..48）"}
    ids = [int(x) for x in (b.get("ids") or [])]
    if not ids:
        q = await _session.post(EP_SPIRITE, act=1, opuin=plain_uin(_session))
        sps = sorted((q.get("data") or {}).get("spirites") or [],
                     key=lambda x: -(x.get("id") or 0))
        need = int(b.get("num") or place.get("elf_num") or len(sps))
        ids = [int(s.get("id") or 0) for s in sps[:max(1, need)]]
    if not ids:
        return {"ok": False, "message": "没有可用灵宠"}
    ids_str = ",".join(str(x) for x in ids)
    tag = f"{spm.island_place_text(seq)} / 灵宠 {ids_str}"
    if b.get("dry_run"):
        return {"ok": True, "dry_run": True, "params": {"act": 14, "type": 1, "id": seq,
                                                       "ids": ids_str}, "label": tag}
    r = await _session.post(EP_SPIRITE, act=14, type=1, id=seq, ids=ids_str,
                            opuin=plain_uin(_session))
    ok = r.get("code") == 0
    return {"ok": ok, "code": r.get("code"),
            "message": r.get("message", ""), "label": tag, "result": r.get("data")}


@app.post("/api/spirite/island/harvest")
async def island_harvest():
    """荒岛（及所有进行中的灵宠探险）收获：`card_user_spirite act=15`，**无参数**。

    实测：调用后 `explore.hy_explore_ts` 归零、`hy_put_spirites` 清空，并真实入包。
    早于 3 分钟收获返回 -2。注意大地图的 `act=4` 对荒岛无效（返回 -5）。
    """
    if err := _require_login():
        return err
    r = await _session.post(EP_SPIRITE, act=15, opuin=plain_uin(_session))
    res = r.get("data") or {}
    content = res.get("content") if isinstance(res, dict) else None
    send = (res.get("send") or []) if isinstance(res, dict) else []
    return {"ok": r.get("code") == 0, "code": r.get("code"),
            "message": r.get("message", ""),
            "items": pd.describe_reward(content) if content else pd.describe_all(send),
            "result": res}


@app.post("/api/spirite/across/run")
async def across_run(body: dict):
    """穿越丛林（官方 c_across）历练：`card_user_spirite act=11`，**即时到账**。

    真机抓包 `sub_id=3&act=11&cnt=1&id=5` —— 用户当时点的是「洪泛森林4」。

      - `id`     = **1 级地图编号**（官方 c_across 的 chapter，共 11 个）
      - `sub_id` = **2 级地图编号**（chapter 下的 section，章内关卡）

    两者都是 **0-based**，与官方配置一一对应：`id = open_c - 1`、`sub_id = open_s/2 - 1`
    （洪泛森林4 的 `open_c/open_s = 6/8` → `(3,5)`，与抓包逐字一致）。

    错误码：(4,5)(5,5)→-2（该关未解锁）/ (4,6)(5,6)→-1（该 1 级地图未开启）
    / 2 级编号越界 → 空返回且不计次。

    奖励在返回的 `content` 里（`大类id_类内编号id_数量`，逗号分隔）：
    59=金钥匙 / 72=银锭 / 73=百变丹（名字查官方统一表 prop_dict.reward_name）。

    body: {chapter: 6, index: 4, dry_run?: false}
    ⚠️ 每次成功扣 1 点 `torrid_zone_explore_cnt`（游戏内 10 次/天，VIP 更多）。
    """
    if err := _require_login():
        return err
    b = body or {}
    ch, idx = int(b.get("chapter") or 0), int(b.get("index") or 0)
    if not (ch and idx):
        return {"ok": False, "message": "需要 chapter（章号）与 index（关内序号）"}
    place = spm.across_place_by_ci(ch, idx)
    if not place:
        return {"ok": False, "message": f"穿越丛林没有 第{ch}章 第{idx}关（官方 11 章 62 关）"}
    sub_id, aid = spm.across_param(ch, idx)
    label = f"{place.get('chapter_name')} 第{idx}关 {place.get('name')}"
    if b.get("dry_run"):
        return {"ok": True, "dry_run": True, "label": label,
                "params": {"act": 11, "sub_id": sub_id, "id": aid, "cnt": 1}}
    r = await _session.post(EP_SPIRITE, act=11, sub_id=sub_id, id=aid, cnt=1,
                            opuin=plain_uin(_session))
    res = r.get("data") or {}
    content = res.get("content") if isinstance(res, dict) else None
    ok = bool(r.get("code") == 0 and content)
    return {"ok": ok, "code": r.get("code"),
            "message": r.get("message", ""), "label": label,
            "params": {"sub_id": sub_id, "id": aid, "cnt": 1},
            "content": content,
            "items": pd.describe_reward(content) if content else [],
            "text": pd.summarize_reward(content) if content else "",
            "result": res}


# ---- 猫咪旅行自动循环（2026-09-20 HAR 实证） ----
# 端点 card_travel_elf_main（只读状态）/ card_travel_elf（act=5 出发 / 23 领取礼物）。
# 仅收录已实证动作（见 travel_elf_auto.py 顶部注释 + REFERENCE.md 猫咪旅行段）。


# ── 猫咪旅行辅助数据缓存（2026-09-29 性能优化）──
# state 路由原本串行发 5 个上游请求（main/act=3/act=32/main 重复/act=12），每个都过
# 1.2s 全局节流 → 点开要等 ~6s。优化：① collect_water_targets 复用已拉的 main（去重）；
# ② 食物(act=3)/任务(act=32)/浇水名单(act=12) 加进程内 TTL 缓存 —— 它们变化频率低
# （买食物/领任务/浇水后才变），state 核心的猫状态(main)仍每次实时拉。
# 任一写动作（run/clover/harvest/water/tasks）完成后立即失效，宁多拉一次不显示旧值。
_TRAVEL_AUX_TTL = 45.0
_travel_aux_cache: dict = {"ts": 0.0, "food": None, "tasks": None}


def _travel_aux_invalidate() -> None:
    _travel_aux_cache["ts"] = 0.0


@app.get("/api/travel/state")
async def travel_state():
    """猫咪旅行只读状态：猫状态 + 免费次数 + 食物持有 + A级食物 + 每日任务。"""
    if err := _require_login():
        return err
    try:
        import time as _time_mod
        st = await te.get_state(_session)
        if st is None:
            return {"ok": False, "message": "读取状态失败"}
        now = int(__import__("time").time())
        elves = []
        for c in st.get("travel_elf_arr") or []:
            end = int(c.get("travel_end_ts") or 0)
            if end > 0 and now < end:
                status = "traveling"
            elif end > 0:
                status = "returned"   # 已返回，待收获
            else:
                status = "idle"       # 空闲，可出发
            elves.append({
                "elf_id": c.get("elf_id"),
                "status": status,
                "travel_end_ts": end,
                "travel_limit_cnt": c.get("travel_limit_cnt"),
                "food_id": c.get("food_id"),
                "food_num": c.get("food_num"),
            })
        # 食物持有（act=3）+ A 级食物（静态配置解析）—— TTL 缓存
        aux = _travel_aux_cache
        aux_ok = aux["ts"] > 0 and (_time_mod.monotonic() - aux["ts"]) < _TRAVEL_AUX_TTL
        try:
            if aux_ok and aux["food"] is not None:
                food_owned = aux["food"]
            else:
                food_owned = await te.get_food_config(_session)
                aux["food"] = food_owned
        except Exception:  # noqa: BLE001
            food_owned = aux["food"] or {}
        a_grade = te.resolve_a_grade_foods()
        # 每日任务（act=32；task_bits = 已领取掩码）—— TTL 缓存
        try:
            if aux_ok and aux["tasks"] is not None:
                info = aux["tasks"]
            else:
                info = await te.get_daily_tasks(_session)
                aux["tasks"] = info
        except Exception:  # noqa: BLE001
            info = aux["tasks"] or {"tasks": [], "bits": 0}
        bits = int(info.get("bits") or 0)
        task_rows = []
        for t in info.get("tasks") or []:
            idx = int(t.get("index") or 0)
            cnt = int(t.get("cnt") or 0)
            lim = te.DAILY_TASK_LIMIT.get(idx, 0)
            claimed = bool(bits & (1 << idx))
            task_rows.append({
                "index": t.get("index"), "cnt": cnt, "type": t.get("type"),
                "limit": lim, "claimed": claimed,
                "done": bool(lim and cnt >= lim),
                "claimable": bool(lim and cnt >= lim and not claimed),
            })
        # 浇水记录（main 的 travel_watered_arr = 今日已浇过水的好友 [{ts,uin}]）
        watered = st.get("travel_watered_arr") or []
        # 免费旅行次数口径（唯一判定入口 te.compute_free_counts）：
        #   main.free_cnt = 今日**已用**次数；上限 = 猫数 × 3（4 猫 = 12，每猫每日 3 次）；
        #   剩余 = Σ max(0, 3 − 该猫已旅行次数)。⚠️ travel_cnt_limit=8 不是这个上限。
        fc = te.compute_free_counts(st)
        free_limit, free_used, free_left = fc["free_limit"], fc["free_used"], fc["free_left"]
        # 可浇好友名单：**不再随 state 拉**（2026-09-29 用户定调「好友分开、各拉各的，
        # 不串行，上面不显示好友相关」）——act=12 名单走独立路由 /api/travel/friends
        # （点「好友列表」按钮才拉），浇水动作路由内部自会实时筛。state 里只保留
        # watered_today（来自 main 的 travel_watered_arr，零额外请求）。
        if not aux_ok:
            aux["ts"] = _time_mod.monotonic()
        return {"ok": True,
                "free_cnt": st.get("free_cnt"),
                "free_limit": free_limit,
                "free_used": free_used,
                "free_left": free_left,
                "free_per_cat_limit": fc.get("per_cat_limit"),
                "pay_cnt": st.get("pay_cnt"),
                "dui_cnt": st.get("dui_cnt"),          # 兑换旅行次数（SWF 面板「兑换旅行次数：」）
                "day_limit_cnt": st.get("day_limit_cnt"),
                "clover_num": st.get("clover_num"),
                "fenmo_num": st.get("fenmo_num"),
                "can_water_times": st.get("can_water_times"),
                "watered_today": len(watered),
                "watered_uins": [str(x.get("uin")) for x in watered],
                "food_owned": food_owned,
                "food_names": te.resolve_food_names(),
                "a_grade_foods": a_grade,
                "task_bits": bits,
                "tasks": task_rows,
                "elves": elves}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"查询异常：{e}"}


@app.post("/api/travel/run")
async def travel_run(body: dict | None = None):
    """跑一轮猫咪旅行：收获已返回猫 → 免费次数出发空闲猫(各喂一种A级食物,不够则购买)
       → 收三叶草 → 给好友浇水 → 领取已完成的每日任务。

    body 可选：
      food_num?: 4          每只猫投喂数量（默认 4）
      harvest_only?: false  只收获不出发
      dry_run?: false       只算参数不发请求（先验证逻辑）
      do_clover?: true      是否收三叶草（act=4）
      do_water?: true       是否去好友家浇水（act=12 拉已开通列表 → 逐个 act=21）
      do_tasks?: true       是否领取已完成的每日任务（act=33）
    ⚠️ 会真实消耗免费旅行次数、购买食物、发起旅行/领取礼物/浇水，谨慎调用。
    """
    if err := _require_login():
        return err
    b = body or {}

    async def gen():
        try:
            async for step in te.run_once(
                _session,
                food_num=int(b.get("food_num") or te.DEFAULT_FOOD_NUM),
                harvest_only=bool(b.get("harvest_only")),
                dry_run=bool(b.get("dry_run")),
                do_clover=bool(b.get("do_clover", True)),
                do_water=bool(b.get("do_water", True)),
                do_tasks=bool(b.get("do_tasks", True)),
            ):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'运行异常：{e}'}, ensure_ascii=False)}\n\n"
    if not b.get("dry_run"):
        _travel_aux_invalidate()
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/travel/clover")
async def travel_clover():
    """单独收三叶草（act=4）。"""
    if err := _require_login():
        return err
    try:
        r = await te.harvest_clover(_session)
        _travel_aux_invalidate()
        return {"ok": r.get("code") == 0, "code": r.get("code"),
                "clover_num": r.get("clover_num"),
                "message": r.get("message") or r.get("desc") or ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/travel/harvest")
async def travel_harvest(body: dict | None = None):
    """手动收获旅行：领取已返回猫的礼物（act=23）。

    body 可选：elf_id?: 只收该只猫（不传=收获所有已返回的猫）。
    ⚠️ 真实动作：会给已返回的猫领礼物并让该猫回到空闲。
    """
    if err := _require_login():
        return err
    b = body or {}
    try:
        eid = b.get("elf_id")
        items = await te.harvest_returned(_session, elf_id=int(eid) if eid else None)
        _travel_aux_invalidate()
        return {"ok": all(i.get("ok") for i in items), "items": items}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/travel/friends")
async def travel_friends():
    """拉取「已开通猫咪旅行玩法」的好友列表 + 各自三叶草状态（act=12，只读）。

    猫咪旅行不是所有好友都开通：客户端把**全量好友**（card_user_list）发给 act=12，
    服务端只回**开通者**（uin_tag_arr）。每条带 `data`：
      - `None`         → 未种三叶草 → 不能浇
      - `"<ts>_0"`     → 已种，ts=成熟时间；ts>now = 未成熟（可浇）
    外加「是否今日已浇」（自己 main 的 travel_watered_arr）。
    """
    if err := _require_login():
        return err
    try:
        col = await te.collect_water_targets(_session)
        if not col.get("opened"):
            return {"ok": False, "message": "未取到已开通猫咪旅行的好友"}
        friends = col.get("all") or []
        return {"ok": True, "total": col.get("opened"), "count": len(friends),
                "watered_today": col.get("watered_today"),
                "waterable": len(col.get("targets") or []),
                "targets": col.get("targets") or [],
                "friends": friends}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/travel/water")
async def travel_water(body: dict | None = None):
    """去好友家浇水跑一轮（逐个 act=21）。

    目标 = act=12 里**已开通 + 已种三叶草且未成熟 + 今日未浇过**的好友（自动排除自己）。
    规则（用户明确）：只有种了三叶草且未成熟才能浇；每个好友每天只浇一次；
    每人每天被浇次数有上限（浇前读好友主页 can_water_times，为 0 跳过）。
    body 可选：only?: 指定 uin 列表（不传=按规则自动筛）。
    """
    if err := _require_login():
        return err
    b = body or {}
    try:
        col = await te.collect_water_targets(_session)
        only = b.get("only")
        targets = [str(u) for u in only] if only else list(col.get("targets") or [])
        if not targets:
            return {"ok": True, "opened": col.get("opened"), "targets": 0,
                    "ok_cnt": 0, "fail": 0, "skipped": 0, "stopped": False,
                    "results": [],
                    "message": f"无可浇好友（已开通 {col.get('opened')} 个，今日已浇 "
                               f"{col.get('watered_today')} 个，其余未种三叶草/已成熟）"}
        r = await te.water_targets(_session, targets)
        if r.get("ok_cnt"):
            _travel_aux_invalidate()
        return {"ok": (r.get("ok_cnt") or 0) > 0, "opened": col.get("opened"),
                "targets": len(targets), "tried": r.get("tried"),
                "ok_cnt": r.get("ok_cnt"), "fail": r.get("fail"),
                "skipped": r.get("skipped"), "stopped": r.get("stopped"),
                "results": r.get("results"),
                "message": "连续被拒，已提前收手" if r.get("stopped") else ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/travel/tasks")
async def travel_tasks():
    """领取可领的每日任务（act=32 查 + act=33 领；cnt>=limit 且未领过才领）。"""
    if err := _require_login():
        return err
    try:
        info = await te.get_daily_tasks(_session)
        bits = int(info.get("bits") or 0)
        claimed = []
        for t in info.get("tasks") or []:
            idx = int(t.get("index") or 0)
            cnt = int(t.get("cnt") or 0)
            lim = te.DAILY_TASK_LIMIT.get(idx, 0)
            if lim and cnt >= lim and not (bits & (1 << idx)):
                r = await te.claim_daily_task(_session, idx)
                claimed.append({"index": idx, "ok": r.get("code") == 0,
                                "code": r.get("code"),
                                "message": r.get("message") or r.get("desc") or ""})
        if claimed:
            _travel_aux_invalidate()
        return {"ok": True, "claimed": claimed}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


# ---- 灵宠探险自动循环（2026-09-14）----


@app.post("/api/spirite/auto/start")
async def spirite_auto_start(body: dict):
    """启动灵宠探险自动循环（三套玩法共用，由 `mode` 选择）。

    body: {
      mode?: "map"        玩法：map=大地图 / island=荒岛 / across=穿越丛林
      # --- map ---
      pass_id?: 1         大地图（1=文明古国 … 6=世界古遗迹 … 11=悬崖之巅）
      section_id?: 1      该大地图内的小关
      min_wait?: 300      最短探索时长秒（低于 ~5 分钟收获会 -1）
      # --- island（act=14 出发 / act=15 收获）---
      island_seq?: 16     连续编号 1..48（= act=14 的 id）
      island_need?: 5     该关需要几只灵宠（默认取官方 elf_num）
      min_wait?: 180      荒岛探险 3 分钟
      # --- across（act=11，即时到账，无收获）---
      across_chapter?: 6  章号（1..11）
      across_index?: 4    关内序号
      daily_max?: 10      每日上限（across 默认 10；island 默认不限）
      # --- 通用 ---
      spirite_ids?: [..]  只用这些精灵（不填=全体轮换）
      spirite_num?: 2     每次派几只（map 实际 1/2/4 由关卡官方 max_spirite_num 定）
      auto_dispatch?: true  收到后自动派下一只（false=只收获不派出）
      max_rounds?: 0      最多派出几轮，0=不限（次数用完自然停）
      interval?: 30       轮询间隔秒
      dry_run?: false     模拟：只查状态、不真发出发请求
    }
    """
    if err := _require_login():
        return err
    mode = str((body or {}).get("mode") or "map")
    eng = get_engine(_session, mode)
    return eng.start(**(body or {}))


@app.post("/api/spirite/auto/stop")
async def spirite_auto_stop(body: dict = None):
    if err := _require_login():
        return err
    mode = str(((body or {}) or {}).get("mode") or "") if body else ""
    if mode in MODES:
        return get_engine(_session, mode).stop()
    # 不带 mode → 停止全部正在跑的玩法
    stopped = {}
    for m in MODES:
        eng = get_engine(_session, m)
        if eng.running:
            stopped[m] = eng.stop()
    return {"ok": True, "message": "已请求停止全部", "stopped": stopped}


@app.get("/api/spirite/auto/status")
async def spirite_auto_status():
    if err := _require_login():
        return err
    modes = {}
    for m in MODES:
        st = get_engine(_session, m).status()
        st["mode"] = m
        modes[m] = st
    return {"ok": True, "modes": modes}


# ---- 挖地（card_user_dig，2026-09-20 抓包 + 真机实证）----
# 端点语义见 backend/dig_auto.py 顶部注释：act=1 状态 / act=2 挖格子 / act=18 一键收获 /
# act=7 收矿机。前端独立页 #pane-dig（网格手动点 + 自动挖）。


@app.get("/api/dig/state")
async def dig_state():
    """挖地只读状态：8×7 网格 + 资源 + 自动挖进度。"""
    if err := _require_login():
        return err
    try:
        st = await dg.get_state(_session)
        if st is None:
            return {"ok": False, "message": "读取挖地状态失败（可能限流，稍后重试）"}
        st["ok"] = True
        st["auto"] = dg.auto_status()
        return st
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/cell")
async def dig_cell(body: dict):
    """挖一格。body: {line, col, dig_type}（dig_type 1=锄头 2=一字型炸药 3=九宫格炸药）。"""
    if err := _require_login():
        return err
    b = body or {}
    try:
        r = await dg.dig_cell(_session, int(b.get("line")), int(b.get("col")),
                              int(b.get("dig_type") or 1))
        r["state_auto"] = dg.auto_status()
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/collect")
async def dig_collect():
    """act=18 一键收获：收走所有已露出的礼物。"""
    if err := _require_login():
        return err
    try:
        r = await dg.collect(_session)
        st = await dg.get_state(_session)
        r["state"] = st
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/machines")
async def dig_machines():
    """act=7 收鉴定机产出（只收到期的槽）。旧名字保留兼容。"""
    if err := _require_login():
        return err
    try:
        r = await dg.jd_collect(_session)
        r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/push")
async def dig_push(body: dict = None):
    """往下推进地层：最深行空格扔炸药（一字型优先）。body: {dig_type}（2 一字型 / 3 九宫格）。"""
    if err := _require_login():
        return err
    b = body or {}
    try:
        r = await dg.push_down(_session, int(b.get("dig_type") or 2))
        if "state" not in r:
            r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/jd/start")
async def dig_jd_start(body: dict):
    """act=4 投晶石。body: {machine, stone}（stone 0=蓝 1=绿 2=红）。"""
    if err := _require_login():
        return err
    b = body or {}
    try:
        r = await dg.jd_start(_session, int(b.get("machine") or 0), int(b.get("stone") or 0))
        r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/jd/collect")
async def dig_jd_collect():
    """act=7 收鉴定机到期产出。"""
    if err := _require_login():
        return err
    try:
        r = await dg.jd_collect(_session)
        r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/jd/auto")
async def dig_jd_auto(body: dict = None):
    """鉴定机一轮：收到期的 + 给空槽按顺序投石。body: {stones:[0,1,2]}。"""
    if err := _require_login():
        return err
    b = body or {}
    order = [int(x) for x in (b.get("stones") or [0, 1, 2]) if str(x).strip().lstrip("-").isdigit()]
    try:
        r = await dg.jd_auto(_session, tuple(order) or (0, 1, 2))
        r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/relic/submit")
async def dig_relic_submit(body: dict = None):
    """act=10 上交鉴定出的文物换挖地套卡。body: {id(文物编号/76 sub-id), num}。"""
    if err := _require_login():
        return err
    b = body or {}
    try:
        r = await dg.relic_submit(_session, int(b.get("id", 0)), int(b.get("num", 1)))
        r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/dig/relic/kinds")
async def dig_relic_kinds():
    """文物种类全集（id ↔ 对应挖地套卡名）。只读，不碰服务端。"""
    return {"ok": True, "kinds": [{"id": i, "theme": n} for i, n in dg.relic_kinds()]}


@app.post("/api/dig/relic/submit_all")
async def dig_relic_submit_all(body: dict = None):
    """一键上交全部文物：遍历文物种类（= 挖地套卡数，id 0..33）逐个 act=10。

    body: {ids?: [int]（分批用，缺省=全部）, per_id_cap?: int}。
    ⚠️ act=1 不下发文物库存，只能遍历探测；每个 id 有 2.4s 节流，故前端**分批**调用。
    """
    if err := _require_login():
        return err
    b = body or {}
    ids = b.get("ids")
    try:
        cap = int(b.get("per_id_cap") or 8)
    except (TypeError, ValueError):
        cap = 8
    try:
        r = await dg.relic_submit_all(_session, ids=ids, per_id_cap=min(max(cap, 1), 20))
        # ⚠️ 不带 state：该接口按批调用（每 id 2.4s 节流），再附一次 state 会多一轮节流；
        # 前端在整批跑完后自己 loadDigState()。
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/daily/tools")
async def dig_daily_tools():
    """act=9 领每日道具（锄头/炸药）。"""
    if err := _require_login():
        return err
    try:
        r = await dg.daily_tools(_session)
        r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/dig/daily/gift")
async def dig_daily_gift():
    """act=15 领每日礼包（一天可连领多次，领完为止）。"""
    if err := _require_login():
        return err
    try:
        r = await dg.daily_gift(_session)
        r["state"] = await dg.get_state(_session)
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/item/name")
async def set_item_name(body: dict):
    """给查不到名字的奖励编号补名字（写进 data/item_names_extra.json，需实证来源）。

    body: {key: "70_5", name: "红晶石"}
    """
    b = body or {}
    try:
        return {"ok": True, **iid.set_extra(b.get("key", ""), b.get("name", ""))}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}


@app.post("/api/dig/auto/start")
async def dig_auto_start(body: dict = None):
    """启动自动挖。body: {max_digs, use_tnt, clear_margin}。

    决策走 dig_plan.plan()（见 backend/dig_plan.py）：穷举落点×工具按「收益/代价」贪心，
    按「探明 > 挖平礼物格 > 免费炸弹 > 下推」排序，并硬性排除会让未清地块滚出窗口的动作。
    use_tnt 默认 True —— 炸药是算法的主力（一字型横向摊开探明、九宫格秒石并下推）。
    """
    if err := _require_login():
        return err
    b = body or {}
    ut = True if b.get("use_tnt") is None else bool(b.get("use_tnt"))
    cm = int(b.get("clear_margin") if b.get("clear_margin") is not None else 1)
    return dg.start_auto(_session, int(b.get("max_digs") or 50), ut, cm)


@app.post("/api/dig/auto/stop")
async def dig_auto_stop():
    if err := _require_login():
        return err
    return dg.stop_auto()


# ---- 每日比赛只读看板（2026-09-25 bisai.har 实证）----
# 数据源与 act 语义见 backend/match.py 顶部注释。**全部只读**，不构造任何写操作。


@app.get("/api/match/state")
async def match_state(want_equip: bool = False):
    if err := _require_login():
        return err
    try:
        return await mt.match_state(_session, want_equip=bool(want_equip))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/match/pvp")
async def match_pvp(top: int = 100, rank_id: int = 0):
    """竞技场 · 竞技榜（card_travel_elf_pvp act=14）；top 控制返回条数。

    默认 100：实测今日可点赞的 10 人全在第 50 名之外，取 50 条会一条可赞的
    都看不到（2026-09-28 用户反馈「和游戏里的列表对不上」）。
    """
    if err := _require_login():
        return err
    try:
        return await mt.pvp_rank(_session, rank_id=int(rank_id), top=int(top))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/match/union")
async def match_union():
    if err := _require_login():
        return err
    try:
        return await mt.union_info(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/match/gold")
async def match_gold():
    if err := _require_login():
        return err
    try:
        return await mt.gold_buy(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/match/gvg")
async def match_gvg():
    """公会战（union act=22）。只读。"""
    if err := _require_login():
        return err
    try:
        return await mt.union_gvg(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/match/sanguo")
async def match_sanguo(want_task: int = 0):
    """三国战役（card_worldsanguo act=1）。只读。

    与「西游记」(/api/match/explore) 是**两个独立玩法**，各自一个 tab。
    """
    if err := _require_login():
        return err
    try:
        return await mt.sanguo_state(_session, want_task=bool(want_task))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/match/explore")
async def match_explore(want_task: int = 0):
    """西游记（card_worldexplore act=1）。只读。"""
    if err := _require_login():
        return err
    try:
        return await mt.explore_state(_session, want_task=bool(want_task))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


# ---- 西游记探索（2026-09-28 xiyou.har 实证的写操作，与三国是两套独立玩法）----

@app.post("/api/match/refresh-card-lib")
async def match_refresh_card_lib():
    """刷新卡库：重新拉取官方 card_info_v3.js 重建 data/card_lib.json（公开数据，无需登录）。

    本地卡库快照自 2026-09-07 起未更新，新套/新卡（如 26188「饭否」）不会进库，
    导致炼卡/卡箱/礼物/卡库查询缺数据。此接口纯拉公开静态数据，不消耗任何游戏次数。
    """
    try:
        from .card_lib_refresh import refresh_card_lib
        return await refresh_card_lib()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"刷新卡库失败：{e}"}


@app.get("/api/match/xiyou")
async def match_xiyou(want_task: int = 1):
    """西游记探索总览（只读）：当前大关 / 探索倒计时 / 三个卡箱 / 重复卡 / 缺哪几张。

    纯只读不碰卡；界面据此显示「还差什么」和一键卖重复卡腾空间。
    """
    if err := _require_login():
        return err
    try:
        st = await xy.xiyou_state(_session, want_task=bool(want_task))
        if not st.get("ok"):
            return {"ok": False, "message": st.get("message") or "读取西游记探索失败"}
        return {"ok": True, "raw": xy.plan_xiyou(st),
                "power": st.get("freepowerbit") or [], "vlb": st.get("vlb")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/match/xiyou-start")
async def match_xiyou_start(req: dict | None = None):
    """出发探索（act=5 type=3 time=5 levelid teamid=0）。**消耗一次探索机会**。"""
    if err := _require_login():
        return err
    req = req or {}
    st = await xy.xiyou_state(_session)
    if not st.get("ok"):
        return {"ok": False, "message": st.get("message") or "状态读取失败"}
    lv = req.get("levelid") or xy.plan_xiyou(st)["level"] or st.get("maxLevelId")
    r = await xy.xiyou_start(_session, int(lv or 0),
                             etype=int(req.get("type", 3)),
                             etime=int(req.get("time", 5)))
    if not r["ok"]:
        return {"ok": False, "message": r.get("desc") or f"出发失败 code={r.get('code')}"}
    return {"ok": True, "content": r.get("content"), "levelData": r.get("levelData")}


@app.post("/api/match/xiyou-get")
async def match_xiyou_get(req: dict):
    """收一个收获点（act=6 levelid+index）。"""
    if err := _require_login():
        return err
    idx = req.get("index")
    if idx is None:
        return {"ok": False, "message": "缺少 index"}
    st = await xy.xiyou_state(_session)
    if not st.get("ok"):
        return {"ok": False, "message": st.get("message") or "状态读取失败"}
    lv = req.get("levelid") or xy.plan_xiyou(st)["level"] or st.get("maxLevelId")
    r = await xy.xiyou_get(_session, int(lv or 0), int(idx))
    if not r["ok"]:
        return {"ok": False, "message": r.get("desc") or f"code={r.get('code')}"}
    return {"ok": True, "index": int(idx), "content": r.get("content")}


@app.post("/api/match/xiyou-sell")
async def match_xiyou_sell(req: dict):
    """卖探索收获箱里的卡（act=9）。传 {index, card_id}；box 缺省 2（收获箱）。

    ⚠️ 只有收获箱（box=2）的卖法有抓包实证；关卡卡箱/保险箱没抓到样本，传别的
    箱号能否生效未证实，所以后端默认只认 2。
    """
    if err := _require_login():
        return err
    idx, cid = req.get("index"), req.get("card_id")
    if idx is None or cid is None:
        return {"ok": False, "message": "缺少 index / card_id"}
    box = int(req.get("box") if req.get("box") is not None else 2)
    if box != 2:
        return {"ok": False, "message": "只支持探索收获箱（box=2），其余箱未实证"}
    r = await xy.xiyou_sell(_session, box, int(idx), int(cid),
                            snap=f"2_{int(idx)}_{int(cid)}",
                            snap_num=req.get("new_box_num"))
    if not r["ok"]:
        return {"ok": False, "message": r.get("desc") or f"code={r.get('code')}"}
    d = r.get("data") or {}
    return {"ok": True, "content": r.get("content"), "index": int(idx),
            "card_id": int(cid),
            "box": {k: d.get(k) for k in ("ex_box_num", "new_box_num", "safe_box_num")}}


@app.post("/api/match/xiyou-task")
async def match_xiyou_task(req: dict | None = None):
    """领每日任务奖（act=16 id=N）。零消耗。传 {id}；不传则领所有已完成的。"""
    if err := _require_login():
        return err
    req = req or {}
    st = await xy.xiyou_state(_session, want_task=True)
    if not st.get("ok"):
        return {"ok": False, "message": st.get("message") or "状态读取失败"}
    t = xy.plan_xiyou(st)
    ids = [int(req["id"])] if req.get("id") is not None else [
        int(x["id"]) for x in t["tasks"] if x.get("done")]
    got = []
    for i in ids:
        r = await xy.xiyou_claim_task(_session, i)
        got.append({"id": i, "ok": r["ok"],
                    "content": r.get("content") or r.get("desc") or ""})
    return {"ok": True, "claimed": got}


@app.post("/api/match/xiyou-run")
async def match_xiyou_run(req: dict | None = None):
    """西游记一键（SSE 流式）：收满收获点 → 出发新 24h 探索 → 领任务奖。

    每步 yield（step/ok/note），末尾 __done__ 带汇总；结果落盘 data/xiyou_run.json。
    """
    if err := _require_login():
        return err
    req = req or {}
    lv = req.get("chapter") or req.get("levelid")

    async def gen():
        try:
            async for step in xy.run_xiyou(_session,
                                          do_start=bool(req.get("start", True)),
                                          do_get=bool(req.get("get", True)),
                                          do_task=bool(req.get("task", True)),
                                          levelid=int(lv) if lv else None,
                                          feed=bool(req.get("feed", True))):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class NoCacheStaticFiles(StaticFiles):
    """静态资源禁用浏览器缓存。

    改完 app.js / index.html 后用户刷新页面常常仍加载旧文件，
    必须 Cmd+Shift+R 才生效；这里直接下发 no-store 根治。
    """

    def file_response(self, *args, **kwargs):  # noqa: D102
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp



# ---------------- 每日比赛：写操作（2026-09-26 bisai1.har 实证 + 真机实打）----------------
# act=11 喂食 / act=4 打一场；参数规则均由真机 A-B 验证得出（详见 match.py）。

@app.post("/api/match/feed")
async def match_feed(req: dict):
    """喂猫（competition act=11）：把心情喂到阈值才能做其他任务。"""
    if err := _require_login():
        return err
    elf_id = req.get("elf_id")
    if elf_id is None:
        return {"ok": False, "message": "缺少 elf_id"}
    try:
        return await mt.competition_feed(_session, int(elf_id), int(req.get("candy_type") or 1))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/match/race")
async def match_race(req: dict):
    """打一场比赛（competition act=4）：按传入的 4 件装备直接上台。

    真机实证（2026-09-26）：{catId:4, id:8, ids:'10493|20141|30383|40582'}
      → code=0、total_value=1174、performe_num 2→1。
    · id = 本场比赛 id（act=2 实时读，每场都变），不传则后端自动取
    · ids **可现场搭配**，不必是猫当前穿着的——每日比赛没有独立穿装接口
    · 参数错误只回 code=-1，不扣次数
    """
    if err := _require_login():
        return err
    cat_id = req.get("cat_id")
    equip = req.get("equip") or []
    if cat_id is None or not equip:
        return {"ok": False, "message": "缺少 cat_id 或 equip"}
    race_id = req.get("race_id")
    # dress（act=6 装扮表）只为生成可读穿装文本，拿不到也照常出战
    dress = (await mt.match_state(_session, want_equip=True)).get("equip") or {}
    try:
        return await mt.competition_race(_session, int(cat_id), [int(x) for x in equip],
                                         int(race_id) if race_id is not None else None,
                                         int(req.get("cat_level") or 0) or None,
                                         True, dress)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.get("/api/match/comp-extra")
async def match_comp_extra():
    """每日比赛附加：英雄榜（act=9）+ 积分次数（act=16）。只读。"""
    if err := _require_login():
        return err
    hero = await mt.comp_hero(_session)
    score = await mt.comp_score(_session)
    return {"ok": True, "hero": hero, "score": score}


@app.get("/api/match/pvp-extra")
async def match_pvp_extra():
    """竞技场：主页（act=1，含对手 enemy_list）+ 商店（act=17）。只读。"""
    if err := _require_login():
        return err
    st = await mt.pvp_state(_session)
    store = await mt.pvp_store(_session)
    # 竞技主题（pvp act=1 week_theme）：每周三更新、持续一周。复用后端 _fmt_week_theme
    # 转中文属性名，避免前端再维护一份 id→名字映射（前端铁律：界面文案禁止代码标识）。
    wd = ((st.get("data") or {}).get("week_theme")) or []
    if isinstance(st, dict):
        st.setdefault("data", {})["week_theme_text"] = mt._fmt_week_theme(wd) if wd else ""
    return {"ok": True, "state": st, "store": store}


@app.get("/api/match/union-extra")
async def match_union_extra(level: int = 8):
    """工会：任务（act=15）+ 贡献（act=1，经 union_info，旧误标 act=16）+ 商店（act=30）+ 关卡对手（act=27）。只读。"""
    if err := _require_login():
        return err
    return {"ok": True,
            "task": await mt.union_task(_session),
            "chip": await mt.union_chip(_session),
            "shop": await mt.union_shop(_session),
            "enemies": await mt.union_enemies(_session, level)}


@app.get("/api/match/explore-extra")
async def match_explore_extra():
    """世界探索：队伍（act=11）+ 任务（act=15）+ 商店（act=26）+ 排行（act=28）。只读。"""
    if err := _require_login():
        return err
    return {"ok": True,
            "team": await mt.explore_team(_session),
            "task": await mt.explore_task(_session),
            "shop": await mt.explore_shop(_session),
            "rank": await mt.explore_rank(_session)}


# ---- 主界面「每日玩法」（backend/cathome.py，2026-09-26 bisai2.har + 实打实证）----

@app.get("/api/home/state")
async def home_state():
    """主界面每日玩法状态：点赞 bit / 扭蛋免费 / 积分 / 猫心情 / 竞技场剩余 / 金币福利 / 珍藏阁。只读。"""
    if err := _require_login():
        return err
    try:
        st = await ch.home_state(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}
    # 珍藏阁（card_user_zcg）状态并入猫窝日常，复用同一端点（最小改动；前端 mode-grid 加同名片）
    try:
        st["zcg"] = await zcg_mod.get_zcg_state(_session)
    except Exception as e:  # noqa: BLE001
        st["zcg"] = {"ok": False, "text": f"珍藏阁状态读取失败：{e}"}
    return st


@app.post("/api/home/like")
async def home_like(req: dict = None):
    """点赞。body {target: "match"|"arena"}。"""
    if err := _require_login():
        return err
    target = ((req or {}).get("target") or "").strip()
    try:
        return await ch.like(_session, target)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/home/gacha-free")
async def home_gacha_free():
    """扭蛋机免费抽一次（act=17 {id:1}，每天 1 次，后端二次校验 free_times）。"""
    if err := _require_login():
        return err
    try:
        return await ch.gacha_free(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/home/exchange-candy")
async def home_exchange_candy(req: dict = None):
    """积分兑换大糖果（act=18 {idx:2, num}，6 积分/颗）。body {num:1}。"""
    if err := _require_login():
        return err
    num = int(((req or {}).get("num")) or 1)
    try:
        return await ch.exchange_candy(_session, num)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/home/feed")
async def home_feed(req: dict = None):
    """喂糖。body {mode:"all"} 全部喂到 80；或 {elf_id, candy_type} 手动喂一颗。"""
    if err := _require_login():
        return err
    body = req or {}
    try:
        if body.get("mode") == "all":
            return await ch.feed_smart(_session)
        if body.get("elf_id") is None or body.get("candy_type") is None:
            return {"ok": False, "message": "缺少 elf_id / candy_type"}
        return await ch.feed_one(_session, int(body["elf_id"]), int(body["candy_type"]))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/home/arena-auto")
async def home_arena_auto():
    """竞技比赛自动（SSE 流式）：挑低战力打（≤4）+ 免费刷对手 + 领任务奖励。消耗挑战次数。"""
    if err := _require_login():
        return err

    async def gen():
        try:
            async for step in ch.arena_auto(_session):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/home/gold-buy")
async def home_gold_buy():
    """金币福利 · 兑换红宝石（act=21）。到上限自动跳过，不浪费次数。"""
    if err := _require_login():
        return err
    try:
        return await ch.gold_buy_ruby(_session)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/home/forge-pick")
async def home_forge_pick():
    """衣柜锻造 · 挑一件今天最该锻的（未锻过且材料需求最小的），只算不锻。"""
    if err := _require_login():
        return err
    try:
        st = await ch.home_state(_session)
        best = ch.pick_forge_target(st.get("forge", {}).get("parts"))
        return {"ok": True, "best": best, "stock": st.get("forge", {}).get("chip")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/home/forge")
async def home_forge(equip_id: int = 0):
    """衣柜锻造一件（act=29 {equip_id}）。会真的消耗材料，先确认再点。"""
    if err := _require_login():
        return err
    if not equip_id:
        return {"ok": False, "message": "缺少 equip_id"}
    try:
        return await ch.forge_one(_session, equip_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/home/all")
async def home_all():
    """一键全部（SSE 流式）：点赞 → 扭蛋 → 换糖 → 喂猫到 80 → 兑红宝石 → 竞技自动。
    每完成一步就推一行，前端实时显示，不再等整段跑完才出日志。"""
    if err := _require_login():
        return err

    async def gen():
        try:
            async for step in ch.run_all(_session):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# mode-props 的取数映射（mode:来源 -> 协程），由 main 注入 match.mode_props
_MODE_PROP_FETCH: dict = {
    "comp:score": mt.comp_score, "comp:hero": mt.comp_hero,
    "comp:dress": mt.dress_info,   # act=6 衣橱：顶级装备属性的可读化数据源
    "pvp:state": mt.pvp_state, "pvp:store": mt.pvp_store,
    "union:task": mt.union_task, "union:chip": mt.union_chip,
    "union:shop": mt.union_shop, "union:enemies": mt.union_enemies,
    "explore:team": mt.explore_team, "explore:task": mt.explore_task,
    "explore:shop": mt.explore_shop, "explore:rank": mt.explore_rank,
    "sanguo:task": mt.sanguo_task, "sanguo:levels": mt.sanguo_list,
    "sanguo:lineup": mt.sanguo_lineup,
}


@app.get("/api/match/mode-props")
async def match_mode_props(mode: str = "", levelid: int = 0):
    """某玩法的状态属性表（分组 + 中文名 + 原始值），前端每种玩法一个子块单独展示。

    mode = comp | pvp | union | explore | sanguo。只读，不发起任何写操作。
    """
    if err := _require_login():
        return err
    if mode not in ("comp", "pvp", "union", "explore", "sanguo"):
        return {"ok": False, "message": "未知玩法 " + str(mode)}
    return await mt.mode_props(_session, mode, _MODE_PROP_FETCH, int(levelid))


@app.get("/api/match/sanguo-extra")
async def match_sanguo_extra(levelid: int = 0):
    """三国战役：任务（act=7）+ 关卡（act=9）+ 阵容（act=16）。只读。"""
    if err := _require_login():
        return err
    return {"ok": True,
            "task": await mt.sanguo_task(_session),
            "levels": await mt.sanguo_list(_session),
            "lineup": await mt.sanguo_lineup(_session, levelid) if levelid else None}


def _claim_group(req, g: str, k: str, coro):
    """包一层，带上玩法组标识（前端各子块一键领奖按 only 过滤后复用本端点）。"""
    return {"g": g, "k": k, "r": coro}


@app.post("/api/match/claim-all")
async def match_claim_all(req: dict | None = None):
    """一键领奖（SSE 流式）：依次领各玩法的奖励（都是零消耗的领奖 act）。

    覆盖 competition act=17/21、pvp act=15、union act=17、explore act=25、sanguo act=2。
    **only** 可选，传 "comp"/"pvp"/"union"/"explore"/"sanguo" 时只领该玩法（其余子块一键用）。
    每领一项 yield 一条，末尾 __done__ 带汇总；失败的单独标出，不影响其他项。
    """
    if err := _require_login():
        return err
    req = req or {}
    only = str(req.get("only") or "").strip()
    cids = req.get("comp_ids") or [1]

    async def want(g: str) -> bool:
        return (not only) or (only == g)

    async def gen():
        out = []
        try:
            if await want("comp"):
                for cid in cids:
                    it = _claim_group(req, "comp", f"比赛领奖 act=17 id={cid}",
                                      await mt.comp_claim(_session, cid))
                    out.append(it); yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
                    it = _claim_group(req, "comp", f"比赛领奖 act=21 id={cid}",
                                      await mt.comp_claim2(_session, cid))
                    out.append(it); yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
            if await want("pvp"):
                it = _claim_group(req, "pvp", "竞技场领奖 act=15",
                                  await mt.pvp_claim(_session, req.get("pvp_id", 1)))
                out.append(it); yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
            if await want("union"):
                it = _claim_group(req, "union", "工会领奖 act=17", await mt.union_claim(_session))
                out.append(it); yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
            if await want("explore"):
                it = _claim_group(req, "explore", "探索领奖 act=25",
                                  await mt.explore_claim(_session, req.get("explore_id", 1)))
                out.append(it); yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
            if await want("sanguo"):
                it = _claim_group(req, "sanguo", "三国领奖 act=2",
                                  await mt.sanguo_claim(_session, req.get("sanguo_id", 0)))
                out.append(it); yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
                it = _claim_group(req, "sanguo", "三国每日任务领奖 act=8",
                                  await mt.sanguo_claim_tasks(_session))
                out.append(it); yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'act': '__done__', 'ok': True, 'items': out}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/match/sanguo-auto")
async def match_sanguo_auto(req: dict | None = None):
    """三国闯关（sanguo act=11）：内部先读 act=16 真实阵容再提交。

    注意：act=11（出战）/ act=16（读阵容）的语义来自早期推断，三国无 HAR 真证
    （bisai2.har 是工会）。2026-10-03 sanguolingka.har 实证的是 act=2=领通关奖励。
    本接口保留 act=11 出战，但 run_sanguo_raid 已先尽量用 act=2 领掉未领奖的关。

    走后端拼装是为了遵守「参数取真实值」铁律：cats/eqs 一律来自当次 act=16 的 elf_list，
    不靠前端猜。只在用户主动点子块按钮时才调。
    """
    if err := _require_login():
        return err
    req = req or {}
    levelid = req.get("levelid")
    if levelid is None:
        return {"ok": False, "message": "缺少 levelid"}
    try:
        lu = await mt.sanguo_lineup(_session, int(levelid))
        d = lu.get("data") if isinstance(lu.get("data"), dict) else {}
        lcode = d.get("code") if isinstance(d, dict) else lu.get("code")
        if lcode not in (0, None):
            desc = (d or {}).get("desc") if isinstance(d, dict) else lu.get("desc")
            return {"ok": False, "message": f"阵容读取失败 code={lcode} {desc or ''}".strip()}
        elves = (d or {}).get("elf_list") or [] if isinstance(d, dict) else []
        if not elves:
            return {"ok": False, "message": "该关卡没有可用阵容"}
        cats = [int(e.get("id")) for e in elves]
        eqs: list = []
        for e in elves:
            eqs.extend(int(x) for x in (e.get("equip_list") or []))
        if len(eqs) != len(cats) * 4:
            return {"ok": False, "message": f"阵容装备数量异常 cats={len(cats)} eqs={len(eqs)}"}
        r = await mt.sanguo_run(_session, int(levelid), cats, eqs, req.get("mode", 0))
        return {"ok": True, "levelid": int(levelid), "cats": cats,
                "eqs": eqs, "eq_count": len(eqs), "result": r}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/match/sanguo-raid")
async def match_sanguo_raid(req: dict | None = None):
    """三国一键出战（SSE 流式）：用真实阵容连打到今日次数用尽（**消耗闯关次数**）。

    逐场 yield（pre_claim / pre_task / round / post_task），末尾 __done__ 带汇总。
    今日已打满时直接 skipped，不发任何写请求。步骤落盘 data/sanguo_raid.json。
    传 `chapter`（1..9）＝ 界面上选的「打第几大关」，后端在该大关内挑该打的小关；
    不传则沿用服务端给的当前关。
    """
    if err := _require_login():
        return err
    req = req or {}
    lv = req.get("levelid")
    ch = req.get("chapter")

    async def gen():
        try:
            async for step in mt.run_sanguo_raid(_session, int(lv) if lv else None,
                                                chapter=int(ch) if ch else None,
                                                feed=bool(req.get("feed", True)),
                                                clear_box=bool(req.get("clear_box", True))):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/match/sanguo-box")
async def match_sanguo_box():
    """三国关卡卡箱（只读）：ex_box 里每层攒了几张、缺哪层、同层多余的可卖哪些。

    纯只读，不碰卡。界面据此提示「还差第 N 层」和一键卖多余旧卡。
    ⚠ 所有对外层号都是**游戏界面口径**（第 1 层＝boss 层只有 1 张，第 5 层＝要攒 8 张）；
      卡库位置值那一套编号是反的，别直接显示（2026-09-28 用户纠正）。
    """
    if err := _require_login():
        return err
    st = await mt.sanguo_state(_session)
    if not st.get("ok"):
        return {"ok": False, "message": st.get("message") or "读取三国战役失败"}
    lv = (st.get("levelData") or [{}])[0]
    # ⚠ levelData[0] 是「上一条记录」（旧残留，如 102），顶层 levelId 才是**下一个该打的关**
    #   （实测 204）。拿 102 当当前关会跟游戏里看到的对不上。
    return {"ok": True, "levelid": st.get("levelId") or lv.get("levelid"),
            "freenum": st.get("freenum"),
            "box": mt.plan_sg_boxes(st), "layers": mt.plan_sg_layers(st),
            "dropitem": lv.get("dropitem"), "pass": lv.get("passData"),
            "steps": lv.get("stepData")}


@app.post("/api/match/sanguo-sell")
async def match_sanguo_sell(req: dict):
    """卖掉某一箱里指定格的层卡（act=5），换三国币。传 {box, index, card_id}。

    box 缺省 0 = 关卡卡箱，1 = 保险箱（2026-09-28 真机实证）。
    index / card_id 必须和 /api/match/sanguo-box 读到的对得上，否则服务端回 -311。
    """
    if err := _require_login():
        return err
    idx, cid = req.get("index"), req.get("card_id")
    if idx is None or cid is None:
        return {"ok": False, "message": "缺少 index / card_id"}
    box = int(req.get("box") if req.get("box") is not None else mt.SG_BOX_EX)
    if box not in (mt.SG_BOX_EX, mt.SG_BOX_SAFE):
        return {"ok": False, "message": "box 只能是 0（关卡卡箱）或 1（保险箱）"}
    r = await mt.sanguo_sell(_session, box, int(idx), int(cid))
    if not r.get("ok"):
        return {"ok": False, "message": r.get("message") or "卖卡失败"}
    d = r.get("data") or {}
    if d.get("code") not in (0, None):
        return {"ok": False, "message": str(d.get("desc") or d.get("code"))}
    return {"ok": True, "box": box, "content": d.get("content"), "index": int(idx),
            "card_id": int(cid), "box_free": d.get(f"{'ex' if box == mt.SG_BOX_EX else 'safe'}_box_num")}


@app.post("/api/match/pvp-fight")
async def match_pvp_fight(req: dict):
    """PVP 挑战（pvp act=8，type=fight）。**消耗 pvp_num**，需传 euin（从 pvp-extra 的 enemy_list 取）。"""
    if err := _require_login():
        return err
    euin = req.get("euin")
    if not euin:
        return {"ok": False, "message": "缺少 euin"}
    return await mt.pvp_fight(_session, str(euin))


@app.post("/api/match/pvp-flush")
async def match_pvp_flush():
    """刷新 PVP 对手（pvp act=7）。"""
    if err := _require_login():
        return err
    return await mt.pvp_flush(_session)


@app.get("/api/match/union-quota")
async def match_union_quota():
    """工会每日限次本地计数（摇水果 3/出战 3/金币兑红包石 1）。只读落盘文件，不发任何游戏请求。"""
    return {"ok": True, "quota": mt._union_quota_today(),
            "shake_limit": mt.SHAKE_DAILY_LIMIT, "battle_limit": mt.BATTLE_DAILY_LIMIT,
            "exchange_limit": mt.EXCHANGE_DAILY_LIMIT}


@app.post("/api/match/union-exchange")
async def match_union_exchange():
    """金币兑换红包石（union act=21）。每日 1 次（周 7 次周一重置）；当日已兑本地拦截不发请求。"""
    if err := _require_login():
        return err
    return await mt.union_exchange_redstone(_session)


@app.post("/api/match/union-shake")
async def match_union_shake():
    """摇水果（union act=17，每日 3 次）。本地硬闸 + 服务端限额双保险，超限不发请求。"""
    if err := _require_login():
        return err
    return await mt.union_shake_fruit(_session)


@app.post("/api/match/union-battle")
async def match_union_battle(req: dict | None = None):
    """公会战出战（union act=23 {cats,eqs}）—— 消耗 3/3 次数，带猫出战。

    阵容从 union_lineup(act=39) 实时读，前端不必传 cats/eqs。
    """
    if err := _require_login():
        return err
    req = req or {}
    return await mt.union_battle(_session, feed=bool(req.get("feed", True)))


@app.post("/api/match/union-redpacket")
async def match_union_redpacket():
    """抢红包（act=19 读列表 + act=20 {id:N} 领取）。只领自己没领过的，已领自动跳过。"""
    if err := _require_login():
        return err
    return await mt.union_redpacket_grab(_session)


@app.post("/api/match/union-info-query")
async def match_union_info_query(req: dict | None = None):
    """按 uin 批量查公会归属（union act=14 {uins:[...]}）—— 只读查询，不是点赞。"""
    if err := _require_login():
        return err
    req = req or {}
    uins = req.get("uins") or []
    if not uins:
        return {"ok": False, "message": "缺少 uins"}
    return await mt.union_unioninfo_query(_session, uins)


@app.post("/api/match/union-all")
async def match_union_all(req: dict | None = None):
    """「一键工会」（SSE 流式）：摇水果(17，每日3次) → 抢红包(19+20) → 公会战出战(23)。

    每步 yield（shake/exchange/redpacket/battle），末尾 __done__ 带汇总。
    抢红包只领自己没领过的。步骤落盘 data/union_run.json。
    """
    if err := _require_login():
        return err
    req = req or {}

    async def gen():
        try:
            async for step in mt.run_union(_session,
                                           do_shake=bool(req.get("shake", True)),
                                           do_redpacket=bool(req.get("redpacket", True)),
                                           feed=bool(req.get("feed", True)),
                                           do_battle=bool(req.get("battle", True))):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/match/sanguo-run")
async def match_sanguo_run(req: dict):
    """三国闯关（sanguo act=11，出战）。**消耗闯关次数**（act=11 语义三国未实证）。

    cats/eqs 必须来自 sanguo act=16 读到的真实阵容（铁律：参数取真实值）。
    """
    if err := _require_login():
        return err
    cats = req.get("cats") or []
    eqs = req.get("eqs") or []
    levelid = req.get("levelid")
    if not cats or not eqs or levelid is None:
        return {"ok": False, "message": "缺少 levelid / cats / eqs"}
    return await mt.sanguo_run(_session, int(levelid), cats, eqs, req.get("mode", 0))


@app.get("/api/match/race-log")
async def match_race_log(limit: int = 20):
    """最近 N 次出战记录（哪只猫 / 穿什么 / 哪场 / 得分 / 剩余次数）。"""
    if err := _require_login():
        return err
    return {"ok": True, "items": mt.load_race_log(limit)}


@app.get("/api/match/theme")
async def match_theme():
    """今日主题 + 最优穿装建议（每日比赛）。

    主题来自 competition act=2（赛前可读、每场随机，必须出发前现读）；
    衣橱来自 act=6；建议 = 穷举头/手/衣/各一件、按主题百分比加权的最优组合。
    另带每只猫「当前穿着 vs 最优」的覆盖对比。
    """
    if err := _require_login():
        return err
    try:
        th = await mt.competition_theme(_session)
        if not th.get("ok"):
            return th
        theme_rows = th.get("theme") or []
        theme = {a["id"]: a["value"] for a in theme_rows}
        st = await mt.match_state(_session, want_equip=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}
    equip = st.get("equip") or {}
    suggest = mt.best_equip(equip, theme) if theme else None
    cats = []
    for c in (st.get("cats") or []):
        worn = c.get("equip") or []
        cats.append({
            "elf_id": c.get("elf_id"), "level": c.get("level"), "mood": c.get("mood"),
            "equip": worn,
            "cover": mt.current_cover(equip, worn, theme) if theme and worn else None,
            # 「头:头巾(唱歌+25) 手:… ｜ 命中主题：…」—— 让用户看得懂现在穿了什么
            "equip_text": mt.equip_summary(worn, equip, theme) if theme else "",
        })
    return {
        "ok": True,
        "theme": theme_rows,          # [{id, value, name}]
        "attrs": cd.attr_names(),     # {属性id: 中文名}，界面显示「属性5=跑动」
        "cats": cats,
        "suggest": suggest,
        "performe_num": st.get("performe_num"),
    }

@app.post("/api/match/run-all")
async def match_run_all(req: dict):
    """一键比赛（SSE 流式）：连续打完今日剩余场次（**消耗 performe_num**）。

    逐场 yield（feed / race），末尾 __done__ 带汇总；前端边收边显示。
    max_races>0 可限制最多打几场。
    """
    if err := _require_login():
        return err
    try:
        max_races = int((req or {}).get("max_races") or 0)
    except (TypeError, ValueError):
        max_races = 0

    async def gen():
        try:
            async for step in mt.run_match_all(_session, max_races=max_races):
                yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'act': '__error__', 'ok': False, 'message': f'异常：{e}'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/match/lineup")
async def match_lineup():
    """读当前出战阵容（union act=39，cats 全 0 即只读）。"""
    if err := _require_login():
        return err
    try:
        st = await mt.match_state(_session, want_equip=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}
    return {
        "ok": True,
        "cats": st.get("cats"),
        "suggest": mt.best_equip(st.get("equip") or {}),
        "theme_arr": mt._load_theme_arr(),
    }


@app.post("/api/match/equip")
async def match_equip(req: dict):
    """一键换装（union act=23）：cats + 每只猫 4 件装备，平铺后一次提交。

    不扣每日比赛次数，可放心调用；调用后可再读 /api/match/lineup 校验。
    """
    if err := _require_login():
        return err
    cats = req.get("cats") or []
    eq = req.get("equip") or []
    if not cats or not eq:
        return {"ok": False, "message": "缺少 cats 或 equip"}
    try:
        return await mt.union_equip(_session, [int(c) for c in cats], [int(x) for x in eq])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}


@app.post("/api/match/pvp-lineup")
async def match_pvp_lineup(req: dict):
    """设置 PVP 出战阵容（pvp act=3，ids 用逗号分隔）。"""
    if err := _require_login():
        return err
    ids = req.get("ids") or []
    if not ids:
        return {"ok": False, "message": "缺少 ids"}
    try:
        return await mt.pvp_lineup(_session, [int(x) for x in ids])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"异常：{e}"}

# ★ 静态挂载必须放在所有 API 路由**之后**：它是 mount('/', html=True)，
#   注册在前会先吞掉 /api/*（GET 找不到文件→404，非 GET→405）。
app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND), html=True), name="static")
