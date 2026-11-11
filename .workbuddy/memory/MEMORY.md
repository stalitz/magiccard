# 魔法卡片助手 · 长期记忆（铁律 + 指针）

全文归档 `.workbuddy/memory/REFERENCE.md`（2026-11-11 压缩前各版 MEMORY.md 逐字原文）；流水 `.workbuddy/memory/YYYY-MM-DD.md`；细节见 `docs/`、`tests/`、`git log`。本文件只留仍生效的铁律。

## 方法论
- 现象 > 推测，禁单点采样下全局结论；写操作先回查定调原文（`git log -S`）。**高面值卡永不自动卖**，只卖 ≤10 面底卡。
- 本机 Bash `grep "a\|b"` alternation 静默失效 → 用 Grep tool 或 `grep -e`。只读排查先跑 `tools/live_contract_check.py`；httpx 探针加 `headers={"Connection":"close"}` + `trust_env=False`。基准「远超 tick 量级」的差异打逐 tick 时间线。
- 提问 ≠ 授权修改；只诊断时不动代码。真机只读探针用项目 venv `/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3`，会话 `/tmp/mc_sessions/<token>.json`（`LoginSession(token).restore()`）。

## 多账号隔离（最高优先级）
- 架构：`SESSIONS[token→LoginSession]` + `SessionProxy`(contextvar)；token 存 `sessionStorage` 经 `X-MC-Token` 传（按标签页隔离），cookie 兜底；落盘 `SESSIONS_DIR/<token>.json`，无 token 走 `default`。
- ★三类状态一律按账号分：①后端内存缓存按 token/uin 分桶（`main._acct_bucket`）；②落盘日计数/限次一律 `acct.acct_path(p, session)`（`x.json`→`x.<uin>.json`，`acct_key` **uin 优先**），函数加 `session=None` 逐点传参；③前端页面级单例进 `showDash()` 的 `resetAccountScopedCache()`；localStorage key 必须加 token 前缀（**localStorage 整浏览器共享，只有 sessionStorage 按标签页**）。
- ★★ 前端统一模式（`20261109b`）：`showDash()` 顺序 = `stopAllAccountLoops()`（8 个定时器 + running 标志）→ `resetAccountScopedCache()`（~40 个账号态 + 界面日志 + `_acctLoaded` + `ACCT_GEN++`）→ 重拉日志/好友池 → 各页 `initXXXOnce()` 由 `acctLoaded(name)` 按 token 判定「数据重拉但事件只绑一次」。全局 fetch 包装**响应回来时**比对 token，不一致换 `ACCT_STALE` 空 JSON（关键回填点再短路 `__stale`）。新增任何 `let xxxCache/xxxDone` 必须进清单或按 token 分桶。
- 已修：`AUTOS`/`_AUTO_EX`/`_ENGINES`/`_autos`、日志三套、`_travel_aux_cache`、`dig_auto._auto`、`_inv_cache`/`_mini_cache`/`_collection_cache`、`_home_all_running`；送礼/工会/比赛/猫家/三国历史、挖地 `_unknown`、偷炉好友池、常去好友QQ。
- ★ 灵宠面板**双数据源**坑（`20261111a`）：`spPoll()` 3s 用 `engine.last_state`，`spTick()`/`spRefresh()` 用 `/api/cat/travel` 全量，交替写同一 DOM。修法：`_snapshot` 补齐前端契约字段（空壳不造 0）；`spMergeState()` 缺失/非数字沿用上一有效值、**0 是合法值不回退**；`spPoll` 遍历 `Object.keys(modes)`（原写死 3 个漏 `invite` → 假停）；切号后 `spPoll(false)` 续轮询。`_ENGINES[(token,mode)]` 分桶本就正确，**不是串号**。
- 故意共享：全局限流器 `_last_req_ts`、`exchange_peers.json`、静态卡数据（`priceChipsLoaded`/`allPrices`/`cardCache`/`spTable`/`gift._name_cache`/`_MODE_PROP_FETCH`）。⚠️ **`_propsCache` 是账号道具库存**，必须按账号失效。
- 日志：`LOG_UIN` → `global.log` + `<uin>.log`；界面日志 `<token>_op.log` 另一套；「跑一轮」SSE 不落引擎日志，只有 `_log()` 落盘。坑：`/api/me` 判 -1001 会 `clear()` 删落盘文件（重扫即可）；default 无 token 可能与真 token 同 QQ 号 → 双引擎抢炉。

## 活动 / 任务（2026-11-11 更新）
- ★ 主界面左侧「活动/任务列表」= **服务端大池子按周/月轮换**。`card_run_base`（无参 POST）的 `version_list.activityInfo` → 免登录静态 XML `appimg2.qq.com/card/mk/activity_info_v_<ver>.xml`（当前 **v_1288**，264KB；`actAddon v_1675`）：`Activity_Main_Info` 63、`Activity_Exchange_Info` 66 带 unix 起止、`Activity_Rule_Info` 48 HTML 规则、`dzp_config`、`ac_item` 348。解析见 `backend/item_dict.py`（⚠️ 9 个 `<?xml?>` 头须剔除包 `<wrap>`）。周任务每周三 0 点刷新。池子含 `renwuliebiao`/`daishulvxing`/`feixingqi`/`xianshirenwu` 等；**活动名是英文代号，中文名需另做映射**。
- **别再说「需要抓包才有活动列表」**：2026-09-15 启动抓包 CSV 在 `~/Downloads/card.qzone.qq.com抓包`（只有 URL+大小，**无 body**），见 `2026-09-15.md:67`。本机 12 个 HAR 全是比赛/探险/送礼/挖地/珍藏阁等具体功能，**没有**「点开任务列表」那次。
- ★★ **实时进度不用抓包**（2026-11-11 在线会话只读实证）：`card_monthtask` 必带 `act` → `act=1`: `{score:7,taskperiodid:5,tasksuit[46](bit/img/num/ts)}`；`act=3`: 月排行榜 `cur_rank_list/pre_rank_list`；`act=5`: 10 个奖励位 `list[{bit,id 11..20,ts}]+score`。`act=0/7=-1`、`act=2/6=-200 参数错误`（**疑似领奖，绝不盲试**）、`act=4=-50005 未上榜`。`card_act_peak act=6`=0；`card_act_volume`/`card_act_travel` 恒 `-1110 activity is end`。
- ★ **写动作铁律**：`backend/daily.py` 只收「真机调用 → 看 code + 服务端原文 → 成功码/『今日已领』码都写进判定」的动作（现 3 项：登陆礼包 -33007 当成功 / 送礼 / 珍藏阁）。领奖/完成任务/转盘/兑换**不许猜参数盲试**（会真领真消耗），必须抓一次带 POST body 的包确认。
- 2026-11-11 14:17 按时间戳筛在期 **5 个**：`mmgj` 11-11~11-17、`xiaofei1` 11 月、`mofashangdian` 与 `zhonghuachuantong` 10-14~12-09、`yincanggoumai` 2021-12-16~2030-12-15；只有后两者有 HTML 规则。
- 奖励：`theme_card_list` **t[6]=金币、t[7]=经验**（tid=45 实测 100/200）；`card_collection_add` 空体；3★ 以上两值多相等，顶格 19999。

## 真机硬约束
- 箱：changebox 18 硬顶 / storebox `cur=168` 是**已开通容量**（`max=200` 非当前）/ 简易箱 15 格。★**炼成卡优先进保险箱、满则溢到换卡箱；只有买的 10 面底卡一定进换卡箱** → 空位一律两箱合计 `_free2`。跨箱只支持 换→保 与 保↔简。
- 箱位闸：两箱空位 < 本套缺张数 → `_calc_sets` 返 0（禁 `max(1,...)`）；同口径拦「收尾买底卡」与 `_make_room` 卖底卡。
- 炉台数每 tick 取 `<stovebox stovenum>`（**禁写死 3**）；`cur=7` = 5 主卡位 + 1 偷炉 + 1 VIP(未开不列)；加炉道具是把排队位升炉位。`_main_cap` 按 `Furnace.stove` 去重判忙；到期无时间戳（`tem_slot_end_time` 是简易箱），只看 stovenum 变小。
- 取卡「空 2 格」是准入门槛非每张占 2 格，逐张重判；被拒炉不消失。时长=表值×0.6232（未投卡预测），在炉走 `btime+locktime`。上交瞬间必须整套各 1 张同在箱。
- 一键日常珍藏阁（`backend/zcg.py`）：抽卡循环后、商店买之前必须先 `_deposit_all_from_box()` 消耗**箱内现成** 10 面卡；读箱失败显式回报禁当空箱；有卡有空炉却 0 投提示「疑似真机繁忙」。
- 卡友 `card_user_theme_list(tid)` 每页 100、uin 需 `html.unescape`；分页环形、部分主题封顶、深页全局去重 0 新增才判翻完。好友列表 ≈194 人含本人 → 偷炉按**数值**判自己。
- 杂卡锁死换卡箱：`_box_compaction` 三段（卖多余 10 面 / 挪本套合成卡 / 箱紧时杂卡按面额降序挪保险箱**只挪不卖**），腾够买 3 张即停；保险箱满只能人工清。
- ★ **`code=-3001` = `card_user_mainpage` 读频次限流（~15 次/10s、冷却 ~15s），不是登录失效/全站繁忙**。只打 mainpage **读**；zcg `act=1/2` 与写请求不受影响 → **禁止一键日常与自动换卡互斥**（用户：「不是每次都报，只是偶尔报」）。一次 `run_zcg` 19 次请求只提高撞窗概率。
- ★ `zcg.py` 已修：统一 `_read_mainpage()` 0.35s 节流 + 有限退避 `(1.5,4.0,8.0)`；`_box_ten_cards` 失败抛 `_BoxReadError`；`_box_state` 带 error 不动作；写后延迟确认；中途失败标 `partial`；`["puzi"]`→`.get`；`_slot_val()` 修 `int(slot or -1)` 把首格 `slot=0` 误判。回归 18/18 PASS。

## 调度引擎（在线 RCPSP）
- 禁 god-function，planner/executor 分离；「优化初始序」类补丁无效（四次复现），CP-SAT 只作标尺。生产 `interval=45.0`（K=1 50.85h → K=200 168.76h）。
- 方案 A（`cdf4753`）：pass 2a `MC_MERGE_READY` 只收 feeders 全非底卡的 refine（3→1 净腾 2 格），绕 `start` 门、按 `cp` 最高档、每 tick ≤1；底层 40 面 op 绝不进。`MC_MERGE_STACK=2` 已证伪删除。仍有退化档（3 炉 85/290、5 炉 60/709）。
- ★ 字典序目标（`MC_LEX_PEAK`，`sched_cmp/sched_dp.py`）：dfs/memo 扩成 `(val, suffix, peak)`，按 `(makespan, peak)` 择优，达下界后 `MC_LEX_EQ_VEC`(2) 有限枚举。**别禁用「达下界即 break」**（890 全量枚举打满 2s → +1.87h）。实测 `lex_swap=0`，890 收益来自枚举改善搜索质量。生产 `MC_BUY_FREE=1`/`MC_BUY_KEEPOP=0` → 骨架 buy op 不进解（买卡由 `jit_need` 决定），求解器层买卡时序改动无效；JIT 重排已证伪（峰只降 0~3 格、工期 −0.65~+3.72h、solve 2~3 倍，**别再走**）。
- ★★ 用户 2026-11-09 验收线（已落地）：跨轮重叠吃箱位可接受（**峰值 < 2N 可接受**）；**绝不能单轮超炼**。①`_calc_sets` 对自动推断套数硬上限 2（`MC_AUTO_MAX_SETS=0` 消融），界面显式 `surplus_sets>0` 不钳；②`DPTickController._union_need0` 的 `pipe_sets` 提套处加 2N 闸（`MC_PIPE_2N=0` 消融，**必须在 sets_fn 之后**），只数箱内不算 pending。W=2 模型 20/20 更快（平均 −24%）、17/20 峰<2N。
- `sets` 封顶 = 换卡箱空位 // 套内张数 N；排队位任何场景都不该用；`reserve` 禁无条件 +2。
- ★集齐 > 取卡：`_collect_tight_ex` 三层（空位 0 延后 / 缺口全是可买底卡且空位≤缺口 → 延后 / 含合成卡不延后），60 次逃逸阀。
- 买卡空位只认 `change_free`，决策前 `_read_state()` 重读；★`abs_slots==0 → base_quota=0`，唯一批量买 = `_album_prebuy`；额度按「炉」成组花；`candidates()` 跳过 5 分钟内 `_recent_buys`。必需卡判据 = `net_plan` 净需求树；`as_take=True` 仅取卡/炉内卡。别加「整波不齐就不买」前置过滤。

## 基准（DP、45s/tick、两箱合计）
单轮 1★85 4.45 / 2★290 14.43 / 3★59 45.41 / 4★60 50.83 / 5★709 104.45；4 主炉 85 3.79、290 13.76。多轮 BF 178×2 3.79 / 178×3 5.69 / 85×3 13.18 / 290×2 21.26；收礼变卡 178×1 2.52 / 85×1 2.52 / 290×1 12.53 / 178×3 5.04。bench 必须含 `dp=False, mr=0` 一档。

## 验收 / 运维
- 改 backend 必须 `./server.sh stop && start` 真重载；改完必须实际 `git commit`（分批）；scratch 脚本不提交不删除。重启后自动炼卡/换卡引擎为 stopped，需界面重新点开始。
- 总览附加状态：`GET /api/overview/extras` = 珍藏阁 `act=1` + 礼物箱 `freegift_get_list` 各 1 读；**别复用 `/api/home/state`**（7 次 travel 太重），也别打 mainpage。`#ov-extras` 独立容器（不能进 `state-box`），切号进 `resetAccountScopedCache()` 清空。
- 前端改 `app.js` 必同时 bump `index.html` 的 `?v=` **和** `APP_VER`；`Cache-Control: no-store`。按钮即时响应、文案禁代码标识。通用下拉必须写 `select.sel`；套卡首行名称 `flex:0 0 auto`。
- 勿杀未知服务：`:8000` 本项目 / `:8777` agent-chat / `:9222` headless Chrome。OCI 新开端口两步：firewall-cmd + 云控制台 Security List。SSH 140.83.54.40:22 opc，密钥 `~/Downloads/ssh-key-2021-12-04.key`。
- 生产默认不勾 DP = 旧引擎，**不再修**（只作 A/B）；多套/多星一律勾 DP。**默认指本地 8000**，未专门说云端一律本地（`SESSIONS_DIR=/tmp/mc_sessions/`）。
- 局域网/手机：`server.sh` host 由 `MC_HOST` 控制，**默认 `0.0.0.0`**；手机同 WiFi 访问 `http://<本机IP>:8000`（192.168.128.77，DHCP 会变）。**不必重新扫码**：登录页 `/api/login/sessions` 可沿用会话；「QQ 一键登录」回调 `location.origin` 在手机不通。⚠️ 该端点无鉴权返回 token，只在可信 WiFi 用。

## 旅行（猫咪）
- `day_limit_cnt=12` = 4 猫各 3 次 = 3 轮/天；硬限制是没食物不能出发。默认 **C 级 + 每猫 1 个**（日耗 1080 三叶草）；4 猫必须各喂不同食物。
- 收获（act=23）前后比 act=3 食物清单可定性「旅行是否掉食物」；已在 `run_once`/`harvest_returned` 埋点。

## 已结案（别再查）
`net_plan` 漏算（证伪）／290×2 +1.28h（已修）／5★ BF +0.34h（不做）／精灵饿着／「取卡 2 格致 750h 卡死」（过期）／买卡时序进解 + JIT 重排（证伪）／前端页面级单例串号（已全修）／「活动列表需要抓包」（2026-11-11 纠正）。

## 未闭环
1. 多套顺序敏感 —— 用户决定**记录不修**（真因=前期炉位分配，别再重查归因）。
2. 金币/魔力余额与退避、翻牌产出、每日上限未建模；收礼 `freegift_id` 83/86/7 未实证；后端不解析 `<elf>`，无喂食端点。
3. 贪心 `_make_room` 死角仍在；「旅行是否掉食物」待一次真机收获确认。
4. 活动写动作（领奖/完成任务/转盘/兑换）参数未抓包确认；活动中文名映射未建；其他活动的个人进度端点待定位。
