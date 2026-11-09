"""回归：一键日常「换卡箱有 10 面卡却投卡 0 炉」（2026-11-09 修复）。

纯离线 stub，零真机请求。用真实 10 面卡 id（13841/13844/13845…）保证
_face() 走真卡库能识别成 10 面。覆盖：
  A 箱内有卡 + 抽卡机会用尽 → 投箱内现成卡、绝不买卡
  B 箱内无卡 + 抽卡机会用尽 → 保留旧行为（走商店买路径）
  C 读卡箱失败（-3001/空壳）→ 显式报错，不发任何写请求
  D 有卡有空炉但 act=2 全被拒 → 结果文案带「疑似真机繁忙」，不再伪装成绿字 0 投
  E 抽卡成功 → 抽卡阶段与箱内现成卡都投，总数为两者之和
"""
import asyncio
import sys

from backend import zcg

CIDS = (13841, 13844, 13845, 13910, 5251, 6173)
FAIL = []
PASS = []


def ck(ok: bool, name: str, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS " if ok else "  FAIL ") + name + (f"  {extra}" if extra else ""),
          file=sys.stderr, flush=True)


class FakeSession:
    def __init__(self, cards=(), box_error=None, fail_deposit=False,
                 draw_cards=0):
        self.cards = [{"id": c, "slot": i} for i, c in enumerate(cards)]
        self.puzi = {1: 0, 2: 0, 3: 0}
        self.box_error = box_error
        self.fail_deposit = fail_deposit
        self.draw_cards = draw_cards
        self.act2 = 0
        self.buy_calls = 0

    async def post(self, endpoint, **params):
        if params.get("act") == 2:
            self.act2 += 1
            if not self.fail_deposit:
                cid, pid = int(params["card_id"]), int(params["puzi_id"])
                self.puzi[pid] = cid
                self.cards = [c for c in self.cards if c["id"] != cid]
            return {"code": 0}
        return {"code": 0}

    async def draw_random_cards(self, fetch_type=2):
        if self.draw_cards > 0:
            n = self.draw_cards
            self.draw_cards = 0
            new = CIDS[10:10 + n] or (25647, 2116, 13807)
            for c in new[:n]:
                self.cards.append({"id": c, "slot": 20 + len(self.cards)})
            return {"n_cards": n, "remain": 0, "code": 0}
        return {"n_cards": 0, "remain": 0, "code": 0}

    async def move_card(self, cid, slot, from_box=0):
        return {"ok": True, "dest_slot": 0}


async def fake_get_zcg_state(session):
    return {"ok": True, "code": 0, "mojing": {}, "jf": 0, "jlz": 0,
            "kongquesi": 0, "yueguangshi": 0,
            "puzi": [{"id": k, "card_id": v, "name": "", "begin": 0,
                      "smelt_time": 0, "lv": 0, "finished": False,
                      "state": "空" if v == 0 else "炼制中"}
                     for k, v in session.puzi.items()]}


async def fake_box_ten_cards(session):
    return [(c["id"], c["slot"], 0) for c in session.cards]


async def fake_box_state(session):
    if session.box_error:
        return {"change": [], "store": [], "cap": {}, "error": session.box_error}
    return {"change": list(session.cards), "store": [],
            "cap": {"change": {"cur": 18, "max": 18}}, "randchance": 0}


async def fake_deposit_from_buy(session, puzi_id, steps):
    session.buy_calls += 1
    steps.append({"act": "buy", "text": "(stub 不真买)"})
    return False


_ORIG = {n: getattr(zcg, n) for n in
         ("get_zcg_state", "_box_ten_cards", "_box_state", "_deposit_from_buy")}


def patch():
    zcg.get_zcg_state = fake_get_zcg_state
    zcg._box_ten_cards = fake_box_ten_cards
    zcg._box_state = fake_box_state
    zcg._deposit_from_buy = fake_deposit_from_buy


def restore():
    for n, f in _ORIG.items():
        setattr(zcg, n, f)


async def main():
    patch()
    try:
        # A: 箱里 3 张 10 面卡、3 空炉、抽卡机会用尽
        s = FakeSession(CIDS[:3])
        r = await zcg.run_zcg(s)
        ck(r["ok"] and r["deposited"] == 3 and r["box_deposited"] == 3,
           "A 箱内现成 10 面卡全投进空炉", str({k: r.get(k) for k in
           ("ok", "deposited", "box_deposited", "drawn_deposited", "bought")}))
        ck(s.buy_calls == 0, "A 箱内还有卡时绝不花钱买卡", f"buy_calls={s.buy_calls}")
        ck(all(s.puzi.values()) and not s.cards, "A 3 炉全占用且箱内卡被消耗")
        ck("箱内现成 3" in r["text"], "A 文案区分箱内现成卡", r["text"])

        # B: 箱里无 10 面卡 → 仍走旧「商店买」路径，不回归
        s = FakeSession(())
        r = await zcg.run_zcg(s)
        ck(r["ok"] and r["deposited"] == 0 and s.buy_calls == 3,
           "B 箱内无卡时保留商店买路径", f"buy_calls={s.buy_calls}")

        # C: 读卡箱失败 → 显式报错，且零写请求
        s = FakeSession(CIDS[:3], box_error="code=-3001 系统繁忙")
        r = await zcg.run_zcg(s)
        ck((not r["ok"]) and "读取卡箱失败" in r["text"],
           "C 读箱失败显式回报", r["text"])
        ck(s.act2 == 0 and s.buy_calls == 0,
           "C 读箱失败时零写请求", f"act2={s.act2} buy={s.buy_calls}")

        # D: 有卡有空炉但 act=2 全被真机拒 → 不再显示成绿字 0 投
        s = FakeSession(CIDS[:3], fail_deposit=True)
        r = await zcg.run_zcg(s)
        ck(r["deposited"] == 0 and "疑似真机繁忙" in r["text"] and
           "10 面卡" in r["text"], "D 有卡却 0 投时给出可读提示", r["text"])

        # E: 抽卡成功 → 抽卡阶段内部的 _deposit_all_from_box 已把箱内卡（含原有卡）
        #    一并投完，2.5 只是补位、不会重复投；总投卡数应为 3、炉全满。
        s = FakeSession(CIDS[:2], draw_cards=1)
        r = await zcg.run_zcg(s)
        ck(r["deposited"] == 3 and all(s.puzi.values()) and not s.cards,
           "E 抽卡成功时箱内卡一并投完、不重复计数",
           str({k: r.get(k) for k in ("deposited", "drawn_deposited",
                                      "box_deposited", "bought")}))
        ck(s.buy_calls == 0, "E 炉被填满后不买卡", f"buy_calls={s.buy_calls}")
    finally:
        restore()

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL", file=sys.stderr)
    if FAIL:
        print("失败项：" + "；".join(FAIL), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
