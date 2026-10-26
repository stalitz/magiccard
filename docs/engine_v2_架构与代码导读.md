# engine_v2 架构与代码导读

> 目标：**新人只读这一份，就能定位代码、看懂数据流、知道改哪里、知道哪里不能碰。**
> 基线 commit：`c6d8338`（2026-10-26）。代码若改动，行号会漂移，但**符号名与不变量**稳定，请以符号名为准。
> 配套文档（本文不重复其内容）：
> - `docs/调度引擎架构设计.md` —— 设计期规格（为什么这么分层、五步流水线的业务推导）。**注意它有实现脱节之处**，凡冲突以本文与代码为准。
> - `docs/engine_v2_仿真报告.md` —— 全部实测数据（bench、CP-SAT 对照、根因复盘）。想知道"为什么这么做"去看它。

---

## 0. 一页速览

### 这是什么

游戏「魔法卡片」的**自动炼卡调度器**。一局里 3 个主炉 + 1 个偷炉（只能炼 40 面卡），卡箱有容量上限，底卡靠金币买（买卡有 1 tick 物流延迟），合成是 3 合 1 固定树。要回答的问题是：

> 每个 tick（45s）该买哪张底卡、该往哪个炉投哪张卡、该不该上交整套。

### 核心机制一句话

**每 tick 从当前真实状态解一次"剩余问题"的调度骨架，然后只执行到期的那几个动作；骨架不许每 tick 重算，只在"承诺点"重解。**

这不是"全状态 DP 填表最优"（4★ 联合状态量级 10²⁶，不可行），而是**作业级 RCPSP 的 anytime 启发式求解 + 滚动重规划**。名字里的 "DP" 指内部的记忆化（memo）与子问题分解，不指填表最优。真实交付质量：离 CP-SAT 精确最优 1.0%(1★) / 4.7%(2★) / 6.1%(3★) / 5.6%(4★) / 0.35%(5★)。

### 环境（先解决，否则第一步就 ModuleNotFoundError）

```bash
# 必须用带 httpx + ortools 的解释器（backend.mc_api 依赖 httpx）
PY=/Users/stalitz/.workbuddy/binaries/python/envs/default/bin/python
cd /Users/stalitz/www/魔法卡片            # 所有命令都在项目根执行
```

### 最省时间的上手路径

```bash
# ① 冒烟验证：1★ 单实例求解 + 独立模拟器复算（~1 秒）
$PY -c "
from backend.engine_v2.tests._verify_sched import build, simulate
from backend.sched_cmp.sched_dp import solve
inst = build(85)
mk, sched, stats = solve(inst, time_budget=0.1, init_stock=inst['init_stock'], memo=None, max_vecs=96)
ok, smk, why = simulate(inst, sched)
print(f'DP={mk/3600:.2f}h sim={smk/3600:.2f}h ok={ok} {why} nodes={stats[\"nodes\"]}')"
# 期望：DP=7.04h sim=7.04h ok=True nodes=6129

# ② 端到端对照：单套 1~5★ + 2套/3套代表（跑全部 8 例，数分钟）
$PY -m backend.engine_v2.tests.bench_dp_greedy

# ③ 生产：勾选前端「自动炼卡 → DP 调度」复选框（#auto-dp）
```

### 目录职责一览

| 目录/文件 | 行数 | 职责 | 谁调用 |
|---|---|---|---|
| `backend/engine_v2/world.py` | 101 | `parse_full()` 结果 → 聚合 `WorldState` | 所有决策入口 |
| `backend/engine_v2/model.py` | 107 | `ThemeModel` 包装 `ThemePlan`，纯静态查询 | planner / controller |
| `backend/engine_v2/plan.py` | 28 | `Action` / `Plan` 契约 | planner ↔ executor |
| `backend/engine_v2/planner.py` | 393 | **贪心**决策（默认路径，5 步流水线） | `engine.Engine.decide` |
| `backend/engine_v2/dp_controller.py` | 1029 | **DP 控制器**：骨架 + 有限偏差 | `engine.run_sim` / `auto_refine._dp_refine_all` |
| `backend/engine_v2/executor.py` | 90 | Plan → API 调用（**只给仿真用**） | `Engine.run_sim` |
| `backend/engine_v2/engine.py` | 175 | 仿真编排 `run_sim` + 门面 | bench / tests |
| `backend/engine_v2/dp_adapter.py` | 169 | `ThemeModel` → 老式实例（单套/联合序） | `engine.run_sim` 非 DP 分支 |
| `backend/sched_cmp/sched_dp.py` | 1036 | **求解器**：UB + DFS/memo/LB + LDS | 上面所有 DP 路径 |
| `backend/sched_cmp/cp_sat_plan.py` | 194 | CP-SAT 后端（同契约），标尺/冷启实验 | `_solve_plan` / 离线对照 |
| `backend/sched_cmp/instance.py` `recipes.py` `executor.py` | 75/212/133 | 离线评测的实例构造与独立模拟器 | `sched_cmp` 离线工具 |
| `backend/engine_v2/tests/` | — | 仿真 / 验证 / 探针（**不是单测框架**） | 手工执行 |

> ⚠️ `sched_cmp/` 其余文件（`sched_greedy.py` `sched_astar.py` `sched_ilp.py` `run_compare.py` …）是**离线评测历史遗留**，生产零引用。新人可以完全忽略，只在做算法调研时看。

---

## 1. 数据流

```
真实游戏 ──HTTP──> mc_api.Session ──> parse_full(raw) ──┐
                                                        ▼
                                              WorldState (库存聚合快照)
                                                        │
                        ┌───────────────────────────────┴───────────────┐
                        ▼                                               ▼
              Planner.decide()  (贪心)                      DPTickController.decide()
                5 步流水线，纯函数                           骨架缓存 → 承诺点? → sched_dp.solve
                        │                                               │
                        └──────────────┬────────────────────────────────┘
                                       ▼
                              Plan = List[Action]
                                       │
                  ┌────────────────────┴─────────────────────┐
                  ▼                                          ▼
        Executor.execute()  (仿真)              auto_refine._dp_refine_all (生产)
        GameClient=VirtualSession                执行层在本引擎重写：买卡过容量、
                                                  偷炉挑好友、槽位重校验、库存同步
```

**两条路径共享的只有三样东西**：`WorldState`（状态）、`ThemeModel`（静态树）、`Action`（动作契约）。**决策逻辑完全独立**。

---

## 2. 两条决策路径（必须分清）

`auto_refine.py:1077` 是唯一的开关点：

```python
if self.dp_mode:                                  # 前端「DP 调度」勾选
    await self._dp_refine_all(...)                # → DPTickController
else:
    await self._refine_all(...)                   # → 传统贪心（不走 engine_v2.Planner！）
```

> ★ **易错点甲**：生产环境的贪心路径在 `auto_refine._refine_all`，**不是** `engine_v2/planner.py`。`planner.py` 只在仿真/bench 里用。两者都是"贪心 5 步"，但代码不同源，改动不会互相影响。

> ★ **易错点乙**：`engine_v2/executor.py` 只被 `Engine.run_sim`（仿真）使用。生产执行在 `auto_refine._dp_refine_all` 里重新实现（要处理真实 API 的坑：买卡逐张过容量、偷炉挑好友+冷却+拉黑、槽位失效重校验、本地库存同步）。**改执行语义必须同时看这两处。**

> ★ **易错点丙**：`collect`（上交整套）在生产链路上**不由 DP 返回的 Action 执行**——`auto_refine` 在 tick 第 3 步已经做过上交，DP 看到的是"上交后状态"，所以 `_dp_refine_all:1240` 对 `action.kind == "collect"` 直接 `continue`。仿真链路（`Engine.run_sim`）则真的执行它。**调试"为什么没上交"时先确认在哪条链路上。**

### 2.1 贪心路径：`Planner.decide`（`planner.py:43`）

> ★ **极易踩的坑：Step 编号 ≠ 执行顺序。** 代码里的执行顺序是：

```
Step1 PriorityResolver      定 P0 / defer
   ↓
每套净需求 net_plan          runs / buy
   ↓
Step2 AdmissionControl      哪些套准入（实际实现=有活即准入）
   ↓
JIT 需求 due                 可炼前沿的底卡喂料 + collect 在即的收藏底卡
   ↓
Step4 BoxCompaction          腾箱（卖 10 面 / 挪保险箱）
   ↓
Step4a collect               整套在箱 → 上交（**确定性动作先于条件性买卡**）
   ↓
Step3 炉位分配 + 投炉         主炉填空 + 偷炉（只 40 面）
   ↓
Step5 买卡                   JIT 拉动 + 轮转买 + 箱容闸 + 速率闸
```

为什么是这个顺序（不要"顺手重排"）：

1. **collect 必须在买卡之前**：上交整套会释放 N 格箱位，`collect_freed` 要计入买卡预算。反过来会出现"本该能买却因箱满买不进"。
2. **腾箱必须在投炉之前**：投炉需要喂料在箱且有 slot；腾箱腾出的空间是给后续买卡用的。
3. **买卡必须最后**：它是唯一"增加卡数"的动作，所有释放量的计算都要先结算完。
4. **PriorityResolver 必须在 AdmissionControl 之前**：先定 P0 才知道给谁预留峰值。

关键局部逻辑：

| 位置 | 逻辑 | 为什么 |
|---|---|---|
| `planner.py:65` Step2 | 有 `runs>0` 或可 collect 就准入，**不做** `sum(runs)` 静态预留 | 旧版静态预留造成 3 套全 defer 死锁（精炼净腾 2 格，根本不长期占箱） |
| `planner.py:97` JIT due | 只买"非底卡喂料全在箱/炉"的合成卡的底卡 | 整套底卡囤齐是仿真假象（4★ 峰值 201 张，94% 是底卡） |
| `planner.py:288` 速率闸 | `len(furnaces)*3+6`，**不是** `free*3+3` | 炉忙时正该前瞻买料；旧版炉忙压到 3/tick → 98% 空闲是没料 |
| `planner.py:300` 轮转买 | 每轮每种底卡各买 1 张 | 按 cid 序买满一种 → 3 合 1 的 trio 永远凑不齐 → 死锁 |
| `planner.py:161` | 底卡也要分配 slot | 旧版 `is_base → continue` → 底卡永不进炉、泄漏囤箱 |

### 2.2 DP 路径：`DPTickController.decide`（`dp_controller.py:933`）

主流程（**这就是 `decide` 的 8 段，按代码顺序**）：

```
1  骨架缓存校验       active 集合 / box_cap 变 → 重建 _skeleton + 清 _memo + 弃 _plan
2  净需求             _union_need0 → (need0_map, buy0_map)，每套只排 1 轮
3  collect            整套在箱 → Action(collect)，累加 collect_freed
                      （★ 生产链路会被跳过：auto_refine 已在 tick 第 3 步上交过，见易错点丙）
3b 专辑底卡预买       _album_prebuy（非物流补偿，是 collect 优先级策略）
4  无需求早退         need0_map 与 buy0_map 都空 → 不解算、不执行，直接返回
5  承诺点判定         命中 → _solve_plan 重解并替换 plan；否则复用旧 plan
6  预买去重            把本 tick 已买的记进 plan["bought"]，防重买
7  执行 _run_plan      腾箱 → pass1 买卡 → pass2 精炼（有限偏差列表调度）
8  退避更新           本 tick 有真实精炼 → 清 _no_progress_resolves
```

**五个承诺点**（`dp_controller.py:986`，只有这些情况才重解）：

| 条件 | 含义 |
|---|---|
| `plan is None` | 首次 / 骨架被弃 |
| `_plan_rounds_sig != rounds_sig` | 某一套上交了整套（轮次翻页，需求结构变了） |
| `exhausted` | `cursor` 走完所有 op |
| `discrepancy > max(12, len(ops)//4)` | 有限偏差累计超限（执行现实偏离骨架太多） |
| `streak >= streak_limit` | 头动作连续 4 tick 发不出去（且未触达退避上限） |

> `MC_SOLVE_EVERY=1` 能让它每 tick 都解——**实测 4★ 反而 +5.27h**（5767 次解算），因为 `cursor` / 买卡去重 / 精炼时序门都以"一份持续的计划"为前提。**默认绝不开。**

---

## 3. 关键数据结构

### 3.1 `WorldState`（`world.py:30`）

聚合而非枚举——不记录"每张卡在哪个格"，只保留维度量，避免对称性爆炸。

| 字段 | 含义 | 来源 |
|---|---|---|
| `change` / `store` | 换卡箱 / 保险箱的 `Counter(cid → 张数)` | `parse_full()` |
| `change_cur` / `store_cur` | 两箱**已开通**格数（硬顶，不是 `max-used`） | `cap.change.cur` |
| `main_free` / `steal_free` | 空闲主炉位 / 偷炉位 | `cap.stove` |
| `furnaces: list[Furnace]` | 在炉卡 `(slot, cid, endtime, steal)` | `stoves` |
| `slots: list[(cid, slot, slottype)]` | 物理槽位索引（投炉要 slot，不能只给 cid） | `change`/`store` 逐条 |

派生量：`stock`（=change+store，**不含在炉**）、`pending`（=在炉 Counter）、`box_free`（双箱合计余位）。

> ★ 分工铁律：**`init_stock` 永远只用 `ws.stock`，在炉的卡走 `init_running` 传**。混用会导致同一张卡被算两次。

### 3.2 `ThemeModel`（`model.py`）

`ThemePlan` 的只读包装。要点：

- `comp`：合成表 `cid → (themeid, ?, name, m1, m2, m3, secs, ...)`；`comp[6]` 是炉时秒数。
- `is_base(cid)`：`cid not in comp` → 必须买，不可合成。
- `is_40(cid)`：面额 == 40（`CHANGE_FACE`）→ **唯一允许进偷炉的素材**。
- `feeders(cid)`：3 个素材 cid。
- `net_plan(stock, pending, sets)`：MRP 净需求，返回 `(runs, buy)`。**与贪心完全同口径**，是两条路径唯一共享的规划原语。
- `cp_order()`：关键路优先序（`cp(c)=secs(c)+max(cp(parent))`，**向上**口径）。

### 3.3 `Action`（`plan.py`）

最小不可分动作。`kind ∈ {refine, buy, collect, move, sell}`。

- `refine`: `target` + `feeders[(slot, slottype)…]` + `steal`
- `buy`: `cid` + `n`
- `collect`: `tid` + `giftid`
- `move` / `sell`: `cid` + `slot` + `slottype`

**顺序即语义**：`Plan` 是有序列表，Executor 照单执行、不重排。`sell`/`move` 必须排在 `buy` 前，否则腾出的格当 tick 用不上。

### 3.4 骨架 `plan`（dp_controller 内部，**不是** `Plan`）

```python
{
  "ops": [ {"kind":"buy"|"refine", "cid", "start", "feeders"?}, ... ],  # 按 start 排序
  "issued": [bool] * len(ops),      # 该 op 是否已处理（买卡去重 / 精炼已发）
  "cursor": int,                    # 精炼扫描到的位置（只前进）
  "solved_at": float,               # 解算时刻（tick 边界），elapsed = now - solved_at
  "planned_buy": Counter,           # 计划要买的量
  "bought": Counter,                # 已买的量（含专辑预买、应急买）
  "streak": int,                    # 头动作连续被卡 tick 数
  "discrepancy": int,               # 有限偏差累计
  "makespan": float, "n_types": int, "box_relaxed": bool,
}
```

`start` 是**绝对秒**，且已向上取齐到 tick 边界：`st_tick = int(s/tick + (1-EPS)) * tick`。这一句不能删——连续时间的 start 每拍系统性迟到 0~45s，在买卡链上会放大成"等料空转 + streak 假过时"。

### 3.5 RCPSP 实例 `inst`（喂给 `sched_dp.solve`）

| 键 | 必填 | 含义 |
|---|---|---|
| `n` / `types` / `idx` | ✓ | 类型数 / 类型（cid）列表 / 反查 |
| `dur[i]` | ✓ | 炉时秒（底卡 = `LAG = 45s`） |
| `mats[i]` | ✓ | `tuple((type_idx, k), …)`，同类型素材可互换 |
| `need0[i]` | ✓ | 该类型要产几张 |
| `machines` | ✓ | 主炉数（3） |
| `depth` / `cp` | ✓ | 深度 / 向下关键路（`dur + 喂料链`，叶全打平） |
| `mclass[i]` | 可选 | `0`主炉 / `1`买卡(shop) / `2`偷炉；**`(0,2)` = 可二选一**（40 面卡） |
| `shop_cap` | 可选 | 每 tick 买卡并发上限（15） |
| `box_cap` | 可选 | 任何落箱后 `sum(stock) ≤ box_cap`；`None`=无限制 |
| `steal_cap` | 可选 | 偷炉容量（默认按有无 class-2 类型推） |
| `init_stock` | 可选 | 当前库存 `list[int]`（**只含箱内**） |
| `init_running` | 可选 | `[(type_idx, 剩余秒, 机器类)]` |
| `ucp` | 内部 | 向上 cp，`solve` 自动算并注入 |
| `tb` / `tb_first` | 实验 | CP 顺序 tie-break 表（默认不传） |

---

## 4. `sched_dp.solve` 求解器

签名（`sched_dp.py:609`）：

```python
mk, sched, stats = solve(inst, time_budget=120, node_budget=8e6, use_memo=True,
                         maximal_only=True, init_stock=None, init_running=None,
                         init_machine_free=None, memo=None, max_vecs=96,
                         rec_budget=50000)
# mk    : makespan（秒）。传 init_* 时是"从当前时刻起还需多久"
# sched : [(type_idx, start_sec)]，每张卡 / 每次买卡一条
# stats : {nodes, time, bounded, ub0, memo_hits, opt, capped, box_relaxed?...}
```

### 4.1 三层结构

```
① UB 列表调度 (_ub_schedule)         → 立刻拿到一个合法解（incumbent 的起点）
② DFS + memo + LB 剪枝 (dfs)         → 试图改进，返回「相对额外耗时 + 后缀链」
③ 向量截断 (_vectors, max_vecs=96)   → 宽 ready 集不枚举到底，只取 top-K 向量
```

**这是 anytime 算法**：`stats["bounded"]=True` 表示预算耗尽，但 `best["val"]` / `best["sched"]` 始终是**完整且可行**的。任何一个时刻中断都有结果。

### 4.2 状态与分支

```
状态 = (need, stock, running_rel)
  need        : 各类型还差几张（tuple）
  stock       : 各类型箱内持有（tuple）
  running_rel : 在炉 [(type, 剩余秒, 机器类)]（相对当前时刻）
```

每个节点：

1. **收卡**：`剩余<=0` 的入库存；若 `sum(stock) > box_cap` → 该分支 `INF`（DP 自动学会 JIT：不敢早买）。
2. **算可开工项**：素材齐的 `need>0` 类型（按 `mclass` 分主炉/偷炉）。
3. **买卡是确定性规则，不分支**：`_buy_demand` 算出缺什么底卡就买（受 `shop_cap` 与 `box_cap` 约束）。这是"买卡入状态"的关键——DP 选卡时就知道"底卡要 LAG 才到"。
4. **分支**：`_vectors` 枚举"这一拍同时开哪些类型各几张"的**极大向量**，受 `caps = (idle_main, idle_shop, idle_steal)` 限制，按 `vec_prio` 排序后截断到 `max_vecs`。
5. **推进**：`dt = min(在炉剩余)`，递归。无可行向量且无在炉 → 死路 `INF`。

### 4.3 五个必须知道的正确性设计（都是踩过的坑）

| # | 设计 | 踩过的坑 |
|---|---|---|
| ① | `dfs` 返回值 **加上 `t_rel`**（while 内推进的时间） | 漏了 → memo 值系统性偏小 → 幻影最优（1★ 报 6.03h，真实 7.03h，比自身下界还小） |
| ② | memo 存 **(值, 后缀链)**，双链（迭代顶 / 调用入口） | 只存值 → 命中时跳过叶子 → 值最优但调度重建不出（1★ 差 1h）；后缀链还必须有纯延时单元衔接 |
| ③ | `rec` 库存恢复用 `st[j] += c*k` | 写成 `+= k` → 库存污染 → "证完最优"是假证明 |
| ④ | **`INF` 不落 memo** | LB 剪枝产生的 INF 依赖当时的 `best["val"]`（绝对口径），不是状态纯函数 → 会把后续更紧 UB 的访问挡住 |
| ⑤ | 分支时旧在炉作业必须带入（`nr = list(running_rel)`） | 曾误写成 `nr=[]` → makespan 系统性低估 → 骨架 start 过早 → 喂料未到 |

### 4.4 `vec_prio`：向上 cp，不是骨架的 `cp`

- `inst["cp"]` 是**向下**口径（`dur + 喂料链`）→ 所有叶子卡打平（4★ 全 = 7245）。
- `vec_prio` 必须用**向上** cp（`dur + 消费者链`，由 `_up_cp` 从 mats 反建）→ 回答"有多少活等我"。
- **只影响截断/排序（搜索指路），不影响 DP 正确性**。
- 实测：改向上 cp 后 4★ 引擎级 84.08h → **77.45h**；UB 的 ready 排序**仍用向下 cp**（换向上 cp 会让 2★ 退化）。

### 4.5 与 `cp_sat_plan.solve` 的关系

同契约（`mk, sched, stats`），可互换。差别：

| | sched_dp | cp_sat_plan |
|---|---|---|
| 性质 | DFS/memo/LDS anytime 启发式 | 作业级 CP-SAT 精确模型 |
| 速度 | 0.05~2s | 100~240s |
| 质量 | 差真最优 0.35~6.1%（交付层，最差在宽而浅的 2★） | 1★ 11s 证 OPTIMAL |
| 能否进 tick 循环 | ✅ | ❌ 只做冷启骨架（`MC_CP_FIRST=1`，默认关） |

`cp_sat_plan` 的时间单位 = 该实例全部 `dur` 的 **gcd**（不是固定 45）；`init_*` 只支持冷启，非冷启返回 `None` 由调用方回退。

---

## 5. `DPTickController` 详解

### 5.1 生命周期状态

| 属性 | 何时失效 | 说明 |
|---|---|---|
| `_skel`（类型级骨架） | `active` 集合或 `box_cap` 变 | `_skeleton()` 构造：非底卡=精炼类型，底卡=买卡类型（`mclass=1, dur=LAG`） |
| `_memo`（求解器 memo） | 与 `_skel` 同步清空 | 骨架变了，状态键空间就变了 |
| `_plan`（骨架计划） | 同上 + 承诺点重解时替换 | 跨 tick 复用的核心 |

### 5.2 `_skeleton`：为什么底卡也是"类型"

`mclass` 三分类（`dp_controller.py:175`）：

```
底卡          → mclass=(1,)     dur=45s   mats=()       ← 买卡是状态里的一等公民
40 面合成卡   → mclass=(0,2)    dur=真实   mats=(…)      ← 主炉或偷炉，DP 逐作业选
其余合成卡    → mclass=(0,)     dur=真实   mats=(…)
```

后果：骨架是 **4 炉**（3 主 + 1 偷）调度，不是 3 炉。`steal_enabled=False` 时全部退化为 `(0,)`。

### 5.3 `_union_need0`：`sets=1` 不是优化，是保命

```python
sets = 1     # 不是 max_rounds - completed[t]
```

- 排满多轮 → 两轮中间卡叠加占满箱 → collect 永不触发 → `rounds_sig` 不变 → **永不重解** → 永久饿死（实测 `done=0/4`、箱满 60/60、槽占 22%）。
- 改成每解只排下一轮：第一轮完成 → `completed` 变 → 触发重解下一轮。

### 5.4 `_solve_plan`：实例构造的四个细节

1. **`live` 过滤**：只留 `need0>0 or buy0>0 or present>0` 的类型。`present>0` 的是"已持有但仍充当别人素材"的类型（`need0=0`，惰性，只供货）。
2. **`init_stock` 只用箱内**，在炉走 `init_running`（`rem = endtime - now`，`cls = 2 if steal else 0`）。
3. **`box_cap = change_cur + store_cur`**。若 `sched` 的投影峰值超容 → `inst["box_cap"]=None` **用全新 memo**（`memo=None`）重解一次，预算砍到 `min(0.5s, _tb)`，标 `box_relaxed=True`。
   > 必须新 memo：约束下的 INF/有限值会毒化无约束重解。
4. **分层预算**：

```python
_tb = time_budget_cold if solve_count < warm_solves else time_budget
# 默认 cold=2.0s（前 8 次）、steady=0.1s（之后）
# 生产 auto_refine 钉死 time_budget=time_budget_cold=2.0, warm_solves=1e9（全程 2s）
```

理由：单次解算质量**与预算几乎无关**（2★/3★/5★ 50ms 与 2s 结果完全相同），但引擎级对"同值不同解"敏感（解的形状 → 头动作序列 → 触发链）。4★ 全程重解 119 次，2s×119 占墙钟 99.6%，降到 0.1s 后 makespan **一字不差**，墙钟 237s→28s。

### 5.5 `_run_plan`：两趟 + 腾箱

```
_box_compaction   箱紧时：① 卖多余 10 面底卡（保留 due）② 挪多余合成卡进保险箱
        ↓
pass 1  买卡：扫描**全部** buy op、按 DP 顺序发（不等 start 时刻）
        闸门：箱预算 box_free + collect_freed + freed - album_bought - reserve
             速率闸 len(furnaces)*3+6
             去重 plan["bought"] >= plan["planned_buy"]
             ★ JIT 门：喂料底卡只买到 due（可炼前沿需求，窗口 = 2×(主炉+偷炉)+2）
        ↓
pass 2  精炼：从 cursor 起扫描
             到期门 start <= elapsed
             头动作允许提前发（early_ok：喂料在箱 + 有空炉）
             阻塞保护 protected：被卡动作的在箱喂料不许被后续消费
             窗口扫描 scan_window=6
```

**`reserve = soon + 2`**，`soon` = 即将收卡的炉数（`0 < endtime-now <= 45s`）。
> 旧版 `reserve = len(furnaces) + 2` 把所有在炉卡的未来落位全预留 → 4 炉全忙长任务时 `reserve=6` 吃光 60 格箱的 `box_free` → 买卡全停 → cursor 冻结饿死。落箱安全性不受影响：精炼净腾 2 格，只需保证 `endtime` 那一 tick 有空位。

**JIT 门为什么必须有**（`_jit_due`）：无门时 pass 1 按箱预算把整套 buy op 一次性买齐 → 紧箱 60 格被囤到 60/60 → 买卡闸被吃光 → 头 op（buy）永发不出 → cursor 冻结。`due` = 可炼前沿（`_jit_due`）的底卡喂料毛需求；纯收藏底卡（不喂任何合成卡）**不设门**，由专辑预买/计划终局 op 拉起。

---

## 6. 不变量与红线（改代码前必读）

按严重度排序。"红线"意思是：违反不一定报错，但会静默产生错误的调度结果。

| # | 不变量 | 违反后果 | 位置 |
|---|---|---|---|
| 1 | **底卡即买即用**，不许按整套需求预买 | 箱被囤满 → 买闸吃光 → 死锁（done=0） | `_jit_due` / `planner.py:97` |
| 2 | `init_stock` 只含箱内，在炉走 `init_running` | 同一张卡算两次 → makespan 低估 | `_solve_plan` |
| 3 | relax 重解必须 `memo=None` | 约束下的 INF 毒化无约束解 | `_solve_plan:560` |
| 4 | `dfs` 返回值必须含 `t_rel`；memo 必须存后缀链 | 幻影最优 / 调度重建不出 | `sched_dp.py:767,1010` |
| 5 | `INF` 不落 memo | 幻影 INF 挡住最优 | `sched_dp.py:999` |
| 6 | 分支时旧在炉作业必须带入 | makespan 系统性低估 | `sched_dp.py:965` |
| 7 | 骨架 `start` 向上取齐 tick | 系统性迟到 0~45s → 等料空转 | `_solve_plan:584` |
| 8 | **只卖 10 面底卡**，高面值卡永不自动卖 | 用户资金损失（`sell_card` 只换回面值） | `_box_compaction` |
| 9 | **落箱**要给在炉卡留落位（`reserve`） | 落箱失败**静默丢卡** | `_run_plan:664` |
| 10 | `solve` 的 `time_budget` 守卫必须 `perf_counter()` | `time.time()` 在本环境被 mock → 预算失效搜满 800 万节点 | `sched_dp.py:787` |
| 11 | 改 `sched_dp` 正确性后必须跑 `_verify_sched` | DP 值可能 ≠ 模拟 makespan | 见 §7 |

业务红线（不在本引擎代码里，但会影响它）：

- 主题模块分类：挖地 / 西游记 / 三国战役**不可变卡**。
- 珍藏阁只投 10 面卡（307/304/280 面是素材，绝不可投），绝不调加速接口。
- 偷炉只接 40 面卡。

---

## 7. 怎么跑、怎么验

### 7.1 端到端仿真

```bash
PY=/Users/stalitz/.workbuddy/binaries/python/envs/default/bin/python

# DP vs 贪心，全量 8 例（无参数，跑 bench_dp_greedy.CASES 全部）
$PY -m backend.engine_v2.tests.bench_dp_greedy

# 聚焦版（场景相同、断言更少，适合改动后快速回归）
$PY -m backend.engine_v2.tests.bench_focused

# 生产口径仿真（复用 auto_refine 逻辑，--dp on|off）
$PY -m backend._sim_faithful --tid 59 --sets 0 --rounds 1 --dp on
```

> `tests/sim_report.py` 是**库**（提供 `run_one`），没有 `__main__`，不能直接执行。

`Engine.run_sim` 的 tick 循环（`engine.py:100`）就是"真机循环的离线版"：

```
now += 45 → 收到时炉 → parse_full → WorldState → active 过滤 → decide → execute → tally
```

### 7.2 求解器正确性验证（**改了 `sched_dp` 就必须跑**）

```bash
$PY -m backend.engine_v2.tests._verify_sched
```

它用**独立模拟器**复算 `solve()` 返回的 `sched`，断言「DP 值 == 模拟 makespan」且可行（素材不超支、容量不超、产量达标）。每星跑两档（截断 `mv=96` / 全量 `mv=∞`），5 星 × 2 = 最坏约 5 分钟（每档 30s 预算）。

> 这是唯一能挡住"幻影最优"的闸门。
> 注意：`_verify_sched` 的 instance 是"单套从零、无预买、固定 t0"，与引擎级 instance **不可横比**（见仿真报告 §4b 口径说明）。

### 7.3 与精确最优对照

```bash
# 五全星 CP-SAT 求解（无参数，硬编码 TIDS 1~5★，100~240s/星，总计数分钟）
$PY -m backend.engine_v2.tests.cp_sat_solve
```

双验证：`precise_check`（按实际 mode 的事件扫描，权威容量）+ `simulate`（物料+箱容，是必要条件，对 `[0,2]` 会保守误报）。

> ★ **禁止并发跑 2s 求解**。benchmark 与紧箱同时跑会扭曲 anytime incumbent（实测 2★ 25.02 vs 干净 23.04）。所有"旧 vs 新"对照必须单独串行跑。

### 7.4 探针惯例

`backend/engine_v2/tests/_probe_*.py` 是临时插桩脚本，**默认不提交、不删除**。命名约定 `_probe_<主题>.py`。已有常用：

| 探针 | 用途 |
|---|---|
| `_probe_speed.py` | 单次解算质量 vs 预算（iso） |
| `_probe_fast.py` | 引擎级等预算对照 |
| `_probe_diff_cp.py` | CP 最优轨迹 vs 引擎轨迹差分（钩 `Executor.execute`） |
| `_probe_deadlock.py` | 逐 tick 死锁定位（26000 tick） |
| `_probe_solver_iso.py` | 同一实例新旧代码隔离对照 |
| `_tightbox_dp.py` | 紧箱三用例验收 |

---

## 8. 参数与环境变量

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `MC_SOLVE_BUDGET` | `0.1` | 稳态单次解算预算（秒） |
| `MC_SOLVE_BUDGET_FIRST` | `2.0` | 冷启预算 |
| `MC_WARM_SOLVES` | `8` | 前 N 次解算用冷启预算 |
| `MC_MAXVECS` | `96` | 向量截断上限（**调大反而更差**：4★ mv≥384 退到 UB 83.35h） |
| `MC_SOLVE_EVERY` | 关 | 每 tick 都解（**有害**，仅实验） |
| `MC_STREAK` | `4` | 头动作连续被卡上限 |
| `MC_SCAN` | `6` | 精炼窗口扫描深度 |
| `MC_CP_FIRST` | 关 | 冷启用 CP-SAT 出首个骨架 |
| `MC_TB` / `MC_TB_FIRST` / `MC_TB_SCOPE` | 关 | CP 顺序 tie-break（**引擎级负收益**，仅实验） |

生产（`auto_refine._dp_refine_all`）**显式钉死** `time_budget=time_budget_cold=2.0, warm_solves=1e9`——不跟随仿真的快速分层。原因：生产的 0.1s 快路径在"重解稀疏"的实例上会让 makespan 差 1~2h（2★ 23.10→25.07h）。

---

## 9. 已知边界与已证伪路线（别重走）

### 9.1 已知边界

1. **交付层 gap 4.7~6.1%**（2★~4★）。残余 gap 定位到"对称关键分支"（2★ `241←{240,239,233}` 向上 cp 全相等），静态优先级区分不了。
2. **全状态 DP 不可行**：4★ 联合状态 ~10²⁶。要缩小 gap 只能走 robust CP-SAT（给 slack 出可执行界）或让重解更准更快。
3. **多套纯高星**（4+3 / 4+5 各 1 轮）DP 略慢贪心 1.5~2h。这是静态骨架 + 滚动重规划的已知 gap。
4. **bench 后 2 例（各 3 轮压力）未跑完**——曾被主动 kill 以聚焦紧箱修复，属 follow-up。

### 9.2 已证伪（**别再试**）

| 路线 | 结论 |
|---|---|
| 用更好的静态序（CP 精确骨架 / CP 顺序 tie-break / 向上 cp 进 UB / 40 面优先偷炉）提升引擎 | **全部负收益或零收益**。四次独立复现：静态序越好 → 头动作越少被卡 → 重解 5→2 次 → 执行层自适应纠错被浪费 |
| 每 tick 全量重解 | 4★ 5767 次 → 82.72h（+5.27h），破坏 plan 连续性 |
| 加大单次解算预算 | 质量与预算无关（50ms == 2s） |
| 加大 `max_vecs` | 枚举吃光预算、DFS 沉不下去，4★ 更差 |
| 提高炉利用率 | CP 最优解刻意空转（68.1% vs 引擎 76.5%），**利用率不是目标** |
| 多套共享段用「套内 DP 序」/「cp 关键路序」 | 2 套 +8h / +18h，3 套 +27h。多套的正解是跨套分配策略，不是套内排序 |
| 联合 DP 静态序接滚动 | 2 套 411.5→433.6h，3 套 459.9→500.2h |

---

## 10. 常见改动，从哪下刀

| 想做的事 | 改哪里 | 连带必须检查 |
|---|---|---|
| 支持新主题 / 任意 tid | **不用改代码**：`ThemeModel(tid)` 从 `data/card_lib.json` 读合成树 | 变卡目标要排除挖地/西游/三国（业务约束不在本引擎） |
| 提升调度质量 | `sched_dp` 的 `vec_prio` / `max_vecs` / LB 剪枝 / `_ub_schedule` 排序 | 改完**必跑** `_verify_sched`；**禁止并发跑对照** |
| 调 JIT 松紧 | `_jit_due` 的 `window`（`due_window = 2*(machines+steal)+2`）+ collect 缺口项 | 窗口太小 → 频繁等料空转；太大 → 囤货死锁 |
| 调买卡闸 | `_run_plan` pass1 的 `box_free_local` / `buy_cap` / `reserve` | 违反 §6 #1 #9 → 死锁或静默丢卡 |
| 加新 `Action` 类型 | `plan.py` + `engine_v2/executor.py` + `auto_refine._dp_refine_all` | **三处必须同步**，否则仿真与生产行为不一致 |
| 改箱容口径 | `WorldState.box_free` / `_solve_plan` 的 `box_cap` | 双箱各自有硬顶，`box_cap` 用合计是近似；换卡箱只有 18 格 |
| 加新资源约束（新炉型等） | `_skeleton` 的 `mclass` + `inst` 的 `shop_cap`/`steal_cap` + `cp_sat_plan` 的 `AddCumulative` | 求解器与 CP 模型必须同改，否则 gap 对照失效 |
| 换求解后端 | 保持契约 `mk, sched, stats = solve(inst, …)` | 参考 `cp_sat_plan.py` 头部契约表 |
| 改前端展示 | `frontend/app.js` 读 `st.dp`（`resolves` / `solve_ms` / `skip` / `streak` / `discrepancy`） | 后端字段在 `auto_refine.py:2255` 组装；改 `app.js` 必 bump `index.html` 的 `?v=` |

---

## 11. 术语表

| 术语 | 含义 |
|---|---|
| tick | 45 秒决策周期；`LAG=45s` 是买卡物流延迟 |
| 骨架 (skeleton) | 一次解算产出的带 `start` 的 op 表，跨 tick 复用 |
| 承诺点 | 允许重解骨架的五种条件（§2.2） |
| 有限偏差 | 允许执行偏离骨架的三种微调：应急买、提前发、窗口扫描；累计超限则判骨架过时 |
| streak | 头动作连续发不出去的 tick 数 |
| due | 可炼前沿的底卡需求（JIT 门的阈值） |
| reserve | 给"即将落箱"的在炉卡预留的箱位 |
| 向上 cp / 向下 cp | `dur+消费者链` / `dur+喂料链`。前者给搜索指路，后者给 UB 排序 |
| 极大向量 (maximal vector) | 一拍内"再开任何一张都放不下"的开工组合 |
| anytime | 任何时刻中断都能返回当前最优完整解 |
| 箱容 relax | DP 搜不到超容解时，去掉 `box_cap` 重解一次拿可行上界 |

---

## 附：一次决策的完整时序（把上面所有东西串起来）

```
t=45k 秒
 ├─ auto_refine.run() 拉到 st = parse_full(...)
 ├─ WorldState.from_parsed(st, now)                 ← 换卡箱/保险箱/炉位/槽位快照
 ├─ _dp_refine_all:
 │   ├─ 队列变了？ → 重建 DPTickController（memo 作废）
 │   ├─ ws.steal_free = 0 if 未勾偷炉
 │   └─ actions, stats = ctrl.decide(ws, queue, completed, max_rounds)
 │        ├─ 骨架签名变？ → _skeleton + 清 memo + 弃 plan
 │        ├─ _union_need0 → need0_map / buy0_map（每套 1 轮）
 │        ├─ collect 在即 → Action(collect)（生产链路跳过，见易错点丙）
 │        ├─ _album_prebuy → Action(buy) × 收藏底卡
 │        ├─ 承诺点？
 │        │    ├─ 是 → _solve_plan:
 │        │    │      live 过滤 → inst(box_cap, mclass, init_stock, init_running)
 │        │    │      → solve(2s 或 0.1s) → 超容则 relax 重解
 │        │    │      → ops（start 取齐 tick，buy 优先）
 │        │    └─ 否 → 复用 _plan
 │        ├─ 预买去重（plan["bought"] 累加）
 │        └─ _run_plan:
 │              _box_compaction（卖 10 面 / 挪保险箱）
 │              pass1 买卡（due 门 + 箱闸 + 速率闸 + 去重）
 │              pass2 精炼（到期门 + 提前发 + 阻塞保护 + 窗口扫描）
 │
 ├─ 执行层（auto_refine 重写版）逐 action：
 │    买卡：逐张过 _free_change，POST，成功则同步本地 stock
 │    炼卡：槽位重校验（slot2cid）→ 主炉/偷炉 payload → 偷炉要挑好友+冷却+拉黑
 │    异常：只记日志，本 tick 不炼，下 tick 重试（绝不卡死生产）
 │
 └─ 下一 tick：回到开头，ws 是新的真实状态
```

**记住这一句就够了**：每 45 秒重新看一眼真实箱子和炉子，如果"承诺点"到了就花 0.1~2 秒重算一份剩余问题的最优骨架，否则沿用旧骨架，然后只执行当下能执行的那几个动作——并且允许在执行时做有限偏差，但如果偏差太多就认为骨架过时、重算。
