"""独立扫码登录脚本（调试用，不依赖 uvicorn 长驻服务）。

用法（项目根目录下）：
  backend/.venv/bin/python -m backend.login_debug

流程：
  1. get_qr() 取二维码并写入 /tmp/mc_qr.png
  2. 轮询 ptqrlogin 直到扫码确认（最多 ~9 分钟）
  3. 确认后跟随回跳 URL，把 cookie 写入 /tmp/mc_cookies.json
  4. 打印登录到的 uin 与所持有的 cookie 域（便于判断 g_tk 用哪个 skey）
"""
import asyncio
import json
import sys
import time

from .auth_qr import QRLogin

QR_PATH = "/tmp/mc_qr.png"
COOKIE_PATH = "/tmp/mc_cookies.json"
POLL_SECONDS = 540  # 9 分钟窗口
POLL_INTERVAL = 3


async def main():
    qr = QRLogin()
    png = await qr.get_qr()
    with open(QR_PATH, "wb") as f:
        f.write(png)
    print(f"QR_WRITTEN {QR_PATH} ({len(png)} bytes)", flush=True)

    deadline = time.time() + POLL_SECONDS
    last = None
    while time.time() < deadline:
        res = await qr.poll()
        st = res.get("status")
        if st != last:
            print(f"POLL {st}", flush=True)
            last = st
        if st == "ok":
            cookies = res.get("cookies", {})
            # 只保留有价值的登录态字段
            keep = {k: v for k, v in cookies.items()
                    if k in ("uin", "skey", "p_skey", "p_uin", "pt4_token",
                             "RK", "rv2", "g_tk", "qz_gdt", "AirPlayerFlag",
                             "LoginUin", "LoginNick", "p_luin", "p_lskey") or
                    k.endswith("_skey") or k.endswith("skey")}
            with open(COOKIE_PATH, "w") as f:
                json.dump(keep, f, indent=2)
            uin = cookies.get("uin", "")
            # 汇总各域 skey 情况
            domains = sorted({k for k in cookies if "skey" in k.lower()})
            print(f"LOGIN_OK uin={uin}", flush=True)
            print(f"SKEY_DOMAINS {domains}", flush=True)
            print(f"COOKIES_SAVED {COOKIE_PATH}", flush=True)
            return
        if st == "expired":
            print("QR_EXPIRED", flush=True)
            return
        await asyncio.sleep(POLL_INTERVAL)

    print("TIMEOUT_NOT_SCANNED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
