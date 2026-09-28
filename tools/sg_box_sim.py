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
    check("14 张全是不同的卡 → 一张都不该卖（同层≠同卡，别把收集进度当垃圾）",
          len(p["spare"]) == 0,
          f"spare={len(p['spare'])} "
          f"{[x['name'] for x in p['spare']]}")
    check("每张多余卡都带卡名", all(x["name"] for x in p["spare"]),
          str([(x["card_id"], x["name"]) for x in p["spare"]][:3]))
    layers = sorted({(s["slot"] // 1000) for s in p["slots"]})
    check("关卡卡箱里没有第 4 层", 4 not in layers, f"持有层={layers}")

    print("\n== plan_box 保险箱（safe_box）")
    sp = mt.plan_box(st, "safe")
    check("容量 12 / 已放 12 / 剩 0 格",
          sp["capacity"] == 12 and sp["filled"] == 12 and sp["free"] == 0,
          f"filled={sp['filled']} free={sp['free']}")
    # 第 4 层三张（25113/25114/25115）是**三张不同的卡**，位置值同为 4502：
    # 一张都不能当「同层多余」卖掉 —— 这正是之前判错的地方
    g4 = [s for s in sp["slots"] if s["layer"] == 4]
    check("保险箱第 4 层 3 张分成 3 组、每组都留",
          len(g4) == 3 and all(s["count"] == 1 for s in g4)
          and {s["keep"]["card_id"] for s in g4} == {25113, 25114, 25115},
          str([(s["layer"], s["keep"]["card_id"]) for s in g4]))
    dup = [x["card_id"] for x in sp["spare"]]
    check("真正多余的是 25111、25116 各多的那 1 张（同名卡重复）",
          sorted(dup) == [25111, 25116], str(dup))
    check("保险箱 ts 全是 0 → 不假装「留最新」",
          all(m["ts"] == 0 for m in sp["spare"] + [s["keep"] for s in sp["slots"]]))

    print("\n== plan_sg_boxes（两箱一起看 + 缺层定位）")
    allp = mt.plan_sg_boxes(st)
    check("同时给出两箱", allp["ex"]["box"] == "ex" and allp["safe"]["box"] == "safe")
    t1745 = next((t for t in allp["themes"] if t["theme"] == 1745), None)
    check("1745 两箱合计不缺层", bool(t1745) and t1745["missing"] == [], str(t1745))
    # ⚠ 这里给的都是**游戏层号**（1=boss 层 1 张、5=8 张），与卡库编号相反
    check("但关卡卡箱缺第 2 游戏层，且那一层只躺在保险箱里",
          bool(t1745) and t1745["in_safe_only"] == [2], str(t1745))
    t1746 = next((t for t in allp["themes"] if t["theme"] == 1746), None)
    check("1746 套缺游戏第 1/2 层，且保险箱里也没有",
          bool(t1746) and t1746["missing"] == [1, 2] and not t1746["in_safe_only"],
          str(t1746))

    print("\n== auto_sell_spare（卖**重复**卡腾位；构造 25110 重复的快照）")
    dup_ex = STATE(ex=EX[:-1] + [dict(EX[0])])   # 15 格全满，且 25110 放了两张
    sim = BoxSim(dup_ex)
    r = await mt.auto_sell_spare(sim, sim.state, keep_free=1)
    check("确实卖掉重复的那张", len(r["sold"]) == 1
          and r["sold"][0]["card_id"] == 25110, str(r["sold"]))
    check("每张都带层/名字/三国币",
          all(s["name"] and s["slot"] and s["content"] for s in r["sold"]),
          str(r["sold"][0] if r["sold"] else {}))
    check("卖完空出格子", r["free_after"] >= 1, f"free_after={r['free_after']}")
    check("参数一律 0_{index}_{card_id}（箱子 0）",
          all(c["cards"].startswith("0_") for c in sim.calls if "cards" in c),
          str([c["cards"] for c in sim.calls if "cards" in c][:2]))
    check("不动保险箱",
          all(not c["cards"].startswith("1_") for c in sim.calls if "cards" in c))

    print("\n== auto_sell_spare 保险箱（箱子 1，快照自带 25111/25116 各重复一张）")
    sim2 = BoxSim(STATE())
    r2 = await mt.auto_sell_spare(sim2, sim2.state, box="safe", keep_free=1)
    check("只卖同名重复的 2 张，第 4 层三张一张不碰",
          sorted(s["card_id"] for s in r2["sold"]) == [25111, 25116],
          str([(s["card_id"], s["name"]) for s in r2["sold"]]))
    check("参数一律 1_{index}_{card_id}（箱子 1）",
          all(c["cards"].startswith("1_") for c in sim2.calls if "cards" in c),
          str([c["cards"] for c in sim2.calls if "cards" in c][:2]))
    check("卖完保险箱空出 2 格", r2["free_after"] == 2, f"free_after={r2['free_after']}")

    print("\n== 没有重复卡（哪怕同层挤了 6 张）也不主动卖")
    roomy = STATE(ex=[dict(EX[0]), {"card_id": 0, "ts": 0}])
    sim3 = BoxSim(roomy)
    r3 = await mt.auto_sell_spare(sim3, roomy, keep_free=1)
    check("还有空位 → skipped 且一次都没卖",
          r3.get("skipped") and not r3["sold"]
          and not [c for c in sim3.calls if "cards" in c])

    print("\n== 卡位对不上时 -311，必须记失败并停手")
    dup_err = STATE(ex=EX[:-1] + [dict(EX[0])])
    sim4 = ErrSim(dup_err)
    r4 = await mt.auto_sell_spare(sim4, sim4.state, keep_free=1)
    check("失败被记进 sold 且 failed 非空", bool(r4.get("failed")),
          str(r4.get("failed")))

    print("\n== 一键出战前的腾位：快照里没有重复卡就一张不动")
    st5 = STATE()
    sim5 = BoxSim(st5)
    r5 = await mt.auto_sell_spare(sim5, st5, keep_free=1)
    check("没重复卡 → skipped，卡箱原样", r5.get("skipped")
          and not r5["sold"] and r5["free_after"] == 1,
          f"free_after={r5['free_after']} sold={r5['sold']}")
    check("保险箱 12 格纹丝不动",
          sum(1 for x in sim5.state["safe_box_data"] if x["card_id"]) == 12,
          f"保险箱剩 {sum(1 for x in sim5.state['safe_box_data'] if x['card_id'])} 张")

    print("\n== 层进度：小关 10N = 第 N 层，推荐打卡少的层")
    st6 = STATE()
    p6 = mt.plan_sg_layers(st6)
    lv = {l["layer"]: l for l in p6["levels"]}
    check("算出 1..5 五层", sorted(lv) == [1, 2, 3, 4, 5], str(sorted(lv)))
    # 用户 2026-09-28 纠正：游戏层号是反的 —— 第 1 层＝卡库第 5 层（boss，1 张），
    # 第 5 层＝卡库第 1 层（第一关张曼成那档，8 张/套）。
    check("游戏层号 g 与卡库层号相反",
          all(l["g"] == 6 - l["layer"] for l in p6["levels"])
          and lv[5]["g"] == 1 and lv[1]["g"] == 5,
          str({l["layer"]: l["g"] for l in p6["levels"]}))
    check("游戏第 5 层 = 两套各 8 张（用户说的「第一关掉 8 张」那档）",
          lv[1]["need"] == 16 and lv[5]["need"] == 2,
          str({L: lv[L]["need"] for L in (1, 5)}))
    check("每层都给了对应小关号 10N",
          all(l["levelid"] == 100 + l["layer"] for l in p6["levels"]),
          str({l["layer"]: l["levelid"] for l in p6["levels"]}))
    # 每层两套的张数固定 8/5/4/3/1
    want_need = {1: 16, 2: 10, 3: 8, 4: 6, 5: 2}
    check("每层总需求 = 两套各 8/5/4/3/1",
          all(lv[L]["need"] == want_need[L] for L in want_need),
          str({L: lv[L]["need"] for L in sorted(lv)}))
    check("第 4 层在关卡卡箱里一张都没有（只有保险箱压着那一张）",
          all(r["have_ex"] == 0 for r in lv[4]["by_theme"]),
          str([(r["theme"], r["have_ex"], r["have_safe"]) for r in lv[4]["by_theme"]]))
    # 用户口述「实际我少的是第 4 层的太平道和第 3 层的天下大吉」→ 真值口径。
    # 注意太平道其实是**第 2 层**（card_lib pos=2502），用户只是层号记岔了。
    def miss_of(p, theme, layer):
        return [c["name"] for r in lv[layer]["by_theme"] if r["theme"] == theme
                for c in r["missing"]]
    check("1745 套第 2 层还缺「太平道」（唯一一张）",
          miss_of(p6, 1745, 2) == ["太平道"], str(miss_of(p6, 1745, 2)))
    check("1745 套第 3 层还缺「天下大吉」（唯一一张）",
          miss_of(p6, 1745, 3) == ["天下大吉"], str(miss_of(p6, 1745, 3)))
    check("1745 套第 1/4/5 层都已齐",
          all(not miss_of(p6, 1745, L) for L in (1, 4, 5)),
          str({L: miss_of(p6, 1745, L) for L in (1, 4, 5)}))
    # 同张卡两箱各一份只算一次：层 2 实际 4/5，曾被 25106 双算成「已齐」
    check("层 2 是 4/5 而不是 5/5（同一张卡两箱各一份不能重复计数）",
          [r["have"] for r in lv[2]["by_theme"] if r["theme"] == 1745] == [4],
          str([(r["theme"], r["have"], r["have_ex"], r["have_safe"])
               for r in lv[2]["by_theme"]]))
    # STATE() 没有 levelId，退化为「还差最少、层号最小」的第 2 层
    check("没给当前关时推第 2 层（只差太平道 1 张）",
          bool(p6["recommend"]) and p6["recommend"]["layer"] == 2
          and p6["recommend"]["levelid"] == 102
          and p6["recommend"].get("from_now") is False,
          str(p6["recommend"]))
    check("推荐理由里带游戏层号和缺的卡名",
          "游戏第 4 层" in (p6["recommend"] or {}).get("why", "")
          and "太平道" in (p6["recommend"] or {}).get("why", ""),
          (p6["recommend"] or {}).get("why", ""))
    # 给了当前关 204（= 第 4 层）：那层 1745 已齐，打它纯拿重复卡，
    # 推荐要落在第 2/3 层这种「打了就能补齐」的关上，而不是死守当前关号
    st7 = STATE()
    st7["levelId"] = 204
    p7 = mt.plan_sg_layers(st7)
    check("当前关 204 那层已齐时改推同大关的小关 202（不是死守 204）",
          bool(p7["recommend"]) and p7["recommend"]["layer"] == 2
          and p7["recommend"]["levelid"] == 202
          and p7["recommend"].get("from_now") is False,
          str(p7["recommend"]))
    # open 只按层号升序供展示，挑「打哪个关」由 recommend 按 gap 取最小
    check("open 覆盖所有没齐的层、按层号升序",
          [g["layer"] for g in p6["open"]] == sorted(g["layer"] for g in p6["open"])
          and len(p6["open"]) == sum(1 for L in want_need if lv[L]["have"] != lv[L]["need"]),
          str([(g["layer"], g["gap"]) for g in p6["open"]]))
    check("recommend 的 gap 是 open 里最小的一档",
          (p6["recommend"] or {}).get("gap") ==
          min(g["gap"] for g in p6["open"]),
          f'recommend={p6["recommend"] and p6["recommend"]["gap"]}')
    check("每层 gap 与该层已持有张数一致",
          all(lv[g["layer"]]["need"] - lv[g["layer"]]["have"] == g["gap"]
              for g in p6["open"]),
          str([(g["layer"], g["gap"], lv[g["layer"]]["have"]) for g in p6["open"]]))

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败：" + "；".join(FAIL))
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
