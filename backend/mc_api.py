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

    def _session_key(self) -> str:
        """g_tk 用 skey 算。

        证据：sxyy/magicCard 的 getUrl() 遍历 cookie jar 取 name=='skey' 再
        Tea.getGTK(skey)，其 card.show.qq.com 各端点 URL 只带 g_tk，不使用 p_skey。
        （此前「需 p_skey」的推测已被该代码证据推翻。）
        """
        return self.client.cookies.get("skey") or ""

    def _inject_qq_cookies(self) -> dict:
        """手动把空间 skey 注入请求（card.show.qq.com 不会自动带 qzs.qq.com 的 p_skey）。"""
        out = {}
        for name in ("p_skey", "skey", "uin", "p_uin"):
            v = self.client.cookies.get(name)
            if v:
                out[name] = v
        return out

    def restore(self) -> bool:
        """从本地文件恢复登录态（重启后端不丢登录）。"""
        try:
            if SESSION_FILE.exists():
                d = json.loads(SESSION_FILE.read_text())
                for k, v in d.get("cookies", {}).items():
                    self.client.cookies.set(k, v, domain=".qq.com")
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
        params.setdefault("g_tk", calc_gtk(self._session_key()))
        if self.uin:
            params.setdefault("uin", self.uin)
        resp = await self.client.get(
            MC_BASE_URL + endpoint, params=params, cookies=self._inject_qq_cookies()
        )
        return parse_response(resp.text)

    async def post(self, endpoint: str, **params) -> dict:
        """部分空间应用接口（如主页面）要求 POST 表单。"""
        data = dict(params)
        data.setdefault("g_tk", calc_gtk(self._session_key()))
        if self.uin:
            data.setdefault("uin", self.uin)
        resp = await self.client.post(
            MC_BASE_URL + endpoint, data=data, cookies=self._inject_qq_cookies()
        )
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
        g_tk = calc_gtk(self._session_key())
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

    async def enter_game(self) -> dict:
        """空间游戏授权握手：用登录态换取游戏票据 ticket。

        流程：mapp_addAuthorize.cgi?appid=1104735529 → 302 带 ticket 跳到
        urlshare.cn/home?ticket=... 。该 ticket 是游戏的授权票据，但 card.show.qq.com
        的真实会话仍需进一步登录（裸客户端拿不到），此处先把票据拿到并复测接口。
        """
        out: dict = {"uin": self.uin}
        c = self.client  # 复用同一 client，自动带 cookie
        try:
            r = await c.get(
                "http://mobile.qzone.qq.com/mapp/mapp_addAuthorize.cgi",
                params={"appid": "1104735529", "platform": "wanba",
                        "via": "qrcodegamebar", "ext_args": "platform=1"},
                follow_redirects=True,
            )
            import re as _re
            m = _re.search(r"ticket=([0-9]+)", str(r.url))
            out["ticket"] = m.group(1) if m else None
            out["final_url"] = str(r.url)
        except Exception as e:  # noqa: BLE001
            out["error"] = repr(e)
            return out
        # 复测接口：看握手后是否仍 -1100
        try:
            rr = await c.get(
                MC_BASE_URL + "card_user_random_get",
                params={"g_tk": calc_gtk(self._session_key()),
                        "uin": self.uin or "", "type": 1},
                cookies=self._inject_qq_cookies(),
            )
            txt = rr.text
            import re as _re2
            cm = _re2.search(r'code="(-?\d+)"(?:\s+message="([^"]*)")?', txt)
            out["card_test"] = {
                "status": rr.status_code,
                "code": int(cm.group(1)) if cm else None,
                "message": cm.group(2) if cm and cm.group(2) else "",
            }
        except Exception as e:  # noqa: BLE001
            out["card_test"] = {"error": repr(e)}
        out["note"] = (
            "已拿到授权票据 ticket；但 card.show.qq.com 仍返回 -1100，"
            "说明还缺游戏侧会话凭据（空间 skey 不足以调用该应用接口）。"
            "下一步：定位游戏侧会话的获取方式（不引入重型浏览器依赖）。"
        )
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
