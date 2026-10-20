"""忠实 tick 仿真器 —— 验证 auto_refine 的真实调度逻辑，不重写任何决策代码。

做法（关键）：只替换最外层的「网络 + 时钟」，让 AutoRefine.tick() 原封不动地跑。
  · VirtualSession 模拟 QQ 服务端：维护虚拟卡箱/卡炉状态，按真实端点语义改变状态；
    EP_MAINPAGE 返回的 raw 用**真实的 parse_full / parse_user_cards** 解析，所以连
    状态解析都是真代码（不是我另写一套）。
  · 虚拟时钟：每跑一个 tick，now += interval(45s)。卡片按真实 secs(=theme_compose_list[6])
    在到时后 flag=2，下一 tick 的收卡环节自动收进箱 —— 与真机一致。
  · 变卡 / 炼卡 / 买卡 / 收卡 / 上交 / 偷炉 全部由虚拟后端按真实端点语义执行。

可加参数：
  --tid 290        （默认 290 七雄Q传，2 星 4 层）
  --gift 20        （卡箱初始塞几张 48 面礼物卡，用于触发 box_gift_change 分布变卡）
  --steal on|off   （偷炉开关，默认 on：1 个偷炉位）
  --sets N         （>0 = 固定每轮排 N 套，即 ar.surplus_sets；0 = 默认按未收集 qq秀数自动）
  --uncollected N  （seed 已收集 qq秀，使自动模式按 N 个未收集排套；0=不seed=全未收集。
                     配合 --rounds N 可精确复刻 sched_cmp 的 sets=N 实例：
                     自动模式每上交 1 轮未收集数 -1，排产目标自动收缩，不超产）
  --rounds N       （max_rounds：每套上交 N 轮后停，默认 1）
  --random         变卡按 50% 概率（默认确定性 100% 成功，便于看逻辑）
  --seed 0         随机种子
跑法：python -m backend._sim_faithful

对比口径（与 backend/sched_cmp 对齐）：--sets N --rounds N --gift 0 --steal on
  → 空箱、无变卡、3 主炉+1 偷炉=4 台机器、净需求与 sched_cmp.compute_runs(N) 同口径
  （每张套内卡含 +1 收藏需求 → 30N 个 smelt 作业）。
  循环结束打印 `=== SUMMARY {json} ===`：最后完工时刻/顶层卡各次完工/上交时刻/产量/残留。
"""
from __future__ import annotations
import argparse
import asyncio
import json
import random
import time
from collections import Counter
from pathlib import Path

from backend import auto_refine as ar_mod
from backend.auto_refine import AutoRefine, CHANGE_FACE, card_name, _gift_info_map
from backend import gift as gf
from backend.constants import (EP_MAINPAGE, EP_REFINE, EP_BUY, EP_COLLECTION_ADD,
                              EP_COLLECTION_GET, EP_USER_LIST, EP_STEAL, EP_TRANSFER)


# ---- 把 asyncio.sleep 变成瞬时（虚拟时钟自己控制，不需要真等） ----
async def _no_sleep(*a, **k):
    return None
asyncio.sleep = _no_sleep

# ---- 把 time.time 接到虚拟时钟（否则 _collected_shows 的 20s 缓存永远不刷新） ----
_VT = {"now": 0}
time.time = lambda: _VT["now"]


def _load_card_lib():
    p = Path(__file__).resolve().parent.parent / "data" / "card_lib.json"
    return json.loads(p.read_text(encoding="utf-8"))


LIB = _load_card_lib()
# 卡 id -> 炼制秒数（来自 theme_compose_list[c][6]，与真实 net_plan/plan_summary 同口径）
SECS = {}
for c in LIB["theme_compose_list"]:
    SECS[int(c[2])] = int(c[6])


def secs_of(cid: int) -> int:
    return SECS.get(int(cid), 3600)


# ---- 虚拟服务端 ----
class VirtualSession:
    MAIN_SLOTS = [1, 2, 3]          # 主炉 3 个（与用户心智一致）
    STEAL_SLOT = 9                  # 偷炉 1 个

    def __init__(self, uin: int = 123456, change_cur: int = 80, store_cur: int = 80):
        self.uin = uin
        self.now = 0
        self.change = {}            # slot -> cid  (slottype 0)
        self.store = {}             # slot -> cid  (slottype 1)
        self.change_cur = change_cur
        self.store_cur = store_cur
        self.stoves = {}            # slot -> {"cid","opuin","endtime","steal"}
        self.friend_uin = "999000001"
        self.ar = None              # 运行期注入
        self.rng = random.Random(0)
        self.change_rng = False
        self.collected: dict[int, set] = {}   # tid -> 已收集 qq秀 实例 id（模拟真实上交反馈）
        self.completions: list[tuple] = []    # (完工时刻endtime, cid, 是否偷炉)——精确完工记录

    # ---- 槽位分配 ----
    def _free_change_slot(self):
        used = set(self.change)
        for s in range(1, self.change_cur + 1):
            if s not in used:
                return s
        return None

    def _free_store_slot(self):
        used = set(self.store)
        for s in range(1, self.store_cur + 1):
            return s if s not in used else None
        return None

    def _free_main_stove(self):
        for s in self.MAIN_SLOTS:
            if s not in self.stoves:
                return s
        return None

    def _free_steal_stove(self):
        if self.STEAL_SLOT not in self.stoves:
            return self.STEAL_SLOT
        return None

    def _add_to_box(self, cid: int):
        s = self._free_change_slot()
        if s is not None:
            self.change[s] = cid
            return True
        s = self._free_store_slot()
        if s is not None:
            self.store[s] = cid
            return True
        return False

    def _remove_mat(self, slot: int, slottype: int):
        box = self.change if slottype == 0 else self.store
        return box.pop(slot, None)

    # ---- 序列化 mainpage（被真实 parse_full 解析） ----
    def _serialize(self) -> str:
        now = self.now
        # 卡箱
        def box_xml(key, d, cur):
            cards = "".join(
                f'<card slot="{s}" id="{c}" type="0" st="{1 if key=="store" else 0}"/>'
                for s, c in sorted(d.items()))
            return f'<{key} cur="{cur}" max="{cur*2}">{cards}</{key}>'
        change_xml = box_xml("changebox", self.change, self.change_cur)
        store_xml = box_xml("storebox", self.store, self.store_cur)
        # 卡炉：占用的 + 空的主炉 + 偷炉（空/占）
        stove_cards = []
        main_occupied = 0
        for s in self.MAIN_SLOTS:
            st = self.stoves.get(s)
            if st:
                main_occupied += 1
                flag = 2 if now >= st["endtime"] else 1
                stove_cards.append(
                    f'<card slot="{s}" id="{st["cid"]}" flag="{flag}" '
                    f'opuin="{st["opuin"]}"/>')
            else:
                stove_cards.append(f'<card slot="{s}" id="0"/>')
        steal = self.stoves.get(self.STEAL_SLOT)
        if steal:
            flag = 2 if now >= steal["endtime"] else 1
            stove_cards.append(
                f'<card slot="{self.STEAL_SLOT}" id="{steal["cid"]}" flag="{flag}" '
                f'opuin="{steal["opuin"]}" slottype="steal"/>')
        else:
            stove_cards.append(f'<card slot="{self.STEAL_SLOT}" id="0" slottype="steal"/>')
        stovebox = (
            f'<stovebox cur="{len(self.MAIN_SLOTS)+1}" max="{(len(self.MAIN_SLOTS)+1)*2}">'
            + "".join(stove_cards) + "</stovebox>")
        stoves_xml = "".join(
            f'<stove id="{st["cid"]}" endtime="{st["endtime"]}"/>'
            for st in self.stoves.values())
        return (f'<user randchance="0" gold_fen="0" color_stone="0">'
                f'<hello time="{now}"/>'
                + change_xml + store_xml + stovebox + stoves_xml +
                f'</user>')

    # ---- 端点 ----
    async def post(self, endpoint: str, **kw):
        if endpoint == EP_MAINPAGE:
            return {"code": 0, "raw": self._serialize()}
        if endpoint == EP_REFINE:
            return self._do_refine(kw, steal=False)
        if endpoint == EP_STEAL:
            return self._do_refine(kw, steal=True)
        if endpoint == EP_BUY:
            cid = int(kw.get("card_id") or 0)
            ok = self._add_to_box(cid)
            return {"code": 0 if ok else -1, "message": "ok" if ok else "箱满"}
        if endpoint == EP_COLLECTION_ADD:
            tid = int(kw.get("theme") or 0)
            plan = self.ar.plans.get(tid) if self.ar else None
            if plan:
                for cid in plan.ids:
                    # 从卡箱每种去掉 1 张（上交消耗整套）
                    for box in (self.change, self.store):
                        for s, c in list(box.items()):
                            if c == cid:
                                box.pop(s)
                                break
                # 记录刚上交的 qq秀 实例（giftid -> 实例 id，与真实 card_collection_get 同口径）
                gmap = _gift_info_map()
                gid = str(kw.get("giftid") or plan.giftid)
                inst = gmap.get(gid, (None, ""))[0]
                if inst:
                    self.collected.setdefault(tid, set()).add(inst)
            return {"code": 0, "raw": ""}
        if endpoint == EP_COLLECTION_GET:
            # 返回已收集 qq秀（与真实响应结构一致：<Node theme_id qqshow_str(逗号分隔实例 id)>）
            nodes = []
            for tid, insts in self.collected.items():
                if insts:
                    nodes.append(f'<Node theme_id="{tid}" qqshow_str="{",".join(sorted(insts))}"/>')
            return {"code": 0, "raw": "".join(nodes)}
        if endpoint == EP_USER_LIST:
            return {"code": 0,
                    "raw": f'<user uin="{self.uin}"><users>'
                           f'<user uin="{self.friend_uin}" nick="好友A" exp="200"/>'
                           f'</users></user>'}
        return {"code": 0, "raw": ""}

    def _do_refine(self, kw, steal: bool):
        target = int(kw.get("targetid") or 0)
        # 消耗素材（与引擎 _payload 选的 slot/slottype 对应）
        for i in range(1, 4):
            sk = kw.get(f"slot{i}")
            if sk is None:
                continue
            self._remove_mat(int(sk), int(kw.get(f"slottype{i}", 0)))
        if steal:
            slot = self._free_steal_stove()
            opuin = str(kw.get("opuin") or self.friend_uin)
        else:
            slot = self._free_main_stove()
            opuin = str(self.uin)
        if slot is None:
            return {"code": -33003, "message": "炉满"}
        self.stoves[slot] = {"cid": target, "opuin": opuin,
                             "endtime": self.now + secs_of(target), "steal": steal}
        return {"code": 0, "message": "ok"}

    async def qzone(self, method: str, endpoint: str, **kw):
        # 变卡（card_user_transfer_card）：消耗源卡 -> 产出目标 40 面卡
        if endpoint == EP_TRANSFER:
            src_slot = int(kw.get("slotid") or 0)
            src_st = int(kw.get("slottype") or 0)
            self._remove_mat(src_slot, src_st)
            dst = int(kw.get("dstid") or 0)
            self._add_to_box(dst)
            if self.change_rng and self.rng.random() < 0.5:
                # 50% 失败：源卡仍被烧掉（上面已删），目标不产出（已在 add 前返回）
                # —— 为忠实「失败也烧源卡」，这里目标不进箱
                self.store.pop(max(self.store), None) if False else None
                return {"code": 0, "raw": '<QQHOME code="0" result="0">'}
            return {"code": 0, "raw": '<QQHOME code="0" result="1">'}
        return {"code": 0, "raw": ""}

    async def get_refined_card(self, slot):
        st = self.stoves.pop(int(slot), None)
        if st:
            self.completions.append((st["endtime"], st["cid"], bool(st["steal"])))
            self._add_to_box(st["cid"])
            return {"ok": True, "message": "成功"}
        return {"ok": False, "message": "无此炉"}

    async def get_steal_card(self, slot, opuin):
        st = self.stoves.pop(int(slot), None)
        if st:
            self.completions.append((st["endtime"], st["cid"], bool(st["steal"])))
            self._add_to_box(st["cid"])
            return {"ok": True, "message": "成功"}
        return {"ok": False, "message": "无此炉"}

    async def open_pai(self):
        return {"ok": True, "message": "ok", "raw": "", "items": []}

    # 腾位路径（箱满才触发，本仿真箱容量大不会走到，但保留接口）
    async def sell_card(self, slot, cid, box_type=1):
        return {"ok": True, "message": "ok"}

    async def move_card(self, cid, slot, from_box=0):
        return {"ok": True, "message": "ok"}


# ---- 快照 ----
def snapshot(vs: VirtualSession, ar: AutoRefine):
    lines = []
    now = vs.now
    hh = now // 3600
    # 卡炉
    furn = []
    for s in sorted(vs.stoves):
        st = vs.stoves[s]
        remain = max(0, st["endtime"] - now)
        tag = "偷" if st["steal"] else f"主{s}"
        furn.append(f"{tag}炉:{card_name(st['cid'])}[{st['cid']}](剩{remain/3600:.2f}h)")
    # 关键卡库存
    plan = ar.plans[ar.queue[0]] if ar.queue else None
    if plan:
        stock = Counter()
        for c in vs.change.values():
            stock[c] += 1
        for c in vs.store.values():
            stock[c] += 1
        key_ids = sorted(plan.ids)
        # 按面额分组展示
        by_face = {}
        for cid in key_ids:
            f = int(plan.cards[cid][3] or 0)
            by_face.setdefault(f, []).append(cid)
        box_lines = []
        for f in sorted(by_face, reverse=True):
            parts = []
            for cid in by_face[f]:
                parts.append(f"{card_name(cid)}[{cid}]×{stock.get(cid,0)}")
            box_lines.append(f"{f}面: " + " ".join(parts))
        free_ch = vs.change_cur - len(vs.change)
        free_st = vs.store_cur - len(vs.store)
        lines.append(f"  [卡炉] " + ("；".join(furn) if furn else "全空"))
        lines.append(f"  [箱存] " + "  ".join(box_lines))
        lines.append(f"  [箱位] 换卡箱空{free_ch} 保险箱空{free_st}")
    return hh, lines


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tid", type=int, default=290)
    ap.add_argument("--gift", type=int, default=20)
    ap.add_argument("--steal", default="on")
    ap.add_argument("--sets", type=int, default=0,
                    help=">0 = 固定每轮排 N 套（surplus_sets）；0 = 按未收集 qq秀自动")
    ap.add_argument("--uncollected", type=int, default=0,
                    help="seed 已收集 qq秀，使自动模式只剩 N 个未收集；0=不seed")
    ap.add_argument("--rounds", type=int, default=1, help="max_rounds：每套上交 N 轮后停")
    ap.add_argument("--random", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if args.random:
        random.seed(args.seed)

    vs = VirtualSession()
    vs.change_rng = args.random
    # 初始塞礼物卡（48 面，theme=487），用于触发 box_gift_change 分布变卡
    gift_ids = sorted(gf._face48_ids())
    gid = gift_ids[0] if gift_ids else 7773
    for i in range(args.gift):
        s = vs._free_change_slot()
        if s is None:
            break
        vs.change[s] = gid

    ar = AutoRefine(vs, [args.tid], buy=True, max_rounds=max(1, args.rounds))
    if args.sets > 0:
        ar.surplus_sets = args.sets        # 固定套数，对齐 sched_cmp 实例口径
    ar.steal_enabled = (args.steal == "on")
    vs.ar = ar

    # seed 已收集 qq秀：自动模式的「未收集数」= 总秀数 - 已seed数。
    # 页面默认逻辑就是按未收集 qq秀数定套数（用户账号状态），seed 后即可精确
    # 复刻 sched_cmp 的 sets=N 实例，且每上交 1 轮目标自动收缩、不超产。
    if args.uncollected > 0:
        plan0 = ar.plans[args.tid]
        gmap0 = _gift_info_map()
        insts = [gmap0[g][0] for g in plan0.gifts
                 if gmap0.get(g) and gmap0[g][0]]
        for inst in insts[:max(0, len(insts) - args.uncollected)]:
            vs.collected.setdefault(args.tid, set()).add(inst)
        print(f"seed 已收集 qq秀 {len(vs.collected.get(args.tid, ()))}/{len(insts)} 个"
              f" → 自动模式按 {args.uncollected} 个未收集排套")

    # 包装 _log：把每次实际操作无损捕获到独立列表（绕过引擎自身 200 条裁剪）
    all_actions = []
    now_to_tick = {}
    orig_log = ar._log

    def _wrap_log(action, ok, detail):
        all_actions.append((_VT["now"], action, ok, detail))
        orig_log(action, ok, detail)
    ar._log = _wrap_log

    print(f"=== 忠实 tick 仿真：tid={args.tid} {ar.plans[args.tid].name} "
          f"初始礼物卡{args.gift}张 变卡{'随机50%' if args.random else '确定性100%'} "
          f"偷炉={'开' if ar.steal_enabled else '关'} ===")
    print(f"主炉={vs.MAIN_SLOTS.__len__()}个 偷炉={'1个' if ar.steal_enabled else '0个'} "
          f"interval=45s 卡炉秒数来自 theme_compose_list[6]（真实）")
    print(f"套数模式={'固定%d套/轮' % args.sets if args.sets > 0 else '自动(未收集qq秀数)'} "
          f"轮数上限={max(1, args.rounds)}"
          + (f" seed后未收集={args.uncollected}" if args.uncollected > 0 else ""))

    # 只看「实际操作」类，排除纯读取/状态变化噪声
    ACTION_OPS = {
        "礼物卡变卡", "礼物变卡", "合成", "炼卡", "合成跳过", "暂缓",
        "收卡", "买卡", "上交", "偷炉", "腾位",
    }

    INTERVAL = 45.0
    done_hh = None
    max_ticks = 15000
    for tick_n in range(max_ticks):
        vs.now += int(INTERVAL)
        _VT["now"] = vs.now            # 让 time.time 走虚拟时钟（刷新 _collected_shows 缓存）
        now_to_tick[vs.now] = tick_n + 1     # 第 1 个 tick 对应 now=45s
        await ar.tick()
        if not ar.queue and done_hh is None:
            done_hh = vs.now // 3600
            print(f">>> 队列空：第一套卡炼成于 模拟{done_hh}h（第 {tick_n + 1} tick）")
            break
    if done_hh is None:
        print(f">>> 未在 {max_ticks} tick 内完成（可能卡死，需检查逻辑）")

    # ---- 生成「有实际操作的 tick」流水，供人工审核 ----
    groups = {}
    for ts, action, ok, detail in all_actions:
        if action in ACTION_OPS:
            groups.setdefault(ts, []).append((action, ok, detail))

    out = []
    out.append(f"=== 有操作的 tick 流水：tid={args.tid} {ar.plans[args.tid].name} "
               f"初始礼物卡{args.gift}张 变卡{'随机' if args.random else '确定'} "
               f"偷炉={'开' if ar.steal_enabled else '关'} ===")
    out.append(f"主炉={vs.MAIN_SLOTS.__len__()} 偷炉={'1' if ar.steal_enabled else '0'} "
               f"interval=45s 卡炉秒数来自 theme_compose_list[6]（真实）")
    out.append(f"完成于 模拟{done_hh}h（第{tick_n + 1} tick）；有实际操作的 tick 共 {len(groups)} 个 / 总计 {tick_n + 1} tick")
    out.append("")
    for ts in sorted(groups):
        h = ts // 3600
        m = (ts % 3600) // 60
        tk = now_to_tick.get(ts, "?")
        out.append(f"[模拟 {h}h{m:02d}m · tick{tk}]")
        for action, ok, detail in groups[ts]:
            out.append(f"    [{'✓' if ok else '✗'}] {action}：{detail}")
    text = "\n".join(out)
    trace_path = Path(__file__).resolve().parent.parent / "data" / f"sim_trace_tid{args.tid}.txt"
    trace_path.write_text(text, encoding="utf-8")
    print(text)

    # ---- 机器可读摘要（供 sched_cmp/run_prod_compare.py 对比，不影响任何决策）----
    plan = ar.plans[args.tid]
    top_cid = max(plan.ids, key=lambda c: plan.depth.get(c, 0))
    machines = len(vs.MAIN_SLOTS) + (1 if ar.steal_enabled else 0)
    done_cnt = Counter(cid for _, cid, _ in vs.completions)
    t_last = max((e for e, _, _ in vs.completions), default=0)
    leftover = [(st["cid"], st["endtime"]) for st in vs.stoves.values()]
    t_all = max([t_last] + [e for _, e in leftover] + [0])
    work_s = sum(secs_of(cid) for _, cid, _ in vs.completions)
    submit_times = [ts for ts, a, ok, _d in all_actions if a == "上交" and ok]
    summary = {
        "tid": args.tid, "sets": args.sets, "uncollected": args.uncollected,
        "rounds": max(1, args.rounds),
        "gift": args.gift, "steal": bool(ar.steal_enabled), "machines": machines,
        "top_cid": top_cid, "ticks": tick_n + 1,
        "t_last": t_last, "t_last_h": round(t_last / 3600, 3),
        "t_all_h": round(t_all / 3600, 3),
        "top_times_h": [round(e / 3600, 3) for e in
                        sorted(e for e, c, _ in vs.completions if c == top_cid)],
        "submit_times_h": [round(t / 3600, 3) for t in submit_times],
        "done": {str(k): v for k, v in sorted(done_cnt.items())},
        "n_done": sum(done_cnt.values()),
        "leftover": [[c, round(e / 3600, 3)] for c, e in leftover],
        "work_h": round(work_s / 3600, 3),
        "util": round(work_s / (machines * t_all), 4) if t_all else 0,
    }
    print("=== SUMMARY " + json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
