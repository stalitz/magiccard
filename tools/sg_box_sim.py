"""离线模拟：三国「关卡卡箱 ex_box」+「保险箱 safe_box」的层卡分析与「卖多余」。

真机 act=1 的 ex_box_data / safe_box_data 快照喂给**真实的**
`match.plan_box` / `match.plan_sg_boxes` / `match.auto_sell_spare`，
假 session 只模拟服务端行为（清空被卖的那格、回 content），
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
    {"card_id": 25107, "ts": 1790573738},   # 层2 最新的一张
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

# 保险箱 safe_box_data（12 位，全满；ts 全是 0；含第 4 层三张）
SAFE = [
    {"card_id": 25100, "ts": 0},
    {"card_id": 25105, "ts": 0},
    {"card_id": 25111, "ts": 0},
    {"card_id": 25114, "ts": 0},
    {"card_id": 25113, "ts": 0},
    {"card_id": 25115, "ts": 0},
    {"card_id": 25116, "ts": 0},
    {"card_id": 25111, "ts": 0},   # 重复
    {"card_id": 25116, "ts": 0},   # 重复
    {"card_id": 25103, "ts": 0},
    {"card_id": 25112, "ts": 0},
    {"card_id": 25106, "ts": 0},
]

LV = [{"levelid": 102, "dropitem": "92_36_6",
       "percent_hp": 20, "passData": {"isget": 0, "sitem": ""},
       "stepData": [{"index": 0, "isget": 0, "sitem": ""}]}]


def STATE(ex=None, safe=None, ex_num=15, safe_num=12) -> dict:
    """每轮给一份干净的快照，避免测试之间互相污染。"""
    return {"ok": True,
            "ex_box_data": [dict(x) for x in (EX if ex is None else ex)],
            "ex_box_num": ex_num,
            "safe_box_data": [dict(x) for x in (SAFE if safe is None else safe)],
            "safe_box_num": safe_num,
            "freenum": 3, "levelData": [dict(x) for x in LV]}


class BoxSim:
    """假 session：act=1 回当前快照，act=5 真的清空那一格并给三国币。

    箱子 0 = 关卡卡箱，1 = 保险箱（2026-09-28 真机实证）；卡位对不上回 -311。
    """

    def __init__(self, state: dict | None = None):
        self.uin = "0004821992"
        self.calls: list[dict] = []
        self.state = state if state is not None else STATE()
        self.resp = None          # 想让 act=5 固定失败时覆盖

    async def post(self, endpoint, **params):
        act = params.get("act")
        self.calls.append({"ep": endpoint, **params})
        if act == 1:
            return {"code": 0, "message": "sucess", "data": self.state, "raw": "{}"}
        if act == 5:
            if self.resp is not None:
                return self.resp
            parts = str(params.get("cards") or "").split("_")
            if len(parts) != 3:
                return {"code": -311, "message": "卡牌有变化,交易失败~", "raw": "{}"}
            box, idx, cid = parts
            key = "ex_box_data" if box == "0" else (
                "safe_box_data" if box == "1" else None)
            if key is None:
                return {"code": -311, "message": "卡牌有变化,交易失败~", "raw": "{}"}
            i, want = int(idx), int(cid)
            cur = self.state.get(key, [])[i] if 0 <= i < len(self.state[key]) else None
            if not cur or int(cur.get("card_id") or 0) != want:
                return {"code": -311, "message": "卡牌有变化,交易失败~", "raw": "{}"}
            cur["card_id"], cur["ts"] = 0, 0
            return {"code": 0, "message": "sucess",
                    "data": {"code": 0, "desc": "sucess",
                             "content": "48_1614_100",
                             "ex_box_data": self.state["ex_box_data"],
                             "safe_box_data": self.state["safe_box_data"],
                             "ex_box_num": 15, "safe_box_num": 12},
                    "raw": "{}"}
        return {"code": -1, "message": f"未模拟的 act={act}", "raw": "{}"}


class ErrSim(BoxSim):
    """act=5 永远 -311（模拟卡位对不上）。"""

    def __init__(self, state=None):
        super().__init__(state)
        self.resp = {"code": -311, "message": "卡牌有变化,交易失败~", "raw": "{}"}


async def main():
    print("== plan_ex_box / plan_box（只读推演关卡卡箱）")
    st = STATE()
    p = mt.plan_ex_box(st)
    check("容量 15 / 已放 14 / 剩 1 格",
          p["capacity"] == 15 and p["filled"] == 14 and p["free"] == 1,
          f"free={p['free']}")
    check("算出多余卡（可卖）", len(p["spare"]) >= 5, f"spare={len(p['spare'])}")
    check("每张多余卡都带卡名", all(x["name"] for x in p["spare"]),
          str([(x["card_id"], x["name"]) for x in p["spare"]][:3]))
    layers = sorted({(s["slot"] // 1000) for s in p["slots"]})
    check("关卡卡箱里没有第 4 层", 4 not in layers, f"持有层={layers}")

    print("\n== plan_box 保险箱（safe_box）")
    sp = mt.plan_box(st, "safe")
    check("容量 12 / 已放 12 / 剩 0 格",
          sp["capacity"] == 12 and sp["filled"] == 12 and sp["free"] == 0,
          f"filled={sp['filled']} free={sp['free']}")
    # 一张层卡可以有多个「同层不同张」：25113/25114/25115 都是层 4、位置值都是 4502，
    # 所以第 4 层那组是 3 张（留 1 张，另 2 张算多余）。
    g4 = next((s for s in sp["slots"] if s["layer"] == 4), None)
    check("保险箱第 4 层一组 3 张、只留 1 张",
          bool(g4) and g4["count"] == 3 and len(sp["spare"]) == 7,
          str([(s["layer"], s["keep"]["card_id"], s["count"]) for s in sp["slots"]]))
    check("保险箱 ts 全是 0 → 不假装「留最新」",
          all(m["ts"] == 0 for m in sp["spare"] + [s["keep"] for s in sp["slots"]]))

    print("\n== plan_sg_boxes（两箱一起看 + 缺层定位）")
    allp = mt.plan_sg_boxes(st)
    check("同时给出两箱", allp["ex"]["box"] == "ex" and allp["safe"]["box"] == "safe")
    t1745 = next((t for t in allp["themes"] if t["theme"] == 1745), None)
    check("1745 两箱合计不缺层", bool(t1745) and t1745["missing"] == [], str(t1745))
    check("但关卡卡箱缺第 4 层，且第 4 层只躺在保险箱里",
          bool(t1745) and t1745["in_safe_only"] == [4], str(t1745))
    t1746 = next((t for t in allp["themes"] if t["theme"] == 1746), None)
    check("1746 套缺第 4/5 层，且保险箱里也没有",
          bool(t1746) and t1746["missing"] == [4, 5] and not t1746["in_safe_only"],
          str(t1746))

    print("\n== auto_sell_spare（关卡卡箱卖多余旧卡腾位）")
    sim = BoxSim(STATE())
    r = await mt.auto_sell_spare(sim, sim.state, keep_free=1)
    check("确实卖掉了一些", len(r["sold"]) >= 5, f"卖了 {len(r['sold'])} 张")
    check("每张都带层/名字/三国币",
          all(s["name"] and s["slot"] and s["content"] for s in r["sold"]),
          str(r["sold"][0] if r["sold"] else {}))
    check("卖完空出格子", r["free_after"] >= 7, f"free_after={r['free_after']}")
    check("参数一律 0_{index}_{card_id}（箱子 0）",
          all(c["cards"].startswith("0_") for c in sim.calls if "cards" in c),
          str([c["cards"] for c in sim.calls if "cards" in c][:2]))
    check("不动保险箱",
          all(not c["cards"].startswith("1_") for c in sim.calls if "cards" in c))

    print("\n== auto_sell_spare 保险箱（箱子 1）")
    sim2 = BoxSim(STATE())
    r2 = await mt.auto_sell_spare(sim2, sim2.state, box="safe", keep_free=1)
    check("保险箱卖掉重复卡", len(r2["sold"]) >= 2, f"卖了 {len(r2['sold'])} 张")
    check("参数一律 1_{index}_{card_id}（箱子 1）",
          all(c["cards"].startswith("1_") for c in sim2.calls if "cards" in c),
          str([c["cards"] for c in sim2.calls if "cards" in c][:2]))
    check("卖完保险箱空出格子", r2["free_after"] >= 2, f"free_after={r2['free_after']}")

    print("\n== 还有空位时不主动卖卡")
    roomy = STATE(ex=[dict(EX[0]), {"card_id": 0, "ts": 0}])
    sim3 = BoxSim(roomy)
    r3 = await mt.auto_sell_spare(sim3, roomy, keep_free=1)
    check("空 13 格 → skipped 且一次都没卖",
          r3.get("skipped") and not r3["sold"]
          and not [c for c in sim3.calls if "cards" in c])

    print("\n== 卡位对不上时 -311，必须记失败并停手")
    sim4 = ErrSim(STATE())
    r4 = await mt.auto_sell_spare(sim4, sim4.state, keep_free=1)
    check("失败被记进 sold 且 failed 非空", bool(r4.get("failed")),
          str(r4.get("failed")))

    print("\n== 一键出战前的腾位仍然只动关卡卡箱")
    st5 = STATE()
    sim5 = BoxSim(st5)
    r5 = await mt.auto_sell_spare(sim5, st5, keep_free=1)
    check("关卡卡箱腾出位置", r5["free_after"] >= 7, f"free_after={r5['free_after']}")
    check("保险箱 12 格纹丝不动",
          sum(1 for x in sim5.state["safe_box_data"] if x["card_id"]) == 12,
          f"保险箱剩 {sum(1 for x in sim5.state['safe_box_data'] if x['card_id'])} 张")

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败：" + "；".join(FAIL))
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
