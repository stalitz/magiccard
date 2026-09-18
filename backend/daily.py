"""一键日常：只收录**已实证可用**的每日领取动作。

铁律：任何「还没抓包确认」的动作一律不许写进 ACTIONS（保留 hobbies 猜测会让
用户以为自动成功实则没领到）。新增一项前必须：真机调用 → 看 code + 服务端
原文 → 把成功码 / 「今日已领」码都写进判定。
"""
import xml.etree.ElementTree as ET

from .constants import EP_MONEY_DRAW, err_msg


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


# (key, 中文名, 执行函数) —— 加项必须先在上面写明实证依据
ACTIONS = [
    ("gift", "每日登陆礼包", _daily_gift),
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
