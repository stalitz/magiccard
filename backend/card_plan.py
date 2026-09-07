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

DATA = Path(__file__).resolve().parent.parent / "data"

_lib: dict | None = None


def _load() -> dict:
    global _lib
    if _lib is None:
        _lib = json.loads((DATA / "card_lib.json").read_text(encoding="utf-8"))
    return _lib


def card_name(cid: int) -> str:
    for c in _load()["card_list"]:
        if c[0] == cid:
            return str(c[2])
    return f"卡{cid}"


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
