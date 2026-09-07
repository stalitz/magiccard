"""长期登录守护进程：自动续码 → 自动轮询 → 登录成功即跑诊断。

解决的问题：二维码有有效期、后端重启会丢状态，导致用户反复扫码。
本进程常驻（默认 3 小时），期间：
  1. 每 95 秒自动换一张新码，写到 /tmp/mc_qr.png（与网页同源，同一 qrsig）
  2. 每 2 秒轮询 /api/login/poll
  3. 一旦登录成功，立刻调 /api/card/diagnose 并把结果写到 /tmp/card_diagnose.json
"""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
QR_PNG = Path("/tmp/mc_qr.png")
RESULT = Path("/tmp/card_diagnose.json")
QR_TTL = 95.0          # 换码周期（小于服务端 100s 缓存，保证拿到新码）
RUN_HOURS = 3.0


async def main() -> int:
    deadline = time.time() + RUN_HOURS * 3600
    async with httpx.AsyncClient(timeout=20) as c:
        while time.time() < deadline:
            # 1) 取新码（force=1 拿新 qrsig，并刷新服务端缓存）
            try:
                r = await c.get(f"{BASE}/api/login/qr", params={"force": 1})
                b64 = r.json()["qr"].split(",", 1)[1]
                QR_PNG.write_bytes(base64.b64decode(b64))
                print(f"[{time.strftime('%H:%M:%S')}] 二维码已更新 -> {QR_PNG}", flush=True)
            except Exception as e:
                print("取码失败:", e, flush=True)
                await asyncio.sleep(5)
                continue

            # 2) 在本张码有效期内轮询
            qr_deadline = time.time() + QR_TTL
            hard_deadline = qr_deadline + 300  # 已扫未确认时最多再等 5 分钟
            while time.time() < qr_deadline:
                await asyncio.sleep(2)
                try:
                    p = (await c.get(f"{BASE}/api/login/poll")).json()
                except Exception:
                    continue
                st = p.get("status")
                if st == "scanned":
                    print("手机已扫描，等待确认…", flush=True)
                    # 已扫未确认时不换码，避免把用户正在确认的码作废
                    if time.time() < hard_deadline:
                        qr_deadline = max(qr_deadline, time.time() + 60)
                    continue
                if st == "waiting":
                    continue
                if st == "ok":
                    print("登录成功 uin=", p.get("uin"), flush=True)
                    try:
                        d = await c.get(f"{BASE}/api/card/diagnose")
                        result = d.json()
                    except Exception as e:
                        result = {"stage": "error", "msg": str(e)}
                    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
                    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000], flush=True)
                    return 0
                if st in ("expired", "timeout", "error"):
                    break

    RESULT.write_text(json.dumps({"stage": "timeout", "msg": "守护进程超时，未登录"},
                                 ensure_ascii=False))
    print("守护进程超时退出", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
