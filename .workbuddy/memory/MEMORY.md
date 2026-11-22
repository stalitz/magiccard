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
- ★★ **「0 = 无限」＝换目标，不是换窗口（`b87164c`，2026-11-17）**：`sets` 在模型里是「做完 N 套就停」的**总数**，不是并发限制；目标＝做完 N 套的最短总时间 → 只看最后一张卡，前几套何时交不进目标 → N 大于流水线在制量时「把前几套压到末尾一起交」就是 makespan 最优（窗口 4/5 首套 306/424min）。修法：`MC_DP_SETS_WINDOW=0` → 需求窗口给资源上界（`MC_INF_SETS_CAP` 默认 **3**；cap=4→7套/峰19，**cap=5→15h 零交付**）+ `inst["infinite"]` 让目标变 **ΣCi 最小**（makespan 次级，剪枝放宽 `MC_INF_RELAX`=1.25）。实测 3 seed 一致：**8 套 vs 现役 7 套，峰 14/13 不涨，空炉 152→3**。坑：交付向量必须 `if lex_deliv or INF_SUM` 才 append，否则叶子恒 None → 空表 + 引擎静默零动作；`INF_SUM` 必须定义在 `BOUND_V` 之前。默认仍 `-1`；`-2` = 旧「空闲炉位+1」A/B 对照。
- ★★ **非本套 ≤10 面杂卡 = DP 的「有代价额度」**（`12c2169`）：`_pool_caps` 加 k、`need_sell=箱压峰值−两箱物理空位`、`_run_plan` 兑现 sell（卖完归零）。只加容量不发货=虚增。无限窗口必须过箱位闸 `(两箱可用+在箱在炉本套)//N`，少「在箱在炉」会误钳（仿真 11→10 套）。紧箱无解先看 `diag.leaf/inf_dead/depth_hit`；`depth_hit` 或 RecursionError = dfs 深挖，`20d269f` 已加深度硬闸。
- ★ 旧结论（保留作反例）：把窗口直接改「主炉总数」反而退化（套数 −1、峰 14→24、首套 187→424min）——**空炉 tick 是「交付需 9 张同时在箱 + 换卡箱 18 格」的必然**，不是浪费；真机 102 min/套 已达结构上限 96.6 的 95%，剩余是执行层损耗（取卡延迟实测中位 0.1min，非此因；上交失败代价 ~0.5min/套）。别再拿空炉率当指标。
- ★★ **主炉占满 ⇒ dfs 原地递归（`f077d62`）**：`_vectors` 在 caps[0]=0（炉满）但买卡/偷炉仍有额度时会产出**全零向量**（每张 0 张），`maximal()` 因 `used[ci]+1<=caps[ci]` 恒假而判「极大」，dfs 把它当可行开工 → dt 只靠旧在炉、状态不变 → 原地递归到深度闸（真机 leaf=0 / depth_hit=34 万 / 每 tick 白跑 10s / 零动作）。两处收集点必须 `any(e[1] for e in cur)`。★ 排障钥匙：`MC_DEPTH_DBG=1` 打印 path 尾部——连续同一时刻同一向量就是这条。
- ★★ **「一炉两卡位」是 5 条 Furnace / 4 台炉**：`_busy_stoves` 按炉号去重，但喂给求解器的 init_running 若 5 条全算 class 0，则「在炉数 > 炉台数」→ `idle[0]` 归零且状态自相矛盾。口径：按 stove 分组，同炉按 rem 升序，**最后一个** class 0（占炉到它完工），其余 **class 5 幽灵**（不进 idle、不占机器池、只到点产出）；dfs 的 `_ro`（箱压）要把 5 计入。
- ★★ **变卡进 DP 模型，不是「事后抠 op + 变完重解」（`63be0c2`+`fe02ba3`，2026-11-18 用户定调）**：用户原话「告诉 dp 有多少张礼物卡，dp 就认为变卡 100%；有 2 个空炉就安排变 6 张 40 面、投 2 个 150；只变了 5 张就是这个 tick 后续没做成，**不用重解**，下个 tick dp 自己排炼缺的那张；没空炉不变应该是 dp 解出来的，不是加限制」。落地：`inst["gift"]={n,types}`（缺省→逐位一致，已验）；变卡口径＝**「某条上层 op 缺的 40 面卡当场做出来」**（零耗时/不占炉/箱格原地替换），写成「素材缺口由 gift 补」是错的（need 没减＝既变又炼，双重计算）；实现为 dfs 确定性兑换 `_gift_exchange`：①有空主炉 ②某 op 缺口**全是** 40 面 ③这几张本就是计划要产 → 兑换后当拍开工，故 `stats["gift_ops"]` 里变卡时刻＝消费它的开工时刻。执行层 `plan["gift_ops"]`＋`gift_change_targets(now=)` 只取**到点**那批、`gift_commit()` 划账、**不置 `_dirty`**；去掉旧「空主炉×3」外部收窄。**gift_n>0 必须关掉 `_face40_credit`**（DP 已原生知道礼物卡，再叠虚拟库存＝同一件事算两遍→DP 少排炼次、实际变不出来）。仿真 1652/600tick/50% 失败：ΣCi 1293.7→872.2（−32%），套数不变、峰 11→17(<2N)、err=0。旧口径（`47e7aac`：从 plan 抠 refine 40 面 + 上限空主炉×3 + 变完 `_dirty`）已废。
- ★★ **「3套 96.0→104.96」不是回归，是 PURE 口径**（`6b0ddf7`，2026-11-22 逐提交二分 15 个 solve 版本）：96.0 只出现在 `017bd81`（`best` 初值 = `{"from":"UB"}`，dfs 一条完整路径都没走通就直接交贪心表），`761861c`（PURE 上线）一步 105.92，其后 10 个提交逐位不变，`d9cccda+dd3454f` 拿回 0.96。**96.0 = 贪心 `_ub_schedule` 质量，104.96 = 纯 DP 质量**，此说法不要再二分。该差距 100% 是**偷炉利用率**：40 面工作量 102.2h ≈ 偷炉容量(~100 台h)、主炉专用 269h；dfs 把 40 面前置做完（49h 后偷炉闲置 58.8h，主炉空 2.1 / 偷炉空 60.1 台h），贪心铺到 81h（12.0 / 19.7 台h）→ 108.04 vs 100.76。★ 让「首解变好」的启发式（hint `d9cccda`、GRASP `5478ce7`、`MC_DP_DIVE_STEAL`）**端到端全部更差**（DIVE_STEAL solve −2.7h 但 3套 +0.87h，第 3 次复现）→ 别再往 dive 排序上打补丁，要改就改搜索结构。
- ★ 算利用率必须用「4 台口径」（3 主炉 + 偷炉）并让 40 面优先走偷炉再分配；只数 class0 会算出 114% 假象。
- 禁 god-function，planner/executor 分离；「优化初始序」类补丁无效（四次复现），CP-SAT 只作标尺。生产 `interval=45.0`。
- ★★ **倒排 hint（`MC_BWD_FIRST`）端到端不是一致收益，禁默认开**（2026-11-22 引擎级 A/B，确定性、seed 无关）：954 −6.1%（20.01→18.79h）、神蛇 −0.3%、寿司 **+3.8%**（hint 冷启解更好 48.63 vs 50.31h 却执行更慢、炉占 −1.9pp）、178/197/1652∞=0。同「更好的静态序 → 重解变少 → 少掉执行层自适应修正」。配套：①倒排商店必须按 `shop_cap` **并行**（单机串行会把 hint 值系统性抬高：954 15.08→14.98、蜻蜓x6 6.47→6.27，`483c96d`）；②`MC_DP_BUY_FOLLOW` 已证伪（hint 形状可达后 DFS 走出来的仍更慢，被 BOUND 剪光）。
- 方案 A（`cdf4753`）：pass 2a `MC_MERGE_READY` 只收 feeders 全非底卡的 refine，绕 `start` 门、按 `cp` 最高档、每 tick ≤1；底层 40 面 op 绝不进。仍有退化档。
- ★ 字典序目标（`MC_LEX_PEAK`）：dfs/memo 扩成 `(val, suffix, peak)`，达下界后 `MC_LEX_EQ_VEC`(2) 有限枚举。**别禁用「达下界即 break」**。生产 `MC_BUY_FREE=1`/`MC_BUY_KEEPOP=0`；JIT 重排已证伪。
- ★★ 首套 ~185min 是**硬下界**，不是 DP 编排选择（`37b65d8`）：目标里加交付时刻字典序（`MC_LEX_DELIV`，默认关）后 A 组逐位不变（7套/首套186.8/峰14），把窗口压到 1 套只快 1.6min（185.2）。窗口改满（B）首套 424→364.5、峰 24→20 但 6 套 < 7 套，**已否决**。剩余 ~5% 差距在执行层（收卡等 tick / 上交失败重试）与换卡箱 17 格紧张，别再往需求窗口/首套上找。
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
