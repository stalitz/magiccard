"""珍藏阁（card_user_zcg）炼卡：投 10 面卡 → 产出三色魔晶（橙/红/紫）。

2026-10-04 zhencang.har 真机抓包实证，act 语义：
  act=1  opuin=纯数字uin         查询状态（魔晶计数 + 3 个免费炉 puzi 数组）
  act=9  puzi_id=N              收卡（炼完的卡出炉，炉位清空）
  act=5  puzi_id=N              收魔晶产物（add_cmj/add_hmj/add_zmj/add_jl/add_jy/add_kongqueshi/add_yueguangshi）
  act=2  card_id + puzi_id + slot_id  投卡（放卡开炼）

投料机制（HAR 实证 + 真机验证 2026-10-04 23:00）：
  * act=2 的 slot_id = 卡箱里该卡所在的位置（换卡箱/保险箱各自的 slot 序号），
    干净四参数（act/card_id/puzi_id/slot_id）即可，服务端按 card_id 定位卡并投炉。
  * 换卡箱（changebox）的 10 面卡：直接 act=2 投（slot_id=换卡箱 slot）。
  * 保险箱（storebox）的 10 面卡：珍藏阁 act=2 只吃换卡箱（保险箱卡投会 -10 系统繁忙），
    需先用 card_user_storage_exchange（move_card）挪到换卡箱，再 act=2 投。
  * 卡箱无 10 面卡时：**优先用随机抽卡机会**（用户 2026-10-11 定调「一键抽完，没次数才买」）：
     反复「抽一批(type=2，抽到箱满/机会用完即「一键抽完」) → 把卡箱 10 面卡全投空炉
     （有多少投多少）→ 投完箱腾出位、还有机会就再抽」直到没机会；最后才 card_market_npc_buy
     商店买任意可买 10 面卡（不限套卡）落换卡箱后 act=2 投（用户 2026-10-04「随便买就行」）。
  * ⚠️ type=2 一次抽「一批」（实测一次 9 张、减 9 次，抽到箱满/机会用完才停），**没有只抽一张
     的参数**；所以靠「抽一批→投 10 面→腾位→再抽」逼近用户意图，而非字面「抽一张」。
  * ⚠️ 简易箱（card_user_temp_slot）/ 机会卡（card_user_random_get）与珍藏阁**没半点关系**，
    投料完全不经过它们——之前误把它们当珍藏阁投料来源是错的。

接口鉴权契约：
  * 查询 act=1 必须带 opuin=纯数字 uin（session.uin 补零会 code=0 但 data 空壳）。
  * 写动作 act=2/5/9 的 HAR 完全不带 uin/opuin，走 cookie 鉴权；代码里传 uin=None
    让 httpx 跳过该键（等于 HAR 无 uin）；实测收魔晶/投料均正常生效。
  * ⚠️ 该接口 code 字段不可靠：成功也常返回 -1/-2/-3（系统繁忙），失败也返回 -2/-3，
    不能靠 code 判定成败。收卡成功 = 收后该炉清空（card_id 归 0）；收魔晶成功 = add_* 到账；
    投料成功 = 投后该炉 card_id 变为所投卡。绝不调用加速道具 card_zhongkong_zk（用户明令禁用），
    炉没炼完就等自然炼完。

铁律（来自抓包 + 用户定调）：
  * 绝不调用加速（card_zhongkong_zk）！炉子没炼完就等它自然炼完，不加速。
  * 只投 10 面卡（card_plan.card_details[price]==10）。任何 307/304/280 面贵卡都是套卡素材，
    绝不可投炉烧掉。
  * 「炼完」判定：puzi.begin 是**开炼起始**时间戳（Unix 秒，真机实测投料时 begin≈now、
    立即收卡被服务端拒绝），完成时间 = begin + smelt_time；now >= 该值即炼完可收。
    收卡后 card_id/begin 归 0（已用真机 timestamp 反推确认：begin 是起始非完成时间）。
"""
import time
import re

from . import card_plan, card_query
from .spirite_auto import plain_uin
from .constants import EP_ZCG, EP_MAINPAGE
from .auto_refine import parse_full
from . import buylog


def _face(cid: int) -> int:
    """卡面值（面额）。price==10 即 10 面卡。"""
    return int((card_plan.card_details([cid]).get(cid) or {}).get("price") or 0)


def _name(cid: int) -> str:
    return (card_plan.card_details([cid]).get(cid) or {}).get("name") or f"卡{cid}"


async def get_zcg_state(session) -> dict:
    """读取珍藏阁状态：三色魔晶计数 + 3 个免费炉的占用/炼制进度。

    注意：card_user_zcg 是 qzone 域，act=1 查询必须带**纯数字** opuin
    （session.uin 是补零格式，传补零会 code=0 但 data 空壳）。统一用 plain_uin。
    """
    r = await session.post(EP_ZCG, act=1, opuin=plain_uin(session))
    d = r.get("data") or {}
    if r.get("code") != 0 and int(d.get("code", -1) or -1) != 0:
        return {"ok": False, "code": r.get("code"), "text": r.get("message") or "读取珍藏阁失败"}
    now = time.time()
    puzi = []
    for p in (d.get("puzi") or []):
        pid = int(p.get("id") or 0)
        cid = int(p.get("card_id") or 0)
        begin = int(p.get("begin") or 0)
        smelt = int(p.get("smelt_time") or 0)
        # begin = 开炼起始时间戳（真机实测：投料时 begin≈now，立即收卡被服务端拒绝）；
        # 完成时间 = begin + smelt_time。now >= 该值即炼完可收（2026-10-04 真机反推确认）。
        finished = bool(cid > 0 and begin > 0 and begin + smelt <= now)
        state = ("空" if cid == 0 else ("炼完可收" if finished else "炼制中"))
        puzi.append({
            "id": pid, "card_id": cid,
            "name": _name(cid) if cid > 0 else "",
            "begin": begin, "smelt_time": int(p.get("smelt_time") or 0),
            "lv": int(p.get("lv") or 0), "finished": finished, "state": state,
        })
    return {
        "ok": True, "code": 0,
        "mojing": {"cmj": int(d.get("cmj") or 0), "hmj": int(d.get("hmj") or 0),
                   "zmj": int(d.get("zmj") or 0)},
        "jf": int(d.get("jf") or 0), "jlz": int(d.get("jlz") or 0),
        "kongquesi": int(d.get("kongquesi") or 0), "yueguangshi": int(d.get("yueguangshi") or 0),
        "puzi": puzi,
    }


async def _box_ten_cards(session) -> list[tuple]:
    """读卡箱（换卡箱/保险箱）里所有 10 面卡，返回 [(cid, slot, box_type)]。

    box_type: 0=换卡箱（直接 act=2 投）；1=保险箱（先 move_card 挪到换卡箱再投）。
    珍藏阁 act=2 只吃换卡箱（保险箱卡直接投会 -10 系统繁忙）。
    """
    try:
        uin = plain_uin(session)
        r = await session.post(EP_MAINPAGE, code="", uin=uin)
        st = parse_full(r.get("raw") or "")
    except Exception:
        return []
    out = []
    for box, bt in (("change", 0), ("store", 1)):
        for c in st.get(box, []):
            cid = int(c.get("id") or 0)
            if cid > 0 and _face(cid) == 10:
                out.append((cid, int(c.get("slot") or -1), bt))
    return out


_BUY_POOL: list[tuple] | None = None


async def _buy_ten_face(session) -> int | None:
    """商店买一张可买的 10 面卡（不限套卡）。返回买到的 card_id，失败返回 None。

    从卡库扫「price==10 且 theme 在 buyable_themes」的卡作为候选池，逐个试买，
    买到第一张落换卡箱的即返回（card_buy 成功会把卡放进换卡箱）。
    """
    global _BUY_POOL
    if _BUY_POOL is None:
        lib = card_plan._load()
        cl = lib["card_list"]
        buyable = card_query.buyable_themes()
        pool = []
        for c in cl:
            cid, tid = c[0], c[1]
            price = c[3] if len(c) > 3 else None
            if price == 10 and tid in buyable:
                pool.append((cid, tid))
            if len(pool) >= 50:
                break
        _BUY_POOL = pool
    from .main import card_buy  # 延迟导入，避开 main<->zcg 循环依赖
    # 箱满（无空位）时买必 -33004，先确认有空位再买（调用方 _pick_ten_card 已先 _ensure_free_change_slot 腾位）
    st0 = await _box_state(session)
    ch0 = st0.get("change", [])
    cap0 = st0.get("cap", {}).get("change", {})
    if int(cap0.get("cur", 0)) - len(ch0) < 1:
        return None
    for cid, tid in _BUY_POOL:
        br = await card_buy(cid, tid)
        # card_buy 返回 ok=(code==0)；买卡端点(actn 域) code=0 即真实成功、卡已落换卡箱。
        # ⚠️ 不能用 `int(br.get("code",-1) or -1)==0` —— code==0 时 `0 or -1` 会算成 -1，
        # 把成功误判成失败，导致买卡循环不停止、一直买到箱满(-33004)才罢手（2026-10-10 踩坑）。
        # 直接以 card_buy 的 ok 为准。
        if isinstance(br, dict) and br.get("ok"):
            return cid
        else:
            # 买失败也要记一笔（caller=zcg._buy_ten_face），否则「商店无可买」事后无法审计
            code = (br or {}).get("code") if isinstance(br, dict) else None
            note = (br or {}).get("message") if isinstance(br, dict) else f"返回{br!r}"
            buylog.log_buy("zcg._buy_ten_face", cid, tid, code=code, ok=False, note=note)
            # 换卡箱满（-33004）买不进，重试别的目标也无用，直接放弃（调用方应已腾位）
            if code == -33004:
                return None
    return None


async def _box_state(session) -> dict:
    """读主界面卡箱结构（换卡箱/保险箱/容量）。失败返回空结构。"""
    try:
        uin = plain_uin(session)
        r = await session.post(EP_MAINPAGE, code="", uin=uin)
        return parse_full(r.get("raw") or "")
    except Exception:
        return {"change": [], "store": [], "cap": {}}


async def _deposit_from_box(session, puzi_id: int, steps: list[dict]) -> bool:
    """给指定空炉投一张 10 面卡（仅从卡箱：换卡箱直投 / 保险箱挪）。不抽不买。"""
    pick = await _pick_ten_card(session, steps, auto_draw=False)
    if not pick:
        return False
    card_id, slot_id = pick
    if slot_id is None or slot_id < 0:
        return False
    return await _do_deposit(session, puzi_id, card_id, slot_id, steps)


async def _do_deposit(session, puzi_id: int, card_id: int, slot_id: int, steps: list[dict]) -> bool:
    """act=2 投卡（写动作干净四参数，仿 HAR 不带 uin 走 cookie 鉴权）；
    成功以「该炉是否开始炼（card_id 变 card_id）」判定（code 字段不可靠）。"""
    r = await session.post(EP_ZCG, act=2, card_id=card_id,
                           puzi_id=puzi_id, slot_id=slot_id, uin=None)
    rc = r.get("data") or r
    code = int(rc.get("code", r.get("code")) or -1)
    st_p = await get_zcg_state(session)
    p_now = next((x for x in st_p["puzi"] if x["id"] == puzi_id), None)
    ok = bool(p_now and p_now["card_id"] == card_id)
    steps.append({"act": "deposit", "puzi_id": puzi_id, "card_id": card_id,
                  "name": _name(card_id), "code": code, "ok": ok,
                  "text": f"炉{puzi_id} 投「{_name(card_id)}」(10面) "
                          + ("成功" if ok else f"code={code}（炉未开始炼，可能失败）")})
    return ok


async def _deposit_all_from_box(session, steps: list[dict]) -> int:
    """把卡箱里所有 10 面卡投进空炉，返回投了多少张（有多少投多少）。"""
    n = 0
    while True:
        st = await get_zcg_state(session)
        empties = [p for p in st["puzi"] if 0 < p["id"] < 10000 and p["card_id"] == 0]
        if not empties:
            break
        ok = await _deposit_from_box(session, empties[0]["id"], steps)
        if not ok:
            break
        n += 1
    return n


async def _deposit_from_buy(session, puzi_id: int, steps: list[dict]) -> bool:
    """商店买一张 10 面卡并投炉（抽卡机会用尽后的兜底，不抽只买）。"""
    await _ensure_free_change_slot(session, steps)
    bcid = await _buy_ten_face(session)
    if not bcid:
        steps.append({"act": "buy", "puzi_id": puzi_id,
                      "text": f"炉{puzi_id} 空，商店无可买 10 面卡，停止投料"})
        return False
    stb = await session.post(EP_MAINPAGE, code="", uin=plain_uin(session))
    stb_box = parse_full(stb.get("raw") or "")
    slot = next((int(c.get("slot") or -1) for c in stb_box.get("change", [])
                 if int(c.get("id") or 0) == bcid), None)
    if slot is None:
        steps.append({"act": "buy", "card_id": bcid,
                      "text": f"商店买「{_name(bcid)}」但读不到落点 slot，跳过"})
        return False
    steps.append({"act": "buy", "card_id": bcid,
                  "text": f"抽卡机会已尽，商店买「{_name(bcid)}」(10面)投炉"})
    return await _do_deposit(session, puzi_id, bcid, slot, steps)


async def _draw_and_refine_loop(session, steps: list[dict]) -> int:
    """抽卡→投炉 循环（用户 2026-10-11 定调「一键抽完，没次数才买」）：

    反复：确保换卡箱有空位 → 抽一批(type=2，抽到箱满/机会用完) → 把卡箱里的 10 面卡
    全部投进空炉（有多少投多少）→ 投完箱腾出位、若还有机会就再抽。
    直到抽卡机会用尽 / 抽不进 / 卡箱无 10 面可投为止。返回本轮投了几张。

    ⚠️ type=2 一次抽「一批」（实测一次 9 张、减 9 次），即「一键抽完占满箱」，
    没有只抽一张的参数；所以靠「抽一批→投 10 面→腾位→再抽」逼近用户意图。
    """
    deposited = 0
    max_iter = 30  # 防异常死循环
    for i in range(max_iter):
        # 抽前确保换卡箱 ≥1 空位（否则抽不进；满箱且有 10 面卡会先投炉腾位，无 10 面卡才挪杂卡去保险箱）
        await _ensure_free_change_slot(session, steps)
        st0 = await _box_state(session)
        ch0 = st0.get("change", [])
        cap0 = st0.get("cap", {}).get("change", {})
        if int(cap0.get("cur", 0)) - len(ch0) < 1:
            steps.append({"act": "draw", "text": "换卡箱满且杂卡无法腾，停止抽卡，转商店买"})
            break
        r = await session.draw_random_cards(fetch_type=2)
        n = r.get("n_cards", 0)
        remain = int(r.get("remain") or 0)
        if n <= 0:
            # 真没抽到（箱满/无机会/接口异常）；剩余机会>0 也抽不进则停
            steps.append({"act": "draw",
                          "text": f"随机抽卡未得卡（code={r.get('code')}，{r.get('message') or ''}）"
                                  + (f"，剩 {remain} 次机会但抽不进，转商店买" if remain > 0 else "，抽卡机会已用完")})
            break
        steps.append({"act": "draw", "no": i + 1,
                      "text": f"随机抽卡第 {i + 1} 批：得 {n} 张（剩 {remain} 次机会）"})
        # 抽到一批：把卡箱里的 10 面卡全部投进空炉（有多少投多少）
        got = await _deposit_all_from_box(session, steps)
        deposited += got
        if remain <= 0:
            steps.append({"act": "draw", "text": "抽卡机会已用完，停止抽卡"})
            break
        if got == 0:
            # 本批抽到的没有 10 面卡，且空炉也投不进（箱满或没空炉）→ 再抽只会空转，停手
            steps.append({"act": "draw", "text": "本批无 10 面卡可投，停止抽卡转商店买"})
            break
        # 还有机会且投了卡（箱已腾出位）→ 下一轮再抽
    return deposited


async def _ensure_free_change_slot(session, steps: list[dict]) -> None:
    """确保换卡箱 ≥1 空位（收卡前 / 抽前 / 买前）。

    用户 2026-10-11 定调：**只有「卡箱全满 且 卡箱里没有任何可炼/可卖的 10 面额卡」时，
    才挪一张杂卡（非 10 面）去保险箱腾位（无损）**。

    若箱里还有 10 面卡，应优先把它投炉(炼)消耗掉腾位，绝不挪杂卡去保险箱：
      - 有空炉 → 投炉(炼)一张 10 面卡腾位；
      - 炉满无法投炉 → 也不挪杂卡、不卖卡（避免浪费 10 面卡），交给上层等待/停止。
    只有「箱满且一张 10 面卡都没有（全是杂卡）」才挪杂卡去保险箱。
    """
    st = await _box_state(session)
    ch = st.get("change", [])
    cap = st.get("cap", {}).get("change", {})
    free = int(cap.get("cur", 0)) - len(ch)
    if free >= 1:
        return
    # 卡箱全满：先看有没有 10 面卡（可炼/可卖）
    tens = [c for c in ch if int(c.get("id") or 0) > 0 and _face(int(c.get("id") or 0)) == 10]
    if tens:
        # 有 10 面卡 → 优先投炉(炼)消耗它腾位；不挪杂卡去保险箱
        zs = await get_zcg_state(session)
        empties = [p for p in zs["puzi"] if 0 < p["id"] < 10000 and p["card_id"] == 0]
        if empties:
            ok = await _deposit_from_box(session, empties[0]["id"], steps)
            if ok:
                return
        # 有 10 面卡但炉满（无法投炉）：不挪杂卡、不卖卡，避免浪费，交给上层等待/停止
        steps.append({"act": "make_room",
                      "text": "换卡箱满且有 10 面卡，但炉满无法投炉腾位（不挪杂卡去保险箱，等炉空）"})
        return
    # 卡箱全满且无任何 10 面卡（全是杂卡）→ 挪一张杂卡去保险箱腾位
    for c in ch:
        cid = int(c.get("id") or 0)
        if cid > 0 and _face(cid) != 10:
            mr = await session.move_card(cid, int(c.get("slot") or -1), from_box=0)
            if isinstance(mr, dict) and mr.get("ok"):
                steps.append({"act": "make_room", "card_id": cid,
                              "text": f"换卡箱满且无 10 面卡，挪「{_name(cid)}」到保险箱腾 1 位"})
                return
    steps.append({"act": "make_room",
                  "text": "换卡箱满且杂卡无法腾（保险箱也满），无法腾位（请游戏内手动留 1 空位）"})


async def _pick_ten_card(session, steps: list[dict], auto_draw: bool = False) -> tuple | None:
    """找一个可投的 10 面卡，返回 (cid, slot_id)。

    仅从卡箱取：优先换卡箱直投；其次保险箱挪到换卡箱。
    （抽卡 / 商店买 由 run_zcg 的抽卡→投炉循环统一驱动，不在此处逐卡触发。）
    每次都重读卡箱，保证 slot 新鲜、不重复用同一张。返回 None = 卡箱无 10 面卡。
    """
    box = await _box_ten_cards(session)
    box.sort(key=lambda x: x[2])
    for cid, src_slot, bt in box:
        if bt == 0:
            return (cid, src_slot)
        # 保险箱 → 换卡箱（珍藏阁 act=2 只吃换卡箱，保险箱直投 -10）
        mr = await session.move_card(cid, src_slot, from_box=1)
        if isinstance(mr, dict) and mr.get("ok"):
            return (cid, int(mr.get("dest_slot") or -1))
        steps.append({"act": "move", "card_id": cid,
                      "text": f"保险箱「{_name(cid)}」挪箱失败（{(mr or {}).get('message')}），跳过"})
    return None


async def _redeploy_ten(session, prefer_other_than: int | None = None,
                        steps: list[dict] | None = None) -> bool:
    """收卡后把退回的 10 面卡重投进某个空炉（仅从卡箱，不抽不买）。
    优先投到别的空炉(让多炉都转起来)，没有别的空炉时投回刚收完的本炉。"""
    steps = steps or []
    st = await get_zcg_state(session)
    empties = [p for p in st["puzi"] if 0 < p["id"] < 10000 and p["card_id"] == 0]
    if not empties:
        return False
    target = next((p for p in empties if p["id"] != prefer_other_than), empties[0])
    return await _deposit_from_box(session, target["id"], steps=steps)


async def run_zcg(session, auto_draw: bool = True) -> dict:
    """珍藏阁炼卡一键循环（不加速）。

    流程（用户 2026-10-11 定调「一键抽完，没次数才买」）：
    1) 逐个收炼完的炉（act=9 收卡 + act=5 收魔晶），收完立即把退回的 10 面卡重投空炉；
    2) 抽卡→投炉 循环：反复「抽一批(type=2) → 把卡箱 10 面卡全投空炉 → 还有机会再抽」，
       直到抽卡机会用尽（抽到箱满/机会用完即「一键抽完」）；
    3) 抽卡机会用尽后，剩余空炉才用商店买 10 面卡投炉（绝不提前买）。
    """
    steps: list[dict] = []
    st = await get_zcg_state(session)
    if not st.get("ok"):
        return {"ok": False, "code": st.get("code"), "text": st.get("text") or "读取珍藏阁失败",
                "steps": steps}
    now = time.time()
    # 只处理 3 个免费炉（id 1..3）；10001+ 是锁定/特殊位，不碰
    normal = [p for p in st["puzi"] if 0 < p["id"] < 10000]
    finished = [p for p in normal if p["card_id"] > 0 and p["finished"]]

    # 炼制中的炉跳过，等自然炼完（不加速）
    for p in normal:
        if p["card_id"] > 0 and not p["finished"]:
            left = int(p["begin"] + p["smelt_time"] - now)
            steps.append({"act": "skip", "puzi_id": p["id"],
                          "text": f"炉{p['id']} 炼制中（剩约 {max(0, left)}s），不加速"})

    # ---- 1) 逐个收炼完的炉，收完立即重投退回的 10 面卡 ----
    # ⚠️ card_user_zcg 的 code 字段不可靠：成功也常返回 -1/-2/-3，失败也返回 -2/-3，
    # 无法靠 code 判定成败。因此「成功」一律以服务端真实状态判定：
    #   收卡 → 收后该炉是否清空（card_id 归 0）；收魔晶 → add_* 是否真到账。
    collected = 0
    for p in finished:
        # 收卡前确保换卡箱 ≥1 空位（极少数卡箱满时才需腾位）
        await _ensure_free_change_slot(session, steps)
        # 收卡 act=9（仿 HAR 不带 uin，走 cookie 鉴权）
        r = await session.post(EP_ZCG, act=9, puzi_id=p["id"], uin=None)
        rc = r.get("data") or r
        code = int(rc.get("code", r.get("code")) or -1)
        steps.append({"act": "collect", "puzi_id": p["id"], "code": code,
                      "text": f"炉{p['id']} 收卡（{p['name']}）code={code}"})
        # 收魔晶 act=5（独立于收卡，同不带 uin）
        r2 = await session.post(EP_ZCG, act=5, puzi_id=p["id"], uin=None)
        rc2 = r2.get("data") or r2
        code2 = int(rc2.get("code", r2.get("code")) or -1)
        add = {k: int(rc2.get(k) or 0) for k in
               ("add_cmj", "add_hmj", "add_zmj", "add_jl", "add_jy",
                "add_kongqueshi", "add_yueguangshi") if rc2.get(k)}
        if add:
            steps.append({"act": "claim", "puzi_id": p["id"], "code": code2, "add": add,
                          "text": f"炉{p['id']} 收魔晶到账 " +
                                  " ".join(f"{k}={v}" for k, v in add.items())})
        else:
            steps.append({"act": "claim", "puzi_id": p["id"], "code": code2,
                          "text": f"炉{p['id']} 收魔晶 code={code2}（无 add_* 到账，可能已收过）"})
        # 收卡成功以「收后该炉是否清空」判定（code 不可靠，成功也可能 -3）
        st_c = await get_zcg_state(session)
        p_c = next((x for x in st_c["puzi"] if x["id"] == p["id"]), None)
        ok_collect = bool(p_c and p_c["card_id"] == 0)
        if not ok_collect:
            steps.append({"act": "collect", "puzi_id": p["id"],
                          "text": f"炉{p['id']} 收卡后炉仍占用（服务端未清空），本次跳过重投"})
            continue
        collected += 1
        # 立即把退回的 10 面卡重投进某个空炉（保持炉不空转；优先别的空炉）
        ok_redep = await _redeploy_ten(session, prefer_other_than=p["id"], steps=steps)
        if not ok_redep:
            steps.append({"act": "deposit", "puzi_id": p["id"],
                          "text": f"炉{p['id']} 已收但无空炉/无卡可重投，留空待下次"})

    # ---- 2) 抽卡→投炉 循环（一键抽完所有机会） ----
    drawn_deposited = 0
    if auto_draw:
        drawn_deposited = await _draw_and_refine_loop(session, steps)

    # ---- 3) 抽卡机会用尽后，剩余空炉才商店买 10 面卡投炉（绝不提前买） ----
    st2 = await get_zcg_state(session)
    empty = [p for p in st2["puzi"] if 0 < p["id"] < 10000 and p["card_id"] == 0]
    bought = 0
    for p in empty:
        ok = await _deposit_from_buy(session, p["id"], steps)
        if ok:
            bought += 1

    total_deposited = drawn_deposited + bought
    return {"ok": True, "collected": collected, "deposited": total_deposited,
            "drawn_deposited": drawn_deposited, "bought": bought,
            "steps": steps, "state": st2,
            "text": f"收卡 {collected} 炉，投卡 {total_deposited} 炉（抽卡 {drawn_deposited} + 商店买 {bought}）"}


async def daily_zcg(session) -> dict:
    """日常一键入口（daily.ACTIONS）：跑 run_zcg，返回统一 item 结构。"""
    r = await run_zcg(session, auto_draw=True)
    return {"ok": bool(r.get("ok")), "code": 0,
            "text": r.get("text") or "珍藏阁炼卡完成",
            "steps": r.get("steps")}
