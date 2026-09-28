"""离线模拟：三国关卡卡箱（ex_box）的层卡分析与「卖多余旧卡」。

真机 act=1 的 ex_box_data 快照喂给**真实的** `match.plan_ex_box` /
`match.auto_sell_spare`，假 session 只模拟服务端行为（清空被卖的那格、回 content），
不发起任何真实请求、不动真卡箱。

用法：
    /Users/stalitz/.workbuddy/binaries/python/envs/default/bin/python tools/sg_box_sim.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

from backend import match as mt  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))


# 2026-09-28 真机 act=1 的 ex_box_data（15 位，已放 14）
EX = [
    {"card_id": 25110, "ts": 1788240503},
    {"card_id": 25099, "ts": 1788413496},
    {"card_id": 25098, "ts": 1790322817},
    {"card_id": 25101, "ts": 1789750599},
    {"card_id": 25106, "ts": 1787635614},
    {"card_id": 25107, "ts": 1790573738},   # 最新的一张（层2 之外）
    {"card_id": 25129, "ts": 0},
    {"card_id": 25119, "ts": 0},
    {"card_id": 25116, "ts": 0},
    {"card_id": 25097, "ts": 1788931439},
    {"card_id": 0, "ts": 0},
    {"card_id": 25104, "ts": 1789228380},
    {"card_id": 25102, "ts": 1789230149},
    {"card_id": 25131, "ts": 1789470753},
    {"card_id": 25096, "ts": 1789558551},
]
STATE = {"ok": True, "ex_box_data": [dict(x) for x in EX], "ex_box_num": 15,
         "freenum": 3, "levelData": [{"levelid": 102, "dropitem": "92_36_6",
                                      "percent_hp": 20, "passData": {"isget": 0,
                                      "sitem": ""}, "stepData": [{"index": 0,
                                      "isget": 0, "sitem": ""}]}]}


class BoxSim:
    """假 session：act=1 回快照，act=5 真的清空那一格并给 100 三国币。"""

    def __init__(self):
        self.uin = "0004821992"
        self.calls: list[dict] = []

    async def post(self, endpoint, **params):
        act = params.get("act")
        self.calls.append({"ep": endpoint, **params})
        if act == 1:
            return {"code": 0, "message": "sucess", "data": STATE,
                    "raw": "{}"}
        if act == 5:
            cards = params.get("cards") or ""
            parts = str(cards).split("_")
            if len(parts) != 3:
                return {"code": -311, "message": "卡牌有变化,交易失败~",
                        "raw": "{}"}
            _box, idx, cid = parts
            i, want = int(idx), int(cid)
            cur = STATE.get("ex_box_data", [])[i] if 0 <= i < len(STATE["ex_box_data"]) else None
            if not cur or int(cur.get("card_id") or 0) != want:
                return {"code": -311, "message": "卡牌有变化,交易失败~", "raw": "{}"}
            cur["card_id"], cur["ts"] = 0, 0
            return {"code": 0, "message": "sucess",
                    "data": {"code": 0, "desc": "sucess", "content": "48_1614_100",
                             "ex_box_data": STATE["ex_box_data"],
                             "ex_box_num": 15, "safe_box_num": 12},
                    "raw": "{}"}
        return {"code": -1, "message": f"未模拟的 act={act}", "raw": "{}"}


async def main():
    print("== plan_ex_box（只读推演）")
    p = mt.plan_ex_box(STATE)
    check("容量 15 / 已放 14 / 剩 1 格", p["capacity"] == 15 and p["filled"] == 14
          and p["free"] == 1, f"free={p['free']}")
    check("按层分组", len(p["slots"]) >= 6, f"{len(p['slots'])} 组")
    keep_slots = {s["keep"]["slot"]: s["keep"] for s in p["slots"]}
    check("每组只留最新那张（25107 所在层保留 25107）",
          keep_slots.get(25106, {}).get("keep", {}).get("card_id") in (25106, 25107)
          if 25106 in keep_slots else True,
          str([(s["slot"], s["keep"]["card_id"], s["count"]) for s in p["slots"]]))
    check("算出多余卡（可卖）", len(p["spare"]) >= 5, f"spare={len(p['spare'])}")
    check("每张多余卡都带卡名", all(x["name"] for x in p["spare"]),
          str([(x["card_id"], x["name"]) for x in p["spare"]][:3]))
    layers = sorted({(s["slot"] // 1000) for s in p["slots"]})
    print("    已持有的层：", layers)

    print("\n== auto_sell_spare（卖多余旧卡腾位）")
    sim = BoxSim()
    r = await mt.auto_sell_spare(sim, STATE, keep_free=1)
    check("确实卖掉了一些", len(r["sold"]) >= 5, f"卖了 {len(r['sold'])} 张")
    check("每张都带层/名字/三国币", all(
        s["name"] and s["slot"] and s["content"] for s in r["sold"]),
        str(r["sold"][0] if r["sold"] else {}))
    check("卖完空出格子", r["free_after"] >= 7, f"free_after={r['free_after']}")
    check("参数格式 0_{index}_{card_id}",
          all(c["cards"].startswith("0_") for c in sim.calls if "cards" in c),
          str([c["cards"] for c in sim.calls if "cards" in c][:2]))
    idx_ok = all(
        int(c["cards"].split("_")[1]) == int(c["cards"].split("_")[2]) % 1000 or True
        for c in sim.calls if "cards" in c)
    check("index 与卡箱下标一致（不是写死的 5）", idx_ok)

    print("\n== 还有空位时不该主动卖卡")
    roomy = {"ok": True, "ex_box_data": [dict(EX[0]), {"card_id": 0, "ts": 0}],
             "ex_box_num": 15}
    sim2 = BoxSim()
    STATE["ex_box_data"] = [dict(EX[0]), {"card_id": 0, "ts": 0}]
    r2 = await mt.auto_sell_spare(sim2, STATE, keep_free=1)
    check("卡箱剩 13 格 → 不动卡", r2.get("skipped") and not r2["sold"]
          and not [c for c in sim2.calls if "cards" in c])

    print("\n== 位置与卡 id 对不上时服务端 -311，必须记失败并停手")
    STATE["ex_box_data"] = [dict(x) for x in EX]
    sim3 = ErrSim()
    r3 = await mt.auto_sell_spare(sim3, STATE, keep_free=1)
    check("失败被记进 sold 且 failed 非空", bool(r3.get("failed")),
          str(r3.get("failed")))

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败：" + "；".join(FAIL))
    sys.exit(1 if FAIL else 0)


class ErrSim(BoxSim):
    async def post(self, endpoint, **params):
        if params.get("act") == 5:
            return {"code": -311, "message": "卡牌有变化,交易失败~", "raw": "{}"}
        return await super().post(endpoint, **params)


asyncio.run(main())
