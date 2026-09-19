"""卡库与炼卡编排（基于服务器在线版 card_info_v3.js 的静态数据 + mainpage 实时状态）。

数据源: https://appimg.qq.com/card/mk/card_info_v3.js （2026-09-07 抓取，2.3MB）
- card_list:        [id, theme_id, name, price, ...]            25786 张
- theme_compose_list: [theme_id, 1, target, mat1, mat2, mat3, secs]  11125 条配方
- theme_card_list:  [theme_id, theme_name, star, ..., [card ids]]    1681 个主题
- gift_list:        [id, ...]

配方语义: 目标卡 target 由 mat1+mat2+mat3 三张素材卡在炼卡炉合成，耗时 secs 秒。
素材位置: slottype1/2/3 = 0(换卡箱 changebox) 或 1(保险箱 storebox)。
"""
import json
import re
from pathlib import Path

# 「商店能买到的套卡」判据只此一处（card_query.buyable_themes），避免规则漂移
from .card_query import buyable_themes

DATA = Path(__file__).resolve().parent.parent / "data"

_lib: dict | None = None


def _load() -> dict:
    global _lib
    if _lib is None:
        _lib = json.loads((DATA / "card_lib.json").read_text(encoding="utf-8"))
    return _lib


_name_idx: dict[int, str] | None = None


def _names() -> dict[int, str]:
    global _name_idx
    if _name_idx is None:
        _name_idx = {c[0]: str(c[2]) for c in _load()["card_list"]}
    return _name_idx


def card_name(cid: int) -> str:
    return _names().get(cid) or f"卡{cid}"


def card_names(cids) -> dict[int, str]:
    idx = _names()
    return {c: (idx.get(c) or f"卡{c}") for c in cids}


_theme_meta: dict[int, dict] | None = None


def theme_meta() -> dict[int, dict]:
    """主题的「能不能从零获得」相关元数据。

    theme_card_list 字段（19 项）实测语义:
      0 theme_id / 1 name / 2 star / 3 上架时间戳(publish，可能是未来=未上架)
      9 起炼素材: "cardId#n_x|cardId|..."，即炼这套需要的 4 张**外套**素材卡
     12 theme_type（9=闪卡）

    注意: 库里**没有**权威的「下架」字段。早先用的 suf[3]（t[15]）实测恒为 0，
    判定从未生效。改用可验证的代理指标 starter：起炼素材是否全为 10 面值基础卡
    ——这正是玩家口径里「商店能直接买 10 面额基础卡」的那一档。
    """
    global _theme_meta
    if _theme_meta is not None:
        return _theme_meta
    lib = _load()
    card_by_id = {int(c[0]): c for c in lib["card_list"]}
    out: dict[int, dict] = {}
    for t in lib["theme_card_list"]:
        tid = int(t[0])
        mats: list[int] = []
        for x in str(t[9] or "").split("|"):
            x = x.strip()
            if not x:
                continue
            try:
                cid = int(x.split("#")[0])
            except ValueError:
                continue
            c = card_by_id.get(cid)
            if c:
                mats.append(int(c[3]) if c[3] is not None else 0)
        out[tid] = {
            "name": str(t[1]).strip(),
            "star": int(t[2]) if t[2] is not None else 0,
            "publish": int(t[3]) if t[3] is not None else 0,
            "mats": mats,
            # 起炼素材全是 10 面值基础卡 => 商店能直接买，可从零起步
            "starter": bool(mats) and all(p == 10 for p in mats),
            "flash": int(t[12]) == 9,
        }
    _theme_meta = out
    return out


def card_details(cids) -> dict[int, dict]:
    """批量取卡的展示元数据：卡名 / 套卡 / 星级 / 面值 / 上架日期 / 起炼素材。"""
    card_by_id = {int(c[0]): c for c in _load()["card_list"]}
    tm = theme_meta()
    out: dict[int, dict] = {}
    for cid in cids:
        c = card_by_id.get(cid)
        if not c:
            out[cid] = {"name": f"卡{cid}", "theme": "", "star": 0, "price": 0,
                        "publish": 0, "mats": [], "starter": 0, "flash": 0}
            continue
        th = tm.get(int(c[1]), {})
        out[cid] = {
            "name": str(c[2]),
            "theme": th.get("name", ""),
            "star": int(th.get("star") or 0),
            "price": int(c[3]) if c[3] is not None else 0,
            "publish": int(th.get("publish") or 0),
            "mats": th.get("mats", []),
            "starter": 1 if th.get("starter") else 0,
            "flash": 1 if th.get("flash") else 0,
        }
    return out


def build_indexes():
    lib = _load()
    card_by_id = {c[0]: c for c in lib["card_list"]}
    comp_by_target = {c[2]: c for c in lib["theme_compose_list"]}
    return card_by_id, comp_by_target


# ---------- mainpage 解析：用户卡的位置与数量 ----------

def parse_user_cards(xml_text: str) -> dict:
    """返回 {box: [(slot, card_id, ...)]} + stoves + now。
    box: changebox(slottype=0), storebox(slottype=1), stovebox(炉内，不可当素材)
    """
    out: dict = {"changebox": [], "storebox": [], "stovebox": [], "stoves": [], "now": 0}
    m = re.search(r"<hello[^>]*time=\"(\d+)\"", xml_text)
    if m:
        out["now"] = int(m.group(1))
    for bname in ("changebox", "storebox", "stovebox"):
        m = re.search(rf"<{bname}[^>]*>(.*?)</{bname}>", xml_text, re.S)
        if not m:
            continue
        for c in re.finditer(r'<card[^>]*slot="(\d+)"[^>]*id="(\d+)"[^>]*>', m.group(1)):
            out[bname].append((int(c.group(1)), int(c.group(2))))
    for st in re.finditer(r'<stove[^>]*id="(\d+)"[^>]*endtime="(\d+)"', xml_text):
        out["stoves"].append((int(st.group(1)), int(st.group(2))))
    return out


def plan_refines(xml_text: str, max_plans: int = 10) -> dict:
    """根据用户当前卡箱 + 配方表，找出「素材在手、炼出有价值目标卡」的炼卡方案。

    规则（保守版，优先避免破坏已有收藏）:
    - 素材只取 changebox(0)/storebox(1) 里的卡（炉内不可用）
    - 目标卡: 用户尚未拥有 或 拥有但素材确实富余
    - 素材: 若素材卡用户在多个箱子都持有(count>=2)才消耗最后一组的方案更稳，
      这里放宽为「该素材存在即可」，由调用方决定是否消耗唯一卡
    """
    user = parse_user_cards(xml_text)
    card_by_id, comp_by_target = build_indexes()

    # 用户各卡总数（change+store 才算可用素材）
    avail: dict[int, list[tuple[int, int]]] = {}
    for bname, st in (("changebox", 0), ("storebox", 1)):
        for slot, cid in user[bname]:
            if cid > 0:
                avail.setdefault(cid, []).append((slot, st))

    # 用户"已经拥有的卡"（三个箱都算，用于判断目标是否有价值）
    owned = set(avail) | {cid for _, cid in user["stovebox"] if cid > 0}

    now = user["now"]
    empty_stoves = [i for i, e in user["stoves"] if e <= now]
    busy_stoves = [i for i, e in user["stoves"] if e > now]

    plans = []
    for target, comp in comp_by_target.items():
        _, _, _, m1, m2, m3, secs = comp
        # 目标卡已有 => 不炼（价值低）
        if target in owned:
            continue
        mats = [m1, m2, m3]
        locs = [avail.get(m) for m in mats]
        if any(not l for l in locs):
            continue
        # 素材可能因被用于别的炼卡而变，这里保守地要求每种素材 ≥1（调用时逐卡核对）
        plans.append({
            "target": target,
            "target_name": card_name(target),
            "theme_id": comp[0],
            "secs": secs,
            "materials": [
                {"card_id": m, "name": card_name(m),
                 "slots": [{"slot": s, "slottype": t} for s, t in locs[i][:1]]}
                for i, m in enumerate(mats)
            ],
        })
    plans.sort(key=lambda p: p["secs"])
    return {
        "now": now,
        "empty_stoves": empty_stoves,
        "busy_stoves": [{"id": i, "left_secs": max(0, e - now)} for i, e in user["stoves"] if e > now],
        "plans": plans[:max_plans],
        "total_candidates": len(plans),
    }


def build_refine_payload(target: int, xml_text: str) -> dict | None:
    """为指定目标卡构造 card_stove_refine 的 POST 参数（素材取第一个可用位置）。

    返回 None 表示素材不足。调用方发送前应再核对炉位空闲。
    """
    user = parse_user_cards(xml_text)
    _, comp_by_target = build_indexes()
    comp = comp_by_target.get(target)
    if not comp:
        return None
    _, _, _, m1, m2, m3, _ = comp
    avail: dict[int, list[tuple[int, int]]] = {}
    for bname, st in (("changebox", 0), ("storebox", 1)):
        for slot, cid in user[bname]:
            if cid > 0:
                avail.setdefault(cid, []).append((slot, st))
    locs = []
    for m in (m1, m2, m3):
        slots = avail.get(m)
        if not slots:
            return None
        locs.append(slots[0])
    return {
        "targetid": target,
        "slot1": locs[0][0], "slottype1": locs[0][1],
        "slot2": locs[1][0], "slottype2": locs[1][1],
        "slot3": locs[2][0], "slottype3": locs[2][1],
        "bflash": 0, "ver": 1, "targettype": 1,
        "themeid": comp[0], "code": "",
    }


def synthesis_for_card(card_id: int, xml_text: str = "") -> dict:
    """给定一张卡，返回它所属套卡的合成路径（配方树）+ 实时持有情况。

    - 结构：套卡内所有「可作为合成目标」的卡（theme_compose_list 的 target）。
    - 每个目标卡列出其 3 张下级卡（mat1/2/3），并递归展开仍是本套目标的下级卡，
      形成合成路径；递归深度上限避免过深。
    - 实时持有：传入主页面 xml 时，统计换卡箱/保险箱各卡数量，标记每张下级卡
      「有/无」，并给出该目标卡「可否炼」（3 张下级卡齐全）；同时统计空炉数。
    """
    lib = _load()
    card_by_id = {int(c[0]): c for c in lib["card_list"]}
    card = card_by_id.get(int(card_id))
    if not card:
        return {"ok": False, "message": f"卡库里找不到卡 {card_id}"}
    theme_id = int(card[1])

    tm = theme_meta()
    theme = tm.get(theme_id, {})
    theme_name = theme.get("name") or f"套卡{theme_id}"
    bt = buyable_themes()

    # 该套所有配方：target -> (mats, secs)
    recipes: dict[int, tuple] = {}
    for c in lib["theme_compose_list"]:
        if int(c[0]) == theme_id:
            t = int(c[2])
            mats = [int(c[3]), int(c[4]), int(c[5])]
            secs = int(c[6])
            recipes[t] = (mats, secs)

    # 实时持有（仅换卡箱/保险箱，炉内不算素材）+ 空炉数 + 炉内正在合成/待收的卡
    owned: dict[int, int] = {}
    crafting: set[int] = set()
    free_stoves = 0
    if xml_text:
        uc = parse_user_cards(xml_text)
        now = uc.get("now", 0)
        for bname in ("changebox", "storebox"):
            for _, cid in uc[bname]:
                if cid > 0:
                    owned[cid] = owned.get(cid, 0) + 1
        free_stoves = sum(1 for _, e in uc.get("stoves", []) if e <= now)
        # 炉内的卡：游戏炼卡窗口里显示为「合成中」的灰态
        crafting = {cid for _s, cid in uc.get("stovebox", []) if cid > 0}

    def agg_slots(mats: list) -> tuple[dict[int, int], list[int]]:
        """同一配方的材料槽可能重复（如 [x, x, y] = 需 x×2）：
        聚合成「一张卡 ×need」，与游戏炼卡窗口的角标一致，不再重复显示。"""
        need: dict[int, int] = {}
        order: list[int] = []
        for m in mats:
            if m > 0:
                if m not in need:
                    need[m] = 0
                    order.append(m)
                need[m] += 1
        return need, order

    def mat_detail(m: int, nd: int) -> dict:
        mc = card_by_id.get(m)
        cnt = owned.get(m, 0)
        return {"card_id": m, "name": str(mc[2]) if mc else f"卡{m}",
                "price": int(mc[3]) if mc else 0,
                "need": nd, "count": cnt, "lack": max(0, nd - cnt),
                "owned": cnt >= nd,
                # 缺料 + 面值 10 + 该套未下架在售 => 可从商店直接买来补齐
                "buyable": 1 if (mc and int(mc[3]) == 10 and theme_id in bt and cnt < nd) else 0}

    # ---- 按面值分层：该套**每张卡只出现一次**（对齐游戏炼卡窗口的排布）----
    # 每张卡：角标=持有数；target=有配方；refinable=3 张下级卡都够；craft=正在炉里合成。
    # 材料只挂在卡上（前端点卡时高亮 + 单行说明），不整片铺开，杜绝同名卡反复出现。
    tiers_map: dict[int, list] = {}
    for c in lib["card_list"]:
        if int(c[1]) != theme_id:
            continue
        cid = int(c[0])
        pr = int(c[3] or 0)
        rec = recipes.get(cid)
        entry = {
            "card_id": cid, "name": str(c[2]), "price": pr,
            "secs": int(rec[1]) if rec else 0,
            "own": owned.get(cid, 0), "craft": cid in crafting,
            "target": bool(rec), "refinable": False, "mats": [],
        }
        if rec:
            need, order = agg_slots(rec[0])
            entry["mats"] = [mat_detail(m, need[m]) for m in order]
            entry["refinable"] = all(m["owned"] for m in entry["mats"])
        tiers_map.setdefault(pr, []).append(entry)

    tiers = []
    for pr in sorted(tiers_map, reverse=True):
        cs = sorted(tiers_map[pr], key=lambda e: -e["card_id"])
        tiers.append({"price": pr, "secs": max(e["secs"] for e in cs), "cards": cs})

    return {
        "ok": True,
        "theme": {"id": theme_id, "name": theme_name},
        "theme_buyable": theme_id in bt,
        "clicked_card_id": int(card_id),
        "free_stoves": free_stoves,
        "total": sum(len(t["cards"]) for t in tiers),
        "tiers": tiers,
    }
