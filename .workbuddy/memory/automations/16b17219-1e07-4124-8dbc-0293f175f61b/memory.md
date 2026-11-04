# 自动化执行记忆 · 偷炉真机端到端验证

## 2026-09-17 19:57（首次执行）· 成功
- 脚本：`/tmp/mc_steal_verify2.py`（+ 探查用 `/tmp/mc_probe.py`），解释器 `/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3`。
- 结论：`card_stove_steal` 真机返回 **code=0**，端到端链路验证通过。
- 路径：先查 `/api/auto/status`（`running=true` 但 `steal.last_steal=null`，未走「直接报告」分支）→ 恢复登录 → 读 mainpage（偷炉位已空，引擎 19:50 已收走上一炉）→ 我的偷炉位为空 → 走真实偷炉分支。
- 关键约束与坑：
  1. `plans[1652].comp` 是**全局配方表**（11125 条），不是本套；必须先按 `plan.ids` 过滤求本套配方，否则「素材不是套内成品卡」筛掉一切。
  2. 本次箱内 1652 底卡（23920~23924）为 0（被引擎吞进主炉）→ 只能补买；买的是引擎 buylist 内的 3 张 10 面值底卡（23923/23922/23921），3 次 `card_market_npc_buy` 全 code=0。
  3. 首选好友 983688972 直接命中（`free_main=3`），未走回扫。
  4. **响应 `slotid=5` ≠ mainpage `slot=6`** → 收卡要用 mainpage 的 slot。
- 未产生需要 present 的文件类交付物（纯 API 验证）。

## 复用提示（下次同类任务）
- 若只要「有 code=0 结果」，先看 `/api/auto/status` 的 `steal.last_steal`；为 null 时再自跑。
- 素材不足时优先选「引擎 buylist 里已列出的 10 面值底卡」补买，成本 20 金/张且对齐计划。
- 严禁选「素材本身带配方的套内成品卡」（如 theme 1797 的 25946/25947/25950，单价 286~372 金）。
