"""总览活动状态回归（纯离线，不打真机、不联网）。

覆盖：静态总表解析（多 <?xml?> 头 / HTML 规则）、在期筛选与剩余天数、
月任务只读响应解析、按账号缓存桶隔离、**绝不调用 act=2/6 写动作**。
运行：/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3 backend/engine_v2/tests/_verify_activities.py
"""
import asyncio
import os
import sys
import time
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)

from backend import activities as A  # noqa: E402

RESULTS = []


def ck(name, cond, extra=""):
    RESULTS.append((name, bool(cond), str(extra)))


SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<Activity_Exchange_Infolist>
  <Activity_Exchange_Info id="1" name="current" activity_starttime="1000"
      activity_endtime="%d" jf_name="积分">
    <card_info card_name="套卡《甲》" card_jf="70"/>
    <card_info card_name="套卡《乙》" card_jf="80"/>
    <gift_info gift_name="小礼包" gift_jf="30"/>
  </Activity_Exchange_Info>
  <Activity_Exchange_Info id="2" name="expired" activity_starttime="1"
      activity_endtime="2" jf_name="积分"/>
  <Activity_Exchange_Info id="3" name="longone" activity_starttime="1000"
      activity_endtime="%d" jf_name="积分"/>
</Activity_Exchange_Infolist>
<?xml version="1.0" encoding="utf-8"?>
<Activity_Rule_Infolist>
  <Activity_Rule_Info name="current" isHtml="1"
    content="&lt;div&gt;1、活动时间：&lt;br/&gt;2、每天 3 次&lt;/div&gt;"/>
</Activity_Rule_Infolist>
""" % (int(time.time()) + 3 * 86400, int(time.time()) + 900 * 86400)


def test_parse():
    all_acts = A._parse_all(SAMPLE)
    ck("多 <?xml?> 头可解析", len(all_acts) == 3, len(all_acts))
    by = {a["code"]: a for a in all_acts}
    ck("规则 HTML 转纯文本", "每天 3 次" in by["current"]["rule"] and "<div>" not in by["current"]["rule"],
       by["current"]["rule"])
    ck("兑换档位计数", by["current"]["cards"] == 2 and by["current"]["gifts"] == 1)
    cur = A.pick_current(all_acts)
    ck("只筛在期活动", {a["code"] for a in cur} == {"current", "longone"}, [a["code"] for a in cur])
    ck("快结束的排前面", cur[0]["code"] == "current")
    # 样本 end 由 time.time() 现算，跨秒会让整除落在 2 或 3，断言只卡区间
    ck("剩余天数 & 长期标记", 2 <= cur[0]["days_left"] <= 3 and cur[1]["long_term"] is True,
       (cur[0]["days_left"], cur[1]["long_term"]))
    ck("缓存期内重筛不会留过期活动",
       len(A.pick_current(all_acts, now=int(time.time()) + 10 * 86400)) == 1)


def test_month():
    parts = {
        1: {"code": 0, "score": 7, "taskperiodid": 5, "ts": 1794372157,
            "tasksuit": [{"bit": 1}, {"bit": 0}, {"bit": 1}, {"bit": 0}]},
        3: {"code": 0, "month_rank_list": {"cur_rank_list": {"rank": 0}}},
        5: {"code": 0, "score": 7, "list": [{"bit": 0, "id": 11}, {"bit": 1, "id": 12}]},
    }
    m = A._month_from_raw(parts)
    ck("月任务字段齐全", m["ok"] and m["score"] == 7 and m["period"] == 5
       and m["suit_total"] == 4 and m["suit_done"] == 2
       and m["reward_total"] == 2 and m["reward_done"] == 1, m)
    ck("act=1 失败时整块降级而不是造 0", A._month_from_raw({})["ok"] is False)


class FakeSession:
    def __init__(self):
        self.calls = []

    async def post(self, ep, **params):
        self.calls.append((ep, params))
        if ep == "card_run_base":
            return {"raw": '{"version_list":[]}'}
        if ep != "card_monthtask":
            raise AssertionError("不该调用 " + ep)
        act = params.get("act")
        if act == 1:
            return {"code": 0, "raw": '{"code":0,"score":7,"taskperiodid":5,'
                    '"tasksuit":[{"bit":1},{"bit":0}],"ts":1794372157}'}
        if act == 3:
            return {"code": 0, "raw": '{"code":0,"month_rank_list":{"cur_rank_list":{"rank":0}}}'}
        if act == 5:
            return {"code": 0, "raw": '{"code":0,"score":7,"list":[{"bit":0,"id":11}]}'}
        raise AssertionError("出现未实证的 act=%s" % act)


def test_overview():
    # 直接塞静态缓存，彻底避开联网；6h TTL 内 _static_table 不会打 card_run_base
    all_acts = A._parse_all(SAMPLE)
    A._static.update({"ts": time.time(), "all": all_acts, "version": "v_test", "error": ""})

    async def go():
        s = FakeSession()
        b1, b2 = {"ts": 0.0, "data": None}, {"ts": 0.0, "data": None}
        d1 = await A.overview(s, b1)
        ck("返回结构", d1["ok"] and d1["static_ok"] and len(d1["activities"]) == 2
           and d1["month"]["ok"] and d1["version"] == "v_test", d1)
        ck("只读三兄弟，绝不碰 act=2/6",
           all(ep == "card_monthtask" and p["act"] in (1, 3, 5) for ep, p in s.calls), s.calls)
        ck("单次 3 次真机读", len(s.calls) == 3, len(s.calls))
        d2 = await A.overview(s, b1)
        ck("同账号 60s 缓存命中", len(s.calls) == 3 and d2["month"]["ok"])
        d3 = await A.overview(s, b2)
        ck("换账号桶独立重读", len(s.calls) == 6 and d3["month"]["ok"])
        # 模拟静态表过期 + 联网失败（monkeypatch _fetch，保证全程不联网）
        A._static["ts"] = 0.0
        A._static["error"] = ""
        A._static["all"] = all_acts
        from backend import item_dict as iid
        orig = iid._fetch
        iid._fetch = lambda url: (_ for _ in ()).throw(RuntimeError("offline"))
        try:
            s2 = FakeSession()
            d4 = await A.overview(s2, {"ts": time.time(), "data": {"ok": True}})
        finally:
            iid._fetch = orig
        ck("静态表刷新失败时沿用旧数据并显式回报",
           d4["static_ok"] is False and len(d4["activities"]) == 2 and bool(d4["static_error"]),
           d4["static_error"])

    asyncio.run(go())


def test_real_xml_if_present():
    """本机有真机静态表时，顺带验证真实 264KB 文件也能解析（不联网、不作为前置）。"""
    p = "/tmp/mc_activity_info.xml"
    if not os.path.exists(p):
        return
    raw = open(p, encoding="utf-8", errors="replace").read()
    all_acts = A._parse_all(raw)
    ck("真实 v_1288 可解析", len(all_acts) == 66, len(all_acts))
    ck("真实表能筛出在期活动", len(A.pick_current(all_acts)) >= 1,
       [a["code"] for a in A.pick_current(all_acts)])
    ck("规则表有中文正文", any(a["rule"] for a in all_acts))


if __name__ == "__main__":
    test_parse()
    test_month()
    test_overview()
    test_real_xml_if_present()
    bad = [r for r in RESULTS if not r[1]]
    for n, ok, extra in RESULTS:
        print(("PASS " if ok else "FAIL ") + n + ("  [" + extra + "]" if extra and not ok else ""))
    print("RESULT: " + (str(len(bad)) + " FAILED" if bad else "ALL " + str(len(RESULTS)) + " PASS"))
    sys.exit(1 if bad else 0)
