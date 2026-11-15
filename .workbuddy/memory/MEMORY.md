# 魔法卡片助手 · 长期记忆（铁律 + 指针）

全文归档 `REFERENCE.md`（**附录 A** = 多账号隔离 / 活动任务 / 已结案（别再查）全文；**附录 B** = 箱容口径 / 基准数字；均仍有效）；流水 `YYYY-MM-DD.md`；细节见 `docs/`、`backend/*/tests`、`git log`。本文件只留仍生效的铁律。

## 方法论
- 现象 > 推测；禁单点采样下全局结论；写操作先回查定调原文（`git log -S`）。**高面值卡永不自动卖**，只卖 ≤10 面底卡。提问 ≠ 授权修改。
- Bash `grep "a\|b"` 交替**静默失效** → 用 Grep tool 或 `grep -e`。真机只读用 venv `~/.workbuddy/binaries/python/envs/mc/bin/python3`，会话 `/tmp/mc_sessions/<token>.json`；只读排查先跑 `tools/live_contract_check.py`；httpx 探针加 `Connection: close` + `trust_env=False`。
- ★★ **单实例 makespan 不是跨轮吞吐的好代理**：交付「更短 makespan + 更高箱峰」解后，重规划下箱压变大 → 实际更慢（2套 peak 51→61 时 +0.4h、3套 47→52 时 +7.65h）。判候选必须 `sim_h` 与 `peak_box` 同看。
- ★★ **「求解器退化」先查实现再归因规模**（2026-11-15 两次打脸）：dfs 到不了叶子时，先打印 `stats["diag"]`（`leaf/lb_prune/inf_pool/inf_box/inf_dead`）+ `cProfile`，再谈 NP-hard。两次真凶都是实现：①宽节点枚举占满预算；②剪枝上界不可达。

## 真机硬约束
- 箱：changebox 18 硬顶 / storebox `cur=168` 是**已开通容量**（`max=200` 非当前）/ 简易箱 15。炼成卡优先进保险箱、满则溢到换卡箱；**只有买的 10 面底卡一定进换卡箱** → 空位一律两箱合计 `_free2`。跨箱只支持 换→保 与 保↔简。
- 箱位闸：两箱空位 < 本套缺张数 → `_calc_sets` 返 0（禁 `max(1,...)`）；同口径拦「收尾买底卡」与 `_make_room` 卖底卡。
- 炉台数每 tick 取 `<stovebox stovenum>`（**禁写死 3**）；`cur=7` = 5 主卡位 + 1 偷炉 + 1 VIP；加炉道具把排队位升炉位。`_main_cap` 按 `Furnace.stove` 去重判忙；到期无时间戳（`tem_slot_end_time` 是简易箱），只看 stovenum 变小。
- 取卡「空 2 格」是准入门槛非每张占 2 格，逐张重判；被拒炉不消失。时长=表值×0.6232，在炉走 `btime+locktime`。上交瞬间必须整套各 1 张同在箱。
- ★ 手动变卡 `POST /api/gift/change {srcid,dstid}`（`change_any` 定位 slotid/slottype）＝**原地替换**：目标落同箱同 slot，空位不变。源卡任意（面额 ≥ 目标），**不只 48 面礼物卡**；成功 `code=0 且 raw 带 result="1"`，约 50% 成功率、失败也烧源卡。⚠️ `/api/gift/change_target` 报 `name '_auto' is not defined`（未修）。
- 杂卡锁死换卡箱：`_box_compaction` 四段（卖多余本主题 10 面底卡 / 挪本套合成卡 / **优先直卖非本主题 ≤10 面杂卡** / 卖完还差才把 >10 面杂卡无损挪保险箱，满则停手报告）。腾够买 3 张即停。
- ★★ **买来即消耗 / 无限炼不该空炉（`4347fdc`）**：「上交之前一张 10 面底卡也不该买；最优解是收最后一张顶层卡那个 tick 才买齐」。**喂料底卡**必须买、前提**同 tick 投炉净占 0 格**；**收藏底卡**严格 `_album_prebuy`。★ `002b03e`「本 tick 买过卡一律不投炉」已证伪撤销。**禁止把零动作说成「正常等出炉」**。买卡后按 `slots_dirty` 重读槽位**即时投炉**。
- ★ 本主题冗余底卡也要清（`_redundant_base_in_change(keep=1)`）：只处理「在炼套 + 底卡 + 0<面≤10」，`keep=1` 留上交必需那张，**一次清到 keep 不受 want 限制**。
- ★ 10 面杂卡（`4c81dcd`）：卖=面额等额金币、买回 1:1 无损 → **直接卖，禁止搬进保险箱**。保险箱卡不能直接卖，清库走 `box/move`+`box/sell`，逐张重读 mainpage 校验。预腾位 `d3be32b`：change_free 3~5 是无人区 → 自锁；`decide` 前算 `_reachable_free`，够 6 就本 tick 只发腾位。
- 一键日常（`backend/zcg.py`）：抽卡后、商店买之前先 `_deposit_all_from_box()` 消耗**箱内现成** 10 面卡；读箱失败显式回报禁当空箱。`_read_mainpage()` 0.35s 节流 + 退避 `(1.5,4.0,8.0)`；`_slot_val()` 修 `int(slot or -1)` 把首格 `slot=0` 误判。
- 卡友 `card_user_theme_list(tid)` 每页 100、uin 需 `html.unescape`；分页环形、部分主题封顶、深页全局去重 0 新增才判翻完。好友 ≈194 人含本人 → 偷炉按**数值**判自己。
- ★ **`code=-3001` = `card_user_mainpage` 读限流（~15 次/10s、冷却 ~15s）**，不是登录失效。只打 mainpage 读；zcg `act=1/2` 与写请求不受影响 → **禁止一键日常与自动换卡互斥**。

## 调度引擎（在线 RCPSP）
- 禁 god-function，planner/executor 分离；「优化初始序」类补丁无效（四次复现），CP-SAT 只作标尺。生产 `interval=45.0`。
- 方案 A（`cdf4753`）：pass 2a `MC_MERGE_READY` 只收 feeders 全非底卡的 refine，绕 `start` 门、按 `cp` 最高档、每 tick ≤1；底层 40 面 op 绝不进。仍有退化档。
- ★ 字典序目标（`MC_LEX_PEAK`）：dfs/memo 扩成 `(val, suffix, peak)`，达下界后 `MC_LEX_EQ_VEC`(2) 有限枚举。**别禁用「达下界即 break」**。生产 `MC_BUY_FREE=1`/`MC_BUY_KEEPOP=0`；JIT 重排已证伪。
- ★★ 验收线：跨轮重叠吃箱位可接受（**峰值 < 2N**）；**绝不能单轮超炼**。`_calc_sets` 自动推断套数硬上限 2；`_union_need0` 的 `pipe_sets` 提套处加 2N 闸（**必须在 sets_fn 之后**），只数箱内不算 pending。
- ★集齐 > 取卡：`_collect_tight_ex` 三层（空位 0 延后 / 缺口全是可买底卡且空位≤缺口 → 延后 / 含合成卡不延后），60 次逃逸阀。买卡空位只认 `change_free`，决策前 `_read_state()` 重读；`abs_slots==0 → base_quota=0`；`candidates()` 跳过 5 分钟内 `_recent_buys`。
- 偷炉：`_steal_cap` 运行时可变（变容量作废骨架+memo 重解）；骨架 40 面 mclass 与 `inst.steal_cap` 一起切；降级最短保持 90s 防抖。失败码 `-33031` 全局 / `-33003` 好友炉满 / `-33084` 挂着我的卡；指定好友只重试不换人（60s 冷却），未指定探测上限 4 且 -1 立即停手。
- ★★ **纯 DP（`MC_DP_PURE`，默认 1）正确姿势＝dive 起手 + B&B**：①PURE 若直接用 UB 当**硬**剪枝上界，大实例（73 类型/547 op）深层下界必然超 UB → 每条路径被剪断，**结构上到不了叶子**（实测 30s/30 万节点 `leaf=0`）；②解法是先跑「无剪枝、跳过下界、每层只走首选向量」的**单路径下潜 dive** 拿 dfs 自己的完整解当 incumbent（547 层 ~0.1s），再用它当上界做 B&B；③宽度必须按活跃类型数自适应（`max_vecs≈1200/k`，k=73→16、k=10→96），固定 96 会把预算全花在宽节点枚举；④`_vectors` 三类满槽快路径 + `maximal` 增量 cnt + `_cum_lb` 前缀和是让 3 套能搜动的必要优化；⑤**retry 只给冷启**（`time_budget≥1.0s`），warm 0.3s 绝不 retry（否则每 tick 卡 8s），warm 预算 0.1→0.3s（0.1s 只够 dive）。诊断先看 `stats["diag"]`（`leaf/lb_prune/inf_pool/inf_box/inf_dead`）。

## 箱容口径 / 基准
细节见 `REFERENCE.md` 附录 B。两条最常踩：①仿真必须同时设 `MC_E_FILL` 与 `MC_E_SFILL`（否则「保险箱空 200」是假象）；②`cap_mode="A"` 不传 `change_cap` → 腾箱额度恒 0，只有 `E4/E5/EP4/EP5` 是真分箱。换卡箱硬门槛 `ch_cap≥6`。

## 验收 / 运维
- 改 backend 必须 `./server.sh stop && start` 真重载；改完必须 `git commit`（分批）；scratch 不提交不删除。重启后自动炼卡/换卡为 stopped，需界面重新点开始。
- 总览附加状态：`GET /api/overview/extras`；**别复用 `/api/home/state`**（7 次 travel），也别打 mainpage。`#ov-extras` 切号进 `resetAccountScopedCache()`。
- 前端改 `app.js` 必同时 bump `index.html` 的 `?v=` **和** `APP_VER`；`Cache-Control: no-store`。按钮即时响应、文案禁代码标识。通用下拉写 `select.sel`；套卡首行名称 `flex:0 0 auto`。
- 勿杀未知服务：`:8000` 本项目 / `:8777` agent-chat / `:9222` headless Chrome。OCI 新开端口两步：firewall-cmd + 云控制台 Security List。SSH 140.83.54.40:22 opc，密钥 `~/Downloads/ssh-key-2021-12-04.key`。
- ★ 云端（`/home/opc/mc`，**无 .git**）：venv 3.12.16，`start.sh` 是 `while true` 守护（别杀父进程）。同步 `git archive … | ssh … 'tar -xf - -C /home/opc/mc'`，**排除 `server.sh`** 与 `travel_*friends.json`/`prod_sim_*.json`，同步前 `cp -a` 备份。只 kill **8001 的 uvicorn 子进程**。云端 8000 是 `/www/daily-fresh-master`，别碰。
- 生产不勾 DP = 旧引擎（只作 A/B，不再修）；多套/多星一律勾 DP。**默认本地 8000**。局域网 host 由 `MC_HOST` 控制，默认 `0.0.0.0`；`/api/login/sessions` 可沿用会话（⚠️ 无鉴权返回 token）。

## 旅行（猫咪）
- `day_limit_cnt=12` = 4 猫各 3 次 = 3 轮/天；硬限制是没食物不能出发。默认 **C 级 + 每猫 1 个**（日耗 1080 三叶草）；4 猫必须各喂不同食物。收获（act=23）前后比 act=3 食物清单可定性是否掉食物（已埋点）。

## 收藏底卡 release（`a9b4bc1`）
- ★ 用户定调「上交之前一张底卡都不该买；求不出是条件没给全，不是加闸」。修法 = `_collect_ready(mclass, need, running_types)`：**非底卡 need 全 0 且无非底卡在炉**，DP 状态派生量，UB/dfs 同判；未就绪 dem=0、dfs 推进到下一完工重判不判死。`inst["collect_gate"]=False` 复现旧行为。
- ★ 判「囤」要看**收藏买卡时刻 vs 整套非底卡落箱时刻**；复算必须带真实 `init_running/init_stock`。★ 滚动重规划 UB 的 makespan 只按 `out` 算、漏 init_running 剩余 → 真机档 3593s vs 真实 12150s；修后 UB=真实齐备时刻，冷启两版逐位一致。

## 未闭环
1. 金币/魔力余额与退避、翻牌产出、每日上限未建模；收礼 `freegift_id` 83/86/7 未实证；后端不解析 `<elf>`，无喂食端点。
2. 贪心 `_make_room` 死角仍在；「旅行是否掉食物」待真机收获确认。
3. 活动写动作（领奖/完成任务/转盘/兑换）参数未抓包；中文名映射未建。
4. ★ 扰动 A/B 前提：`external_tick` 随机流必须**按 tick 派生**，仅重置 `rng_evt` 不够 → `_bench_perturb.PairedVS` 把 `rng_evt`/`_rand_evt`/`_rand` 全按 (seed,tick,key,第k次) 派生，实测 20/20 对 `judge_hash` 相同。箱容扰动只增不减。
5. ★ 扰动判定口径：`errors` 全是 `buy_net_fail` 注入的买卡失败，**绝对数 ∝ 买卡次数**，须按 (买卡+炼卡) 归一化；「少交 1 套」要换 seed + 延长窗口复核，方向随 seed 翻转即噪声。SAFE 档因 gift=0 其实是**无扰动基线**。
6. ★ 空转归因：无扰动下「有空主炉+零动作」只占 **0.4~3.3%**，其中 **80~85% 是关键路等待 + 树依赖**。**DP makespan 相对 CP-SAT 标尺慢 9~36%**（标尺口径不同，只作参照）。
7. 紧箱无解停手报告已上线 `23dbeb7`（`MC_DP_DEADLOCK_REPORT=0` 复现旧行为；`dp.reason/deadlock` 已透传 `/api/auto/status`，**前端未显示**）：死锁档 92 次空解→0、30 条报告。`dp.reason=physical_deadlock` = 真箱位死锁，需手动清卡箱/扩容。
