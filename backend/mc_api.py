"""魔法卡片接口层：封装请求、g_tk 签名、XML 解析。

2026 实测要点（已用真实登录态验证）：
  * 基址 https://card.show.qq.com/cgi-bin/ 仍然可用
  * 所有接口必须 **POST** 表单；用 GET 一律返回 -1100
  * 多数接口需要 ver=1、code='' 等参数，缺失会返回 "versionError"
  * 登录态用 qq.com 的 skey 算 g_tk（与 magicCard(2016) 源码一致）
"""
import asyncio
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import httpx

from . import prop_dict as pd

logger = logging.getLogger("mc.api")
# 直接挂 handler：uvicorn 的 --log-level warning 不会自动带上本模块的 logger
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] mc.api: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False

from .constants import (  # noqa: F401
    MC_BASE_URL, EP_MAINPAGE, EP_REFINE, EP_CANCEL, EP_REFINED_GET, EP_STEAL_GET,
    EP_REGISTER, EP_RANDOM_GET, EP_PAI_OPEN, EP_SELL, EP_MOVE,
    calc_gtk, err_msg,
)

SESSION_FILE = Path("/tmp/mc_session.json")

# 连接层可重试异常：keep-alive 死连接、服务端提前断连、连接超时等。
RETRYABLE = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


class LoginSession:
    """持有登录态（cookie）并代理魔法卡片接口。生产环境应做多用户/持久化。"""

    def __init__(self) -> None:
        # trust_env=False：腾讯服务国内直连可达，不走环境代理（避免后台进程继承的代理端口失效导致 ConnectError）
        self.client = httpx.AsyncClient(
            timeout=15,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            trust_env=False,
        )
        # 连接池重建锁：避免并发请求同时重置，把彼此正在用的 client 关掉
        self._reset_lock = asyncio.Lock()
        self.uin: Optional[str] = None
        self.logged_in: bool = False

    def load_cookies(self, cookies: dict) -> None:
        # 重建整个 jar：restore() 已按 .qq.com 塞入旧的同名 cookie（skey 等），
        # 直接 update 会造成 httpx.CookieConflict（Multiple cookies exist with name=skey），
        # 导致后续所有带 cookie 的请求抛 500。登录成功即全新会话，旧 cookie 一并丢弃。
        jar = httpx.Cookies()
        for k, v in cookies.items():
            jar.set(k, v, domain=".qq.com")
        self.client.cookies = jar
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
        url = MC_BASE_URL + endpoint
        ck = self._inject_qq_cookies()
        cli = self.client
        try:
            resp = await cli.get(url, params=params, cookies=ck)
            return parse_response(resp.text)
        except RETRYABLE as e:
            logger.warning("GET %s 连接失败，重建连接池重试：%r", endpoint, e)
            await self._reset_client(cli)
            resp = await self.client.get(url, params=params, cookies=ck)
            return parse_response(resp.text)

    async def post(self, endpoint: str, **params) -> dict:
        """部分空间应用接口（如主页面）要求 POST 表单。

        连接自愈（2026-09-10）：长驻 AsyncClient 的 keep-alive 连接会被腾讯服务端
        在空闲后单方面关闭，连接池却在下次请求时继续复用这条死连接 → 持续抛
        ConnectError，表现为「已登录但取不到信息」，且只有重启进程才恢复。
        这里捕获连接类异常后丢弃旧连接池重建 client，并重试一次。
        """
        data = dict(params)
        data.setdefault("g_tk", calc_gtk(self._session_key()))
        if self.uin:
            data.setdefault("uin", self.uin)
        url = MC_BASE_URL + endpoint
        ck = self._inject_qq_cookies()
        cli = self.client
        try:
            resp = await cli.post(url, data=data, cookies=ck)
            return parse_response(resp.text)
        except RETRYABLE as e:
            logger.warning("POST %s 连接失败，重建连接池重试：%r", endpoint, e)
            await self._reset_client(cli)
            resp = await self.client.post(url, data=data, cookies=ck)
            return parse_response(resp.text)

    async def _reset_client(self, seen: Optional[httpx.AsyncClient] = None) -> None:
        """丢弃陈旧连接池并重建 client，cookie 原样迁移。

        seen 为触发重建的那个 client；若它已被别的协程换掉（说明刚重建过），
        直接返回，避免把新连接池又关一次。
        """
        async with self._reset_lock:
            if seen is not None and self.client is not seen:
                return
            old = dict(self.client.cookies)
            try:
                await self.client.aclose()
            except Exception:  # noqa: BLE001
                pass
            # trust_env=False：直连腾讯，不读环境代理
            new = httpx.AsyncClient(
                timeout=15,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
                trust_env=False,
            )
            for k, v in old.items():
                new.cookies.set(k, v, domain=".qq.com")
            self.client = new

    async def get_state(self) -> dict:
        """读取主页面并解析成结构化游戏状态（供前端渲染）。"""
        r = await self.post(EP_MAINPAGE, code="", opuin=self.uin)
        if r.get("code") != 0:
            return {"ok": False, "code": r.get("code"),
                    "message": r.get("message", ""), "raw": r.get("raw", "")[:200]}
        return parse_state(r.get("raw", ""))

    async def refine(self, target: int, **extra) -> dict:
        """开始炼卡。参数由 card_plan 构造，调用方负责核素材/炉位。"""
        from .card_plan import build_refine_payload

        mp = await self.post(EP_MAINPAGE, code="")
        if mp.get("code") != 0:
            return {"ok": False, "message": f"读取卡箱失败 code={mp.get('code')}"}
        payload = build_refine_payload(target, mp.get("raw", ""))
        if payload is None:
            return {"ok": False, "message": "素材不足（需要的三张素材卡不在换卡箱/保险箱）"}
        payload.update(extra)
        if self.uin:
            payload.setdefault("opuin", self.uin)
        r = await self.post(EP_REFINE, **payload)
        r["ok"] = r.get("code") == 0
        r["sent_target"] = target
        return r

    async def cancel_refine(self, slotid: int, stoveid: int = 0) -> dict:
        """取消炼卡，素材卡原样退回卡箱。

        实测（2026-09-08，买 10 面值卡实测通过）：
        - 端点 `card_stove_cancel`（其它候选名一律 bad request）。
        - 参数**只能**是 slotid / stoveid / ver 三个，多传 cardid 反而返回 -1。
        - stoveid 必须是该炉位卡的真实 stove 值（stovebox 的 card@stove
          属性），恒传 0 会对不上——这是之前一直失败的根因。
        - 取消成功后素材卡回到保险箱（storebox），不会消失。
        - 已完成（flag=2）的卡不能取消，需先取卡。
        """
        payload = {"slotid": slotid, "stoveid": stoveid, "ver": 1, "code": ""}
        if self.uin:
            payload.setdefault("opuin", self.uin)
        r = await self.post(EP_CANCEL, **payload)
        code = r.get("code")
        # code=-1 的服务端 message 编码是坏的（UTF-8 字节被按 GBK 解，显示成"锟斤拷"），
        # 这里直接用本地文案，避免把乱码透给前端
        msg = ("服务端已拒绝：取消炼卡功能当前不可用（系统繁忙）" if code == -1
               else (r.get("message") or "").strip() or f"未知错误 code={code}")
        return {
            "ok": code == 0,
            "code": code,
            "message": msg,
            "raw": (r.get("raw") or "")[:300],
        }

    async def get_refined_card(self, slotid: int, slottype=1) -> dict:
        """取出「普通炉」（自己炼）已炼制完成（flag=2）的卡。

        注意：本端点仅适用于普通炉。偷炉（用好友空炉炼自己的卡）必须用
        get_steal_card（card_stove_stealcard_get + opuin=好友uin）——普通端点对
        偷炉永远返回 -32001，不可混用。

        实测要点（2026-09-09 修复）：
        - 端点 `card_stove_refinedcard_get` 来自 magicCard 源码解读，参数为
          slotid + slottype=1 + ver=1 + code=''（uin/g_tk 由基类自动注入）。
        - **必须 POST 表单**：此前误用 HTTP GET，而 card.show.qq.com 的业务接口
          用 GET 一律返回 -1100（见模块头注释），导致取卡永远失败。
        - 返回值统一包装成 {ok, code, message}，前端据此判断成败。
        """
        payload = {"slotid": slotid, "slottype": slottype, "ver": 1, "code": ""}
        if self.uin:
            payload.setdefault("opuin", self.uin)
        r = await self.post(EP_REFINED_GET, **payload)
        code = r.get("code")
        msg = (r.get("message") or "").strip()
        if code == 0:
            msg = "取卡成功"
        elif not msg:
            msg = f"未知错误 code={code}"
        return {
            "ok": code == 0,
            "code": code,
            "message": msg,
            "raw": (r.get("raw") or "")[:300],
        }

    async def draw_random_cards(self, fetch_type: int = 2) -> dict:
        """随机抽卡（累积机会，最高16次，可单抽/全抽）。
        - type=1：单抽一次；type=2：一次抽完当前可抽次数（服务端可能分批返回）。
        - 成功返回 <QQHOME num="N" remain="M"><card .../>...，随机卡进换卡箱。
        - remain 即剩余抽奖次数。
        """
        r = await self.post(EP_RANDOM_GET, type=fetch_type)
        code = r.get("code")
        raw = r.get("raw") or ""
        n = len(re.findall(r"<card\b", raw))
        remain_match = re.search(r'<QQHOME[^>]*\bremain="(\d+)"', raw)
        remain = int(remain_match.group(1)) if remain_match else 0
        cards = r.get("cards") or []
        if code == 0:
            msg = f"随机抽卡成功，获得 {n} 张卡，剩余 {remain} 次"
        else:
            msg = (r.get("message") or "").strip() or f"未知错误 code={code}"
        return {
            "ok": code == 0,
            "code": code,
            "message": msg,
            "n_cards": n,
            "remain": remain,
            "cards": cards,
            "raw": raw[:300],
        }

    async def open_pai(self) -> dict:
        """翻牌（取自己炉子卡后派生的临时机会，必须紧接着取卡后翻，否则服务端返回 system busy）。

        - 端点 card_pai_open，参数 type=1 + ver=1 + code=''。
        - 翻牌奖励在 JSON 响应里：content / pack_str 字段（如 "48_1415_1"）。
        - 没有「刚取完卡」的待翻状态时返回 -1「system busy」（不是卡箱问题，是没牌可翻）。
        """
        r = await self.post(EP_PAI_OPEN, type=1, ver=1, code="")
        code = r.get("code")
        raw = r.get("raw") or ""
        data = r.get("data") or {}
        # 奖励内容优先取 JSON 的 content / pack_str（翻牌返回是 JSON 而非 XML）
        # 形如 "48_1415_1" = **`大类_类内编号_数量`**（不是「卡id」也不是「道具卡id」）：
        # 第 1 段是玩法/品类 id、第 2 段才是该类内部编号。
        # 翻译走 prop_dict.describe_pai → item_dict（大类_编号 → 名字），
        # 名字表与实证链见 backend/item_dict.py。
        content = (data.get("content") or data.get("pack_str") or "").strip()
        items = pd.describe_pai(content) if content else []
        summary = pd.summarize_pai(content)
        if code == 0:
            msg = "翻牌成功" + (f"，奖励: {summary}" if summary else "")
        elif code == -1 and "busy" in (r.get("message") or "").lower():
            msg = ("当前没有可翻的牌：翻牌机会来自「取自己炉位的卡」，取卡后须立即翻；"
                   "没有待翻牌时返回此提示（不是卡箱满，是此刻没有可翻的牌）")
        else:
            msg = (r.get("message") or "").strip() or f"未知错误 code={code}"
        return {
            "ok": code == 0,
            "code": code,
            "message": msg,
            "items": items,
            "raw": raw[:300],
        }

    async def sell_card(self, slot_no: int, card_id: int, box_type: int = 1) -> dict:
        """卖卡给 NPC。
        - 端点 card_market_npc_sell。
        - 参数：slot_no（卡槽位）, cardid, type（1=换卡箱/2=保险箱，待实测）, uin。
        - 卖卡获得金币 = 卡面值（10 面额卡卖 10 金币）。
        """
        payload = {"slot_no": slot_no, "cardid": card_id, "type": box_type, "uin": self.uin or ""}
        r = await self.post(EP_SELL, **payload)
        code = r.get("code")
        if code == 0:
            msg = "卖卡成功"
        else:
            msg = (r.get("message") or "").strip() or f"未知错误 code={code}"
        return {
            "ok": code == 0,
            "code": code,
            "message": msg,
            "raw": (r.get("raw") or "")[:300],
        }

    async def move_card(self, card_id: int, slot: int, from_box: int) -> dict:
        """换卡箱 <-> 保险箱 之间移动卡（card_user_storage_exchange）。

        参数（来自 sxyy/magicCard 真实源码，2026-09-10 实测通过）：
        - src  = 源箱内整数 slot（位置索引）
        - dest = 目标箱内整数空位 slot（由本方法自动查找）
        - type = 0 存入保险箱 / 1 取回换卡箱（指目标方向）
        - ver=1, code='' 必填，否则服务端返回 versionError
        - 调用方只需传 card_id + 源 slot（from_box 指定当前所在箱）。
        """
        to_box = 1 - from_box
        mtype = 0 if to_box == 1 else 1  # 0=存保险箱(to=1), 1=回换卡箱(to=0)
        # 自动查找目标箱第一个空位作为 dest
        mp = await self.post(EP_MAINPAGE, code="", opuin=self.uin)
        if mp.get("code") != 0:
            return {"ok": False, "code": mp.get("code"), "message": "读取卡箱失败"}
        st = parse_state(mp.get("raw", ""))
        tkey = "storebox" if to_box == 1 else "changebox"
        tbox = st.get(tkey, {})
        cur = tbox.get("cur", 0)
        used = {c.get("slot") for c in tbox.get("cards", []) if c.get("id", 0) > 0}
        dest = next((i for i in range(cur) if i not in used), None)
        if dest is None:
            where = "保险箱" if to_box == 1 else "换卡箱"
            return {"ok": False, "code": -1, "message": f"目标{where}已满，无处可移"}
        payload = {"src": slot, "dest": dest, "type": mtype, "uin": self.uin or "",
                   "ver": 1, "code": ""}
        r = await self.post(EP_MOVE, **payload)
        code = r.get("code")
        where = "保险箱" if to_box == 1 else "换卡箱"
        if code == 0:
            msg = f"已移到{where}（slot {dest}）"
        else:
            msg = (r.get("message") or "").strip() or f"未知错误 code={code}"
        return {
            "ok": code == 0,
            "code": code,
            "message": msg,
            "to_box": to_box,
            "dest_slot": dest,
            "raw": (r.get("raw") or "")[:300],
        }

    async def auto_random_draw_and_sell(self, max_rounds: int = 30) -> dict:
        """一键抽完所有随机抽卡次数，并自动卖掉 10 面额卡。
        - 循环调 draw_random_cards(type=2)，直到 remain=0 或出错或达到 max_rounds。
        - 抽到的卡中 price=10 的调用 sell_card 卖掉。
        - 返回统计：抽卡次数、获得卡数、卖出卡数、金币收入、剩余机会。
        """
        from . import card_plan

        total_draws = 0
        total_cards = 0
        sold = 0
        gold = 0
        errors = 0
        rounds = 0
        last_remain = -1
        last_draw_remain = 0
        randchance_before = 0

        while rounds < max_rounds:
            # 抽卡前先把卡箱里现有的 10 面额卡卖光腾位置（卡箱满会抽不进）
            sold_now, gold_now = await self._sell_all_ten()
            sold += sold_now
            gold += gold_now

            st = await self.post(EP_MAINPAGE, code="", opuin=self.uin)
            if st.get("code") != 0:
                errors += 1
                break
            randchance = 0
            m = re.search(r'<user\b[^>]*\brandchance="(\d+)"', st.get("raw", ""))
            if m:
                randchance = int(m.group(1))
            if rounds == 0:
                randchance_before = randchance
            if randchance <= 0:
                break
            if randchance == last_remain:
                # 防止 stuck
                errors += 1
                break
            last_remain = randchance

            r = await self.draw_random_cards(fetch_type=2)
            last_draw_remain = r.get("remain", 0)
            rounds += 1
            total_draws += 1
            if not r.get("ok"):
                errors += 1
                break

            total_cards += r.get("n_cards", 0)
            if r.get("remain", 0) <= 0:
                break

        # 收尾：把最后一轮抽到的 10 面额卡也卖光
        sold_now, gold_now = await self._sell_all_ten()
        sold += sold_now
        gold += gold_now

        message = "完成"
        if randchance_before <= 0:
            message = "当前没有随机抽卡机会（randchance=0），请等待自动恢复后再抽"
        elif total_draws == 0:
            message = "未进行抽卡，可能卡箱已满或机会已耗尽"

        return {
            "ok": errors == 0 and total_draws > 0,
            "draws": total_draws,
            "cards": total_cards,
            "sold": sold,
            "gold": gold,
            "remain": last_draw_remain,
            "randchance_before": randchance_before,
            "errors": errors,
            "message": message,
        }

    async def _sell_all_ten(self) -> tuple[int, int]:
        """扫描换卡箱，把目前所有 10 面额卡卖光（type=1），腾出空位。

        卡箱满时抽卡会失败，所以一键抽卡每轮抽之前都先调用本方法清库存。
        返回 (卖出张数, 获得金币)。不会卖非 10 面额卡（限时卡/套卡保留）。
        """
        from . import card_plan

        st = await self.post(EP_MAINPAGE, code="", opuin=self.uin)
        if st.get("code") != 0:
            return 0, 0
        raw = st.get("raw", "")
        cb = re.search(r"<changebox\b[^>]*>(.*?)</changebox>", raw, re.S)
        if not cb:
            return 0, 0
        sold = 0
        gold = 0
        for cm in re.finditer(r"<card\b([^>]*)>", cb.group(1)):
            attrs = cm.group(1)
            sm = re.search(r'\bslot="(\d+)"', attrs)
            im = re.search(r'\bid="(\d+)"', attrs)
            if not sm or not im:
                continue
            slot = int(sm.group(1))
            cid = int(im.group(1))
            if cid <= 0:
                continue
            meta = card_plan.card_details([cid])
            price = int((meta.get(cid) or {}).get("price") or 0)
            if price == 10:
                sr = await self.sell_card(slot, cid, box_type=1)
                if sr.get("ok"):
                    sold += 1
                    gold += price
        return sold, gold

    async def get_steal_card(self, slotid: int, opuin: str) -> dict:
        """取走偷炉（用好友空炉炼自己的卡）炼制完成的卡。

        实测结论（2026-09-09）：
        - 偷炉卡原始 XML 带 slottype="steal" + opuin=好友uin（普通炉无这两个字段）。
        - 普通端点 card_stove_refinedcard_get 对偷炉永远返回 -32001「系统繁忙」，不可用。
        - 正确端点为 card_stove_stealcard_get，且**必须带 opuin=好友uin**（不带/带自己uin均失败）。
        - 成功后卡进入自己卡箱、炉位清空。好友若已收走则卡不可见（也取不到），可见即能收。
        """
        payload = {"slotid": slotid, "opuin": opuin, "ver": 1, "code": ""}
        r = await self.post(EP_STEAL_GET, **payload)
        code = r.get("code")
        msg = (r.get("message") or "").strip()
        if code == 0:
            msg = "偷炉取卡成功"
        elif not msg:
            msg = f"未知错误 code={code}"
        return {
            "ok": code == 0,
            "code": code,
            "message": msg,
            "raw": (r.get("raw") or "")[:300],
        }

    async def get_refine_plan(self, max_plans: int = 10) -> dict:
        """生成「现在可以炼什么」的推荐方案。"""
        from .card_plan import plan_refines

        mp = await self.post(EP_MAINPAGE, code="")
        if mp.get("code") != 0:
            return {"ok": False, "message": f"读取卡箱失败 code={mp.get('code')}"}
        plans = plan_refines(mp.get("raw", ""), max_plans=max_plans)
        plans["ok"] = True
        return plans

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
    """解析 <QQHOME code=.. message=..> 结构；部分接口实际返回 JSON，需兼容。"""
    txt = xml_text.strip()
    if txt.startswith("{"):
        try:
            data = json.loads(txt)
            # 活动接口常见字段：code/desc；把 desc 映射到 message 保持统一
            code = data.get("code", 0)
            return {
                "code": int(code) if isinstance(code, (int, str)) and str(code).lstrip("-").isdigit() else -1,
                "message": data.get("desc", data.get("message", "")),
                "data": data,
                "raw": txt,
            }
        except json.JSONDecodeError:
            return {"code": -1, "message": "JSON 解析失败", "raw": txt[:500]}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"code": -1, "message": "XML 解析失败", "raw": xml_text[:500]}
    # 有些失败响应 code 不是数字（如 "versionError"），必须容错
    raw_code = root.get("code", -1)
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        code = -1
    # 服务端 message 里中文是 &#xXXXX; 数字实体，必须解码否则前端显示乱码
    message = html.unescape(root.get("message", "") or "")
    cards = [{k: c.get(k) for k in c.attrib} for c in root.iter("card")]
    return {
        "code": code,
        "message": message or err_msg(code),
        "cards": cards,
        "raw": xml_text,
    }


def _i(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_state(xml_text: str) -> dict:
    """把 card_user_mainpage 的 XML 解析成前端可直接渲染的结构。

    结构见 docs/magicCard-源码解读.md 第 5 节。
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"ok": False, "message": "XML 解析失败"}

    out: dict = {"ok": _i(root.get("code"), -1) == 0}

    u = root.find("user")
    if u is not None:
        out["user"] = {
            "nick": u.get("nick", ""),
            "uin": u.get("uin", ""),
            "money": _i(u.get("money")),
            "exp": _i(u.get("exp")),
            "randchance": _i(u.get("randchance")),   # 剩余抽卡机会
        }

    hello = root.find("hello")
    now = _i(hello.get("time")) if hello is not None else 0
    out["now"] = now

    out["stoves"] = [
        {"id": _i(s.get("id")), "endtime": _i(s.get("endtime")), "lv": _i(s.get("lv"))}
        for s in root.iter("stove")
    ]

    def box(name: str) -> dict:
        b = root.find(name)
        if b is None:
            return {"cur": 0, "max": 0, "cards": []}
        cards = [{k: c.get(k) for k in c.attrib} for c in b.iter("card")]
        return {"cur": _i(b.get("cur")), "max": _i(b.get("max")), "cards": cards}

    sb = box("stovebox")
    slots = []
    for c in sb["cards"]:
        cid = _i(c.get("id"))
        # 空位没有 btime/locktime，直接相减会算出巨大负数，前端显示很难看
        left = (_i(c.get("btime")) + _i(c.get("locktime")) - now) if cid else 0
        slots.append({
            "slot": _i(c.get("slot")),
            "stove": _i(c.get("stove")),        # 取消炼卡必须带上，恒传 0 会失败
            "id": cid,
            "flag": _i(c.get("flag")),          # 2 = 炼制完成可取卡
            "left": left,
            # 好友互助炉（帮好友在其空炉炼卡）：slottype=steal 且带 opuin（好友 uin）。
            # 这种炉位炼好的卡归好友，自己取不了也取消不了，前端须识别并隐藏按钮。
            "slottype": (c.get("slottype") or "").strip(),
            "opuin": (c.get("opuin") or "").strip(),
        })
    out["stovebox"] = {"cur": sb["cur"], "max": sb["max"], "cards": slots}

    for key in ("changebox", "storebox"):
        b = box(key)
        filled = sum(1 for c in b["cards"] if _i(c.get("id")) > 0)
        out[key] = {
            "cur": b["cur"], "max": b["max"], "filled": filled,
            "cards": [
                {
                    "slot": _i(c.get("slot")),
                    "id": _i(c.get("id")),
                    "type": _i(c.get("type")),
                    "status": _i(c.get("status")),
                    "unlock": _i(c.get("unlock")),
                }
                for c in b["cards"] if _i(c.get("id")) > 0
            ],
        }

    return out
