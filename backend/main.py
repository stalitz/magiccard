"""FastAPI 入口：薄代理 + 扫码登录 + 静态前端。

职责（刻意保持轻量，不是重型后端）：
1. 提供静态前端（纯 HTML/JS，QQ 空间风格登录界面）
2. 代理 ptlogin 扫码登录（绕开浏览器 CORS）
3. 代理魔法卡片接口（带 cookie + g_tk 签名，待探明真实端点）

不使用任何 Web 框架的重量级特性，仅 FastAPI + StaticFiles。
"""
import base64
import time
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from .auth_qr import QRLogin
from .mc_api import LoginSession
from . import card_query as cq
from .constants import EP_MAINPAGE, EP_REGISTER, EP_RANDOM_GET

app = FastAPI(title="魔法卡片助手")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# 单用户 demo：登录态持久化到本地文件，重启不丢
_qr = QRLogin()
_session = LoginSession()
_session.restore()


# 二维码缓存：100s 内复用同一张码，避免页面刷新/多端取码互相作废
_qr_cache: dict = {"b64": "", "ts": 0.0}
QR_TTL = 100.0


@app.get("/api/login/qr")
async def login_qr(force: int = 0):
    """取二维码。默认 100s 内复用（force=1 强制刷新）。"""
    now = time.time()
    if force or not _qr_cache["b64"] or now - _qr_cache["ts"] > QR_TTL:
        png = await _qr.get_qr()
        _qr_cache["b64"] = base64.b64encode(png).decode()
        _qr_cache["ts"] = now
    return {"qr": f"data:image/png;base64,{_qr_cache['b64']}", "age": round(now - _qr_cache["ts"], 1)}


@app.get("/api/login/poll")
async def login_poll():
    """轮询扫码状态；确认后把 cookie 载入会话。"""
    res = await _qr.poll()
    if res.get("status") == "ok":
        _session.load_cookies(res["cookies"])
        skey_domains = sorted({k for k in res["cookies"] if "skey" in k.lower()})
        return {"status": "ok", "uin": res.get("uin"), "skey_domains": skey_domains}
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


@app.get("/api/me")
async def me():
    return {"logged_in": _session.logged_in, "uin": _session.uin}


@app.api_route("/api/logout", methods=["GET", "POST"])
async def logout():
    _session.clear()
    return {"ok": True}


def _require_login():
    if not _session.logged_in:
        return JSONResponse({"code": -1, "message": "未登录"}, status_code=401)
    return None


# ---- 魔法卡片接口代理（端点为 2014 参考值，登录后需抓包校准）----
@app.get("/api/refine/status")
async def refine_status():
    if err := _require_login():
        return err
    return await _session.get("card_stove_refinedcard_get", slottype=1, ver=1, code="")


@app.get("/api/refine/get")
async def refine_get(slotid: int):
    if err := _require_login():
        return err
    return await _session.get(
        "card_stove_refinedcard_get", slotid=slotid, slottype=1, ver=1, code=""
    )


@app.get("/api/random")
async def random_get():
    if err := _require_login():
        return err
    return await _session.get("card_user_random_get", type=1)


# TODO: 开始炼卡端点需抓包确认（EP_REFINE="card_stove_refine"）
@app.get("/api/refine/start")
async def refine_start():
    return {"code": -1, "message": "开始炼卡端点待抓包确认（见 README）"}


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
        return await _session.get_state()
    except httpx.ConnectError:
        return {"ok": False, "code": -1,
                "message": "连接腾讯服务器失败（网络抖动），请稍后点击刷新重试"}
    except httpx.TimeoutException:
        return {"ok": False, "code": -1,
                "message": "连接腾讯服务器超时，请稍后点击刷新重试"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": -1, "message": f"读取状态异常：{e}"}


@app.get("/api/card/plan")
async def card_plan(max_plans: int = 10):
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
        return await _session.refine(target)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"炼卡请求异常：{e}"}


@app.get("/api/card/mainpage")
async def card_mainpage(method: str = "get"):
    """主页面/卡箱入口。magicCard 用 POST，也提供 GET 变体便于对比。"""
    if err := _require_login():
        return err
    fn = _session.post if method == "post" else _session.get
    return await fn(EP_MAINPAGE, code="")


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
                  flash: int = -1, offline: int = -1,
                  owned_only: int = 0, limit: int = 300):
    """按面值/关键词全库查卡（含持有数、可炼、素材）。只读。"""
    if err := _require_login():
        return err
    inv = await _owned_snapshot() or {"avail": {}}
    out = cq.query_cards(
        price=price, q=kw, star=star, flash=flash, offline=offline,
        owned_only=owned_only, owned=inv["avail"], limit=min(limit, 500))
    out["ok"] = True
    out["furnace"] = inv.get("furnace", [])
    return out


@app.get("/api/cards/themes")
async def cards_themes(q: str = "", star: int = 0, flash: int = -1,
                       offline: int = -1, limit: int = 500):
    """套卡目录（星级/闪卡/关键词过滤），按上架时间倒序。只读。"""
    if err := _require_login():
        return err
    return cq.list_themes(q=q, star=star, flash=flash, offline=offline,
                          limit=min(limit, 800))


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
async def exchange_peers(tid: int = 0):
    """按主题搜「在玩该主题的卡友」（下架套也能搜），并列出手动名单。只读。"""
    if err := _require_login():
        return err
    from . import exchange as ex
    api = await ex.theme_peers(_session, tid) if tid else []
    manual = ex.load_manual_peers()
    return {"ok": True, "tid": tid, "api_peers": api,
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

    body: {theme_id, price?, card_id?, peers?:[qq..], use_api?:bool, manual_peers?:[qq..]}
    会并发请求多个卡友主页，耗时随卡友数线性增长（80 人约 10~30 秒）。
    """
    if err := _require_login():
        return err
    from . import exchange as ex
    theme_id = int(body.get("theme_id") or 0)
    peers = [str(p) for p in (body.get("peers") or []) if str(p).isdigit()]
    if not theme_id:
        return {"ok": False, "message": "请先选择目标套卡"}
    if body.get("use_api", True):
        peers += await ex.theme_peers(_session, theme_id)
    peers += [str(u) for u in (body.get("manual_peers") or []) if str(u).isdigit()]
    peers = list(dict.fromkeys(peers))
    if not peers:
        return {"ok": False, "message": "没有可扫描的卡友（主题无人玩或名单为空）"}
    try:
        return await ex.scan_peers(
            _session, theme_id, peers,
            price=int(body.get("price") or 0),
            card_id=int(body.get("card_id") or 0),
            concurrency=int(body.get("concurrency") or 8))
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


app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
