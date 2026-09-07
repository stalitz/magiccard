"""登录后自动诊断：系统性排查 card.show.qq.com 的 -1100（未登录到该应用）。

用法：后端服务运行后，在用户扫码登录期间后台跑：
    backend/.venv/bin/python -m backend.verify_p_skey
轮询 /api/me 直到登录成功，然后：
1) 列出 session cookie，检查是否含 p_skey（空间 skey）
2) 用多组变体实测 card_user_random_get：GET/POST、skey/p_skey 的 g_tk、
   带/不带 Referer、UA、appid 等，看哪一组能跳出 -1100
3) 尝试用登录态去空间入口换取 p_skey
结果写入 /tmp/p_skey_verify.json（不落盘任何 cookie 明文值）。
"""
import json
import time
from pathlib import Path

import httpx

SESSION_FILE = Path("/tmp/mc_session.json")
OUT_FILE = Path("/tmp/p_skey_verify.json")
ME_URL = "http://localhost:8000/api/me"
EP = "https://card.show.qq.com/cgi-bin/card_user_random_get"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


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


def code_of(text: str) -> str:
    """从 <QQHOME code=".." ..> 里取 code，取不到返回原文片段。"""
    import re
    m = re.search(r'code="(-?\d+)"', text)
    return m.group(1) if m else text[:80].replace("\n", " ")


def mk_client(cookies: dict) -> httpx.Client:
    c = httpx.Client()
    for k, v in cookies.items():
        c.cookies.set(k, v, domain=".qq.com")
    return c


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

    # ---- 矩阵测试：找出能跳出 -1100 的组合 ----
    skey = cookies.get("skey", "")
    p_skey = cookies.get("p_skey", "")
    base = {"uin": uin, "type": 1}
    variants = [
        ("GET skey-gtk 裸", "GET", {"g_tk": gtk(skey), **base}, {}),
        ("GET skey-gtk +Referer", "GET", {"g_tk": gtk(skey), **base},
         {"Referer": "https://card.show.qq.com/"}),
        ("GET skey-gtk +Referer+UA", "GET", {"g_tk": gtk(skey), **base},
         {"Referer": "https://card.show.qq.com/", "User-Agent": UA}),
        ("POST skey-gtk +Referer", "POST", {"g_tk": gtk(skey), **base},
         {"Referer": "https://card.show.qq.com/"}),
        ("GET skey-gtk +appid(游戏)", "GET",
         {"g_tk": gtk(skey), **base, "appid": 1104735529},
         {"Referer": "https://card.show.qq.com/"}),
        ("GET skey-gtk +appid(空间)", "GET",
         {"g_tk": gtk(skey), **base, "appid": 549000929},
         {"Referer": "https://card.show.qq.com/"}),
    ]
    if p_skey:
        variants.append(("GET p_skey-gtk +Referer", "GET",
                         {"g_tk": gtk(p_skey), **base},
                         {"Referer": "https://card.show.qq.com/"}))

    tests = []
    for label, method, params, headers in variants:
        cc = mk_client(cookies)
        try:
            if method == "GET":
                r = cc.get(EP, params=params, headers=headers, timeout=12)
            else:
                r = cc.post(EP, params=params, headers=headers, timeout=12)
            tests.append({"variant": label, "status": r.status_code,
                          "code": code_of(r.text)})
        except Exception as e:  # noqa: BLE001
            tests.append({"variant": label, "error": repr(e)[:120]})
    out["tests"] = tests

    # ---- 尝试用登录态换 p_skey ----
    probes = []
    cc = mk_client(cookies)
    for url in [
        "https://qzs.qq.com/qzone/",
        "https://user.qzone.qq.com/" + uin,
        "https://qzs.qq.com/qzone/v5/loginsucc.html",
        "https://h5.qzone.qq.com/",
    ]:
        try:
            r = cc.get(url, headers={"User-Agent": UA}, timeout=12,
                       follow_redirects=True)
            got = "p_skey" in cc.cookies
            probes.append({"url": url[:55], "status": r.status_code, "got_p_skey": got})
        except Exception as e:  # noqa: BLE001
            probes.append({"url": url[:55], "error": repr(e)[:120]})
    out["p_skey_probes"] = probes

    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
