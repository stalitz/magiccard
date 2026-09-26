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
import html
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from .mc_api import LoginSession
from .constants import (
    EP_FREEGIFT_SEND, EP_FREEGIFT_LIST, EP_FREEGIFT_TAKE, EP_FREEGIFT_FRDS,
    EP_TRANSFER, EP_MAINPAGE, EP_USER_LIST,
)

logger = logging.getLogger("mc.gift")

# 送礼用的免费礼物 id（HAR 实证：freegift_send freegift_id=81 = 送 48 面卡）。
FREEGIFT_ID_48 = 81
# 礼物箱上限（songli.har get_list 实测 + 用户口述；服务端会按 time 丢老件）。
GIFT_BOX_CAP = 50
# 送礼：**每次调用最多 5 个好友**（用户口述 + 实测），所以按 5 人拆批。
SEND_BATCH = 5
# 每日送礼硬上限（用户 2026-09-24 从游戏界面确认：每天只能送 50 次）。
# 本地计数器跟踪本工具今天已送人数，跨多次点击/重启不超 50；游戏内手动送的不计入，
# 但 freegift_get_frds 在额度用尽后返回空列表，工具会如实报「无可送」。
DAILY_CAP = 50
# 单次送礼默认人数上限（每日送礼按当日剩余额度覆盖此值）。
SEND_LIMIT = 30
# 变卡默认目标（HAR 实证：srcid=7773 → dstid=26117 涡旋仪，40 面卡）。
# 用户可在 UI 改成自己正在炼的套卡对应的 40 面卡 id。
DEFAULT_CHANGE_DST = 26117
# ⚠️ DEFAULT_CHANGE_DST 只是**手动按钮**的兜底值，自动炼卡流程**不许用它**：
#    2017 卡 26117 属 theme 1810「研精覃思」，而引擎炼的常常是别的套 ——
#    变出一张本套用不上的卡，这个环节就是白做（失败还白烧两张源卡）。
#    自动流程的目标由 auto_refine._pick_change_dst() 按「队列里缺哪张套内 40 面卡」挑。
# 礼物卡 itemid（freegift_take 产出，可作变卡 srcid）。
# ⚠️ **不固定**：产出哪个 id 取决于该礼物的 freegift_id 与 action，实测：
#   freegift_id=81 + action=2 → 7773(蚕丝)；82 + action=2 → 7775(金丝线)；
#   84 + action=1 → 7774(五彩线)。都是**48 面礼物卡**（card_info theme=487：蚕丝/五彩线/金丝线/羊毛…）。
# 所以**只认服务端返回的 itemid**，不写死白名单（2026-09-21 踩过：写死 (7773,7774) 把
# 真实成功的 7775 判成失败，卡住整条收礼→变卡链路）。
GIFT_ITEM_THEME = 487      # 48 面礼物卡所在套卡主题（仅作说明/校验参考）
# ── 卡箱里已有的 48 面礼物卡 → 优先就地变卡（用户 2026-09-26 反馈） ──
# 现状问题：自动炼卡时卡箱里躺着礼物送来的 48 面卡，而现有链路只认
#   freegift_take 刚收出来那一张 —— 箱里同套的那几张**一张都不用**（白占箱位）。
# 事实依据：礼物收进来产出的 7773(蚕丝)/7774(五彩线)/7775(金丝线) 与卡箱里
#   躺着的那几张**本来就是同一套** —— 同属 card_lib theme=487「手工织锦」。
#   该套里面额 48 的 7771..7776 就是礼物形态；7777+ 是 187/612 面的织锦，
#   不是礼物送的形态，不参与本逻辑。
# 变卡（card_user_transfer_card）只认「源卡的 slotid+slottype」，**不要求它是刚收的**
#   （HAR 实证 srcid 可以是卡箱里的任意卡 id），所以箱里同套的 48 面卡能直接当
#   srcid 用，整个 freegift_take 步骤可以省掉 —— 少一次收礼就少一次礼物箱消耗。
GIFT_THEME = 487           # 礼物卡所在套卡（与 GIFT_ITEM_THEME 同一套，此处为判据引用）
# qzreferrer 固定串（HAR 里 freegift_send/take 都带，照原样回传避免被服务端当成异常来源）。
_QZ_REFERRER = "https://appimg2.qq.com/card/index.htm?appid=365&page=receive"

# 每日送礼额度本地计数器（与游戏内 50 次/天上限对账；游戏内手动送的不计入本计数，
# 但 freegift_get_frds 在额度用尽后返回空列表，工具会如实报「无可送」）。
_DAILY_PATH = os.path.join("/tmp", "mc_gift_daily.json")


def _load_daily() -> dict:
    today = time.strftime("%Y-%m-%d")
    try:
        with open(_DAILY_PATH) as f:
            d = json.load(f)
        if d.get("date") != today:
            return {"date": today, "sent": 0}
        return d
    except Exception:
        return {"date": today, "sent": 0}


def _save_daily(d: dict) -> None:
    try:
        with open(_DAILY_PATH, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def gift_daily_remaining() -> int:
    """返回今天还能通过本工具送的人数（DAILY_CAP - 今天已送）。"""
    return max(0, DAILY_CAP - _load_daily().get("sent", 0))

# 变卡成功判定：code=0 且原始响应带 result="1"（见 HAR 两次成功样本）。
# 50% 失败时服务端多半返回 code!=0 或其他 result 值 —— 一律当失败，记录后继续。


async def gift_friends(session: LoginSession) -> dict:
    """拉好友列表（送礼对象的 uin 池）。只读。

    freegift_get_frds 返回 friends[]（全部 QQ 好友，含未玩本游戏的）与
    def_friends[]（默认推荐）。送礼用 friends 即可。
    """
    r = await session.qzone("get", EP_FREEGIFT_FRDS)
    d = r.get("data") or {}
    friends = [str(f.get("uin")) for f in (d.get("friends") or []) if isinstance(f, dict) and f.get("uin")]
    def_friends = [str(x) for x in (d.get("def_friends") or []) if x]
    if not friends:
        # 兜底：扫描 data 里任何「元素为带 uin 的 dict」的列表（接口字段名偶尔变动）
        for v in d.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) and x.get("uin") for x in v):
                friends = [str(x.get("uin")) for x in v if x.get("uin")]
                break
    return {"ok": r.get("code") == 0, "code": r.get("code"),
            "friends": friends, "def_friends": def_friends,
            "total": len(friends)}


async def _player_index(session: LoginSession) -> dict:
    """拉 card_user_list（玩家列表，含 uin/nick/exp），解析成 uin -> {nick, exp}。只读。"""
    try:
        r = await session.post(EP_USER_LIST)
    except Exception:
        return {}
    raw = r.get("raw", "")
    out = {}
    for m in re.finditer(r'<user\s+uin="(\d+)"\s+nick="([^"]*)"([^>]*)>', raw):
        exp = re.search(r'exp="(\d+)"', m.group(3) or "")
        out[m.group(1)] = {
            "nick": html.unescape(m.group(2)),
            "exp": int(exp.group(1)) if exp else 0,
        }
    return out


async def _level_sorted_gift_friends(session: LoginSession, limit: int):
    """送礼自动模式：取 freegift 好友池，按 card_user_list 的 exp 从高到低排序，排除自己，取前 limit。

    非玩家好友（不在 card_user_list）exp 记 0，自然排到最后；玩家按等级优先。
    返回 (ranked_list[{uin,nick,exp}], pool_total)。
    """
    fr = await gift_friends(session)
    pool = fr.get("friends") or []
    pool_total = fr.get("total", 0)
    players = await _player_index(session)
    self_uin = str(session.uin or "")
    rows = []
    for uin in pool:
        if uin == self_uin:
            continue
        p = players.get(uin, {})
        rows.append({"uin": uin, "nick": p.get("nick", ""), "exp": p.get("exp", 0)})
    rows.sort(key=lambda x: x["exp"], reverse=True)
    return rows[:limit], pool_total


async def gift_send(session: LoginSession, friends: Optional[list] = None,
                    freegift_id: int = FREEGIFT_ID_48, limit: int = SEND_LIMIT,
                    sort_by_level: bool = True) -> dict:
    """送 48 面卡（freegift_id=81）给好友。会真实发出，谨慎调用。

    ⚠️ 实测服务端**每次调用最多接受 5 个好友**（>5 人一起发不生效），所以按 5 人一批拆。
    每天上限 50 次（DAILY_CAP，用户 2026-09-24 从游戏界面确认）；每日送礼按当日剩余额度取人，
    （`limit` 在每日送礼场景下被「当日剩余额度」覆盖）。
    friends 留空（自动模式）→ 送礼对象按 **card_user_list 的 exp 从高到低** 取前 limit 人
    （排除自己；不在玩家列表里的好友 exp 记 0，自然排到最后）。sort_by_level=False 则退回
    原始 QQ 好友列表顺序取前 limit 人。
    返回 sent_list：[{uin, nick, exp}]，供上层打日志显示「送给谁」。
    """
    meta: dict = {}
    pool_total = 0
    if friends is None:
        if sort_by_level:
            ranked, pool_total = await _level_sorted_gift_friends(session, limit)
            friends = [x["uin"] for x in ranked]
            meta = {x["uin"]: {"nick": x.get("nick", ""), "exp": x.get("exp", 0)} for x in ranked}
        else:
            fr = await gift_friends(session)
            friends = fr["friends"]
            pool_total = fr.get("total", 0)
    friends = [str(u).strip() for u in friends if str(u).strip().isdigit()]
    if not friends:
        return {"ok": False, "code": -1, "message": "没有可送的好友",
                "batches": [], "sent": 0, "failed": 0, "friends_total": pool_total,
                "sent_list": []}
    total_friends = len(friends)
    if limit and limit > 0:
        friends = friends[:limit]

    batches = [friends[i:i + SEND_BATCH] for i in range(0, len(friends), SEND_BATCH)]
    results = []
    sent = 0
    failed = 0
    sent_list = []
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
            for u in bt:
                m = meta.get(u, {})
                sent_list.append({"uin": u, "nick": m.get("nick", ""), "exp": m.get("exp", 0)})
        else:
            failed += len(bt)
        results.append({"uins": bt, "ok": ok,
                        "code": r.get("code"),
                        "message": r.get("message") or ""})
        # 批间稍歇，避免触发限流
        await asyncio.sleep(0.4)
    return {"ok": failed == 0, "batches": results, "sent": sent, "failed": failed,
            "friends_total": pool_total or total_friends,
            "sent_list": sent_list,
            "message": f"已送 {sent} 人（好友共 {pool_total or total_friends} 人），失败 {failed} 人"}


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



# ───────────── 供「自动炼卡」调用：礼物箱快满 → 收礼 + 变卡 ─────────────
# 2026-09-21 用户定调：不做独立自动化，做成**自动炼卡里的一个可选环节**。
# 变出来的 40 面卡直接进卡箱，引擎本来就读卡箱当素材 → 不用买 10 面卡、也不用自己炼。

DEFAULT_UPPER = 45      # 礼物箱快满阈值（箱上限 50，留几格缓冲）
DEFAULT_LOWER = 25      # 收到这个件数就停手（约一半）

_face48: set[int] | None = None
_face_of_cache: dict[int, int] = {}


def _card_face(cid: int) -> int:
    """查一张卡的**面额**（0 = 卡库里查不到）。带缓存，反复调用不重复读卡库。"""
    if cid in _face_of_cache:
        return _face_of_cache[cid]
    lib = json.loads((Path(__file__).resolve().parent.parent
                      / "data" / "card_lib.json").read_text(encoding="utf-8"))
    for c in lib["card_list"]:
        _face_of_cache[int(c[0])] = int(c[3] or 0)
    return _face_of_cache.get(int(cid), 0)


def change_dst_is_face40(dstid: int) -> bool | None:
    """变卡目标是否**面额 40**。

    用户 2026-09-26 明确：「礼物卡只能变 40 面额的，变不了其他面额，因为当前的
    礼物卡只有 48 面额」。礼物套（theme=487）里 48 面的卡是唯一形态，变卡只把
    它变成面额 40 的卡，变不出别的面额。

    返回 True/False，**None = 卡库里查不到这张卡**（无法判定，调用方按放行处理，
    别因为查不到卡就把正常流程拦住）。
    """
    f = _card_face(int(dstid))
    return None if f == 0 else (f == 40)


def _face48_ids() -> set[int]:
    """礼物套（theme=487「手工织锦」）里面额 48 的卡 id = 礼物卡的形态。

    从卡库算而不是抄死 id：礼物卡与「卡箱里躺着的那几张」本来就是同一批东西，
    只要 theme 不变，这套定义自动跟着卡库走。
    """
    global _face48
    if _face48 is None:
        lib = json.loads((Path(__file__).resolve().parent.parent
                          / "data" / "card_lib.json").read_text(encoding="utf-8"))
        cards = {int(c[0]): c for c in lib["card_list"]}
        _face48 = set()
        for t in lib["theme_card_list"]:
            if int(t[0]) != GIFT_THEME:
                continue
            for cid in (t[11] or []):
                c = cards.get(int(cid))
                if c and int(c[3] or 0) == 48:
                    _face48.add(int(cid))
    return _face48


async def find_box_gift_cards(session: LoginSession) -> list[dict]:
    """扫卡箱（换卡箱 + 保险箱），找**同礼物套的 48 面卡**及其真实槽位。

    返回 [{"id": int, "slot": int, "slottype": 0|1}, ...]（箱子内的原始顺序）。
    读不到 / 箱里没有 → 空列表（**调用方据此判断「要不要去礼物箱取」**）。
    """
    try:
        r = await session.post(EP_MAINPAGE, code="", opuin=session.uin)
    except Exception:  # noqa: BLE001
        return []
    if r.get("code") != 0:
        return []
    ids = _face48_ids()
    if not ids:
        return []
    from .card_plan import parse_user_cards

    out = []
    for bname, st in (("changebox", 0), ("storebox", 1)):
        for slot, cid in (parse_user_cards(r.get("raw") or "").get(bname) or []):
            if cid in ids:
                out.append({"id": cid, "slot": slot, "slottype": st})
    return out


async def drain_box_gift_cards(session: LoginSession, dstid: int = DEFAULT_CHANGE_DST,
                               max_try: int = 1) -> dict:
    """卡箱里已有的同礼物套 48 面卡 → 就地变卡，**不走收礼**。

    变出的 dstid 卡直接进卡箱，自动炼卡引擎下一 tick 就当素材用
    （等价于「少买一张底卡、少炼一次」）。

    ⚠️ **变卡失败同样消耗源卡**（2026-09-26 真机实测：箱里 8 张，尝试 3 次只成 1 张，
       箱里直接剩 5 张 = 8-3）。所以：
         · max_try 默认 **1** —— 一个 tick 只试一次，失败就收手，绝不「换下一张继续」
           （旧版那样一次最多能烧掉 4 张源卡换 2 张目标卡，纯亏）；
         · 变出一张立刻停 —— dstid 是单张目标，连变没有意义。
    """
    # 目标面额硬校验：礼物卡只有 48 面一种形态，变卡只产出 40 面。
    # 传别的面额进去变，即使服务端收了也只是白烧一张源卡（变卡成功率约 50%）。
    v = change_dst_is_face40(dstid)
    if v is False:
        return {"ok": False, "skipped": True, "inbox": 0, "tried": 0, "changed": 0,
                "dst": dstid,
                "message": f"变卡目标 #{dstid} 不是 40 面卡，礼物卡变不了其他面额，本轮不动卡"}
    pool = await find_box_gift_cards(session)
    inbox = len(pool)
    if not inbox:
        return {"ok": True, "skipped": True, "inbox": 0, "tried": 0, "changed": 0,
                "dst": dstid,
                "message": "卡箱里没有礼物套的 48 面卡"}
    tried = changed = 0
    last = ""
    for p in pool[:max_try]:
        r = await change_card(session, p["id"], dstid, slotid=p["slot"],
                              slottype=p["slottype"])
        tried += 1
        if r.get("ok"):
            changed += 1
            last = f"#{p['id']} → {dstid} 成功"
            break
        last = f"#{p['id']} → {dstid} 失败 code={r.get('code')}"
        await asyncio.sleep(0.3)
    return {"ok": changed > 0, "skipped": False, "inbox": inbox, "tried": tried,
            "changed": changed, "dstid": dstid, "last": last,
            "message": f"卡箱礼物卡 {inbox} 张：变成功 {changed} 张"
                       + (f"；{last}" if last else "")}


async def drain_gift_box(session: LoginSession, dstid: int = DEFAULT_CHANGE_DST,
                         upper: int = DEFAULT_UPPER, lower: int = DEFAULT_LOWER,
                         action: int = 2, max_take: int = 30) -> dict:
    """礼物箱件数 ≥ upper 时，从最老的开始「收礼 + 变卡」，降到 ≤ lower 停手。

    设计成自动炼卡的一个前置环节：由调用方在「缺素材、准备买卡」之前调用。
    变卡约 50% 成功 → 失败的记下来继续，不阻塞；收礼失败（多半该条已失效）同样跳过。

    ★ 2026-09-26 用户定调「卡箱里有礼物送的 48 面卡时，可以优先变卡，不用先从礼物里
      取了变，卡箱没有再取」：所以本函数在动礼物箱**之前**，先检查卡箱里是否已经有
      同套（theme=487）的 48 面礼物卡 —— 有就就地变掉，本轮不收礼；箱里一张都没有
      （或全变失败）才走下面的收礼链路。

    目标必须面额 40（礼物卡只有 48 面一种形态，变卡只产出 40 面），否则直接拒绝，
    不收礼也不变卡 —— 收了也是白烧礼物卡。
    """
    v = change_dst_is_face40(dstid)
    if v is False:
        return {"ok": False, "skipped": True, "took": 0, "changed": 0, "dst": dstid,
                "message": f"变卡目标 #{dstid} 不是 40 面卡，礼物卡变不了其他面额，本轮不动手"}
    # ① 卡箱优先：不收礼，直接用箱里已有的同套 48 面卡变
    box = await drain_box_gift_cards(session, dstid)
    if box.get("inbox") and box.get("changed"):
        return {"ok": True, "skipped": False, "box": box["inbox"], "upper": upper,
                "took": 0, "changed": box["changed"], "from_box": True,
                "message": box.get("message") or ""}
    inb = await gift_inbox(session)
    if not inb.get("ok"):
        return {"ok": False, "message": inb.get("message") or "读礼物箱失败"}
    count = inb["count"]
    if count < upper:
        return {"ok": True, "skipped": True, "box": count, "upper": upper, "took": 0,
                "changed": 0,
                "message": f"礼物箱 {count}/{GIFT_BOX_CAP}，未到 {upper}，本环节跳过"}

    gifts = sorted(inb["gifts"], key=lambda g: g.get("time") or 0)   # 最老的先收
    took = changed = failed = 0
    last = ""
    for g in gifts[:max_take]:
        if count - took <= lower:
            break
        r = await take_and_change(session, g["index_id"], dstid, action)
        if r.get("stage") == "take" and not r.get("ok"):
            failed += 1
            last = f"收礼 #{g['index_id']} 失败 code={r.get('code')}"
            await asyncio.sleep(0.3)
            continue
        took += 1
        if r.get("ok"):
            changed += 1
        else:
            failed += 1
            last = f"变卡失败（约 50% 概率）code={r.get('code')}"
        await asyncio.sleep(0.4)
    return {"ok": True, "skipped": False, "box_before": count, "box_after": count - took,
            "took": took, "changed": changed, "failed": failed, "dstid": dstid,
            "last": last,
            "message": f"礼物箱 {count}→{count - took}：收 {took} 件、变成功 {changed} 张"
                       f"（dst={dstid}）" + (f"；{last}" if last else "")}
