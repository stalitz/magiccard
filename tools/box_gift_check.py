"""离线验收：卡箱里已有的礼物套 48 面卡 → 就地变卡（不收礼）。

覆盖三条（2026-09-26 用户反馈「现在你完全不用卡箱里的 48 面额卡」）：
  1. 礼物套 48 面卡 id 集合是从卡库算出来的（theme=487 且面额 48），不是抄死的
  2. find_box_gift_cards 能读出**卡箱里**那几张（含真实槽位/箱子）
  3. drain_box_gift_cards 用箱里的卡当 srcid 变卡，变出一张即收手、绝不去收礼
  4. drain_gift_box 在箱里有卡且变成功时，本轮不碰礼物箱（from_box=True, took=0）
无网络、无副作用：全部用假 session。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import gift as gf  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name + (f"｜{extra}" if extra else ""))
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))


# mainpage 样本：换卡箱里有 7773(slot 3)、保险箱里有 7774(slot 2) 与 5000(slot 1)
RAW = """<qqhome>
<hello time="1780000000"/>
<user randchance="3" gold_fen="10"/>
<changebox cur="6" max="18" used="2"><card slot="3" id="7773"/><card slot="5" id="5000"/></changebox>
<storebox cur="4" max="10" used="2"><card slot="1" id="7786"/><card slot="2" id="7774"/></storebox>
<stovebox cur="7" max="7"><card slot="1" id="0" slottype="0"/></stovebox>
</qqhome>"""


class FakeSession:
    """只提供 gift 层用到的两个入口：post(mainpage) 与 qzone(transfer)。"""

    def __init__(self, raw: str = RAW, transfer_ok: bool = True, fail_times: int = 0):
        self.uin = "123456"          # gift.find_box_gift_cards 会读 session.uin
        self.raw = raw
        self.transfer_ok = transfer_ok
        self.fail_times = fail_times
        self.calls: list[tuple] = []          # 每次 transfer 的 (srcid, dstid, slot, slottype)
        self.transfer_n = 0
        self.mainpage_n = 0

    async def post(self, ep, **kw):
        if "mainpage" in ep or "MAINPAGE" in ep:
            self.mainpage_n += 1
            return {"code": 0, "raw": self.raw}
        return {"code": -1, "raw": ""}

    async def qzone(self, method, ep, **kw):
        self.calls.append((kw.get("srcid"), kw.get("dstid"),
                           kw.get("slotid"), kw.get("slottype")))
        self.transfer_n += 1
        if self.transfer_n <= self.fail_times:
            return {"code": 0, "raw": '<qqhome code="0" result="0"/>', "message": ""}
        return {"code": 0, "raw": '<qqhome code="0" result="1"/>', "message": ""}


async def main() -> None:
    print("\n[1] 礼物套 48 面卡集合（从卡库算）")
    ids = gf._face48_ids()
    check("theme=487 里面额 48 的卡 = 7771..7776",
          ids == {7771, 7772, 7773, 7774, 7775, 7776}, str(sorted(ids)))
    check("187/612 面的织锦（7777+）不在集合内",
          not any(i >= 7777 for i in ids))

    print("\n[2] find_box_gift_cards 读卡箱里的礼物卡（含真实槽位）")
    pool = await gf.find_box_gift_cards(FakeSession())
    found = {(p["id"], p["slot"], p["slottype"]) for p in pool}
    check("读出箱里两张 48 面礼物卡",
          found == {(7773, 3, 0), (7774, 2, 1)}, str(sorted(found)))
    check("612 面的 7786 不混入（不是礼物形态）",
          all(p["id"] != 7786 for p in pool))
    empty = await gf.find_box_gift_cards(FakeSession(raw="<qqhome></qqhome>"))
    check("空响应 → 空列表（调用方据此去收礼）", empty == [])

    print("\n[3] drain_box_gift_cards：用箱里的卡变卡，变出一张即收手")
    s = FakeSession()
    r = await gf.drain_box_gift_cards(s, dstid=26117)
    check("变成功 changed=1", r.get("changed") == 1, r.get("message", ""))
    check("只发 1 次变卡请求（不连烧源卡）", s.transfer_n == 1, f"实际 {s.transfer_n}")
    check("srcid 用的是**卡箱里**那张 7773，不是收礼产出的新 id",
          s.calls[0][0] == 7773, str(s.calls))
    check("带上了真实槽位 slotid=3/slottype=0",
          s.calls[0][2:] == (3, 0), str(s.calls[0]))
    check("skipped=False（本轮确实干了活）", r.get("skipped") is False)

    print("\n[4] 变卡失败**不连烧源卡**（真机实测：失败也消耗源卡，8 张→3 次尝试剩 5 张）")
    s = FakeSession(fail_times=4)
    r = await gf.drain_box_gift_cards(s, dstid=26117)
    check("changed=0", r.get("changed") == 0, r.get("message", ""))
    check("一次只试一张就收手（transfer 次数 = 1）",
          s.transfer_n == 1, f"实际 {s.transfer_n}")
    check("inbox 仍如实报 2（调用方据此知道箱里还有卡）", r.get("inbox") == 2)

    print("\n[5] drain_gift_box：箱里有卡且变成功 → 本轮不碰礼物箱")
    s = FakeSession()
    r = await gf.drain_gift_box(s, dstid=26117, upper=45, lower=25)
    check("from_box=True（走的是卡箱优先）", r.get("from_box") is True)
    check("took=0（一张礼都没收）", r.get("took") == 0)
    check("changed=1", r.get("changed") == 1)

    print("\n[6] 箱里一张 48 面卡都没有 → 才回到礼物箱链路")
    raw_no_gift = (RAW.replace('<card slot="3" id="7773"/>', "")
                      .replace('<card slot="2" id="7774"/>', ""))
    s = FakeSession(raw=raw_no_gift)
    r = await gf.drain_gift_box(s, dstid=26117, upper=45, lower=25)
    check("没有 from_box 标记", r.get("from_box") is not True)
    check("skipped=True（礼物箱没到阈值时原样跳过）", r.get("skipped") is True)

    print("\n[7] 引擎 tick 里「卡箱优先变卡」这一步真的会被调到")
    from backend import auto_refine as ar
    from collections import Counter
    s = FakeSession()
    eng = ar.AutoRefine(s, [1652])
    st = ar.parse_full(RAW)

    async def fake_read():
        return st, Counter(), {}, Counter()

    async def fake_refine(*a, **k):
        return None

    eng._read_state = fake_read
    eng._refine_all = fake_refine

    eng.gift_dst = 99999999                  # 用不上的卡 → 不该动箱里的礼物卡
    await eng.tick()
    check("dst 用不上时不动手（拿礼物卡换废卡不划算）",
          not any(x["action"] == "礼物卡变卡" for x in eng.log)
          and s.transfer_n == 0, f"transfer {s.transfer_n} 次")

    p = eng.plans[1652]
    eng.gift_dst = next(iter(p.depth))       # 换成这一套真正用得上的卡
    await eng.tick()
    step = [x for x in eng.log if x["action"] == "礼物卡变卡"]
    check("dst 用得上时 tick 产出去变卡", bool(step),
          str([x["detail"] for x in eng.log][:4]))
    check("用的 srcid 是卡箱里那张 7773", bool(s.calls) and s.calls[0][0] == 7773,
          str(s.calls[:1]))
    bg = eng.status()["box_gift"]
    check("status 里带 box_gift 供面板显示", "useful" in bg and "last" in bg)

    print(f"\n结果：PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        sys.exit(1)


asyncio.run(main())
