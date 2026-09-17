"""换卡模块：按目标套卡找卡友 → 扫对方换卡箱 → 匹配「能换」→ 执行交换。

机制（2026-09-07 实测，用真实登录态验证）：
- 卡友来源：card_user_theme_list(tid) 返回一批「正在玩该主题」的玩家 node，
  node 的 uin 属性为 `&#x7C;`(|) 分隔的多个 QQ，一次固定约 80~100 人；
  服务器不限制 tid 是否下架，所以下架/绝版套也能搜到人。
- 读他人主页：card_user_mainpage + opuin=对方 QQ → 返回对方 XML，换卡箱可见
  （不需要是 QQ 好友，能浏览即可）。
- 换卡箱 changebox：
    exch="tid1,tid2,tid3,tid4"  = 对方「想收的主题」，0 表示空位；
    <card slot unlock="ts" id>  = unlock 是【锁定截止时间戳】：0 或已过 = 未锁可换。
- 交换 card_user_exchangecard：
    cmd=1, isFriend=1, frnd=<对方QQ>, uin=<我>，
    dst = "<对方卡id>_<对方换卡箱slot>_0",
    src = "<我的卡id>_<slot>_<type:0=换卡箱 1=保险箱>"
  服务器校验：
    dst 卡必须存在且未锁（锁定/被换走 → -33059「好友的卡片可能已被换走」）；
    src 面值须与 dst 相同，且 src 所属主题须在对方 exch 里
    （否则 -33023「您选择的卡片类型并不是您的好友所需要的!」）。
"""
import asyncio
import html
import json
import re
import time
from pathlib import Path

from .card_query import indexes, cname, cprice, buyable_themes
from .constants import (
    EP_MAINPAGE, EP_THEME_LIST, EP_EXCHANGE_CARD,
)

PEER_FILE = Path(__file__).resolve().parent.parent / "data" / "exchange_peers.json"


# ---------- 卡友名单（手动维护，持久化到 data/） ----------

def load_manual_peers() -> list[str]:
    try:
        if PEER_FILE.exists():
            d = json.loads(PEER_FILE.read_text(encoding="utf-8"))
            return [str(x) for x in d if str(x).strip()]
    except Exception:  # noqa: BLE001
        pass
    return []


def save_manual_peers(peers: list[str]) -> list[str]:
    clean = []
    for p in peers:
        p = str(p).strip()
        if p.isdigit() and p not in clean:
            clean.append(p)
    try:
        PEER_FILE.parent.mkdir(exist_ok=True)
        PEER_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return clean


# ---------- 卡友检索 ----------

async def theme_peers(session, tid: int) -> list[str]:
    """按主题搜「在玩该主题的卡友」，返回 QQ 号去重列表。"""
    r = await session.post(EP_THEME_LIST, uin=session.uin, tid=tid)
    if r.get("code") != 0:
        return []
    raw = r.get("raw", "")
    uins: list[str] = []
    for m in re.finditer(r'<node uin="([^"]+)"', raw):
        for u in html.unescape(m.group(1)).split("|"):
            u = u.strip()
            if u and u not in uins:
                uins.append(u)
    if session.uin and session.uin in uins:
        uins.remove(session.uin)
    return uins


async def fetch_peer_box(session, opuin: str) -> dict:
    """读一个卡友主页，解析换卡箱。

    返回 {"ok", "nick", "uin", "exch": [..int..], "cards": [{slot,id,unlock,locked}], ...}
    """
    r = await session.post(EP_MAINPAGE, code="", uin=session.uin, opuin=opuin)
    raw = r.get("raw", "")
    if r.get("code") != 0:
        return {"ok": False, "uin": opuin, "code": r.get("code"),
                "message": r.get("message", "")}
    out = {"ok": True, "uin": opuin}
    m = re.search(r'<user[^>]*nick="([^"]*)"', raw)
    out["nick"] = html.unescape(m.group(1)) if m else opuin
    m = re.search(r"<changebox([^>]*)>", raw)
    if not m:
        return {**out, "exch": [], "cards": [], "closed": False}
    head = m.group(1)
    m = re.search(r'exch="([^"]*)"', head)
    exch = [int(x) for x in m.group(1).split(",")] if m else []
    out["exch"] = exch
    now = int(time.time())
    cards = []
    m = re.search(r"<changebox.*?</changebox>", raw, re.S)
    if m:
        for c in re.finditer(r"<card ([^>]+)>", m.group(0)):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', c.group(1)))
            cid = int(attrs.get("id", "0"))
            if cid <= 0:
                continue
            ulk = int(attrs.get("unlock", "0") or 0)
            cards.append({
                "slot": int(attrs.get("slot", "0")),
                "id": cid,
                "unlock": ulk,
                "locked": ulk > 0 and ulk > now,
            })
    # 对方是否关闭了卡主页（有 stovebox 有炉子一般没关）
    out["closed"] = "<stovebox" not in raw and not cards
    out["cards"] = cards
    return out


# ---------- 我方卡箱 ----------

def parse_my_boxes(xml_text: str) -> dict:
    """解析自己的 mainpage：storebox/changebox 卡明细 + exch + now。"""
    idx = indexes()
    now = 0
    m = re.search(r'<hello[^>]*time="(\d+)"', xml_text)
    if m:
        now = int(m.group(1))
    boxes = {"storebox": [], "changebox": []}
    for bname in ("storebox", "changebox"):
        m = re.search(rf"<{bname}([^>]*)>(.*?)</{bname}>", xml_text, re.S)
        if not m:
            continue
        if bname == "changebox":
            em = re.search(r'exch="([^"]*)"', m.group(1))
            boxes["exch"] = [int(x) for x in em.group(1).split(",")] if em else []
        for c in re.finditer(r"<card ([^>]+)>", m.group(2)):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', c.group(1)))
            cid = int(attrs.get("id", "0"))
            if cid <= 0:
                continue
            info = {"slot": int(attrs.get("slot", "0")), "id": cid}
            if bname == "changebox":
                ulk = int(attrs.get("unlock", "0") or 0)
                info["locked"] = ulk > 0 and ulk > now
            ci = idx["card_by_id"].get(cid)
            if ci:
                info["name"] = str(ci[2]).strip()
                info["price"] = ci[3]
                info["theme_id"] = int(ci[1])
            else:
                info["name"] = f"卡{cid}"
                info["price"] = 0
                info["theme_id"] = 0
            boxes[bname].append(info)
    return boxes


# ---------- 扫描 ----------

async def _scan_one(session, opuin, theme_ids, price, card_ids, my_payable,
                    idx, theme_names, stats) -> dict:
    """扫一个卡友，返回命中 dst 及其可付出的 src 候选。

    服务器限流铁律（实测 2026-09-07）：读他人主页约 15 次/窗口（窗口 ~10s）
    后开始返回 -3001，冷却 ~15s 恢复。因此本函数不做并发，由调用方以
    受控速率逐个调用；-3001 时按指数退避重试。
    """
    p = None
    for attempt in range(4):  # 最多 4 次尝试
        try:
            p = await fetch_peer_box(session, opuin)
        except Exception:  # noqa: BLE001
            p = None
        if p and p.get("ok"):
            return _hit_from_box(p, opuin, theme_ids, price, card_ids,
                                 my_payable, idx, theme_names)
        code = (p or {}).get("code") if p else None
        is_limit = (code == -3001) or p is None
        if not is_limit:
            break  # 其它错误码（如封禁/无卡主页）不重试
        stats["retries"] = stats.get("retries", 0) + 1
        await asyncio.sleep(6 * (attempt + 1))  # 6s/12s/18s 退避
    return {"uin": opuin, "error": (p or {}).get("message", "请求失败")}


def _as_theme_set(theme_ids) -> set[int]:
    """把「目标套」参数归一成 set[int]，兼容传单个 int / 列表 / 元组。"""
    if isinstance(theme_ids, int):
        raw = [theme_ids]
    else:
        raw = list(theme_ids or [])
    out: set[int] = set()
    for x in raw:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.add(v)
    return out


def normalize_picked(idx, theme_ids, card_id: int = 0,
                     card_ids: list | None = None) -> set[int]:
    """把用户指定的「指定卡」归一化成集合：去重、丢非法值、剔除不属于目标套的卡。

    theme_ids 可以是单个 int（单套）或列表/集合（多套），内部统一按集合处理。
    剔除「不属于目标套」这一步很关键：卡 id 全局唯一，用户若串套（在 A 套选了卡、
    又把目标切成别的套），不剔除就会一个都扫不到，而界面上完全看不出原因。
    返回空集 = 不筛（命中目标套的全部卡）。
    """
    targets = _as_theme_set(theme_ids)
    picked: set[int] = set()
    for x in list(card_ids or []) + ([card_id] if card_id else []):
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v > 0:
            picked.add(v)
    if picked and targets:
        # 多套时：只要卡属于**任意一个**目标套就保留（跨套选卡是正常用法——
        # 在炼某套卡的卡友，其换卡箱里也会有别的套的卡）
        allowed: set[int] = set()
        for th in idx["themes"]:
            if th["tid"] in targets:
                allowed |= {int(c) for c in th["cards"]}
        picked &= allowed
    return picked


def _hit_from_box(p, opuin, theme_ids, price, card_ids, my_payable,
                  idx, theme_names):
    """从已读取的卡友换卡箱挑出命中（可跨多个目标套）。

    theme_ids: set[int]，目标套集合（支持多套——炼某套的卡友，换卡箱里也会有别的套的卡）。
    card_ids：用户点名的「指定卡」集合（可多张、可跨套）。空集 = 目标套全部卡都算命中。
    注意腾讯侧**没有「按卡搜人」的接口**——搜人只能按套（theme_peers），
    所以「找出有某张卡的人」= 在目标套的玩家池里，筛出换卡箱正放着这张卡的人。
    """
    targets = _as_theme_set(theme_ids)
    hits = []
    # 对方「想收主题」是 changebox 的 exch 属性，可为空（0 或空串=未设置）。
    # 用户 2026-09-10 实测纠正：**未设置 = 不限制**，对方任何同面额的卡都能换过去。
    exch_list = [int(x) for x in (p.get("exch") or []) if x]
    want_theme = exch_list[0] if exch_list else 0
    exch_set = set(exch_list)
    buyable = buyable_themes()
    for card in p.get("cards", []):
        if card["locked"]:
            continue
        cid = card["id"]
        ci = idx["card_by_id"].get(cid)
        if ci is None:
            continue
        tid = int(ci[1])
        if tid not in targets:
            continue
        pr = ci[3]
        if price and pr != price:
            continue
        # 指定卡（可多选）：只保留用户点名的那几张；空集 = 该套全部
        if card_ids and cid not in card_ids:
            continue
        # 可付出的 src：面额相同，且满足 exch 限制。
        # exch 限制是**套卡级**的，且只作用于「商店能买到的套卡」（套内存在 10 面额基础卡）：
        #   - 对方未设想收套（exch 为空）→ 不限制，任何同面额卡都能换；
        #   - 下架/绝版套（如纸寿千年、名家）商店买不到 → 不受限，同面额即可换；
        #   - 商店可买的套卡 → 其主题必须在对方 exch 里。
        srcs = [
            {"slot": s["slot"], "id": s["id"], "name": s["name"],
             "price": s["price"],
             "theme_name": s.get("theme_name", "")}
            for s in my_payable
            if s["price"] == pr
            and not (exch_set and s["theme_id"] in buyable
                     and s["theme_id"] not in exch_set)
        ]
        hit = {
            "slot": card["slot"],
            "id": cid,
            "name": str(ci[2]).strip(),
            "price": pr,
            # 多套扫描时，卡片属于哪个目标套要一并带回，界面才能分得清
            "theme_id": tid,
            "theme_name": theme_names.get(tid, f"主题{tid}"),
            "unlock": card["unlock"],
            "want_theme_id": want_theme,
            "want_theme_name": theme_names.get(want_theme, f"主题{want_theme}") if want_theme else "",
            "want_themes": [{"tid": t, "name": theme_names.get(t, f"主题{t}")}
                            for t in exch_list],
            "srcs": srcs[:6],
        }
        hits.append(hit)
    if not hits:
        return None
    return {
        "uin": opuin,
        "nick": p.get("nick", opuin),
        "exch": p.get("exch", []),
        "hits": hits,
    }


async def scan_peers(session, theme_id: int, peers: list[str],
                     price: int = 0, card_id: int = 0,
                     card_ids: list | None = None,
                     theme_ids: list | None = None,
                     concurrency: int = 1, stop_after: int = 0) -> dict:
    """受控速率扫描卡友，返回「对方换卡箱里有目标套卡且未锁」的命中。

    theme_id 为主套（兼容单套用法），theme_ids 可再给若干套，两者合并去重成目标套集合 ——
    现实动机：在炼某套卡的卡友，其换卡箱里往往也会有别的套的卡，一次扫描应该都捞出来。
    注意搜索范围仍是这些套各自的玩家池（腾讯无按卡搜人接口），多套只是把池子并起来。

    实测服务器约 15 人/10s 窗口限流（-3001），故采用串行 + 0.7s 间隔，
    触限后由 _scan_one 退避重试。80 人约 1~2 分钟 —— 这个速率已经贴着限流上限
    （0.7s/人 ≈ 1.43 人/秒，阈值约 1.5 人/秒），**再快只会触发退避反而更慢**，
    所以唯一的提速手段是少扫：stop_after=N 表示累计够 N 个「真正可换」的目标
    （命中里 srcs 非空的）就提前收工，默认 0 = 全部扫完。
    """
    idx = indexes()
    theme_names = {th["tid"]: th["name"] for th in idx["themes"]}
    # 目标套集合：主套 + 额外选的多套（去重）。空集合时按主套兜底（调用方保证必有主套）
    targets = _as_theme_set([theme_id] + list(theme_ids or []))
    # 指定卡（可多张、可跨套）：空集 = 不筛（目标套全部卡都算命中）
    picked = normalize_picked(idx, targets, card_id, card_ids)
    picked_names = [str(idx["card_by_id"][c][2]).strip()
                    for c in sorted(picked) if c in idx["card_by_id"]]
    # 我方可付出的卡 = 保险箱全部（storebox 无锁概念，可直接换出）
    mp = await session.post(EP_MAINPAGE, code="", opuin=session.uin)
    my = parse_my_boxes(mp.get("raw", "")) if mp.get("code") == 0 else {}
    my_payable = [
        {**c, "theme_name": theme_names.get(c.get("theme_id", 0), "")}
        for c in my.get("storebox", []) if c["id"] > 0
    ]
    stats: dict = {"retries": 0}
    t0 = time.time()
    results: list = []
    stopped_early = False
    usable_n = 0          # 真正能换的目标数（有可付卡的命中）
    for i, u in enumerate(peers):
        r = await _scan_one(
            session, u, targets, price, picked, my_payable, idx,
            theme_names, stats)
        results.append(r)
        if r and r.get("hits"):
            usable_n += sum(1 for h in r["hits"] if h.get("srcs"))
        if stop_after and usable_n >= stop_after:
            stopped_early = True
            break
        # 触限退避后，给服务器多留恢复时间
        if stats.get("retries") and i % 3 == 2:
            await asyncio.sleep(2)
        else:
            await asyncio.sleep(0.7)
    rows, errors, ok_n = [], [], 0
    for r in results:
        if r is None:
            ok_n += 1
            continue
        if "error" in r:
            errors.append(r)
            continue
        ok_n += 1
        if r.get("hits"):
            rows.append(r)
    return {
        "ok": True,
        "theme_id": theme_id,
        "theme_ids": sorted(targets),
        "target_name": theme_names.get(theme_id, f"主题{theme_id}"),
        "target_names": [theme_names.get(t, f"主题{t}") for t in sorted(targets)],
        "price": price,
        "card_ids": sorted(picked),
        "card_names": picked_names,
        "peers_total": len(peers),
        "scanned": ok_n,
        "stopped_early": stopped_early,
        "usable_n": usable_n,
        "hit_users": len(rows),
        "errors": errors[:20],
        "errors_n": len(errors),
        "elapsed": round(time.time() - t0, 1),
        "rows": rows,
        "my_exch": my.get("exch", []) if my else [],
        "my_payable_n": len(my_payable),
    }


def _theme_name(idx, tid: int) -> str:
    for th in idx["themes"]:
        if th["tid"] == tid:
            return th["name"]
    return f"主题{tid}"


async def do_exchange(session, friend: str, dst_slot: int, dst_id: int,
                      src_slot: int, src_id: int, src_type: int = 1) -> dict:
    """执行一次换卡：src(我的保险箱/换卡箱卡) -> dst(对方换卡箱卡)。"""
    dst = f"{dst_id}_{dst_slot}_0"
    src = f"{src_id}_{src_slot}_{src_type}"
    r = await session.post(
        EP_EXCHANGE_CARD, cmd=1, isFriend=1, frnd=friend,
        uin=session.uin, dst=dst, src=src,
    )
    r["ok"] = r.get("code") == 0
    return r
