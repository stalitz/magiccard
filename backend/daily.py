"""一键日常：只收录**已实证可用**的每日领取动作。

铁律：任何「还没抓包确认」的动作一律不许写进 ACTIONS（保留 hobbies 猜测会让
用户以为自动成功实则没领到）。新增一项前必须：真机调用 → 看 code + 服务端
原文 → 把成功码 / 「今日已领」码都写进判定。
"""
import xml.etree.ElementTree as ET

from .constants import EP_MONEY_DRAW, err_msg
from . import gift as _gift
from . import zcg as _zcg


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

    2026-09-24 用户从游戏界面确认：**每天只能送 50 次**（gift.DAILY_CAP）。
    按「当日剩余额度」从 freegift 好友池（今天还没送的人）取前 N 人，按 card_user_list 的
    exp 从高到低排序（活跃高等级优先）；送完更新本地日计数。额度用尽或列表空如实回报。
    """
    remaining = _gift.gift_daily_remaining()
    if remaining <= 0:
        return {"ok": False, "code": -2,
                "text": f"今日送礼额度已用完（{_gift.DAILY_CAP}/{_gift.DAILY_CAP}），明天再送",
                "quota_done": True, "sent_list": []}
    r = await _gift.gift_send(session, friends=None, freegift_id=_gift.FREEGIFT_ID_48,
                              limit=remaining, sort_by_level=True)
    sent = r.get("sent", 0)
    failed = r.get("failed", 0)
    sent_list = r.get("sent_list") or []
    if sent > 0:
        d = _gift._load_daily()
        d["sent"] = d.get("sent", 0) + sent
        _gift._save_daily(d)
        left = _gift.gift_daily_remaining()
        return {"ok": True, "code": 0,
                "text": f"送礼 48面卡 成功 {sent} 人（按等级从高到低，今日 "
                        f"{_gift.DAILY_CAP - left}/{_gift.DAILY_CAP}）"
                        + (f"，{failed} 人失败" if failed else ""),
                "sent_list": sent_list}
    return {"ok": False, "code": r.get("code", -1),
            "text": (r.get("message") or "今日免费礼物列表为空")
                    + "（游戏内可能已达 50 次上限，或好友都已送过）",
            "sent_list": []}


# (key, 中文名, 执行函数) —— 加项必须先在上面写明实证依据
ACTIONS = [
    ("gift", "每日登陆礼包", _daily_gift),
    ("sendgift", "每日送礼（48卡，按等级送，每日上限50）", _daily_send_gift),
    # 珍藏阁炼卡：zhencang.har 真机实证。只投 10 面卡、绝不调用加速（card_zhongkong_zk），
    # 炉没炼完就等自然炼完。详见 backend/zcg.py 顶部铁律注释。
    ("zcg", "珍藏阁炼卡（投10面卡→三色魔晶，不加速）", _zcg.daily_zcg),
]


async def run(session, keys: list[str] | None = None):
    """按顺序跑一遍（async generator）。

    每完成一项就 yield 该 item 字典，便于前端边跑边显示（不再等整段跑完才出日志）。
    keys 为空 = 全跑。单项异常不影响后续项。
    """
    for key, name, fn in ACTIONS:
        if keys is not None and key not in keys:
            continue
        try:
            item = await fn(session)
        except Exception as e:  # noqa: BLE001
            item = {"ok": False, "code": -1, "text": f"异常：{e}"}
        item["key"], item["name"] = key, name
        yield item
