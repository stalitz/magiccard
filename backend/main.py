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
import logging
import re
import time
from collections import Counter
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

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
from .constants import (EP_BUY, EP_MAINPAGE, EP_MINI_GET, EP_PROPS_INFO, EP_RANDOM_GET,
                        EP_REGISTER, EP_SPIRITE, EP_USER_LIST,
                        EP_WORLD_XIYOU, EP_WORLD_SANGUO)
from .spirite_auto import SPIRITE_MAPS, get_engine, plain_uin

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
    """随机抽卡：单抽一次（type=1）。"""
    if err := _require_login():
        return err
    return await _session.draw_random_cards(fetch_type=1)


@app.post("/api/random/draw")
async def random_draw_all():
    """一键抽完所有随机抽卡次数，并自动卖掉 10 面额卡。"""
    if err := _require_login():
        return err
    return await _session.auto_random_draw_and_sell()


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
                    gift_upper: int = 0, gift_lower: int = 0):
    """启动自动炼卡（**可多套并行**）。先按**实时库存算净需求**做启动预检。

    · `tids` = 逗号分隔的套卡 id 列表（队列，按序轮转，空着的炉位给各套分着用）；
      兼容旧参数 `tid`（单套）。
    · `rounds` = **每套**集齐上限，0 = 不限。某套达标后自动移出队列，队列空即停止。
      队列里有几套就炼几套 —— 长炉时（如魏书 10h）炉位不会闲着。

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
    plans = [eng.plan_summary(stock, pending, t) for t in ids]
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
                       limit: int = 500):
    """套卡目录（卡册 module / 星级 / 闪卡 / 关键词过滤），按上架时间倒序。只读。

    transferable=1 → 只返回「能变卡得到」的套卡（变卡目标套卡候选）。
    每套附 have（已持有卡种数）/ n（总卡数），供前端标记「已有套卡」。
    """
    if err := _require_login():
        return err
    inv = await _owned_snapshot() or {"avail": {}}
    return cq.list_themes(q=q, star=star, flash=flash, offline=offline, module=module,
                          transferable=transferable, owned=inv["avail"], limit=min(limit, 2000))


@app.get("/api/cards/theme/{tid}")
async def cards_theme(tid: int):
    """某套卡全部卡：名称/面值/持有/配方(素材+耗时)/被用作哪些卡素材。"""
    if err := _require_login():
        return err
    inv = await _owned_snapshot() or {"avail": {}}
    d = cq.theme_detail(tid, owned=inv["avail"])
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
    """一键日常：跑 daily.ACTIONS 里**已实证**的每日领取动作。

    body 可选 {"keys": ["gift"]} 只跑指定项；不传 = 全跑。
    ⚠️ 只有 daily.py 里写明实证依据的动作才会执行 —— 需要抓包确认的（签到 /
    连续登录领奖 / 猫咪旅行等）宁缺勿滥，不在这里凭猜测补。
    """
    if err := _require_login():
        return err
    keys = (body or {}).get("keys") or None
    items = await daily.run(_session, keys)
    return {"ok": bool(items) and all(i["ok"] for i in items), "items": items}


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
    ep = EP_WORLD_SANGUO if w == "sanguo" else EP_WORLD_XIYOU
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
    ep = EP_WORLD_SANGUO if w == "sanguo" else EP_WORLD_XIYOU
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


@app.get("/api/travel/state")
async def travel_state():
    """猫咪旅行只读状态：猫状态 + 免费次数 + 食物持有 + A级食物 + 每日任务。"""
    if err := _require_login():
        return err
    try:
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
        # 食物持有（act=3）+ A 级食物（静态配置解析）
        try:
            food_owned = await te.get_food_config(_session)
        except Exception:  # noqa: BLE001
            food_owned = {}
        a_grade = te.resolve_a_grade_foods()
        # 每日任务（act=32；task_bits = 已领取掩码）
        try:
            info = await te.get_daily_tasks(_session)
        except Exception:  # noqa: BLE001
            info = {"tasks": [], "bits": 0}
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
        # 可浇好友（act=12 只读筛选：已开通 + 已种三叶草未成熟 + 今日未浇）
        try:
            col = await te.collect_water_targets(_session)
            water_open = col.get("opened")
            water_targets = col.get("targets") or []
            water_rows = col.get("all") or []
        except Exception:  # noqa: BLE001
            water_open, water_targets, water_rows = None, [], []
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
                "water_open": water_open,
                "water_targets": water_targets,
                "water_rows": water_rows,
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
    try:
        out = await te.run_once(
            _session,
            food_num=int(b.get("food_num") or te.DEFAULT_FOOD_NUM),
            harvest_only=bool(b.get("harvest_only")),
            dry_run=bool(b.get("dry_run")),
            do_clover=bool(b.get("do_clover", True)),
            do_water=bool(b.get("do_water", True)),
            do_tasks=bool(b.get("do_tasks", True)),
        )
        return {"ok": bool(out) and all(i.get("ok") for i in out),
                "dry_run": bool(b.get("dry_run")), "items": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"运行异常：{e}"}


@app.post("/api/travel/clover")
async def travel_clover():
    """单独收三叶草（act=4）。"""
    if err := _require_login():
        return err
    try:
        r = await te.harvest_clover(_session)
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
    if mode in ("map", "island", "across"):
        return get_engine(_session, mode).stop()
    # 不带 mode → 停止全部正在跑的玩法
    stopped = {}
    for m in ("map", "island", "across"):
        eng = get_engine(_session, m)
        if eng.running:
            stopped[m] = eng.stop()
    return {"ok": True, "message": "已请求停止全部", "stopped": stopped}


@app.get("/api/spirite/auto/status")
async def spirite_auto_status():
    if err := _require_login():
        return err
    modes = {}
    for m in ("map", "island", "across"):
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


app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND), html=True), name="static")
