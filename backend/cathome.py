"""主界面「每日玩法」（猫窝）—— 2026-09-26 bisai2.har + 当日实打实证。

实证结论（全部有 HAR / 实打对照，勿凭印象改）：
- 点赞状态：每日比赛榜 = competition act=9 的 `zan_bit`（0 未点 / 1 已点，当日点赞后 0→1）；
  竞技比赛榜 = pvp act=14 的 `zan_bit`（同上）。
- 点赞动作：每日榜 act=22 {opuin=<榜1 uin>, rank_idx:0} → content 44_0_3034；
  竞技榜 act=22 {rank_idx:0} → content 92_9_130（三叶草×130）。
- 扭蛋机：免费 1 次/日，状态 = act=16 的 `free_times`（抽完 0→1，当日实打）；
  免费抽 = act=17 {id:1}（HAR 00:38，limit_free:1，signleTimes 89→90）。
- 积分兑换 = act=18 {idx, num}：idx=2 是大糖果，花 6 积分（score 703→697），
  返回 content 92_33_1；商品表来自 mk/catHome_Info_v_114.xml 的 catHome_Integrallist。
- 糖果心情增量（当日实打）：candy_type 1=小 +10、2=中 +20、3=大 +50（用户确认大 +50）。
  喂猫 = act=11 {candy_type, elf_id}，返回喂后 cat_mood。
- 金币福利（act=20 读 gold_buy_id1..6）：商品表 = catHome_GoldShoplist，
  id=2 = 红宝石×8（10000 金币）。**购买动作的 act 尚未定位**（act=19/act=20 带 id 均
  只回 busy/状态，未实际扣金币）→ 这里只展示状态，按钮等抓包。
- 竞技场：pvp act=1 的 surplus=剩余挑战（4/日）、flush_num=已用免费刷新（4/日）；
  enemy_list 里带**自己**的条目（uin 相同），其 ce 就是自己当前战力；
  挑战 = act=8 {type:'fight', euin}；领奖 = act=15 {id:1..4}（daily_task 位）。
"""
from .constants import EP_TRAVEL_MATCH, EP_TRAVEL_PVP
from .match import _act, _json_of, _plain_uin

CANDY_EFFECT = {1: 10, 2: 20, 3: 50}   # 当日实打：小+10 / 中+20；大+50 为用户口述
MOOD_TARGET = 80                        # 用户要求：玩耍保证心情 80 以上
GACHA_CANDY_IDX = 2                     # 积分兑换 idx=2 = 大糖果（6 积分）


async def home_state(session) -> dict:
    """主界面每日玩法状态（全部只读）。"""
    uin = _plain_uin(session)

    comp1 = await _act(session, EP_TRAVEL_MATCH, 1, opuin=uin)
    d1 = comp1.get("data") or {}
    cats = [{"elf_id": c.get("elf_id"), "mood": c.get("cat_mood"),
             "level": c.get("cat_level"), "ce": c.get("ce")}
            for c in d1.get("travel_elf_arr") or []]

    comp16 = await _act(session, EP_TRAVEL_MATCH, 16)
    d16 = comp16.get("data") or {}

    comp9 = await _act(session, EP_TRAVEL_MATCH, 9, dayindex=0)
    d9 = comp9.get("data") or {}
    hero0 = (d9.get("hero") or [{}])[0]

    pvp14 = await _act(session, EP_TRAVEL_PVP, 14)
    d14 = pvp14.get("data") or {}

    pvp1 = await _act(session, EP_TRAVEL_PVP, 1)
    dp1 = pvp1.get("data") or {}
    # 自己在对手列表里的条目 ce 才是竞技场口径的「我的战力」
    my_uin = uin
    my_ce = next((e.get("ce") for e in dp1.get("enemy_list") or []
                  if str(e.get("uin")) == my_uin), None) or d14.get("my_ce")

    comp20 = await _act(session, EP_TRAVEL_MATCH, 20)
    d20 = comp20.get("data") or {}

    return {
        "ok": True,
        "likes": {
            "match": d9.get("zan_bit"),          # 每日比赛榜 0/1
            "arena": d14.get("zan_bit"),         # 竞技比赛榜 0/1
            "match_top": {"uin": hero0.get("uin"), "nick": hero0.get("nick"),
                          "score": hero0.get("score")},
        },
        "gacha": {"free_used": d16.get("free_times"), "score": d16.get("score"),
                  "free_num": d16.get("free_num")},
        "cats": cats,
        "pvp": {"surplus": dp1.get("surplus"), "flush_num": dp1.get("flush_num"),
                "my_ce": my_ce, "score": dp1.get("score"),
                "enemies": [{"uin": e.get("uin"), "name": e.get("name"),
                             "ce": e.get("ce"), "winnum": e.get("winnum")}
                            for e in dp1.get("enemy_list") or []]},
        "gold": {"ruby_bought": d20.get("gold_buy_id2"), "raw": d20},
    }


async def like(session, target: str) -> dict:
    """点赞。target = match（每日比赛榜，点榜1）| arena（竞技比赛榜）。"""
    if target == "match":
        hero = await _act(session, EP_TRAVEL_MATCH, 9, dayindex=0)
        d = hero.get("data") or {}
        top = (d.get("hero") or [{}])[0]
        if not top.get("uin"):
            return {"ok": False, "message": "英雄榜为空，无法点赞"}
        r = await _act(session, EP_TRAVEL_MATCH, 22,
                       opuin=str(top["uin"]), rank_idx=0)
    elif target == "arena":
        r = await _act(session, EP_TRAVEL_PVP, 22, rank_idx=0)
    else:
        return {"ok": False, "message": f"未知点赞目标 {target}"}
    if not r.get("ok"):
        return r
    d = r.get("data") or {}
    if d.get("code") not in (0, None):
        return {"ok": False, "code": d.get("code"), "message": d.get("desc") or "点赞被拒"}
    return {"ok": True, "content": d.get("content") or ""}


async def gacha_free(session) -> dict:
    """扭蛋机免费抽一次（act=17 {id:1}）。先查 act=16 free_times，已用直接拒绝。"""
    st = await _act(session, EP_TRAVEL_MATCH, 16)
    used = (st.get("data") or {}).get("free_times")
    if used not in (0, None):
        return {"ok": False, "message": f"今天的免费扭蛋已用过（free_times={used}）"}
    r = await _act(session, EP_TRAVEL_MATCH, 17, id=1)
    if not r.get("ok"):
        return r
    d = r.get("data") or {}
    if d.get("code") not in (0, None):
        return {"ok": False, "code": d.get("code"), "message": d.get("desc") or "抽取被拒"}
    return {"ok": True, "content": d.get("content") or "", "score": d.get("score")}


async def exchange_candy(session, num: int = 1) -> dict:
    """积分兑换大糖果（act=18 {idx:2, num}，6 积分/颗）。先查积分够不够。"""
    st = await _act(session, EP_TRAVEL_MATCH, 16)
    score = (st.get("data") or {}).get("score")
    need = 6 * int(num)
    if score is not None and score < need:
        return {"ok": False, "message": f"积分不足（{score} < {need}）"}
    r = await _act(session, EP_TRAVEL_MATCH, 18, num=int(num), idx=GACHA_CANDY_IDX)
    if not r.get("ok"):
        return r
    d = r.get("data") or {}
    if d.get("code") not in (0, None):
        return {"ok": False, "code": d.get("code"), "message": d.get("desc") or "兑换被拒"}
    return {"ok": True, "content": d.get("content") or "", "score": d.get("score")}


def _candy_plan(mood: int, target: int = MOOD_TARGET) -> list:
    """给心情 mood 的猫挑一套糖果组合（增量 CANDY_EFFECT）。

    目标：达标且浪费最少 —— 枚举 ≤3 颗的组合，按（超出量, 颗数）排序取最优。
    """
    need = target - mood
    if need <= 0:
        return []
    best = None
    for a in range(4):
        for b in range(4 - a):
            for c in range(4 - a - b):
                if a + b + c == 0 or a + b + c > 3:
                    continue
                gain = a * CANDY_EFFECT[1] + b * CANDY_EFFECT[2] + c * CANDY_EFFECT[3]
                if gain < need:
                    continue
                key = (gain - need, a + b + c)
                if best is None or key < best[0]:
                    best = (key, [1] * a + [2] * b + [3] * c)
    return (best[1] if best else [3])   # 兜底：喂一颗大的


async def feed_smart(session, threshold: int = MOOD_TARGET) -> dict:
    """把所有猫的心情喂到阈值（act=11）。返回逐条记录；某只失败即停（防刷屏）。"""
    uin = _plain_uin(session)
    st = await _act(session, EP_TRAVEL_MATCH, 1, opuin=uin)
    cats = (st.get("data") or {}).get("travel_elf_arr") or []
    steps, fed_any = [], False
    for c in cats:
        elf, mood = c.get("elf_id"), c.get("cat_mood") or 0
        plan = _candy_plan(mood, threshold)
        if not plan:
            steps.append({"elf_id": elf, "mood": mood, "msg": "无需喂"})
            continue
        for ct in plan:
            r = await _act(session, EP_TRAVEL_MATCH, 11, elf_id=int(elf), candy_type=int(ct))
            d = r.get("data") or {}
            ok = r.get("ok") and d.get("code") in (0, None)
            steps.append({"elf_id": elf, "candy": ct, "ok": ok,
                          "mood": d.get("cat_mood"),
                          "msg": d.get("desc") or r.get("message") or ""})
            if not ok:
                return {"ok": False, "steps": steps,
                        "message": f"猫{elf} 喂糖果{ct} 失败（{d.get('desc') or r.get('message') or '可能糖果不足'}）"}
            fed_any = True
    return {"ok": True, "steps": steps, "fed": fed_any}


async def feed_one(session, elf_id: int, candy_type: int) -> dict:
    """手动喂一颗糖（act=11）。"""
    r = await _act(session, EP_TRAVEL_MATCH, 11, elf_id=int(elf_id), candy_type=int(candy_type))
    d = r.get("data") or {}
    if not r.get("ok") or d.get("code") not in (0, None):
        return {"ok": False, "message": d.get("desc") or r.get("message") or "喂食失败"}
    return {"ok": True, "mood": d.get("cat_mood"), "elf_id": d.get("elfid")}


async def arena_auto(session, max_flush: int = 4, feed: bool = True) -> dict:
    """竞技比赛自动：挑战战力低于自己的对手（≤surplus 次），不够就免费刷对手，
    打完领任务奖励（act=15 id=1..4）。不换装（pvp act=8 用猫当前穿着，HAR 实证）。

    feed=True 时**先**把 4 只猫喂到心情 80 再去打 —— 用户明确要求「4 个猫的玩耍要在
    竞技比赛之前进行，以保证心情在 80 以上」，顺序由代码保证，不靠用户手动点两下。
    """
    uin = _plain_uin(session)
    steps = []

    if feed:
        f = await feed_smart(session)
        fed_n = len([s for s in f.get("steps") or [] if s.get("candy")])
        steps.append({"act": "feed", "ok": f.get("ok", False),
                      "msg": f"先喂猫到 80（喂了 {fed_n} 颗）"})
        if not f.get("ok"):
            return {"ok": False, "steps": steps,
                    "message": f.get("message") or "喂猫失败，已中止竞技"}

    st = await _act(session, EP_TRAVEL_PVP, 1)
    dp = st.get("data") or {}
    surplus = int(dp.get("surplus") or 0)
    flush_left = max_flush - int(dp.get("flush_num") or 0)
    my_ce = next((e.get("ce") for e in dp.get("enemy_list") or []
                  if str(e.get("uin")) == uin), None)

    def targets(lst, my):
        if my is None:
            return []
        return [e for e in lst or [] if str(e.get("uin")) != str(uin)
                and e.get("ce") is not None and e.get("ce") < my]

    enemies = dp.get("enemy_list") or []
    wins, fights = 0, 0
    while surplus > 0:
        cand = sorted(targets(enemies, my_ce), key=lambda e: -e["ce"])
        if not cand:
            if flush_left <= 0:
                steps.append({"act": "flush", "ok": False, "msg": "没有更低战力对手，免费刷新已用完"})
                break
            fr = await _act(session, EP_TRAVEL_PVP, 7)
            fd = fr.get("data") or {}
            if not fr.get("ok") or fd.get("code") not in (0, None):
                steps.append({"act": "flush", "ok": False,
                              "msg": fd.get("desc") or fr.get("message") or "刷新失败"})
                break
            flush_left -= 1
            enemies = fd.get("enemy_list") or []
            steps.append({"act": "flush", "ok": True,
                          "msg": f"刷新对手（剩 {flush_left} 次），新对手 {len(enemies)} 个"})
            continue
        foe = cand[0]
        r = await _act(session, EP_TRAVEL_PVP, 8, type="fight", euin=str(foe["uin"]))
        d = r.get("data") or {}
        if not r.get("ok") or d.get("code") not in (0, None):
            steps.append({"act": "fight", "ok": False, "foe": foe.get("name"),
                          "msg": d.get("desc") or r.get("message") or "挑战失败"})
            break
        surplus -= 1
        fights += 1
        ba = (d.get("battle_data") or {}).get("attack_accounts") or {}
        win = ba.get("iswin")
        wins += 1 if win == 1 else 0
        steps.append({"act": "fight", "ok": True, "foe": foe.get("name"),
                      "ce": foe.get("ce"), "win": win,
                      "score": ba.get("pvp_score"), "msg": f"vs {foe.get('name')}（ce {foe.get('ce')}）"})
        enemies = [e for e in enemies if str(e.get("uin")) != str(foe["uin"])]

    # 只有真正打了才领任务奖励（HAR 实证：都是打完连着领 4 个）；一次没打就别空领
    claims = []
    if fights:
        for cid in (1, 2, 3, 4):
            r = await _act(session, EP_TRAVEL_PVP, 15, id=cid)
            d = r.get("data") or {}
            if r.get("ok") and d.get("code") in (0, None) and d.get("content"):
                claims.append({"id": cid, "content": d.get("content")})
        steps.append({"act": "claim", "ok": True, "msg": f"领任务奖励 {len(claims)} 项"})
    else:
        steps.append({"act": "claim", "ok": False, "msg": "今天没打挑战，跳过领奖"})

    return {"ok": True, "steps": steps, "fights": fights, "wins": wins,
            "claims": claims, "surplus_left": surplus}
