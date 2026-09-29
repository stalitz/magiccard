"""离线模拟：西游记探索（card_worldexplore）——套卡缺口 / 跨箱重复 / 探索倒计时。

把真机 act=1 的快照喂给**真实的** `xiyou.plan_xiyou`，假 session 只模拟服务端
行为（收 / 卖 / 领奖），不发起任何真实请求、不动真卡箱。

真机基线（2026-09-28 xiyou.har 抓包 + 实机核对）：
  大关 2 = 天宫（套 1605），玩家持有 18/21，缺 太上老君 / 玉帝王母 / 兜率宫
  —— 与用户口述「还缺 3 个：第 2 层兜率宫、第 3 层玉帝王母、太上老君」逐条吻合。

用法：
    /Users/stalitz/.workbuddy/binaries/python/envs/default/bin/python tools/xiyou_sim.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

from backend import xiyou as xy  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))


# ---- 2026-09-28 真机 act=1 快照（与 xiyou.har 里的一致、只删掉了无关字段）
NEW = [{"card_id": 23201, "ts": 1775398847}, {"card_id": 23203, "ts": 1790322963},
       {"card_id": 23208, "ts": 1790323017}, {"card_id": 23198, "ts": 1790322965},
       {"card_id": 23199, "ts": 1776006204}, {"card_id": 23202, "ts": 1775799042},
       {"card_id": 23233, "ts": 1786004805}, {"card_id": 23196, "ts": 1790162770},
       {"card_id": 23197, "ts": 1790162705}, {"card_id": 23196, "ts": 1790322999},
       {"card_id": 23191, "ts": 1788682530}, {"card_id": 23201, "ts": 1790323000},
       {"card_id": 23196, "ts": 1790323004}, {"card_id": 23189, "ts": 1788682530},
       {"card_id": 23200, "ts": 1790162706}]
SAFE = [{"card_id": 23207}, {"card_id": 23210}, {"card_id": 23208}, {"card_id": 23206},
        {"card_id": 23214}, {"card_id": 23216}, {"card_id": 23209}, {"card_id": 23198},
        {"card_id": 23205}, {"card_id": 23203}, {"card_id": 23197}, {"card_id": 23215},
        {"card_id": 23199}, {"card_id": 23202}, {"card_id": 23204}, {"card_id": 23201},
        {"card_id": 23196}]
EX = [{"card_id": 0} for _ in range(6)]

NOW = int(time.time())
_STEPS = [{"getts": 200005, "gifts": "121_23196_1", "index": 0, "isget": 0}]
_STEPS += [{"getts": 200008, "gifts": "92_58_6", "index": i, "isget": 0}
           for i in range(1, 24)]
CURR = [{"formationid": 0, "levelid": 2, "origin_time": 86400,
         "startts": NOW - 3600, "stepData": _STEPS}]

STATE = {
    "currLevelData": CURR, "logData": [], "formationList": [],
    "ex_box_data": EX, "ex_box_num": 6,
    "new_box_data": NEW, "new_box_num": len(NEW),
    "safe_box_data": SAFE, "safe_box_num": len(SAFE),
    "maxLevelId": 2, "nlb": 1477, "sjb": 47550, "txd": 176, "vlb": 0,
    "freepowerbit": [{"bit_1": 2, "id_1": 1}, {"bit_2": 0, "id_2": 2}],
    "shoplist": [{"cnt": 0, "id": 1}, {"cnt": 2, "id": 2}],
    "task_bits": 7, "task_detail": [{"cnt": 1, "id": 0, "type": 151},
                                    {"cnt": 1, "id": 1, "type": 152},
                                    {"cnt": 1, "id": 2, "type": 153}],
}


# ---------------------------------------------------------------- 纯函数
def test_plan():
    print("\n[1] plan_xiyou 纯函数")
    p = xy.plan_xiyou(STATE)
    print("   ", json.dumps({k: p[k] for k in ("level", "theme", "theme_name", "maxlevel")},
                            ensure_ascii=False))
    check("大关 2 ↔ 套 1605 天宫", p["level"] == 2 and p["theme"] == 1605
          and p["theme_name"] == "天宫", f"{p['theme_name']}")
    g = p["gap"]
    miss = sorted(m["name"] for m in g["missing"])
    check("天宫套 18/21", g["have"] == 18 and g["need"] == 21, f"{g['have']}/{g['need']}")
    check("缺的正好是用户说的 3 张（顺序无关）",
          miss == sorted(["太上老君", "玉帝王母", "兜率宫"]), str(miss))
    byname = {m["name"]: m["game_layer"] for m in g["missing"]}
    check("层号用游戏口径：兜率宫=第2层、玉帝王母/太上老君=第3层",
          byname.get("兜率宫") == 2 and byname.get("玉帝王母") == 3
          and byname.get("太上老君") == 3, str(byname))
    check("卡库层 4 → 游戏层 2（映射是 6 减）",
          all(m["game_layer"] == 6 - m["layer"] for m in g["missing"]))

    r = p["run"]
    check("探索进行中、总步 24、已收 0",
          r["running"] and r["total"] == 24 and r["got"] == 0,
          f"running={r['running']} total={r['total']} got={r['got']}")
    check("剩余时间 = 86400 - 已过 3600（±2 秒）",
          abs(r["remain"] - (86400 - 3600)) <= 2, str(r["remain"]))
    check("24 个收获点都可收（pending 24 个）", len(r["pending"]) == 24)

    # 跨箱重复：23208/23198/23197/23199/23203/23201/23202/23196 各在两箱
    dnames = sorted(x["name"] for x in p["dupes"])
    check("跨箱重复 8 组", len(p["dupes"]) == 8, str(len(p["dupes"])))
    check("重复卡按 card_id 聚合：同一张卡 3 份就只出 1 组、卖 2 份",
          all(len(x["sell"]) == x["count"] - 1 for x in p["dupes"]),
          str([(x["name"], x["count"], len(x["sell"])) for x in p["dupes"]]))
    check("留着的那份不在卖点里",
          all(x["keep"] not in [s["index"] for s in x["sell"]] for x in p["dupes"]),
          str([(x["name"], x["keep"]["index"], [s["index"] for s in x["sell"]])
               for x in p["dupes"]][:2]))
    check("每一组重复都记着卡 id（卖卡要拼 <箱>_<格>_<卡id>）",
          all(x["card_id"] for x in p["dupes"]))

    b = p["boxes"]
    # 格子数：收获箱 15 格（其中只有 12 张不同的卡），保险箱 17 格（17 张各一份）
    check("三箱格数 15 / 17 / 0",
          b["new"]["filled"] == 15 and b["safe"]["filled"] == 17 and b["ex"]["filled"] == 0,
          str([b[k]["filled"] for k in ("new", "safe", "ex")]))
    check("收获箱只持有 12 张不同的卡（23196 出现 3 次、23201 两次）",
          len({i["card_id"] for i in b["new"]["items"]}) == 12)
    check("保险箱 17 张互不重复",
          len({i["card_id"] for i in b["safe"]["items"]}) == 17)
    t = p["tasks"]
    check("3 个任务都已完成且可领",
          len(t) == 3 and all(x["done"] and x["claimable"] for x in t),
          str([(x["id"], x["done"], x["claimable"]) for x in t]))

    # 收掉一半后再算：缺口不能变（收获点只是"待收"，还没进箱子）
    half = json.loads(json.dumps(STATE))
    for s in half["currLevelData"][0]["stepData"]:
        s["isget"] = 1
    p2 = xy.plan_xiyou(half)
    check("全部收完后不再算「进行中」，但卡片仍按两箱算缺口",
          p2["run"]["running"] is False and p2["gap"]["have"] == 18
          and len(p2["gap"]["missing"]) == 3)

    # ---- 大关可选（2026-09-29 用户要求）：已开启的大关都列出来，带套名与缺口
    ch = {c["chapter"]: c for c in p["chapters"]}
    check("大关列表 = 已开启的 1..上限，levelid=N ↔ 套 1603+N",
          sorted(ch) == [1, 2] and (ch[1]["theme"], ch[2]["theme"]) == (1604, 1605)
          and (ch[1]["levelid"], ch[2]["levelid"]) == (1, 2),
          str([(c["chapter"], c["levelid"], c["theme"], c["name"])
               for c in p["chapters"]]))
    check("第 2 大关天宫是当前关、缺 3 张（与用户口述一致）",
          ch[2]["current"] and ch[2]["name"] == "天宫" and ch[2]["gap"] == 3
          and sorted(x["name"] for x in ch[2]["missing"]) ==
          sorted(["兜率宫", "玉帝王母", "太上老君"]),
          str({k: ch[2][k] for k in ("name", "gap", "current")}))
    check("第 1 大关花果山也列出来（可以回头补）、缺 19 张",
          ch[1]["name"] == "花果山" and ch[1]["gap"] == 19
          and ch[1]["current"] is False and ch[1]["done"] is False,
          str({k: ch[1][k] for k in ("name", "have", "need", "gap")}))


# ---------------------------------------------------------------- 假 session
class FakeSession:
    """只按 xiyou.har 的行为回话：不联网、不真动卡。"""

    def __init__(self, state=None):
        self.state = json.loads(json.dumps(state or STATE))
        self.calls: list = []
        self.uin = "0004821992"        # _plain_uin() 要读它，别让真机会话逻辑炸掉

    async def post(self, ep, **kw):
        act = int(kw.pop("act", 0) or 0)
        self.calls.append((act, kw))
        if act == 1:                      # 主状态：把当前快照整个回出去
            return {"code": 0, "data": self.state}
        if act == 6:
            idx = int(kw.get("index", 0))
            sd = self.state["currLevelData"][0]["stepData"]
            if idx >= len(sd):
                return {"code": -1, "message": "index 越界"}
            sd[idx]["isget"] = 1
            return {"code": 0, "data": {"content": sd[idx]["gifts"],
                                        "isgetall": 1 if idx == len(sd) - 1 else 0}}
        if act == 9:
            tag = str(kw.get("cards") or "")
            parts = tag.split("_")
            if len(parts) != 3:
                return {"code": -311, "message": "卡牌有变化"}
            box_i, idx, cid = int(parts[0]), int(parts[1]), int(parts[2])
            if box_i != 2:
                return {"code": -1, "message": "未实证的箱号"}
            arr = self.state["new_box_data"]
            if idx >= len(arr) or int(arr[idx].get("card_id") or 0) != cid:
                return {"code": -311, "message": "卡牌有变化"}
            arr[idx] = {"card_id": 0, "ts": 0}
            self.state["new_box_num"] = len(arr)
            return {"code": 0, "data": {"content": "48_1601_100",
                                        "new_box_data": arr,
                                        "new_box_num": len(arr)}}
        if act == 16:
            tid = int(kw.get("id", 0))
            return {"code": 0, "data": {"content": f"48_1599_10,92_32_1", "bits": 1 << tid}}
        return {"code": 0, "data": {}}


def test_flow():
    print("\n[2] 收 / 卖 / 领奖（假服务端）")
    s = FakeSession()

    async def main():
        # 收 24 个收获点
        ok = 0
        for i in range(24):
            r = await xy.xiyou_get(s, 2, i)
            if r["ok"]:
                ok += 1
            else:
                check(f"收第 {i} 个成功", False, str(r.get("desc")))
                break
        check("连收 24 个收获点全部成功", ok == 24, f"ok={ok}")
        st = await xy.xiyou_state(s)
        p = xy.plan_xiyou(st)
        check("收完后当前不再有进行中的探索", p["run"]["running"] is False)

        # 卖一组重复卡
        s2 = FakeSession()
        st2 = await xy.xiyou_state(s2)
        p2 = xy.plan_xiyou(st2)
        print("     debug: state.ok=%s boxes=%s dupes=%s"
              % (st2.get("ok"), [len((st2.get("new_box_data")) or [])], len(p2["dupes"])))
        dup = p2["dupes"][0]
        sell = dup["sell"][0]
        r = await xy.xiyou_sell(s2, 2, sell["index"], sell["card_id"],
                                snap=f"2_{sell['index']}_{sell['card_id']}",
                                snap_num=p2["boxes"]["new"]["capacity"])
        check("卖重复卡（act=9）成功并返回奖励串",
              r["ok"] and str(r.get("content") or "").startswith("48_"),
              str(r.get("content")))
        st2 = await xy.xiyou_state(s2)
        p3 = xy.plan_xiyou(st2)
        # ⚠️ 组数不必然减 1：跨箱重复组卖掉收获箱那份后，该卡在保险箱里还留着 1 份，
        #    这一组因此不算重复了（组数 -1），但它仍会出现在「所有卡」里。
        #    能确定的是：被卖的那一格空了、这张卡的总份数少 1。
        cid = int(sell["card_id"])
        nb = [it for it in p2["boxes"]["new"]["items"] if it["card_id"] == cid]
        na = [it for it in p3["boxes"]["new"]["items"] if it["card_id"] == cid]
        b4 = next(d for d in p2["dupes"] if d["card_id"] == cid)
        af = next((d for d in p3["dupes"] if d["card_id"] == cid), None)
        check("卖掉的那份从收获箱消失了（该卡总份数 -1）",
              sell["index"] not in [it["index"] for it in na]
              and len(na) == len(nb) - 1
              and (af is None or af["count"] == b4["count"] - 1),
              "份数 %d→%d，组内总数 %d→%s"
              % (len(nb), len(na), b4["count"], af["count"] if af else "已不算重复"))
        r2 = await xy.xiyou_sell(s2, 2, 999, 1, snap="2_999_1", snap_num=15)
        check("对不上的格号会被拒绝（不会卖错卡）", not r2["ok"])

        # 领任务奖
        r3 = await xy.xiyou_claim_task(s2, 0)
        check("领任务奖（act=16）成功并带奖励串",
              r3["ok"] and "48_1599_10" in str(r3.get("content") or ""),
              str(r3.get("content")))

        # 大关可选：选了第 1 大关，出发就得带 levelid=1（不再跟当前大关 2）
        s3 = FakeSession()
        s3.state["currLevelData"] = []        # 没有进行中的探索 → 这一步真的会出发
        rr = await xy.run_xiyou(s3, do_get=False, do_task=False, levelid=1)
        departed = [kw for a, kw in s3.calls if a == 5]
        check("选第 1 大关 → 出发带的 levelid=1（不跟当前大关）",
              bool(departed) and departed[0].get("levelid") == 1
              and bool(rr.get("steps")) and rr["steps"][0]["ok"],
              str(departed[:1]))
        s4 = FakeSession()
        s4.state["currLevelData"] = []
        await xy.run_xiyou(s4, do_get=False, do_task=False)
        departed4 = [kw for a, kw in s4.calls if a == 5]
        check("没选大关 → 出发沿用当前大关（levelid=2）",
              bool(departed4) and departed4[0].get("levelid") == 2,
              str(departed4[:1]))

    asyncio.run(main())


test_plan()
test_flow()
print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
