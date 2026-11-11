"""总览「进行中的活动」只读状态（2026-11-11 接入）。

两类数据源，严格分开：
1. **静态活动总表**（免登录、跨账号共享、6h 缓存）：`card_run_base` 无参 POST 的
   `version_list.activityInfo` → `appimg2.qq.com/card/mk/activity_info_v_<ver>.xml`。
   里面有活动池、unix 起止时间、HTML 规则、兑换档位，**没有任何用户进度**。
2. **个人进度**（按账号、60s 缓存）：`card_monthtask act=1/3/5`，全是只读。
   act=1 集卡格 / act=3 月排行榜 / act=5 奖励档位。

★ 铁律（见 backend/daily.py）：`card_monthtask act=2/6` 返回「参数错误」，
疑似领奖/完成任务动作，参数没抓包确认前**绝不调用**，否则会真领真消耗。
本模块只允许出现 act 1/3/5，新增任何 act 前必须有真机抓包原文。
"""
import asyncio
import html as _html
import json
import re
import time
import xml.etree.ElementTree as ET

from . import item_dict as iid

STATIC_TTL = 6 * 3600.0
MONTH_TTL = 60.0
MONTH_ACTS = (1, 3, 5)              # 只读三兄弟，别加 2/6

# 静态总表是服务端配置，不含账号差异 → 进程级共享（与「故意共享的静态卡数据」同类）
_static: dict = {"ts": 0.0, "all": None, "version": "", "error": ""}

# 展示名覆盖层：活动名在官方表里是英文代号，中文名只在**有原文证据**时写进来。
# 别放 data/（该目录被 .gitignore 忽略，部署会丢），要补中文名直接改这里并注明出处。
_CN_NAMES = {
    # 证据：mofashangdian 规则正文「魔法帽、魔法书通过参与新活动【中华传统】获取」
    "zhonghuachuantong": "中华传统",
}


def _plain(text: str) -> str:
    """规则 HTML（且被 XML 实体转义过）→ 一行纯文本。"""
    s = _html.unescape(text or "")
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_all(raw: str) -> list[dict]:
    """静态总表 → 全量活动（含起止时间戳）。9 个 `<?xml?>` 头须剔除再包 <wrap>。"""
    clean = re.sub(r"<\?xml[^>]*\?>", "", raw or "").strip()
    root = ET.fromstring("<wrap>" + clean + "</wrap>")
    rules = {}
    for e in root.iter():
        if e.tag == "Activity_Rule_Info" and e.get("name"):
            rules[e.get("name")] = _plain(e.get("content") or "")
    out = []
    for e in root.iter():
        if e.tag != "Activity_Exchange_Info":
            continue
        try:
            start, end = int(e.get("activity_starttime")), int(e.get("activity_endtime"))
        except (TypeError, ValueError):
            continue
        code = e.get("name") or ""
        out.append({
            "code": code,
            "title": _CN_NAMES.get(code) or code,
            "id": e.get("id") or "",
            "start": start, "end": end,
            "jf": e.get("jf_name") or "",
            "cards": sum(1 for c in e if c.tag == "card_info"),
            "gifts": sum(1 for c in e if c.tag == "gift_info"),
            "rule": rules.get(code, ""),
        })
    return out


def pick_current(all_acts: list[dict], now: float | None = None) -> list[dict]:
    """按当前时间戳筛在期活动（每次请求都重筛，避免 6h 缓存里留过期活动）。"""
    now = int(now if now is not None else time.time())
    cur = []
    for a in all_acts or []:
        if not (a["start"] <= now <= a["end"]):
            continue
        left = a["end"] - now
        x = dict(a)
        x["days_left"] = left // 86400
        x["hours_left"] = left // 3600
        x["start_txt"] = time.strftime("%m-%d", time.localtime(a["start"]))
        x["end_txt"] = time.strftime("%m-%d", time.localtime(a["end"]))
        x["long_term"] = left > 365 * 86400
        cur.append(x)
    # 快结束的排前面，长期活动沉底
    cur.sort(key=lambda x: (x["long_term"], x["end"]))
    return cur


async def _static_table(session) -> tuple[list[dict], str, str]:
    """返回 (全量活动, 版本号, 错误文本)；6h 内复用，失败时退回上次成功的数据。"""
    nowt = time.time()
    if _static["all"] is not None and nowt - _static["ts"] < STATIC_TTL:
        return _static["all"], _static["version"], ""
    try:
        r = await session.post("card_run_base")
        urls = iid.parse_version_list(r.get("raw") or "")
        url = urls.get("activityInfo") or iid.DEFAULT_URLS["activityInfo"]
        raw = await asyncio.to_thread(iid._fetch, url)
        all_acts = _parse_all(raw)
        if not all_acts:
            raise ValueError("活动总表解析为空")
        ver = re.search(r"activity_info_(v_\d+)\.xml", url)
        _static.update({"ts": nowt, "all": all_acts,
                        "version": ver.group(1) if ver else "", "error": ""})
        return all_acts, _static["version"], ""
    except Exception as e:                                  # noqa: BLE001
        _static["error"] = f"活动总表读取失败：{e}"
        # 有旧数据就继续用（只是可能不是最新一期），别把整块总览打空
        if _static["all"] is not None:
            return _static["all"], _static["version"], _static["error"]
        return [], "", _static["error"]


def _month_from_raw(parts: dict[int, dict]) -> dict:
    """三个只读响应 → 前端契约字段。缺哪个都不让整块失败。"""
    a1 = parts.get(1) or {}
    if a1.get("code") != 0:
        return {"ok": False, "text": "月任务进度读取失败（act=1）"}
    suit = a1.get("tasksuit") or []
    a5 = parts.get(5) or {}
    rewards = a5.get("list") or []
    a3 = parts.get(3) or {}
    cur_rank = ((a3.get("month_rank_list") or {}).get("cur_rank_list") or {})
    return {
        "ok": True,
        "score": int(a1.get("score") or 0),
        "period": int(a1.get("taskperiodid") or 0),
        "suit_total": len(suit),
        "suit_done": sum(1 for x in suit if int(x.get("bit") or 0) == 1),
        "reward_total": len(rewards),
        "reward_done": sum(1 for x in rewards if int(x.get("bit") or 0) == 1),
        "rank": int(cur_rank.get("rank") or 0),
        "ts": int(a1.get("ts") or 0),
    }


async def _month_state(session, bucket: dict) -> dict:
    """按账号桶缓存 60s；串行三次只读，避开同一会话并发打签名。"""
    nowt = time.time()
    if bucket.get("data") and nowt - bucket.get("ts", 0) < MONTH_TTL:
        return bucket["data"]
    parts: dict[int, dict] = {}
    for act in MONTH_ACTS:
        try:
            r = await session.post("card_monthtask", act=act)
            raw = r.get("raw") or ""
            parts[act] = json.loads(raw) if raw.strip().startswith("{") else {"code": r.get("code")}
        except Exception:                                    # noqa: BLE001
            parts[act] = {}
        await asyncio.sleep(0.15)
    data = _month_from_raw(parts)
    bucket["ts"], bucket["data"] = nowt, data
    return data


async def overview(session, month_bucket: dict | None = None) -> dict:
    """总览活动区数据：静态在期活动 + 个人月任务进度。两者独立降级。"""
    bucket = month_bucket if isinstance(month_bucket, dict) else {"ts": 0.0, "data": None}
    all_acts, ver, static_err = await _static_table(session)
    month = await _month_state(session, bucket)
    now = int(time.time())
    return {
        "ok": True,
        "now": now,
        "version": ver,
        "static_ok": not static_err,
        "static_error": static_err,
        "activities": pick_current(all_acts, now),
        "month": month,
    }
