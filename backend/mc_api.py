"""魔法卡片接口层：封装请求、g_tk 签名、XML 解析。

端点和基址来自 2014 年 mfkp 逆向参考值，2026 年真实接口需抓包确认。
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import httpx

from .constants import MC_BASE_URL, calc_gtk, err_msg

SESSION_FILE = Path("/tmp/mc_session.json")


class LoginSession:
    """持有登录态（cookie）并代理魔法卡片接口。生产环境应做多用户/持久化。"""

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=15)
        self.uin: Optional[str] = None
        self.logged_in: bool = False

    def load_cookies(self, cookies: dict) -> None:
        self.client.cookies.update(cookies)
        raw = cookies.get("uin") or cookies.get("p_uin", "")
        # ptlogin 的 uin cookie 形如 o1234567890
        self.uin = raw[1:] if raw.startswith("o") else raw
        self.logged_in = bool(cookies.get("skey"))
        self._save()

    def _save(self) -> None:
        try:
            SESSION_FILE.write_text(
                json.dumps(
                    {"uin": self.uin, "cookies": dict(self.client.cookies)},
                    ensure_ascii=False,
                )
            )
        except Exception:  # noqa: BLE001
            pass

    def restore(self) -> bool:
        """从本地文件恢复登录态（重启后端不丢登录）。"""
        try:
            if SESSION_FILE.exists():
                d = json.loads(SESSION_FILE.read_text())
                self.client.cookies.update(d.get("cookies", {}))
                raw = d.get("uin", "")
                self.uin = raw[1:] if raw.startswith("o") else raw
                self.logged_in = bool(d.get("cookies", {}).get("skey"))
        except Exception:  # noqa: BLE001
            return False
        return self.logged_in

    def clear(self) -> None:
        self.uin = None
        self.logged_in = False
        try:
            SESSION_FILE.unlink()
        except Exception:  # noqa: BLE001
            pass

    async def get(self, endpoint: str, **params) -> dict:
        params.setdefault("g_tk", calc_gtk(self.client.cookies.get("skey", "")))
        if self.uin:
            params.setdefault("uin", self.uin)
        resp = await self.client.get(MC_BASE_URL + endpoint, params=params)
        return parse_response(resp.text)

    async def probe_api(self) -> dict:
        """探测魔法卡片真实接口域名与可用端点（card.show.qq.com 已死，需现探）。"""
        out: dict = {"uin": self.uin, "hosts_found": [], "tests": []}
        # 1) 从游戏入口页扫描里面出现的 qq.com 主机名，定位真实 API host
        try:
            r = await self.client.get(
                "https://qzs.qq.com/qzone/hybrid/page/gamebar/direct.html?appid=1104735529",
                follow_redirects=True,
                timeout=12,
            )
            hosts = sorted(set(re.findall(r"https?://([a-zA-Z0-9.\-]+\.qq\.com)", r.text)))
            out["hosts_found"] = hosts
        except Exception as e:  # noqa: BLE001
            out["page_error"] = repr(e)
        # 2) 对候选 host 试一个已知端点，看哪个还活着
        g_tk = calc_gtk(self.client.cookies.get("skey", ""))
        for base in [
            "https://card.show.qq.com/cgi-bin/",
            "https://appimg.qq.com/cgi-bin/",
            "https://qzs.qq.com/cgi-bin/",
        ]:
            try:
                rr = await self.client.get(
                    base + "card_user_random_get",
                    params={"g_tk": g_tk, "uin": self.uin or "", "type": 1},
                    timeout=8,
                )
                out["tests"].append(
                    {"base": base, "status": rr.status_code, "head": rr.text[:160]}
                )
            except Exception as e:  # noqa: BLE001
                out["tests"].append({"base": base, "error": repr(e)[:160]})
        return out


def parse_response(xml_text: str) -> dict:
    """解析 <QQHOME code=.. message=..><card .../></QQHOME> 结构。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"code": -1, "message": "XML 解析失败", "raw": xml_text[:500]}
    code = int(root.get("code", -1))
    message = root.get("message", "")
    cards = [{k: c.get(k) for k in c.attrib} for c in root.iter("card")]
    return {
        "code": code,
        "message": message or err_msg(code),
        "cards": cards,
        "raw": xml_text,
    }
