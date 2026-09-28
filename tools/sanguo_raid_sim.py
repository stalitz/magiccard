"""离线模拟：三国「一键出战」连打到次数用尽。

真机今天已打满（act=1 freenum=3），不适合真跑循环，所以全部在**假 session** 上跑
**真实 `match.run_sanguo_raid` 代码路径**，不发起任何真实闯关请求、不烧次数。

用法：
    /Users/stalitz/.workbuddy/binaries/python/envs/default/bin/python tools/sanguo_raid_sim.py
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
    (PASS if cond else FAIL).append(name + (f"｜{extra}" if extra else ""))
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))


ELVES = [{"id": 3, "equip_list": [10413, 20463, 30753, 40353]},
         {"id": 4, "equip_list": [10433, 20473, 30122, 40111]},
         {"id": 2, "equip_list": [10392, 20522, 30403, 40253]},
         {"id": 1, "equip_list": [10562, 20362, 30352, 40192]}]


class SGSim:
    """假 session：act=1 给 freenum（每次成功 act=11 后 +1），act=16 给固定阵容。"""

    def __init__(self, freenum: int = 0, fail_at: int | None = None):
        self.uin = "0004821992"          # _plain_uin 会读它（真机也是补零格式）
        self.freenum = freenum
        self.fail_at = fail_at
        self.calls: list[tuple] = []
        self.submits: list[dict] = []

    async def post(self, ep, act=None, **kw):  # noqa: ARG002
        self.calls.append(("act", act, kw))
        if act == 1:
            return {"code": 0, "data": {"freenum": self.freenum, "levelId": 204,
                                        "logData": [], "buynum": 0}}
        if act == 16:
            return {"code": 0, "data": {"code": 0, "elf_list": ELVES}}
        if act == 11:
            n = len(self.submits) + 1
            if self.fail_at is not None and n == self.fail_at:
                return {"code": -1, "data": {"code": -1, "desc": "次数不足"}}
            self.freenum += 1
            self.submits.append(kw)
            return {"code": 0, "data": {"code": 0, "base_score": 4800,
                                        "battle_data": {"attack_accounts": {
                                            "iswin": 1, "total_score": 5000 + n}},
                                        "boss_reduce_hp": 1200, "percent": 80,
                                        "content": "48_1614_10"}}
        return {"code": 0, "data": {}}


def count(calls, act_no):
    return sum(1 for c in calls if c[1] == act_no)


async def main() -> None:
    # 1) 今天一次没打：应连打 3 场，freenum 0→3
    s = SGSim(0)
    r = await mt.run_sanguo_raid(s, feed=False)
    check("打满：出战 3 场", r.get("played") == 3, f"played={r.get('played')}")
    check("打满：used_after=3", r.get("used_after") == 3, str(r.get("used_after")))
    check("打满：每场前都重读阵容 act=16", count(s.calls, 16) == 3, f"act16×{count(s.calls, 16)}")
    check("打满：提交了 3 次 act=11", count(s.calls, 11) == 3, f"act11×{count(s.calls, 11)}")
    eqs = s.submits[0].get("eqs") if s.submits else ""
    cats = s.submits[0].get("cats") if s.submits else ""
    check("参数：eqs=16 件平铺且尾随 |", eqs == "10413|20463|30753|40353|10433|20473|30122|" \
          "40111|10392|20522|30403|40253|10562|20362|30352|40192|", str(eqs)[:60])
    check("参数：cats=4 只猫且尾随 |", cats == "3|4|2|1|", str(cats))
    check("参数：levelid 取 act=1 真实关卡 204",
          all(x.get("levelid") == 204 for x in s.submits), str(s.submits[0].get("levelid")))
    txt = (r.get("rounds") or [{}])[0].get("text", "")
    check("结果文案含胜负/总分/boss 掉血", "胜" in txt and "总分" in txt and "boss 掉血" in txt, txt)

    # 2) 今日已打满：一次都不该发写请求
    s2 = SGSim(3)
    r2 = await mt.run_sanguo_raid(s2, feed=False)
    check("已打满 → skipped", bool(r2.get("skipped")) and r2.get("playe" "d", 0) == 0 and
          count(s2.calls, 11) == 0, f"skipped={r2.get('skipped')} act11×{count(s2.calls, 11)}")

    # 3) 今日已打 1 次：只补 2 场
    s3 = SGSim(1)
    r3 = await mt.run_sanguo_raid(s3, feed=False)
    check("已打 1 次 → 再打 2 场", r3.get("played") == 2 and count(s3.calls, 11) == 2,
          f"played={r3.get('played')}")

    # 4) 第 2 场服务端拒绝：立即停，不继续烧次数
    s4 = SGSim(0, fail_at=2)
    r4 = await mt.run_sanguo_raid(s4, feed=False)
    check("第 2 场失败即停", count(s4.calls, 11) == 2 and r4.get("played") == 1,
          f"act11×{count(s4.calls, 11)} played={r4.get('played')}")
    last = (r4.get("rounds") or [{}])[-1]
    check("失败原因落进 rounds（前端展示的是 text）",
          last.get("ok") is False and "次数不足" in str(last.get("text") or last.get("msg")),
          str(last))

    # 5) 指定 levelid 时用前端传的值
    s5 = SGSim(0)
    await mt.run_sanguo_raid(s5, levelid=102, feed=False)
    check("指定关卡 levelid=102", all(x.get("levelid") == 102 for x in s5.submits), "")

    print(f"\nPASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
