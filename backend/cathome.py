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
- 竞技场次数口径（2026-09-27 全天实测，别再把四个字段混用）：
  `surplus` = **当前还能打的免费场次**（动态的：打一场掉 1，打到 0 后 act=8 直接回
  `system busy`(-3) —— 用两个不同对手连打都 busy，确认是真没次数，不是参数问题）；
  `free_pvp_num` = 每日免费次数**总量 4**（不随消耗变化，全天恒为 4）；
  `pvp_num` = 付费挑战次数（恒 0）；`flush_num` = 已用免费刷新（跑之前 0、跑完 1）；
  `battle_arr=[3,4,2]` 语义至今未定位 → **只记录不解读**，别拿它当剩余次数用。
  教训：上一版把「开局 surplus=2」写成「每日上限就是 2」是错的 —— 那天是**开跑前就已经
  用掉 2 次**，surplus 只是剩余量，不是日上限。
  enemy_list 里带**自己**的条目（uin 相同），其 ce 就是自己当前战力；
  挑战 = act=8 {type:'fight', euin}；领奖 = act=15 {id:1..4}（daily_task 位）。
"""
from .constants import EP_TRAVEL_MATCH, EP_TRAVEL_PVP
from .match import _act, _json_of, _plain_uin
from .prop_dict import summarize_reward as _reward_text

import time as _time
import json as _json
from pathlib import Path as _Path
import asyncio

# 「一键全部 / 竞技自动」的步骤落盘：用户报「某某没成功」时先看这份，
# 不必再让用户翻浏览器日志。data/ 已在 .gitignore 内。
_HOME_RUN_LOG = _Path(__file__).resolve().parent.parent / "data" / "home_run.json"


def _save_run(tag: str, payload: dict) -> None:
    """把最近一次自动流程的步骤写进 data/home_run.json（只留最近 20 次）。"""
    try:
        hist = []
        if _HOME_RUN_LOG.exists():
            loaded = _json.loads(_HOME_RUN_LOG.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                hist = loaded
        row = dict(payload)
        row["tag"], row["ts"] = tag, _time.strftime("%F %T")
        hist.append(row)
        _HOME_RUN_LOG.write_text(_json.dumps(hist[-20:], ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    except Exception:  # 落盘失败不影响主流程
        pass


CANDY_EFFECT = {1: 10, 2: 20, 3: 50}   # 当日实打：小+10 / 中+20；大+50 为用户口述
MOOD_TARGET = 80                        # 用户要求：玩耍保证心情 80 以上
GACHA_CANDY_IDX = 2                     # 积分兑换 idx=2 = 大糖果（6 积分）

# 金币福利 · 兑红宝石（bisai3.har 实证）：
#   请求 card_travel_elf_competition act=21 {id:1} → {"code":0,"content":"92_32_8"}
#   本次请求前 gold_buy_id2=6，请求后 gold_buy_id2=7（当日实跑确认）。
# **编号撞车，别按字面推**：act=21 传的 id=1 不是 gold_buy_id 的下标（若按下标，
# 该涨的该是 gold_buy_id1，但它仍是 0）；红宝石在配置表 catHome_Shoplist 里
# 的 id 是 9（name="红宝石" price_num="2" price_type="1"），也不是 1。
# 三者是三套编号，只有「现象」这一条证据链是硬的：传 1 → 计数落 2。
GOLD_RUBY_ACT_ID = 1        # act=21 的 id 参数（照抄抓包，勿改）
GOLD_RUBY_SLOT = 2          # 服务端计数字段 gold_buy_id2
GOLD_RUBY_LIMIT = 7         # 用户口述 + 今日实测到顶（id=2 已是 7）


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

    # 衣柜锻造状态：competition act=6 的 cat_home_dress_*_attr（每个部位一件件服装）
    comp6 = await _act(session, EP_TRAVEL_MATCH, 6)
    d6 = comp6.get("data") or {}
    parts = {}
    for key, label in (("cat_home_dress_head_attr", "头"),
                       ("cat_home_dress_clothing_attr", "衣"),
                       ("cat_home_dress_hand_attr", "手"),
                       ("cat_home_dress_other_attr", "其他")):
        parts[label] = [{"id": it.get("id"), "forge": it.get("forge"),
                         "chip": it.get("chip"),          # 锻这件要吃掉的材料数
                         "elv": it.get("elv"), "starlv": it.get("starlv"),
                         "xiyoulv": it.get("xiyoulv"), "ranklv": it.get("pvpRankLv"),
                         "exp": it.get("pvpexp")}
                        for it in (d6.get(key) or [])]

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
        "pvp": {"surplus": dp1.get("surplus"), "free_pvp_num": dp1.get("free_pvp_num"),
                "pvp_num": dp1.get("pvp_num"),
                "flush_num": dp1.get("flush_num"),
                "my_ce": my_ce, "score": dp1.get("score"),
                "enemies": [{"uin": e.get("uin"), "name": e.get("name"),
                             "ce": e.get("ce"), "winnum": e.get("winnum")}
                            for e in dp1.get("enemy_list") or []]},
        # 金币福利：act=20 的 gold_buy_id1..6（各商品今日已兑次数）+ gold_buy_id_tsN（兑换时间戳）
        "gold": {"items": [{"id": n, "bought": d20.get(f"gold_buy_id{n}"),
                            "ts": d20.get(f"gold_buy_id_ts{n}")} for n in range(1, 7)]},
        # 衣柜锻造：act=6 四部位的服装（每件的 forge=已锻造次数）
        "forge": {"parts": parts, "chip": d6.get("cat_home_chip_vt")},
        # 竞技段位奖励：pvp act=1 的 rank_gift_bit（空 = 未领）
        "rank_gift": {"bits": dp1.get("rank_gift_bit"), "my_rankbits": dp1.get("my_rankbits"),
                      "lv": dp1.get("level_rank_bit")},
    }


async def _zan_bit(session, target: str):
    """读回某榜今天的点赞状态：1=已点（0=未点）。服务端对重复点赞会回 system busy，
    所以点赞前后都要读回状态来判定真实结果，不能只信动作接口的返回。"""
    if target == "match":
        r = await _act(session, EP_TRAVEL_MATCH, 9, dayindex=0)
    else:
        r = await _act(session, EP_TRAVEL_PVP, 14)
    return ((r.get("data") or {}).get("zan_bit"))


async def like(session, target: str, only_if_unliked: bool = True) -> dict:
    """点赞。target = match（每日比赛榜，点榜1）| arena（竞技比赛榜）。

    only_if_unliked=True（默认）：已经点过就直接跳过，不重复发动作。
    """
    if target not in ("match", "arena"):
        return {"ok": False, "message": f"未知点赞目标 {target}"}
    if only_if_unliked and await _zan_bit(session, target) in (1, "1"):
        return {"ok": True, "skipped": True, "message": "今天已点过，跳过"}
    if target == "match":
        hero = await _act(session, EP_TRAVEL_MATCH, 9, dayindex=0)
        d = hero.get("data") or {}
        top = (d.get("hero") or [{}])[0]
        if not top.get("uin"):
            return {"ok": False, "message": "英雄榜为空，无法点赞"}
        r = await _act(session, EP_TRAVEL_MATCH, 22,
                       opuin=str(top["uin"]), rank_idx=0)
    else:
        # ⚠️ 竞技榜点赞**必须带赞对象的 uin**（rank_uin）。只发 rank_idx 服务端回
        #    `system busy` 且不做任何事（2026-09-27 实踩：一键全部跑完，竞技榜点赞位
        #    仍是 0，玩家白丢每日 130 三叶草）。可赞对象 = act=14 日榜里
        #    `zan` 非空的条目（HAR 实证约 10 人），赞完这一批当天就没了。
        rk = await _act(session, EP_TRAVEL_PVP, 14, rank_id=0)
        rd = rk.get("data") or {}
        cand = [x for x in (rd.get("list") or []) if x.get("zan") is not None]
        if not cand:
            return {"ok": False, "code": -1, "message": "今日竞技榜没有可点赞的对象（可能已赞过）"}
        r = await _act(session, EP_TRAVEL_PVP, 22,
                       rank_uin=str((cand[0] or {}).get("uin")), rank_idx=0)
    if not r.get("ok"):
        return r
    d = r.get("data") or {}
    if d.get("code") not in (0, None):
        return {"ok": False, "code": d.get("code"), "message": d.get("desc") or "点赞被拒"}
    # 服务端对重复点赞会回 system busy，用「动作 + 读回状态」确认最终结果
    actually = await _zan_bit(session, target)
    if actually in (1, "1"):
        return {"ok": True, "content": d.get("content") or "", "confirmed": True}
    return {"ok": True, "content": d.get("content") or "",
            "message": f"动作已发但点赞位未变化（zan_bit={actually}），可能今天的赞已用过"}


async def gacha_free(session) -> dict:
    """扭蛋机免费抽一次（act=17 {id:1}）。已用过就跳过（不是失败）。"""
    st = await _act(session, EP_TRAVEL_MATCH, 16)
    used = (st.get("data") or {}).get("free_times")
    if used not in (0, None):
        return {"ok": True, "skipped": True,
                "message": f"今天的免费扭蛋已用过（free_times={used}）"}
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


async def gold_buy_ruby(session) -> dict:
    """金币福利 · 兑换红宝石（act=21 {id:1}）。

    到上限就直接跳过 —— 这个动作每天能兑的次数有限，多点是真的浪费（用户已为此损失 1 次）。
    动作前后都读回 gold_buy_id2：到顶不算成功也不重试。
    """
    slot = GOLD_RUBY_SLOT
    before = await _act(session, EP_TRAVEL_MATCH, 20)
    bought = ((before.get("data") or {}).get(f"gold_buy_id{slot}"))
    if bought is not None and bought >= GOLD_RUBY_LIMIT:
        return {"ok": True, "skipped": True,
                "message": f"今日已兑 {bought} 次（上限 {GOLD_RUBY_LIMIT}），跳过"}
    r = await _act(session, EP_TRAVEL_MATCH, 21, id=GOLD_RUBY_ACT_ID)
    if not r.get("ok"):
        return r
    d = r.get("data") or {}
    if d.get("code") not in (0, None):
        return {"ok": False, "code": d.get("code"), "message": d.get("desc") or "兑换被拒"}
    after = await _act(session, EP_TRAVEL_MATCH, 20)
    bought2 = ((after.get("data") or {}).get(f"gold_buy_id{slot}"))
    if bought2 is not None and bought2 <= (bought or 0):
        return {"ok": False, "message": f"兑换未生效（gold_buy_id{slot} 仍为 {bought2}）"}
    return {"ok": True, "content": d.get("content") or "", "bought": bought2}


def pick_forge_target(parts: dict) -> dict:
    """挑一件今天最该锻的服装：用户要求「找不花红宝石的锻造」。

    判据（都来自 act=6 的原始字段，不臆造）：
      - forge=0 表示这件还没锻过（已锻的 forge 普遍 9/19/29/39…）；
      - chip = 这件锻造要吃掉的材料数；库存来自 cat_home_chip_vt。
    所以在「未锻过」里挑 chip 最小的那件 —— 最不容易浪费材料/红宝石。
    """
    flat = []
    for label, lst in (parts or {}).items():
        for it in lst or []:
            flat.append({"part": label, "id": it.get("id"), "forge": it.get("forge") or 0,
                         "chip": it.get("chip") or 0})
    new = [x for x in flat if x["forge"] == 0]
    pool = new or flat
    if not pool:
        return None
    return min(pool, key=lambda x: (x["chip"], x["id"]))


async def forge_one(session, equip_id) -> dict:
    """衣柜锻造一件（act=29 {equip_id}）。bisai3.har 实证：
    {equip_id:20622} → {"attr":[...],"code":0,"forge":9}。
    代价（要多少材料、要不要红宝石）尚未实证 —— 调用前先问用户。
    """
    r = await _act(session, EP_TRAVEL_MATCH, 29, equip_id=int(equip_id))
    if not r.get("ok"):
        return r
    d = r.get("data") or {}
    if d.get("code") not in (0, None):
        return {"ok": False, "code": d.get("code"), "message": d.get("desc") or "锻造被拒"}
    return {"ok": True, "forge": d.get("forge"), "attr": d.get("attr")}


# 一键全部里**当前仍无法自动执行**的玩法：写动作的 act 仍没在抓包里出现。
# 服务端对未知 act 一律回 `code=0, system busy`（已用 probe_home_writes.py 验证），
# 盲探无解 —— 只能在日志里如实跳过，不猜参数、不假装有按钮。
ALL_SKIPS = [
    {"what": "竞技 · 领段位奖励", "why": "领段位奖的 act 未定位（act=15 只是任务奖励）"},
]


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


# 竞技一键穿装（持久化，与每日比赛不共享）：act=27，无参数，服务端自动给竞技猫穿
# 最优装并回写保存。HAR 实证(bisai2)：act=1 开对手列表 → act=27 一键穿装 → act=3 设阵容
# → act=8 出战。竞技穿装一旦设置就一直保存（capture 里开局猫已穿好、全程 equip_arr 不变），
# 所以 arena_auto 只在开局调用一次，不必每场重穿。每日比赛是 act=4 ids 每场临时算、不持久，
# 两套机制互不相干，严禁把每日比赛的 best_equip/act=4 复用到竞技。
DRESS_ACT = 27   # 竞技「一键穿装」：服务端自动算最优装、持久保存（bisai2 抓包确认）

async def dress_cat(session) -> dict:
    """竞技开局一键穿装：调用 act=27（无参数），服务端自动给竞技猫穿最优装并持久保存。

    不传 catId/ids —— act=27 本身是无参数的全局一键穿装，与每日比赛 act=4 ids 机制无关。
    调用失败不中止竞技（用当前穿着继续），只告警。
    """
    r = await _act(session, EP_TRAVEL_PVP, DRESS_ACT)
    d = r.get("data") or {}
    if not r.get("ok") or d.get("code") not in (0, None):
        return {"ok": False, "message": d.get("desc") or r.get("message") or "穿装失败"}
    return {"ok": True, "message": "竞技一键穿装完成（服务端自动穿最优装，已持久保存）"}


async def arena_auto(session, max_flush: int = 4, feed: bool = True):
    """竞技比赛自动（生成器，逐步 yield 以便前端实时显示）。
    开局一键穿装(act=27，持久化、只做一次) → 每场走完整流程：重读对手/自己战力 +
    比战力挑最低 + 没有更低才刷新(act=7) + 出战(act=8)。
    不并发：每场前 3s 冷却（从源头避开服务端限流），且每场都重新读对手/自己战力。
    竞技穿装与每日比赛(act=4 ids)不共享、持久保存，故只开局穿一次。
    """
    uin = _plain_uin(session)
    steps = []
    def emit(d):
        steps.append(d)
        return d

    # 连发冷却 / 限流重试（09-29 修复）：自动连发挑战会被服务端限流(system busy)，
    # 导致第 2 场即中断、整轮只打 1 场。HAR 手动连点 4 场全成功，差异仅在间隔。
    FIGHT_COOLDOWN = 3.0   # 每场挑战前冷却（秒），从源头避免触发限流
    FIGHT_RETRY_WAIT = 5.0  # system busy 后重试等待（秒）
    FIGHT_MAX_TRY = 4      # 单场最多尝试次数（含首试）

    if feed:
        f = await feed_smart(session)
        fed_n = len([s for s in f.get("steps") or [] if s.get("candy")])
        yield emit({"act": "feed", "ok": f.get("ok", False),
                    "msg": f"先喂猫到 80（喂了 {fed_n} 颗）"})
        if not f.get("ok"):
            yield emit({"act": "arena:error", "ok": False,
                        "msg": f.get("message") or "喂猫失败，已中止竞技"})
            return

    # 开门配额
    st0 = await _act(session, EP_TRAVEL_PVP, 1)
    dp0 = st0.get("data") or {}
    my_ce0 = next((e.get("ce") for e in dp0.get("enemy_list") or []
                   if str(e.get("uin")) == uin), None)
    quota_before = {"surplus": dp0.get("surplus"), "free_pvp_num": dp0.get("free_pvp_num"),
                    "pvp_num": dp0.get("pvp_num"), "flush_num": dp0.get("flush_num"),
                    "battle_arr": dp0.get("battle_arr"), "my_ce": my_ce0,
                    "score": dp0.get("score")}
    yield emit({"act": "arena:state", "ok": True,
                "msg": f"开局可打 {dp0.get('surplus')} 场，我的战力 {my_ce0}"})

    # 竞技一键穿装（持久化，只做一次）：act=27 服务端自动给竞技猫穿最优装并保存，
    # 与每日比赛 act=4 ids（每场临时、不持久）不共享。之后所有场次都用这套保存的装。
    dres = await dress_cat(session)
    yield emit({"act": "arena:dress", "ok": dres.get("ok", False),
                "msg": dres.get("message") or "穿装"})

    free_seen = int(dp0.get("free_pvp_num") or 0)
    flush_left = max_flush - int(dp0.get("flush_num") or 0)
    wins, fights = 0, 0
    busy_total = 0
    stop_all = False
    BUSY_MAX = 3                    # 整轮最多容忍的 busy 次数：防 7 对手×4 重试刷一墙失败
    loop_guard, LOOP_MAX = 0, 40    # 总轮次硬上限：防「全员持续限流只跳过不扣次数」空转
    live = int(dp0.get("surplus") or 0)
    skip_uins = set()               # 本回合已打过（胜/负）或持续 busy 的对手 uin，绝不重复打同一个人

    def pick_target(enemies, my):
        """挑本回合最优目标：比自己战力低、且本回合没打过，按战力升序取最低（最易打）。

        修复 10-01 用户反馈：原 sort(key=-ce) 取「比自己低里战力最高的」，等于专挑
        只比自己低几十的硬骨头，极易输且反复打同一人。改为取最低战力对手。
        """
        if my is None:
            return None
        cand = [e for e in enemies or []
                if str(e.get("uin")) != str(uin)
                and e.get("ce") is not None and e.get("ce") < my
                and str(e.get("uin")) not in skip_uins]
        if not cand:
            return None
        return min(cand, key=lambda e: e["ce"])   # 战力最低 = 最易打

    while True:
        loop_guard += 1
        if loop_guard > LOOP_MAX:
            yield emit({"act": "arena:limit", "ok": True,
                        "msg": "已达安全轮次上限，停止（疑似服务端持续限流）"})
            break
        # 每场前：看对手 + 看自己战力（实时重读，不是开局看一次就打 4 场）
        st = await _act(session, EP_TRAVEL_PVP, 1)
        dp = st.get("data") or {}
        live = int(dp.get("surplus") or 0)
        if live <= 0:
            yield emit({"act": "arena:end", "ok": True,
                        "msg": f"今日竞技次数已用完（已打 {fights} 场）"})
            break
        my_ce = next((e.get("ce") for e in dp.get("enemy_list") or []
                      if str(e.get("uin")) == uin), my_ce0)
        enemies = dp.get("enemy_list") or []
        foe = pick_target(enemies, my_ce)
        if foe is None:
            if flush_left <= 0:
                yield emit({"act": "flush", "ok": False,
                            "msg": "没有更低战力且未打过的对手，免费刷新已用完，结束"})
                break
            fr = await _act(session, EP_TRAVEL_PVP, 7)
            fd = fr.get("data") or {}
            if not fr.get("ok") or fd.get("code") not in (0, None):
                yield emit({"act": "flush", "ok": False,
                            "msg": fd.get("desc") or fr.get("message") or "刷新失败"})
                break
            flush_left -= 1
            yield emit({"act": "flush", "ok": True,
                        "msg": f"刷新对手（剩 {flush_left} 次），新对手 {len(fd.get('enemy_list') or [])} 个"})
            continue
        # 竞技穿装已在开局做一次并持久保存，这里无需每场重穿（与每日比赛每场临时算不同）。
        # 每场挑战前短冷却：连发会触发服务端限流（system busy），
        # 09-29 实测 auto 跑第 2 场必 busy 而中断（HAR 手动连点 4 场全成功，差异是间隔）。
        await asyncio.sleep(FIGHT_COOLDOWN)
        foe_uin = str(foe["uin"])
        fight_ok = False
        for _try in range(1, FIGHT_MAX_TRY + 1):
            r = await _act(session, EP_TRAVEL_PVP, 8, type="fight", euin=foe_uin)
            d = r.get("data") or {}
            if r.get("ok") and d.get("code") in (0, None):
                fight_ok = True
                break
            desc = str(d.get("desc") or r.get("message") or "挑战失败")
            if "system busy" not in desc.lower():
                # 非 busy 失败（非法/状态不允许/战败返回错误码）：本场判失败，跳过该对手换人
                skip_uins.add(foe_uin)
                yield emit({"act": "fight", "ok": False, "foe": foe.get("name"), "msg": desc})
                break
            # busy ≠ 纯限流（铁律：system busy 常掩盖「次数/状态不允许」）。先回读
            # 配额再决定下一步，绝不盲目重试：次数被扣=战斗可能已发生；次数归零=结束。
            busy_total += 1
            q = await _act(session, EP_TRAVEL_PVP, 1)
            qd = q.get("data") or {}
            live_surplus = int(qd.get("surplus") or 0)
            free_now = int(qd.get("free_pvp_num") or 0)
            if free_now > free_seen:
                # busy 但次数被扣：战斗实际已发生（胜负未知，响应里没有战报）
                fights += 1
                free_seen = free_now
                skip_uins.add(foe_uin)
                yield emit({"act": "fight", "ok": True, "foe": foe.get("name"), "win": None,
                            "msg": f"返回 busy 但次数已扣（已打 {free_now} 场），战斗实际已发生"
                                   f"（胜负以游戏内战绩为准）"})
                break
            else:
                yield emit({"act": "fight", "ok": False, "foe": foe.get("name"),
                            "msg": f"system busy（未扣次数，服务端剩 {live_surplus} 次）"})
            if live_surplus <= 0:
                stop_all = True
                yield emit({"act": "arena:limit", "ok": True,
                            "msg": "服务端剩余次数 0，今日竞技结束（busy=次数不允许，非限流）"})
                break
            if busy_total >= BUSY_MAX:
                stop_all = True
                yield emit({"act": "arena:limit", "ok": True,
                            "msg": f"busy 已 {busy_total} 次，停止挑战（服务端剩 {live_surplus} 次，"
                                   f"稍后可再跑）"})
                break
            if _try < FIGHT_MAX_TRY:
                await asyncio.sleep(FIGHT_RETRY_WAIT)
                continue
            yield emit({"act": "fight", "ok": False, "foe": foe.get("name"), "msg": desc})
            break
        if stop_all:
            break
        if not fight_ok:
            # 该对手重试耗尽（未扣次数）→ 跳过它打其余对手，不因一个对手卡住就整轮停。
            skip_uins.add(foe_uin)
            yield emit({"act": "fight", "ok": False, "foe": foe.get("name"),
                        "msg": f"对手「{foe.get('name')}」持续 busy，跳过，转打其余对手"})
            continue

        fights += 1
        ba = (d.get("battle_data") or {}).get("attack_accounts") or {}
        win = ba.get("iswin")
        wins += 1 if win == 1 else 0
        # 无论输赢，本回合不再打这个人（避免反复打同一人）
        skip_uins.add(foe_uin)
        yield emit({"act": "fight", "ok": True, "foe": foe.get("name"),
                    "ce": foe.get("ce"), "win": win,
                    "score": ba.get("pvp_score"),
                    "msg": f"vs {foe.get('name')}（ce {foe.get('ce')}）{'胜 ✓' if win == 1 else ('负 ✗' if win == 0 else '')}"})

    # 只有真正打了才领任务奖励（HAR 实证：都是打完连着领 4 个）；一次没打就别空领
    claims = []
    if fights:
        for cid in (1, 2, 3, 4):
            r = await _act(session, EP_TRAVEL_PVP, 15, id=cid)
            d = r.get("data") or {}
            if r.get("ok") and d.get("code") in (0, None) and d.get("content"):
                claims.append({"id": cid, "content": d.get("content")})
        yield emit({"act": "arena:claim", "ok": True, "msg": f"领任务奖励 {len(claims)} 项"})
    else:
        yield emit({"act": "arena:claim", "ok": False, "msg": "今天没打挑战，跳过领奖"})

    # 打完再读一次原始配额，和用户/游戏里的显示对照
    tail = await _act(session, EP_TRAVEL_PVP, 1)
    td = tail.get("data") or {}
    quota_after = {"surplus": td.get("surplus"), "free_pvp_num": td.get("free_pvp_num"),
                   "pvp_num": td.get("pvp_num"), "flush_num": td.get("flush_num"),
                   "battle_arr": td.get("battle_arr"), "score": td.get("score")}
    out = {"ok": True, "steps": steps, "fights": fights, "wins": wins,
           "claims": claims, "surplus_left": live,
           "quota_before": quota_before, "quota_after": quota_after,
           "fights_today": (quota_after.get("free_pvp_num") or 0)
                           - (quota_before.get("free_pvp_num") or 0) or None}
    _save_run("arena_auto", out)
    yield {"act": "arena:done", "ok": True, "fights": fights, "wins": wins,
           "surplus_left": live, "claims": claims}


async def run_all(session):
    """「一键全部」：按正确顺序自动跑完每日玩法 —— 用户要的主入口（2026-09-26）。

    顺序：点赞 → 扭蛋 → 换糖（不够时）→ 喂猫到 80 → 竞技自动。
    改为生成器：每完成一步就 yield，配合 /api/home/all 的 SSE 让前端实时显示。
    """
    steps = []
    def emit(d):
        steps.append(d)
        return d

    # 1) 两个榜各点一次赞（每天各 1 次，已点过自动跳过）
    for tg, label in (("match", "每日比赛榜"), ("arena", "竞技比赛榜")):
        r = await like(session, tg)
        yield emit({"act": "like", "ok": bool(r.get("ok")), "target": tg,
                    "msg": f"{label} {'已点 ✓' if r.get('ok') else (r.get('message') or '未点')}"})

    # 2) 扭蛋机免费抽一次
    r = await gacha_free(session)
    yield emit({"act": "gacha", "ok": bool(r.get("ok")),
                "msg": (f"扭蛋：{r['content']}" if r.get("content") else "")
                       or r.get("message") or "扭蛋"})

    # 3) 喂猫到 80；糖不够就先用积分换大糖果再喂
    f = await feed_smart(session)
    if not f.get("ok") and "不足" in (f.get("message") or ""):
        c = await exchange_candy(session, 1)
        if c.get("ok"):
            yield emit({"act": "exchange", "ok": True, "msg": "糖果不足，先用 6 积分换 1 颗大糖果"})
            f = await feed_smart(session)
    n_candy = len([s for s in f.get("steps") or [] if s.get("candy")])
    yield emit({"act": "feed", "ok": bool(f.get("ok")),
                "msg": f.get("message") or f"喂猫到 80（共 {n_candy} 颗糖）"})

    # 4) 金币福利 · 兑红宝石（到上限自动跳过，不会白白浪费次数）
    g = await gold_buy_ruby(session)
    yield emit({"act": "gold", "ok": bool(g.get("ok")), "skipped": bool(g.get("skipped")),
                "msg": g.get("message") or f"兑红宝石　→ {_reward_text(g.get('content'))}"})

    # 5) 竞技自动（玩耍已在第 3 步做完，这里不再重复喂）
    async for s in arena_auto(session, feed=False):
        steps.append(s)
        yield s

    # 6) 写动作仍没定位的玩法：如实跳过（不猜参数、不假装有按钮）
    yield emit({"act": "skip-group", "ok": True, "skipped": True,
                "msg": "以下写动作未定位，本次跳过：" + "；".join(s["what"] for s in ALL_SKIPS)})

    _save_run("run_all", {"ok": True, "steps": steps, "skipped": ALL_SKIPS})
    yield {"act": "__done__", "ok": True}
