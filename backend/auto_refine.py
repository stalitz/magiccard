"""自动炼卡引擎：买底卡 → 炼卡 → 收卡 → 集齐上交，可**多套并行**。

实测确认的接口参数（2026-09-09，本账号 code=0 验证）:
  买卡   card_market_npc_buy  POST card_id=<卡id>&theme_id=<套id>
         -> code=0，返回 <card slot="1" id=...>，卡进换卡箱
  炼卡   card_stove_refine    POST targetid + slot1..3 / slottype1..3
         + bflash=0&ver=1&targettype=1&themeid=<套id>
  偷炉   card_stove_steal     POST 参数同炼卡 + opuin=<好友uin>
         ★ 必须用 steal 端点；用 refine+opuin 会被当成「在自己炉上炼」而误报 -33003（2026-09-17 抓包）
  取卡   card_stove_refinedcard_get（普通炉）/ card_stove_stealcard_get（偷炉，须带 opuin）
  上交   card_collection_add  POST theme=<套id>&themetype=0&cardtype=1&giftid=<idx9 首项>
         -32002「找不到相应的记录」= 卡没齐；成功返回「成功放入集卡册」

调度算法（**净需求 MRP**，逐层贪心 + 实时库存）:
  need[cid] = 套内每种卡要留箱的数量（1）
  按配方树深度**降序**（根 -> 叶）逐节点结算:
      net = need[cid] - 现有库存(箱内 + 炉内在炼)
      net <= 0 -> 库存已够，不炼、也不向素材传导（这就是「补缺口」的关键）
      net > 0  -> 有配方则炼 net 次、无配方则买 net 张，
                  且把 net（**不是** need）加到素材的 need 上
  每 tick: 收卡 -> 集齐则上交 -> 按深度升序炼卡（素材**就地补买**）

多套并行（2026-09-17 用户要求）:
  引擎持有一个**套卡队列**，每 tick 按轮转顺序遍历队列，把**当前空着的主炉位**
  依次分给各套能立刻提交的炉次 —— 长炉时（如魏书 10h）不会让炉子闲着，
  可以同时推进下一套。空间（换卡箱格位）是唯一的硬约束。
  · `rounds` 是**每套**的集齐上限，0 = 不限；某套达标后移出队列，队列空则停止。
  · 套间共享库存：`keep` 取各套并集（任一套的套内卡都要保底留 1 张），
    所以 A 套不会把 B 套的成品卡当素材吃掉。

买卡策略（2026-09-16 用户要求，重写）: **就地为每一炉补买，绝不预先囤货**
  只有「这一炉马上要入炉」的底卡才买，买几张用几张，卡箱里的底卡只存在于
  「买 -> 立刻入炉」的一瞬间；没有任何一炉会用到时，一张都不买。
  等到全套成品卡都炼齐了，才把集卡册还缺的底卡一次买齐 —— 那时箱位若不够，
  才是真正的空间不足（会写进日志）。
  ⚠️ 旧版把当前所有缺的底卡一次性买齐（≤6 张/tick），上层成品卡还没炼出来、
     底卡先在箱里堆成山：二十四史实测 3 个 tick 就在 18 格的换卡箱里堆了 9 张
     （元史×3/新五代史×2/辽史×2/宋史×2），把箱位堵死、引擎随后空转。

  ⚠️ 旧版用「全套从零」的 mrp 展开做 demand，已持有的成品卡只用来跳过它自己，
     却仍把它作为素材的需求全额传导给下层 —— 补缺口时会多炼近 10 倍
     （二十四史实测：90 次 / 236.5h  vs  净需求 9 次 / 19h）。
     现改为净需求：传导的是「实际要新炼的张数」。

溯源辅助（2026-09-17）:
  · `parse_state_fields` + `_log_state_delta`：把主界面所有数值状态字段跨 tick 差分，
    翻牌/上交后哪个字段跳了就说明那个「未知道具」是它（如 gold_fen=金粉、
    color_stone=五彩石 已由逐位吻合证实）。这是识别官方配置里查不到的大类（78）
    的**唯一实证手段**。
  · 翻牌响应里出现查不到名字的道具时，把**原始响应**写进日志，便于确认 server
    是否本来就带名字。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import gift as gf
from .constants import (EP_BUY, EP_COLLECTION_ADD, EP_MAINPAGE, EP_REFINE, EP_STEAL,
                        EP_USER_LIST, err_msg)

DATA = Path(__file__).resolve().parent.parent / "data"

MAX_BUY_PER_TICK = 6   # 每 tick 最多买几张底卡，防请求过频（就地买后天然远小于此值）
# 变卡目标的面额：变出一张 40 面卡，引擎就能直接拿它炼更大面额（用户 2026-09-26 定调
# 「就变自动炼卡队列里的套卡中 40 面额的卡，缺哪个变哪个」）。
# ⚠️ 目标**绝不能写死**：写死的 26117 属于 theme 1810「研精覃思」，炼的却是别的套 ——
#    变出来一张本套用不上的卡，整个环节等于白做（失败还白烧两张源卡）。
CHANGE_FACE = 40
# 注：不再有「必须留 N 格才买」的硬门槛。旧版留 2 格导致换卡箱满时引擎彻底停摆；
# 现在买卡只发生在「这一炉马上要提交」之前，买完立刻入炉（消耗 3 张），净释放 2 格，
# 所以允许把换卡箱买到刚好满 —— 前提是这一炉确实能提交出去。

_DERIV: tuple | None = None


def _lib() -> dict:
    global _DERIV
    if _DERIV is None:
        lib = json.loads((DATA / "card_lib.json").read_text(encoding="utf-8"))
        _DERIV = (
            {int(c[0]): c for c in lib["card_list"]},
            {int(c[2]): c for c in lib["theme_compose_list"]},
            {int(t[0]): t for t in lib["theme_card_list"]},
            lib,
        )
    return _DERIV[3]


def _load() -> dict:
    """兼容旧调用：返回原始 card_lib.json。"""
    return _lib()


def _maps() -> tuple[dict, dict, dict]:
    _lib()
    assert _DERIV is not None
    return _DERIV[0], _DERIV[1], _DERIV[2]


def card_name(cid: int) -> str:
    cards, _c, _t = _maps()
    c = cards.get(int(cid))
    return str(c[2]) if c else f"卡{cid}"


_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _attrs(s: str) -> dict:
    return dict(_ATTR.findall(s))


def parse_full(xml_text: str) -> dict:
    """解析 mainpage XML -> 三个箱子 + 炉位 + 容量 + 抽奖机会。

    changebox slottype=0 / storebox slottype=1 / stovebox 炉内（不可作素材）
    randchance = 取自己卡炉累积的抽奖机会数（<user randchance="N">），抽奖消耗。
    """
    out: dict = {"change": [], "store": [], "stove": [], "cap": {}, "stoves": [],
                 "now": 0, "randchance": 0}
    m = re.search(r'<user\b[^>]*\brandchance="(\d+)"', xml_text)
    if m:
        out["randchance"] = int(m.group(1))
    m = re.search(r"<hello[^>]*time=\"(\d+)\"", xml_text)
    if m:
        out["now"] = int(m.group(1))
    for tag, key in (("changebox", "change"), ("storebox", "store"), ("stovebox", "stove")):
        mm = re.search(rf"<{tag}([^>]*)>(.*?)</{tag}>", xml_text, re.S)
        if not mm:
            continue
        head = _attrs(mm.group(1))
        # 容量语义（用户确认 2026-09-09）：
        #   cur = 已开通格位数（当前真正能用的容量，可能已放满）
        #   max = 可开通上限；未开通 = max - cur 的格子不能用
        #   used = 箱内实际卡片数（按 <card> 元素数）
        # 剩余可放 = cur - used（不是 max - used！）
        out["cap"][key] = {"cur": int(head.get("cur") or 0),
                           "max": int(head.get("max") or 0)}
        free_main = free_steal = 0
        for cm in re.finditer(r"<card\s+([^>]*?)/?>", mm.group(2)):
            a = _attrs(cm.group(1))
            a["slot"] = int(a.get("slot") or -1)
            a["id"] = int(a.get("id") or 0)
            if a["id"] > 0:
                out[key].append(a)
            elif key == "stove":
                # stovebox 的空槽 id=0：slottype="steal" 的是偷炉位（本版引擎暂不占用）
                if a.get("slottype") == "steal":
                    free_steal += 1
                else:
                    free_main += 1
        out["cap"][key]["used"] = len(out[key])
        if key == "stove":
            # 空槽 = 服务端列出的 id=0 且非 steal 的格子（实测只列真正可用的空槽：
            # 3 张在炼时列出 2 个空槽=主炉上限 5，占满 5 个后一个都不列）。
            # ⚠️ 别用 cur 反推：stovebox cur=7，但主炉实际上限是 5（炉满时服务端
            # 返回「您的炼卡炉满了」）；cur 里含偷炉格 + 1 个用不了的格子。
            out["cap"][key]["free_main"] = free_main
            out["cap"][key]["free_steal"] = free_steal
    for sm in re.finditer(r"<stove[^>]*>", xml_text):
        a = _attrs(sm.group(0))
        if "id" in a:
            out["stoves"].append({"id": int(a.get("id") or 0),
                                  "endtime": int(a.get("endtime") or 0)})
    return out


# ---------------------------------------------------------------------
# 状态字段差分（溯源「未知道具」用）
# ---------------------------------------------------------------------
# 主界面把常见的可累计道具直接做成字段：gold_fen=金粉、color_stone=五彩石
# （2026-09-16 逐位吻合证实）。所以只要在「翻牌/上交」前后 diff 这些字段，
# 哪个字段正好涨了奖励的数量，那个编号就是这个字段代表的道具。
_STATE_TAGS = ("user", "new_data", "zcg")


def parse_state_fields(raw: str) -> dict:
    """抽取主界面的数值型状态字段 → {"new_data.gold_fen": 520626, ...}。"""
    out: dict = {}
    for tag in _STATE_TAGS:
        m = re.search(rf"<{tag}\b([^>]*)>", raw or "")
        if not m:
            continue
        for k, v in _attrs(m.group(1)).items():
            if v.isdigit():
                out[f"{tag}.{k}"] = int(v)
    return out


def diff_state(prev: dict, cur: dict) -> list[dict]:
    """两个快照的差异，按变化量绝对值降序（用于把奖励对上号）。"""
    out = []
    for k, v in (cur or {}).items():
        p = (prev or {}).get(k)
        if p is not None and p != v:
            out.append({"field": k, "from": p, "to": v, "delta": v - p})
    out.sort(key=lambda x: -abs(x["delta"]))
    return out


# ---------------------------------------------------------------------
# 上交（放入集卡册）响应解析
# ---------------------------------------------------------------------
# ⚠️ `card_collection_add` 的**成功响应结构尚未抓包确认**（2026-09-17 用户反馈：
#    「上交集卡册后没显示获得的奖励，还会得一张卡」——说明响应里确实带东西，
#    只是旧代码把整个响应丢掉了）。这里的解析写成**宽容提取**，不假结构：
#      · 奖励：扫出所有 `大类_编号_数量` 三段式串（与翻牌/礼包同格式）→ item_dict
#      · 卡  ：扫出所有 <card ... id="N"> 元素 → 卡库取名
#    解析不到东西时把原始响应写进日志，下一次上交就能看到真实结构再收紧。
_REWARD_RE = re.compile(r'(?<![\d_])(\d{1,3})_(\d{1,7})_(\d{1,9})(?![\d_])')


def parse_collect_rewards(raw: str, own_ids: set | None = None) -> dict:
    """从 card_collection_add 响应里提取「获得的奖励」与「额外得到的卡」。"""
    from . import item_dict as it

    parts = ["_".join(m) for m in _REWARD_RE.findall(raw or "")]
    items = it.decode(",".join(parts)) if parts else []
    cards = []
    for m in re.finditer(r"<card\b([^>]*)/?>", raw or ""):
        a = _attrs(m.group(1))
        cid = int(a.get("id") or 0)
        if cid > 0:
            cards.append({"id": cid, "name": card_name(cid),
                          "price": int(cards_price(cid)),
                          "mine": bool(own_ids and cid in own_ids)})
    return {"items": items, "cards": cards, "raw": (raw or "")[:600]}


def cards_price(cid: int) -> int:
    cards, _c, _t = _maps()
    c = cards.get(int(cid))
    return int(c[3] or 0) if c else 0


# ---------------------------------------------------------------------
# 单套卡的配方计划（纯计算，无副作用）
# ---------------------------------------------------------------------
class ThemePlan:
    """一套卡的静态配方 + 净需求计算。"""

    def __init__(self, theme_id: int):
        cards, comp, themes = _maps()
        t = themes.get(int(theme_id))
        if t is None:
            raise KeyError(f"套卡 {theme_id} 不在卡库（data/card_lib.json）")
        self.tid = int(theme_id)
        self.cards = cards
        self.comp = comp
        self.name = str(t[1]).strip()
        self.star = int(t[2] or 0)
        self.prize = int(t[6] or 0)
        self.ids = [int(x) for x in (t[11] or []) if x]
        gifts = str(t[9] or "").split("|")
        self.giftid = gifts[0].strip() if gifts and gifts[0].strip() else "0"

        from .economy import mrp  # 复用 MRP 展开

        def price_of(cid: int) -> int:
            c = self.cards.get(cid)
            return int(c[3]) if c and c[3] is not None else 0

        self.base_need, self.runs, self.ok, self.demand = mrp(
            self.ids, self.comp, price_of)
        # 配方树深度（升序 = 先炼低层；降序 = 先算净需求）
        depth: dict[int, int] = {}

        def d(cid: int, stack: set) -> int:
            if cid in depth:
                return depth[cid]
            c = self.comp.get(cid)
            if not c or cid in stack:
                depth[cid] = 0
                return 0
            stack.add(cid)
            v = 1 + max((d(int(m), stack) for m in (c[3], c[4], c[5]) if m), default=0)
            stack.discard(cid)
            depth[cid] = v
            return v

        # 从套内卡递归下行，把整条素材链（含叶子）都纳入 depth
        for cid in self.ids:
            d(cid, set())
        self.depth = depth
        self.rounds = 0          # 本套已集齐并上交的次数
        self.done = False        # 达到 rounds 上限

    def cname(self, cid: int) -> str:
        return card_name(cid)

    def net_plan(self, stock: Counter, pending: Counter) -> tuple[dict, dict]:
        """净需求：返回 (要炼的 {卡id: 次数}, 要买的 {底卡id: 张数})。

        库存（箱内 + 炉内在炼）从总需求里抵扣；只有**实际要新炼/新买**的数量
        才向素材层传导 —— 已有的成品卡不会再把它那份素材需求全额压到最底层。
        """
        need: dict[int, int] = {cid: 1 for cid in self.ids}
        runs: dict[int, int] = {}
        buy: dict[int, int] = {}
        for cid in sorted(self.depth, key=lambda c: -self.depth[c]):
            n = need.pop(cid, 0)
            if n <= 0:
                continue
            net = n - (stock.get(cid, 0) + pending.get(cid, 0))
            if net <= 0:
                continue
            c = self.comp.get(cid)
            if c:
                runs[cid] = net
                for m in (c[3], c[4], c[5]):
                    if m:
                        need[int(m)] = need.get(int(m), 0) + net
            else:
                buy[cid] = net
        return runs, buy

    def plan_summary(self, stock: Counter | None = None,
                     pending: Counter | None = None) -> dict:
        """静态视图：不给库存时按「全套从零」展示（等价旧 mrp 口径）。"""
        if stock is None:
            runs, buy = dict(self.runs), dict(self.base_need)
            ok = bool(self.ok)
        else:
            runs, buy = self.net_plan(stock, pending or Counter())
            ok = True
        order = sorted(runs, key=lambda c: self.depth.get(c, 0))
        secs = sum(runs[c] * int(self.comp[c][6]) for c in runs)
        return {
            "tid": self.tid, "name": self.name, "star": self.star,
            "prize": self.prize, "giftid": self.giftid,
            "ok": ok,                      # 底卡是否全为商店面值10
            "cards": len(self.ids),
            "recipes": sum(runs.values()),  # 还需炼的次数
            "base_cards": sum(buy.values()),  # 还需买的底卡张数
            "cost": sum(buy.values()) * 20,   # 金币
            "hours": round(secs / 3600, 1),
            "need_slots": len(self.ids),
            "rounds": self.rounds,
            "buylist": {self.cname(c): n for c, n in buy.items()},
            "runlist": [{"id": c, "name": self.cname(c),
                         "price": int(self.cards[c][3] or 0), "times": runs[c],
                         "secs": int(self.comp[c][6])} for c in order],
        }


def pick_change_target(plans: dict, queue: list, stock: Counter | None = None,
                       pending: Counter | None = None,
                       keep: set | None = None) -> dict | None:
    """（纯计算）按「在炼队列 + 实时库存」挑下一步该变哪张卡 —— 变卡目标**不许写死**。

    用户 2026-09-26 定调：「不要指定某个卡，就变自动炼卡队列里的套卡中 40 面额的卡，
    缺哪个变哪个 —— 变卡是为了不用炼 40 面额，直接变了炼更大面额的卡。」
    此前写死 26117（theme 1810「研精覃思」），炼的却是别的套，变出来的卡本套用不上。

    挑法（严格 → 兜底，逐级放宽；都不满足返回 None = 本轮不动箱里的礼物卡）：
      ① 套内卡 ∩ 引擎净需求里**要炼/要买**的卡 ∩ **面额 CHANGE_FACE(40)**
      ② ①为空时放宽到「套内卡 ∩ 净需求」（那一套里没有 40 面卡可补）
    同分时：需求张数多的优先，再按队列顺序（多套并行时不让第一套独吞）。
    """
    if not queue:
        return None
    if keep is None:
        keep = {cid for t in queue for cid in plans[t].ids}
    stock = stock or Counter()
    pending = pending or Counter()
    cards = plans[queue[0]].cards                    # 全局卡表，各套共用

    def face(cid: int) -> int:
        c = cards.get(int(cid))
        return int(c[3] or 0) if c else 0

    strict, loose = [], []                           # (需求张数, 队列序, cid)
    for order, tid in enumerate(queue):
        runs, buy = plans[tid].net_plan(stock, pending)
        for cid, n in {**runs, **buy}.items():
            if cid not in keep or not int(n or 0):
                continue                             # 必须是套内卡，且引擎确实还缺
            row = (int(n), order, int(cid))
            (strict if face(cid) == CHANGE_FACE else loose).append(row)
    cands = sorted(strict or loose, key=lambda x: (-x[0], x[1], x[2]))
    if not cands:
        return None
    n, order, cid = cands[0]
    tid = queue[order]
    plan = plans[tid]
    return {"dst": cid, "name": plan.cname(cid), "face": face(cid),
            "times": n, "tid": tid, "theme": plan.name,
            "target": f'{plan.cname(cid)}（{plan.name} {face(cid)}面，还差 {n}）'}


# ---------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------
class AutoRefine:
    """自动炼卡循环，支持一个**套卡队列**（多套并行）。"""

    def __init__(self, session, theme_ids, buy=True, max_rounds: int = 0):
        self.s = session
        if isinstance(theme_ids, (int, str)):
            theme_ids = [theme_ids]
        tids = [int(t) for t in theme_ids]
        self.tids = list(dict.fromkeys(tids))          # 去重保序
        self.plans = {t: ThemePlan(t) for t in self.tids}
        self.queue = [t for t in self.tids]
        self.buy = buy
        self.max_rounds = max_rounds          # 0 = 不限（**每套**的上限）
        self.running = False
        self.interval = 45.0
        self.log: list[dict] = []
        self.stats = {"rounds": 0, "refined": 0, "bought": 0, "collected": 0,
                      "gold": 0, "errors": 0}
        self.randchance = 0          # 取自己卡炉累积的抽奖机会数（status 透传）
        self.live_plan: dict | None = None   # 主套实时净需求（兼容旧面板）
        self.live_plans: list[dict] = []     # 队列里每套的实时净需求
        self._stock: Counter = Counter()     # 最近一次读到库存（status 里算实时净需求用）
        self._pending: Counter = Counter()
        self.state_delta: list[dict] = []    # 最近一次跨 tick 状态字段差分
        self.last_pai_raw = ""               # 最近一次翻牌的原始响应（溯源用）
        self.last_collect: dict | None = None  # 最近一次上交的奖励解析
        self._prev_fields: dict = {}         # 上一 tick 的状态字段快照
        self._pai_happened = False           # 上一 tick 是否翻过牌（决定是否记差分）
        self._rr = 0                         # 轮转起点
        self._task: asyncio.Task | None = None
        # 偷炉（借好友空炉炼自己的卡）：用好友账号(opuin)发起 card_stove_steal。
        # ★ 端点必须是 steal（refine+opuin 会被当成「在自己炉上炼」→ 误报 -33003）。
        # 素材仍取我自己的卡箱槽位；炼成后卡挂在该好友炉上，取卡走
        # card_stove_stealcard_get + opuin。好友列表取 card_user_list
        # （194 人，顺序=游戏界面顺序，非卡友）。
        self.steal_enabled = True            # 偷炉总开关
        self.steal_search_depth = 12         # 从最后一名好友往回找的深度
        self._friends = None                 # 好友 uin 列表（缓存）
        self._friend_cache: dict = {}         # uin -> (empty_count, ts)：好友炉空槽缓存
        self._friend_fail: dict = {}          # uin -> ts：最近偷炉失败（含好友相关错误），暂不重试
        self._steal_cooldown_until = 0.0     # 全局冷却（服务端报“我炉满”则冷却一段时间）
        # 礼物箱 → 变卡（可选环节，勾选才做）。2026-09-21 用户定调：
        # 不做独立自动化，做成炼卡流程里的一步 —— 缺素材、准备买卡之前先看礼物箱。
        self.gift_drain = False              # 勾选项
        self.gift_dst = gf.DEFAULT_CHANGE_DST  # 变卡目标（默认 26117 = 40 面卡）
        self.gift_upper = gf.DEFAULT_UPPER   # 箱件数 ≥ 此值开始收+变
        self.gift_lower = gf.DEFAULT_LOWER   # 降到 ≤ 此值停手
        self.gift_last: dict | None = None   # 最近一次本环节的结果（status 透传）
        # 卡箱里已有的礼物套 48 面卡 → 优先就地变卡（不必先收礼）。
        # 用户 2026-09-26 反馈「现在你完全不用卡箱里的 48 面额卡」：以前箱里躺着
        # 礼物送的 48 面卡，变卡链路却只认 freegift_take 刚收出来的那张。
        # 二者本来就同一套（theme=487 手工织锦），箱里的卡同样能当 srcid。
        # 这一项**不受 gift_drain 开关限制**——没勾收礼变卡，也该把箱里的卡用掉。
        self.box_gift_change = True
        self.box_gift_last: dict | None = None   # 最近一次结果（status 透传）
        self._box_gift_fail = 0                  # 连续「一张都没变成功」的次数
        self._box_gift_cooldown = 0.0            # 连败后的静默期（失败也烧源卡，别硬试）
        self.last_steal: dict | None = None   # 最近一次偷炉尝试结果
        self.steal_blocked: str = ""          # 本轮「能偷却没偷」的原因，供面板显示
        # 常去好友（用户指定，如 983688972）：若有值，选友时**优先**用它，
        # 仅当它被失败缓存或炉上挂着我未取回的卡时才退回「从最后往回扫」。
        self.preferred_steal_friend: str | None = None
        # 套间共享的素材保护：任一套的套内卡都要保底留 1 张
        self._keep: dict[int, int] = {}
        for p in self.plans.values():
            for cid in p.ids:
                self._keep[cid] = 1

    # ---------- 兼容旧调用 ----------
    @property
    def tid(self) -> int:
        return self.queue[0] if self.queue else self.tids[0]

    @property
    def name(self) -> str:
        return self.plans[self.tids[0]].name

    @property
    def ids(self) -> list:
        return self.plans[self.tids[0]].ids

    def plan_summary(self, stock: Counter | None = None,
                     pending: Counter | None = None, tid: int | None = None) -> dict:
        p = self.plans[tid or self.tids[0]]
        return p.plan_summary(stock, pending)

    def net_plan(self, stock: Counter, pending: Counter, tid: int | None = None):
        p = self.plans[tid or self.tids[0]]
        return p.net_plan(stock, pending)

    @staticmethod
    def _stock_from(st: dict | None) -> Counter:
        """从 mainpage 解析结果里数库存（换卡箱 + 保险箱，不含炉内在炼的）。"""
        c: Counter = Counter()
        for k in ("change", "store"):
            for x in (st or {}).get(k) or []:
                c[int(x["id"])] += 1
        return c

    def _pick_change_dst(self, stock: Counter | None = None) -> dict | None:
        """自动挑「变卡目标」= `pick_change_target`（本引擎的队列 + 最近库存）。"""
        return pick_change_target(self.plans, self.queue,
                                  self._stock if stock is None else stock,
                                  self._pending, set(self._keep))

    def _dst_useful(self, dst: int) -> bool:
        """`dstid` 是不是当前队列**用得上的卡**（套内卡或配方链上任意一层）。

        卡箱礼物卡自动变卡的门槛：变卡约 50% 成功、失败也烧掉一张源卡，若变出来的
        卡不是这一套要的，那就是拿礼物卡换废卡（2026-09-26 真机实测）。
        队列为空 → 返回 False（现在没在炼卡，先不动箱里的礼物卡）。
        """
        if not self.queue:
            return False
        for tid in self.queue:
            p = self.plans[tid]
            if dst in p.ids or dst in p.depth:
                return True
        return False

    # ---------- 日志 ----------
    def _log(self, action: str, ok: bool, detail: str) -> None:
        self.log.append({"ts": time.time(), "action": action, "ok": ok, "detail": detail})
        if not ok:
            self.stats["errors"] += 1
        self.log[:] = self.log[-200:]

    def _log_state_delta(self) -> None:
        """翻牌/上交之后，把主界面数值字段的变化记一行——用来给「未知道具」对号。"""
        if not self._pai_happened or not self.state_delta:
            return
        top = [d for d in self.state_delta if abs(d["delta"]) > 0][:6]
        if not top:
            return
        txt = "，".join(f'{d["field"]} {d["from"]}→{d["to"]}（{d["delta"]:+d}）' for d in top)
        self._log("状态变化", True, txt)

    # ---------- 单步 ----------
    async def tick(self) -> list[dict]:
        steps: list[dict] = []
        mp = await self.s.post(EP_MAINPAGE, code="", opuin=self.s.uin)
        if mp.get("code") != 0:
            self._log("读取卡箱", False, f"code={mp.get('code')}")
            return steps
        raw0 = mp.get("raw", "")
        st = parse_full(raw0)
        self.randchance = st.get("randchance", 0)
        # 跨 tick 差分：上一 tick 翻过牌 → 此刻的变化就是那些奖励的去向
        cur_fields = parse_state_fields(raw0)
        self.state_delta = diff_state(self._prev_fields, cur_fields)
        self._log_state_delta()
        self._prev_fields = cur_fields

        # 1) 收卡：flag=2 已完成
        self._pai_happened = False
        for c in st["stove"]:
            if int(c.get("flag") or 0) == 2 and c["id"] > 0:
                slot = c["slot"]
                opu = c.get("opuin")
                is_self = not (opu and str(opu) != str(self.s.uin))
                if not is_self:
                    # 好友炉上炼的卡（偷炉成功收回）：必须带好友 uin 走 steal 端点
                    r = await self.s.get_steal_card(slot, str(opu))
                else:
                    r = await self.s.get_refined_card(slot)
                nm = card_name(c["id"])
                steps.append({"action": "收卡", "ok": r.get("ok"), "detail": f"{nm}: {r.get('message')}"})
                self._log("收卡", bool(r.get("ok")), f"{nm} {r.get('message')}")
                if not r.get("ok"):
                    continue
                self.stats["refined"] += 1
                # 取「自己炉子」的卡 → 获得 1 次翻牌机会，立即翻一次（5选1奖，不翻就浪费）
                if is_self:
                    dr = await self.s.open_pai()
                    self._pai_happened = True
                    self.last_pai_raw = dr.get("raw") or ""
                    detail = dr.get("message") or ""
                    # 有查不到名字的道具 → 把原始响应带进日志，确认 server 是否本来就带名字
                    unknown = [i for i in (dr.get("items") or []) if not i.get("name")]
                    if unknown:
                        detail += f"｜原始响应 {self.last_pai_raw[:220]}"
                    steps.append({"action": "翻牌", "ok": dr.get("ok"), "detail": detail})
                    self._log("翻牌", bool(dr.get("ok")), detail)

        # 1.4) 收卡刚把新的成品卡放进箱子 —— 变卡目标必须按**最新**缺口挑：用 tick
        #      开头那份库存会低估（挑中一张其实已经收到的卡，白烧一张源卡）。
        #      这一步顺带给第 2 步提供数据，所以闭合时没有额外请求。
        rd_now = None
        if self.box_gift_change or self.gift_drain:
            rd_now = await self._read_state()

        # 1.45) 卡箱里已有的礼物套 48 面卡 → 就地变卡（不收礼，见 _drain_box_gift）
        if self.box_gift_change:
            await self._drain_box_gift(steps, rd_now[1] if rd_now else None)

        # 1.5) 礼物箱快满 → 收礼 + 变卡成目标卡（可选环节）
        #      变出来的 40 面卡直接进卡箱 → 下面第 2 步重读状态时就当素材了，
        #      于是本来要「买 10 面卡 + 自己炼」的那一步可以直接省掉。
        if self.gift_drain:
            await self._gift_drain(steps)

        # 2) 重新取状态（收卡后库存变了）。mainpage 偶发 code=-1（服务端抖动），
        #    重试一次再放弃，避免白丢一个 tick。
        rd = rd_now or await self._read_state() or await self._read_state()
        if rd is None:
            self._log("读取卡箱", False, "重读失败，本轮跳过")
            return steps
        st, stock, slots, pending = rd
        self._stock, self._pending = stock, pending
        # 面板显示实时净需求（还需炼几次 / 还差几张底卡），别显示「全套从零」的静态数字
        self.live_plans = [self.plans[t].plan_summary(stock, pending) for t in self.queue]
        self.live_plan = self.live_plans[0] if self.live_plans else None

        # 3) 集齐则上交（按队列逐个检查：每套各自上交、各自计轮次）
        progressed = False
        for tid in list(self.queue):
            plan = self.plans[tid]
            if not all(stock[c] >= 1 for c in plan.ids):
                continue
            r = await self.s.post(EP_COLLECTION_ADD, themetype=0, giftid=plan.giftid,
                                  cardtype=1, theme=tid)
            code = r.get("code")
            ok = code == 0
            msg = ("成功放入集卡册" if ok
                   else ((r.get("message") or "").strip() or err_msg(code)))
            detail = f"{plan.name}: {msg}"
            if ok:
                self.stats["collected"] += 1
                plan.rounds += 1
                self.stats["rounds"] = sum(p.rounds for p in self.plans.values())
                # 解析「获得的奖励」与「额外得到的卡」（响应结构未抓包，宽容提取）
                got = parse_collect_rewards(r.get("raw") or "", set(plan.ids))
                self.last_collect = {"theme": plan.name, "message": msg, **got}
                extra = [c for c in got["cards"] if not c["mine"]]
                if got["items"]:
                    detail += "｜奖励 " + "、".join(i["text"] for i in got["items"])
                if extra:
                    detail += "｜得卡 " + "、".join(
                        f'{c["name"]}（{c["price"]}面值）' for c in extra[:6])
                if not got["items"] and not extra:
                    # 什么都没提出来 → 把原始响应留下来，下次收紧解析
                    detail += f"｜响应原文 {got['raw'][:220]}"
                if self.max_rounds and plan.rounds >= self.max_rounds:
                    plan.done = True
                    if tid in self.queue:
                        self.queue.remove(tid)
                    self._log("完成", True, f"{plan.name} 已达 {self.max_rounds} 轮，移出队列")
            steps.append({"action": "上交集卡册", "ok": ok, "detail": detail})
            self._log("上交", ok, detail)
            progressed = True
        if progressed:
            # 上交消耗了整套卡，库存变了；下一 tick 重新读即可
            return steps

        # 4) 按轮转顺序把**空着的主炉位**分给各套（多套并行，长炉时不让炉位闲着）
        await self._refine_all(st, stock, slots, pending, steps)

        # 5) 收尾：某套该炼的都炼完了（成品卡全在箱内或在炉），只差商店底卡
        #    → 只给这一套把底卡买齐。买来只为放进集卡册，不再入炉消耗。
        if self.buy:
            for tid in list(self.queue):
                plan = self.plans[tid]
                miss_comp = [c for c in plan.ids if c in plan.comp
                             and stock.get(c, 0) + pending.get(c, 0) < 1]
                miss_base = [c for c in plan.ids if c not in plan.comp and stock.get(c, 0) < 1]
                if miss_comp or not miss_base:
                    continue
                for cid in miss_base:
                    if self._free_change(st) <= 0:
                        self._log("买卡", False,
                                  f"卡箱 0 空位，{card_name(cid)} 买不进来："
                                  f"{plan.name} 集卡册还差 {len(miss_base)} 张底卡 —— 这是真的空间不足")
                        break
                    r = await self.s.post(EP_BUY, card_id=cid, theme_id=tid)
                    ok = r.get("code") == 0
                    if ok:
                        self.stats["bought"] += 1
                        stock[cid] += 1
                        st["cap"]["change"]["used"] = st["cap"]["change"].get("used", 0) + 1
                    msg = r.get("message") or err_msg(r.get("code"))
                    self._log("买卡", ok, f"{card_name(cid)}（{plan.name} 补集卡册） {msg}")
                    steps.append({"action": "买卡", "ok": ok,
                                  "detail": f"{card_name(cid)}（{plan.name} 补集卡册） {msg}"})
                    if not ok:
                        break
        return steps

    async def _refine_all(self, st: dict, stock: Counter, slots: dict,
                          pending: Counter, steps: list[dict]) -> None:
        """把空着的主炉位按轮转顺序分给队列里各套能立刻提交的炉次；
        主炉满时仍要尝试偷炉（借好友空炉再并行一炉）。"""
        free_stove = st["cap"].get("stove", {}).get("free_main", 0)
        free_steal = st["cap"].get("stove", {}).get("free_steal", 0)
        can_steal = bool(self.steal_enabled and free_steal > 0
                         and self._steal_cooldown_until < time.time())
        self.steal_blocked = ""              # 每 tick 重算
        # ★ 2026-09-18 修：旧版这里写的是 `if free_stove <= 0 or not self.queue: return`，
        #   于是主炉一满（free_main=0）整个函数立刻返回，末尾的偷炉分支**永远走不到** ——
        #   而主炉满恰恰是常态（实测 free_main=0 / free_steal=1，偷炉位空着没人用），
        #   表现出来就是「自动炼卡从来没偷过炉」。
        #   现在：主炉空位与偷炉位各自独立，只要还有一边可用就继续。
        if not self.queue or (free_stove <= 0 and not can_steal):
            return
        # planned = 各套并集（「会再产出」的卡，允许被当素材消耗）
        planned: set = set()
        plans_net: dict[int, tuple] = {}
        for tid in self.queue:
            runs, buy = self.plans[tid].net_plan(stock, pending)
            plans_net[tid] = (runs, buy)
            planned |= set(runs) | set(buy)
        bought = 0
        # 轮转：从 _rr 开始，保证每套都有机会拿到炉位，不被第一套独吞
        order = self.queue[self._rr % len(self.queue):] + self.queue[:self._rr % len(self.queue)]
        self._rr += 1
        # **按炉次交错**展开：每套依次出一个炉次（各自深度升序），轮流取。
        # 不能「一套把自己的炉次全提交完再轮到下一套」——那第一套会把空炉位全吃光，
        # 多套并行就退化成单套串行了（2026-09-17 离线验证抓到）。
        buckets = {tid: sorted(plans_net.get(tid, ({}, {}))[0],
                               key=lambda c: self.plans[tid].depth.get(c, 0))
                   for tid in order}
        seq: list[tuple[int, int]] = []
        while any(buckets.values()):
            for tid in order:
                if buckets[tid]:
                    seq.append((tid, buckets[tid].pop(0)))
        taken: set = set()                    # 本 tick 已被主炉/偷炉占用的目标，防重复提交
        for tid, target in seq:
            if free_stove <= 0:
                break
            # 素材必须此刻在箱里（炉内在炼的不能当素材），套内卡不能被动到清空，
            # 缺的 10 面值底卡就地买 —— 与偷炉共用 _prepare_turn，判定只有一份。
            prep = await self._prepare_turn(tid, target, st, stock, slots, pending,
                                            planned, bought, steps, tag="炼卡")
            if prep is None:
                continue
            st, stock, slots, pending, need_cnt, nb = prep
            bought += nb
            plan = self.plans[tid]
            res = self._payload(plan, target, need_cnt, slots)
            if res is None:
                continue
            payload, picked = res
            r = await self.s.post(EP_REFINE, **payload)
            ok = r.get("code") == 0
            nm = plan.cname(target)
            if ok:
                self.stats["refined"] += 1
                free_stove -= 1
                for m, n in need_cnt.items():
                    stock[m] -= n
                    for _ in range(n):
                        if slots.get(m):
                            slots[m].pop(0)
                # 素材里来自换卡箱的那几张已经烧掉了 → 同步占用数，
                # 否则本 tick 后面几炉会误判「换卡箱 0 格」而白白空转一轮。
                used = st["cap"].get("change", {}).get("used", 0)
                st["cap"]["change"]["used"] = max(
                    0, used - sum(1 for _sl, _st in picked if _st == 0))
                pending[target] += 1
                taken.add(target)
            self._log("炼卡", ok, f"{nm}（{plan.name}） {r.get('message') or err_msg(r.get('code'))}")
            steps.append({"action": "炼卡", "ok": ok,
                          "detail": f"{nm}（{plan.name}） {r.get('message') or err_msg(r.get('code'))}"})

        # ---------- 偷炉：用好友空炉再并行 1 张 ----------
        # 条件：开启偷炉、偷炉槽空闲、未被全局冷却、尚有未提交的炉次。
        # 注意这里**不要求主炉有空位**（主炉满时也要偷，见函数开头注释）。
        if can_steal and st["cap"].get("stove", {}).get("free_steal", 0) > 0:
            # 与主炉走**同一个** _prepare_turn（含「就地买底卡」）。
            # 旧版这里自写了一份「素材必须已经躺在箱里」的判定，而底卡平时零库存
            # （主炉路径就地为每炉补买、用完即烧）→ 候选永远被否 → 「指定了好友也从没偷过」
            # （2026-09-18 定位：live 库存下 1652 的三个候选被同一理由全部跳过）。
            stole = False
            for tid, target in seq:
                if target in taken:
                    continue                        # 主炉已占，别在好友炉上重复炼同一张
                prep = await self._prepare_turn(tid, target, st, stock, slots, pending,
                                                planned, bought, steps, tag="偷炉")
                if prep is None:
                    continue
                st, stock, slots, pending, need_cnt, nb = prep
                bought += nb
                # 买卡 + 重读之后世界可能变了：偷炉槽被别人占了 / 我这轮进了全局冷却
                if st["cap"].get("stove", {}).get("free_steal", 0) <= 0:
                    self.steal_blocked = "偷炉位已被占用"
                    break
                friend = await self._pick_steal_friend(st)
                if not friend:
                    self.steal_blocked = "没找到可用好友（炉满或挂着我没取的卡）"
                    break
                plan = self.plans[tid]
                await self._refine_steal(st, tid, target, need_cnt, plan, slots, friend,
                                         stock, pending, steps)
                taken.add(target)                   # 防重复提交
                stole = True
                break
            if not stole and not self.steal_blocked:
                rest = [t for _tid, t in seq if t not in taken]
                self.steal_blocked = ("没有待提交的炉次（都已在炉上）" if not rest
                                      else "候选素材没备齐（底卡买不到/换卡箱没格，见日志）")

    @staticmethod
    def _free_change(st: dict) -> int:
        """换卡箱剩余可放格数 = 已开通格 cur - 已放卡数 used（max-cur 是未开通格）。"""
        c = st["cap"].get("change", {})
        return c.get("cur", 0) - c.get("used", 0)

    async def _prepare_turn(self, tid: int, target: int, st, stock, slots, pending,
                            planned: set, bought: int, steps: list[dict],
                            tag: str = "炼卡"):
        """备齐一个炉次的素材（缺的 10 面值底卡**就地买**），返回
        `(st, stock, slots, pending, need_cnt, n_bought)`；不可提交返回 None。

        ★ 主炉与偷炉必须共用这一份逻辑（2026-09-18 修）：
          偷炉原先自己写了一份「素材必须已经躺在箱里、且不许买」的判定，而底卡
          平时是**零库存**的（主炉路径就地为每炉补买，用完即烧）—— 于是偷炉的候选
          永远被「需就地买 N 张素材」这一条否掉，表现为「指定了好友也从来没偷过」。
          离线复现：live 库存下 1652 的三个候选全部被同一理由跳过。
        """
        plan = self.plans[tid]
        comp = plan.comp[target]
        need_cnt = Counter(int(m) for m in (comp[3], comp[4], comp[5]) if m)
        short = {m: n - stock.get(m, 0) for m, n in need_cnt.items()
                 if n > stock.get(m, 0)}
        if any(m in plan.comp for m in short):
            return None                        # 缺的是「还得炼出来」的成品卡 → 等主炉先炼它
        need_buy = sum(short.values())
        if need_buy:
            free_box = self._free_change(st)
            if need_buy > free_box or bought + need_buy > MAX_BUY_PER_TICK:
                # 不是故障，是等前面那几炉把素材烧掉腾格位 —— 不计入 errors
                self._log("暂缓", True,
                          f"{tag}·{plan.cname(target)}（{plan.name}）：需就地买 {need_buy} 张素材，"
                          f"换卡箱只剩 {free_box} 格，等下一轮")
                return None
            if not await self._buy_for(tid, target, short, steps):
                return None
            rd = await self._read_state()      # 买完槽位变了，重读一次再入炉
            if rd is None:
                self._log("读取卡箱", False, f"{tag}买卡后重读失败")
                return None
            st, stock, slots, pending = rd
        if any(stock.get(m, 0) < n or not self._spare(m, stock, pending, n, planned)
               for m, n in need_cnt.items()):
            return None
        return st, stock, slots, pending, need_cnt, need_buy

    async def _drain_box_gift(self, steps: list[dict], stock: Counter | None = None) -> None:
        """卡箱里已有的礼物套 48 面卡 → 就地变卡，**不收礼**。

        与 _gift_drain 相互独立（不受 gift_drain 开关约束）：礼物收进来产出的
        48 面卡和箱里躺着的那几张**本来就是同一套**（card_lib theme=487 手工织锦），
        变卡只认源卡的 slotid+slottype，箱里的卡同样能当 srcid —— 于是「收礼 →
        变卡」整条路可以省成「直接变」，既省一次收礼，也把白占箱位的卡变成素材。

        门槛（2026-09-26 真机实测补充）：**变卡失败也消耗源卡**（箱里 8 张，尝试 3 次
        只成 1 张，箱里当场剩 5 张），所以每 tick 只试一次，且只在 dstid **确实是当前
        队列用得上的卡**时才动手 —— 变出一张用不上的卡，等于拿 2 张礼物卡换 1 张废卡。

        目标来源（2026-09-26 用户定调）：**不再写死** `gift_dst`。变卡的意义是「省掉炼
        40 面额这一步」，目标必须是**当前在炼的那套里的 40 面卡、且引擎正缺的那张**
        —— 所以这里交给 `_pick_change_dst()` 按实时缺口挑（缺哪个变哪个）。
        """
        if self._box_gift_fail >= 2 or time.time() < self._box_gift_cooldown:
            return
        if stock is None:
            stock = self._stock
        pick = self._pick_change_dst(stock)
        if not pick:
            self.box_gift_last = {"ok": True, "skipped": True, "inbox": 0, "tried": 0,
                                  "changed": 0, "dst": None,
                                  "message": "当前没有缺的套内卡（40 面优先），本轮不消耗卡箱里的礼物卡"}
            return
        dst = int(pick["dst"])
        try:
            r = await gf.drain_box_gift_cards(self.s, dstid=dst)
        except Exception as e:  # noqa: BLE001
            self._log("礼物卡变卡", False, f"异常：{e}")
            steps.append({"action": "礼物卡变卡", "ok": False, "detail": str(e)})
            return
        r["dst"] = dst
        r["target"] = pick.get("target")
        self.box_gift_last = r
        if r.get("skipped"):
            self._box_gift_fail = 0            # 箱里没卡 = 正常，不累计
            return
        changed = int(r.get("changed") or 0)
        if changed:
            self._box_gift_fail = 0
        else:
            self._box_gift_fail += 1
            # 失败也烧掉一张源卡，连试不该硬撑 → 静默 30 分钟后再看
            self._box_gift_cooldown = time.time() + 1800
        ok = changed > 0
        self._log("礼物卡变卡", ok, r.get("message") or "")
        steps.append({"action": "礼物卡变卡", "ok": ok, "detail": r.get("message") or ""})

    async def _gift_drain(self, steps: list[dict]) -> None:
        """礼物箱快满 → 收礼 + 变卡（自动炼卡的可选环节，见 gf.drain_gift_box）。

        只在「箱件数 ≥ upper」时才动，变卡约 50% 成功、失败不阻塞 —— 所以这里
        除了写日志不改变 tick 的成败，绝不让炼卡主流程因为变卡失败而中断。

        变卡目标同样不写死：走 `_pick_change_dst()`（缺哪个套内 40 面卡变哪个），
        和上面「卡箱礼物卡变卡」保持同一口径 —— 否则收礼变出的卡不属于在炼的那套，
        等于白烧礼物卡。一条都挑不出来时本环节直接跳过。
        """
        pick = self._pick_change_dst(self._stock)
        if not pick:
            self.gift_last = {"ok": True, "skipped": True, "took": 0, "changed": 0,
                              "dst": None,
                              "message": "当前没有缺的套内卡（40 面优先），收礼变卡跳过"}
            return
        try:
            r = await gf.drain_gift_box(self.s, dstid=int(pick["dst"]),
                                        upper=int(self.gift_upper),
                                        lower=int(self.gift_lower))
        except Exception as e:  # noqa: BLE001
            self._log("礼物变卡", False, f"异常：{e}")
            steps.append({"action": "礼物变卡", "ok": False, "detail": str(e)})
            return
        r["dst"] = int(pick["dst"])
        r["target"] = pick.get("target")
        self.gift_last = r
        if not r.get("ok"):
            self._log("礼物变卡", False, r.get("message") or "失败")
            steps.append({"action": "礼物变卡", "ok": False,
                          "detail": r.get("message") or "失败"})
            return
        if r.get("skipped"):
            # 没到阈值：不写日志（每 tick 都写会刷屏），只在 status 里留个快照
            return
        ok = (r.get("changed") or 0) > 0
        self._log("礼物变卡", ok, r.get("message") or "")
        steps.append({"action": "礼物变卡", "ok": ok,
                      "detail": r.get("message") or ""})

    async def _read_state(self):
        """读主界面 -> (st, stock, slots, pending)。失败返回 None。"""
        mp = await self.s.post(EP_MAINPAGE, code="", opuin=self.s.uin)
        if mp.get("code") != 0:
            return None
        st = parse_full(mp.get("raw", ""))
        stock: Counter = Counter()
        slots: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for c in st["change"]:
            stock[c["id"]] += 1
            slots[c["id"]].append((c["slot"], 0))
        for c in st["store"]:
            stock[c["id"]] += 1
            slots[c["id"]].append((c["slot"], 1))
        pending: Counter = Counter()
        for c in st["stove"]:
            pending[c["id"]] += 1
        return st, stock, slots, pending

    # ---------- 偷炉（借好友空炉炼自己的卡） ----------
    async def _load_friends(self) -> list[str]:
        """好友 uin 列表（card_user_list，194 人，顺序=游戏界面顺序，非卡友）。缓存。"""
        if self._friends is not None:
            return self._friends
        try:
            r = await self.s.post(EP_USER_LIST)
        except Exception:
            return []
        self._friends = re.findall(r'<user\s+uin="(\d+)"', r.get("raw", ""))
        return self._friends

    async def _friend_stove_empty(self, opuin: str) -> int:
        """好友的空**主**炉数（>0 = 可偷）。-1 = 查询失败。
        命中 5 分钟缓存，避免每 tick 反复打 mainpage(opuin)。

        ⚠️ 必须读 `cap.stove.free_main`：parse_full 只把 id>0 的卡放进 st["stove"]，
        空槽（id=0）只体现在 cap 计数里。早期版本在 st["stove"] 里数 id==0
        **永远得 0**，导致「扫 194 好友全都没空炉」的假结论（2026-09-17 定位修正）。
        """
        now = time.time()
        c = self._friend_cache.get(opuin)
        if c and now - c[1] < 300:
            return c[0]
        try:
            mp = await self.s.post(EP_MAINPAGE, code="", opuin=opuin)
            if mp.get("code") != 0:
                return -1
            st = parse_full(mp.get("raw", ""))
            empty = int(st.get("cap", {}).get("stove", {}).get("free_main", 0))
            self._friend_cache[opuin] = (empty, now)
            return empty
        except Exception:
            return -1

    async def _pick_steal_friend(self, st: dict) -> str | None:
        """从最后一名好友往回找第一个可用的偷炉对象。
        优先选「检测到有空炉」的；若检测盲区（都测不到空炉）则退而取最后一名，
        交给服务端最终裁决（好友相关错误会被缓存、换下一名重试）。
        排除：① 近期偷炉失败被缓存的好友；② 已在对方炉上挂着我自己未取回的卡的好友
        （再偷会吃 -33084，等 tick 收卡后自然解除）。"""
        friends = await self._load_friends()
        if not friends:
            return None
        now = time.time()
        busy = {str(c.get("opuin")) for c in st.get("stove", [])
                if c.get("opuin") and str(c.get("opuin")) != str(self.s.uin)}
        # 优先用「常去好友」：未被失败缓存、其炉上没挂着我未取回的卡，且确实有空主炉
        # （探测失败 -1 时仍交给服务端裁决，别因为探测抖动放弃常去好友）
        pref = self.preferred_steal_friend
        if pref and pref not in busy and self._friend_fail.get(pref, 0) <= now:
            if await self._friend_stove_empty(pref) != 0:
                return pref
        # 否则从最后一名好友往回找
        window = friends[-self.steal_search_depth:]
        window.reverse()                      # 从最后往回
        chosen = None
        for uin in window:
            if self._friend_fail.get(uin, 0) > now:
                continue
            if uin in busy:
                continue
            e = await self._friend_stove_empty(uin)
            if e > 0:
                return uin
            chosen = uin                      # 退化候选：测不到空炉也尝试一次
        return chosen

    async def _refine_steal(self, st, tid, target, need_cnt, plan, slots, friend,
                            stock: Counter, pending: Counter, steps) -> None:
        """对好友 friend 的空炉发起一次偷炉炼卡。

        **端点必须是 `card_stove_steal`，不是 `card_stove_refine`**（2026-09-17 真机抓包）：
        用 refine+opuin 会被服务端当成「在我自己炉上炼」，我主炉满时直接吃 -33003 误导排查方向。
        参数与普通炼卡同构，仅额外带 `opuin=好友uin`（素材仍取我自己的卡箱槽位）。
        """
        res = self._payload(plan, target, need_cnt, slots)
        if res is None:
            return
        payload, picked = res
        payload.pop("code", None)             # 抓包原文无 code 字段
        payload["opuin"] = friend             # 关键：借好友账号炼自己的卡
        r = await self.s.post(EP_STEAL, **payload)
        code = r.get("code")
        ok = code == 0
        nm = plan.cname(target)
        msg = r.get("message") or err_msg(code)
        self.last_steal = {"friend": friend, "target": target, "name": nm,
                           "code": code, "msg": msg, "ts": time.time()}
        self._log("偷炉", ok, f"{nm}（{plan.name}）@{friend} {msg}")
        steps.append({"action": "偷炉", "ok": ok,
                      "detail": f"{nm}（{plan.name}）@{friend} {msg}"})
        if ok:
            self.stats["refined"] += 1
            for m, n in need_cnt.items():
                stock[m] -= n
                for _ in range(n):
                    if slots.get(m):
                        slots[m].pop(0)
            used = st["cap"].get("change", {}).get("used", 0)
            st["cap"]["change"]["used"] = max(0, used - sum(1 for _s, _t in picked if _t == 0))
            pending[target] += 1
        else:
            # 偷炉失败分两类（2026-09-17 真机实测）：
            #   -33031「同时只能偷一个炉子」= 全局限制（我还有一炉在偷），与好友无关，
            #          禁止拉黑好友，只做全局冷却，等那炉收取后自然恢复。
            #   其它（好友炉满 / 挂着我未取的卡等）= 好友相关 → 拉黑换人。
            now = time.time()
            if code == -33031:
                self._steal_cooldown_until = now + 120
            else:
                if friend:
                    self._friend_fail[friend] = now + 1800
                self._steal_cooldown_until = now + 120

    async def _buy_for(self, tid: int, target: int, short: dict, steps: list[dict]) -> bool:
        """就地为 (套 tid 的) target 这一炉补买缺的底卡。全成功返回 True。"""
        plan = self.plans[tid]
        for m, n in short.items():
            for _ in range(n):
                r = await self.s.post(EP_BUY, card_id=m, theme_id=tid)
                ok = r.get("code") == 0
                if ok:
                    self.stats["bought"] += 1
                msg = r.get("message") or err_msg(r.get("code"))
                self._log("买卡", ok, f"{plan.cname(m)}（{plan.cname(target)}素材） {msg}")
                steps.append({"action": "买卡", "ok": ok,
                              "detail": f"{plan.cname(m)}（{plan.cname(target)}素材） {msg}"})
                if not ok:
                    return False
        return True

    def _spare(self, m: int, stock: Counter, pending: Counter, n: int,
               planned: set) -> bool:
        """素材能否动：套内卡要留住箱内那 1 张，除非它本身正在补货。

        没有这条保护时引擎会「先消耗现有唯一一张、再慢慢补炼」，中间态下套内卡
        被清零 —— 一旦中途停跑，用户箱里的收藏就凭空少几张（2026-09-16 二十四史
        实测：首 tick 就把箱内唯一的隋书/北史/新唐书喂给了魏书）。

        放行条件（按优先级）：
          1. 非套内卡（任意在跑套的套内卡都算），或商店可买的底卡 —— 随便用，
             用完再买（底卡不用留，收尾那一刻才把集卡册缺的补齐）
          2. 消耗 n 张后箱内仍够留 keep[m] 张
          3. 这张卡在净需求里本来就要补（planned）或有货在途（pending）
             —— 不满足 3 会死锁：刚买 1 张金史就被保护挡住，炼不出隋书、也腾不出格位买下一张

        ⚠️ 多套并行时 keep 取**队列并集**：A 套的素材若正好是 B 套的套内卡，
           不能被 A 吃掉（B 的 ledger 里该卡会进 planned，所以 B 要炼时仍放行）。
        """
        cards, comp, _t = _maps()
        if m not in self._keep or m not in comp:
            return True
        if stock.get(m, 0) - n >= self._keep[m]:
            return True
        return m in planned or pending.get(m, 0) > 0

    def _payload(self, plan: ThemePlan, target: int, need_cnt: Counter, slots: dict):
        """构造 card_stove_refine 参数 -> (payload, picked)。

        picked = [(slot, slottype), ...]，即真正吃掉的槽位（调用方据此同步箱占用数）。
        注意：只在**本地副本**上取槽，不改 slots —— 提交成功后由调用方统一删，
        否则重复素材会被删两次（旧版 bug）。
        """
        comp = plan.comp[target]
        mats = [int(m) for m in (comp[3], comp[4], comp[5]) if m]
        pools = {m: list(v) for m, v in slots.items()}
        picked = []
        for m in mats:
            pool = pools.get(m) or []
            if not pool:
                return None
            picked.append(pool.pop(0))
        p = {"targetid": target, "bflash": 0, "ver": 1, "targettype": 1,
             "themeid": comp[0], "code": ""}
        for i, (slot, st) in enumerate(picked, start=1):
            p[f"slot{i}"] = slot
            p[f"slottype{i}"] = st
        return p, picked

    # ---------- 队列 ----------
    def seed_state(self, stock: Counter, pending: Counter) -> None:
        """启动预检时喂一次库存 → 面板在第一个 tick 之前就显示实时口径。"""
        self._stock = Counter(stock)
        self._pending = Counter(pending)
        self.live_plans = [self.plans[t].plan_summary(self._stock, self._pending)
                           for t in self.queue]
        self.live_plan = self.live_plans[0] if self.live_plans else None

    def add_theme(self, tid: int) -> dict:
        """把一套加进队列（运行中也能加）。已在队列里则只是重复添加。"""
        tid = int(tid)
        if tid not in self.plans:
            self.plans[tid] = ThemePlan(tid)
            for cid in self.plans[tid].ids:
                self._keep[cid] = 1
        if tid not in self.tids:
            self.tids.append(tid)
        if tid not in self.queue:
            self.queue.append(tid)
        # 用最近的库存算实时净需求（不喂库存会显示「全套从零」的假数字）
        return self.plans[tid].plan_summary(self._stock, self._pending)

    # ---------- 循环 ----------
    async def _loop(self) -> None:
        while self.running:
            if not self.queue:
                self.running = False
                self._log("完成", True, "队列已空（所有套卡都达上限），自动停止")
                break
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log("异常", False, str(exc)[:120])
            if not self.queue:
                self.running = False
                self._log("完成", True, "队列已空（所有套卡都达上限），自动停止")
                break
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def status(self) -> dict:
        # 变卡目标按**最近一次读到的库存**实时挑（与 tick 里同一函数同一口径），
        # 面板就能显示「下一步会变哪张 40 面卡」，而不是写死的 dst。
        pick = self._pick_change_dst(self._stock)
        # 队列里每一套都按**最近一次读到的库存**算净需求 —— 追加新套卡后
        # 也能立刻显示真实缺口，而不是「全套从零」的静态数字。
        if self._stock or self._pending:
            themes = [self.plans[t].plan_summary(self._stock, self._pending)
                      for t in self.queue]
        else:
            themes = [self.plans[t].plan_summary() for t in self.queue]
        return {"running": self.running,
                "theme": themes[0] if themes else (self.live_plan
                                                   or self.plans[self.tids[0]].plan_summary()),
                "themes": themes or self.live_plans,
                "queue": list(self.queue),
                "stats": self.stats, "interval": self.interval,
                "randchance": self.randchance,
                "state_delta": self.state_delta[:10],
                "last_pai_raw": self.last_pai_raw[:400],
                "last_collect": self.last_collect,
                "gift": {"enabled": self.gift_drain,
                         "dst": pick["dst"] if pick else None,
                         "dst_name": pick["name"] if pick else "",
                         "dst_face": pick["face"] if pick else 0,
                         "dst_theme": pick["theme"] if pick else "",
                         "target": (pick.get("target") if pick else ""),
                         "upper": self.gift_upper, "lower": self.gift_lower,
                         "last": self.gift_last},
                "box_gift": {"enabled": self.box_gift_change,
                             "dst": pick["dst"] if pick else None,
                             "dst_name": pick["name"] if pick else "",
                             "dst_face": pick["face"] if pick else 0,
                             "dst_theme": pick["theme"] if pick else "",
                             "target": (pick.get("target") if pick else ""),
                             "useful": bool(pick),
                             "cooldown_remain": max(0, int(self._box_gift_cooldown - time.time())),
                             "last": self.box_gift_last},
                "steal": {"enabled": self.steal_enabled,
                           "preferred": self.preferred_steal_friend,
                           "last_steal": self.last_steal,
                           "blocked": self.steal_blocked,
                           "cooldown_until": self._steal_cooldown_until},
                "log": self.log[-30:]}
