"""FastAPI 入口：薄代理 + 扫码登录 + 静态前端。

职责（刻意保持轻量，不是重型后端）：
1. 提供静态前端（纯 HTML/JS，QQ 空间风格登录界面）
2. 代理 ptlogin 扫码登录（绕开浏览器 CORS）
3. 代理魔法卡片接口（带 cookie + g_tk 签名，待探明真实端点）

不使用任何 Web 框架的重量级特性，仅 FastAPI + StaticFiles。
"""
import base64
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from .auth_qr import QRLogin
from .mc_api import LoginSession

app = FastAPI(title="魔法卡片助手")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# 单用户 demo：登录态持久化到本地文件，重启不丢
_qr = QRLogin()
_session = LoginSession()
_session.restore()


@app.get("/api/login/qr")
async def login_qr():
    """取/刷新二维码（每次调用生成新码，重置 qrsig）。"""
    png = await _qr.get_qr()
    b64 = base64.b64encode(png).decode()
    return {"qr": f"data:image/png;base64,{b64}"}


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
    """玩吧登录握手：拿游戏票据 ticket（进一步会话需浏览器内登录）。"""
    if err := _require_login():
        return err
    return await _session.enter_game()


app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
