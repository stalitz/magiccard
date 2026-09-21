"""一键日常：只收录**已实证可用**的每日领取动作。

铁律：任何「还没抓包确认」的动作一律不许写进 ACTIONS（保留 hobbies 猜测会让
用户以为自动成功实则没领到）。新增一项前必须：真机调用 → 看 code + 服务端
原文 → 把成功码 / 「今日已领」码都写进判定。
"""
import xml.etree.ElementTree as ET

from .constants import EP_MONEY_DRAW, err_msg
from . import gift as _gift


def _xml_attrs(raw: str) -> dict:
    """从 <QQHOME ...> 里取全部属性（parse_response 只留 code/message）。"""
    try:
        return dict(ET.fromstring(raw or "").attrib)
    except ET.ParseError:
        return {}


async def _daily_gift(session) -> dict:
    """每日登陆礼包：card_user_money_draw，无参 POST。

    2026-09-19 真机实证：code=0 + `money_plus=100`（金币）/`days`（连续天数）/
    `todayend`（今日截止时间戳）；同一天重复领返 **-33007「系统金币今日已经发放。」**
    —— 已领要当成功处理，不然每天第二次点会假报错。
    """
    r = await session.post(EP_MONEY_DRAW)
    code = r.get("code")
    attrs = _xml_attrs(r.get("raw", ""))
    plus = int(attrs.get("money_plus") or 0)
    days = int(attrs.get("days") or 0)
    if code == 0:
        return {"ok": True, "code": 0, "text": f"金币 +{plus}（已连续 {days} 天）"}
    if code == -33007:
        return {"ok": True, "code": code, "text": "今日已领过"}
    return {"ok": False, "code": code, "text": r.get("message") or err_msg(code)}


async def _daily_send_gift(session) -> dict:
    """每日送礼：freegift_send 送 48 面卡（freegift_id=81）给好友。

    2026-09-20 songli.har 实证：端点 freegift/freegift_send 在 card.qzone.qq.com，
    参数 freegift_id=81 = 送 48 面卡；friends 为 `;` 分隔的 uin 列表。响应
    ecode=0 即成功（无「今日已送」码——送礼理论上每天可送多人，故以「发出人数>0」
    判定成功；若好友列表为空或接口拒，按失败处理）。
    """
    r = await _gift.gift_send(session, friends=None, freegift_id=_gift.FREEGIFT_ID_48)
    sent = r.get("sent", 0)
    failed = r.get("failed", 0)
    if sent > 0:
        return {"ok": True, "code": 0,
                "text": f"送礼成功 {sent} 人" + (f"（{failed} 人失败）" if failed else "")}
    return {"ok": False, "code": r.get("code", -1),
            "text": r.get("message") or "送礼失败（好友列表空或接口拒绝）"}


# (key, 中文名, 执行函数) —— 加项必须先在上面写明实证依据
ACTIONS = [
    ("gift", "每日登陆礼包", _daily_gift),
    ("sendgift", "每日送礼（48卡）", _daily_send_gift),
]


async def run(session, keys: list[str] | None = None) -> list[dict]:
    """按顺序跑一遍；keys 为空 = 全跑。单项异常不影响后续项。"""
    out: list[dict] = []
    for key, name, fn in ACTIONS:
        if keys is not None and key not in keys:
            continue
        try:
            item = await fn(session)
        except Exception as e:  # noqa: BLE001
            item = {"ok": False, "code": -1, "text": f"异常：{e}"}
        item["key"], item["name"] = key, name
        out.append(item)
    return out
