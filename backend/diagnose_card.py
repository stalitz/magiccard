"""登录后自动诊断：定位并处理 card.show.qq.com 的 -1100。

用法：后端服务运行后，在用户扫码登录期间后台跑：
    backend/.venv/bin/python -m backend.diagnose_card
轮询 /api/me 直到登录成功，然后：
1) 测主页面入口 card_user_mainpage（POST code='' uin=）—— magicCard 的 CARDLOGINURL
2) 测抽卡 card_user_random_get（POST type=2）
3) 若返回 -1100 / -1005（未开通），调 card_user_register 开通后复测主页面
结果写入 /tmp/card_diagnose.json（不落盘 cookie 明文值）。
"""
import json
import re
import time
from pathlib import Path

import httpx

SESSION_FILE = Path("/tmp/mc_session.json")
OUT_FILE = Path("/tmp/card_diagnose.json")
ME_URL = "http://localhost:8000/api/me"
BASE = "https://card.show.qq.com/cgi-bin/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def gtk(s: str) -> int:
    """腾讯 g_tk：DJB hash(skey)，与 constants.calc_gtk 一致。"""
    h = 5381
    for c in s:
        h += (h << 5) + ord(c)
    return h & 0x7fffffff


def code_of(text: str) -> str:
    m = re.search(r'code="(-?\d+)"', text)
    return m.group(1) if m else ("NOXML:" + text[:60].replace("\n", " "))


def wait_login(c: httpx.Client, timeout: int = 900) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if c.get(ME_URL, timeout=5).json().get("logged_in"):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    return False


def mk(cookies: dict) -> httpx.Client:
    c = httpx.Client()
    for k, v in cookies.items():
        c.cookies.set(k, v, domain=".qq.com")
    return c


def main() -> None:
    out: dict = {"stage": "start"}
    if not wait_login(httpx.Client(timeout=15)):
        out.update(stage="timeout", msg="等待扫码超时（未登录）")
        OUT_FILE.write_text(json.dumps(out, ensure_ascii=False))
        print("TIMEOUT_NOT_SCANNED")
        return

    d = json.loads(SESSION_FILE.read_text())
    cookies = d.get("cookies", {})
    uin = d.get("uin", "")
    skey = cookies.get("skey", "")
    g = gtk(skey)
    out.update(stage="logged_in", uin=uin, cookie_names=list(cookies.keys()))

    c = mk(cookies)

    def call(label: str, ep: str, method: str = "POST", data: dict | None = None,
             params: dict | None = None) -> str:
        url = BASE + ep
        p = {"g_tk": g, **(params or {})}
        try:
            if method == "POST":
                r = c.post(url, params=p, data=data or {},
                           headers={"User-Agent": UA,
                                    "Referer": "https://card.show.qq.com/"},
                           timeout=12)
            else:
                r = c.get(url, params=p,
                          headers={"User-Agent": UA,
                                   "Referer": "https://card.show.qq.com/"},
                          timeout=12)
            code = code_of(r.text)
            out.setdefault("calls", []).append(
                {"call": label, "status": r.status_code, "code": code})
            return code
        except Exception as e:  # noqa: BLE001
            out.setdefault("calls", []).append({"call": label, "error": repr(e)[:120]})
            return "ERR"

    # 1) 主页面入口（magicCard 的 CARDLOGINURL，POST code='' uin=）
    c1 = call("mainpage(POST code='' uin=)", "card_user_mainpage",
              "POST", {"code": "", "uin": uin})
    # 2) 抽卡
    call("random_get(POST type=2)", "card_user_random_get", "POST", {"type": 2})

    # 3) 若像「未开通/未初始化」，先 register 开通再复测
    if c1 in ("-1100", "-1005", "-1001"):
        out["register_triggered_by"] = c1
        rc = call("register(GET 开通)", "card_user_register", "GET",
                  params={"timestamp": "36236368_0.7147724962439818"})
        out["register_result"] = rc
        # 开通后复测主页面（POST + GET 各一次）
        call("mainpage(复测 POST)", "card_user_mainpage", "POST",
             {"code": "", "uin": uin})
        call("mainpage(复测 GET)", "card_user_mainpage", "GET",
             params={"uin": uin})

    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
