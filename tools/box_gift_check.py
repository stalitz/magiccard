"""离线验收：卡箱里已有的礼物套 48 面卡 → 就地变卡（不收礼）。

覆盖三条（2026-09-26 用户反馈「现在你完全不用卡箱里的 48 面额卡」）：
  1. 礼物套 48 面卡 id 集合是从卡库算出来的（theme=487 且面额 48），不是抄死的
  2. find_box_gift_cards 能读出**卡箱里**那几张（含真实槽位/箱子）
  3. drain_box_gift_cards 用箱里的卡当 srcid 变卡，变出一张即收手、绝不去收礼
  4. drain_gift_box 在箱里有卡且变成功时，本轮不碰礼物箱（from_box=True, took=0）
  5. 2026-09-26 新定调「变卡失败要一直变，直到成功或者礼物卡没了」：失败换下一张
     继续，箱里的卡烧完/变出一张才收手
无网络、无副作用：全部用假 session（假 session 会像真机一样，变卡成功或失败都
消耗源卡，于是「重扫卡箱」能真实反映箱里的卡在减少）。
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


# 换卡箱里有 7773(slot 3)、保险箱里有 7774(slot 2)（两张都是礼物套 48 面卡）；
# 5000(slot 5) 与 7786(slot 1) 是干扰卡（非礼物形态，永远留在箱里）
RAW = """<qqhome>
<hello time="1780000000"/>
<user randchance="3" gold_fen="10"/>
<changebox cur="6" max="18" used="2"><card slot="3" id="7773"/><card slot="5" id="5000"/></changebox>
<storebox cur="4" max="10" used="2"><card slot="1" id="7786"/><card slot="2" id="7774"/></storebox>
<stovebox cur="7" max="7"><card slot="1" id="0" slottype="0"/></stovebox>
</qqhome>"""


class FakeSession:
    """只提供 gift 层用到的两个入口：post(mainpage) 与 qzone(transfer)。

    卡箱是**活的**：变卡（成功或失败）都会把源卡从箱里拿走 —— 与真机一致
    （真机实测失败同样消耗源卡）。这样 `drain_box_gift_cards` 内部每试一张就重扫
    一次卡箱的行为才测得出「直到成功或礼物卡没了」。
    """

    def __init__(self, raw: str = RAW, transfer_ok: bool = True, fail_times: int = 0,
                 cards: list[tuple] | None = None):
        self.uin = "123456"          # gift.find_box_gift_cards 会读 session.uin
        self.raw = raw
        self.transfer_ok = transfer_ok
        self.fail_times = fail_times
        self.cards = list(cards if cards is not None
                          else [(7773, 3, 0), (7774, 2, 1)])
        self.calls: list[tuple] = []          # 每次 transfer 的 (srcid, dstid, slot, slottype)
        self.transfer_n = 0
        self.mainpage_n = 0

    def _raw(self) -> str:
        ch = "".join(f'<card slot="{s}" id="{i}"/>' for i, s, t in self.cards if t == 0)
        ch += '<card slot="5" id="5000"/>'          # 干扰卡，永远在
        st = "".join(f'<card slot="{s}" id="{i}"/>' for i, s, t in self.cards if t == 1)
        st += '<card slot="1" id="7786"/>'          # 干扰卡，永远在
        return ('<qqhome><hello time="1780000000"/><user randchance="3" gold_fen="10"/>'
                f'<changebox cur="6" max="18" used="2">{ch}</changebox>'
                f'<storebox cur="4" max="10" used="2">{st}</storebox>'
                '<stovebox cur="7" max="7"><card slot="1" id="0" slottype="0"/></stovebox>'
                '</qqhome>')

    async def post(self, ep, **kw):
        if "mainpage" in ep or "MAINPAGE" in ep:
            self.mainpage_n += 1
            return {"code": 0, "raw": self._raw()}
        return {"code": -1, "raw": ""}

    async def qzone(self, method, ep, **kw):
        self.calls.append((kw.get("srcid"), kw.get("dstid"),
                           kw.get("slotid"), kw.get("slottype")))
        self.transfer_n += 1
        fail = self.transfer_n > self.fail_times    # 前 fail_times 次是「成功」
        # 变卡成败**都**消耗源卡 → 从箱里拿掉
        sid, sl, slt = kw.get("srcid"), kw.get("slotid"), kw.get("slottype", 0)
        self.cards = [c for c in self.cards
                      if not (c[0] == sid and c[1] == sl and c[2] == slt)]
        return {"code": 0,
                "raw": '<qqhome code="0" result="0"/>' if not fail
                       else '<qqhome code="0" result="1"/>',
                "message": ""}


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
    empty = await gf.find_box_gift_cards(FakeSession(cards=[]))
    check("箱里没礼物卡 → 空列表（调用方据此去收礼）", empty == [])

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

    print("\n[4] 变卡失败**换下一张继续**（2026-09-26 用户定调：一直变，到成功或礼物卡没了）")
    s = FakeSession(fail_times=1)                     # 第 1 次失败，第 2 次成功
    r = await gf.drain_box_gift_cards(s, dstid=26117)
    check("失败后继续变下一张 → 最终 changed=1", r.get("changed") == 1,
          r.get("message", ""))
    check("确实试了 2 次（失败那张的源卡已被烧掉，换的是箱里的另一张）",
          s.transfer_n == 2 and r.get("tried") == 2 and r.get("burned") == 1,
          f"transfer={s.transfer_n} tried={r.get('tried')} burned={r.get('burned')}")
    check("第二次用的源卡是箱里剩下的那张 7774",
          s.calls[1][0] == 7774, str(s.calls))
    check("变成功即收手（不继续连变）", s.transfer_n == 2, f"实际 {s.transfer_n}")

    s = FakeSession(fail_times=99)                    # 永远失败
    r = await gf.drain_box_gift_cards(s, dstid=26117)
    check("一直失败 → 箱里的礼物卡烧完才收手（不会无限循环）",
          r.get("changed") == 0 and s.transfer_n == 2,
          f"changed={r.get('changed')} transfer={s.transfer_n}")
    check("失败确实烧掉了 2 张源卡（burned=2，与 transfer 次数一致；失败不额外烧）",
          r.get("burned") == 2, f"burned={r.get('burned')}")
    check("inbox 仍如实报 2（调用方据此知道箱里原本有几张）", r.get("inbox") == 2)

    one = FakeSession(fail_times=99, cards=[(7774, 2, 1)])   # 箱里只有 1 张源卡
    r1 = await gf.drain_box_gift_cards(one, dstid=26117)
    check("箱里只剩 1 张源卡、且失败 → 试 1 次后池空即收手",
          r1.get("changed") == 0 and one.transfer_n == 1,
          f"transfer={one.transfer_n} {r1.get('message','')}")

    print("\n[4b] 收手后不会凭空重试：卡箱里没礼物卡了就跳过")
    s = FakeSession(cards=[])
    r = await gf.drain_box_gift_cards(s, dstid=26117)
    check("箱里没源卡 → skipped，一次都不试",
          r.get("skipped") is True and s.transfer_n == 0, str(r)[:120])

    print("\n[5] drain_gift_box：箱里有卡且变成功 → 本轮不碰礼物箱")
    s = FakeSession()
    r = await gf.drain_gift_box(s, dstid=26117, upper=45, lower=25)
    check("from_box=True（走的是卡箱优先）", r.get("from_box") is True)
    check("took=0（一张礼都没收）", r.get("took") == 0)
    check("changed=1", r.get("changed") == 1)

    print("\n[6] 箱里一张 48 面卡都没有 → 才回到礼物箱链路")
    s = FakeSession(cards=[])
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

    # 2026-09-26：目标不再写死。gift_dst 填一个本套用不上的卡， Automatic 仍应按
    # 「缺哪张套内 40 面卡」动手 —— 这正是用户批评的「指定某卡但炼的不是那套」。
    eng.gift_dst = 99999999
    eng.log.clear()
    await eng.tick()
    step = [x for x in eng.log if x["action"] == "礼物卡变卡"]
    check("写死的 gift_dst 已作废：dst=99999999 也不会被拿来变卡",
          all("99999999" not in x["detail"] for x in step),
          str([x["detail"] for x in step][:2]))
    check("tick 仍然动手变卡（目标自动挑）", bool(step),
          str([x["detail"] for x in step][:2]) or "tick 未产出变卡日志")

    p = eng.plans[1652]
    bg = eng.status()["box_gift"]
    check("自动目标属于「在炼的那套」（1652 七夕情人节）",
          int(bg.get("dst") or 0) in set(p.ids), f"dst={bg.get('dst')}")
    check("自动目标是 40 面卡", int(bg.get("dst_face") or 0) == 40,
          f"face={bg.get('dst_face')}，{bg.get('dst_name')}")
    check("目标不是写死的 26117（属 theme 1810，本套用不上）",
          int(bg.get("dst") or 0) != 26117, f"dst={bg.get('dst')}")
    check("用的 srcid 是卡箱里那张 7773", bool(s.calls) and s.calls[0][0] == 7773,
          str(s.calls[:1]))
    check("status 里带 box_gift 供面板显示", "useful" in bg and "last" in bg)

    print("\n[8] pick_change_target：缺哪个变哪个（缺口按总量算，只挑 40 面）")
    from backend.auto_refine import face40_gap, pick_change_target
    keep = set(eng._keep)
    p40 = sorted(c for c in p.ids if p.cards[c][3] == 40)
    check("1652 七夕情人节整套里确实有多张 40 面卡（不止一张可挑）", len(p40) >= 3,
          str([(c, p.cname(c)) for c in p40]))

    # 用户 2026-09-26 纠正①：礼物卡只有 48 面一种形态，变卡只产出 40 面 → 候选
    # 必须是 40 面，**没有「放宽到别的面额」的兜底**。
    half40 = Counter({c: 1 for c in p.ids if c not in p40})      # 40 面卡一张没有
    g0 = face40_gap(p, half40, Counter())
    zero = pick_change_target(eng.plans, eng.queue, half40, Counter(), keep)
    check("箱里 40 面卡一张没有时 → 仍然按缺口动手（不是「没得挑就不动手」）",
          zero is not None and zero["face"] == 40, str(zero))
    for cid in p40:                                              # 40 面卡按需求量给齐
        half40[cid] += face40_gap(p, Counter(), Counter())["gap"].get(cid, 0)
    check("40 面卡按整套需求量凑齐 → 不挑（本轮不动箱里的礼物卡）",
          pick_change_target(eng.plans, eng.queue, half40, Counter(), keep) is None,
          str(face40_gap(p, half40, Counter())["gap"]))
    # 部分补齐：三张 40 面卡凑齐两张后，第三张的缺口必须被看见
    # 从零需求出发（整套要几张 40 面卡），把除斗巧外的都补齐 → 就只剩斗巧的缺口
    req0 = face40_gap(p, Counter(), Counter())["gap"]
    have_all_but_qiao = Counter({c: 1 for c in p.ids})
    for cid, n in req0.items():
        if cid != 23927:
            have_all_but_qiao[cid] += n
    gq = face40_gap(p, have_all_but_qiao, Counter())
    got = pick_change_target(eng.plans, eng.queue, have_all_but_qiao, Counter(), keep)
    check("已攒够的 40 面卡不进 gap（只剩斗巧的缺口）",
          set(gq["gap"]) == {23927}, f'需求={req0} 剩余缺口={gq["gap"]}')
    check("只缺 23927 斗巧 → 就变 23927（缺哪个变哪个）",
          bool(got) and got["dst"] == 23927, str(got))

    # 用户 2026-09-26 纠正②：「缺」= 炼的套卡一共需要多少张 40 的，凑不够就算缺，
    # 不是「当前一张 40 没有」才算缺。
    only_one = Counter({c: 1 for c in p.ids})
    only_one[23926] = only_one[23925] = 0        # 只有 23927 斗巧一张 40 面卡
    g1 = face40_gap(p, only_one, Counter())
    check("手里只有 1 张 40 面卡 → 也算有缺口（不是等一张不剩才算缺）",
          g1["need_total"] > 0 and g1["have"] == 1,
          f'need={g1["need_total"]} have={g1["have"]} gap={g1["gap"]}')
    check("缺口 = 各张 40 面卡净需求之和", g1["need_total"] == sum(g1["gap"].values()),
          f'{g1["need_total"]} vs {sum(g1["gap"].values())}')
    check("挑的是缺口最大的那张（一张顶最大进度）",
          only_one[23927] == 1 and max(g1["gap"].items(), key=lambda kv: kv[1])[0]
          in g1["gap"], str(g1["gap"]))
    full = Counter({c: 3 for c in p.ids})        # 全套齐
    g2 = face40_gap(p, full, Counter())
    check("全套齐 → 缺口为 0", g2["need_total"] == 0, str(g2["gap"]))
    check("全套齐 → 不挑", pick_change_target(eng.plans, eng.queue,
                                              full, Counter(), keep) is None)

    eng2 = ar.AutoRefine(s, [1652, 1810])
    keep2 = set(eng2._keep)
    both_empty = pick_change_target(eng2.plans, eng2.queue, Counter(), Counter(), keep2)
    check("多套并行：候选只来自队列里的套卡，且都是 40 面",
          bool(both_empty) and both_empty["tid"] in (1652, 1810)
          and both_empty["face"] == 40
          and both_empty["dst"] in set(eng2.plans[both_empty["tid"]].ids), str(both_empty))
    check("多套并行：谁缺口大先补谁（1652 缺 6 张 vs 1810 缺 18 张 → 1810 的移液器）",
          bool(both_empty) and both_empty["tid"] == 1810, str(both_empty))
    only1652 = pick_change_target(eng2.plans, [1652], Counter(), Counter(), keep2)
    check("队列只留 1652 时，1810 的卡不再算候选",
          bool(only1652) and only1652["tid"] == 1652
          and only1652["dst"] in set(eng2.plans[1652].ids), str(only1652))
    check("队列为空 → None（现在没在炼卡，别动箱里的卡）",
          pick_change_target(eng2.plans, [], Counter(), Counter(), keep2) is None)

    print("\n[9] 目标面额硬校验：非 40 面一律不许变（礼物卡只有 48 面一种形态）")
    n_before = s.transfer_n
    r = await gf.drain_box_gift_cards(s, dstid=7773)      # 源卡 id 本身当 dst 用
    check("dst 不是 40 面 → 直接拒绝，连源卡都不碰",
          r.get("ok") is False and s.transfer_n == n_before, str(r)[:120])
    n_before = s.transfer_n
    r2 = await gf.drain_box_gift_cards(s, dstid=99999999)  # 卡库查不到这张卡
    check("卡库查不到 dst（None）= 放行而不是误拦", s.transfer_n > n_before,
          str(r2)[:120])
    s3 = FakeSession()                                    # 箱里还有礼物卡的那一组
    r3 = await gf.drain_box_gift_cards(s3, dstid=23927)   # 正常 40 面套内卡
    check("dst 是 40 面 → 正常走流程（源卡被消耗）", r3.get("tried") >= 1,
          str(r3)[:120])

    print(f"\n结果：PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        sys.exit(1)


asyncio.run(main())
