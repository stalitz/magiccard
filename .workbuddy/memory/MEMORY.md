# 魔法卡片助手 · 长期记忆（铁律 + 指针）

全文归档：`.workbuddy/memory/REFERENCE.md`（含 2026-11-09 及更早各版 MEMORY.md 逐字原文）；流水 `.workbuddy/memory/YYYY-MM-DD.md`；其余细节 `docs/`、`tests/`、`git log`。本文件只留仍生效的铁律。

## 方法论
- 现象 > 推测，禁单点采样下全局结论；写操作先回查定调原文（`git log -S`）。**高面值卡永不自动卖**，只卖 ≤10 面底卡。
- 本机 Bash `grep "a\|b"` alternation 静默失效 → 用 Grep tool 或 `grep -e`。只读排查先跑 `tools/live_contract_check.py`；httpx 探针加 `headers={"Connection":"close"}` + `trust_env=False`。基准「远超 tick 量级」的差异直接打逐 tick 时间线。

## 多账号隔离（最高优先级）
- 架构：`SESSIONS[token→LoginSession]` + `SessionProxy`(contextvar)；token 存 `sessionStorage` 经 `X-MC-Token` 传（按标签页隔离），cookie 兜底；落盘 `SESSIONS_DIR/<token>.json`，无 token 走 `default`。
- ★三类状态一律按账号分：①后端内存缓存按 token/uin 分桶（`main._acct_bucket`，invalidate 只清当前号）；②落盘日计数/限次一律 `acct.acct_path(p, session)`（`x.json`→`x.<uin>.json`，`acct_key` **uin 优先**），函数加 `session=None` 逐点传参；③前端页面级单例进 `showDash()` 的 `resetAccountScopedCache()` 全量失效清单（2026-11-09 已落地，见下）；localStorage key 必须加 token 前缀（**localStorage 整浏览器共享，只有 sessionStorage 按标签页**）。
- ★★ **前端隔离统一模式（2026-11-09 `20261109b`）**：`showDash()` 顺序固定为 `stopAllAccountLoops()`（清 8 个定时器 + running 标志）→ `resetAccountScopedCache()`（~40 个账号态 + 界面日志 + `_acctLoaded` + `ACCT_GEN++`）→ `logHistory()/mlogHistory()` 重拉 → 各页 `initXXXOnce()` 由 `acctLoaded(name)` 按 token 判定「数据重拉但事件只绑一次」。全局 fetch 包装**响应回来时**比对 token，不一致换 `ACCT_STALE` 空 JSON（关键回填点再短路 `__stale`），零调用点改动防慢响应覆盖。新增任何 `let xxxCache/xxxDone` 必须进这个清单或按 token 分桶。
- 已修：`AUTOS`/`_AUTO_EX`/`_ENGINES`/`_autos`、日志 `_UI_LOGS`/`_MATCH_LOGS`/`LOG_UIN`、`_travel_aux_cache`、`dig_auto._auto`、`_inv_cache`/`_mini_cache`/`_collection_cache`、`_home_all_running`；送礼日计数、工会限次、比赛主题/出战记录、猫家/工会/三国历史、挖地 `_unknown`；前端偷炉好友池（`autoInitDone`/`stealFriendsLoaded`→按 token 重拉 + `stealFriendsPending` 并发去重 + 响应回来再校验 token）、常去好友QQ。
- 故意共享：全局限流器（`_last_req_ts`，护 IP）、`exchange_peers.json`（用户输入）、静态卡数据（`priceChipsLoaded`/`allPrices`/`cardCache`/`spTable` 等官方配置表/`gift._name_cache`/`_MODE_PROP_FETCH`）。⚠️ **`_propsCache` 不是静态数据**（`/api/props` 账号道具库存，2026-11-09 复核纠正），必须按账号失效。
- 日志：`LOG_UIN` → `global.log` + `<uin>.log`；界面日志 `<token>_op.log` 是另一套；「跑一轮」SSE 不落引擎日志，只有 `_log()` 落盘。
- 坑：`/api/me` 探针判 -1001 会 `clear()` 删落盘文件（重扫即可）；default 无 token 可能与真 token 同 QQ 号 → 双引擎抢炉。

## 真机硬约束（API 事实）
- 奖励：`theme_card_list` **t[6]=金币、t[7]=经验**（时尚女套装 tid=45 交实测 100/200）；`card_collection_add` 空体。规律：老套两值不同，3★ 以上多相等，顶格 19999。
- 箱：changebox 18 硬顶 / storebox `cur=168` 是**已开通容量**（`max=200` 非当前）、`nor` 普通卡数 / 简易箱 15 格。★**炼成卡优先进保险箱、满则溢到换卡箱；只有买的 10 面底卡一定进换卡箱** → 空位一律两箱合计 `_free2`。跨箱只支持 换→保 与 保↔简，换卡箱不能直接进简易箱。
- 箱位闸：两箱空位 < 本套缺张数 → `_calc_sets` 返 0（`net_plan(sets<=0)` 返空，禁 `max(1,...)`）；同口径同时拦「收尾买底卡」与 `_make_room` 卖底卡。
- 炉台数每 tick 取 `<stovebox stovenum>`（**禁写死 3**）；`cur=7` = 5 主卡位 + 1 偷炉 + 1 VIP(未开不列)；加炉道具是把排队位升炉位（3 炉=3 炼+2 排 … 5 炉=5 炼+0 排）。`_main_cap` 按 `Furnace.stove` 炉号去重判忙；`_busy_stoves` 只数 `0..machines-1`。到期无时间戳（`tem_slot_end_time` 是简易箱），只看 stovenum 变小。
- 取卡「空 2 格」是**准入门槛**非每张占 2 格，逐张重判；被拒炉不消失。时长=表值×0.6232（未投卡预测），在炉走 `btime+locktime`。上交瞬间必须整套各 1 张同在箱。
- 一键日常珍藏阁（`backend/zcg.py`，2026-11-09 修）：抽卡循环后、商店买之前必须先 `_deposit_all_from_box()` 消耗**箱内现成** 10 面卡（旧版只在抽卡成功后才调，抽卡机会用尽就「有卡却投 0 炉」）；读箱失败（-3001/空壳）必须显式回报，禁止静默当空箱；有卡有空炉却 0 投要给「疑似真机繁忙」提示。回归 `_verify_zcg_box.py`。
- 卡友 `card_user_theme_list(tid)` 每页 100、uin 需 `html.unescape`；分页环形、部分主题封顶（page1==page0）；深页全局去重 0 新增才判翻完。好友列表 ≈194 人含本人 → 偷炉按**数值**判自己。
- 杂卡锁死换卡箱：`_box_compaction` 三段（卖多余 10 面 / 挪本套合成卡 / 箱紧时杂卡按面额降序挪保险箱**只挪不卖**），腾够一次买 3 张即停；保险箱满只能人工清。

- ★ **`code=-3001` = `card_user_mainpage` 读频次限流（~15 次/10s 窗口、冷却 ~15s），不是登录失效/全站繁忙**（`exchange.py:351`、`auto_refine.py:2611`）。限流只打 mainpage **读**：zcg `act=1/2` 与游戏页面写请求不受影响 → 自动换卡并发时用户手动投卡照样成功。★ 用户 2026-11-10 纠正我上一轮的过度归因：**「不是每次都报，只是偶尔报」**，别把「一次 19 次请求」推成「必限流」；**明确禁止一键日常与自动换卡互斥**（并发本就能共存）。离线计数一次 `run_zcg` 发 19 次请求（mainpage 6 / zcg act=1 9 / 抽卡 1 / act=2 3）只说明**提高撞窗口概率**，不是必然。
- ★ **2026-11-10 已修（`backend/zcg.py`）**：统一 `_read_mainpage()` = 0.35s 轻度节流 + `-3001`/空壳/异常有限退避 `(1.5,4.0,8.0)`（4 次尝试、最长 ~14s）；所有读箱点走它；`_box_ten_cards` 失败**抛 `_BoxReadError`**，不再静默当空箱；`_box_state` 带 error 时腾位/抽卡循环不动作（读箱失败≠箱满）；`_do_deposit` 写后有限次延迟确认炉状态、防 `st_p["puzi"]` KeyError；中途读箱失败标 `partial` + 文案「不完整」；所有 `["puzi"]` 改 `.get`。**顺带修真 bug**：`int(slot or -1)` 把首格 `slot=0` 误判成 -1，第 1 张卡永远投不了 → 新增 `_slot_val()`。回归 `_verify_zcg_box.py` **18/18 PASS**（新增 F 偶发限流自愈 / G 重试用尽零写 / H 复读失败不误判 / I 中途失败 partial）。

## 调度引擎（在线 RCPSP）
- 禁 god-function，planner/executor 分离；「优化初始序」类补丁一律无效（四次复现），CP-SAT 只作标尺。生产 `interval=45.0`（决策间隔第一性能变量：K=1 50.85h → K=200 168.76h）。
- 方案 A（`cdf4753`/`9dd2deb`）：执行层 pass 2a `MC_MERGE_READY` 只收 feeders 全非底卡的 refine（3→1 净腾 2 格），绕 `start` 门、按 `cp` 最高档、每 tick ≤ `MC_MERGE_PER_TICK`(1)；底层 40 面 op 绝不进。`MC_MERGE_STACK=2` 门槛已证伪删除（抢主炉炼非关键路径拖慢顶层）。A 仍有退化档（3 炉 85/290、5 炉 60/709）；根因 CP 目标只有 makespan、箱位不进模型。箱位压力闸门已被 `8599f71` 数据否掉（K=3 箱最紧时无条件上溯反而既快又省箱）。
- ★ 字典序目标已落地（`MC_LEX_PEAK`，`sched_cmp/sched_dp.py`）：dfs 值/memo 扩成 `(val, suffix, peak)`，`sched_peak()` 同口径（occ = `sum(stock)+len(running)`，开工净增量 `1-Σmats`、完工不改 occ），分支按 `(makespan, peak)` 择优，达下界后按 `MC_LEX_EQ_VEC`(默认 2) 有限枚举等价解。**别禁用「达下界即 break」**：全量枚举让 890 求解打满 2s → 仿真 +1.87h。★★ **实测限制**：真实承诺点「同 makespan 异构」0 次、`lex_swap` 0 次 —— lazy 买卡确定性不分支、买卡时序不是决策变量，第二级几乎无解可选；890 收益（峰 23→20、15.01→15.00h）来自多枚举改善搜索质量。8 档 A/B 其余 7 档逐位一致、178 基线不动。回归 `_verify_lex_peak.py`。★生产 `MC_BUY_FREE=1`/`MC_BUY_KEEPOP=0` 会在 `_solve_plan` 直接丢掉 buy op（买卡由执行层 `jit_need` 决定），故求解器层买卡时序改动对真机无效；2026-11-09 独立副本实验（JIT 买卡重排 + keepop=1）已证伪：峰只降 0~3 格、工期 −0.65~+3.72h、solve 2~3 倍，**别再走这条**。箱位大头是保险箱堆积的合成卡。
- ★★ **用户 2026-11-09 验收线（已落地）**：跨轮重叠换时间、吃箱位可接受，等价于同时炼 2 套同星卡，**峰值 < 2N 都算可接受**；**绝不能单轮超炼**（`sets` 被 `_calc_sets_raw` 按囤卡 `max(have)` 放大到 5~6 而只凑 1 套）。落地两处：①`_calc_sets` 对**自动推断**套数硬上限 2（`MC_AUTO_MAX_SETS=0` 消融），界面显式 `surplus_sets>0` 不钳（否则砍掉用户手动 5 套）；②`DPTickController._union_need0` 的 `pipe_sets` 提套处加 2N 闸（**必须在 sets_fn 之后**，因为 `pipe_sets` 是返回后才把 1 提到 2，`_calc_sets` 钳不到）：本套箱内已占 + N >= 2N 就不提套（`MC_PIPE_2N=0` 消融），只数箱内不算 pending（投炉已从箱移除）。回归 `_verify_sets_boxcap.py` / `_verify_sets_2n.py`。A/B 基准 6 档（含 `dp=False,mr=0`）新旧逐位一致 → 无误伤。W=2 模型 20/20 更快（平均 −24%）、17/20 峰<2N；越线 3 档全是 5★67 低炉档。**与 890 正交**（890 是 `ops.start` 时序门，已由方案 A 修）。
- `sets` 封顶 = 换卡箱空位 // 套内张数 N（箱位才是硬约束）；排队位任何场景都不该用；`reserve` 禁无条件 +2。
- ★集齐 > 取卡：`_collect_tight_ex` 三层（空位 0 延后 / 缺口全是可买底卡且空位≤缺口 → 延后先集齐上交 / 含合成卡不延后），60 次逃逸阀。11-04 曾删掉前提导致取卡吃光买卡格位。
- 买卡空位只认 `change_free`（`_run_plan`/`_album_prebuy`/`_box_compaction`），决策前 `_read_state()` 重读；★买底卡铁律 `abs_slots==0 → base_quota=0`，唯一批量买 = `_album_prebuy`；额度按「炉」成组花（waves）；`candidates()` 跳过 5 分钟内 `_recent_buys`（否则买一张卖一张空转）。必需卡判据 = `net_plan` 净需求树（distinct 口径错）；`as_take=True` 仅取卡/炉内卡；本 tick 无 refine 时不延后预买。别加「整波不齐就不买」类前置过滤（实测回退）。

## 基准（DP、45s/tick、两箱合计）
单轮：1★85 4.45 / 2★290 14.43 / 3★59 45.41 / 4★60 50.83 / 5★709 104.45；4 主炉：85 3.79、290 13.76。多轮 BF：178×2 3.79 / 178×3 5.69 / 85×3 13.18 / 290×2 21.26；收礼变卡 178×1 2.52 / 85×1 2.52 / 290×1 12.53 / 178×3 5.04。`MC_SIM_TAKE2=0`/`MC_BUY_FREE=0` 仅历史对照。

## 验收 / 运维
- 改 backend 必须 `./server.sh stop && start` 真重载（`/api/auto/*` 不重载模块）；改完必须实际 `git commit`（分批：代码+测试 / 文档）；scratch 脚本不提交不删除。重启后自动炼卡/换卡引擎为 stopped，需界面重新点开始。
- 总览附加状态（2026-11-10）：`GET /api/overview/extras` = 珍藏阁 `card_user_zcg act=1` + 礼物箱 `freegift_get_list` 各 1 读；**别复用 `/api/home/state`**（7 次 travel 请求，挂 60s 轮询会放大真机压力），也别打 mainpage。前端 `#ov-extras` 是独立容器（不能进 `state-box`，会被 `loadState()` 覆盖），切号时进 `resetAccountScopedCache()` 清空。
- 前端改 `app.js` 必同时 bump `index.html` 的 `?v=` **和** `APP_VER`（只改一处 → 日志版本号不可信）；`Cache-Control: no-store`。按钮即时响应、文案禁代码标识。★类名 `sel` 双用途：通用下拉样式必须写 `select.sel`，否则 `.sel{max-width:340px}` 把选中行压短；套卡首行名称 `flex:0 0 auto` 不参与收缩。
- 勿杀未知服务：`:8000` 本项目 / `:8777` agent-chat / `:9222` headless Chrome。OCI 新开端口两步：① `sudo firewall-cmd --permanent --add-port=<port>/tcp && --reload` ② 云控制台 Security List 加 TCP 入站；`command -v iptables` 在 opc PATH 会误报（须 `sudo /sbin/iptables -S`）。SSH 140.83.54.40:22 opc，密钥 `~/Downloads/ssh-key-2021-12-04.key`。
- 生产默认不勾 DP = 旧引擎，**不再修**（只作 A/B），DP 稳定后直接删；多套/多星一律勾 DP。bench 必须含 `dp=False, mr=0` 一档。
- **默认指本地 8000**；未专门说「云端 / 140.83.54.40 / 8001」时一律指本地（`SESSIONS_DIR=/tmp/mc_sessions/`）。
- 局域网/手机（2026-11-10）：`server.sh` 的 uvicorn host 由 `MC_HOST` 控制，**默认 `0.0.0.0`**，手机同 WiFi 访问 `http://<本机IP>:8000`（当前 `ipconfig getifaddr en0` = 192.168.128.77，DHCP 会变）；`MC_HOST=127.0.0.1 ./server.sh start` 回退只本机。手机登录**不必扫码**：登录页 `/api/login/sessions` 直接列出服务端已有会话，点选沿用；「QQ 一键登录」的回调是 `location.origin`（内网 IP）在手机上不通，别用。⚠️ 该端点无鉴权返回 token，绑 0.0.0.0 等于把全部账号登录态暴露给同网段，只在可信 WiFi 用。

## 旅行（猫咪）
- `day_limit_cnt=12` = 4 猫各 3 次 = 3 轮/天，每天重置、与投喂数无关；硬限制是没食物不能出发。默认 **C 级 + 每猫 1 个**（日耗 1080 三叶草）；4 猫必须各喂不同食物（A/C 一一对应 4 个碎片池）。
- 收获（act=23）前后比 act=3 食物清单可定性「旅行是否掉食物」；已在 `run_once` 与 `harvest_returned` 埋点，以 `act=harvest_gain` 回前端。

## 已结案（别再查）
`net_plan` 中间层漏算（证伪）／290×2 +1.28h（已修）／5★ BF +0.34h（不做）／精灵饿着（代价≈0）／「取卡 2 格致 750h 卡死」（过期）／买卡时序进解 + JIT 重排（2026-11-09 独立副本仿真证伪，见当日日志）。

## 未闭环
1. 多套顺序敏感 —— 用户 2026-10-30 决定**记录不修**（真因=前期炉位分配，无单一静态规则，**别再重查归因**）。
2. 金币/魔力余额与退避、翻牌产出、每日上限未建模。
3. 收礼 `freegift_id` 83/86/7 未实证；后端不解析 `<elf>`，无喂食端点。
4. 贪心 `_make_room` 死角仍在（`keep_ids` 保护在炼套卡）。
5. 「旅行是否掉食物」待一次真机收获确认。
6. ~~前端页面级单例四类串号~~ **2026-11-09 已全量修复**（`20261109b`，仿真 21/21 PASS，详见当日日志）。
