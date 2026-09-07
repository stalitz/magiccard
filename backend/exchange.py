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

from .card_query import indexes, cname, cprice
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

async def _scan_one(session, opuin, theme_id, price, card_id, my_payable,
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
            return _hit_from_box(p, opuin, theme_id, price, card_id,
                                 my_payable, idx, theme_names)
        code = (p or {}).get("code") if p else None
        is_limit = (code == -3001) or p is None
        if not is_limit:
            break  # 其它错误码（如封禁/无卡主页）不重试
        stats["retries"] = stats.get("retries", 0) + 1
        await asyncio.sleep(6 * (attempt + 1))  # 6s/12s/18s 退避
    return {"uin": opuin, "error": (p or {}).get("message", "请求失败")}


def _hit_from_box(p, opuin, theme_id, price, card_id, my_payable,
                  idx, theme_names):
    """从已读取的卡友换卡箱挑出目标套卡的命中。"""
    hits = []
    want_theme = next((x for x in p.get("exch", []) if x), 0)
    for card in p.get("cards", []):
        if card["locked"]:
            continue
        cid = card["id"]
        ci = idx["card_by_id"].get(cid)
        if ci is None:
            continue
        if int(ci[1]) != theme_id:
            continue
        pr = ci[3]
        if price and pr != price:
            continue
        if card_id and cid != card_id:
            continue
        srcs = []
        if want_theme:
            srcs = [{"slot": s["slot"], "id": s["id"], "name": s["name"],
                     "price": s["price"],
                     "theme_name": s.get("theme_name", "")}
                    for s in my_payable
                    if s["price"] == pr and s["theme_id"] == want_theme]
        hit = {
            "slot": card["slot"],
            "id": cid,
            "name": str(ci[2]).strip(),
            "price": pr,
            "unlock": card["unlock"],
            "want_theme_id": want_theme,
            "want_theme_name": theme_names.get(want_theme, f"主题{want_theme}") if want_theme else "",
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
                     concurrency: int = 1) -> dict:
    """受控速率扫描卡友，返回所有「对方换卡箱里有目标套卡且未锁」的命中。

    实测服务器约 15 人/10s 窗口限流（-3001），故采用串行 + 0.7s 间隔，
    触限后由 _scan_one 退避重试。80 人约 1~2 分钟。
    """
    idx = indexes()
    theme_names = {th["tid"]: th["name"] for th in idx["themes"]}
    # 我方可付出的卡 = 保险箱全部（storebox 无锁概念，可直接换出）
    mp = await session.post(EP_MAINPAGE, code="")
    my = parse_my_boxes(mp.get("raw", "")) if mp.get("code") == 0 else {}
    my_payable = [
        {**c, "theme_name": theme_names.get(c.get("theme_id", 0), "")}
        for c in my.get("storebox", []) if c["id"] > 0
    ]
    stats: dict = {"retries": 0}
    t0 = time.time()
    results: list = []
    for i, u in enumerate(peers):
        results.append(await _scan_one(
            session, u, theme_id, price, card_id, my_payable, idx,
            theme_names, stats))
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
        "target_name": theme_names.get(theme_id, f"主题{theme_id}"),
        "price": price,
        "peers_total": len(peers),
        "scanned": ok_n,
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
