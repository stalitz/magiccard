# 魔法卡片助手 · 长期记忆（铁律 + 指针）

深细节看 `.workbuddy/memory/REFERENCE.md`（含 2026-11-04 精简前 MEMORY.md 逐字原文 + 2026-10-30 归档旧版）、`docs/`、`tests/`、`git log`；流水看 `YYYY-MM-DD.md`。

## 方法论
- 现象 > 推测，禁单点采样下全局结论；写操作先回查定调原文（`git log -S`）。**高面值卡永不自动卖**，只卖 ≤10 面底卡。
- ★本机 Bash `grep "a\|b"` 的 alternation **不生效**（静默空 → 误判「没这个东西」）→ 用 Grep tool 或 `grep -e`。
- 只读排查先跑 `tools/live_contract_check.py`；httpx 探针必须 `headers={"Connection":"close"}`。
- 基准出现「远超 tick 量级」的差异时，别用机制直觉解释，直接打逐 tick 时间线。

## 会话 / 多账号架构（2026-10-31 落地）
- 同端口多账号：`SESSIONS[token→LoginSession]` + `SessionProxy`（contextvar 路由）；前端 token 存 `sessionStorage` 经 `X-MC-Token` 头携带（按标签页隔离，普通开两个标签即各登各号），cookie 仅兜底。落盘 `SESSIONS_DIR/<token>.json`，无 token 走 `default`。各引擎缓存键改用真实 session。
- ★★ **模块级缓存必须按账号分桶**（2026-11-04 真 bug）：`_travel_aux_cache` 曾是进程级单例，45s TTL 内 A 账号的食物/任务被 B 复用（3822843 显示 4821992 的库存）。→ 新增任何 `_xxx_cache = {...}` 都要按 `token/uin` 分桶，invalidate 只清当前账号。
- **隔离现状清单（2026-11-04 全量排查）**：引擎 `AUTOS[token]`（炼卡）/ `_AUTO_EX[token]`（换卡）/ `_ENGINES[(token,mode)]`（灵宠×4 玩法）/ `_autos[token]`（挖地）✅；日志 `_UI_LOGS`/`_MATCH_LOGS`/`LOG_UIN` ✅。已修单例：`_travel_aux_cache`、`dig_auto._auto`、`_inv_cache`(8s) / `_mini_cache`(60s) / `_collection_cache`(120s)、`_home_all_running`。统一走 `main._acct_bucket(store)`。`gift._face_of/_name_cache`、`_MODE_PROP_FETCH` 是静态卡数据，无需分桶。
- ⚠️ **default 与真 token 可能是同一个 QQ 号**（`/tmp/mc_sessions/default.json` 当前 = 4821992，与 `7k2x…` 同号）：无 token 的请求会落到 default 的**另一个 LoginSession**，若两边各起一个引擎就是同号双引擎抢炉/抢卡。排查串号先看这个。
- 日志按账号：`LOG_UIN` contextvar 分流 → `<LOG_DIR>/global.log`（聚合）+ `<LOG_DIR>/<uin>.log`；引擎流水走 `engine_logger` 只落 `<uin>.log` + stdout（`server.sh` 另有 `/tmp/mc_<PORT>.log` 总览）。`LOG_DIR` 默认 `/tmp/mc_logs/<PORT>`。
- ⚠️ `/api/me` 探针判 -1001 会 `clear()` 删落盘文件 → 重扫一次即可。
- ⚠️ 界面日志（`<token>_op.log`）与引擎日志（`<uin>.log`）是两套；「跑一轮」走 SSE 直推前端，**不落引擎日志**，只有 `_log()` 才落盘。

## 真机硬约束（API 事实）
- 卡箱：changebox **18 硬顶**、storebox `<cur="168" max="200">` —— **cur 是已开通容量（168）**，max 是没花钱扩的上限；`nor` 才是普通卡数。简易箱 15 格。买卡只落换卡箱。**卡张数按两箱合计算**（保险箱的卡照样能投炉、能上交）。
- 炉：**主炉台数 = `<stovebox stovenum>`**（`cur` 恒 7 是卡位总数）。`cur=7` = **5 主卡位(slot 0~4) + 1 偷炉位(slot 6) + 1 个 VIP 卡位(slot 5，未开通→不列)**，能用 6 个。**加炉道具 = 把排队位升成炉位**（非加卡位）：3 炉 = 3 炼+2 排／4 炉 = 4 炼+1 排／5 炉 = 5 炼+0 排（道具最多 +2）。硬约束 `free_main = 主卡位数 − 占位卡数`（数出来的，开 VIP 自动 +1）。5 炉时 `MC_MAIN_QUEUE=1` 消融自动失效。
- 加炉到期：mainpage **不下发到期时间**（`stove_acc_ts`/`stove_jiasukaTs` 恒 0；`tem_slot_end_time` 是简易箱，别混）。信号只有 `stovenum` 变小；到期=炉位降回排队位，卡大概率继续炼完。已做 `_busy_stoves` 只数合法炉号 `0..machines-1`（超额炉不占并发预算，否则额度变负→停摆）+ 《缩容取证》日志。
- 引擎并发炉数每 tick 取 `cap.stove.stovenum`（**禁写死 3**）；`_main_cap` 判忙 **必须按 `Furnace.stove` 炉号去重**（按卡数会把「1 炼 + 1 排」算成 2 台 → 额度恒 0 → 引擎不动）。
- 取卡：**「空 2 格」是取卡前的准入门槛**（防幸运事件多给一张），**不是每张占 2 格**。逐张取、每取一张按新空位重判；没得奖就还剩 1 格可接着取，得奖才多吃 1 格。被拒时炉不消失、卡不丢。
- 炼卡时长 = 表值 × **0.6232**（只用于未投的卡预测）；在炉卡走 `btime+locktime`，永远精确。上交瞬间必须**整套各 1 张同时在箱**。
- 卡友 `card_user_theme_list(tid)`：每页 100 人（`node.uin` 用 `&#x7C;` 打包 10 个，需 `html.unescape`）。**分页环形**，翻到末页会绕回早前页、不返空；**部分主题被封顶=逻辑仅 1 页**（如床 1818：page1==page0）。识别：page1==page0 判封顶（`capped_tids` 上报）；深页并入全局去重后 **0 新增**判翻完（不能只比 page0，否则多页套死循环）。多套策略：主套先翻完，其余源套逐页交替。
- 好友列表 `card_user_list` ≈194 人、不分页、**含本人**（uin 无前导 0）→ 偷炉必须按**数值**判「是不是我」（只比字符串会偷自己 → 冷却 120s + 自拉黑 30 分钟）。
- ★换卡箱会被「非本套杂卡」永久锁死：`_box_compaction` 三段——卖多余 10 面底卡 / 挪本套合成卡 / **箱紧且保险箱有位时杂卡按面额降序挪保险箱（只挪不卖）**，腾到够一次「买 3 张立即投炉」即停。保险箱满（168/168）则只能用户自己清。

## 调度引擎（在线 RCPSP，滚动重规划）
- 禁 god-function，planner/executor 分离。「优化初始序」类补丁**一律无效**（四次复现）；CP-SAT 只作标尺。
- `sets` 封顶 = **换卡箱空位 // 套内张数**（真机爆箱事故，箱位才是硬约束，改炉位口径无效）。排队位任何场景都不该用。`reserve` 禁无条件 +2。
- 买卡空位只认 `change_free`（`_run_plan` / `_album_prebuy` / `_box_compaction` 三处）；决策前必须 `_read_state()` 重读。**决策间隔是第一性能变量**（K=1 50.85h → K=200 168.76h），生产 `interval=45.0`。
- ★**集齐 > 取卡（2026-11-05 修正）**：`_collect_tight_ex` 三层——①空位==0→延后；②缺口**全是可买底卡**（`miss_comp` 空，与补集卡册同口径）且空位≤缺口→延后先集齐上交；③含合成卡→**不延后**（延后=不投炉=缺口不减=死锁）。11-04 曾一律收紧成「空位==0」，把「先集齐再取卡」的前提删掉 → 取卡吃光买底卡格位。有逃逸阀（连续 60 次延后且 bought 没涨→放行）。
- 腾位 `candidates()` 跳过 `_recent_buys` 里 5 分钟内买进的卡，否则「买一张→卖掉刚买那张」空转（真机 bought 214 次、进度零）。
- 必需卡判据 = **`net_plan` 净需求树**（`_is_collect_essential`，runs/buy 里还要它才算必需）；**distinct 口径是错的**（一个 id 要 7 张时误判「不缺」→ 预买全延后 → 熔断 35 分钟）。`as_take=True` 仅取卡/炉内卡用（先把它从在途扣掉）；买卡/变卡不扣。`_net_now` 按家底签名缓存。★逃逸阀：本 tick 无 refine 动作时不延后预买。
- ★**买底卡铁律（2026-11-03）**：「卡没集齐时一个 10 面都不要买，只占位；收了最后一张在炼卡才一次买齐上交。」落地：`abs_slots==0`（无空主炉、无 1 tick 内空出的）→ `base_quota=0`，不留「箱宽松就囤」的口子。唯一批量买 10 面 = `_album_prebuy`。
  - ⚠️ 别加「非底卡喂料必须在箱才买它的料」这类前置过滤（试过回退：一次只投 1 炉，85 4.45→5.04h）；也别改成「整波不齐就不买」（箱常只剩 1~2 格会死锁）。
- ★**额度按「炉」成组花**（2026-11-04）：买卡与投炉本就同 tick（`buy_sametick` + 虚拟槽，执行层买完重读真实 slot）。pass 1 建 `waves`（一波 = 一个 refine op 的整套底卡），先按波填齐再按缺口补；按卡种累加会让额度全砸同一张卡 → 一炉凑不齐。

## 已结案（别再查）
`net_plan` 中间层漏算（证伪）／290×2 +1.28h（骨架不纠正，已修）／5★ BF +0.34h（判定差异，不做）／精灵饿着（代价≈0，不建模）／「取卡 2 格致 750h 卡死」（过期，实测仅 5★ +1.61h）。

## 基准（DP、45s/tick、两箱合计口径）
单轮：1★85 **4.45** / 2★290 **14.43** / 3★59 **45.41** / 4★60 **50.83** / 5★709 **104.45**；4 主炉：85 **3.79**、290 **13.76**。
多轮（BF）：178×2 3.79 / 178×3 5.69 / 85×3 13.18 / 290×2 21.26；收礼变卡：178×1 2.52 / 85×1 2.52 / 290×1 12.53 / 178×3 5.04。
`MC_SIM_TAKE2=0` / `MC_BUY_FREE=0` 仅供历史对照。

## 验收 / 运维
- 改 backend 必须 `./server.sh stop && start` 真重载（`/api/auto/*` 不重载模块）；改完必须实际 `git commit`（分批：代码+测试 / 文档）；scratch 脚本不提交不删除。
- 前端改 `app.js` 必同时 bump `index.html` 的 `?v=` **和** `app.js` 里的 `APP_VER` 常量（只改一处 → 日志里的版本号不可信）。按钮即时响应、文案禁代码标识。
- 勿杀未知服务：`:8000` 本项目 / `:8777` agent-chat / `:9222` headless Chrome。
- ★OCI 云部署：新开端口必须两步——①`sudo firewall-cmd --permanent --add-port=<port>/tcp && --reload` ②云控制台 Security List 加 TCP 入站。`command -v iptables` 在 opc PATH 会误报"无防火墙"（须 `sudo /sbin/iptables -S`）。SSH 140.83.54.40:22 opc，密钥 `~/Downloads/ssh-key-2021-12-04.key`。
- 生产默认不勾 DP = 旧引擎，**不再修**（只作 A/B），DP 稳定后直接删；多套/多星一律勾 DP。bench 必须含 `dp=False, mr=0` 一档。

## 旅行（猫咪）要点
- 次数：`day_limit_cnt=12` = 4 猫各 3 次 = 3 轮/天，**每天重置、与投喂数无关** → 投喂数是纯成本项。硬限制是「没食物不能出发」，既没食物又没三叶草=停摆。
- 食物等级：A(quality=3) 500/个 +20min；C(quality=1) 360/个 +15min；quality 2/4 商店买不到。默认已切 **C + 每猫 1 个**（日耗 1080 三叶草）。4 猫必须各喂**不同**食物（4 种掉不同碎片池，A/C 一一对应：曲奇饼↔夹心饼干、寿司↔饭团、牛排↔汉堡、回忆棒棒糖↔回忆便当）。
- 收获（act=23）前后比 act=3 食物清单可定性「旅行是否掉食物」；该对比已在 `run_once` 与 `harvest_returned`（界面「收获」按钮）两处埋点，结果以 `act=harvest_gain` 回前端。

## 未闭环
1. 多套顺序敏感 —— **用户 2026-10-30 决定记录不修**（3 套 16.29~20.64h 差 27%；2 套幅度小；真机常见 178+85 顺序无关）。真因=4 炉位前期分配（在线 RCPSP），无单一静态规则，**别再重查归因**。
2. 金币/魔力余额与退避、翻牌产出、每日上限未建模。
3. 收礼 `freegift_id` 83/86/7 未实证；后端不解析 `<elf>` 节点，无喂食端点。
4. 贪心 `_make_room` 死角仍在（`keep_ids` 保护在炼套卡 → 卖不动），按上条不再修。
5. 「旅行是否掉食物」待一次真机收获 + `act=harvest_gain` 日志确认。

## 会话约定
- **默认指本地 8000**：未专门说「云端 / 140.83.54.40 / 8001」时一律指本地（`SESSIONS_DIR=/tmp/mc_sessions/`）。之前多次误去云端查本地现象，属方向错。
