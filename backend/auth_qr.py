"""QQ 扫码登录（ptlogin2 标准 QR 协议）。

流程：
1. ptqrshow 拿二维码 PNG + qrsig cookie
2. ptqrlogin 轮询扫码状态（66=已扫待确认 / 0=已确认 / 67=失效）
3. 确认后访问跳转 URL，服务端下发 p_uin / p_skey / skey / uin 等 cookie
"""
import re
import time
from typing import Optional

import httpx

from .constants import PTLOGIN_APPID

PT_QRSHOW = "https://ssl.ptlogin2.qq.com/ptqrshow"
PT_QRLOGIN = "https://ssl.ptlogin2.qq.com/ptqrlogin"
# 登录回跳改为空间主页：check_sig 跳到 qzs.qq.com/qzone/ 时会签发空间的 p_skey
# （农场/卡片等空间应用要的是 qzs.qq.com 的 p_skey，而非 qq.com 的 skey）
U1 = "https://qzs.qq.com/qzone/"


def hash33(s: str) -> int:
    """ptqrtoken 计算（ptlogin 标准 hash33）。"""
    e = 0
    for ch in s:
        e += (e << 5) + ord(ch)
    return 2147483647 & e


class QRLogin:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=15, follow_redirects=True)
        self.qrsig: Optional[str] = None

    async def get_qr(self) -> bytes:
        """返回二维码 PNG 字节，并保存 qrsig cookie。"""
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
        m = re.search(r"ptuiCB\('(\d+)'", text)
        code = m.group(1) if m else "67"
        if code == "0":
            jump = re.search(r"ptuiCB\('0','0','([^']+)'", text)
            if jump:
                await self.client.get(jump.group(1))
            cookies = {k: v for k, v in self.client.cookies.items()}
            uin = cookies.get("uin", "")
            return {"status": "ok", "uin": uin, "cookies": cookies}
        if code == "66":
            return {"status": "scanned"}
        if code == "67":
            return {"status": "expired"}
        return {"status": "waiting"}
