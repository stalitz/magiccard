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
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import gift as gf
from . import buylog
from .constants import (EP_BUY, EP_COLLECTION_ADD, EP_COLLECTION_GET, EP_MAINPAGE,
                        EP_REFINE, EP_STEAL, EP_USER_LIST, err_msg, scale_secs,
                        TIME_SCALE)
from .mc_api import LOG_UIN, engine_logger

DATA = Path(__file__).resolve().parent.parent / "data"

MAX_BUY_PER_TICK = 12  # 每 tick 最多买几张底卡，防请求过频。12 = 4 主炉×3 底卡，
                        # 保证 4 个主炉同时空出时能一口气各开一张 40 面（不再被预算卡成 2 张）。
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
        # ★ comp 行里的 secs（[6]）必须是**真机实际炉·秒**，不是卡片表原值：
        #   真机 locktime = floor(表值 × 0.6232)（炉等级 + 精灵加速，2026-10-28 实测
        #   四张卡逐秒吻合，见 constants.TIME_SCALE）。这里统一缩放一次，所有下游
        #   （ThemePlan.comp / ThemeModel.secs / net_plan / _card_secs）自动同口径。
        _comp: dict = {}
        for _c in lib["theme_compose_list"]:
            _row = list(_c)
            _row[6] = scale_secs(int(_c[6] or 0))
            _comp[int(_c[2])] = _row
        _DERIV = (
            {int(c[0]): c for c in lib["card_list"]},
            _comp,
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


# ---- giftid → qq秀(主题秀)实例/名称 映射 ----
# t[9] 的 giftid（如 614）与 card_collection_get 的 qqshow_str 已收集实例 id（如
# 608624）是**不同命名空间**：上交 card_collection_add 传的是 t[9] 的 giftid，游戏
# 内部把它映射到真实 qq秀实例。要「挑未收集的 qq秀上交」，必须靠 gift_list 把
# giftid 翻成实例 id，再比对 qqshow_str。gift_list 每项 = [giftid, type, qq_show_id, name]。
_GIFT_INFO: dict | None = None


def _gift_info_map() -> dict:
    """{giftid(str): (qq秀实例id(str), 名称(str))}，来自 card_lib.json 的 gift_list。"""
    global _GIFT_INFO
    if _GIFT_INFO is None:
        lib = _lib()
        m: dict = {}
        for g in (lib.get("gift_list") or []):
            try:
                m[str(g[0])] = (str(g[2]), str(g[3]) if len(g) > 3 else "")
            except Exception:
                pass
        _GIFT_INFO = m
    return _GIFT_INFO


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
        steal_ids: set = set()
        for cm in re.finditer(r"<card\s+([^>]*?)/?>", mm.group(2)):
            a = _attrs(cm.group(1))
            a["slot"] = int(a.get("slot") or -1)
            a["id"] = int(a.get("id") or 0)
            if a["id"] > 0:
                out[key].append(a)
                if key == "stove" and a.get("slottype") == "steal":
                    steal_ids.add(a["id"])
            elif key == "stove":
                # stovebox 的空槽 id=0：slottype="steal" 的是偷炉位（本版引擎暂不占用）
                if a.get("slottype") == "steal":
                    free_steal += 1
                else:
                    free_main += 1
        out["cap"][key]["used"] = len(out[key])
        if key == "stove":
            # 空槽 = 服务端列出的 id=0 且非 steal 的格子（实测只列真正可用的空槽）。
            # ★★ 真机卡位口径（2026-11-04 用户点破 + 两份真机报文核对）：
            #   `cur="7"` = **5 主卡位（slot 0~4）+ 1 偷炉位（slot 6）+ 1 个 VIP 卡位
            #   （slot 5，未开通所以服务端根本不列）**。用户没开 VIP → 能用的就 6 个。
            #   别拿 cur 当可用容量，也别把 slot5 当死格子——开通 VIP 后主卡位
            #   5→6，本字段（free_main，是**数出来的**）自动跟着变，引擎无需改。
            #   加炉道具不是加卡位，而是**把排队位升成炉位**：
            #     3 炉 → 5 个主卡位 = 3 炼 + 2 排队（实测 09-21 报文：炉1 挂 2 张、
            #             炉2 挂 2 张、炉0 挂 1 张）
            #     4 炉 → 4 炼 + 1 排队（11-04 报文：4 张各占一台炉，只剩 slot3 空）
            #     5 炉 → 5 炼 + 0 排队（道具最多 +2，零排队位）
            #   → 所以「每炉 2 卡位」只在 2~3 炉时近似成立，真正的硬约束是
            #     `free_main = 主卡位数 - 占位卡数`（本字段），并发炉数由 stovenum 给。
            # ★ stovenum = **主炉台数**（真机 `<stovebox cur="7" max="7" stovenum="4">`）。
            #   2026-11-04 道具卡把主炉从 3 开到 4 后实测确认：`cur` 恒 7 不变，
            #   只有 stovenum 与 `<stove id="0..3">` 跟着变 → 引擎建模的并发炉数
            #   必须取自这里，不能写死 3。
            out["cap"][key]["stovenum"] = int(head.get("stovenum") or 0)
            out["cap"][key]["free_main"] = free_main
            out["cap"][key]["free_steal"] = free_steal
    stove_entries = []
    for sm in re.finditer(r"<stove[^>]*>", xml_text):
        a = _attrs(sm.group(0))
        if "id" in a:
            stove_entries.append(a)
    stove_id_cnt = Counter(int(a.get("id") or 0) for a in stove_entries)
    for a in stove_entries:
        sid = int(a.get("id") or 0)
        # 偷炉判定：① <stove> 显式 slottype="steal"（仿真序列化保证）；
        # ② 兜底——id 与 stovebox 偷炉槽卡片一致且该 id 在 <stove> 里唯一
        # （同 cid 同时炼于主炉+偷炉时不判定，保守按主炉处理）。
        steal = (a.get("slottype") == "steal"
                 or (sid > 0 and sid in steal_ids and stove_id_cnt[sid] == 1))
        out["stoves"].append({"id": sid,
                              "endtime": int(a.get("endtime") or 0),
                              "steal": steal})
    return out


def _shrink_report(st: dict, n_stove: int) -> str:
    """加炉道具到期缩容时的取证文本（2026-11-04）。

    服务端在 `stovenum` 从 4 掉回 3 的瞬间对**超额炉**（炉号 >= n_stove）里
    正在炼的卡到底怎么处理，用户也无法确认：可能是「继续炼完」，也可能是
    「直接停掉」。这里不动任何东西，只把现场（卡 id / flag / 剩余时间）原样
    打出来，等第一次真机到期，看下一条 tick 的剩余时间是继续递减还是卡没了，
    就能定性，不用再猜。

    `st` 为 `parse_full()` 结果，`st["stove"]` 只含 id>0 的已占卡位。
    """
    extra = [c for c in (st.get("stove") or [])
             if int(c.get("stove") or -1) >= n_stove]
    if not extra:
        return (f"缩容取证：超额炉（炉号≥{n_stove}）已无卡 "
                f"→ 到期瞬间服务端直接清掉了超额炉")
    now = int(st.get("now") or 0)
    parts = []
    for c in extra:
        btime = int(c.get("btime") or 0)
        left = (btime + int(c.get("locktime") or 0) - now) if btime else None
        if left is None:
            stt = "无计时"
        elif left <= 0:
            stt = "到点待取"
        else:
            stt = f"剩{left // 60}分"
        parts.append(f"炉{c.get('stove')}/位{c.get('slot')}/卡{c.get('id')}"
                     f" flag={c.get('flag')} {stt}")
    return (f"缩容取证：超额炉仍有 {len(extra)} 张卡（下 tick 剩余时间继续递减"
            f"=炼完才失效；卡消失或计时冻结=直接停）｜" + "；".join(parts))


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
        # ★ 集齐奖励（2026-11-06 真机实证：交「时尚女套装」实测 money +100 / exp +200，
        #   与卡库 t[6]/t[7] 对上）——t[6] = 金币、t[7] = 经验。`prize` 保留为金币别名，
        #   老调用点（面板/经济测算）不用改。
        self.gold = int(t[6] or 0)
        self.exp = int(t[7] or 0)
        self.prize = self.gold
        self.ids = [int(x) for x in (t[11] or []) if x]
        gifts = str(t[9] or "").split("|")
        self.gifts = [g.strip() for g in gifts if g.strip()]  # t[9] 全量 qq秀 giftid
        self.giftid = self.gifts[0] if self.gifts else "0"

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
        # 这套卡从头炼到尾一共要几张面额 40 的卡（展开到 40 面为止，不扣库存）。
        # `base_need` 里看不到它们 —— mrp 会把需求继续摊到更低的面额上。
        self.f40 = face40_demand(self.ids, self.comp, self.depth, price_of)
        self.rounds = 0          # 本套已集齐并上交的次数
        self.done = False        # 达到 rounds 上限

    def cname(self, cid: int) -> str:
        return card_name(cid)

    def net_plan(self, stock: Counter, pending: Counter,
                 credit: Counter | None = None, sets: int = 1) -> tuple[dict, dict]:
        """净需求：返回 (要炼的 {卡id: 次数}, 要买的 {底卡id: 张数})。

        库存（箱内 + 炉内在炼）从总需求里抵扣；只有**实际要新炼/新买**的数量
        才向素材层传导 —— 已有的成品卡不会再把它那份素材需求全额压到最底层。

        `credit` = **变卡环节接下来会产出的 40 面卡**（见 `_face40_credit`），
        先算作已到货。否则净需求会把「集卡册那一份 40 面卡」当成还得从底卡炼出来，
        于是「变卡变出 40 面卡」和「炼卡再炼一张 40 面卡」两头同时做 —— 变卡就白变了。

        ★ 2026-10-03 用户定调「加套数设置，默认 0 = 一直炼、不空炉、1 星按囤卡炼多套」：
        `sets` = 每张套内卡要几张（= 炼几套）。`sets=1` 是旧行为（凑 1 套）。
        更大的 `sets` 让引擎一次排更多炉次（多套并行、炉位不闲）；库存抵扣照旧，
        所以「按囤卡炼多套」是自动的 —— 囤够就多炼、囤不够就少炼，绝不空转。
        ★ 2026-11-05：`sets <= 0` 表示**本轮不排任何炉次**（`_calc_sets` 的缺口闸：
        两箱空位放不下还缺的卡时返回 0）。旧写法 `need = max(1, sets)` 会把 0 悄悄
        抬回 1，等于闸门形同虚设 —— 正是这次超炼 4 张卡、把箱位吃死的机制。
        """
        if int(sets) <= 0:
            return {}, {}
        avail = Counter(stock)
        if credit:
            avail.update({int(c): n for c, n in credit.items() if n > 0})
        # ★ 2026-10-03 修正：只有【顶层收藏卡】按 sets 放大，**素材/中间层不放大**。
        #   40 面卡是「每套**消耗** 1 张」的素材，不是「每套要**收集** N 张」的藏品。
        #   旧写法 need={cid: sets} 把每张套内卡都按 sets 算 → 40 面被「收藏需求 +
        #   素材消耗」双重放大（实测 sets=6：种生求子被算成要炼 7 张，而囤 4 张本已
        #   够 6 套用 → 超采、偷炉去炼囤多的那张）。现在素材层 need 初始为 0，只靠
        #   顶层需求沿配方树传导 —— 炼 N 张顶层就正好消耗各 N 张 40 面，不多不少。
        maxd = max((self.depth.get(c, 0) for c in self.ids), default=0)
        tops = {c for c in self.ids
                if self.depth.get(c, 0) == maxd and self.comp.get(c)}
        # ★ 每张套内卡（含中间层 40 面卡）都要 1 份进集卡册，所以**所有 id** 都按 sets 算
        #   收藏需求；中间层既是藏品又是上层卡的素材，缺了这份需求会导致 _spare 死保它、
        #   上层卡永远炼不出（2026-10-11 吴韵汉风 2 星实测：炉空转、4 空炉不投料）。
        #   旧「只顶层按 sets」会让中间层收藏需求=0，需求传导只算素材消耗，于是
        #   planned 不含它 → _spare 挡消费 → 死锁。
        need: dict[int, int] = {cid: max(1, int(sets)) for cid in self.ids}
        runs: dict[int, int] = {}
        buy: dict[int, int] = {}
        for cid in sorted(self.depth, key=lambda c: -self.depth[c]):
            n = need.pop(cid, 0)
            if n <= 0:
                continue
            net = n - (avail.get(cid, 0) + pending.get(cid, 0))
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
                     pending: Counter | None = None, sets: int = 1) -> dict:
        """静态视图：不给库存时按「全套从零」展示（等价旧 mrp 口径）。

        ★ 2026-10-03 加 `sets`：套数设置 >1 时按多套算净需求（囤了多张 40 面就要
        炼多套），否则启动预检会按「1 套已凑齐」硬挡、sets 参数形同虚设。
        """
        if stock is None:
            runs, buy = dict(self.runs), dict(self.base_need)
            ok = bool(self.ok)
        else:
            runs, buy = self.net_plan(stock, pending or Counter(), sets=sets)
            ok = True
        # 与 _refine_all 的 real 排产同口径：深度**降序 = 先炼高面额**，
        # 否则面板列的顺序和引擎实际先提交的不一致（看着像在囤 40 面卡）
        order = sorted(runs, key=lambda c: -self.depth.get(c, 0))
        secs = sum(runs[c] * int(self.comp[c][6]) for c in runs)
        return {
            "tid": self.tid, "name": self.name, "star": self.star,
            "prize": self.prize, "giftid": self.giftid,
            "gold": self.gold, "exp": self.exp,
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


def face40_demand(ids: list, comp: dict, depth: dict, face) -> dict[int, int]:
    """这套卡从头炼到尾**一共要几张面额 40 的卡**（按配方往下展开到 40 面为止）。

    与 `ThemePlan.base_need` 不同：`mrp` 会把需求继续摊到更低的面额上，所以 40 面
    这一层在 base_need 里是 0；这里刻意停在 40 面 —— 变卡产出就是 40 面，凑的就是它。
    不扣库存，纯静态。
    """
    need = {int(c): 1 for c in ids}
    out: dict[int, int] = {}
    for cid in sorted(need, key=lambda c: -depth.get(c, 0)):
        n = need.pop(cid, 0)
        if n <= 0:
            continue
        if face(cid) == CHANGE_FACE:
            out[int(cid)] = out.get(int(cid), 0) + n
            continue
        c = comp.get(cid)
        if c:
            for m in (c[3], c[4], c[5]):
                if m:
                    need[int(m)] = need.get(int(m), 0) + n
    return out


def face40_gap(plan: "ThemePlan", stock: Counter | None = None,
               pending: Counter | None = None) -> dict:
    """一套卡的「40 面卡缺口」—— **整套从零的总量口径**（不是「一张都没有」才算缺）。

    用户 2026-09-26 定调：「什么是缺你要明白 —— 是炼的套卡一共需要多少张 40 的，
    只要不够炼完全套的就是缺，不是当前一张 40 没有就是缺。」

    ⚠️ 这里**不能**用 `net_plan()` 的净需求：净需求是「顶层卡算出缺口才向下传导」，
    只要顶层那一张卡已经攒到了，整套的 40 面需求就凭空消失（实测：全套 9 张各囤 1
    张、明明还缺斗巧时，net_plan 返回空）。但对「凑一套」来说，顶层有了还得再攒一
    轮 —— 那几张 40 面卡照样是缺口。

    所以按 `ThemePlan.f40`（从头炼到尾共需几张 40 面，展开到 40 面为止）扣掉现有库存：
      · gap        —— 按卡 id 细分：这张卡还差几张
      · need_total —— 还差几张才能凑齐这一套；> 0 就继续变卡
      · have       —— 这些 40 面卡在库存 + 炉内在炼里各已有几张
    返回 `{"need_total", "have", "gap", "kinds", "ids"}`。
    """
    stock = stock or Counter()
    pending = pending or Counter()

    def face(cid: int) -> int:
        c = plan.cards.get(int(cid))
        return int(c[3] or 0) if c else 0

    inner40 = [int(c) for c in plan.ids if face(c) == CHANGE_FACE]
    gap: dict[int, int] = {}
    for cid in inner40:
        have_c = stock.get(cid, 0) + pending.get(cid, 0)
        need = int(plan.f40.get(cid, 0) or 0)
        if need > have_c:
            gap[cid] = need - have_c
    return {"need_total": sum(gap.values()),     # 还差几张才能凑齐这一套
            "have": sum(stock.get(c, 0) + pending.get(c, 0) for c in inner40),
            "gap": gap,                          # {卡id: 还差几张}
            "kinds": len(inner40),
            "ids": inner40}


def pick_change_target(plans: dict, queue: list, stock: Counter | None = None,
                       pending: Counter | None = None,
                       keep: set | None = None,
                       rot: dict | None = None) -> dict | None:
    """（纯计算）按「在炼队列 + 实时库存」挑下一步该变哪张卡 —— 变卡目标**不许写死**。

    用户 2026-09-26 两条定调：
      ① 「礼物卡只能变 40 面额的，变不了其他面额，因为当前的礼物卡只有 48 面」——
         礼物卡只有 48 面一种形态，变卡**只产出面额 40**，变不出别的面额。所以候选
         卡必须是面额 CHANGE_FACE(40) 的套内卡，**没有「放宽到别的面额」的兜底**，
         挑不出来就返回 None（本轮不动箱里的礼物卡）。
      ② 「什么是缺：是炼的套卡一共需要多少张 40 的，只要不够炼完全套的就是缺，
         不是当前一张 40 没有就是缺」—— 缺口按 `face40_gap()` 的**总量**算。
      （此前写死 26117 属 theme 1810，炼的却是别的套，等于白烧礼物卡。）

    候选排序（★ 2026-10-03 用户定调「成功后再次变卡要找少的变」）：
      ① **持有张数少的优先**（`held_c` 升序）——只要该 40 面卡还有缺口，就补手上最少的
        那张，让多张不同的 40 面卡轮流被变出来。此前按「缺口最大」挑会死盯同一张
        （实测七夕情人节求姻缘 23926 已有 9 张还一直变，全占卡箱；而变 3 张不同的
        才能炼更高面额）。
      ② 持有相同则**单卡缺口大**的优先；再同则整套缺口大者优先（多套并行不让第一套独吞）
      ③ 再相同则炼完全套用得更多的优先（`f40`）；最后按队列序、卡 id 保证稳定不抖
    """
    if not queue:
        return None
    if keep is None:
        keep = {cid for t in queue for cid in plans[t].ids}
    stock = stock or Counter()
    pending = pending or Counter()

    cands = []                       # (持有数, -单卡缺口, -整套缺口, -f40需求, 队列序, cid, tid)
    for order, tid in enumerate(queue):
        g = face40_gap(plans[tid], stock, pending)
        for cid, n in g["gap"].items():
            if cid not in keep or n <= 0:
                continue
            need_c = int(plans[tid].f40.get(cid, 0) or 0)
            held_c = int(stock.get(cid, 0) + pending.get(cid, 0))
            # ★ 2026-10-03 用户定调「成功后再次变卡要找少的变」：主排序 **持有张数少的
            #   优先**（held_c 升序）。此前按「缺口最大」挑会死盯同一张变。
            # ★ 2026-10-18 修：held_c 每 tick 会被炼卡吃掉而归 0（40 面素材卡刚变出就
            #   被拿去炼更高面额），于是主排序永远打平、二级仍落到「缺口最大」→ 还是只盯
            #   缺口最大的那一张（实锤：七雄Q传 孙膑被连续变 7 张堆在箱里）。
            #   修法：二级排序加 **轮转**——held_c 相同（都缺）时，优先选「本会话被变次数
            #   最少」的卡（rot 升序），让多张不同的 40 面卡轮流被变出来，不再死盯一张。
            #   rot=None（手动变卡等一次性调用）时该键恒为 0，退化为原缺口优先，不影响。
            rot_count = int((rot or {}).get(cid, 0))
            cands.append((held_c, rot_count, -int(n), -int(g["need_total"]), -need_c,
                          order, int(cid), tid))
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
    held_c, _rot, _neg_n, _neg_need_total, neg_need_c, order, cid, tid = cands[0]
    n = -_neg_n
    plan = plans[tid]
    c = plan.cards.get(cid)
    face = int(c[3] or 0) if c else 0
    g = face40_gap(plan, stock, pending)
    return {"dst": cid, "name": plan.cname(cid), "face": face, "times": n,
            "held": held_c,
            "tid": tid, "theme": plan.name, "f40_need": -neg_need_c,
            "need_total": g["need_total"], "have": g["have"],
            "gap_total": sum(g["gap"].values()),
            "gap": dict(sorted(g["gap"].items())),
            "target": f'{plan.cname(cid)}（{plan.name} {face}面，手上 {held_c} 张、'
                      f'还差 {n} 张；'
                      f'本套 40 面卡已有 {g["have"]} 张、总共还差 {g["need_total"]} 张；'
                      f'炼完全套需用 {max(1, -neg_need_c)} 张）'}


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
        # ★ 2026-10-03 用户定调「加一个套数设置，默认 0 = 一直炼、不停、不空卡炉；
        #   1 星卡按囤的卡炼多套，其他星先不用这个逻辑」。
        # surplus_sets = 每轮 net_plan 的「套数」倍数（每张套内卡要几张）：
        #   0  = 自动（1 星套按当前囤卡能炼几套就算几套，炉位排满；其他星 = 1 套）；
        #   >0 = 固定每轮排这么多套。
        self.surplus_sets = 0
        self._last_sets: dict[int, int] = {}  # 每套本轮实际用的 sets（status/日志用）
        self.running = False
        self.interval = 45.0
        self.log: list[dict] = []
        self.stats = {"rounds": 0, "refined": 0, "bought": 0, "collected": 0,
                      "gold": 0, "errors": 0}
        self.randchance = 0          # 取自己卡炉累积的抽奖机会数（status 透传）
        # 刚买进的卡：cid -> 时间戳。腾位（_make_room）不许把它们当杂卡卖掉，
        # 否则会出现「买一张 → 卖掉刚买那张 → 再买下一张」的空转（2026-11-05）。
        self._recent_buys: dict[int, float] = {}
        self._throttle: dict[str, float] = {}   # 同类日志节流（key → 上次打印时刻）
        self._freeze_msg: str = ""              # 缺口闸上次的原因文案（变了才记日志）
        self.live_plan: dict | None = None   # 主套实时净需求（兼容旧面板）
        self.live_plans: list[dict] = []     # 队列里每套的实时净需求
        self._stock: Counter = Counter()     # 最近一次读到库存（status 里算实时净需求用）
        self._pending: Counter = Counter()
        # 净需求树缓存（2026-11-03）：`_is_collect_essential` 每个 tick 会对多张卡
        # 反复问「需求树还要不要它」，每次重跑 net_plan 太浪费 —— 按「箱内+在炉」
        # 签名缓存，家底一变（买卡/出炉/上交）自动失效重算。
        self._net_sig: tuple | None = None
        self._net_cache: dict = {}           # id(plan) -> (runs, buy) | None
        self._change_rot: Counter = Counter()  # 每张 40 面卡本会话被变出的累计次数（轮转防死盯一张）
        self.state_delta: list[dict] = []    # 最近一次跨 tick 状态字段差分
        self.last_pai_raw = ""               # 最近一次翻牌的原始响应（溯源用）
        self.last_collect: dict | None = None  # 最近一次上交的奖励解析
        self._prev_fields: dict = {}         # 上一 tick 的状态字段快照
        self._pai_happened = False           # 上一 tick 是否翻过牌（决定是否记差分）
        self._rr = 0                         # 轮转起点
        self._made: Counter = Counter()       # 每类卡本会话累计「开炉生产」次数（40 面防饿死用）
        self._task: asyncio.Task | None = None
        # 偷炉（借好友空炉炼自己的卡）：用好友账号(opuin)发起 card_stove_steal。
        # ★ 端点必须是 steal（refine+opuin 会被当成「在自己炉上炼」→ 误报 -33003）。
        # 素材仍取我自己的卡箱槽位；炼成后卡挂在该好友炉上，取卡走
        # card_stove_stealcard_get + opuin。好友列表取 card_user_list
        # （194 人，顺序=游戏界面顺序，非卡友）。
        self.steal_enabled = True            # 偷炉总开关
        self.steal_search_depth = 12         # 每 tick 扫描的好友深度（按等级降序取前 N）
        # ★ 2026-11-14：窗口内**真正发 mainpage 探测**的上限。深度 12 意味着未指定
        #   好友时要连打 12 次 `card_user_mainpage`，而它正是 -3001（~15 次/10s
        #   读频次限流）的唯一触发点；本 tick 还要读自己的 mainpage（_read_state /
        #   _refresh_caps）→ 撞窗概率很高。真机常态是指定好友（零探测），这里只在
        #   「没指定」时兜底，探测 4 个足够（命中不了就交给服务端裁决）。
        self.steal_probe_max = 4
        # 偷炉降级（DP 按 M 台重排）的最短持有时长，防 60s 短冷却造成每 tick 抖动
        self._steal_down_until = 0.0
        self._friends = None                 # 好友 uin 列表（card_user_list，uin 序，缓存）
        self._friends_by_exp = None          # 同上但按 exp(等级代理)降序，活跃优先
        self._friend_cache: dict = {}         # uin -> (empty_count, ts)：好友炉空槽缓存
        # 已收集 qq秀 实例 id 缓存（来自 card_collection_get 的 qqshow_str），
        # 用于「上交挑未收集 qq秀」与「非 1 星按未收集数定套数」。一次查询全库缓存。
        self._show_cache: dict[int, set] = {}
        self._show_cache_ts: float = 0.0
        self._tick_collected: dict[int, set] = {}  # 本 tick 各套已收集 qq秀（submit/_calc_sets 共用）
        # 换卡箱已开通格数（parse_full 的 cap.change.cur，真机 18）——`_calc_sets` 用它
        # 给并行套数封顶（N 张一套必须同时在箱才能上交，见 _calc_sets 的 2026-10-28 事故说明）
        self._box_cur: int = 0
        self._box_used: int = 0      # 本 tick 换卡箱已占格数（空位 = cur - used）
        self._store_free: int = 0     # 本 tick 保险箱空位（炼成卡优先落这里）
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
        # 变卡这 tick 是否会供应 40 面卡：会 → 炼卡环节不许现造 40 面（靠变卡），
        # 保持卡箱空位最大化、不囤卡（用户 2026-10-18 定调）。每 tick 重算。
        self._gift_will_supply: bool = False
        # 最近一次算出的「变卡还没交、但马上会交」的 40 面卡（{套id: Counter}），
        # 供面板说明「为什么这轮不再炼 40 面卡了」
        self.face40_credit: dict[int, Counter] = {}
        # ★ 2026-09-26 去掉了「连败 N 次 → 静默 30 分钟」：用户定调「变卡失败要一直变，
        #   直到成功或者礼物卡没了」。失败也烧源卡，所以箱里的卡烧完必然收手，不需要静默。
        self.last_steal: dict | None = None   # 最近一次偷炉尝试结果
        self.steal_blocked: str = ""          # 本轮「能偷却没偷」的原因，供面板显示
        # 常去好友（用户指定，如 983688972）：若有值，选友时**优先**用它，
        # 仅当它被失败缓存或炉上挂着我未取回的卡时才退回「从最后往回扫」。
        self.preferred_steal_friend: str | None = None
        # ★ DP 调度开关（2026-10-24 用户「把界面加上」）：勾选后炼卡环节改用
        #   engine_v2 的 DPTickController —— 每 tick 用真实库存重解类型级 RCPSP
        #   （3 合 1 固定树 + 3 主炉 + 1 偷炉），取最优 start==0 动作执行；
        #   执行层仍走本引擎 API（买卡逐张过容量、偷炉挑好友+冷却+拉黑、库存同步全保留）。
        #   离线评测（16 场景）：低星/多套比贪心快 1~4h，高星单套与默认相当。
        self.dp_mode = False
        self._dp_models: dict = {}            # tid -> engine_v2 ThemeModel（懒加载）
        self._dp_ctrl = None                  # DPTickController 单例（跨 tick memo 热启动）
        self._dp_sig: tuple = None            # 控制器对应的队列签名（追加套卡时重建）
        self.dp_stats_last: dict | None = None  # 最近一次 decide 统计（面板透传）
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
                     pending: Counter | None = None, tid: int | None = None,
                     sets: int = 1) -> dict:
        p = self.plans[tid or self.tids[0]]
        return p.plan_summary(stock, pending, sets=sets)

    def net_plan(self, stock: Counter, pending: Counter, tid: int | None = None):
        p = self.plans[tid or self.tids[0]]
        return p.net_plan(stock, pending)

    # ---------- 套数（每轮排几套）★ 2026-10-03 用户定调 ----------
    @staticmethod
    def _card_secs(plan: "ThemePlan", cid: int) -> int:
        """炼一张 cid 需要的炉·秒（0 = 底卡，买来即用不占炉）。"""
        comp = plan.comp.get(cid)
        return int(comp[6]) if comp else 0

    def _set_furnace_hours(self, plan: "ThemePlan", top: int) -> float:
        """炼 1 套（1 张顶层卡）占用的炉·小时。

        把顶层卡的整条素材链**展开到可直接炼的 40 面**（更低层是买来的底卡、
        占 0 炉·h），逐张累加炼时。1 星七夕情人节：3 张 40 面各 1h + 1 张 150 面 4h
        = **7 炉·h/套**（这是 sets 时间上限的依据）。
        """
        total = 0
        stack = [int(top)]
        seen: set = set()
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            comp = plan.comp.get(c)
            if not comp:
                continue                            # 底卡：买即用，0 炉·h
            total += self._card_secs(plan, c)
            for m in (comp[3], comp[4], comp[5]):
                if m:
                    stack.append(int(m))
        return total / 3600.0

    def _target_ready(self, plan: "ThemePlan", target: int, stock: Counter) -> bool:
        """target 的素材此刻是否齐（能马上投炉）—— 时间感知排序用。

        与 `_prepare_turn` 的「投得进去」判定同口径：底卡（不在 comp 里，可买）永远
        算齐；成品素材必须在箱里（`stock` 够）。不齐的 target 投进去会被 `_prepare_turn`
        挡掉、白占一次判断，所以排序时排后面。
        """
        comp = plan.comp.get(target)
        if not comp:
            return True
        for m in (comp[3], comp[4], comp[5]):
            if not m:
                continue
            m = int(m)
            if m in plan.comp:                  # 成品素材：必须在箱里
                if stock.get(m, 0) < 1:
                    return False
        return True

    def _sort_time_aware(self, plan: "ThemePlan", runs: dict, stock: Counter) -> list:
        """把一套的候选炉次按「时间感知 + 落后度优先」排序。

        时间感知（★ 2026-10-03 用户定调，要考虑炼卡时间）：
          ① 就绪优先（ready）② 占炉时长降序（secs）③ 深度降序（depth）。

        ★ 新增「落后度优先」（2026-10-19 修）：同级内优先排「离顶层卡还差得最远」的
          组件及其瓶颈材料。否则末位用 **cid 升序**兜底，会系统性偏袒低 cid 的
          信陵君(4256)/白起(4257)、饿死高 cid 的管仲(4258) 及其独门料 蒙恬(4255)
          —— 表现出来就是「白起炼了一堆、管仲始终排不到、540 面西施永远炼不上」
          （用户原话：「白起都炼了 4 个了，就是不炼管仲，有这时间西施早炼上了」）。

          落后度 = runs[c] - stock[c]（净需求 − 箱存；pending 不传但量级足够排序）。
          40 面材料卡的紧急度 = 用它、**且仍在需求中(runs)的**最高层成品组件的落后度
          （蒙恬只喂管仲 → 紧急度=管仲落后度；李牧只喂白起 → =白起落后度）。
          这样每次 tick 重排都会自动先产蒙恬、先追平管仲，把炉位让给关键路径。
        """
        # 预计算：每张 40 面材料被哪些「成品卡(>40 面)」用到（缓存在 plan 上，只算一次）
        up = getattr(plan, "_upstream", None)
        if up is None:
            up = {}
            for cid in plan.ids:
                comp = plan.comp.get(cid)
                if not comp or int(plan.cards[cid][3]) <= CHANGE_FACE:
                    continue                    # 只从成品卡往下挂材料
                for m in (comp[3], comp[4], comp[5]):
                    if m:
                        up.setdefault(int(m), []).append(cid)
            plan._upstream = up

        def deficit(c):
            return runs.get(c, 0) - stock.get(c, 0)

        def urgency_pair(c):
            """返回 (瓶颈落后度, 被共用家数)。成品卡=自身落后度、被共用数 0；
            40 面材料=上游最高层组件落后度、以及被几家上游共用。"""
            if int(plan.cards[c][3]) > CHANGE_FACE:
                return (deficit(c), 0)
            us = [u for u in up.get(c, []) if u in runs]
            m = max(deficit(u) for u in us) if us else 0
            return (m, len(us))

        def key(c):
            ready = 0 if self._target_ready(plan, c, stock) else 1   # 0=就绪在前
            u1, ncons = urgency_pair(c)
            # 同瓶颈落后度内：被共用家数「越多」越优先（孙膑喂3家 > 王/吕喂2家 >
            # 蒙/李喂1家）。共用越多的 40 面材料卡住的上层越多、越该先造；否则会像
            # 七雄Q传那样把孙膑（3家共用）排到最后，信陵君一开炼吃掉孙/王/吕后孙膑
            # 迟迟不补，白起/管仲各晚1h、540面西施从理论H7拖到H9。
            # 独门料（蒙恬只喂管仲）不会被饿死：阶段二的「覆盖优先」保证同套5种40面
            # 在 H0~H1 全部开过炉，不受此处排序影响。
            return (ready, -self._card_secs(plan, c), -plan.depth.get(c, 0),
                    -u1, -ncons, c)
        return sorted(runs, key=key)

    def _calc_sets(self, plan: "ThemePlan", stock: Counter, pending: Counter,
                   free_slots: int = 0, box_cur: int = 0) -> int:
        """这一套**本轮同时排几套**（并行度，不是「总共能炼几套」）。

        ★★ 2026-10-28 真机事故（用户报「自动炼蜻蜓把同一个 40 面卡囤到 13 张、三箱全满、
        零上交」）：`_calc_sets_raw` 只看「囤得最多的那张 40 面」和炉位，**完全不看箱还剩
        几格**。1★ 一套 9 张卡要 6 张 40 面（3 张 40 面各 2 张 = 1 收藏 + 1 素材）；40 面
        一囤到 5~6 张 → sets 跳到 5~6 → net_plan 一次排 6 张顶层 + 12 张 40 面 ≈ 18 张
        半成品。而顶层要等**三张** 40 面全齐才能开炉 → 40 面只进不出 → 堆满换卡箱 →
        底卡买不进 → 炉子清空后永久停摆；且囤得越多 sets 越大，是**正反馈**
        （真机保险箱 168 格能装，所以一路堆到 13 张）。

        ★ 封顶对象是**在途并行度**，用**空位**而不是总容量（用户 2026-10-28 纠正：
        「换卡箱空位 9，收卡又没有奖励卡占格，那可以炼无限套」—— 一套的卡是滚动消耗的，
        只要**空位够一套流转**就能一直炼下去，`max_rounds=0` 不限轮次就是无限套；
        这里限的只是「同时铺开几套」，铺开太多才会「三张 40 面全在等」把箱塞死）。

        `_box_used` 来自本 tick 的 `parse_full`；容量未知（0）时不封顶，退回旧口径。

        ★★ 2026-11-05 缺口闸（用户定调，取代「只看换卡箱 18 格」的旧口径）：
          用户原话：「你看空位怎么不看两个箱，只看换卡箱，你不知道取的卡会优先进
          保险箱吗，只有买的 10 面卡才会进换卡箱」。

          真实落箱规则：**炼成的卡优先进保险箱，保险箱满了才溢到换卡箱；只有买的
          底卡一定进换卡箱**。所以「还能不能再往箱里塞一套」的判据是**两箱合计空位**，
          而不是换卡箱单箱空位。旧口径把保险箱只当库存计数（`_stock_from` 合算张数），
          从没把它当「换卡箱可以往外挪的承接位」，于是算不出「保险箱满 = 换卡箱也
          腾不出格」。

          真机 4821992 好少年(tid326) 事故链：超炼 4 张重复卡，正好吃掉「换卡箱 3 格
          + 保险箱 1 格」这 4 格 —— 这 4 格本来刚好够买齐缺的 4 张 10 面底卡。箱位
          一满，买卡一张也进不来，然后每 tick 空转几百次。

          闸门：`两箱空位 < 本套还缺张数` **且缺口全是商店底卡**（收尾集齐态）→
          返回 **0 套**（本轮不排任何炉次），因为在这种状态下投炉的产物也无处可放，
          只会把箱位锁得更死。
          **注意**：0 套只停「投炉/买卡」，收卡与上交照常走（tick 前半段）。

          ★★ 2026-11-05 补丁：闸门最初无条件按缺口冻结，真机 3822843 毒菇凉
          (tid1223) 立刻被误冻 —— 它缺 9 张里 **7 张是合成卡**（必须投炉产出），
          两箱只有 4 格。缺口含合成卡时缺口张数 ≠ 所需空位数：投炉消耗底卡腾格、
          产物出炉再取，全程只需 1~2 格流转。按缺口冻结 = 不投炉 = 缺口永不减，
          正是 11-04 死了 58 分钟的那个机制。故加 `_plan_base_only` 限定。
        """
        sets = self._calc_sets_raw(plan, stock, pending, free_slots)
        # ★★ 2026-11-09 用户验收线（禁单轮超炼）：
        #   自动推断的套数最多 2 套 —— 2 套 = 合法的「跨轮重叠」（相当于同时炼
        #   2 套同星卡，峰值 < 2N 可接受）。历史事故是 1★ 按 max(囤的 40 面)
        #   把 sets 抬到 5~6，只为凑 1 套却按 5~6 套重复炼料 = 单轮超炼，
        #   箱位被吃光、零上交。界面显式设的套数（surplus_sets>0）是用户主动
        #   要多套，不钳；MC_AUTO_MAX_SETS=0 可消融回旧口径（仅供 A/B）。
        if not (getattr(self, "surplus_sets", 0) and self.surplus_sets > 0):
            _cap_auto = int(os.environ.get("MC_AUTO_MAX_SETS", "2") or 0)
            if _cap_auto > 0:
                sets = min(sets, _cap_auto)   # sets<=0 的缺口闸语义保持不变
        cur = int(box_cur or getattr(self, "_box_cur", 0) or 0)
        if cur > 0 and plan.ids:
            free = cur - int(getattr(self, "_box_used", 0) or 0)
            # 缺口闸：两箱合计空位（换卡箱 + 保险箱）够不够放下还没到手的那些张
            store_free = int(getattr(self, "_store_free", 0) or 0)
            # ★ 2026-11-05（B3）：保险箱满时还能把「用不上的」保险箱卡寄存到简易箱，
            #   一张寄存 = 从保险箱腾 1 格给换卡箱的卡 → 等价于换卡箱多 1 格。
            #   `_simple_free` 是本 tick 拉到的可寄存张数（`_refresh_simple`），
            #   所以周转空间 = 两箱空位 + 可寄存数。
            parkable = int(getattr(self, "_simple_free", 0) or 0)
            relief = max(0, free) + max(0, store_free) + parkable
            need = sum(1 for c in plan.ids if stock.get(c, 0) < 1)
            # ★ 只有「收尾集齐态」才闸（2026-11-05 真机 3822843 误冻后补）：
            #   缺的卡必须**全部是商店底卡**（合成卡都在箱），这时缺口张数 = 必须
            #   一次性同时占住的格数，`两箱空位 < 缺口` 才是真装不下。
            #   若缺口含合成卡（要靠投炉产出），缺口张数 ≠ 需要的空位数 —— 投炉
            #   消耗底卡腾格、产物出炉再取，全程只需 1~2 格流转。这时按缺口冻结
            #   就是 11-04 那个死锁的翻版：不投炉 → 缺口永不减 → 空位涨不上来。
            #   真机反例：毒菇凉(tid1223) 缺 9 张里 7 张是合成卡、两箱只有 4 格，
            #   被无条件闸冻住，而它 09:30 前一直在正常滚动炼制。
            if need > 0 and relief < need and self._plan_base_only(plan, stock):
                self._log_freeze(plan, need, relief, free, store_free)
                self._last_sets[plan.tid] = 0     # 界面「套数」如实显示 0（DP 路径不写这个字典）
                return 0
            self._freeze_msg = ""      # 已解冻：下次再冻要重新记一行
            sets = min(sets, max(1, free // max(1, len(plan.ids))))
        self._last_sets[plan.tid] = sets
        return sets

    def _log_freeze(self, plan, need: int, relief: int, change_free: int,
                    store_free: int) -> None:
        """箱位不够时把「缺几格、哪边满」打成一条可执行的日志。

        只在原因变化时记一次（tick 45s，否则同一句话刷屏几百行，正是这次事故的
        日志形态：`腾位不足 还需 1 格` 连打 300+ 次）。
        """
        msg = (f"{plan.name} 暂停开新炉：还缺 {need} 张，两箱只有 {relief} 格"
               f"（换卡箱 {change_free} / 保险箱 {store_free}）"
               f"→ 还差 {need - relief} 格，需清卡箱才能继续")
        if msg == getattr(self, "_freeze_msg", ""):
            return
        self._freeze_msg = msg
        self._log("箱位不足", False, msg)

    def box_relief(self, stock: Counter) -> tuple[int, int, int]:
        """本 tick 两箱可用空位与本套缺口：返回 (换卡箱空位, 保险箱空位, 缺口)。

        给界面/预检用（`/api/auto/plan` 之类可以直接读），避免各处自己又按单箱算。
        """
        free = max(0, int(getattr(self, "_box_cur", 0) or 0)
                   - int(getattr(self, "_box_used", 0) or 0))
        store_free = max(0, int(getattr(self, "_store_free", 0) or 0))
        need = 0
        for tid in self.queue:
            p = self.plans.get(tid)
            if p:
                need += sum(1 for c in p.ids if stock.get(c, 0) < 1)
        return free, store_free, need

    def _calc_sets_raw(self, plan: "ThemePlan", stock: Counter, pending: Counter,
                       free_slots: int = 0) -> int:
        """套数原始口径（未加箱位封顶，只做消融对照用）。

        `surplus_sets`（用户设置的「套数」，默认 0）：
          · >0  —— 固定每轮排这么多套；
          · 0   —— **自动**：
               · 1 星套按「当前囤的 40 面卡能炼几套顶层卡」算（不设上限、炉位能排多少排多少）；
               · 其他星按「还剩几个**未收集**的 qq秀」定套数 = 流水线集满 qq秀、不空炉
                 （2026-10-12 改：旧逻辑非 1 星永远排 1 套，表现为「等一套齐后才炼新的一套」）。
                 已收集数来自 card_collection_get 的 qqshow_str（gift_list 把 t[9] 的 giftid
                 翻成实例 id 比对）。qq秀已全部收集则保持 1（仍持续炼、不空炉）。

        1 星「按囤卡算套数」：顶层卡（如七夕情人节 150 面）= 若干张 40 面各 1，
        能炼几套 = 这些 40 面持有数的**最小值**（瓶颈）。但 0 套会空炉，所以至少
        排 1 套（缺的那部分引擎会自己变卡/买底卡炼出来）。

        ★ 2026-10-03 修正：套数按「**囤得最多的那张 40 面卡**」(`max`) 算、只以炉位数
        封顶。曾用「顶层炼时(4h)×炉位/每套炉·h(7)」做 time_cap，那是**错的** —— 引擎
        45s 一 tick **持续**跑，固定 4h 窗口把套数掐死成 1~2，囤 6 张也只炼 1 套
        （空炉 + 囤货白囤）。防买爆底卡交给 MAX_BUY_PER_TICK + 换卡箱容量。
        """
        if self.surplus_sets and self.surplus_sets > 0:
            return int(self.surplus_sets)
        if plan.star != 1:
            # 非 1 星：按「还剩几个未收集 qq秀」定套数（流水线集满 qq秀、不空炉），
            # 至少 1、以炉位数+1 封顶；qq秀已全部收集则保持 1（仍持续炼、不空炉）。
            collected = self._tick_collected.get(plan.tid, set())
            gmap = _gift_info_map()
            uncollected = sum(1 for g in plan.gifts
                              if (inst := gmap.get(g)) and inst[0] not in collected)
            sets = max(1, uncollected) if uncollected else 1
            cap = max(1, free_slots + 1) if free_slots else sets
            return max(1, min(sets, cap))
        # 1 星：找顶层卡（depth 最大）及其需要的 40 面卡
        if not plan.ids:
            return 1
        maxd = max((plan.depth.get(c, 0) for c in plan.ids), default=0)
        tops = [c for c in plan.ids if plan.depth.get(c, 0) == maxd and plan.comp.get(c)]
        if not tops:
            return 1
        comp = plan.comp[tops[0]]
        ing = [int(m) for m in (comp[3], comp[4], comp[5]) if m]
        if not ing:
            return 1
        have = [stock.get(c, 0) + pending.get(c, 0) for c in ing]
        # ★ 2026-10-03 修正为 **max(持有)**（原 min 是错的）：
        #   用户意图「按**已囤的卡**炼多套」——囤了 6 张种生求子就想炼 6 套，
        #   缺的求姻缘/斗巧**引擎自己变卡/买底卡炼出来**，不该拖低套数。
        #   min 会让「种6/姻0/巧0」这种（求姻缘/斗巧还没炼好）恒算成 1 套，
        #   囤 6 张也只炼 1 套 → 空炉 + 囤货白囤（真机复现：预检判「已凑齐」硬挡）。
        by_stock = max(have)                   # 囤得多的那张 = 能炼几套
        sets = max(1, by_stock)                # 至少 1 套（缺料引擎自己补，不空炉）
        # 兜底以炉位数封顶：一轮最多把空闲炉位排满，剩余的下一 tick 继续（持续循环）。
        # ⚠ 之前用「顶层炼时(4h)×炉位/每套炉·h」做 time_cap 是**错的**：引擎是 45s
        #   一 tick 持续跑的，不是「只看 4 小时的单轮」，固定 4h 窗口把套数掐死成 1
        #   （4炉×4h/7炉·h=2），囤 6 张也只炼 1~2 套 → 空炉 + 囤货白囤。
        #   防「一次买爆底卡」交给 MAX_BUY_PER_TICK(每 tick 买卡上限) + 换卡箱容量，
        #   不再用固定时间窗掐套数。
        cap = max(1, int(free_slots) + 1) if free_slots else sets
        return max(1, min(sets, cap))

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
                                  self._pending, set(self._keep),
                                  self._change_rot)

    # ---------- 日志 ----------
    def _log_change_attempts(self, attempts: list[dict] | None, label: str) -> None:
        """把变卡的逐张明细写成日志：消耗的源卡名 → 变出的目标卡名 + 成功/失败。

        ★ 2026-10-03 用户定调「每次变卡成功都要记录成功，要把消耗的卡和变的卡名字
        都显示出来」：此前只写一条汇总（`changed=N`），逐张成败不落盘，导致「日志零
        成功但卡箱多出好几张」无法核对。这里每张单独一行，成功用 ok=True。
        """
        for a in attempts or []:
            src = a.get("src_name") or a.get("src_id")
            dst = a.get("dst_name") or a.get("dst_id")
            if a.get("ok"):
                detail = f"{src} → {dst} 成功"
            else:
                extra = a.get("msg") or f"code={a.get('code')}"
                stage = "收礼失败" if a.get("stage") == "take" else "变卡失败"
                detail = f"{src} → {dst} {stage}（{extra}）"
            self._log(label, bool(a.get("ok")), detail)

    def _log_throttled(self, key: str, action: str, ok: bool, detail: str,
                       window: float = 900.0) -> None:
        """同类日志节流：同一 key 在 `window` 秒内只打一行。

        ★ 2026-11-05 真机：卡箱锁死时 `腾位不足 还需 1 格` + 3 条「移入保险箱失败」
          每 45s 刷一轮，日志里连打 300+ 行全是同一句，把有用的信息全冲掉了。
          状态没变就不该重复记 —— 失败计数已经由 `stats` 统计。
        """
        now = time.time()
        last = self._throttle.get(key)
        if last is not None and now - last < window:
            return
        self._throttle[key] = now
        self._log(action, ok, detail)

    def _log(self, action: str, ok: bool, detail: str) -> None:
        self.log.append({"ts": time.time(), "action": action, "ok": ok, "detail": detail})
        if not ok:
            self.stats["errors"] += 1
        self.log[:] = self.log[-200:]
        # 落盘到对应账号的 <uin>.log（不进 global），重启可查 —— 这就是界面那条滚动日志的存档
        try:
            LOG_UIN.set(getattr(self.s, "uin", "") or "")
            engine_logger.info("%s %s %s", "✓" if ok else "✗", action, detail)
        except Exception:  # noqa: BLE001
            pass

    def _log_state_delta(self) -> None:
        """翻牌/上交之后，把主界面数值字段的变化记一行——用来给「未知道具」对号。"""
        if not self._pai_happened or not self.state_delta:
            return
        top = [d for d in self.state_delta if abs(d["delta"]) > 0][:6]
        if not top:
            return
        txt = "，".join(f'{d["field"]} {d["from"]}→{d["to"]}（{d["delta"]:+d}）' for d in top)
        self._log("状态变化", True, txt)

    async def _submit_album(self, tid: int, steps: list[dict]) -> bool:
        """把一套集齐的卡上交到集卡册（服务端消耗整套各 1 张），返回是否尝试过。

        ★ 2026-11-05 抽成方法（紧箱仿真暴露）：旧流程里上交是 tick 第 3 步、
          「收尾买底卡」是第 5 步 —— 于是**买齐之后要等下一个 tick 才提交**，
          中间整整一个 45s 的窗口：箱是满的、已经集齐，此时任何取卡都会挤掉
          一格买底卡的位置，引擎只能靠卖底卡腾位（真机与仿真 C1/C3 都复现：
          「卖 大显身手 / 消防少年 腾位」→ 缺口 +2 → 永远收敛不了）。
          现在收尾买完底卡可以在**同一个 tick 内**直接提交，窗口消失。

        ★ 上交挑「未收集」的 qq秀：遍历 t[9] 各 giftid，用 gift_list 翻成实例 id，
          选第一个不在已收集列表里的（2026-10-12 修：旧逻辑永远传 t[9] 首项，
          套内首个 qq秀已收集时反复上交同一个已收集的，永远集不满）。
        """
        plan = self.plans[tid]
        gmap = _gift_info_map()
        collected = self._tick_collected.get(tid, set())
        chosen = plan.giftid
        chosen_inst = None
        chosen_name = None
        for g in plan.gifts:
            inst, nm = gmap.get(g, (None, ""))
            if inst and inst not in collected:
                chosen, chosen_inst, chosen_name = g, inst, nm
                break
        r = await self.s.post(EP_COLLECTION_ADD, themetype=0, giftid=chosen,
                              cardtype=1, theme=tid)
        code = r.get("code")
        ok = code == 0
        msg = ("成功放入集卡册" if ok
               else ((r.get("message") or "").strip() or err_msg(code)))
        detail = f"{plan.name}: {msg}"
        if chosen != plan.giftid:
            detail += f"｜选未收集qq秀 {chosen_name}({chosen_inst})"
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
            # 把刚收集的 qq秀实例写回缓存，避免同缓存窗口（20s）内下一轮重复选它
            if chosen_inst:
                self._show_cache.setdefault(tid, set()).add(chosen_inst)
                self._tick_collected.setdefault(tid, set()).add(chosen_inst)
            if self.max_rounds and plan.rounds >= self.max_rounds:
                plan.done = True
                if tid in self.queue:
                    self.queue.remove(tid)
                self._log("完成", True, f"{plan.name} 已达 {self.max_rounds} 轮，移出队列")
        steps.append({"action": "上交集卡册", "ok": ok, "detail": detail})
        self._log("上交", ok, detail)
        return True

    # ---------- 单步 ----------
    async def tick(self) -> list[dict]:
        steps: list[dict] = []
        mp = await self.s.post(EP_MAINPAGE, code="", opuin=self.s.uin)
        if mp.get("code") != 0:
            self._log("读取卡箱", False, f"code={mp.get('code')}")
            return steps
        raw0 = mp.get("raw", "")
        st = parse_full(raw0)
        # 换卡箱容量与占用（供 `_calc_sets` 用**空位**给并行套数封顶；0 = 未知则不封顶）
        self._refresh_caps(st)
        # 简易箱可寄存张数（只在两箱紧时才多打这一次接口）——保险箱满时的周转空间
        await self._refresh_simple(st)
        self.randchance = st.get("randchance", 0)
        # 跨 tick 差分：上一 tick 翻过牌 → 此刻的变化就是那些奖励的去向
        cur_fields = parse_state_fields(raw0)
        self.state_delta = diff_state(self._prev_fields, cur_fields)
        self._log_state_delta()
        self._prev_fields = cur_fields

        # 1) 收卡：flag=2 已完成
        self._pai_happened = False
        stock_now: Counter = Counter()
        for x in st["change"] + st["store"]:
            stock_now[int(x["id"])] += 1
        # 炉内在途（在炼 + 已炼成待收）——判「这张到底是不是凑齐必需」要连在途一起
        # 算，否则「炉里那第二张 81 是炼 82 的素材」会被误判成多余（见
        # `_is_collect_essential` 判据②）。
        pending_now: Counter = Counter()
        for x in st["stove"]:
            if int(x.get("id") or 0) > 0:
                pending_now[int(x["id"])] += 1
        for c in st["stove"]:
            if int(c.get("flag") or 0) == 2 and c["id"] > 0:
                slot = c["slot"]
                opu = c.get("opuin")
                is_self = not (opu and str(opu) != str(self.s.uin))
                # ★ 2026-11-02 用户指正（真机经典女套装）：箱位刚好只够凑齐这一套时，
                #   把「对凑齐零贡献」的多余卡先留在炉里，别取出来占格。
                #   现场：两箱空位 7，凑齐还差 7 格（1 张 150「整套搭配」+ 6 张 10 面
                #   底卡），零余量；炉里却还躺着多余的 80 和第二张 82 —— 各取一张就
                #   占 1 格，最后买底卡少一格凑不齐。这张 80 放着不取，等整箱上交
                #   （释放全部格位）后再取，代价只是占一个炉位。
                if self._should_defer_take(int(c["id"]), st, stock_now, pending_now):
                    nm = card_name(c["id"])
                    why = getattr(self, "_defer_reason", "") or "箱位不够"
                    self._log("收卡", True,
                              f"{nm} 暂缓取出：{why}，先留炉里，腾出空位马上取")
                    steps.append({"action": "收卡", "ok": True,
                                  "detail": f"{nm} 暂缓（空位留给凑齐）"})
                    continue
                # ★ 2026-11-04 用户澄清取卡口径（别再把「2 格」当成每张卡的占用量）：
                #   服务端那句「亲，可能会出现幸运事件，请空出两个卡箱的位置，再取卡！」
                #   是**准入门槛**（取卡可能触发幸运事件，得奖要多占 1 格），
                #   不是每张卡占 2 格。实际取一张只占 1 格，没得奖就还剩 1 格 →
                #   所以是**逐个取、每取一张按新空位重判**：本地 `used += 1` 就是
                #   这个口径，空 4 格完全可能连取 4 张（中间得一次奖就少取一张）。
                #   本地不足 2 格时先腾位，免得白打一次注定被拒的请求。
                if self._free_change(st) < 2:
                    freed0 = await self._make_room(
                        self.queue[0] if self.queue else 0, 2, st, steps)
                    if freed0:
                        # `_make_room` 自己重扫 snapshot，不回写外部 st → 这里按
                        # 服务端确认腾出的格数把本地 used 减回去（freed0 是实腾数）。
                        st["cap"]["change"]["used"] = max(
                            0, int(st["cap"]["change"].get("used", 0)) - freed0)
                    if self._free_change(st) < 2:
                        nm0 = card_name(c["id"])
                        self._log("收卡", True,
                                  f"{nm0} 暂缓：换卡箱只剩 {self._free_change(st)} 格"
                                  f"（取卡要空 2 格防幸运事件），腾不出来，留炉里")
                        steps.append({"action": "收卡", "ok": True,
                                      "detail": f"{nm0} 暂缓（换卡箱不足 2 格）"})
                        continue
                r = await self._take_card(slot, opu, is_self)
                if not r.get("ok") and self._is_box_full(r):
                    # ★ 2026-10-26 真机死锁（用户报「买 10 面把卡箱占满、取不出 40 面」）：
                    #   换卡箱满 → 收卡被拒（"您的卡片箱满了…"）→ **炉位不释放** →
                    #   没有空炉炼箱里的 10 面 → 10 面烧不掉 → 箱永远满 → 永远取不出。
                    #   真机实测连续几十个 tick 全是这三条失败，引擎一次都没腾箱。
                    #   收卡是最高优先级（不取卡整条流水线停摆），必须立刻腾位重试。
                    freed = await self._make_room(
                        self.queue[0] if self.queue else 0, 2, st, steps)
                    if freed > 0:
                        r = await self._take_card(slot, opu, is_self)
                nm = card_name(c["id"])
                steps.append({"action": "收卡", "ok": r.get("ok"), "detail": f"{nm}: {r.get('message')}"})
                self._log("收卡", bool(r.get("ok")), f"{nm} {r.get('message')}")
                if not r.get("ok"):
                    continue
                # 取进换卡箱：同步本地库存与箱占用，供后续这类「够不够装下」的判断用
                stock_now[int(c["id"])] += 1
                pending_now[int(c["id"])] = max(0, pending_now[int(c["id"])] - 1)
                st["cap"]["change"]["used"] = st["cap"]["change"].get("used", 0) + 1
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

        # 1.42) 预测「变卡这 tick 是否会供应 40 面卡」：会 → _refine_all 不许现造 40 面
        #       （靠变卡），保持卡箱空位最大化、不囤卡（用户 2026-10-18 定调）。
        #       箱里已有礼物套 48 面卡（box_gift_change 会就地变）→ 会变出 40 面；
        #       礼物箱件数 ≥ upper（gift_drain 会收+变）→ 也会变出 40 面。
        #       用 tick 开头的 st（变卡之前）判断，即「本 tick 会变出多少」的预测。
        if self.box_gift_change or self.gift_drain:
            box_gift_n = self._gift_cards_left(st)        # 箱里已有的礼物套 48 面卡
            gift_box_n = 0
            if self.gift_drain:
                try:
                    gi = await gf.gift_inbox(self.s)
                    gift_box_n = int((gi or {}).get("count", 0) or 0)
                except Exception:  # noqa: BLE001
                    gift_box_n = 0
            self._gift_will_supply = (
                (self.box_gift_change and box_gift_n > 0)
                or (self.gift_drain and gift_box_n >= int(self.gift_upper))
            )
        else:
            self._gift_will_supply = False

        # 1.45) 卡箱里已有的礼物套 48 面卡 → 就地变卡（不收礼，见 _drain_box_gift）。
        #      变卡动过卡箱状态，_drain_box_gift 会重读并回传最新 rd，下面第 2 步直接用。
        if self.box_gift_change:
            rd_now = (await self._drain_box_gift(
                steps, rd_now[1] if rd_now else None, rd_now) or rd_now)

        # 1.5) 礼物箱快满 → 收礼 + 变卡成目标卡（可选环节）
        #      变出来的 40 面卡直接进卡箱 → 下面第 2 步重读状态时就当素材了，
        #      于是本来要「买 10 面卡 + 自己炼」的那一步可以直接省掉。
        if self.gift_drain:
            await self._gift_drain(steps, rd_now[0] if rd_now else st)

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

        # 预读本 tick 各套已收集 qq秀（一次网络，全库缓存）。用途：
        # ① 上交挑「未收集」的 qq秀；② 非 1 星按未收集数定套数（流水线集满，不空炉）。
        if self.queue:
            await self._collected_shows(self.queue[0])
        self._tick_collected = self._show_cache

        # 3) 集齐则上交（按队列逐个检查：每套各自上交、各自计轮次）
        progressed = False
        for tid in list(self.queue):
            plan = self.plans[tid]
            if not all(stock[c] >= 1 for c in plan.ids):
                continue
            await self._submit_album(tid, steps)
            progressed = True
        if progressed:
            # 上交消耗了整套卡，库存变了；重读后**立刻**续炼下一轮（流水线、炉位不空，
            # 不等下一 tick 干等 45s）。旧逻辑这里直接 return，导致每轮上交后炉子空 45s、
            # 表现为「等一套齐后才炼新的一套」。
            self._log("上交后", True, "重读库存、流水线续炼下一轮")
            rd2 = await self._read_state()
            if rd2 is not None:
                st, stock, slots, pending = rd2
                self._stock, self._pending = stock, pending

        # 4) 把**空着的炉位**分给各套（多套并行，长炉时不让炉位闲着）
        #    DP 调度（勾选时）：改用 DPTickController 每 tick 重解最优调度；
        #    执行层（买卡/炼卡/偷炉/库存同步）仍在 _dp_refine_all 里走本引擎 API。
        if self.dp_mode:
            await self._dp_refine_all(st, stock, slots, pending, steps)
        else:
            await self._refine_all(st, stock, slots, pending, steps)

        # 5) 收尾：**除底卡外的所有卡都已在卡箱里**（不是在炉里），只差商店底卡
        #    → 只给这一套把底卡买齐，买齐立刻上交（上交消耗整套、格位当场释放）。
        if self.buy:
            for tid in list(self.queue):
                plan = self.plans[tid]
                # ★★ 2026-10-27 用户定调（真机纠正）：
                #   「买底卡的时机在除底卡外的所有卡都在卡箱子里，现在买齐直接上交，
                #     **而不是还在炼呢就要买齐**；只差底卡也不需要买（紧箱时少一个
                #     空位就取不了卡）」。
                #   旧行为：`成品卡全在箱**或在炉**` 就算「只差底卡」→ 顶层还要 4h
                #   时就把 5 张 10 面底卡买进换卡箱，白占 5 格；换卡箱硬顶 18、其中
                #   9 格还是用户藏品卡 → 少一个取卡空位 → 「请空出两个卡箱的位置」。
                #   现要求：本套 **pending 必须为空**（炉里一张都没有）+ 非底卡
                #   **全部在箱**，才买底卡；买完同一个 tick 就上交，占用只剩几秒。
                # ★ 2026-11-02 用户指正（补丁）：pending 判据此前是「本套**任何**卡在炉里
                #   就不买底卡」—— 于是箱位紧时按 `_should_defer_take` 故意留在炉里的
                #   那张多余 40 面，会反过来把收尾买底卡**永远堵死**（两处规则互相打架）。
                #   现在只把「凑齐还缺、必须从炉里出来」的卡算作阻塞；多余卡留炉里不阻塞。
                if any(pending.get(c, 0) > 0 and stock.get(c, 0) < 1 for c in plan.ids):
                    continue
                miss_comp = [c for c in plan.ids if c in plan.comp and stock.get(c, 0) < 1]
                miss_base = [c for c in plan.ids if c not in plan.comp and stock.get(c, 0) < 1]
                if miss_comp or not miss_base:
                    continue
                # ★★ 2026-11-05 物理装不下就别硬买（真机空转的真因）：
                #   两箱空位 < 还缺的底卡张数时，逐张「卖一张 → 买一张」只会原地打转
                #   ——卖掉的正是集卡册还要的底卡，买回来又满，缺口一格没少。
                #   真机 4821992 日志：卖智慧少年 → 买大显身手 → 卖消防少年 →
                #   买消防少年 → 再满…… `stats.bought` 一直涨、集卡册永远差 4 张。
                #   这种状态唯一的出路是**清卡箱**（挪走别主题卡 / 卖重复卡），
                #   引擎能做的就是把「差几格、哪边满」报清楚，别再空转。
                free2 = self._free2(st)
                # ★ 2026-11-05（B3）：保险箱满时还能靠「寄存简易箱」周转，
                #   周转空间 = 两箱空位 + 可寄存张数（见 `_refresh_simple`）。
                relief = free2 + int(getattr(self, "_simple_free", 0) or 0)
                if len(miss_base) > relief:
                    self._log_throttled(
                        f"albumfull:{tid}", "买卡", False,
                        f"{plan.name} 还缺 {len(miss_base)} 张底卡，两箱只剩 {free2} 格"
                        f"（换卡箱 {self._free_change(st)} / 保险箱 {self._store_free} / "
                        f"可寄存简易箱 {getattr(self, '_simple_free', 0)}）"
                        f"→ 还差 {len(miss_base) - relief} 格，物理装不下，先别买；"
                        f"请清卡箱：把别主题卡移进保险箱、或处理掉套内重复卡")
                    continue
                for cid in miss_base:
                    if self._free_change(st) <= 0:
                        # ★ 收尾补集卡册底卡也要能腾位（此前只在 _prepare_turn 腾，
                        #   这里换卡箱满就报「0 空位」买不进底卡 → 集卡册永远补不齐）。
                        freed = await self._make_room(tid, 1, st, steps)
                        if freed > 0:
                            rd = await self._read_state()
                            if rd is not None:
                                st, stock, slots, pending = rd
                    if self._free_change(st) <= 0:
                        self._log_throttled(
                            f"albumbuy:{tid}", "买卡", False,
                            f"卡箱 0 空位，{card_name(cid)} 买不进来："
                            f"{plan.name} 集卡册还差 {len(miss_base)} 张底卡 —— 这是真的空间不足")
                        break
                    r = await self.s.post(EP_BUY, card_id=cid, theme_id=tid)
                    ok = r.get("code") == 0
                    if ok:
                        self.stats["bought"] += 1
                        self._mark_bought(cid)
                        stock[cid] += 1
                        st["cap"]["change"]["used"] = st["cap"]["change"].get("used", 0) + 1
                    msg = r.get("message") or err_msg(r.get("code"))
                    self._log("买卡", ok, f"{card_name(cid)}（{plan.name} 补集卡册） {msg}")
                    steps.append({"action": "买卡", "ok": ok,
                                  "detail": f"{card_name(cid)}（{plan.name} 补集卡册） {msg}"})
                    if not ok:
                        break
                # ★★ 买齐就**在同一个 tick 内**上交（2026-11-05 紧箱仿真 C1/C3）：
                #   旧流程买完只是 return，提交要等下一个 tick 的第 3 步 —— 中间
                #   一整个 45s 窗口箱是满的、已经集齐，此时任何取卡都得先卖底卡腾位，
                #   卖掉一张缺口 +1，永远收敛不了。买完立刻提交就把窗口关掉。
                if all(stock.get(c, 0) >= 1 for c in plan.ids):
                    await self._submit_album(tid, steps)
                    rd = await self._read_state()
                    if rd is not None:
                        st, stock, slots, pending = rd
                        self._stock, self._pending = stock, pending
                        self._refresh_caps(st)
        return steps

    # ---------- 变卡与炼卡共用的一套 40 面卡口径 ----------
    def _gift_cards_left(self, st: dict) -> int:
        """卡箱里还有几张礼物套 48 面卡 = 变卡环节接下来还能产出多少张 40 面卡。

        只读 `st`（本 tick 第 2 步那份，变卡之后重读过），不额外发请求。
        """
        ids = gf._face48_ids()
        return sum(1 for box in ("change", "store")
                   for c in st.get(box, []) if c.get("id") in ids)

    def _face40_credit(self, plan: "ThemePlan", st: dict,
                       stock: Counter | None = None,
                       pending: Counter | None = None) -> Counter:
        """这套卡「变卡环节还没交、但马上就会交」的 40 面卡 -> 净需求里的虚拟库存。

        用户 2026-09-26 指出：「已经变了 40 的卡，怎么不去用 40 的去练 80 的，
        还要再炼 40 的，那变卡的意义在哪」—— 原来的净需求只认手上的库存，
        变卡计划要产出的那几张不算数，于是炼卡环节转头又拿底卡炼了一张同款 40 面卡
        （真机日志实证：17:58 同一个 tick 里先变出移液器/水浴锅，紧接着又炼了
        液氮罐/水浴锅/移液器三张 40 面卡）。这里把它们先顶上，炼卡环节就不再重复炼。

        ⚠️ 只顶「变卡环节真能产出的量」（卡箱里还剩几张礼物卡），且只在变卡开关
        打开时生效；礼物卡用完 credit 自然归零，净需求退回「拿底卡炼 40 面」，
        不会死锁。

        `stock` / `pending` 可显式传入（DP 路径在 tick 中段会重读一次状态，
        此时成员 `self._stock` 还是旧快照 —— 用新快照算缺口才是对的）。
        """
        if not (self.box_gift_change or self.gift_drain):
            return Counter()
        sk = self._stock if stock is None else stock
        pd_ = self._pending if pending is None else pending
        gap = face40_gap(plan, sk, pd_)["gap"]
        if not gap:
            return Counter()
        cap = self._gift_cards_left(st)
        if cap <= 0:
            return Counter()
        # 与 `_pick_change_target` 同一优先级：缺口大的先补，同缺口按卡 id 稳定排序
        order = sorted(gap, key=lambda c: (-int(gap[c]), int(c)))
        out: Counter = Counter()
        left = cap
        # 轮着发：先**每个还缺的卡 id 各 1 张**（让「还得从底card炼 40 面」的种类尽量
        # 变少，这就是用户要的「拿变出来的 40 面去炼 80」），发完一轮再补第二张。
        while left > 0:
            moved = False
            for cid in order:
                if left <= 0:
                    break
                if out.get(int(cid), 0) >= int(gap[cid]):
                    continue
                out[int(cid)] += 1
                left -= 1
                moved = True
            if not moved:
                break
        return out

    # ---------- DP 调度（engine_v2 DPTickController，2026-10-24 接界面） ----------
    def _dp_model(self, tid: int):
        """懒加载 engine_v2 ThemeModel（包装同一张 ThemePlan，静态量无副作用）。"""
        m = self._dp_models.get(tid)
        if m is None:
            from .engine_v2.model import ThemeModel
            m = ThemeModel(tid)
            self._dp_models[tid] = m
        return m

    async def _dp_refine_all(self, st: dict, stock: Counter, slots: dict,
                             pending: Counter, steps: list[dict]) -> None:
        """DP 控制器版炼卡环节（勾选「DP 调度」时走这里，替代 _refine_all）。

        分工：DP 决定「这一 tick 炼什么/买什么」（每 tick 用真实箱内库存+在炉重解
        类型级 RCPSP，取最优 start==0 动作）；执行仍走本引擎的 API 与全部兜底
        （买卡逐张过容量、偷炉挑好友+失败冷却+拉黑、本地库存/炉位同步）。
        异常绝不卡死生产：decide 抛错只记日志、本 tick 不炼，下 tick 重试。
        """
        if not self.queue:
            return
        from .engine_v2.world import WorldState
        # 队列变化（追加套卡）→ 控制器模型集合跟上（memo 热启动作废，重建）
        sig = tuple(self.queue)
        if self._dp_ctrl is None or self._dp_sig != sig:
            from .engine_v2.dp_controller import DPTickController
            self._dp_ctrl = DPTickController(
                {t: self._dp_model(t) for t in self.queue}, machines=3,
                # ★ 生产固定 2s 全程（不做快速分层降级）：生产是真实操作、
                # 真卡真炉，且 decide 每 tick 只调一次，2s 可接受；而分层快
                # 路径的 0.1s 在「重解稀疏」实例上会让 makespan 差 1~2h
                # （2★ 23.10→25.07h，见 dp_controller「自适应分层预算」注释）。
                # 仿真/bench 走默认（warm=8 后 0.1s），不受此处影响。
                time_budget=2.0, time_budget_cold=2.0, warm_solves=10**9,
                steal_enabled=bool(self.steal_enabled),
                # ★ 多套（2026-10-27 接入）：DP 此前 `_union_need0` 里 `sets = 1`
                #   写死，前端「套数设置」在 DP 下形同虚设，1 星囤了多张 40 面也只
                #   排 1 套（空炉 + 囤货白囤）。这里直接把生产那份 `_calc_sets`
                #   传进去 —— 贪心与 DP 用同一个套数口径，不写两套逻辑。
                sets_fn=lambda t, stk, pd_, fm: self._calc_sets(
                    self.plans[t], stk, pd_, fm))
            self._dp_sig = sig
        # ★ 决策前重读真实状态（2026-10-26，仿真=生产统一后实测出的坑）：
        # tick 早段（收礼/翻牌/变卡/上交后续炼）会把新卡落进换卡箱，而传进来的
        # st/slots 是那一刻的快照 → DP 拿着**过期的 change_free** 排买卡：
        # 4★ 实测 tick687 DP 以为还剩 2 格、真实箱 18/18，于是发出 4 张买卡
        # （k=2,3 全部「卡箱 0 空位」）；后续 tick 靠腾箱 freed 继续发，累计
        # 220 次失败买卡。多一次 GET 换决策输入正确，45s 周期下成本可忽略。
        rd = await self._read_state()
        if rd is not None:
            st, stock, slots, pending = rd
            self._refresh_caps(st)     # 缺口闸/套数封顶必须用重读后的真实箱容
        ws = WorldState.from_parsed(st, now=int(st.get("now") or 0))
        # ★ 主炉台数随真机走（2026-11-04，道具卡把主炉 3→4）：`<stovebox stovenum>`
        #   是权威值（`cur` 恒 7 不变，只有 stovenum 与 `<stove id=0..3>` 跟着变）。
        #   以前 `machines=3` 写死 → 4 炉里永远只按 3 台排期，多出的那台一直空转；
        #   更糟的是 `_main_cap` 拿「按卡数的 running」减 machines，用户往炉里排队
        #   一张就让它变负 → 投料额度恒 0。
        n_stove = int((st.get("cap", {}).get("stove", {}) or {}).get("stovenum") or 0)
        if n_stove > 0 and n_stove != self._dp_ctrl.machines:
            prev_stove = self._dp_ctrl.machines
            self._log("DP调度", True,
                      f"主炉台数 {prev_stove} → {n_stove}（按真机 stovenum）")
            self._dp_ctrl.machines = n_stove
            if n_stove < prev_stove:
                # ★ 加炉道具到期缩容的**取证**（2026-11-04）：服务端到底是
                #   ①让超额炉继续炼完、还是 ②把那张卡直接停掉，用户也无法确认。
                #   首次真机到期时把超额炉的卡原样打出来（id/flag/剩余），
                #   下次看日志就能定性，不用再猜。
                self._log("DP调度", True, _shrink_report(st, n_stove))
        # ★★ 2026-11-14：把「此刻偷炉还偷不偷得到」同步给 DP —— 拿不到第 5 台
        #   就要按 M 台重排，而不是继续按 M+1 台估工期。
        #   判据复用执行层已有的失败策略状态（`_steal_fail_policy` 设的冷却）：
        #   冷却未到期 = 偷不到。已有偷炉在炼时容量仍是 1（池被自己的作业占着，
        #   不是不可用；否则在炉的 class-2 作业会超出容量）。
        _steal_busy = any(getattr(f, "steal", False) and f.cid > 0
                          for f in ws.furnaces)
        _now = time.time()
        _cooling = self._steal_cooldown_until > _now
        if _steal_busy:
            self._steal_down_until = 0.0        # 已在偷 = 第 5 台在用，立刻恢复
        elif _cooling and self._steal_down_until <= _now:
            # 刚进冷却 → 降级并锁定 90s（≈2 tick）。不锁的话 60s 短冷却会变成
            # 「降→升→降」每 tick 反复作废 memo 重解，抖动成本大于收益。
            self._steal_down_until = _now + 90
        _steal_avail = _steal_busy or (not _cooling
                                       and self._steal_down_until <= _now)
        if self._dp_ctrl.set_steal_cap(_steal_avail):
            # 容量一变骨架与 memo 全部作废（控制器内做），这里只留可观测记录
            self._log("DP调度", True,
                      (f"偷炉可用 → DP 按 {n_stove + 1} 台排"
                       if _steal_avail else
                       f"偷炉暂不可用 → DP 按 {n_stove} 台重排（拿不到第 "
                       f"{n_stove + 1} 台就不按它估工期）"))
        if not self.steal_enabled:
            ws.steal_free = 0      # 没勾偷炉 → DP 不排偷炉（与贪心 can_steal 同口径）
        completed = {t: self.plans[t].rounds for t in self.queue}
        # ★ 变卡 credit 接入 DP（2026-10-27）：`_face40_credit` 此前只在贪心
        #   `_refine_all` 里用，DP 路径完全不知道「变卡环节马上要交 40 面卡」→
        #   于是 DP 又拿底卡现造同款 40 面（一炉一炉白烧），变卡等于白变。
        #   这里按与贪心同一个函数算，交给控制器并进净需求库存。
        credits: dict[int, Counter] = {}
        for t in self.queue:
            cr = self._face40_credit(self.plans[t], st, stock, pending)
            if cr:
                credits[t] = cr
        self.face40_credit = credits or {t: Counter() for t in self.queue}
        try:
            actions, dpstats = self._dp_ctrl.decide(
                ws, list(self.queue), completed, self.max_rounds,
                credits=credits)
        except Exception as exc:  # noqa: BLE001  DP 异常不拖死生产循环
            # _log(ok=False) 自身会累加 stats["errors"]，这里别再 +1
            self._log("DP调度", False, f"decide 异常：{str(exc)[:120]}")
            return
        self.dp_stats_last = {"solve_ms": dpstats.get("solve_ms"),
                              "n_types": dpstats.get("n_types"),
                              "bounded": dpstats.get("bounded"),
                              "skip": bool(dpstats.get("skip_solve")),
                              "resolves": dpstats.get("resolves"),
                              "plan_ops": dpstats.get("plan_ops"),
                              "streak": dpstats.get("streak"),
                              "discrepancy": dpstats.get("discrepancy"),
                              "emergency_now": dpstats.get("emergency_now"),
                              "early_now": dpstats.get("early_now"),
                              "box_relaxed": dpstats.get("box_relaxed"),
                              # 紧箱物理无解：把停手原因透传到 /api/auto/status
                              "reason": dpstats.get("reason"),
                              "deadlock": dpstats.get("deadlock")}
        # cid -> 所属套（买卡要带 theme_id；合成卡/底卡都可能被买）
        owner: dict[int, int] = {}
        for t in self.queue:
            for c in self.plans[t].ids:
                owner.setdefault(c, t)
        # (slot, slottype) -> cid：执行成功后同步本地库存用
        slot2cid: dict[tuple, int] = {}
        for cid, lst in slots.items():
            for sl in lst:
                slot2cid[(sl[0], sl[1])] = cid
        # 本 tick 买过卡 → slots 快照过期（买的卡还没有 slot 记录）。带 feed_cids 的
        # refine 要按 cid 现查真实槽位，故买完必须重读一次（每 tick 至多 1 次）。
        # ★ 逃逸阀（2026-11-03）：本 tick 若**没有任何炼卡动作**（DP 只给了买卡/腾箱），
        #   延后买卡就是纯自杀 —— 不买的直接结果是下个 tick 依然没素材、依然没动作，
        #   于是永远冻在「空位只够凑齐」的边界态（云端 4821992 实测 35 分钟零动作）。
        #   边界态保格位的前提是**真有东西要投炉**；没有可投的，格位留着也是空着。
        has_refine = any(a.kind == "refine" for a in actions)
        slots_dirty = False
        bought_this_tick = 0
        sync_noted = False
        for a in actions:
            if a.kind == "collect":
                continue              # 上交已在 tick 第 3 步做过（DP 看到的是上交后状态）
            if a.kind in ("sell", "move"):
                # ★ DP 腾箱（2026-10-26 补执行）：控制器 `_box_compaction` 一直在发
                # sell/move（只卖 10 面底卡、只把多余合成卡挪保险箱，铁律已在控制器里
                # 过滤），但这里旧版写着「move/sell 当前 DP 不产出」直接 continue ——
                # 结果是换卡箱 18/18 满时永远腾不出位：买卡进不来、精炼喂料被幽灵
                # 买卡挡住，1★ 实测 800 tick 零进展。仿真/生产统一后立刻暴露。
                # 卖/挪之后卡箱 slot 会重排，必须重读状态再继续（同贪心 _make_room）。
                if a.kind == "sell":
                    r = await self.s.sell_card(
                        a.slot, a.cid, box_type=(1 if a.slottype == 0 else 2))
                    how = "卖杂卡" if "杂卡" in (a.note or "") else "卖10面底卡"
                else:
                    r = await self.s.move_card(a.cid, a.slot, from_box=0)
                    how = "挪保险箱"
                ok = bool(r.get("ok"))
                msg = r.get("message") or ""
                self._log("腾位", ok, f"{card_name(a.cid)}（DP腾箱·{how}） {msg}")
                steps.append({"action": "腾位", "ok": ok,
                              "detail": f"{card_name(a.cid)}（DP腾箱·{how}） {msg}"})
                if ok:
                    rd = await self._read_state()
                    if rd is not None:
                        st, stock, slots, pending = rd
                        slot2cid = {(sl[0], sl[1]): c
                                    for c, lst in slots.items() for sl in lst}
                continue
            if a.kind == "buy":
                tid_c = owner.get(a.cid) or self.queue[0]
                # ★ 2026-11-02 最高优先级规则：箱位只够凑齐本套时，DP 的「囤备用」
                #   预买一律延后（每张净占 1 格），下个 tick 重解时会再来；但若这张
                #   正是「凑齐还缺的卡」，则放行 —— 它本身就是凑齐的一部分。
                # ★ 2026-11-03 逃逸阀：本 tick 没有炼卡动作时不延后（见 has_refine 说明）。
                if (has_refine
                        and not self._is_collect_essential(a.cid, stock)
                        and self._collect_tight(st, stock) > 0):
                    self._log("买卡", True,
                              f"{card_name(a.cid)}（DP预买）暂缓：两箱一个空位都没有，"
                              f"先不囤卡，腾出空位再来")
                    continue
                for _ in range(max(1, a.n)):
                    if self._free_change(st) <= 0:
                        self._log("买卡", False,
                                  f"卡箱 0 空位，{card_name(a.cid)} 买不进来")
                        break
                    r = await self.s.post(EP_BUY, card_id=a.cid, theme_id=tid_c)
                    ok = r.get("code") == 0
                    if ok:
                        self.stats["bought"] += 1
                        self._mark_bought(a.cid)
                        stock[a.cid] += 1
                        slots_dirty = True      # 新卡还没有 slot 记录，refine 前要重读
                        bought_this_tick += 1
                        st["cap"]["change"]["used"] = st["cap"]["change"].get("used", 0) + 1
                    msg = r.get("message") or err_msg(r.get("code"))
                    self._log("买卡", ok, f"{card_name(a.cid)}（DP预买） {msg}")
                    steps.append({"action": "买卡", "ok": ok,
                                  "detail": f"{card_name(a.cid)}（DP预买） {msg}"})
                    if not ok:
                        break
                continue
            if a.kind != "refine":
                continue              # 其余未知动作不执行（保底）
            # ★ 2026-11-15 真机实测（10708702 首轮）：买卡 code=0 之后 mainpage
            #   仍可能读到旧快照，重读也拿不到新卡 slot → 首 tick 4 次投炉全失败。
            #   旧补丁「本 tick 买过卡就一律不投炉」把买与投拆成两个 tick，
            #   ★ 2026-11-15 二次实测证伪：11:45 预买 9 张喂料底卡 → 不投炉 →
            #   9 张落箱把 change_free 13→4 → pipe_min_free=6 提套闸关闭 →
            #   下个 tick need0={}、计划被清空 → 3 主炉 + 1 偷炉空 4 小时。
            #   用户定调「卡没集齐时箱里一个 10 面都不该买」成立的前提正是
            #   「买来的料同 tick 就被消耗，净占 0 格」，拆 tick 直接违反它。
            #   现改回：买卡后按下面 slots_dirty 重读真实槽位即时投炉；
            #   重读确实拿不到新料时**只跳过那一个 op**，记调度日志不计 errors
            #   （昨夜失败日志/errors=8 的副作用由此消除），其余 op 照常投。
            if bought_this_tick and not sync_noted:
                sync_noted = True
                self._log("DP调度", True,
                          f"本 tick 预买 {bought_this_tick} 张，"
                          f"按重读槽位即时投炉")
                steps.append({"action": "DP调度", "ok": True,
                              "detail": f"预买 {bought_this_tick} 张，即时投炉"})
            target = a.target
            tid_c = owner.get(target) or self.queue[0]
            plan = self.plans[tid_c]
            comp = plan.comp.get(target)
            if not comp:
                continue
            # 喂料槽 -> payload（与 _payload 同构；DP 的 taken 集合保证槽不重复）
            payload = {"targetid": target, "bflash": 0, "ver": 1,
                       "targettype": 1, "themeid": comp[0], "code": ""}
            used_feeders: list[tuple] = []
            ok_slots = True
            if a.feed_cids:
                # 含本 tick 刚买的料（规划期是虚拟槽）→ 先重读拿到真实槽位
                if slots_dirty:
                    rd = await self._read_state()
                    if rd is not None:
                        st, stock, slots, pending = rd
                        slot2cid = {(sl[0], sl[1]): c
                                    for c, lst in slots.items() for sl in lst}
                    slots_dirty = False
                _used: set = set()
                for i, cid_f in enumerate(a.feed_cids, 1):
                    cand = [sl for sl in slots.get(cid_f, [])
                            if (sl[0], sl[1]) not in _used]
                    if not cand:
                        ok_slots = False
                        break
                    sl = cand[0]
                    _used.add((sl[0], sl[1]))
                    payload[f"slot{i}"] = sl[0]
                    payload[f"slottype{i}"] = sl[1]
                    used_feeders.append((cid_f, sl[0], sl[1]))
            else:
                for i, (sl, stt) in enumerate(a.feeders, 1):
                    cid_f = slot2cid.get((sl, stt))
                    if cid_f is None or not slots.get(cid_f):
                        ok_slots = False
                        break
                    payload[f"slot{i}"] = sl
                    payload[f"slottype{i}"] = stt
                    used_feeders.append((cid_f, sl, stt))
            if not ok_slots:
                if sync_noted:
                    # 本 tick 刚买过卡、真机快照尚未同步：这是**等待**不是失败，
                    # 不计 errors、不写失败日志，下一 tick 按真实槽位重投。
                    self._log("DP调度", True,
                              f"{plan.cname(target)}：新买喂料未同步，下一 tick 再投")
                    steps.append({"action": "DP调度", "ok": True,
                                  "detail": f"{plan.cname(target)} 等箱位同步"})
                else:
                    self._log("DP炼卡", False,
                              f"{plan.cname(target)}：喂料槽已不在箱内（状态变了），跳过")
                continue
            steal = bool(a.steal)
            friend = None
            if steal:
                if self._steal_cooldown_until <= time.time():
                    friend = await self._pick_steal_friend(st)
                if not friend:
                    # ★★ 2026-11-14：**拿不到偷炉 ≠ 丢掉这个动作**。
                    #   进入「让主炉」分支的前提是主炉余量 > 0（主炉还在我手上），
                    #   旧版这里直接 `continue` 把动作扔了；而它要让位的那张「需主炉
                    #   的卡」通常料还没齐 → 主炉与偷炉两边同时空着（每 tick 45s 白等）。
                    if st["cap"].get("stove", {}).get("free_main", 0) > 0:
                        steal = False
                        self.steal_blocked = "偷炉不可用 → 退回主炉"
                    else:
                        self.steal_blocked = "偷炉不可用且主炉已满"
                        continue
                else:
                    payload.pop("code", None)
                    payload["opuin"] = friend
            if steal:
                r = await self.s.post(EP_STEAL, **payload)
            else:
                r = await self.s.post(EP_REFINE, **payload)
            code = r.get("code")
            ok = code == 0
            msg = r.get("message") or err_msg(code)
            tag = "DP偷炉" if steal else "DP炼卡"
            # 可观测性：DP 计划里是偷炉、执行时退回主炉的，必须在日志里看得见。
            # （`steal_blocked` 只是个属性，前端不读、也不落盘，光靠它等于没观测。）
            _fb = "（偷炉不可用→退回主炉）" if (not steal and a.steal) else ""
            self._log(tag, ok,
                      f"{plan.cname(target)}（{plan.name}）"
                      f"{('@' + friend) if (steal and friend) else ''}{_fb} {msg}")
            steps.append({"action": tag, "ok": ok,
                          "detail": f"{plan.cname(target)}（{plan.name}）{_fb} {msg}"})
            if not ok:
                if steal:
                    self._steal_fail_policy(code, friend)
                continue
            # ---- 成功：同步本地库存/炉位（与 _refine_steal 同口径）----
            self.stats["refined"] += 1
            for cid_f, _sl, stt in used_feeders:
                stock[cid_f] -= 1
                if slots.get(cid_f):
                    slots[cid_f].pop(0)
                if stt == 0:
                    st["cap"]["change"]["used"] = max(
                        0, st["cap"]["change"].get("used", 0) - 1)
            pending[target] += 1
            if steal:
                st["cap"]["stove"]["free_steal"] = max(
                    0, st["cap"]["stove"].get("free_steal", 1) - 1)
                self.last_steal = {"friend": friend, "target": target,
                                   "name": plan.cname(target), "code": code,
                                   "msg": msg, "ts": time.time()}
            else:
                st["cap"]["stove"]["free_main"] = max(
                    0, st["cap"]["stove"].get("free_main", 1) - 1)

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
            p = self.plans[tid]
            # 变卡环节接下来能产出的 40 面卡先顶在库存里 —— 否则炼卡环节会拿底卡
            # 再炼一张同款 40 面卡，变卡就白做了（详见 _face40_credit）
            credit = self._face40_credit(p, st)
            self.face40_credit[tid] = credit
            # ★ 套数：1 星按「囤得最多的那张 40 面卡」算能炼几套，只以炉位数封顶
            #   （引擎持续循环跑，剩的下一 tick 继续；见 _calc_sets 修正说明）。
            sets = self._calc_sets(p, stock, pending, free_stove)
            self._last_sets[tid] = sets
            runs, buy = p.net_plan(stock, pending, credit, sets=sets)
            plans_net[tid] = (runs, buy)
            planned |= set(runs) | set(buy) | set(credit)
        bought = 0
        # 轮转：从 _rr 开始，保证每套都有机会拿到炉位，不被第一套独吞
        order = self.queue[self._rr % len(self.queue):] + self.queue[:self._rr % len(self.queue)]
        self._rr += 1
        # **按炉次交错**展开：每套依次出一个炉次（各自深度**降序**），轮流取。
        # 不能「一套把自己的炉次全提交完再轮到下一套」——那第一套会把空炉位全吃光，
        # 多套并行就退化成单套串行了（2026-09-17 离线验证抓到）。
        #
        # ★ 深度降序（高面额优先）是 2026-09-26 用户定调的主线：
        #   「能炼更高面额的就炼更高面额的」「我已经囤了 40 面的，明明可以直接炼 80 的，
        #     这样才能更快的炼 130，你却又去炼 40 的」。炉位是稀缺资源，40 面卡
        #   （depth=1）排在 130 面卡（depth=3）前面时，**能直接开工的高面额卡会被素材
        #   卡占住炉位而排队** —— 表现出来就是「主线是 1810，结果一直在炼 40 面」。
        #   素材不够的 target 会被 _prepare_turn 挡掉（缺的是还得炼出来的成品卡 →
        #   return None），所以降序不会死循环：高面额等不到素材就让位给下一张。
        #
        # ★ 2026-10-03 用户指出「要考虑炼卡时间，150 的 4 小时、40 的 1 小时」，
        #   改成**时间感知排序**（见 `_sort_time_aware`）：纯深度降序把「还没凑齐
        #   素材的 150(4h)」排在「素材齐的 40(1h)」前面，引擎反复试投 150 都被挡、
        #   白跑；而真��占炉 4 小时的 150 应该素材一齐就**立刻**投进去（早投早占炉），
        #   腾出的炉位再用 1h 的 40 补料下一轮 —— 长短任务交织才是流水线。
        buckets = {tid: self._sort_time_aware(self.plans[tid],
                                             plans_net.get(tid, ({}, {}))[0],
                                             stock)
                   for tid in order}
        seq: list[tuple[int, int]] = []
        while any(buckets.values()):
            for tid in order:
                if buckets[tid]:
                    seq.append((tid, buckets[tid].pop(0)))
        # ★ 产线平衡用：每套「还需要产的 40 面类型」集合（phase2 让位判断）
        needed_40: dict[int, set] = {}
        for tid in order:
            runs_t = plans_net.get(tid, ({}, {}))[0]
            needed_40[tid] = {c for c in runs_t
                              if int(self.plans[tid].cards[c][3]) == CHANGE_FACE}
        taken: set = set()                    # 本 tick 已被主炉/偷炉占用的目标，防重复提交
        # ★ 两阶段调度（2026-10-18 用户定调「即时拉动 / 不囤卡」）：
        #   变卡本 tick 会供 40 面 → manu_allowed=False → 阶段二（现造 40）整体关闭，
        #   卡炉只做「箱里已有 40 面 → 合成 150」这种消耗库存的动作，把箱位腾出来；
        #   变卡不供 40 面（没勾变卡 / 礼物卡耗尽）时才现造缺的 40 面，保进度不卡死。
        #   ★ 防过量制造已由 net_plan 的 runs 保证（runs 已扣减 pending，在炉的 40 面不计入
        #     缺口），无需额外的「在途 40 即将合成」判定 —— 该判定曾用 stock+pending>=1 误判
        #     需求量>1 的 40 面「即将合成」而跳过整套现造，导致死锁（2026-10-19 仿真实证）。
        manu_allowed = not self._gift_will_supply
        # ---- 阶段一：仅从箱存「合成」上层卡（allow_buy=False，绝不现买现造） ----
        # 优先把箱里已有的 40 面等素材合成更高面额（150/80…），直接吃掉库存、释放箱位。
        for tid, target in seq:
            if free_stove <= 0:
                break
            out = await self._submit_one(tid, target, st, stock, slots, pending,
                                        planned, bought, steps,
                                        tag="合成", allow_buy=False)
            if out is None:
                continue
            st, stock, slots, pending, bought, took = out
            if took:
                free_stove -= 1
                taken.add(target)
        # ---- 阶段二：只在「变卡不供 40 面」时，才现造缺的 40 面（allow_buy=True） ----
        # 防过量制造已由 net_plan 的 runs 保证（runs 已扣减 pending，在炉的 40 面不计入缺口），
        # 无需额外的「在途 40 即将合成」判定（该判定曾误判需求量>1 的 40 面导致死锁，已删）。
        if manu_allowed:
            started_40: Counter = Counter()   # 本 tick 已开炉的 40 面类型（保持产线平衡）

            def tier_40(target):
                """40 面开炉优先级（越低越先开）：
                0 = 本会话从未生产过（蒙恬这类独门料，饿死风险最高，强制先开）；
                1 = 当前未覆盖（库存/在炉/本tick已开 全 0，需补第 1 张）；
                2 = 其余（补第 2 张及以上）。"""
                if int(self.plans[tid].cards[target][3]) != CHANGE_FACE:
                    return 9
                if self._made.get(target, 0) == 0 \
                        and plans_net.get(tid, ({}, {}))[0].get(target, 0) > 0:
                    return 0
                if stock.get(target, 0) == 0 and pending.get(target, 0) == 0 \
                        and started_40.get(target, 0) == 0:
                    return 1
                return 2

            # ★ 三档扫描，保证覆盖 + 防饿死（2026-10-19 修，根除材料饿死）：
            #   单 tick 炉位有限（4 炉 < 一套需要的 5 种 40 面），单遍扫描会在冷启动时
            #   把排序末位的「独门料」（蒙恬只喂管仲）挤掉、永不轮到 → 管仲合不出、西施卡死。
            #   先开「从未生产」(tier0) → 再开「未覆盖」(tier1) → 其余(tier2)。
            for tier in (0, 1, 2):
                for tid, target in seq:
                    if free_stove <= 0:
                        break
                    if target in taken:
                        continue
                    if tier_40(target) != tier:
                        continue
                    out = await self._submit_one(tid, target, st, stock, slots, pending,
                                                planned, bought, steps,
                                                tag="炼卡", allow_buy=True)
                    if out is None:
                        continue
                    st, stock, slots, pending, bought, took = out
                    if took:
                        free_stove -= 1
                        taken.add(target)
                        if int(self.plans[tid].cards[target][3]) == CHANGE_FACE:
                            started_40[target] += 1

        # ---------- 偷炉：用好友空炉再并行 1 张 ----------
        # 条件：开启偷炉、偷炉槽空闲、未被全局冷却、尚有未提交的炉次。
        # 注意这里**不要求主炉有空位**（主炉满时也要偷，见函数开头注释）。
        if can_steal and st["cap"].get("stove", {}).get("free_steal", 0) > 0:
            # 与主炉走**同一个** _prepare_turn（含「就地买底卡」）。
            # 旧版这里自写了一份「素材必须已经躺在箱里」的判定，而底卡平时零库存
            # （主炉路径就地为每炉补买、用完即烧）→ 候选永远被否 → 「指定了好友也从没偷过」
            # （2026-09-18 定位：live 库存下 1652 的三个候选被同一理由全部跳过）。
            stole = False
            # ★ 偷炉也走「三档」同主炉（2026-10-19 修）：从未生产(tier0) > 未覆盖(tier1) > 其余，
            #   否则裸 seq 会因 cid 兜底把排序末位的蒙恬（管仲独门料）挤出、永不轮到。
            for tier in (0, 1, 2):
                for tid, target in seq:
                    if target in taken:
                        continue                    # 主炉已占，别在好友炉上重复炼同一张
                    if int(self.plans[tid].cards[target][3]) != CHANGE_FACE:
                        continue
                    made0 = (self._made.get(target, 0) == 0
                             and plans_net.get(tid, ({}, {}))[0].get(target, 0) > 0)
                    unc = (stock.get(target, 0) == 0 and pending.get(target, 0) == 0)
                    tgt_tier = 0 if made0 else (1 if unc else 2)
                    if tgt_tier != tier:
                        continue
                    prep = await self._prepare_turn(tid, target, st, stock, slots, pending,
                                                    planned, bought, steps, tag="偷炉",
                                                    allow_buy=manu_allowed)
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
                if stole:
                    break
            if not stole and not self.steal_blocked:
                rest = [t for _tid, t in seq if t not in taken]
                self.steal_blocked = ("没有待提交的炉次（都已在炉上）" if not rest
                                      else "候选素材没备齐（底卡买不到/换卡箱没格，见日志）")

    async def _take_card(self, slot: int, opu, is_self: bool) -> dict:
        """从炉位取回一张已炼成的卡（自己炉 / 好友偷炉走不同端点）。"""
        if not is_self:
            # 好友炉上炼的卡（偷炉成功收回）：必须带好友 uin 走 steal 端点
            return await self.s.get_steal_card(slot, str(opu))
        return await self.s.get_refined_card(slot)

    @staticmethod
    def _is_box_full(r: dict) -> bool:
        """收卡/取卡被拒的原因是不是「换卡箱空位不够」。

        真机两种原文：
          · `您的卡片箱满了，不能取出炼制成功的卡片，请整理卡片箱后再试。`（0 空位）
          · `亲，可能会出现幸运事件，请空出两个卡箱的位置，再取卡！`（只剩 1 空位）
        ★ 第二种最坑：只差 1 格时提示语完全不含「满」字，旧判定漏掉 → 引擎误以为
          是别的原因（限流/风控），一直原地重试而不腾位（真机实测连续几十 tick）。
        这类失败**不是限流**，腾够 2 格后重试就能成功。
        """
        m = str((r or {}).get("message") or "")
        return ("箱满" in m) or ("满了" in m) or ("空间不够" in m) \
            or ("空出两个" in m) or ("两个卡箱" in m)

    @staticmethod
    def _take_reserve(st: dict) -> int:
        """给「已炼成待收」的炉预留换卡箱空位（真机取卡要 2 格空位）。

        ⚠️ 这 2 格是**准入门槛**，不是「每张卡占 2 格」：取一张实际只占 1 格，
        没触发幸运事件就还剩 1 格可继续取下一张（用户 2026-11-04 澄清）。
        所以这里只需在有牌待收时留 2，不要按 ×张数 放大。

        ★ 只在本 tick 真有卡等收时预留。无条件预留会把买卡流水线掐死 ——
          历史上 `reserve = soon * 2` 让 3★/4★ 直接 750h 卡死（记忆里的老坑）。
        """
        for c in st.get("stove") or []:
            if int(c.get("id") or 0) > 0 and int(c.get("flag") or 0) == 2:
                return 2
        return 0

    @staticmethod
    def _free_change(st: dict) -> int:
        """换卡箱剩余可放格数 = 已开通格 cur - 已放卡数 used（max-cur 是未开通格）。"""
        c = st["cap"].get("change", {})
        return c.get("cur", 0) - c.get("used", 0)

    def _collect_need(self, stock: Counter) -> int:
        """队列里各套「凑齐还缺多少张 distinct 卡」（箱内没有的）。"""
        n = 0
        for tid in self.queue:
            plan = self.plans.get(tid)
            if not plan:
                continue
            n += sum(1 for c in plan.ids if stock.get(c, 0) < 1)
        return n

    def _net_now(self, plan, stock: Counter, pd: Counter):
        """本套在「箱内 + 在炉」这套家底下的净需求树 `(runs, buy)`。

        按家底签名缓存：同一 tick 里 `_is_collect_essential` 会被多张卡反复问
        （炉内 7 张待收 × 6 种待买底卡），每张重跑一次 `net_plan` 纯属浪费。
        家底一变（买卡落箱 / 出炉 / 上交）签名就变，缓存自动失效重算。
        """
        sig = (tuple(sorted(stock.items())),
               tuple(sorted((int(k), int(v)) for k, v in pd.items() if v)))
        if self._net_sig != sig:
            self._net_sig, self._net_cache = sig, {}
        key = id(plan)
        if key not in self._net_cache:
            try:
                self._net_cache[key] = plan.net_plan(stock, pd, sets=1)
            except Exception:       # noqa: BLE001  算不出来就别拦（宁可照取/照买）
                self._net_cache[key] = None
        return self._net_cache[key]

    def _is_collect_essential(self, cid: int, stock: Counter,
                              pending: Counter | None = None,
                              as_take: bool = False) -> bool:
        """这张卡是不是「凑齐这一套所必需」的供给 —— 必取、必买，任何闸门都不许延后。

        判据 = **净需求树还要不要它**（2026-11-03 修正，见下）。

        `as_take` 区分两种提问语义（这是旧版踩过的坑）：
          · **买卡**（默认 False）：「箱内 + 在炉之外，还要不要再买/炼这张？」
            直接用当前家底跑需求树 —— 箱里 3 张但配方要 7 张，buy 里还剩 4 张 → 必需。
          · **取卡**（True）：「如果这张**不取出来**（少一张在途），凑齐还缺不缺它？」
            先把这张从在途扣掉再跑 —— 炉里那张 82 正是凑齐要的 1 张，扣掉后需求树
            又要它 → 必需（必取）；而箱里已有 1 张 82 时，炉里多余的第二张扣掉后
            需求树不再要 → 非必需（可留炉里等上交）。

        旧口径（distinct：箱里没有这张卡才算缺）在两个真机场景都会误判：

        · **箱里有几张但配方要更多**（2026-11-03 云端 4821992 / tid=321「珍稀动物」）：
          整套 47 张底卡，`小头鼠海豚` 一个 id 就要 7 张（1 张进集卡册 + 6 张当三个
          40 面中间卡的共用素材）。箱里已有 3 张 → distinct 判「不缺」→ 每次 DP 预买
          都被最高优先级规则延后 → 底卡进不来 → 素材不齐、偷炉没料、主炉出锅无卡可投，
          引擎连续 35 分钟零动作（日志只有 153 条「DP预买暂缓」）。
        · **「一路向上合成」的前置**（用户 2026-11-02 指出的反例）：凑齐要 79/80/81
          各 2 张（1 进册 + 1 当炼 82 的素材），箱里各 1 张时第二张 81 不是 distinct
          缺口，却正是炼 82 的必需品；不取就没法一路向上合成，整套永远停住。

        所以改成问「把箱内 + 在炉的家底全算进去之后，`net_plan` 的 runs/buy 里还要
        不要这张卡」—— 需求树会把每个中间卡要消耗的素材**张数**一路传导到底卡，
        上述两种情形都会命中；而「箱里已够本套用」的囤货、跨套遗留卡不在需求树里，
        照旧可以被延后（这正是规则该管的部分）。
        """
        base_pd = self._pending if pending is None else pending
        pd = Counter(base_pd or Counter()) if as_take else (
            base_pd if isinstance(base_pd, Counter) else Counter(base_pd or {}))
        if as_take and pd.get(cid, 0) > 0:
            pd[cid] -= 1        # 假设这一张不到货（还在炉里），看需求树是否还缺它
        for tid in self.queue:
            plan = self.plans.get(tid)
            if not plan or cid not in plan.ids:
                continue        # 不是本套的卡 → 帮不上凑齐
            net = self._net_now(plan, stock, pd)
            if net is None:
                return True     # 需求树算不出来就别拦（宁可照取/照买）
            runs, buy = net
            if runs.get(cid, 0) > 0 or buy.get(cid, 0) > 0:
                return True     # 需求树还要它（含「箱里有几张但不够」与合成素材）
        return False

    def _refresh_caps(self, st: dict) -> None:
        """把本 tick 的箱容量快照记到实例上（tick 开头与 DP 决策前重读都要调）。

        · `_box_cur/_box_used` = 换卡箱已开通格 / 已占格
        · `_store_free` = 保险箱空位 —— 炼成的卡**优先落保险箱**，满了才溢到换卡箱
          （用户 2026-11-05 定调），所以它是「换卡箱能不能腾出格」的承接位。
        """
        cap = st.get("cap") or {}
        ch = cap.get("change") or {}
        stc = cap.get("store") or {}
        self._box_cur = int(ch.get("cur") or 0)
        self._box_used = len(st.get("change") or [])
        self._store_free = max(0, int(stc.get("cur", 0)) - int(stc.get("used", 0)))

    def _free2(self, st: dict) -> int:
        """两箱合计空位。保险箱看 `cur`（已开通格），`max` 是没开通的上限。"""
        store = (st.get("cap") or {}).get("store") or {}
        store_free = max(0, int(store.get("cur", 0)) - int(store.get("used", 0)))
        return self._free_change(st) + store_free

    def _parkable_count(self, st: dict) -> int:
        """保险箱里「可以寄存到简易箱」的卡张数（不在在炼套、不是集齐还要的）。

        这些卡挪进简易箱不影响任何在炼/收尾的套 —— 只有这类卡才允许寄存。
        """
        stock = self._stock_from(st)
        keep: set = set()
        for t in self.queue:
            p = self.plans.get(t)
            if p:
                keep |= set(p.ids)
        n = 0
        for c in (st.get("store") or []):
            cid = int(c.get("id") or 0)
            if cid <= 0 or cid in keep or self._recently_bought(cid):
                continue
            if self._is_collect_essential(cid, stock, self._pending):
                continue
            n += 1
        return n

    async def _refresh_simple(self, st: dict) -> None:
        """本 tick 最多能把多少张保险箱卡寄存到简易箱（`_simple_free`）。

        ★ 2026-11-05（B3 场景）：保险箱满、换卡箱也满时，唯一还能腾格的动作就是
          「保险箱卡 → 简易箱」（真机只支持这两跳：换卡箱↔保险箱、保险箱↔简易箱）。
          这一步的收益必须算进「物理装不下」的判定，否则闸门会在还有救的时候先停摆。

        只在**两箱已经很紧**时才多打一次接口（正常滚动时 `_simple_free` 恒 0）。
        读不到就退化成旧行为（当没有可寄存空间），绝不因为简易箱接口失败挡住主流程。
        """
        self._simple_free = 0
        fn = getattr(self.s, "get_simple_box", None)
        if fn is None:
            return
        if self._free2(st) >= max(1, self._collect_need(self._stock_from(st))):
            return
        try:
            sb = await fn()
        except Exception:       # noqa: BLE001  简易箱读不到就当没有
            return
        if not sb.get("ok"):
            return
        free = int(sb.get("cur") or 0) - int(sb.get("filled") or 0)
        self._simple_free = max(0, min(free, self._parkable_count(st)))

    def _collect_tight(self, st: dict, stock: Counter) -> int:
        """★ 最高优先级规则（用户 2026-11-02 定调）

        「当任何操作会卡住集齐一套卡时，都要延后。」

        返回**必须给凑齐预留的格数**（= 凑齐还缺几张 distinct 卡）。返回值 > 0
        表示此刻处于「真·卡死」态，必须延后非凑齐必需的操作。

        ★ 判据（2026-11-04 用户定调修正）：**判死的唯一标准是两箱空位 == 0**。
          用户原话：「什么时候真的卡住，就是卡箱一个空位都没了，但凡有一个空位，
          都要把在炼的卡继续取卡投炉。」

          旧判据是 `空位 <= 缺口`，在「缺口远大于空位」（如还差 8 格、空 4 格）
          时也恒成立 —— 而那种状态下空位根本涨不上去（没集齐不买底卡、也上交
          不了），于是每个 tick 都把出炉的卡延后，那张卡永久占住炉位。真机实测
          4821992 连续 78 tick（58 分钟）只打「暂缓取出」，引擎零动作。

        ⚠️ 11-04 版还基于一个假设：「没集齐时一个 10 面底卡都不买，所以空位不会
          被底卡需求占掉」。**该假设已不成立** —— 收尾「补集卡册」这条路径会
          买底卡，且买完就上交，正是最该先做的事（见 `_collect_tight_ex`）。

        空位 ≥ 1（或已凑齐、队列为空）时一律返回 0 —— 正常情况绝不做延后。
        """
        return self._collect_tight_ex(st, stock)[0]

    def _plan_base_only(self, plan, stock: Counter) -> bool:
        """这一套是否「合成卡全在、只差从商店买的底卡」—— 收尾集齐态。

        判据与收尾补集卡册买底卡、`_calc_sets` 缺口闸**同一口径**：
        `miss_comp 为空 且 miss_base 非空`。改一处必须同步另两处。

        ★ 2026-11-05：三种语义必须分开，否则要么空转要么死锁：
          · 收尾集齐态（本函数为真）→ 缺口张数 = 必须同时占住的格数，可以按箱位闸；
          · 缺口含合成卡 → 靠投炉产出，缺口张数 ≠ 所需空位数，**绝不能按缺口闸**
            （不投炉 → 缺口不减 → 空位涨不上来，11-04 死锁）。
        """
        if any(c in plan.comp and stock.get(c, 0) < 1 for c in plan.ids):
            return False
        return any(stock.get(c, 0) < 1 for c in plan.ids)

    def _base_only_short(self, stock: Counter) -> bool:
        """队列里是否有一套「只差能从商店买的底卡」（合成卡全在箱）。

        与收尾补集卡册买底卡的判据**完全一致**（`miss_comp 为空 且 miss_base
        非空`），这样两处不会各说各话。
        """
        for tid in self.queue:
            plan = self.plans.get(tid)
            if not plan:
                continue
            if self._plan_base_only(plan, stock):
                return True
        return False

    def _mark_bought(self, cid: int) -> None:
        """记下刚买进的卡：腾位时不许把它当杂卡卖掉。

        ★ 2026-11-05 真机 4821992：买一张底卡 → 下一轮腾位把它当「多余 10 面
          底卡」卖掉 → 再买下一张 → 再卖…… `stats.bought` 累积到 201 次、
          集卡册进度零。给买进来的卡 5 分钟活口（投炉或同 tick 上交都够了）。
        """
        self._recent_buys[int(cid)] = time.time()

    def _recently_bought(self, cid: int, window: float = 300.0) -> bool:
        ts = self._recent_buys.get(int(cid))
        return bool(ts) and (time.time() - ts) < window

    def _defer_escape(self) -> bool:
        """逃逸阀：连续多 tick 因「先集齐」延后，却一张卡都没买成 → 放行取卡。
        买路本身不通（金币不够 / 卡已下架 / 箱里杂卡把位占死）时，继续延后只是
        拿炉位空耗，必须放行。阈值按「每 tick 该函数被调 N 次」放大到 60
        （≈4~6 tick，3~5 分钟）；只要买进过一张卡就清零重来。
        """
        bought = int(self.stats.get("bought", 0))
        if bought > getattr(self, "_defer_bought", 0):
            self._defer_ticks = 0
        self._defer_bought = bought
        self._defer_ticks = getattr(self, "_defer_ticks", 0) + 1
        if self._defer_ticks >= 60:
            self._defer_ticks = 0
            return True
        return False

    def _collect_tight_ex(self, st: dict, stock: Counter) -> tuple[int, str]:
        """`_collect_tight` 的完整版：返回 (要预留的格数, 原因文案)。

        ★ 2026-11-05 用户定调（补回 11-02 的前提，但不再掉回 11-04 的死锁）：
          「但凡有一个空位都要取卡投炉」**有一个前提** —— 卡箱里的卡已经能凑成
          一套、空位也够时，**先集齐上交，再取卡**。取卡会吃掉买底卡的格位，
          而集齐（买底卡 → 同一 tick 上交）一次性释放十几格；顺序反了就会
          「取了卡反而买不进底卡」，然后买一张卖一张空转。

          判据分三层（优先级从高到低）：
            ① 两箱空位 == 0 → 无条件延后（无路可走）。
            ② 缺口**全部是可买底卡**、且空位 ≤ 缺口 → 延后（先集齐上交）。
               这种状态买完即上交，有明确出路，延后是值得的。
            ③ 其余（缺口含必须投炉的合成卡）→ 不延后。
               ⚠️ 这里正是 11-04 那次 58 分钟零动作的真因：延后取卡=不投炉
               =缺口永远不减=空位永远涨不上去。判据必须能区分「缺口能不能
               靠买补齐」，不能只看 `空位 <= 缺口`。
        """
        need = self._collect_need(stock)
        free2 = self._free2(st)
        if need <= 0:
            # ★★ 2026-11-05 紧箱仿真（C1/C3）：**已集齐**时也有一道闸。
            #   已集齐 = 整套 17 张各 1 张都在箱里，此刻唯一该做的是上交。
            #   若两箱已满（free2 == 0），取任何一张出炉卡都得先卖底卡腾位 ——
            #   卖掉的那张很可能正是集齐要用的，缺口 +1，然后永远凑不回来
            #   （真机日志「腾位 大显身手 / 消防少年」+「还缺 2 张，两箱只有 0 格」）。
            #   等上交释放 17 格再取，代价只有一个 tick；卖卡重建是永久损伤。
            if free2 <= 0:
                return 1, "本套已集齐、两箱无空位，先上交再取卡"
            return 0, ""
        if free2 <= 0:
            return need, "两箱一个空位都没有"
        if free2 <= need and self._base_only_short(stock):
            if self._defer_escape():
                return 0, ""
            return need, (f"只差 {need} 张可买底卡、空位只剩 {free2} 格，"
                          f"先集齐上交再取卡")
        return 0, ""

    def _should_defer_take(self, cid: int, st: dict, stock: Counter,
                           pending: Counter | None = None) -> bool:
        """箱位紧时是否该把这张**已炼成**的卡先留在炉里、暂不取出。

        ★ 2026-11-02 用户指正（真机经典女套装 tid=54）：两箱空位 7，凑齐还差 7 格
          （1 张 150「整套搭配」+ 6 张 10 面底卡），**零余量**。而炉里还躺着一张
          多余的 80 面卡和第二张 82 —— 它们对凑齐零贡献，取出来各占 1 格，最后
          买底卡就差一格，整套永远凑不齐。用户原话：「如果你取一个40卡出来,
          卡箱可能就不够了, 你能看出这种情况, 把40卡放着不取, 等上交后再取吗」。
          这类卡的正确处理是**留炉里**，等本套上交（整箱格位一次性释放）后再取。

        ⚠️ 但「看似占位、实为腾箱前置」的卡绝不能延后 —— 判据 ① 已按
          `_is_collect_essential` 的净需求树口径覆盖（既认 distinct 缺口，也认
          「一路向上合成」要消耗的素材张数）。用户 2026-11-02 补充的正是这一点：
          「炼好了缺的那个最低层卡，其实可以一路向上合成，最终达到腾箱，但取
           炼好的卡却实际会占位更多，如果这个不取，就没法一路向上合成。」

        判据（三道闸，缺一不可，任何一道不满足就照常取）：
          ① 这张卡是「凑齐所必需的供给」（含向上合成的素材）→ 必取，不延后；
          ② 炉里「多余待收卡」还没堆到 4 张 → 才允许继续延后，避免多余卡把炉位长期堆死；
          ③ 箱位确实不够让「集齐」和「取卡」同时进行 → 才延后（详见 `_collect_tight_ex`
             的三层优先级）。⚠️ 11-04 曾把 ③ 收紧成「空位 == 0」，等于把「先集齐
             上交再取卡」这条前提删掉了 —— 结果取卡吃光买底卡的格位。
        """
        # ★★ 最高优先级（2026-11-05 紧箱仿真 C1/C3）：**已集齐 + 两箱满** → 一律延后。
        #   这一条必须排在「必需卡必取」之前：集齐时整套各 1 张都在箱里，上交就在
        #   下一行代码（tick 第 3 步 / 收尾买齐后同一 tick），等一个 tick 的代价
        #   远小于「卖一张底卡腾位 → 缺口 +1 → 永远凑不回来」。
        #   真机日志形态：「腾位 大显身手（面10底卡）卖掉腾位」紧随
        #   「还缺 2 张，两箱只有 0 格（换卡箱 0 / 保险箱 0）」。
        if self._collect_need(stock) <= 0 and self._free2(st) <= 0:
            self._defer_reason = "本套已集齐、两箱无空位，先上交再取卡"
            return True
        if self._is_collect_essential(cid, stock, pending, as_take=True):
            return False                          # ① 必需卡，照常取
        # ② 炉里多余待收卡积压到 4 张就照常取（别把炉位长期占死）
        n_extra = 0
        for c2 in st.get("stove") or []:
            c2id = int(c2.get("id") or 0)
            if c2id <= 0 or int(c2.get("flag") or 0) != 2:
                continue
            if self._is_collect_essential(c2id, stock, pending, as_take=True):
                continue                          # 它自己是必需卡，不算积压
            n_extra += 1
        if n_extra >= 4:
            return False
        tight, why = self._collect_tight_ex(st, stock)   # ③ 边界态才延后
        self._defer_reason = why
        return tight > 0

    # ---------- 腾位（换卡箱满了买不进底卡时）★ 2026-10-03 用户定调 ----------
    async def _park_one_to_simple(self, snap: dict, steps: list[dict]) -> bool:
        """把一张**用不上的**保险箱卡寄存到简易箱，腾出 1 个保险箱格。

        ★ 2026-11-05 用户定调（B3 场景）：
          · 只做「保险箱 → 简易箱」这一跳，**不做自动搬回** —— 保险箱本来就放不下，
            搬回去等于白挪；简易箱快到期他会自己用道具续期，没道具再另想办法。
          · 因此候选必须严格筛：不是在炼套的卡、不是需求树还要的卡；
            再按**面值从低到高**挑（真忘了续期，损失最小）。
          · 高面值藏品不动 —— 用户 2026-10-21 定调「高面值卡永不自动处理」的延伸。

        返回是否真的腾出了 1 格。`snap` = parse_full 结果（含 store 列表与 slot）。
        """
        cards = snap.get("store") or []
        if not cards:
            return False
        stock = self._stock_from(snap)
        keep: set = set()
        for t in self.queue:
            p = self.plans.get(t)
            if p:
                keep |= set(p.ids)
        _all_cards = _maps()[0]

        def _face(cid: int) -> int:
            c = _all_cards.get(cid)
            return int(c[3] or 0) if c else 0

        cands = []
        for c in cards:
            cid = int(c.get("id") or 0)
            slot = int(c.get("slot") or -1)
            if cid <= 0 or slot < 0 or cid in keep:
                continue
            if self._is_collect_essential(cid, stock, self._pending):
                continue
            cands.append((_face(cid), cid, slot))
        if not cands:
            self._log_throttled("park_none", "腾位", False,
                                "保险箱里没有可寄存的卡（全是在炼套/集齐要用的）—— 需清卡箱")
            return False
        cands.sort()
        face, cid, slot = cands[0]
        nm = card_name(cid)
        fn = getattr(self.s, "to_simple_box", None)
        if fn is None:
            return False
        r = await fn(cid, slot)
        if r.get("ok"):
            self._simple_free = max(0, int(getattr(self, "_simple_free", 0) or 0) - 1)
            self._log("腾位", True,
                      f"{nm}（面{face}）寄存到简易箱腾 1 格"
                      f"（不自动搬回；简易箱到期前请续期）")
            steps.append({"action": "腾位", "ok": True,
                          "detail": f"{nm}（面{face}）寄存简易箱"})
            return True
        self._log_throttled(f"parkfail:{cid}", "腾位", False,
                            f"{nm}（面{face}）寄存简易箱失败：{(r.get('message') or '')[:60]}")
        return False

    async def _make_room(self, tid: int, need: int, st: dict, steps: list[dict],
                         running_themes: set | None = None) -> int:
        """换卡箱腾出至少 `need` 格，返回腾出的格数（不够/失败返回实际腾出数）。

        真机死锁（2026-10-03 复现）：换卡箱 18/18 满，40 面底卡买不进来 → 流水线全停；
        唯一腾格的是占炉 4h 的 150（期间不烧素材），于是「暂缓」连转 4 小时不自愈。

        腾位策略（用户定调「优先移保险箱」）：
          · **优先把换卡箱里「与在炼套卡无关的高面值卡」移到保险箱**（保险箱 200 格，
            无损，不卖）—— 换卡箱塞着鬼门关1337/酆都城1333 这类大面额杂卡，炼卡根本
            用不上，白占换卡箱（硬顶 18）；
          · 10 面值底卡**不动**（它们是炼卡素材，要买/留着当料）；
          · 在炼套卡自己的卡不动（要当素材/收藏）。

        ★ **高面值杂卡永不自动卖**（用户 2026-10-21 定调，修正越权行为）：
          旧实现在「保险箱也满」时会**自动卖掉高面值杂卡**当保底 —— 这超出了用户
          2026-10-03 的授权（那次只说「未下架的 10 面值卡都买掉，高面值卡移保险箱，
          优先移保险箱」，**没授权卖高面值卡**）。而且 `sell_card` 实证是
          **「卖卡获得金币 = 卡面值」**（`mc_api.sell_card` 注释）→ 卖掉一张 588 面额
          的卡只换回 588 金币，等于把高价值收藏卡折成零头金币，**用户明确反对**。
          现在：高面值杂卡移不进保险箱就**跳过不卖**，只继续尝试下一张；
          腾位只能靠 10 面值底卡（用户授权过的那条）。腾不出来就如实报
          「保险箱已满 + 高面值卡不自动卖」，让用户自己决定。

        `running_themes` = 在炼套卡 id 集合，用于保护它们相关的卡不被误移/误卖。
        """
        keep_themes = running_themes if running_themes is not None else set(self.queue)
        keep_ids: set = set()
        for t in keep_themes:
            if t in self.plans:
                keep_ids |= set(self.plans[t].ids)

        async def snapshot():
            mp = await self.s.post(EP_MAINPAGE, code="", opuin=self.s.uin)
            if mp.get("code") != 0:
                return None
            return parse_full(mp.get("raw", ""))

        snap = await snapshot()
        if snap is None:
            return 0
        freed = 0
        # 候选：换卡箱里「不在任何在炼套卡内」的卡，按面额从高到低腾（大面额最没用）。
        # ⚠ parse_full 的卡 dict **没有 face 字段**（只有 slot/id/type/st），面额要从卡库查。
        _all_cards = _maps()[0]

        def _face_of(cid: int) -> int:
            c = _all_cards.get(cid)
            return int(c[3] or 0) if c else 0

        _stock_now = self._stock_from(st)

        def _sell_would_reopen_gap(cid: int) -> bool:
            """卖掉这张之后，需求树是不是又要它了（= 它本来就是集齐缺的/还差的张数）。"""
            stock2 = Counter(_stock_now)
            if stock2.get(cid, 0) <= 0:
                return False
            stock2[cid] -= 1
            if stock2[cid] <= 0:
                del stock2[cid]
            return self._is_collect_essential(cid, stock2, self._pending)

        def candidates(box_key):
            # ★ 2026-10-26 死锁修复（用户报「买 10 面把卡箱占满、取不出 40 面」）：
            #   旧版 `if cid in keep_ids: continue` 把**套内 10 面底卡也保护了**，
            #   而真机满箱形态恰恰是「9 张藏品 + 9 张套内卡」，藏品移不进保险箱
            #   （已开通 168 格全满）→ 候选集为空 → _make_room 恒返回 0 →
            #   收卡永远「卡片箱满了」→ 炉位不释放 → 箱里的 10 面也烧不掉 → 永久死锁。
            #   事实依据：10 面卡**卖 = 面值金币、买回同价（无损）**，且用户 2026-10-03
            #   已定调「10 面值卡用多少现场买多少，留着纯占格位，卖掉既腾格又回金币」。
            #   故：套内 10 面卡可以卖，只是**优先卖同 cid 的重复张**（>1），
            #   单张的垫到最后 —— 3 合 1 配方要的是 3 张**不同**卡，同 cid 留 1 张就够。
            have: Counter = Counter()
            for c in snap.get(box_key) or []:
                cid = int(c.get("id") or 0)
                if cid > 0:
                    have[cid] += 1
            out = []
            for c in snap.get(box_key) or []:
                cid = int(c.get("id") or 0)
                if cid <= 0:
                    continue
                face = _face_of(cid)
                if self._recently_bought(cid):
                    # ★ 2026-11-05：刚买进来的卡不许当杂卡卖掉 —— 否则就是
                    #   「买一张 → 腾位卖掉刚买那张 → 再买下一张」的空转，
                    #   集卡册永远补不齐（真机 bought 累计 201 次、进度零）。
                    continue
                rank = 0
                if cid in keep_ids:
                    # ★ 套内卡：只有 **10 面底卡**可腾（40 面及以上是一炉炼出来的成品，
                    #   卖掉就白烧 3 张底卡 + 1 小时炉时）。旧版用 `face > CHANGE_FACE`
                    #   （40）判，40 面成品正好落在「可卖」一侧 → 会误卖在炼配方要用的
                    #   40 面卡，是隐患，这里收紧到 > 10。
                    if face > 10:
                        continue
                    # ★★ 2026-11-05 紧箱仿真 D2：**缺着的底卡一张都不许卖**。
                    #   D2（两箱全满、炉空、缺口含合成卡）原先会卖「消防少年/大显身手」
                    #   去换 2 格买 3 合 1 的素材 —— 结果素材只买进 2 张，第 3 格没有了，
                    #   投炉失败：卖掉 2 张还缺的卡 + 箱里多 2 张用不上的素材，
                    #   缺口从 6 涨到 8，之后永久零动作（真机同形态，收敛不了）。
                    #   判据不是「有没有缺口」（缺口含合成卡时仍要放行投炉），而是
                    #   **卖掉这一张之后需求树还缺不缺它**：还有富余张（重复且超出需求）
                    #   才能卖；卖完就缺 → 停手报「需清卡箱」。
                    if _sell_would_reopen_gap(cid):
                        continue
                    rank = 1 if have[cid] > 1 else 2   # 重复底卡先卖，单张垫后
                out.append((cid, face, int(c.get("slot") or 0), rank))
            # 排序：① 高面值杂卡最先（移保险箱，无损、且大面额本就用不上，用户定调
            #   「优先移保险箱」）；② 套内重复的 10 面底卡；③ 套内单张 10 面底卡。
            out.sort(key=lambda x: (x[3], -x[1]))
            return out

        cands = candidates("change")
        n_cand0 = len(cands)
        if not cands:
            self._log("腾位", False,
                      "换卡箱满且无卡可腾：高面值杂卡移不进保险箱（保险箱已开通格全满）、"
                      "箱内也没有多余的 10 面底卡")
            return 0
        # ★★ 2026-11-05：缺的卡**全是可买底卡**、且两箱空位装不下时，禁止靠卖底卡腾位。
        #   这种状态下卖掉的底卡正是集齐要用的（缺口清单本身），卖掉只会让缺口更大，
        #   买回来又满 → 「卖一张买一张」无限空转（真机 4821992 实测）。
        #   只拦「卖底卡」这一支：高面值杂卡移保险箱仍然放行，缺口含合成卡时也照常
        #   腾位取卡（那种场景卖底卡能推进合成，不能一起拦，否则复现 11-04 死锁）。
        no_sell = (self._base_only_short(_stock_now)
                   and self._collect_need(_stock_now) > self._free2(st))
        # ★ 用 while + 每次重取候选：卖卡/移卡后**其他卡的 slot 会重排**，
        #   继续用旧 slot 会 -311（卡牌有变化）；且失败的那张要跳过、试下一张，
        #   不能每次都重扫 snapshot（保险箱满时会空转一串网络请求）。
        freed = 0
        parked = 0                  # 本 tick 已寄存到简易箱的张数（上限 = need）
        tried: set = set()          # 已试过且失败的 (cid, slot)，避免重复试同一张
        guard = 0
        while freed < need and guard < 60:
            guard += 1
            cands = [c for c in candidates("change") if (c[0], c[2]) not in tried]
            if not cands:
                break
            cid, face, slot, _rank = cands[0]
            tried.add((cid, slot))
            nm = card_name(cid)
            done = False
            if face <= 10:
                if no_sell:
                    self._log_throttled(
                        "no_sell_base", "腾位", False,
                        f"{nm}（面{face}底卡）不卖：缺的卡全是可买底卡、两箱只"
                        f"{self._free2(st)} 格，卖了会让缺口更大 —— 需清卡箱才能继续")
                    break
                # ★ 10 面值底卡：用户定调「未下架的 10 面值卡都买掉」——它们是炼卡
                #   素材、用多少现场买多少，留着纯占格位，卖掉既腾格又回金币。
                #   ★ 2026-10-26 用户重申：「不能用 10 面的卡占位，要卖掉再取卡向上合」。
                #   卖价=面值、买回同价 → 无损；40 面以上是炼出来的成品，绝不卖。
                sr = await self.s.sell_card(slot, cid, box_type=1)
                if sr.get("ok"):
                    self._log("腾位", True, f"{nm}（面{face}底卡）卖掉腾位（炼卡素材，用多少买多少）")
                    steps.append({"action": "腾位", "ok": True,
                                  "detail": f"{nm}（面{face}底卡）卖掉腾位"})
                    done = True
                else:
                    self._log("腾位", False, f"{nm} 卖失败：{(sr.get('message') or '')[:80]}")
            else:
                # 高面值杂卡：优先移保险箱（无损，用户定调「优先移保险箱」）
                r = await self.s.move_card(cid, slot, from_box=0)
                if r.get("ok"):
                    self._log("腾位", True, f"{nm}（面{face}）移入保险箱（换卡箱太满，炼卡用不上）")
                    steps.append({"action": "腾位", "ok": True,
                                  "detail": f"{nm}（面{face}）移入保险箱"})
                    done = True
                else:
                    # ★ 2026-10-21 用户定调：高面值杂卡**不自动卖**（旧实现会卖掉当保底，
                    #   属越权；且 sell_card 只换回面值等额金币，高价值卡被折成零头）。
                    # ★★ 2026-11-05（B3 场景）：保险箱满时先试**寄存到简易箱** —— 挪一张
                    #   用不上的保险箱卡去简易箱，保险箱立刻多 1 格，这张杂卡就能落位、
                    #   换卡箱腾出 1 格。不做自动搬回（用户定调：保险箱本来就放不下）。
                    #   每轮最多寄存 `need` 张，避免把简易箱一次吃光。
                    if parked < need and await self._park_one_to_simple(snap, steps):
                        parked += 1
                        snap = await snapshot()
                        if snap is None:
                            self._log("腾位", False, "寄存后重读卡箱失败，中止本轮腾位")
                            break
                        tried.clear()      # 保险箱 slot 变了，候选要重取
                        continue
                    #   保险箱满且简易箱也没法寄存 → 跳过这张，继续试下一张候选
                    #   （可能还有底卡可卖）。
                    self._log_throttled(
                        f"roomstore:{cid}", "腾位", False,
                        f"{nm}（面{face}）移入保险箱失败（保险箱已满、简易箱也无处寄存）——"
                        f"高面值卡不自动卖，跳过")
            if done:
                freed += 1
                tried.clear()      # 状态变了，重新看全部候选
                snap = await snapshot()      # 只有真腾出格位才需要重扫（slot 会重排）
                if snap is None:
                    self._log("腾位", False, "重读卡箱失败（mainpage code≠0），中止本轮腾位")
                    break
            # ★ 失败不重读：卖/移失败根本没改状态，旧版这里每失败一张就重扫一次
            #   —— 真机连打 9 次 mainpage 直接被限流 → snap=None → 循环提前 break，
            #   结果「9 张藏品移不进保险箱」之后**再也轮不到卖 10 面卡**（实测只尝试 8 次）。
            #   直接试下一张即可。
        if freed < need:
            # 腾不出位：如实说明原因（保险箱满 + 高面值卡不自动卖 → 只能靠清保险箱）
            self._log_throttled(
                "room_short", "腾位不足", False,
                f"还需 {need - freed} 格｜候选 {n_cand0} 张 / 尝试 {guard} 次 / "
                f"腾出 {freed} 格。保险箱已满、高面值卡不自动卖；箱里的 10 面底卡"
                f"全是集齐还缺的（卖掉缺口只会更大）—— 需清卡箱才能继续")
        return freed

    async def _prepare_turn(self, tid: int, target: int, st, stock, slots, pending,
                            planned: set, bought: int, steps: list[dict],
                            tag: str = "炼卡", allow_buy: bool = True):
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
        target_is_40 = int(plan.cards[target][3]) == CHANGE_FACE
        if not allow_buy and need_buy > 0 and target_is_40:
            # 合成阶段(allow_buy=False)只禁止「现买现造 40 面卡本身」——
            #   depth≥3 主题的上层卡会**直接吃箱外底卡**（如 4699=4693+4690+4691），
            #   缺的底卡就地买属于「即时拉动合成上层卡」、并非现造 40 面，必须放行，
            #   否则上层卡永缺底卡 → 整套餐卡死锁（金陵十二钗 3 星实锤 2026-10-20）。
            #   40 面卡本身仍由变卡/阶段二负责，这里素材不齐（含 40 面成分）就等。
            self._log("合成跳过", True,
                      f"{tag}·{plan.cname(target)}（{plan.name}）：40面卡素材不足且不允许现造，"
                      f"等变卡/阶段二")
            return None
        if need_buy:
            free_box = self._free_change(st)
            # ★ 2026-11-02 最高优先级规则（「任何会卡住集齐的操作都要延后」）
            #   不适用本处：买素材**同一 tick 立刻投炉**（下面 _submit_one 直接
            #   _payload + EP_REFINE），净增占用 = 0，只要投炉成功就不会挤掉凑齐
            #   所需的格位；素材本身要不要留由 _spare/keep 保证（套内卡留 1 张）。
            #   真需要拦的是「买了囤着不用」的动作 —— 收益见 DP 预买与收卡两处闸门。
            # ★ 2026-10-26：给「已炼成待收」的炉预留 2 格取卡空位（真机取卡要 2 格）。
            #   不留 → 买卡把换卡箱塞满 → 收卡永远被拒 → 炉位不释放 →
            #   箱里的 10 面烧不掉 → 箱永远满（真机死锁，用户当场报）。
            free_box = max(0, free_box - self._take_reserve(st))
            # ★ 2026-10-03 死锁修复：换卡箱满 → 先腾位（优先移保险箱）再买。
            #   真机复现：18/18 满时底卡买不进来，而唯一腾格的是占炉 4h 的 150，
            #   于是「暂缓」连转 4 小时不自愈（150 不烧素材就不腾格）。
            if need_buy > free_box:
                freed = await self._make_room(tid, need_buy - free_box, st, steps)
                if freed > 0:
                    rd = await self._read_state()   # 腾位后重读，空位/槽位都变了
                    if rd is None:
                        return None
                    st, stock, slots, pending = rd
                    free_box = self._free_change(st)
                    free_box = max(0, free_box - self._take_reserve(st))
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
            bad = [plan.cname(m) for m, n in need_cnt.items()
                   if stock.get(m, 0) < n or not self._spare(m, stock, pending, n, planned)]
            return None
        return st, stock, slots, pending, need_cnt, need_buy

    async def _submit_one(self, tid: int, target: int, st, stock, slots, pending,
                          planned: set, bought: int, steps: list[dict],
                          tag: str, allow_buy: bool) -> tuple | None:
        """备料 + 投炉 + 同步世界状态，返回 (st, stock, slots, pending, bought, took)。

        抽出主炉/偷炉共用的「投一炉」动作，_refine_all 的两阶段调度都调它。
        `allow_buy=False` 时只接受「素材全在箱里」的炉次（合成，不现买）。
        """
        prep = await self._prepare_turn(tid, target, st, stock, slots, pending,
                                        planned, bought, steps, tag=tag,
                                        allow_buy=allow_buy)
        if prep is None:
            return None
        st, stock, slots, pending, need_cnt, nb = prep
        bought += nb
        plan = self.plans[tid]
        res = self._payload(plan, target, need_cnt, slots)
        if res is None:
            return (st, stock, slots, pending, bought, False)
        payload, picked = res
        r = await self.s.post(EP_REFINE, **payload)
        ok = r.get("code") == 0
        nm = plan.cname(target)
        took = False
        if ok:
            self.stats["refined"] += 1
            for m, n in need_cnt.items():
                stock[m] -= n
                for _ in range(n):
                    if slots.get(m):
                        slots[m].pop(0)
            # 素材里来自换卡箱的那几张已经烧掉 → 同步占用数，否则本 tick 后面几炉
            # 会误判「换卡箱 0 格」而白白空转一轮。
            used = st["cap"].get("change", {}).get("used", 0)
            st["cap"]["change"]["used"] = max(
                0, used - sum(1 for _sl, _st in picked if _st == 0))
            pending[target] += 1
            took = True
            self._made[target] += 1        # 累计开炉生产次数（40 面防饿死用）
        self._log(tag, ok, f"{nm}（{plan.name}） {r.get('message') or err_msg(r.get('code'))}")
        steps.append({"action": tag, "ok": ok,
                      "detail": f"{nm}（{plan.name}） {r.get('message') or err_msg(r.get('code'))}"})
        return (st, stock, slots, pending, bought, took)

    def _change_specs(self, stock: Counter, pending: Counter) -> list[tuple[int, int, int]]:
        """变卡分布目标（纯计算）：把各套「本该从底卡炼出来的 40 面卡」列出来，按需求量
        降序 → 交给礼物卡变卡环节**分散**变出，直接替代炼卡环节「买 10 面卡 + 炼 40 面」
        这一步，让卡炉空出来炼更高面额（用户 2026-10-18 定调「轮转不是好算法」）。

        语义：`net_plan(credit=空)` 算出的 40 面卡 runs，正是「若不靠变卡、需从底卡新炼」
        的 40 面张数；把它们变齐，net_plan（带库存）就不会再排这些 40 面的炼次，卡炉
        转而炼 150 等高面额。这比「轮转只换单张目标」直接：多头缺口一次按量填满，且
        不同种类都覆盖到，不会把礼物卡全堆成同一种、其余仍从底卡炼。
        """
        specs: list[tuple[int, int, int]] = []
        for tid in self.queue:
            plan = self.plans[tid]
            # 这套本 tick 打算排几套（与 _refine_all 同口径；free_stove 仅影响封顶，给粗值）
            sets = self._calc_sets(plan, stock, pending, 4)
            runs, _buy = plan.net_plan(stock, pending, Counter(), sets=sets)
            for cid, n in runs.items():
                if n > 0 and int(cards_price(cid)) == CHANGE_FACE:
                    specs.append((int(cid), int(n), tid))
        # 需求量最大的 40 面卡优先补（先填满瓶颈），同量按卡 id 稳定
        specs.sort(key=lambda x: (-x[1], x[0]))
        return specs

    async def _drain_box_gift(self, steps: list[dict], stock: Counter | None = None,
                              rd=None) -> tuple | None:
        """卡箱里已有的礼物套 48 面卡 → 就地变卡，**不收礼**。

        与 _gift_drain 相互独立（不受 gift_drain 开关约束）：礼物收进来产出的
        48 面卡和箱里躺着的那几张**本来就是同一套**（card_lib theme=487 手工织锦），
        变卡只认源卡的 slotid+slottype，箱里的卡同样能当 srcid —— 于是「收礼 →
        变卡」整条路可以省成「直接变」，既省一次收礼，也把白占箱位的卡变成素材。

        门槛（2026-09-26 真机实测补充）：**变卡失败也消耗源卡**（箱里 8 张，尝试 3 次
        只成 1 张，箱里当场剩 5 张），所以只在 dstid **确实是当前队列用得上的卡**时才
        动手 —— 变出一张用不上的卡，等于拿 2 张礼物卡换 1 张废卡。

        目标来源（2026-09-26 用户定调）：**不再写死** `gift_dst`。变卡的意义是「省掉炼
        40 面额这一步」，目标必须是**当前在炼的那套里的 40 面卡、且引擎正缺的那张**
        —— 所以这里交给 `_change_specs()` 把「本该从底卡炼的 40 面」按需求量列出，
        再交 `drain_box_gift_cards(dst_specs=...)` 轮流分散到各缺失种类（缺哪个变哪个、
        且不同种类都覆盖到，不会全堆成同一种）。

        ★ 2026-09-26 用户定调「变卡失败要一直变，直到成功或者礼物卡没了」：失败不再
          静默 30 分钟，而是换下一张继续（失败也烧源卡，所以箱里的卡烧完必然收手）。

        ★ 变卡完**立刻刷新卡箱状态**（用户同条要求）：变出的 dst 卡进了箱子、源卡消失，
          不重读的话这一 tick 后面几步会拿旧库存判断「还差素材 → 又去买一张底卡」。
          `gift.drain_box_gift_cards` 内部每试一张都会重扫卡箱，本函数收尾再读一次主界面
          校正；返回新的 `rd`（tuple），调用方（tick 第 2 步）拿它当最新状态用，
          正常情况下净增 0 次额外请求。读失败就沿用调用方给的那份，不打断流程。
        """
        if stock is None:
            stock = self._stock
        pending = rd[3] if rd else self._pending
        # ★ 2026-10-18 修：分布变卡。旧版 `_pick_change_dst` 只挑**一张**单目标，礼物卡
        #   全堆成同一种 40 面卡，其余种类仍得从底卡炼 —— 礼物卡白变、卡炉还在炼 40 面。
        #   现改由 `_change_specs()` 把「本该从底卡炼的 40 面」按需求量列出，交
        #   `drain_box_gift_cards(dst_specs=...)` 轮流分散到各缺失种类，一次把缺口填满。
        specs = self._change_specs(stock, pending)
        if not specs:
            self.box_gift_last = {"ok": True, "skipped": True, "inbox": 0, "tried": 0,
                                  "changed": 0, "burned": 0, "dst": None,
                                  "message": "这一套的 40 面卡已够（或没有 40 面候选），"
                                             "本轮不消耗卡箱里的礼物卡"}
            return rd
        dst_ids = [cid for cid, _need, _tid in specs]
        dst_need = {cid: need for cid, need, _tid in specs}
        try:
            r = await gf.drain_box_gift_cards(
                self.s, dst_specs=[(d, dst_need[d]) for d in dst_ids])
        except Exception as e:  # noqa: BLE001
            self._log("礼物卡变卡", False, f"异常：{e}")
            steps.append({"action": "礼物卡变卡", "ok": False, "detail": str(e)})
            return rd
        r["target"] = "分布变卡：" + "、".join(
            f"{card_name(d)}×{dst_need[d]}" for d in dst_ids)
        self.box_gift_last = r
        if r.get("skipped") and not int(r.get("tried") or 0):
            return rd
        changed = int(r.get("changed") or 0)
        ok = changed > 0
        # 轮转计数：本会话累计各 40 面卡被变出的张数（仍是兜底，防死盯一张）
        for d, c in (r.get("per_dst") or {}).items():
            self._change_rot[int(d)] += int(c)
        # 变卡动过卡箱 → 立刻重读，把「源卡没了 / 变出 dst 了」同步给引擎与调用方
        if changed or r.get("burned") or r.get("tried"):
            try:
                rd_new = await self._read_state()
            except Exception:  # noqa: BLE001
                rd_new = None
            if rd_new:
                rd = rd_new
                self._stock, self._pending = rd[1], rd[3]
        self._log("礼物卡变卡", ok, r.get("message") or "")
        steps.append({"action": "礼物卡变卡", "ok": ok, "detail": r.get("message") or ""})
        # 逐张明细单独成行（成功/失败都记，显示消耗的源卡名与变出的目标卡名）
        self._log_change_attempts(r.get("attempts"), "卡变明细")
        return rd

    async def _gift_drain(self, steps: list[dict], st: dict | None = None) -> None:
        """礼物箱快满 → 收礼 + 变卡（自动炼卡的可选环节，见 gf.drain_gift_box）。

        只在「箱件数 ≥ upper」时才动，变卡约 50% 成功、失败不阻塞 —— 所以这里
        除了写日志不改变 tick 的成败，绝不让炼卡主流程因为变卡失败而中断。

        ★ 2026-10-18 修：分布变卡（同 _drain_box_gift），把礼物卡按缺失种类分散变出，
          不再只变同一张。
        ★ 2026-11-02 最高优先级规则：箱位只够凑齐本套时**整套延后**。收礼变卡
          是唯一会把「箱外卡凭空搬进箱」的操作（每变一张净 +1 格），变出来的
          若对凑齐零贡献，就会挤掉凑齐所需的最后一格。
        """
        specs = self._change_specs(self._stock, self._pending)
        if not specs:
            self.gift_last = {"ok": True, "skipped": True, "took": 0, "changed": 0,
                              "dst": None,
                              "message": "这一套的 40 面卡已够（或没有 40 面候选），收礼变卡跳过"}
            return
        if st is not None:
            tight = self._collect_tight(st, self._stock)
            # 只要有一个变卡目标本身就是「凑齐还缺的卡」，就照常变（它在帮凑齐）；
            # 全部是多余的才延后。
            if tight > 0 and not any(self._is_collect_essential(c, self._stock)
                                     for c, _n, _t in specs):
                self._log("礼物变卡", True,
                          f"暂缓：两箱一个空位都没有（还差 {tight} 格），"
                          f"变出的卡放不下，腾出空位再变")
                self.gift_last = {"ok": True, "skipped": True, "took": 0, "changed": 0,
                                  "dst": None,
                                  "message": "两箱无空位，收礼变卡暂缓"}
                return
        dst_ids = [cid for cid, _need, _tid in specs]
        dst_need = {cid: need for cid, need, _tid in specs}
        try:
            r = await gf.drain_gift_box(
                self.s, dst_specs=[(d, dst_need[d]) for d in dst_ids],
                upper=int(self.gift_upper), lower=int(self.gift_lower))
        except Exception as e:  # noqa: BLE001
            self._log("礼物变卡", False, f"异常：{e}")
            steps.append({"action": "礼物变卡", "ok": False, "detail": str(e)})
            return
        r["target"] = "分布变卡：" + "、".join(
            f"{card_name(d)}×{dst_need[d]}" for d in dst_ids)
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
        if ok:                              # 轮转计数：本会话累计变出几张，防死盯一张
            for d, c in (r.get("per_dst") or {}).items():
                self._change_rot[int(d)] += int(c)
        self._log("礼物变卡", ok, r.get("message") or "")
        steps.append({"action": "礼物变卡", "ok": ok,
                      "detail": r.get("message") or ""})
        # 逐张明细单独成行（成功/失败都记，显示消耗的源卡名与变出的目标卡名）
        self._log_change_attempts(r.get("attempts"), "收礼变卡明细")

    async def _collected_shows(self, tid: int) -> set:
        """读 card_collection_get，返回该套【已收集】的 qq秀实例 id 集合（字符串）。

        一次调用解析响应里**所有** <Node> 并全库缓存（self._show_cache），本 tick
        内多次调用只发一次网络。qqshow_str 用 HTML 实体 `&#x2C;`（逗号）分隔实例 id。
        成功上交后由调用方把实例 id 写回缓存，避免下一轮在同一缓存窗口内重复选到它。
        """
        now = time.time()
        if self._show_cache and now - self._show_cache_ts < 20:
            return self._show_cache.get(tid, set())
        try:
            r = await self.s.post(EP_COLLECTION_GET)
            raw = r.get("raw", "") or ""
            cache: dict[int, set] = {}
            for spec in re.findall(r"<Node\s+([^>]*?)/?>", raw):
                th = re.search(r'theme_id="(\d+)"', spec)
                qs = re.search(r'qqshow_str="([^"]*)"', spec)
                if th and qs:
                    ids = [x.strip() for x in qs.group(1).replace("&#x2C;", ",").split(",")
                           if x.strip()]
                    cache[int(th.group(1))] = set(ids)
            self._show_cache = cache
            self._show_cache_ts = now
            return cache.get(tid, set())
        except Exception:
            return self._show_cache.get(tid, set())

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
        """好友 uin 列表（card_user_list）。

        ⚠️ 实测 card_user_list 是 **uin 升序**、与等级/活跃度无关（2026-10-12 拉原始
        响应确认：<user> 属性只有 uin/nick/exp/vip_*/turn，无 level 字段，顺序=uín）。
        但 `exp` 是等级代理，故额外建一份按 exp 降序的 `_friends_by_exp`，让偷炉优先
        挑等级高（更可能活跃）的好友。缓存。"""
        if self._friends is not None:
            return self._friends
        try:
            r = await self.s.post(EP_USER_LIST)
        except Exception:
            return []
        raw = r.get("raw", "")
        users = re.findall(r'<user\s+uin="(\d+)"[^>]*?exp="(\d+)"', raw)
        if not users:                        # 兜底：regex 没匹配到 exp 就退回纯 uin 序
            self._friends = re.findall(r'<user\s+uin="(\d+)"', raw)
            self._friends_by_exp = None
            return self._friends
        self._friends = [u for u, _ in users]
        # 按 exp 降序：等级高≈活跃概率大（用户 2026-10-12 定调），偷炉优先这批
        self._friends_by_exp = [u for u, _ in
                                sorted(users, key=lambda x: int(x[1]), reverse=True)]
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
        """按「等级高优先」挑第一个可用的偷炉对象。
        从 exp 降序列表头部取前 N 个（等级高≈活跃概率大，用户 2026-10-12 定调），
        第一个检测到有空主炉的就借。若检测盲区（都测不到空炉）则退而取扫描到的
        最后一名，交给服务端最终裁决（好友相关错误会被缓存、换下一名重试）。
        排除：① 近期偷炉失败被缓存的好友；② 已在对方炉上挂着我自己未取回的卡的好友
        （再偷会吃 -33084，等 tick 收卡后自然解除）。"""
        friends = await self._load_friends()
        if not friends:
            return None
        now = time.time()
        # ★ 2026-11-03 修：`card_user_list` 返回的好友列表里**包含自己**（uin 不带前导 0，
        #   而 self.s.uin 是 "0004821992"），旧版只按字符串比 `busy`、且从不排除自己 →
        #   真机实测发出 `card_stove_steal` 给 opuin=4821992（自己），被服务端拒：
        #   「魔法学院规定不允许偷自己家的炉子！」→ 偷炉冷却 120s、好友还被拉黑 30 分钟，
        #   等于偷炉功能整个失效。统一按数值口径判「是不是我」。
        me = str(self.s.uin or "")
        me_n = me.lstrip("0") or me

        def _is_me(u) -> bool:
            u = str(u or "")
            return bool(u) and (u.lstrip("0") or u) == me_n

        busy = {str(c.get("opuin")) for c in st.get("stove", [])
                if c.get("opuin") and not _is_me(c.get("opuin"))}
        # ★★ 2026-11-14：**指定好友 = 只用它，不换人**（用户定调：真机都是指定好友）。
        #   旧版会因「被失败缓存 / 探测到满炉 / 炉上挂着我的卡」回落去扫描别的好友，
        #   与用户意图相反，而且指定的人通常就是自己想借的那台。现在：指定了就只投它，
        #   失败交给服务端裁决、下一 tick 继续对同一个好友重试（不拉黑、不全局冷却，
        #   见 `_steal_fail_policy`）。唯一例外：它炉上还挂着我未取的卡（-33084）→
        #   本 tick 先不投，等收卡再来 —— **仍然不换人**。
        pref = self.preferred_steal_friend
        if pref and not _is_me(pref):
            _pn = str(pref).lstrip("0") or str(pref)
            if any((u.lstrip("0") or u) == _pn for u in busy):
                return None
            return pref
        # 否则按「等级高优先」扫描：从 exp 降序列表头部取前 N 个，第一个有空主炉就借
        # （exp 是等级代理，用户定调等级高≈活跃；原「从最后 12 名往回」是 uin 序、
        #  与活跃度无关，已废弃 —— 2026-10-12）
        ordered = self._friends_by_exp or friends
        window = ordered[:self.steal_search_depth]
        chosen = None
        probes = 0
        for uin in window:
            if _is_me(uin):
                continue
            if self._friend_fail.get(uin, 0) > now:
                continue
            if uin in busy:
                continue
            if probes >= self.steal_probe_max:
                # ★ 2026-11-14：探测配额用完 → 剩下的当退化候选直接投（服务端裁决），
                #   不再继续打 mainpage（-3001 是读频次限流，探测越多越毒）。
                chosen = chosen or uin
                break
            probes += 1
            e = await self._friend_stove_empty(uin)
            if e > 0:
                return uin
            if e < 0:
                # 读失败 = 多半已被限流（-3001）/网络异常：继续扫只会把限流拖成
                # 整段冷却，且本函数是「挑一个能投的人」，不是必须探明不可。
                # 立刻停手，用当前退化候选（没有就它自己）交给服务端裁决。
                return chosen or uin
            chosen = uin                      # 退化候选：测不到空炉也尝试一次
        return chosen

    def _steal_fail_policy(self, code, friend) -> None:
        """偷炉失败后的退避策略（2026-11-14 重写）。

        旧版：`-33031` 之外**一律**「拉黑该好友 30 分钟 + 全局冷却 120s」。
        120s = 8 个 tick（45s/tick），即一次「好友炉满」就把偷炉整体停掉；而
        `-33003`（该好友炉满）/ `-33084`（该好友挂着我未取的卡）都只是**单个好友**
        的问题，换个人几乎必成（好友 194 人）。偷炉可用率被自己压到很低 —— 这正是
        「DP 按 M+1 台排计划、执行层只拿到 M 台」的机制级原因（`dp_controller`
        的 40 面卡 mclass=(0,2) + steal_cap=1）。

        新口径：
          · `-33031` 全局限制（我自己已有一炉在偷）→ 只设全局冷却，不拉黑好友；
          · **指定好友**（真机常态）→ 不拉黑、不冷却，下一 tick 对同一个好友重试；
          · 其它（好友炉满 / 挂着我没取的卡 / 未知）→ 只拉黑该好友，不全局冷却。
        """
        now = time.time()
        if code == -33031:
            self._steal_cooldown_until = now + 120
            return
        pref = str(self.preferred_steal_friend or "")
        _f = str(friend or "")
        if pref and _f and (_f.lstrip("0") or _f) == (pref.lstrip("0") or pref):
            # 指定好友（用户口径：真机都用指定好友）：**不拉黑、不换人**，只给一个
            # 短冷却 ≈ 1.3 个 tick 后继续对同一人重试。
            # 为什么不是「完全不禁」：`_log` 没有节流，每次失败都会 append 一条日志 +
            # `stats["errors"] += 1` + 落盘；tick 是 45s，好友炉满时会变成每 45s 刷一条。
            # 旧版的 120s 全局冷却恰好把这个盖住了，去掉冷却就得自己补一个更短的。
            self._steal_cooldown_until = now + 60
            return
        if friend:
            self._friend_fail[friend] = now + 1800

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
            # 偷炉失败分三类（2026-11-14 重写，见 `_steal_fail_policy`）：
            #   -33031 全局限制（我还有一炉在偷）→ 只冷却，不拉黑；
            #   指定好友 → 不拉黑不冷却，下一 tick 对同一个人重试；
            #   其它好友问题（炉满 / 挂着我未取的卡）→ 只拉黑该好友。
            #   ★ 旧版这里 `else` 分支同时设全局冷却 120s，等于「好友炉满一次，
            #     偷炉停 8 个 tick」—— DP 计划的第 M+1 台机器就此长期拿不到。
            self._steal_fail_policy(code, friend)

    async def _buy_for(self, tid: int, target: int, short: dict, steps: list[dict]) -> bool:
        """就地为 (套 tid 的) target 这一炉补买缺的底卡。全成功返回 True。"""
        plan = self.plans[tid]
        for m, n in short.items():
            for _ in range(n):
                r = await self.s.post(EP_BUY, card_id=m, theme_id=tid)
                ok = r.get("code") == 0
                if ok:
                    self.stats["bought"] += 1
                    self._mark_bought(m)
                msg = r.get("message") or err_msg(r.get("code"))
                buylog.log_buy("engine._buy_for", m, tid, code=r.get("code"),
                               ok=ok, note=f"{plan.cname(target)}素材 {msg}")
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
                # 套数设置（0=自动：1 星按囤卡炼多套不空炉；>0 固定每轮排这么多套）
                "surplus_sets": self.surplus_sets,
                "sets_now": {self.plans[t].name: self._last_sets.get(t, 1)
                             for t in self.queue},
                "randchance": self.randchance,
                "state_delta": self.state_delta[:10],
                "last_pai_raw": self.last_pai_raw[:400],
                "last_collect": self.last_collect,
                # 每套「40 面卡缺口」：这一套从头炼到尾还要几张 40 面卡（库存已抵扣）
                "face40": [{"tid": t, "theme": self.plans[t].name,
                            # credit = 变卡环节接下来会交的 40 面卡，顶在净需求里，
                            # 所以下面这轮**不再拿底card炼 40 面卡**
                            "credit": dict(self.face40_credit.get(t) or {}),
                            **face40_gap(self.plans[t], self._stock, self._pending)}
                           for t in self.queue],
                "gift": {"enabled": self.gift_drain,
                         "dst": pick["dst"] if pick else None,
                         "dst_name": pick["name"] if pick else "",
                         "dst_face": pick["face"] if pick else 0,
                         "dst_theme": pick["theme"] if pick else "",
                         "target": (pick.get("target") if pick else ""),
                         "need40": (pick.get("need_total") if pick else 0),
                         "have40": (pick.get("have") if pick else 0),
                         "upper": self.gift_upper, "lower": self.gift_lower,
                         "last": self.gift_last},
                "box_gift": {"enabled": self.box_gift_change,
                             "dst": pick["dst"] if pick else None,
                             "dst_name": pick["name"] if pick else "",
                             "dst_face": pick["face"] if pick else 0,
                             "dst_theme": pick["theme"] if pick else "",
                             "target": (pick.get("target") if pick else ""),
                             "need40": (pick.get("need_total") if pick else 0),
                             "have40": (pick.get("have") if pick else 0),
                             "useful": bool(pick), "last": self.box_gift_last},
                "steal": {"enabled": self.steal_enabled,
                          "preferred": self.preferred_steal_friend,
                          "last_steal": self.last_steal,
                          "blocked": self.steal_blocked,
                          "cooldown_until": self._steal_cooldown_until},
                # DP 调度开关 + 最近一次 decide 统计（solve_ms/n_types/bounded）
                "dp_mode": self.dp_mode,
                "dp": self.dp_stats_last,
                "log": self.log[-30:]}
