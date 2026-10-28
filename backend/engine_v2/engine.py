"""编排层：Engine = 生产代码的仿真驱动器（单一实现，2026-10-26 用户定调）。

铁律（用户原话）：
  「仿真就是要模拟生产环境，真实 API 中的各种细节在仿真中也要模拟，
    引擎决策层和执行层就是同一套，不要分开写，要接就一起接。」

因此 Engine 不再自带 Planner / Executor —— 那两份是 engine_v2 早期把生产逻辑
**重写了一遍**的并行实现（贪心 planner.py 393 行、执行 executor.py 90 行），
导致「bench 数字」和「生产行为」是两条代码路径，grep 一次就能看出两边都在改。

现在的结构（仿真 = 生产，只有一层被替换）：
    VirtualSession（假的网络层：post(endpoint, **kw) 与 mc_api.Session 同签名
                    + 虚拟时钟）
  + AutoRefine.tick()（生产同一个 tick 流程：收卡/翻牌/变卡/上交/炼卡/买卡/偷炉）
  + DPTickController（勾选 DP 时，生产同一个决策器；不勾走 _refine_all 生产贪心）

即：**唯一被替换的是「网络 + 时钟」**（VirtualSession），决策与执行不重写。
所有真实 API 的坑（买卡逐张过容量、偷炉挑好友+冷却+拉黑、槽位失效重校验、
本地库存同步、collect 在 tick 第 3 步）都跟着生产代码一起被跑到。

代价与有意取舍：
  · bench 绝对值会变（旧 Engine 的贪心是 planner.py，生产贪心是 _refine_all；
    旧 Engine 还不刷新虚拟时钟 → 贪心永远不偷炉）。这是校正，不是回归。
  · 场景旋钮保持不变（空箱起步 / 箱容 / max_rounds / 是否偷炉 / 是否 DP），
    这样与 sched_cmp 的 from-scratch 实例口径仍然可比。
"""
from __future__ import annotations

from collections import Counter
from typing import List

from .model import ThemeModel

# 生产日志里「投炉成功」的动作名（_refine_all 的 tag / DP 的专用 tag）
_REFINE_TAGS = {"合成", "炼卡", "偷炉", "DP炼卡"}
_COLLECT_TAGS = {"上交"}
_BUY_TAG = "买卡"


class Engine:
    """仿真驱动器：把 AutoRefine 原封不动地跑在一个虚拟 Session 上。

    对外接口保持与旧 Engine 兼容（探针/bench 依赖）：
      · run_sim(...) -> (vs, ticks)
      · stats / completed_rounds / dp_ctrl / queue / plans / max_rounds
    已删除的实现：planner.py / executor.py / dp_adapter.py（见模块 docstring）。
    """

    def __init__(self, tids, buy: bool = True, surplus_sets: int = 0,
                 max_rounds: int = 0, steal_enabled: bool = True,
                 dp_single=None, use_dp: bool = False):
        self.queue: List[int] = [int(t) for t in tids]
        # ThemeModel 保留：它与生产共用同一份静态知识（dp_controller 也用）
        self.models = {t: ThemeModel(t) for t in self.queue}
        self.buy = buy
        self.surplus_sets = surplus_sets
        self.max_rounds = max_rounds
        self.steal_enabled = steal_enabled
        self.use_dp = use_dp
        if dp_single is not None:
            # 旧的「静态 DP 序」分支（dp_adapter.dp_order）在生产里没有对应物，
            # 已随 dp_adapter.py 一起删除。传 True 会被忽略 —— 显式喊一声，
            # 免得探针悄悄测成贪心还以为在测 DP。
            print("[Engine] 警告：dp_single 已废弃（生产无此路径），"
                  "改用 use_dp=True 走生产 DPTickController", flush=True)
        self.dp_single = bool(dp_single)
        self.completed_rounds: Counter = Counter()
        self.stats = {"refined": 0, "bought": 0, "buy_fail": 0,
                      "collected": 0, "errors": 0, "busy_ticks": 0,
                      "total_ticks": 0, "slot_busy_ticks": 0,
                      "slot_total_ticks": 0, "peak_change_used": 0,
                      "peak_store_used": 0, "peak_box_used": 0}
        self.ar = None              # 生产 AutoRefine 实例（run_sim 里建）
        self.plans = {t: m.tp for t, m in self.models.items()}   # 兼容旧探针
        self._vs = None

    # ---------------- 统计：从生产自己的 _log 派生（与面板同一份流水） ----------------
    def _tally(self, action: str, ok: bool, detail: str) -> None:
        st = self.stats
        if action == _BUY_TAG:
            if ok:
                st["bought"] += 1
            else:
                st["buy_fail"] += 1
                st["errors"] += 1
            return
        if action in _REFINE_TAGS:
            if ok:
                st["refined"] += 1
            else:
                st["errors"] += 1
            return
        if action in _COLLECT_TAGS and ok:
            st["collected"] += 1

    # ---------------- DP 控制器对外暴露（探针取 solve_count / solve_ms） ----------------
    @property
    def dp_ctrl(self):
        return getattr(self.ar, "_dp_ctrl", None) if self.ar is not None else None

    def _sync_peaks(self, vs) -> None:
        st = self.stats
        cu = len(vs.change)
        su = len(vs.store)
        st["peak_change_used"] = max(st["peak_change_used"], cu)
        st["peak_store_used"] = max(st["peak_store_used"], su)
        st["peak_box_used"] = max(st["peak_box_used"], cu + su)
        occ = len(vs.stoves)
        st["total_ticks"] += 1
        if occ:
            st["busy_ticks"] += 1
        st["slot_busy_ticks"] += occ
        st["slot_total_ticks"] += (len(vs.MAIN_SLOTS) + 1)

    async def run_sim(self, change_cur: int = 18, store_cur: int = 200,
                     max_ticks: int = 20000, interval: int = 45,
                     gift: int = 0, chaos: dict | None = None,
                     seed: int = 0, gift_box: int = 0,
                     gift_drain: bool = False,
                     gift_upper: int = 0, gift_lower: int = 0):
        """跑一遍仿真。返回 (VirtualSession, 实际 tick 数)。

        gift > 0 时先往换卡箱塞 N 张礼物卡（48 面）——生产环境箱里本来就有累计的
        礼物卡，变卡由此供给 40 面素材。bench 默认 0 保持「空箱起步」的可比口径。

        **gift_box**（与 gift 无关，别混）：礼物箱（freegift_get_list）的初始件数。
        真机 2026-10-28 实测 46/50。旧版虚拟层没有礼物箱模拟 → `gift_inbox()`
        读到空响应 count=0 → `gift_drain` 永不触发，这条路径从未被测过。

        **gift_drain**：打开「收礼变卡」环节（生产界面对应勾选框）。

        chaos：扰动覆盖（见 `_sim_faithful.CHAOS_SAFE/CHAOS_STRESS`）。默认走
        SAFE 档——含**真实**的变卡约 50% 失败（失败也烧源卡）。环境变量
        `MC_SIM_CHAOS=1` 切压力档（网络失败/提前收卡/被好友收走/卡被换走）。
        """
        from backend import _sim_faithful as sim      # 导入即把 time.time 接到虚拟时钟
        from backend.auto_refine import AutoRefine

        vs = sim.VirtualSession(change_cur=change_cur, store_cur=store_cur,
                                chaos=chaos, seed=seed)
        self._vs = vs
        if gift > 0:
            gids = sorted(sim.gf._face48_ids())
            gid = gids[0] if gids else 7773
            for _ in range(gift):
                s = vs._free_change_slot()
                if s is None:
                    break
                vs.change[s] = gid
        if gift_box > 0:
            # 真机形态：老件在前（gift_inbox 按 time 升序，drain 从最老的开始收）
            vs.gift_box = [{"index_id": 10000 + i,
                            "freegift_id": sim.GIFT_BOX_DEFAULT_ID,
                            "sender": f"sim{i}",
                            "nick": "",
                            "time": 1000 + i} for i in range(gift_box)]

        ar = AutoRefine(vs, self.queue, buy=self.buy, max_rounds=self.max_rounds)
        if self.surplus_sets:
            ar.surplus_sets = int(self.surplus_sets)
        ar.steal_enabled = bool(self.steal_enabled)
        ar.dp_mode = bool(self.use_dp)      # ★ 生产同一个开关
        # 收礼变卡（生产界面对应「收礼变卡」勾选框）
        ar.gift_drain = bool(gift_drain)
        if gift_upper:
            ar.gift_upper = int(gift_upper)
        if gift_lower:
            ar.gift_lower = int(gift_lower)
        vs.ar = ar
        self.ar = ar

        # 统计旁路：包一层生产自己的 _log，不改它的行为
        _orig_log = ar._log

        def _spy(action, ok, detail):
            self._tally(action, ok, detail)
            _orig_log(action, ok, detail)
        ar._log = _spy

        n = 0
        for n in range(max_ticks):
            vs.now += int(interval)
            sim._VT["now"] = vs.now          # ★ 虚拟时钟必须推进：否则 time.time()
            #   永远 0，生产里的偷炉冷却 / 缓存刷新逻辑全部失真
            vs.external_tick()               # 人工/外部扰动（提前收卡、被好友收走、卡被换走）
            await ar.tick()
            self.completed_rounds = Counter({t: ar.plans[t].rounds for t in self.plans})
            self._sync_peaks(vs)
            if n and n % 200 == 0:           # 心跳：长跑时给外部一个进度（也防静默被回收）
                print(f"  …tick {n} ({round(n * interval / 3600, 1)}h) "
                      f"done={dict(self.completed_rounds)} box={len(vs.change)}"
                      f"+{len(vs.store)} stoves={len(vs.stoves)}", flush=True)
            if not ar.queue:
                break
        return vs, n + 1
