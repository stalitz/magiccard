"""免费礼物 + 变卡（magic transform）。

所有端点都在 **card.qzone.qq.com**（见 constants.QZONE_BASE_URL），由 mc_api.LoginSession.qzone() 代理。
端点与参数全部来自 songli.har（2026-09-20 用户抓包），无一处凭猜测：

  freegift/freegift_send   POST 送礼（日常送 48 卡：freegift_id=81）
        params: appid=365, friends="<uin>;<uin>;...", freegift_id=81, sfcount=0,
                uin, cb=1, r=<rand>, qzreferrer=<url>
        resp:   frameElement.callback({"change":{...},"ecode":0,"msg":"ok","rewards":10})
  freegift/freegift_get_list  GET  礼物箱列表（上限 50，超出按 time 丢老的）
        resp:   _Callback({"ecode":0,"freegifts":[{"freegift_id","sender","nick","index_id","time",...}]})
  freegift/freegift_take   POST 收礼（把礼物箱里的某条变成道具卡）
        params: appid=365, action=1|2, index_id=<id>, uin, cb=1, r, qzreferrer
        resp:   frameElement.callback({"change":{"bag":[{"itemid":7773|7774,"num":1,"type":1}]},"ecode":0})
        → action=2 产出 itemid 7773（蚕丝），action=1 产出 7774（五彩线）；二者都是 48 面礼物卡
  card_user_transfer_card  POST 变卡（魔变）
        params: uin, dstid=<目标卡>, slotid=1, kind=0, type=2, srcid=<源卡/礼物卡/道具卡>, slottype=0
                （prop 可选；HAR 第二次调用省略了 prop）
        resp:   <QQHOME code="0" ... result="1" manacost="0">
        → 实测模板：srcid=7773(蚕丝) → dstid=26117(涡旋仪,40 面)；成功率约 50%（服务端判定）

礼物卡 7773/7774 经 freegift_take 进入「道具袋」，再作为 srcid 喂给 transfer_card 变出 40 面卡
（26116/26117 移液器/涡旋仪）。变出的 40 面卡进卡箱，自动炼卡引擎会把它当普通素材用
（引擎本来就读卡箱里的卡当配方材料，不需改引擎）。

礼物箱上限 50 是服务端行为（超出自动丢 time 最老的），本模块只读 + 取，不试图在本地维持容量。
"""
import asyncio
import logging
import time
from typing import Optional

from .mc_api import LoginSession
from .constants import (
    EP_FREEGIFT_SEND, EP_FREEGIFT_LIST, EP_FREEGIFT_TAKE, EP_FREEGIFT_FRDS,
    EP_TRANSFER, EP_MAINPAGE,
)

logger = logging.getLogger("mc.gift")

# 送礼用的免费礼物 id（HAR 实证：freegift_send freegift_id=81 = 送 48 面卡）。
FREEGIFT_ID_48 = 81
# 礼物箱上限（songli.har get_list 实测 + 用户口述；服务端会按 time 丢老件）。
GIFT_BOX_CAP = 50
# 变卡默认目标（HAR 实证：srcid=7773 → dstid=26117 涡旋仪，40 面卡）。
# 用户可在 UI 改成自己正在炼的套卡对应的 40 面卡 id。
DEFAULT_CHANGE_DST = 26117
# 礼物卡 itemid（freegift_take 产出，可作变卡 srcid）。
# ⚠️ **不固定**：产出哪个 id 取决于该礼物的 freegift_id 与 action，实测：
#   freegift_id=81 + action=2 → 7773(蚕丝)；82 + action=2 → 7775(金丝线)；
#   84 + action=1 → 7774(五彩线)。都是**48 面礼物卡**（card_info theme=487：蚕丝/五彩线/金丝线/羊毛…）。
# 所以**只认服务端返回的 itemid**，不写死白名单（2026-09-21 踩过：写死 (7773,7774) 把
# 真实成功的 7775 判成失败，卡住整条收礼→变卡链路）。
GIFT_ITEM_THEME = 487      # 48 面礼物卡所在套卡主题（仅作说明/校验参考）
# qzreferrer 固定串（HAR 里 freegift_send/take 都带，照原样回传避免被服务端当成异常来源）。
_QZ_REFERRER = "https://appimg2.qq.com/card/index.htm?appid=365&page=receive"

# 变卡成功判定：code=0 且原始响应带 result="1"（见 HAR 两次成功样本）。
# 50% 失败时服务端多半返回 code!=0 或其他 result 值 —— 一律当失败，记录后继续。


async def gift_friends(session: LoginSession) -> dict:
    """拉好友列表（送礼对象的 uin 池）。只读。

    freegift_get_frds 返回 friends[]（全部 QQ 好友，含未玩本游戏的）与
    def_friends[]（默认推荐）。送礼用 friends 即可。
    """
    r = await session.qzone("get", EP_FREEGIFT_FRDS)
    d = r.get("data") or {}
    friends = [str(f.get("uin")) for f in (d.get("friends") or []) if f.get("uin")]
    def_friends = [str(x) for x in (d.get("def_friends") or []) if x]
    return {"ok": r.get("code") == 0, "code": r.get("code"),
            "friends": friends, "def_friends": def_friends,
            "total": len(friends)}


async def gift_send(session: LoginSession, friends: Optional[list] = None,
                    freegift_id: int = FREEGIFT_ID_48) -> dict:
    """送 48 面卡（freegift_id=81）给一批好友。会真实发出，谨慎调用。

    friends 留空 → 自动拉好友列表全送（按 10 人一批，对齐 HAR 的批量习惯）。
    返回逐批结果 + 成功/失败计数。
    """
    if not friends:
        fr = await gift_friends(session)
        if not fr.get("friends"):
            return {"ok": False, "code": -1, "message": "没有可送的好友",
                    "batches": [], "sent": 0, "failed": 0}
        friends = fr["friends"]
    friends = [str(u).strip() for u in friends if str(u).strip().isdigit()]
    if not friends:
        return {"ok": False, "code": -1, "message": "好友列表为空",
                "batches": [], "sent": 0, "failed": 0}

    batches = [friends[i:i + 10] for i in range(0, len(friends), 10)]
    results = []
    sent = 0
    failed = 0
    for bt in batches:
        payload = {
            "friends": ";".join(bt),
            "freegift_id": freegift_id,
            "sfcount": 0,
            "cb": 1,
            "r": round(time.time() * 1000),
            "qzreferrer": _QZ_REFERRER,
        }
        r = await session.qzone("post", EP_FREEGIFT_SEND, **payload)
        ok = r.get("code") == 0
        if ok:
            sent += len(bt)
        else:
            failed += len(bt)
        results.append({"uins": bt, "ok": ok,
                        "code": r.get("code"),
                        "message": r.get("message") or ""})
        # 批间稍歇，避免触发限流
        await asyncio.sleep(0.4)
    return {"ok": failed == 0, "batches": results, "sent": sent, "failed": failed,
            "message": f"已送 {sent} 人，失败 {failed} 人"}


async def gift_inbox(session: LoginSession) -> dict:
    """读礼物箱列表（freegift_get_list）。只读。

    返回 freegifts[]：每条 {index_id, freegift_id, sender, nick, time}，
    按 time 升序（最老的在前）。count 即当前礼物箱件数（上限 50）。
    """
    r = await session.qzone("get", EP_FREEGIFT_LIST)
    d = r.get("data") or {}
    gifts = d.get("freegifts") or []
    out = []
    for g in gifts:
        try:
            t = int(g.get("time") or 0)
        except (TypeError, ValueError):
            t = 0
        out.append({
            "index_id": int(g.get("index_id") or 0),
            "freegift_id": int(g.get("freegift_id") or 0),
            "sender": str(g.get("sender") or ""),
            "nick": g.get("nick") or "",
            "time": t,
        })
    out.sort(key=lambda x: x["time"])
    return {"ok": r.get("code") == 0, "code": r.get("code"),
            "cap": GIFT_BOX_CAP, "count": len(out), "gifts": out}


async def gift_take(session: LoginSession, index_id: int, action: int = 2) -> dict:
    """收一条礼物（freegift_take）→ 得到一张 48 面礼物卡（itemid 由服务端决定）。

    产出哪个 itemid 取决于该礼物的 freegift_id 与 action（见 GIFT_ITEM_THEME 段注释），
    不再写死白名单；只要 code=0 且拿到 itemid 即算成功。默认 action=2。
    """
    payload = {
        "action": action,
        "index_id": index_id,
        "cb": 1,
        "r": round(time.time() * 1000),
        "qzreferrer": _QZ_REFERRER,
    }
    r = await session.qzone("post", EP_FREEGIFT_TAKE, **payload)
    d = r.get("data") or {}
    bag = d.get("change", {}).get("bag", []) if isinstance(d.get("change"), dict) else []
    item = bag[0] if bag else {}
    itemid = int(item.get("itemid") or 0)
    num = int(item.get("num") or 0)
    ok = r.get("code") == 0 and itemid > 0
    return {"ok": ok, "code": r.get("code"), "message": r.get("message") or "",
            "itemid": itemid, "num": num, "action": action}


async def change_card(session: LoginSession, srcid: int, dstid: int,
                      slotid: int = 1, kind: int = 0, type_: int = 2,
                      slottype: int = 0, prop: int = 0) -> dict:
    """变卡（card_user_transfer_card）：把 srcid 变成 dstid。底层调用，需自行给对槽位。

    srcid 可以是：
      - 48 面礼物卡（freegift_take 产出，如 7773/7774/7775…）
      - 卡箱里的卡 id（changebox/storebox）
      - 道具卡 id（100/200/600 面额，来自 /api/props 的 subtype）
    dstid 是目标卡 id（默认 26117 = 40 面卡，用户可在 UI 改）。

    ⚠️ `slotid`/`slottype` 必须是**源卡在箱子里的真实槽位**（0=换卡箱/1=保险箱），
    不是固定值 —— 2026-09-21 实证：固定 slotid=1/slottype=0 → -32002「找不到相应的记录」。
    一般用 `change_any()`（自动定位槽位）而不是直接调本函数。

    成功 = code=0 且响应带 result="1"（见 HAR）。成功率约 50%，失败按返回判定。
    """
    payload = {
        "dstid": dstid,
        "slotid": slotid,
        "kind": kind,
        "type": type_,
        "srcid": srcid,
        "slottype": slottype,
    }
    if prop:
        payload["prop"] = prop
    r = await session.qzone("post", EP_TRANSFER, **payload)
    raw = r.get("raw", "")
    ok = r.get("code") == 0 and 'result="1"' in raw
    return {"ok": ok, "code": r.get("code"), "message": r.get("message") or "",
            "result": 1 if 'result="1"' in raw else 0,
            "raw": raw[:300]}


# ---- 变卡前必须定位源卡的真实槽位（否则 -32002） ----

async def locate_card(session: LoginSession, cid: int):
    """读 mainpage，找卡 `cid` 的 (slotid, slottype)。找不到返回 (None, None)。

    slottype 约定（与 card_plan.parse_user_cards 一致）：0=换卡箱 changebox / 1=保险箱 storebox。
    实证：HAR 里 7773 位于 changebox slot=1，其 transfer 调用正是 slotid=1&slottype=0；
    而收礼产出的卡（如 7774/7775）落在 **storebox**，必须带 slottype=1 + 实际槽号才成功。
    """
    r = await session.post(EP_MAINPAGE, code="", opuin=session.uin)
    if r.get("code") != 0:
        return None, None
    from .card_plan import parse_user_cards

    user = parse_user_cards(r.get("raw") or "")
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return None, None
    for bname, st in (("changebox", 0), ("storebox", 1)):
        for slot, c in user.get(bname) or []:
            if c == cid:
                return slot, st
    return None, None


async def change_any(session: LoginSession, srcid: int, dstid: int, prop: int = 0) -> dict:
    """自动定位源卡槽位后变卡（推荐入口）。

    - prop>0：按 HAR 的「道具卡变卡」形态调用（slotid=0/slottype=0，prop=<道具 id>）
    - 否则先读 mainpage 定位该卡的真实 slotid/slottype；定位不到直接返回失败，
      不发必然 -32002 的请求。
    """
    if prop:
        return await change_card(session, srcid, dstid, slotid=0, slottype=0, prop=prop)
    slot, st = await locate_card(session, srcid)
    if slot is None:
        return {"ok": False, "code": -32002,
                "message": f"卡 {srcid} 不在换卡箱/保险箱里（不在箱中或已被消耗）"}
    return await change_card(session, srcid, dstid, slotid=slot, slottype=st)


async def take_and_change(session: LoginSession, index_id: int,
                          dstid: int = DEFAULT_CHANGE_DST, action: int = 2) -> dict:
    """收一条礼物 → 自动定位槽位 → 变卡。一步完成（前端按钮 / Auto 循环用）。"""
    tk = await gift_take(session, index_id, action)
    if not tk.get("ok"):
        return {"ok": False, "stage": "take", **tk}
    src = tk["itemid"]
    ch = await change_any(session, src, dstid)
    return {"ok": bool(ch.get("ok")), "stage": "change",
            "index_id": index_id, "itemid": src, "dstid": dstid,
            "code": ch.get("code"), "message": ch.get("message") or "",
            "result": ch.get("result")}


# ---- 自动循环：礼物箱快满 → 收礼 + 变卡，降到一半就停 ----
# 勾选「优先取礼物卡变」即启用本循环。产出 40 面卡进卡箱，自动炼卡引擎自然当素材用。
class GiftAuto:
    """礼物箱→变卡 自动循环（后台任务）。

    策略（对齐用户口述）：
      - 礼物箱件数 >= upper（快满）→ 开始收礼 + 变卡
      - 一直收到件数 <= lower（剩一半）或礼物取完为止
      - 取完一轮后休眠 interval 秒，等礼物箱又被填满（新礼物到达）再触发
    变卡约 50% 成功：失败的条目记日志后跳过，不阻塞后续。
    """

    def __init__(self, session: LoginSession, dstid: int = DEFAULT_CHANGE_DST,
                 upper: int = 45, lower: int = 25, interval: int = 120,
                 action: int = 2):
        self.session = session
        self.dstid = dstid
        self.upper = upper
        self.lower = lower
        self.interval = interval
        self.action = action
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.last = {"count": 0, "took": 0, "changed": 0, "failed": 0,
                     "at": 0, "note": "未启动"}

    def status(self) -> dict:
        return {"running": self.running, "dstid": self.dstid,
                "upper": self.upper, "lower": self.lower, "interval": self.interval,
                **self.last}

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            while self.running:
                try:
                    await self._drain_once()
                except Exception as e:  # noqa: BLE001
                    logger.exception("礼物自动变卡异常")
                    self.last["note"] = f"异常：{e}"
                for _ in range(max(1, self.interval)):
                    if not self.running:
                        return
                    await asyncio.sleep(1)
        finally:
            self.running = False

    async def _drain_once(self) -> None:
        box = await gift_inbox(self.session)
        gifts = box.get("gifts") or []
        count = len(gifts)
        self.last["count"] = count
        self.last["at"] = int(time.time())
        if count < self.upper:
            self.last["note"] = f"礼物箱 {count} 件 < 上限 {self.upper}，暂不处理"
            return
        took = changed = failed = 0
        # 从最老的（time 最小）开始取，直到 <= lower 或取完
        for g in list(gifts):
            if not self.running:
                break
            if (count - took) <= self.lower:
                break
            # 收礼 + 变卡一步完成（内部读 mainpage 定位源卡真实槽位，避免 -32002）
            r = await take_and_change(self.session, g["index_id"], self.dstid, self.action)
            if r.get("stage") == "take" and not r.get("ok"):
                self.last["note"] = (f"收礼失败 index={g['index_id']} "
                                     f"code={r.get('code')} {r.get('message')}")
                failed += 1
                # 收礼失败多半是这条已失效，继续下一条而非卡死
                await asyncio.sleep(0.3)
                continue
            took += 1
            if r.get("ok"):
                changed += 1
            else:
                failed += 1
                self.last["note"] = (f"变卡失败 src={r.get('itemid')}→dst={self.dstid} "
                                     f"code={r.get('code')}（约50%概率，下轮再试）")
            await asyncio.sleep(0.5)
        self.last["took"] = took
        self.last["changed"] = changed
        self.last["failed"] = failed
        self.last["note"] = (f"处理完：收 {took} 件、变成功 {changed} 张"
                             + (f"、失败 {failed} 张" if failed else "")
                             + f"；礼物箱剩约 {count - took} 件")
