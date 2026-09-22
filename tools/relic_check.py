"""离线验收 `dig_auto.relic_submit_all`（文物一键上交）。

为什么要离线：真机当前**没有**文物库存（鉴定机 4 槽都在鉴定中、id 0..3 全返 -1），
成功路径（code=0 + content）在真机上测不到 → 用 FakeSession 精确构造响应序列。

覆盖：
  ① 成功路径：id0 连交 2 件、id1 交 1 件、id2/3 存在但无库存(-1)、id4/5/6 不存在(0+busy)
     → 断言 total/items/not_exist/tried，且**在连续 3 个不存在处停下**。
  ② 全部无库存（id 0..3 都 -1）→ 断言不误报、把 probe_max 跑完且 not_exist 为空。
  ③ 打乱 id 探针的语义：`-1` = 服务端认得该 id（存在无库存），`0+busy` = 不存在。

跑：/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3 tools/relic_check.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.dig_auto as dg  # noqa: E402


class FakeSession:
    """按 (act, id) 返回预设响应序列；序列用尽后重复最后一个。"""

    def __init__(self, script: dict):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    async def post(self, ep, **params):
        rid = params.get("id")
        self.calls.append((params.get("act"), rid, params.get("num")))
        seq = self.script.get(rid)
        if seq is None:
            return {"data": {"code": 0, "content": None, "desc": "system busy"}}
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"data": dict(r)}


def ok(content):
    return {"code": 0, "content": content, "desc": "sucess"}


ABSENT = {"code": 0, "content": None, "desc": "system busy"}   # 不存在该 id
NOSTOCK = {"code": -1, "content": None, "desc": "system busy"}  # 存在但无库存


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return cond


async def case1():
    print("用例①：成功路径 + 在连续 3 个不存在处停止")
    script = {
        0: [ok("69_8396_1"), ok("69_8397_1"), NOSTOCK],
        1: [ok("69_8448_1"), NOSTOCK],
        2: [NOSTOCK],
        3: [NOSTOCK],
        4: [ABSENT], 5: [ABSENT], 6: [ABSENT],
        7: [ABSENT],  # 不应该被请求到
    }
    s = FakeSession(script)
    r = await dg.relic_submit_all(s)
    allok = True
    allok &= check("total == 3", r["total"] == 3, f"total={r['total']}")
    allok &= check("items 两条(id0×2, id1×1)",
                   [(x["id"], x["num"]) for x in r["items"]] == [(0, 2), (1, 1)],
                   str([(x["id"], x["num"]) for x in r["items"]]))
    allok &= check("not_exist == [4,5,6]", r["not_exist"] == [4, 5, 6], str(r["not_exist"]))
    allok &= check("tried == 7（连续 3 个不存在即停）", r["tried"] == 7, f"tried={r['tried']}")
    allok &= check("id7 未被请求", all(c[1] != 7 for c in s.calls), str([c[1] for c in s.calls]))
    allok &= check("id0 的 text 带卡名(螣蛇)", "螣蛇" in (r["items"][0]["text"] if r["items"] else ""),
                   r["items"][0]["text"] if r["items"] else "")
    return allok


async def case2():
    print("用例②：全无库存（0..3 都 -1）→ 不误报、跑满 probe_max、not_exist 为空")
    script = {i: [NOSTOCK] for i in range(0, 40)}
    s = FakeSession(script)
    r = await dg.relic_submit_all(s, probe_max=6)
    allok = True
    allok &= check("total == 0", r["total"] == 0, f"total={r['total']}")
    allok &= check("items 空", r["items"] == [], str(r["items"]))
    allok &= check("not_exist 空（全都认得）", r["not_exist"] == [], str(r["not_exist"]))
    allok &= check("tried == 6（跑满 probe_max，因没有 gap）", r["tried"] == 6, f"tried={r['tried']}")
    return allok


async def case3():
    print("用例③：指定 ids 时不启用 gap 停止")
    script = {0: [NOSTOCK], 1: [NOSTOCK], 2: [NOSTOCK], 3: [NOSTOCK]}
    s = FakeSession(script)
    r = await dg.relic_submit_all(s, ids=[0, 1, 2, 3])
    allok = True
    allok &= check("tried == 4（显式 ids 全跑）", r["tried"] == 4, f"tried={r['tried']}")
    allok &= check("total == 0", r["total"] == 0, f"total={r['total']}")
    return allok


async def main():
    dg._MIN_INTERVAL = 0.0          # 关掉节流，让离线测试秒级跑完
    results = [await case1(), await case2(), await case3()]
    print("\n" + ("全部 PASS ✓" if all(results) else "有 FAIL ✗"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
