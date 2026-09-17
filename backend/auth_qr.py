"""QQ 扫码登录（ptlogin2 标准 QR 协议）。

流程：
1. ptqrshow 拿二维码 PNG + qrsig cookie
2. ptqrlogin 轮询扫码状态（**必须按提示文案判定，不能按返回码**）
3. 确认后访问跳转 URL，服务端下发 p_uin / p_skey / skey / uin 等 cookie

ptuiCB 返回码实测语义（2026-09-11 实测校正，此前注释一直是错的）：
- 未扫描：code=66，「二维码未失效。」
- 已扫描待确认：**code=67**，「二维码已扫描，请在手机上确认。」
- 已确认登录：code=0，「登录成功！」
也就是说 **67 不是"失效"**。把 67 当失效会立即停掉轮询，
导致手机确认成功后前端仍收不到 code 0 → 页面卡在"已失效"进不去主界面。
"""
import logging
import re
import time
from typing import Optional

import httpx

from .constants import PTLOGIN_APPID

# 注意 logger 名必须挂在 "mc.api" 层级下：只有 mc.api 配了 handler 且 propagate=False，
# root 没有 handler。用 "mc.qr" 这类名字的日志会被静默丢弃（2026-09-11 踩过）。
logger = logging.getLogger("mc.api.qr")

PT_QRSHOW = "https://ssl.ptlogin2.qq.com/ptqrshow"
PT_QRLOGIN = "https://ssl.ptlogin2.qq.com/ptqrlogin"
# 登录回跳用你验证过能登录的官方地址（gamebar）。
# 注意：gamebar 是静态移动壳，check_sig 跳到它不会签发空间的 p_skey，
# 所以 p_skey 需在登录成功后另想办法拿（不能靠改这里解决，改了会导致登录失败）。
U1 = "https://qzs.qq.com/qzone/hybrid/page/gamebar/direct.html?appid=1104735529"


def hash33(s: str) -> int:
    """ptqrtoken 计算（ptlogin 标准 hash33）。"""
    e = 0
    for ch in s:
        e += (e << 5) + ord(ch)
    return 2147483647 & e


class QRLogin:
    def __init__(self) -> None:
        # trust_env=False：腾讯服务国内直连可达，不要读环境代理（如 127.0.0.1:xxxx），
        # 否则后台进程继承的代理端口一旦失效就会 ConnectError（curl 能通但 httpx 走代理死端口）。
        self.client = httpx.AsyncClient(timeout=15, follow_redirects=True, trust_env=False)
        self.qrsig: Optional[str] = None
        # used：本张二维码是否已被消费（确认登录成功）或失效。
        # 一旦 used，refresh/重新取码必须换新码，避免复用一张已死的码；
        # 未 used 时刷新应复用同一张（含正等待确认的那张），防止覆盖掉正在进行的扫码。
        self.used = False

    async def get_qr(self) -> bytes:
        """返回二维码 PNG 字节，并保存 qrsig cookie。"""
        # 新二维码 = 新会话：清掉上一轮遗留的 cookie（尤其 skey/uin）。
        # 否则 poll() 里「cookie 已有 skey 即视为登录成功」的兜底会把旧会话误判成新登录。
        self.client.cookies.clear()
        ts = int(time.time() * 1000)
        r = await self.client.get(
            PT_QRSHOW,
            params={
                "appid": PTLOGIN_APPID,
                "e": 0, "l": "M", "s": 3, "d": 72, "v": 4,
                "t": ts, "daid": "", "pt_3rd_aid": 0,
            },
        )
        self.qrsig = r.cookies.get("qrsig")
        self.used = False  # 新取码：回到未使用态
        return r.content

    async def poll(self) -> dict:
        """轮询扫码状态。返回 {status, uin?, cookies?}。"""
        if not self.qrsig:
            return {"status": "error", "msg": "请先获取二维码"}
        ts = int(time.time() * 1000)
        ptqrtoken = hash33(self.qrsig)
        r = await self.client.get(
            PT_QRLOGIN,
            params={
                "u1": U1,
                "ptqrtoken": ptqrtoken,
                "ptredirect": 1, "h": 1, "t": 1, "g": 1,
                "from_ui": 1, "ptlang": 2052,
                "action": f"0-0-{ts}",
                "js_ver": 20431, "js_type": 1,
                "login_sig": "", "pt_uistyle": 40,
                "aid": PTLOGIN_APPID, "daid": "", "pt_3rd_aid": 0,
            },
        )
        text = r.text
        parts = re.findall(r"'([^']*)'", text)
        code = parts[0] if parts else ""
        msg = parts[4] if len(parts) > 4 else ""
        # 全量记录每次轮询的真实返回（含等待态），便于下次再出问题直接看日志定因
        logger.info("poll qrsig=%s code=%s msg=%s",
                    (self.qrsig or "")[-8:], code, msg[:60])
        # ---- 0：已确认登录（必须最先判定，payload 里带跳转 URL）----
        if code == "0":
            jump = re.search(r"ptuiCB\('0','0','([^']+)'", text)
            if jump:
                try:
                    await self.client.get(jump.group(1))
                except Exception as e:  # noqa: BLE001
                    logger.warning("poll jump fetch failed: %r", e)
            cookies = {k: v for k, v in self.client.cookies.items()}
            uin = cookies.get("uin", "")
            self.used = True
            logger.info("poll login OK uin=%s skey=%s", uin,
                        "yes" if any("skey" in k.lower() for k in cookies) else "NO")
            return {"status": "ok", "uin": uin, "cookies": cookies}
        # ---- 硬兜底：登录成功的唯一铁证是 cookie jar 里出现 skey ----
        # 只要 skey 已下发，无论返回码/文案是什么都按成功处理。
        # 这直接堵死用户反复遇到的「手机显示登录成功、页面却进不去主界面」。
        # 由于 get_qr() 每次取码都会清空 cookie jar，这里的 skey 必然来自本次会话。
        cookies_now = {k: v for k, v in self.client.cookies.items()}
        if any("skey" in k.lower() for k in cookies_now):
            self.used = True
            logger.info("poll fallback OK(code=%s msg=%s) uin=%s",
                        code, msg[:30], cookies_now.get("uin", ""))
            return {"status": "ok", "uin": cookies_now.get("uin", ""), "cookies": cookies_now}
        # ---- 其余状态一律「文案优先」----
        # 未扫描与已扫描待确认阶段，腾讯会复用同一批返回码（实测 66 / 67），
        # 只有第 5 个字段的提示文案能区分。绝不能按 code 直接判失效——
        # 实测「扫完码」返回的是 code=67 +「二维码已扫描，请在手机上确认」，
        # 若按 code 当失效会停掉轮询，手机确认成功也进不去（2026-09-11 用户报告）。
        # 文案无法识别时降级为 waiting（继续轮询），保证后续 0 仍能被收到。
        # 注意否定词：「二维码未失效。」本身含「失效」二字，必须先排除，否则等待扫码会被误判为失效。
        if "未失效" in msg or "未扫描" in msg or "未过期" in msg:
            return {"status": "waiting"}
        if "已扫描" in msg or "已扫码" in msg or "确认" in msg or "扫描成功" in msg:
            return {"status": "scanned"}
        if "失效" in msg or "过期" in msg or "取消" in msg or "无效" in msg:
            self.used = True
            return {"status": "expired"}
        return {"status": "waiting"}
