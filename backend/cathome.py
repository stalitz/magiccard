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
- 竞技场次数口径（2026-10-15 实测校正，别再把四个字段混用）：
  `surplus` = **当前还能打的免费场次（剩余量）**：打一场掉 1，打到 0 后 act=8 直接回
  `system busy`(-3) —— 用两个不同对手连打都 busy，确认是真没次数，不是参数问题；
  `free_pvp_num` = **今日已用掉的免费场次（动态：打一场 +1，范围 0→4）**，**不是**每日总量！
  与 surplus 互补，**二者之和恒为每日免费总量 4**（surplus + free_pvp_num = 4）。
  前端「每日免费」显示要用这个和，绝不能直接把 free_pvp_num 当总量（它会随消耗增长）。
  `pvp_num` = 付费挑战次数（恒 0）；`flush_num` = 已用免费刷新（跑之前 0、跑完 1）；
  `battle_arr=[3,4,2]` 语义至今未定位 → **只记录不解读**，别拿它当剩余次数用。
  教训：曾把 `free_pvp_num` 误当成「每日总量恒为 4」、又把开局 `surplus=2` 写成「每日上限=2」——
  二者都错：surplus 只是剩余量、free_pvp_num 只是已用量，日总量恒为二者之和。
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


def _run_log_path() -> _Path:
    """步骤落盘文件 —— 按账号分文件（2026-11-06：原来是全局一份，两个账号的
    运行历史互相顶掉，「某某没成功」时查到的是另一个号的记录）。"""
    from .acct import acct_path
    return acct_path(_HOME_RUN_LOG)


def _save_run(tag: str, payload: dict) -> None:
    """把最近一次自动流程的步骤写进 data/home_run.<uin>.json（只留最近 20 次）。"""
    try:
        hist = []
        p = _run_log_path()
        if p.exists():
            loaded = _json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                hist = loaded
        row = dict(payload)
        row["tag"], row["ts"] = tag, _time.strftime("%F %T")
        hist.append(row)
        p.write_text(_json.dumps(hist[-20:], ensure_ascii=False, indent=1),
                     encoding="utf-8")
    except Exception:  # 落盘失败不影响主流程
        pass


CANDY_EFFECT = {1: 10, 2: 20, 3: 50}   # 当日实打：小+10 / 中+20；大+50 为用户口述
MOOD_TARGET = 80                        # 用户要求：玩耍保证心情 80 以上
CANDY_PER_CAT_MAX = 10                  # 单只猫单类糖果一次最多连喂几颗（够 80：小 8 / 中 4 / 大 2）
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
        # ⚠️ 只试 cand[0] 不稳：某对象此刻偶发被服务端回 `system busy`（非限流，是
        #    该对象不允许赞），单列[0]失败就整榜没点上 → 日常卡住。改为遍历可赞列表，
        #    命中第一个成功（code∈{0,None}）即停，最小化副作用（每天仍只点 1 次）。
        r = None
        last_msg = ""
        for c in cand:
            rr = await _act(session, EP_TRAVEL_PVP, 22,
                            rank_uin=str((c or {}).get("uin")), rank_idx=0)
            if rr.get("ok") and (rr.get("data") or {}).get("code") in (0, None):
                r = rr
                break
            last_msg = (rr.get("data") or {}).get("desc") or rr.get("message") or "点赞被拒"
        if r is None:
            return {"ok": False, "code": -1,
                    "message": f"竞技榜逐个点赞均失败（试了 {len(cand)} 个）：{last_msg}"}
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


def _candy_plan(mood: int, target: int = MOOD_TARGET, exclude=()) -> list:
    """给心情 mood 的猫算出**候选方案列表**（按「浪费增量 → 颗数」升序），供逐级降级。

    每个方案 = {"candy_type": ct, "n": 连喂颗数, "waste": 超出量}，只用单一糖果类型。

    ★ 2026-11-16 修复：旧版只枚举「≤3 颗」的组合，猫心情 0（需 +80）时 3 颗内最大增量
    只有 20×3 = 60，**永远无解** → 兜底硬返回 [3]（1 颗大糖果）。大糖果经常是 0 个，
    而服务端对「没有的糖果类型」只回 system busy → 喂必失败；`feed_smart` 又一见失败就
    整体 return，于是「小糖果 25 颗、中糖果 18 颗明明够，却一只猫都喂不到 80」。
    现在按每种类型单独算够 need 所需颗数，全部留作候选，喂不动就换下一种。
    """
    need = target - mood
    if need <= 0:
        return []
    out = []
    for ct, eff in CANDY_EFFECT.items():
        if ct in exclude:
            continue
        n = -(-need // eff)                       # ceil(need / eff)
        if n <= 0 or n > CANDY_PER_CAT_MAX:
            continue
        out.append({"candy_type": int(ct), "n": int(n), "waste": int(n * eff - need)})
    out.sort(key=lambda x: (x["waste"], x["n"]))
    return out


async def feed_smart(session, threshold: int = MOOD_TARGET) -> dict:
    """把所有猫的心情喂到阈值（act=11），返回逐条记录。

    ★ 2026-11-16 修复两点（旧版「糖果够却喂不到 80」的根因之二）：
    1) 某颗糖喂不动时，只把该糖果类型在**本次调用内**判死（dead），换下一种继续喂；
       不再一失败就整体 return —— 否则第一只猫失败会让后面所有猫一颗都喂不上。
    2) 单只猫喂不到阈值只记入 shortfalls，不阻断其它猫；全部试尽仍不够才 ok=False。
    """
    uin = _plain_uin(session)
    st = await _act(session, EP_TRAVEL_MATCH, 1, opuin=uin)
    cats = (st.get("data") or {}).get("travel_elf_arr") or []
    steps, fed_any, dead, shortfalls = [], False, set(), []
    for c in cats:
        elf, mood = c.get("elf_id"), c.get("cat_mood") or 0
        cur = int(mood or 0)
        if cur >= threshold:
            steps.append({"elf_id": elf, "mood": cur, "msg": "无需喂"})
            continue
        for _ in range(20):                        # 死循环保护（每颗都要发请求）
            plan = _candy_plan(cur, threshold, exclude=dead)
            if not plan:
                break
            ct = plan[0]["candy_type"]
            r = await _act(session, EP_TRAVEL_MATCH, 11, elf_id=int(elf), candy_type=int(ct))
            d = r.get("data") or {}
            ok = r.get("ok") and d.get("code") in (0, None)
            nm = d.get("cat_mood")
            steps.append({"elf_id": elf, "candy": ct, "ok": ok,
                          "mood": int(nm) if (ok and nm is not None) else cur,
                          "msg": d.get("desc") or r.get("message") or ""})
            if not ok:
                dead.add(ct)                       # 该类型没货/不可用 → 本次不再试
                continue
            fed_any = True
            cur = int(nm) if nm is not None else cur + CANDY_EFFECT.get(ct, 0)
            if cur >= threshold:
                break
        if cur < threshold:
            shortfalls.append(f"猫{elf} 只到心情 {cur}/{threshold}")
    if shortfalls:
        tried = ("、".join(f"糖果{t}" for t in sorted(dead)) + " 喂不动；") if dead else ""
        return {"ok": False, "steps": steps, "fed": fed_any,
                "message": f"{tried}{'，'.join(shortfalls)}"}
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


# ---------- 竞技场出战安全边际（2026-11-08 真机实测后定调） ----------
# 日志：我 ce=20004，对手 17728(−2276) 胜，19830(−174)/19839(−165)/19984(−20) 连负 3 场，
# 输一样扣次数。旧版只判「ce < 我的战力」就打，按升序打完最低的后全是几乎同战力的硬骨头，
# 而且只要列表里还有「低 20 点」的对手就永不触发刷新 → 当天免费刷新一次没用、4 次全打光。
ARENA_SAFE_RATIO = 0.10      # 对手战力至少比自己低 10%
ARENA_SAFE_MIN_GAP = 1000    # 低战力段的绝对下限（至少低 1000 点）
ARENA_LOSE_STREAK_MAX = 2    # 连败 2 场直接停手，保留剩余次数


def _ce_of(e) -> int:
    """enemy_list 的 ce 可能是数字或字符串，统一转 int；缺失/异常按 0（不参选）。"""
    try:
        return int(e.get("ce") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _safe_gap(my_ce) -> int:
    return max(ARENA_SAFE_MIN_GAP, int(_ce_of({"ce": my_ce}) * ARENA_SAFE_RATIO))


async def arena_auto(session, max_flush: int = 4, feed: bool = True):
    """竞技比赛自动（生成器，逐步 yield 以便前端实时显示）。
    开局一键穿装(act=27，持久化、只做一次) → 每场走完整流程：重读对手/自己战力 +
    挑「战力差 ≥ 安全边际」里最低的 + 没有安全目标就刷新(act=7) + 出战(act=8)。
    刷新用完仍没有安全目标则停手留次数；连败 2 场也停手（2026-11-08）。
    不并发：每场前 3s 冷却（从源头避开服务端限流），且每场都重新读对手/自己战力。
    竞技穿装与每日比赛(act=4 ids)不共享、持久保存，故只开局穿一次。
    """
    uin = _plain_uin(session)
    steps = []
    def emit(d):
        steps.append(d)
        return d

    # 每场挑战前短冷却：连发会触发服务端限流(system busy)，09-29 实测 auto 跑第 2 场必 busy
    # 而中断（HAR 手动连点 4 场全成功，差异仅在间隔）。3s 冷却已从源头避开限流。
    # ⚠ 关键修复（2026-10-15）：busy 一律**跳过该对手**转打下一个，绝不在同一对手上反复重试
    # （旧版对同一个对手重试 4 次×5s，遇到限流会把整轮拖成「卡住」假象，且会虚高战斗计数）。
    FIGHT_COOLDOWN = 3.0   # 每场挑战前冷却（秒），从源头避免触发限流

    if feed:
        f = await feed_smart(session)
        fed_n = len([s for s in f.get("steps") or [] if s.get("candy")])
        yield emit({"act": "feed", "ok": f.get("ok", False),
                    "msg": f"先喂猫到 80（喂了 {fed_n} 颗）"
                           + ("" if f.get("ok") else f"：{f.get('message') or '有猫未达标'}")})
        # ★ 2026-11-16：喂猫未达标不再中止竞技。两者互相独立，旧版一失败就 return，
        #   会让「糖果不够/某类型没货」连带把当天比赛也停掉（用户实测 3822843 就是这情况）。
        if not f.get("ok"):
            yield emit({"act": "feed:warn", "ok": True,
                        "msg": "有猫没喂到 80，但继续跑竞技（喂猫与比赛互不影响）"})

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

    flush_left = max_flush - int(dp0.get("flush_num") or 0)
    wins, fights = 0, 0
    busy_total = 0
    stop_all = False
    lose_streak = 0             # 连续明确战败（win==0）计数；赢了清零、结果未知不动
    BUSY_MAX = 20                   # 整轮最多容忍的 busy 次数（兜底收尾；正常靠 flush_left 与 surplus 自然结束）
    loop_guard, LOOP_MAX = 0, 40    # 总轮次硬上限：防「全员持续限流只跳过不扣次数」空转
    live = int(dp0.get("surplus") or 0)
    skip_uins = set()               # 本回合已打过（胜/负）或持续 busy 的对手 uin，绝不重复打同一个人

    def pick_target(enemies, my):
        """挑安全目标：战力比我低至少 _safe_gap(my)、本回合没打过，取其中战力最低的。

        修复 10-01：原 sort(key=-ce) 取「比我低里战力最高的」，专挑只低几十的硬骨头。
        修复 2026-11-08：只判 ce<my 也不够 —— 低 20 点的照样打、照样输、照样扣次数。
        """
        m = _ce_of({"ce": my})
        if m <= 0:
            return None
        gap = _safe_gap(m)
        cand = [e for e in enemies or []
                if str(e.get("uin")) != str(uin)
                and _ce_of(e) > 0
                and m - _ce_of(e) >= gap
                and str(e.get("uin")) not in skip_uins]
        if not cand:
            return None
        return min(cand, key=_ce_of)   # 差距够大里战力最低 = 最易打

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
        free_seen = int(dp.get("free_pvp_num") or 0)   # 本场开局已用次数，用于判定 busy 是否真扣次数
        if live <= 0:
            yield emit({"act": "arena:end", "ok": True,
                        "msg": f"今日竞技次数已用完（已打 {fights} 场）"})
            break
        # 连败保护（2026-11-08）：明确输满 2 场就收手，别在同一批/接近战力的对手上继续送次数
        if lose_streak >= ARENA_LOSE_STREAK_MAX:
            yield emit({"act": "arena:limit", "ok": True,
                        "msg": f"连败 {lose_streak} 场，停止挑战（保留 {live} 次，"
                               f"不再硬碰战力接近的对手）"})
            break
        my_ce = next((e.get("ce") for e in dp.get("enemy_list") or []
                      if str(e.get("uin")) == uin), my_ce0)
        enemies = dp.get("enemy_list") or []
        foe = pick_target(enemies, my_ce)
        if foe is None:
            # 诊断文案：把「为什么刷/为什么停」写清楚（我多少、最接近差多少、门槛多少）
            m = _ce_of({"ce": my_ce})
            near = [_ce_of(e) for e in enemies
                    if str(e.get("uin")) != str(uin) and _ce_of(e) > 0
                    and str(e.get("uin")) not in skip_uins and _ce_of(e) < m] if m > 0 else []
            if near:
                why = (f"我 {m}，本批最接近的只低 {m - max(near)}"
                       f"（需 ≥{_safe_gap(m)}）")
            else:
                why = f"我 {m or '未知'}，本批没有比我弱且未打过的对手"
            if flush_left <= 0:
                yield emit({"act": "arena:end", "ok": True,
                            "msg": f"没有安全边际内的对手（{why}），免费刷新已用完 → "
                                   f"保留 {live} 次不出战，可手动挑对手"})
                break
            fr = await _act(session, EP_TRAVEL_PVP, 7)
            fd = fr.get("data") or {}
            if not fr.get("ok") or fd.get("code") not in (0, None):
                yield emit({"act": "flush", "ok": False,
                            "msg": fd.get("desc") or fr.get("message") or "刷新失败"})
                break
            flush_left -= 1
            yield emit({"act": "flush", "ok": True,
                        "msg": f"战力差不足（{why}）→ 刷新对手（剩 {flush_left} 次），"
                               f"新对手 {len(fd.get('enemy_list') or [])} 个"})
            continue
        # 竞技穿装已在开局做一次并持久保存，这里无需每场重穿（与每日比赛每场临时算不同）。
        # 每场只挑战**一次**：3s 冷却已从源头避开限流。busy 一律**跳过该对手**转打下一个，
        # 绝不在同一对手上反复重试（那只会持续触发限流、拖成「卡住」假象）。是否真扣次数
        # 以回读 free_pvp_num 为准（本场开局值记在 free_seen）。
        await asyncio.sleep(FIGHT_COOLDOWN)
        foe_uin = str(foe["uin"])
        r = await _act(session, EP_TRAVEL_PVP, 8, type="fight", euin=foe_uin)
        d = r.get("data") or {}
        if r.get("ok") and d.get("code") in (0, None):
            fights += 1
            ba = (d.get("battle_data") or {}).get("attack_accounts") or {}
            win = ba.get("iswin")
            wins += 1 if win == 1 else 0
            skip_uins.add(foe_uin)   # 无论输赢，本回合不再打这个人
            retire = ""
            if win == 1:
                lose_streak = 0
            elif win == 0:
                lose_streak += 1
                # 连败保护：明确输了就把本批剩余对手全部作废，逼下一轮走刷新分支换一批，
                # 而不是继续在这批里挑「只低几十点」的下一个硬骨头。
                if lose_streak < ARENA_LOSE_STREAK_MAX:
                    for e in enemies:
                        if str(e.get("uin")) != str(uin):
                            skip_uins.add(str(e.get("uin")))
                    retire = " · 本批对手作废，下一轮换一批"
            # win 为 None（busy 但次数已扣、战报缺失）：不动连败计数，也不作废整批
            yield emit({"act": "arena:fight", "ok": True, "foe": foe.get("name"),
                        "ce": foe.get("ce"), "win": win,
                        "score": ba.get("pvp_score"),
                        "msg": f"vs {foe.get('name')}（ce {foe.get('ce')}）"
                               f"{'胜 ✓' if win == 1 else ('负 ✗' if win == 0 else '')}{retire}"})
            continue
        desc = str(d.get("desc") or r.get("message") or "挑战失败")
        if "system busy" not in desc.lower():
            # 非 busy 失败（非法/状态不允许/战败返回错误码）：跳过该对手换人
            skip_uins.add(foe_uin)
            yield emit({"act": "arena:fight", "ok": False, "foe": foe.get("name"), "msg": desc})
            continue
        # busy（限流 / 次数不允许，铁律：busy 常掩盖真实语义）：回读配额判定是否真扣次数，
        # 然后跳过该对手转打下一个。绝不在同一对手上反复重试。
        busy_total += 1
        q = await _act(session, EP_TRAVEL_PVP, 1)
        qd = q.get("data") or {}
        live_surplus = int(qd.get("surplus") or 0)
        free_now = int(qd.get("free_pvp_num") or 0)
        if free_now > free_seen:
            # 次数被扣：战斗实际已发生（胜负未知，响应里没有战报）
            fights += 1
            skip_uins.add(foe_uin)
            yield emit({"act": "arena:fight", "ok": True, "foe": foe.get("name"), "win": None,
                        "msg": f"返回 busy 但次数已扣（已打 {free_now} 场），战斗实际已发生"
                               f"（胜负以游戏内战绩为准）"})
            continue
        skip_uins.add(foe_uin)
        yield emit({"act": "arena:fight", "ok": False, "foe": foe.get("name"),
                    "msg": f"system busy（未扣次数，服务端剩 {live_surplus} 次），转打下一个对手"})
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


async def run_all(session, gold: bool = False):
    """「一键全部」：按正确顺序自动跑完每日玩法 —— 用户要的主入口（2026-09-26）。

    顺序：点赞 → 扭蛋 → 换糖（不够时）→ 喂猫到 80 →〔兑红宝石：**默认跳过**〕→ 竞技自动。

    ⚠️ `gold` 默认 False（2026-11-16 用户定调）：兑一次红宝石×8 要花 **10000 金币**，
    而这是「一键」里最容易被无视的支出（历史上 13 天净降 7.6 万的主因）。
    必须由前端勾选项显式传 True 才会兑；不勾就只是在日志里说明跳过，不产生任何扣费。
    单独兑一次走 `POST /api/home/gold-buy`（前端「手动兑红宝石」按钮）。
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

    # 3) 喂猫到 80；有糖果类型喂不动（多半是用完了）就先用积分换 1 颗大糖果再喂
    f = await feed_smart(session)
    blocked = [s for s in (f.get("steps") or []) if s.get("candy") and not s.get("ok")]
    if not f.get("ok") and blocked:
        c = await exchange_candy(session, 1)
        if c.get("ok"):
            yield emit({"act": "exchange", "ok": True, "msg": "糖果喂不动，先用 6 积分换 1 颗大糖果"})
            f = await feed_smart(session)
    n_candy = len([s for s in f.get("steps") or [] if s.get("candy")])
    yield emit({"act": "feed", "ok": bool(f.get("ok")),
                "msg": f.get("message") or f"喂猫到 80（共 {n_candy} 颗糖）"})

    # 4) 金币福利 · 兑红宝石：**默认不兑**（一次 10000 金币），仅勾选时执行
    if gold:
        g = await gold_buy_ruby(session)
        yield emit({"act": "gold", "ok": bool(g.get("ok")), "skipped": bool(g.get("skipped")),
                    "msg": g.get("message") or f"兑红宝石　→ {_reward_text(g.get('content'))}"})
    else:
        yield emit({"act": "gold", "ok": True, "skipped": True,
                    "msg": "未勾选「兑红宝石」，跳过（省 10000 金币）；要兑就点「手动兑红宝石」"})

    # 5) 竞技自动（玩耍已在第 3 步做完，这里不再重复喂）
    async for s in arena_auto(session, feed=False):
        steps.append(s)
        yield s

    # 6) 写动作仍没定位的玩法：如实跳过（不猜参数、不假装有按钮）
    yield emit({"act": "skip-group", "ok": True, "skipped": True,
                "msg": "以下写动作未定位，本次跳过：" + "；".join(s["what"] for s in ALL_SKIPS)})

    _save_run("run_all", {"ok": True, "steps": steps, "skipped": ALL_SKIPS})
    yield {"act": "__done__", "ok": True}
