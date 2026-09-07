"""登录后自动验证：是否拿到空间 p_skey，以及能否用它打通 card.show.qq.com（-1100）。

用法：后端服务运行后，在用户扫码登录期间后台跑此脚本：
    backend/.venv/bin/python -m backend.verify_p_skey
轮询 /api/me 直到登录成功，然后检查 session cookie 里的 p_skey，并分别用
p_skey / skey 算 g_tk 实测接口对比哪个能通，结果写入 /tmp/p_skey_verify.json。
"""
import json
import time
from pathlib import Path

import httpx

SESSION_FILE = Path("/tmp/mc_session.json")
OUT_FILE = Path("/tmp/p_skey_verify.json")
ME_URL = "http://localhost:8000/api/me"
EP = "https://card.show.qq.com/cgi-bin/card_user_random_get"


def gtk(s: str) -> int:
    """腾讯 g_tk：DJB hash(skey)，与 constants.calc_gtk 一致。"""
    h = 5381
    for c in s:
        h += (h << 5) + ord(c)
    return h & 0x7fffffff


def wait_login(c: httpx.Client, timeout: int = 900) -> bool:
    """轮询 /api/me 直到登录成功（最多 15 分钟，给足扫码时间）。"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if c.get(ME_URL, timeout=5).json().get("logged_in"):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    return False


def main() -> None:
    out: dict = {"stage": "start"}
    c = httpx.Client(timeout=15)
    if not wait_login(c):
        out.update(stage="timeout", msg="等待扫码超时（未登录）")
        OUT_FILE.write_text(json.dumps(out, ensure_ascii=False))
        print("TIMEOUT_NOT_SCANNED")
        return

    d = json.loads(SESSION_FILE.read_text())
    cookies = d.get("cookies", {})
    uin = d.get("uin", "")
    out.update(
        stage="logged_in",
        uin=uin,
        cookie_names=list(cookies.keys()),
        has_p_skey="p_skey" in cookies,
    )

    # 分别用 p_skey / skey 算 g_tk 实测，对比哪个能通
    tests = []
    for key_name in ("p_skey", "skey"):
        val = cookies.get(key_name)
        if not val:
            tests.append({"key": key_name, "skip": "无此 cookie"})
            continue
        cc = httpx.Client()
        for k, v in cookies.items():
            cc.cookies.set(k, v, domain=".qq.com")
        r = cc.get(EP, params={"g_tk": gtk(val), "uin": uin, "type": 1}, timeout=12)
        tests.append({"key": key_name, "status": r.status_code, "body": r.text[:200]})
    out["tests"] = tests

    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
