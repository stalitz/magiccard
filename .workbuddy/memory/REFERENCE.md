# 魔法卡片助手 · 详细参考（由 MEMORY.md 索引，按需读）

## 官方静态配置源（优先级最高）
`card_run_base`（无参 POST）→ `version_list`：67 模块 + 配置 URL，多为 `appimg2.qq.com` 公开静态 XML，免登录（用 python urllib；curl 常不可用）。
- `soulElfCfgUrl` → `mk/soul_elf_v_78.xml`：灵宠 `pass_mainlist`(11 章)/`pass_sectionlist`(122 关，含 `max_spirite_num`)/`spirit_maplist`(16 宠)/`card_piecelist`(488 碎片) → 已导出 `data/spirite_pass.json`，`backend/spirite_maps.py` 读。
- `activityInfo` → `mk/activity_info_v_1274.xml`：`Activity_Exchange_Infolist`(66 活动带起止)/`pack_buylist`/`ac_items`。⚠️ 含多个 `<?xml?>`，须正则剔除后包 `<wrap>` 再 parse。
- `propCardUrl` → `mk/propCard_v_26.xml` → `data/prop_dict.json`；其它 `travel_Info_v_460.xml`/`catHome_Info_v_114.xml`/`card_info_v3.xml`。
- `cat_cloth_v_54188.xml`（2026-09-29 用户抓包实证，版本号随游戏更新）→ **全量服装表 285 条**（catHome_Info_v_114 的 96 条是其子集），带 name/cloth_type/cloth_param/cloth_intro；`cat_dress.build()` 双源合并 → `data/cat_equip.json`（286 件），衣柜 127 件名字全覆盖。相关同版本：`cat_cloth_bulid/awake/tempering`、`season_date`、`formation_level_conf`、`pvp_talent`。⚠️ 必须 https 直连（http 301）。

### 奖励编号格式（全局统一）
一律 `大类id_类内编号_数量`（翻牌 `content`/灵宠 `register_gift`/礼包 `package`/猫咪 `giftInfo`）。**第 1 段是玩法品类 id，不是道具 id**。名字表 = `backend/item_dict.py` + `data/item_dict.json`（472 条，`python -m backend.item_dict` 联网重建）。

| 大类 | 官方源 / 已知编号 |
|---|---|
| 2..73 | `swf/resource/resource_v_*.xml` `<prop type id name>`：4=小魔瓶 / 7=100面值卡 / 44=金币 / 46=五彩石 / 47=**金粉** / 51,52,53=橙,红,紫魔晶 / 55=魔力 / 57=灵力果 / 58=藏宝图 / 59=金钥匙 / 72=银锭 / 73=百变丹 / **78=月光石**（人工核实；静态表无 → 走 `data/item_names_extra.json` 覆盖层，`POST /api/items/name?key=78_0&name=月光石` 热生效） |
| 48 通用活动道具 | `activity_info` `<ac_items>`：1410=礼物 / 1415=气球 / 1599=能量包 |
| 92 猫咪旅行 | `catHome_Info` `<catHome_Item>`：1=护身符 / 2=幸运石 / 9=三叶草 / 10=扭蛋币 / 32=红宝石 / 59=纤维 / 62=锻造石 / 85=进化石 |
| 93 / 97 | 猫咪小屋经验 / 猫咪装备 |

⚠️ 同编号跨字典重名是常态（1415 在卡库是「比利亚」）→ **先定大类再查编号**；**禁止在前后端硬编码物品名**（旧 `REWARD_ITEM_IDS` 已删）。

## 已接端点
- `card_user_list` = 好友列表（194 人，**非全服**，含僵尸号）→ `/api/players` + 换卡好友池（localStorage `mc.friendPool`；排除自己需 `lstrip("0")`）。扫描配 `stop_after`。
- `card_user_theme_list` = 在玩某套的卡友，**每套硬上限 100 人**，跨套几乎不重合。
- `card_mini_get` = 集卡册 1622 套 → `/api/collection/progress`。⚠️ `num/lv` 与卡箱持有无关（1624 套仅 16 套吻合），勿当收集度。
- `card_market_props_info` = 道具持有（`<prop type subtype count>`）→ `/api/props`；8 条全是 `type=1` 炼卡道具。
- `card_user_mainpage`（无参 POST）一次下发主界面绝大部分状态 → `GET /api/card/mainpage`（透传 raw）/ `/api/state`（精简）。
  - `<user>`：nick/uin/money/**mana=蓝宝石**/exp/lv/regti/randchance（真机逐位吻合）。`<new_data>`：show_record/current_use_skinId/color_stone/purple_day/tem_slot_end_time/stove_jiasukaPercent/doble_exp_ts/day_fp_cnt/lj3/lj7/puzi_rent14,15,43。
  - `<info><stove id endtime lv strong_lv>` 主炉位（strong_lv=30 即界面 Lv.30）；`<stovebox slottype="steal">` 偷炉位；`<rent_slot max_count=9>` 租炉；`<refineguide>` 攻略；`<zcg>` 种子罐；`<elf>` 宠物。
- 其它：猫咪旅行 `act=191`（仅积分统计）；`card_act_*` 的 act = 活动号（141 周年庆 / 185 连续登录 / 251 签到 / 281 排行榜 / 241 消耗类 / 11 需 md5 签名）。挖地/农场 = 独立 appid 353（`rc.qzone.qq.com/myhome/353`），不在 card.show 体系（`card_farm/land/dig` 全无效），但模块表有 `waDiModule`、活动表有 `wadihuodong` → **待复核**。

## 灵宠探险（`card_user_spirite`）
act：1 查询 / 3 大地图出发 / 4 收获 / 11 穿越 / 14 荒岛出发 / 15 荒岛收获。**三套玩法同一入口、同页 tab 切换**（用户要求，勿拆页）。
- ⚠️ `opuin` 必须**不带前导零**（`4821992`），否则静默返回空。`ids` 用逗号串（`|` 只取首值、JSON 数组 -2）。
- ⚠️ 大地图 `free_explore_cnt=0` 时出发仍成功、改扣 `permanent` → **严禁用真实灵宠探测**（`spirite_auto.py` 有兜底）。收获最短 ≈5 分钟。`pass_id`=章 / `section_id`=章内关。
- ① **大地图**（11 章 122 关，单关派 `max_spirite_num`=1/2/4）act=3/4，仅它接了自动派发。
  ⚠️ **免费次数口径（2026-09-23 定案，act=1 实测单帧）**：`free_explore_cnt` = **剩余**免费次数
  （每次出发 -1，归零改扣 `permanent`）；`today_fri_explore_cnt` = **今日已用**；**总数 = 二者之和**。
  前端 `spRenderAll` 大地图行显示「免费 已用/总数 次 · 剩 剩余」（与「进行中/当前关」同块）。
  ② **荒岛** = `c_island`（8 章 48 关，`elf_num` 3~8 + `elf_hp` 战斗，奖励含五彩石/金粉）= mainpage `hy_*`：**act=14** `type=1&id=<连续 1..48>&ids=<逗号串>`，**act=15** 无参收获（act=4 收不了返 -5；早于 3 分钟 -2）；关卡号只认 `hy_explore_pass_id`，`hy_section_id=65535` 无效。
  ③ **穿越丛林** = `c_across`（11 章 62 关，`elf_num` 1~6 + `elf_lv` 门槛，无 `elf_hp`）：**act=11** `sub_id=&id=&cnt=1`（均 0-based；`id=open_c-1`、`sub_id=open_s/2-1`，见 `spirite_maps.across_param()`）；即时到账、无收获，奖励在 `content`；-2=未解锁 / -1=1 级地图未开启 / code=0 无 content=2 级编号越界；状态走 `torrid_zone_explore_cnt`；不传灵宠 ids；1 级地图与大地图 11 章一一对应。
- **「能去的最新关」= 官方规则推导，不落盘**：`open_c/open_s` 是解锁条件（`open_s` 就是章内关号，勿 /2）。大地图 act=1 直接下发 `pass_id/section_id`（`vt52`=全局累计序号）；穿越/荒岛 `解锁 = (open_c,open_s) ≤ (pass_id,section_id) 且满足 elf_num/elf_lv`，取 seq 最大者（`backend/spirite_progress.derive`，纯函数）。前端 `spPicked` 按玩法各记「是否手动改过」。
- 灵宠道具（碎片/银锭/百变丹/金钥匙/藏宝图/灵力果）全在 act=1 顶层 ≠ 炼卡道具。灵宠名用官方 `spirit_maplist`，勿显示「灵宠N」（后端 `re.sub(r"\s+","")` 归一化）。

## 套卡凑齐模型 + 集卡册
- 配方 = `card_lib.theme_compose_list`：`[theme_id, 1, 目标卡, 素材1..3, 秒数]`（`/api/cards/theme/{tid}` 吐成 `recipe`）。10 面值卡 `recipe=null` = 无配方基础卡，只能买/抽/换 → 凑套瓶颈常在此。
- 凑齐整套需重复卡（不动点迭代，**每轮从 owned 重算**，否则爆炸）。
- 「上交」非游戏术语，实际是入册 `card_collection_add`；读卡册 `card_collection_get`（`<Node theme_id cnt lv total_cnt>`，`cnt`=在册数）。
- ⚠️ `/api/random/draw` 一键抽卡会**自动卖掉 10 面额卡** → 凑套期间禁用；单抽用 `/api/random`。
- **`GET /api/card/synthesis?card_id=`（2026-09-19 改版）**：返回该卡所属套卡的 `tiers` —— 按面值倒序分层 `[{price, secs, cards:[...]}]`，**该套每张卡只出现一次**；每张卡：`own`(持有数)、`target`(有配方)、`refinable`(3 张下级都够数)、`craft`(炉内合成中，来自 `<stovebox>` 的 card id)、`mats:[{card_id,name,price,need,count,lack,owned,buyable}]`；顶层还有 `free_stoves`/`clicked_card_id`/`total`。旧字段 `nodes`(16 个配方块) 已删。前端 `renderSynTiers/mountSynView/synSelect` 消费它（卡库套卡明细 + 卡箱弹窗共用）。
- 该接口走 `_require_login()` + 主界面 XML，**离线（会话失效）时卡库套卡明细会显示加载失败**（静态卡库本身仍可查）。

## 炼卡引擎（`backend/auto_refine.py`）
- 10 面值基础卡一律商店买（20 金/张，无限量）。`recipe=null` ≠ 拿不到。
- 调度用净需求 MRP `net_plan(stock, pending)`：按配方深度降序结算 `net = need − (箱内 + 炉内在炼)`，**只把 net 传给素材**。用「全套从零」口径会多炼近 10 倍。
- **买卡 = 就地为每炉补买，绝不预囤**；等所有成品套内卡都在箱/在炉，才做收尾补买，此刻换卡箱剩余 0 才是真空间不足。**底卡常态化零库存** → 任何候选判定都不能要求「素材已在箱里」。
- `_prepare_turn()` = 备齐一个炉次（缺的底卡就地买 → 重读状态 → spare 校验），**主炉与偷炉共用**；`_spare()` 素材保护：非套内卡或商店可买 → 放行；消耗后仍够 `keep[m]` → 放行；在 `planned` 或有 `pending` → 放行（缺则死锁）。
- 炉位：`stovebox cur=7` 但**主炉上限 5**；空槽只数服务端列出的 `id==0 && slottype!="steal"`，别用 cur 反推。剩余时间看 `<stove endtime> − <hello time>`（`locktime` 不是倒计时）。
- `_payload` 返回 `(payload, picked)`，只在本地副本取槽；成功后按 `picked` 中 `slottype==0` 的张数递减 `st["cap"]["change"]["used"]`。
- 暂缓记 `action="暂缓", ok=True`，不计 errors；`err_msg(0)` 必须返「成功」。
- 容量：换卡箱 `cur=max=18` 不可扩容；保险箱 `cur=166/max=200`。腾位 `EP_MOVE=card_user_storage_exchange`（**payload 未实测**）。
- 端点：`GET /api/auto/plan?tid=`（只读）；`POST /api/auto/start?tid=&buy=&rounds=&steal=&steal_friend=`。
- **`_refine_all` 绝不能用 `free_main<=0` 提前 return**（曾是「偷炉从没触发」的根因）：主炉空位与偷炉位是**两条独立通道**。

## 偷炉（借好友空炉）· 2026-09-17 抓包 + 真机验证通过
- 端点 **`card_stove_steal`**（≠ refine），参数同炼卡 `targetid/bflash=0/ver=1/targettype=1/themeid/slot1..3/slottype1..3` + **`opuin=好友uin`**，**不要带 `code`**。refine+opuin 会被当成自己炉炼 → 主炉满时误报 **-33003**。
- 素材取**我卡箱**槽位；炼成后卡挂好友炉，收卡 `card_stove_stealcard_get` + `opuin`。
- **全局同时只能偷 1 个炉子**：再偷返 **-33031**（**不能拉黑好友**，只做全局冷却）。偷炉位被占时我 stovebox 有 `slottype='steal'`+`opuin`+`flag=1`，`cap.stove.free_steal=0` → 据此门控；条件 `can_steal = steal_enabled ∧ free_steal>0 ∧ 未冷却`，buy 路径重读 `st` 后要再查一次 `free_steal`；`engine.steal_blocked` 记录「能偷却没偷」的原因并透传到面板。
- 好友空炉探测：`card_user_mainpage`（`code=''` + `opuin=好友`）→ **读 `cap.stove.free_main`**；在 `st["stove"]` 里数 `id==0` 永远得 0（曾误判「好友炉不可探测」）。
- ✅ 真机验证：23926「求姻缘」（1652 七夕）→ 好友 983688972（`free_main=3`）→ `code=0`；mainpage 回 `slot=6 id=23926 opuin=983688972 flag=1 slottype=steal`，`free_steal` 1→0。⚠️ 响应里的 `slotid=5` 与 mainpage `slot=6` **不一致** → 收卡用 mainpage 的 slot。

## 商店买卡
- 可买是**套卡级**属性：`card_query.buyable_themes()` = 套内含 10 面额基础卡的 487 套（唯一判据，`card_plan` 复用，勿写第二份）。单卡可买 = `面值==10 且 theme_id ∈ buyable_themes()`，扣 20 金。
- `POST /api/card/buy?card_id=&theme_id=`（底层 `card_market_npc_buy`）；成功后清 `_inv_cache` 让持有数立刻刷新。
- 「买」按钮落点（用户澄清）：**合成路径弹窗里缺料的下级卡**（主）+ 卡库套卡明细页 + 面值查卡列表。**卡箱卡片上不要买按钮**。

## 珍藏阁（zcg）· 2026-09-17 定位
- 配置全在 `resource_v_*.xml`：`card_zcglist`(20 级 jlz/puzi_cnt)/`card_zcg_pzlist`/`card_zcg_stronglist`(需孔雀石、月光石)/`zcg_juebanlist`/`zcg_limit_duilist`/`zcg_proplist`(112 项)；模块 `treasureLoftModule`。
- 状态 `POST card_user_zcg` **必须 `act=1`**（其它值 = -1 XML 解析失败）→ JSON `jf/cmj/hmj/zmj/kongquesi/yueguangsi/jlz/sum_add_score/prop_bits/props[]/puzi[]`。孔雀石编号未确认（不猜）。当前账号状态全 0。


## 其它玩法/活动盘点（2026-09-18 静态配置实证，未实现的按"需抓包"标注）
- **猫咪旅行 = `mk/travel_Info_v_461.xml`**（不是 `card_user_spirite`）：`travel_elfInfolist`
  (小灰猫/豹猫…) / `travel_houselist`(120) / `travel_cardThemelist`(25，**产出套卡是
  「一起去XX」系列**，当前在期仅 theme=1774「一起去美容」2026-06-26→09-22) /
  `travel_day_tasklist`(id=100 每日任务 gift_limit=7) / `travel_petShoplist`(type=91,92) /
  `travel_toollist`(113) / `travel_activitylist`(8, 在期：lianxugoumai 09-09~09-22、
  maomisongli 09-16~09-29)。
- **端点已坐实（2026-09-19 抓包 + 零副作用探活）**：动作 `card_travel_elf`(POST，参数在
  body，**非 act 系** —— `act=1`→-1 system busy、`act=999999`→-1 XML 解析失败)；状态/配置
  `card_travel_elf_main`(POST，无参即返完整 JSON `code=0`，**自动化"读"已通**)；偶现
  `card_zhongkong_zk`(疑似活动/中控)；SWF 白名单 `card_travel_elf_main.card_travel_elf.
  card_user_vip.card_pack_buy_detail.card_act_send_free`。
- **READ 路径已通**：`card_travel_elf_main` 返回 `free_cnt`/`free_ts`(免费次数)、`pay_cnt`、
  `travel_elf_arr[]`(每只猫 `elf_id`/`travel_ts`出发时间/`travel_end_ts`结束时间/
  `travel_limit_cnt`/`food_id`/`food_num`喂食/`elf_lock`/`amulet_vt`护身符/`lucky_stone`
  幸运石/`travel_dui`/`travel_send_gift`)、`task_detail[]`(每日任务 type 81-85)、`jifen`、
  `clover_num`(三叶草)、`fenmo_num`(粉末)、`random_visit_cnt`/`random_visit_pet`(随机探访)、
  `recollection_piece`。→ 可据此算出"哪只猫在旅行、何时结束、免费次数还剩几"来驱动自动召回。
- **WRITE 已全部落地（2026-09-20，用户 HAR 含 Request Body + 实跑实证）**：全部动作走
  `card_travel_elf`(POST，参数在 body)。**⚠️ 注意 `card_travel_elf_main` 是状态端点：对任意 act
  都返回完整主状态(code=0)**，别拿它的 code=0 当"动作成功"（曾据此误判 act=4/33 在 main 成功）。
  act 全表：
  - `act=3` 配置（`travel_travel_food_arr` food_id 1-8 + 持有 num、`travel_travel_vt_arr`）
  - **`act=5` 出发**：`elf_id, food_id, food_num, amulet_vt, lucky_stone` → free_cnt 递减、end_ts +3600s
  - **`act=23` 收获/召回**：`elf_id` → `travel_gift`；该猫 end_ts 归零(=空闲)
  - **`act=10` 商店购买**：`buy_type(=1 食物), id, buy_num`。**⚠️ `id` 是 0-based：`id = food_id-1`**
    （实证传 id=6 买到 itemid 7，返回 content="91_7_1"）。**食物用三叶草买**（q1=360、q3=500/个）。
  - **`act=12` = ①拉「已开通猫咪旅行」的好友列表（只读）+ ②一次批量浇水**：`uins`(候选好友
    uin 用 `|` 连), `uin`(**自己，必须不补零** `int(session.uin)`) → 返回 `uin_tag_arr`
    = **已开通该玩法的好友**（服务端从全量里筛）。
    **⚠️ 关键实证（2026-09-20）**：① 传 194 全量 → 只回 **17** 条，且这 17 个在 194 里的位置
    **分散**（1,3,16,52,…,193）→ 是**筛选**不是截断；② 连调两次结果完全一致、main 的
    `travel_watered_arr`/`can_water_times` 均不变 → **只读**，可安全反复调用；③ 传 2 个回 2 条，
    传 16 个回 16 条（输入里开通的都回）。→ **浇水目标必须取本接口返回**，不能拿全量好友。
    **⚠️ 服务端把自己也放进 `uin_tag_arr`**（实测 17 里 1 个是自己的 uin）→ 目标须排除自己。
    好友候选池来自 **`card_user_list`**(`<user uin="...">`，本账号 194 人)。
  - `act=21` 单个好友（`opuin`）= **唯一真正浇水动作**（HAR 实证成功：`code=0 content=92_9_117`，
    且自己 `travel_watered_arr` 立刻新增 `{uin, ts=浇水时刻}`）。**错误码语义（实测）**：
    `-20205` = 不可浇/被拒（无三叶草好友、以及当前水面可用性都用它）、`-20207` = **该好友今日已浇过**、
    缺 `opuin` → `-1000`。**⚠️ 2026-09-20 傍晚实测 act=21 对可浇好友也返 -20205**（已复刻 HAR 全序：
    `card_badge_info`→`main(opuin)`→`act=21`；也试过 uin 不补零/空/去 uin/裸 post 仅 act+opuin，全 -20205）
    → 判断是**当日浇水次数已用尽**（`travel_watered_arr` 当日已有 2 条），非参数问题。次日/后续轮次再试。
  - **⚠️ code=0 短路坑**：`int(d.get("code", r.get("code", -1)) or -1)` 会把**成功**的 `code=0`
    变成 -1（`0 or -1`）→ 曾把"浇水成功"误报为失败。统一用 `_resp_code(r)` 取码。
  - `act=4` 收三叶草（无参）→ `clover_num`；**无可收时返 `code=-1 system busy`**（软处理="暂无可收"）
  - `act=32` 每日任务列表 → `task_detail[{cnt,index,type}]` + **`task_bits` = 已领取掩码**
    （bit i 置位 = 第 i 个任务已领；**不是"已完成"**）。判定可领 = `cnt>=limit 且 bit 未置位`；
    重复领会返 system busy。limit 表：{1:1,2:3,3:1,4:3,5:1,6:3,7:3,8:1}（travel_day_task XML）。
  - `act=33` 领每日任务奖：`id`(=index)；`act=26`/`act=7`/`act=29`/`act=47`/`act=52` 见 HAR（少用）。
  - **生命周期**：`end_ts>0 && now<end`=旅行中；`>0 && now>=end`=已返回待收获；`==0`=空闲可出发。
    **收获(act=23)是让猫回到空闲的唯一动作**。**手动收获**：`harvest_returned()/POST /api/travel/harvest`
    （不传 elf_id = 收全部已返回的猫；对旅行中的猫会明确回"还在旅行中，剩 Ns"）。
  - **✅✅ 免费次数口径（2026-09-20 定案，三层证据，别再改成 8）**：
    ① `free_cnt` / `day_limit_cnt` = **今日「已用」免费旅行次数，不是剩余**。HAR #48(14:02:20) 自己
    free=0 且无猫在旅行 → 14:02:35 出发 elf1 后 **=1** → 14:02:49 出发 elf2 后 **=2**。
    ② 每只猫另有 `travel_elf_arr[].travel_limit_cnt` = **该猫今日已旅行次数**，**每猫每日上限 3**。
    ③ **上限 = 猫数 × 3**：HAR #45 好友 327904123 的 main 满额帧 —— 四猫 `travel_limit_cnt` 全 = 3，
    同帧 `free_cnt = day_limit_cnt = **12**` = 4 × 3。
    → `free_limit = 猫数 × 3`（4 猫 = **12**）；`free_left = Σ max(0, 3 − 该猫已旅行次数)`。
    用户界面 17:00 显示"还剩 5 次"，与实测四猫已用 2/2/2/1 → 剩 1+1+1+2 = **5** 完全吻合（用户自行
    核出 7 已用 + 5 剩 = 12 总）。
    ⚠️ **`travel_house.travel_cnt_limit = 8`（1..120 级恒 8）不是这个上限**，语义另有所指；曾据此
    误判"每日免费 8 次 → 剩 1"，是错的。**唯一判定入口 `te.compute_free_counts(state)`**（UI 与出发
    循环共用），`resolve_free_daily_limit(cats)` 只是 `猫数 × 3` 的薄封装。
  - **三个独立池（SWF 面板标签实证）**：`.swf` 面板文案里有「**免费旅行次数：**」「**兑换旅行次数：**」
    两行 → 对应 `free_cnt`（免费，次日清零）/ `dui_cnt`（**兑换旅行次数**，来自商城兑换，不清零；
    每日任务 task_id=8 就叫「使用兑换旅行次数」）/ `pay_cnt`（付费次数，不清零）。SWF 常量含
    `travel_cnt_limit / free_cnt / day_limit_cnt / dui_cnt / pay_cnt / free_ts / dui_ts`。
    另有 SWF 文案佐证：「免费次数次日清零，付费次数不清」「月卡开通期间，每日0点后可领取旅行次数」
    「今日旅行次数已达到上限，明天再旅行吧」「[幸运事件] 每第10次旅行，返还本次消耗的全部三叶草」。
  - **free_cnt 口径旁证**：每日任务 `task_detail` index=1/2（task_text=「猫咪旅行次数」）进度 cnt
    **恒等于 free_cnt**。当日快照：free_cnt=7、上限 12 → 剩 5；dui_cnt=5、pay_cnt=11、task_bits=62。
  - **⚠️ 同时旅行上限 = 4**（用户确认 4 猫可同时旅行；此前误判的 3 是连续出发限流，非并发上限）。
  - **⚠️ 浇水规则（用户 2026-09-20 明确）**：① 只有**已种三叶草且未成熟**的好友能浇 —— 依据
    `act=12` 每条好友的 `data`：`None`=未种（不能浇）、`"<成熟ts>_<flag>"` 且 `ts>now`=未成熟（可浇）、
    `ts<=now`=已成熟（不能浇）；② **每个好友每天只浇一次**（用自己 `travel_watered_arr` 去重，
    重复浇返 `-20207`）；③ **每人每天被浇次数有上限** → 浇前读好友主页 `main?opuin=<uin>` 的
    `can_water_times`（= 他还可被浇几次，实测好友 6/10、自己 1/2），为 0 跳过。
    **`card_travel_elf_main` 带 `opuin` = 看好友主页（只读）**，返回的是**好友的**数据。
  - **⚠️ 限流**：连续请求会返 `system busy` → 统一 `_act()` 节流（最小间隔 1.2s）+ busy 退避重试。
- **食物目录 = `travel_Info_v_461.xml` 的 `travel_petShop type=91`（食物商店）**：
  food_id = itemid = `tool_food` 位置序 = `travel_tool.id`。8 种食物与**等级**（`tool_food.food_quality`，
  3=A级）：
  1 曲奇饼(1) / 2 寿司(1) / 3 牛排(1) / **4 夹心饼干(3)** / **5 饭团(3)** / **6 汉堡(3)** /
  7 回忆棒棒糖(1) / **8 回忆便当(3)**。→ **A 级正好 4 种 = food_id 4/5/6/8**（每猫各喂其一）。
  购买价：q1=360、q3=500（三叶草）。`resolve_food_quality()/resolve_a_grade_foods()` 运行时拉
  XML 解析（失败回退 [4,5,6,8]）。
- 实现：`backend/travel_elf_auto.py`（`run_once` 一轮：收获已返回 → 用**今日剩余免费次数**出发空闲猫
  各喂一种 A 级食物(不足则 `act=10` 买) → 收三叶草 → 给「已种未成熟且今日未浇」的好友逐个 act=21
  → 领可领每日任务；`_act` 统一节流重试）+ `travel_friend_uins.json`(194 好友，HAR 提取，仅回退用，
  好友池优先实时 `card_user_list`) + `backend/constants.py`(EP_*) + `main.py`
  `GET /api/travel/state`(免费口径/猫状态/食物/A级/每日任务/可浇好友) / `POST /api/travel/run`
  (food_num/harvest_only/dry_run/do_clover/do_water/do_tasks) / `POST /api/travel/harvest`(手动收获) /
  `POST /api/travel/clover` / `POST /api/travel/water` / `GET /api/travel/friends`(含每条好友三叶草状态) /
  `POST /api/travel/tasks`。前端独立页 `#pane-travel`（两栏 `.au-grid` + 已开通好友面板）。
  **已实跑验证**：猫4 买 2 个夹心饼干(三叶草 -1000)后出发成功；每日任务 task_bits 判定正确；
  三叶草暂无可收（软处理）；浇水筛选正确（17 已开通 → 1 可浇），act=21 当日被拒（见上）。
- **猫咪小屋 = `mk/catHome_Info_v_114.xml`**：`catHomeInfo_everyDayMatchlist`(9 项 =
  用户说的「每日比赛」：表演/外形/运动考核项目) / `catHome_Integrallist`(积分兑换) /
  `CatHome_Gachalist`(110 扭蛋) / `catHome_Shoplist`(15) / `catHome_Itemlist`(31 = 大类 92
  道具字典源) / `catHome_Equiplist`(96)。⚠️ **端点未探明**。
- **挖地 = `wadihuodong`**(活动表 id=3)：官方活动规则写明「参与**挖地层**挖地得数字 1/5/10/50，
  积分排行，09-09~09-22 21 点，领奖 09-23 00:00~09-25 24:00」。⚠️ 属独立 appid 353
  (`rc.qzone.qq.com/myhome/353`)，不在 card.show 体系 → 要另接一套。
- **在期活动**（activity_info_v_1275，2026-09-18）：68 mmfl(→09-22) / 3 wadihuodong(→09-25) /
  10 xiaofei1(→09-30) / 29 shizhounian(→10-04) / 31 zhounianrenwu(→09-29) /
  33 zhounianlibao(→09-29) / 53 yincanggoumai(→12-15)。
- **✅ 已落地「一键日常」**（2026-09-19）：`backend/daily.py` + `POST /api/daily/run`
  + 总览页「一键日常」卡。铁律：**ACTIONS 里只允许已实证动作**，每项 docstring 必须写
  「实证时间 + 成功码 + 重复领取码」。
  ① 每日登陆礼包 `card_user_money_draw`（**无参** POST）：code=0 → `money_plus=100` 金币/
  `days` 连续天数/`todayend`；同日再领 → **-33007**（判成功，别当报错）。
  ⚠️ XML 属性要自己 `ET.fromstring(raw).attrib` 取（`parse_response` 只留 code/message）。
  ❌ 排除：`card_act_send_free act=251` 只是**只读查询**（连发两次 cnt 不变），
  `card_act_get_free act=251`（含 id/index/cnt/type 各种参数）恒 -1 system busy（要 md5 签名）。
- **`EP_MONEY_DRAW`(card_user_money_draw = 每日登陆礼包) 已在 constants 定义但全项目零引用**
  → 最现成的"白捡"自动化点（同族还有 act=251 每日签到 / 185 连续登录 / 141 收集卡 / 11 任务）。
- **西游/三国不是猫咪产出**：`card_lib.theme_card_list` 里 56 三国风云 / 155 西游记 /
  266 西游降魔 / 1347 三国二十四名将 都是老套卡，跟「一起去XX」无关；想炼它们直接加进
  自动炼卡队列 tid 即可。
- 灵宠自动（`spirite_auto.py`）**三套玩法都有自动循环**：`_loop_map` / `_loop_island` /
  `_loop_across`，不缺。

## 猫咪探险（西游 / 三国大地图）· 2026-09-18 探明
- 入口：游戏内「每日比赛」→ 猫咪探险；西游、三国各是一个**大地图**，下面若干
  **小地图**，每个小地图 = 一套卡（如西游的「花果山」）。
- 端点：**`card_worldexplore`**（西游）/ **`card_worldsanguo`**（三国）/
  `card_travel_elf_competition`（每日比赛）。
  定位手法：`card_run_base`.version_list → `XiYouModule_v_41.swf` / `SanGuoModule_v_14.swf`
  → `zlib.decompress(swf[8:])` 解压 → grep `card_[a-z_]+` 取常量。
- 参数：**`levelid`（全小写！）**。`levelId` / `level_id` → -40006「错误的关卡ID」。
- act 实测（三国）：1=状态 / 2=领奖(101→-302 未达到领取条件) / 7=任务 /
  9=关卡进度表(56 关：1..9 小地图 + 101..104,201..204… 小节 + 5001..5014) /
  11=魔法钻购买(-201 魔法钻不足) / 15=排行榜。西游：1=状态 / 11=teamlist /
  13=guide / 15=任务；act=9 不存在。
- ⚠️ **「出发探索」的 act 未定位**（西游 act=2/4 恒 -30002「队伍忙碌中」）→ 只能只读。
- 小地图套卡（**按卡名推断，未证实**）：西游 1604 花果山/1605 天宫/1606 五庄观/
  1607 黄风岭/1608 莲花洞/1609 火焰山/1610 狮驼岭/1611 灵山（8 张）；三国 1745 黄巾之乱/
  1746 虎牢关/1747 界桥之战/1748 兖州之战/1749 官渡之战/1750 赤壁之战/1751 荆州之战/
  1755 夷陵之战/1756 五丈原之战（9 张）。别写成常量，等抓包验证。
- 只读接口：`GET /api/world/status?w=xiyou|sanguo`、`GET /api/world/levels?w=sanguo`。

## 挖地（`card_user_dig`，2026-09-20 抓包 + 真机逐格实测）· 已落地独立页
- 端点：`POST card_user_dig`（`card.show.qq.com/cgi-bin/` 可直调，响应是 **JSON** 不是 XML）。
  客户端 SWF = `appimg2.qq.com/card/swf/modules/WaDiModule_v_32.swf`（strings 有 `CAN_DIG_TYPE`/
  `CAN_NOT_DIG_TYPE`/`LIANTONG_DIG_TYPE`/`DIG_LINE_TNT_TYPE`/`DIG_JGG_TNT_TYPE`/`DIG_SUPER_TNT_TYPE`
  /`requestPickUpWaDiItem`；**常量数值取不到**，语义靠真机实测）。
- **act**：`1` 状态（只读）／`2` 挖格子(line,col,dig_type)／`18` 一键收获／`7` 收矿机(id,slot_id)／
  `4` 启动矿机槽。`9`/`15` 也吐 content 但语义未定论 → **未接入**（只接已实证动作）。
- **dig_type**：`1`=锄头(扣 `chutou`)｜`2`=一字型炸药(扣 `line_tnt`)｜`3`=九宫格炸药(扣 `jgg_tnt`，
  大范围、整屏下推地层)。⚠️ **2/3 只能扔在已挖开的空格 type=0**，对土返 **-2**，对岩石返 **-3**，
  对空地/未探索用锄头返 **-1**。`DIG_SUPER_TNT_TYPE`(疑似 4) 未验证 → 不接。
- **格子 type**：`0`=空地（**带 gift_* 才是「待收」，act=18 收**）｜`1`=特殊(挖不动)｜`2`=土(挖 1 次)｜
  `3`=石(**挖 2 次**：3→4→0)｜`4`=**石头挖了一半，继续挖**（早期误判为"待收"，已推翻）｜`5`=岩石(不可挖)。
- **未探索格(fog) 挖不动**：`gift_*` 字段**整个缺失**的格子 act=2 必返 -1，须等相邻格挖开把它点亮。
  → 可挖 = `known and type in (2,3,4)`；待收 = `known and type==0 and gift_type≠0`。
- 资源字段：`chutou` 锄头 / `line_tnt` 一字型炸药 / `jgg_tnt` 九宫格炸药 / `kuangshi_bi` 矿石币 /
  `sui_jinshi` 碎金石 / `cash` / `dig_index` 当前层 / `page_index` 窗口首行（8 行 × 7 列，随挖掘下移）。
- 奖励编号：44=金币 45=经验 46=五彩石 47=金粉 **48=卡片(编号=卡 id，走 `cq.cname` 取名)** 55=魔力
  65=锄头 66=一字型炸药 68=九宫格炸药 69=挖地套卡 71=现金（70_x / 76_x 未知 → 显示 `#70_5`，**不猜名**）。
- 限流：连续请求返 -1 "system busy"（act=1 间隔 2.0s 稳过；但 act=1→act=18 紧邻必挂一次）
  → `dig_auto._MIN_INTERVAL=2.4` + busy 退避重试（被拒响应不带状态字段，重试不会重复扣道具）。
- 代码：`backend/dig_auto.py`（act 封装 / `parse_grid` / `summarize_state` / `dig_cell` / `collect` /
  `collect_machines` / `auto_loop`+`auto_status`）；main.py 端点 `/api/dig/state`、`/api/dig/cell`、
  `/api/dig/collect`、`/api/dig/machines`、`/api/dig/auto/start`(max_digs,use_tnt)、`/api/dig/auto/stop`；
  前端独立页 `#pane-dig`（`renderDigGrid` 8×7 网格：可挖格点击挖、待收格点击收获、图例、工具下拉、
  自动挖 + 上限 + 允许炸药开关），`?v=20260920h`。验收：`tools` 式 jsdom 跑真实渲染 → 8 行 56 格。
- 自动挖策略：先收 → 挑**最深行优先**（往下推进才出新区域）、同深度优先带礼物的格子 → 挖；
  默认**只用锄头**，`use_tnt` 打开后没土可挖才扔九宫格炸药推进；连续失败 3 次或无格可挖即停。

## 挖地补遗（2026-09-21 实测，覆盖 09-20 段的旧结论）
- **dig_type 作用范围**：1=锄头单格｜2=一字型=**横向 5 格（中心±2）**，边缘半伤（石3→4）｜
  3=九宫格=**3×3（中心±1 行列）**，同时把 `dig_index` 往下推。落点一律必须是 **type=0 空格**。
- **地图窗口**：服务端只给 8 行 = `dig_index-5 .. dig_index+2`；`act=1&page_index=N`
  **实测被忽略**，翻页不存在。往下走 = 挖更深 known 格 / **用炸药炸开 fog 行**
  （fog 格用 act=2 单点必 -1，但**炸药可以**）。`push_down()` 取最深一行的 type=0 格、优先中间列。

### 挖地机制硬结论（2026-09-21 逐格探针实证，**设计算法前必读**）
1. **`dig_index` = 最深的「含至少 1 格空地(type=0)」的行**。不是「整行挖平的行」——
   实测只挖开 (16428,0) 一格，dig_index 就从 16427 跳到 16428。窗口随之整体下移一行。
2. **已探明 ⟺ 自身 type=0，或 4 邻（上下左右，**不含斜角**）中至少 1 格 type=0**。
   **精确 1 跳**，逐列生效。实测双向零反例。
   ⚠️ **不能靠「礼物字段有无」判断已探明**（2026-09-21 纠错）：服务端会**抽掉**部分行的
   gift 字段（实测顶行 3 格字段凭空消失、下一轮又出现，且礼物值原封不动）→ 必须自己按
   4 邻规则算。**雾格的 `type` 是真实值**（服务端照样下发）→ 可**提前规划绕开岩石列**。
3. **炸药能直接作用于 fog 格**（实测：九宫格落点 (16428,0) 把 fog 的 (16429,1) 直接炸成空地）。
   → 不必先"开路再探明"，炸药可越雾直炸。**锄头不能碰 fog（返 -1）**。
4. **炸药对土/石是「一击秒」**（九宫格 3×3 每格一次打平，石 3→0）；
   **对岩石(5)/特殊格(1) 完全无效** → **岩石与特殊格是永久墙**，只能靠"探明只需 1 个邻格"绕开。
5. ⚠️ **2026-09-21 定向探针推翻本段旧结论（原写「一字型固定 ±2、不能推进」）**：
   - **一字型 = 从中心向左右逐格各 1 点伤害，遇到「打完仍不是空地」的格子即中断**。
     实测中心右行：土→空、土→空、雾→空（**雾被直接炸平**）连续 5 格，直到遇 **石 3→4
     （未打开）→ 阻断**，石后那格纹丝不动。→ **范围不是固定 ±2，是「传播到阻断为止」**；
     石只要敲过一下（锄头 3→4）或再补一发，就能继续往后打。
   - **一字型不推进 dig_index**（落在全空行 → 成功但网格零变化）。
   - **锄头能推进 dig_index**：实测挖开 (16430,0) 的土（2→0），dig_index 16429→16430、
     page_index 同步下移。条件只是「该格已探明」（= 与空地相邻，用户口述与 4 邻规则一致）。
   - 九宫格 3×3、**一次打平**（土/石/半开石/雾全变空地），**能推进 dig_index**，是唯一
     "一次开一片 + 下推" 的工具。
6. **「压平」的真实语义**（2026-09-21 修正旧结论）：行滚到/滚过窗口顶后，服务端把它**整行
   type→0**（**连岩石 5 也变 0**），礼物字段被抽掉但**礼物本身还在**（下一轮字段又出现）。
   → 真正的损失不是"压平"，是**行滚出窗口后 act=18 收不走**。实测损失：金币×150、经验×30、
   积分×1（推 dig_index 前没先收）。**硬规则：任何推进动作之前先 act=18 清账。**
7. **act=18 一次只收「当前可收」的全部**，节流 2.4s 起，连发即 system busy。
   收的工具礼物**直接进对应库存**（实测 content `65_0_2` → `chutou` +2），**不经过 `new_prop`**。
8. ⚠️ **2026-09-21 纠正本节旧结论**：挖地工具有**四种**（`item_dict` 的 `types` 表）：
   `65`=锄头｜`66`=一字型炸药｜**`67`=超级炸药**｜`68`=九宫格炸药。
   **网格礼物**（gift_type = 大类、gift_id = 类内编号）里能出现 `66`/`68` 这些炸药，挖平后
   act=18 收进库存（实测 `65_0_2` → chutou +2）。
   - **超级炸药(67) 是「格子属性」不是可收道具**（用户 2026-09-21 澄清）：
     挖开后格子里出现，**点了就地炸开**；`act=1` 顶层只有 chutou/line_tnt/jgg_tnt，
     **没有超级炸药计数器** → 收不走。所以它**不算「待收礼物」**，
     `dig_plan.load()` 把 gift_type=67 记成 0，不参与「先清账再推进」的硬约束。
   - ⚠️ **爆炸范围 = 菱形**（用户口述）：**上下左右各 2 格 + 对角线各 1 格**
     → `|Δ行| + |Δ列| ≤ 2`，共 **13 格**（`dig_plan.SUPER_R = 2`）。**不是 5×5 方块**。
   - ⚠️ **两种传播规则不同**（用户 2026-09-21 补充口述：「**横向会被石头挡住，斜向能直接炸石头**」）：
     · **横向/纵向**（各 2 格）：**逐格传播，遇石头(3)/半开石(4) 即被挡住**
       —— 石头**不被破坏**、它后面的格子也打不到（土(2)/雾照常打平并可继续）；
     · **斜向**（`±1,±1` 四格）：**能直接炸石头** → 石头/半开石一次打平；
     · 岩石(5)/特殊(1) 两种方向都是永久墙，照旧炸不动。
     → 实际命中数**常常 < 13**。代码：`dig_plan.SUPER_BLOCK_AXIAL / SUPER_DIAG`，
     断言见 `tools/dig_plan_check.py`（横向挡石、纵向挡石、斜角炸石、斜角炸不动岩石）。
     ⚠️ 「纵向同横向被挡」是**推断**（用户只说了横向），若要改只需换 `SUPER_BLOCK_AXIAL` 的用法。
   - **从已探明格就能看出是不是超级炸药**（用户口述）→ 规划器只对「已探明」的炸弹格下 tier0。
   - SWF 常量链证实四种 dig_type：`DIG_CT_TYPE / DIG_LINE_TNT_TYPE / DIG_JGG_TNT_TYPE /
     DIG_SUPER_TNT_TYPE`（顺序对应 1/2/3/**4**）。
   - ⚠️ **`dig_type=4` 仍未实证**：在非炸弹格上连试 3 次都返 `-2 system busy`（与"落点非法"
     同码），当前网格也没有炸弹格可试。`dig_plan.candidates()` 走 `supers`（格子标记）+
     `BOMB_GIFT_TO_TYPE = {66:2, 67:4, 68:3}` 两条路，失败由 `auto_loop` 的 `bomb_bad`
     回退，**不写死为必然成立**。
   - 服务端 `dig_info` 单格实测只下发 `{j, type, gift_type, gift_id, gift_num}`，
     **没有炸弹标记字段**（SWF 里叫 `isSuperTnt`）→ 大概率「没有才不下发」，
     `dig_auto.parse_grid` 做**防御式探测**（`dig_plan.SUPER_KEYS` + gift_type==67），
     当前网格实测全是 False（还没有炸弹格出现）。
     顶层另有 `collect_bits` / `dig_gift_bits` / **`day_buy_bits`（= 挖地商店「每日限购」已买位**，
     2026-09-21 定性：该字段只在存在商店时才有意义，是「今天的购买标记位」，实测值 1）。
- **jd_macs = 鉴定机（不是矿机）**：`act=4` 的 `type` = 投的晶石 0/1/2（蓝/绿/红，
  顺序来自 SWF 文案与每日任务 74/75/76，**索引→颜色未逐条实证**），`id` = 机器序号，
  服务端自动占一个空槽；`act=7` 收到期槽（id + slot_id）。实测 act=4 不扣可见资源。
- **act 9 / 15**：9=领每日道具（锄头×10、一字型×5、九宫格×1，每日一次）｜
  15=领每日礼包（可连领多次：锄头×4 / 魔力×100 / 现金×20）。
- **act 10（文物上交，2026-09-22 已接通 + 一键全部）**：`num&act=10&id=<文物编号>` →
  content `69_<卡id>_<num>`（69=挖地套卡）。游戏里这功能叫「**兑换**」（古文物兑换卡片）。
  - **文物 = 76 类**（act=7 收鉴定产出得 `76_0_1`）。**id 空间只有 0..3 共 4 种**
    （对应挖地套卡按 tid 升序的**最早 4 套**：0=上古神兽 / 1=臧八宝 / 2=中华国粹 / 3=中华国粹(下)）。
    锚点实证：`76_0` → 卡 8396（套卡[0]）、`76_3` → 卡 9757（套卡[3]）。
  - ⚠️ **两种失败形态截然不同，是判断「id 存不存在」的唯一依据**（2026-09-22 **打乱顺序**实测，
    code **随 id 走、与请求顺序无关**，故非限流）：
    `code=-1`+desc=system busy = **服务端认得该 id**（存在、只是没库存，实测 id 0/1/2/3）；
    `code=0` +desc=system busy = **不存在该 id**（实测 id 4/5/20/33/999）。
    据此可**自适应探测**：连续 3 个「不存在」即停 → 正常只跑 id 0..6 ≈ 15s，
    而不是盲目遍历 34 套卡（那要 79s）。实现 `dig_auto.relic_submit_all` + `tools/relic_check.py` 离线断言。
  - ⚠️ **act=1 不下发文物库存**（原始 act=1 全部字段实测无任何 76 类字段），**没有只读接口**，
    仓库里有多少文物只能靠遍历探测。「一键上交全部」= 遍历 id + 每类 `num=1` 交到交不动。
  - ⚠️ 调用必须 `retries=1`：默认 `retries=3` 会把 `-1`（在 `_BUSY_CODES` 里）当限流退避重试，
    白等 3 轮；而 HAR 里连续两次 act=10 都成功 → 该接口不是严格限流。
  - ⚠️ 文物**没有独立显示名**（服务端不下发、SWF 文案也没有）→ UI 用「对应套卡」标注，**禁止猜名**。
  文物 = **76 类**（鉴定机 act=7 收产出得 `76_0_1`；`id` 即 76 的 sub-id 0/1/2/3…）。
  文物**不在** `new_prop`（那是 70 类材料）也不在 `jd_macs`，只在 act=7 收获响应里出现 →
  上交按钮挂在「收鉴定产出」结果上（识别到 76_x 即给「上交」）；另留手动「文物编号+数量」表单兜底。
  实证：`Downloads/wadi.har`（`act=10 id=0`→`69_8396_1`、`id=3`→`69_9757_1`；act=7 产出 `76_0_1`）。
  后端：`dig_auto.relic_submit()` + `jd_collect()` 附 `relics`（解析 content 里 type==76）；
  路由 `/api/dig/relic/submit`。前端：鉴定机面板「文物上交」区（#dig-relic）。
- **⚠️ 鉴定机一轮 `jd_auto` 必须自动上交文物**：收完 act=7 产出后立刻调 `relic_submit_all`
  （与「一键上交全部」同语义，含仓库里之前没交的；对无库存/不存在 id 是探测型、不扣次数），
  否则文物只收不出、永远堆仓库（2026-09-29 用户反馈漏交）。`jd_auto(submit_relics=True 默认)`
  返回 `submitted` 件数并拼进 `text`；前端 `digJdAuto` 只 log `r.text` + 刷新，无需改前端。
- **⚠️ 2026-09-23 用户指正：晶石鉴定（act=7）不是必得文物，也可能得「碎晶石」等**：`jd_collect()`
  现同时返回 `gains`（**全部** `type_id_num` 段，由新增 `_parse_gains` 解析，name 走 `_gift_name`、
  查不到回落 `#type_id`，**绝不硬编码碎晶石名**）；前端 `digMachines` 拆 `relicGain=76`(可上交)
  与 `other`(碎晶石等，已入道具箱) 分别展示。碎晶石在 act=1 是顶层经济字段 `sui_jinshi`、在 act=7
  奖励串里也可能出现（type/id 以实机为准，不猜）。
- **未实现的 act**：3/5/6/8/11/12/13/**14**/16/17 无参一律返 system busy（**已带 3s 节流复验**，
  不是限流假象）；≥19 返回空包体。⚠️ **挖地商店不在 `card_user_dig` 的任何 act 上**（见下条）。
- **挖地有自己的商店**（用户 2026-09-21 告知：**可以用现金买，也可以用金币、魔力**；
  **现金是挖地出产**）。SWF 侧证据（`WaDiModule_v_32.swf` + `ShopModule_v_24.swf` + `PropToolsModule_v_49.swf`）：
  · 常量 `BUY_CFG_ARR`（购买配置数组）、`DZ_CASH_PRICE`／`MB_CASH_PRICE`
    （**钉子/DZ、木板/MB 的现金价**）、`VT_ID_ARR`、`BAG_ITEM_SELL_PRICE`；
  · 枚举 `TYPE_WADI_CASH`（**挖地现金**，与 `_lgiftcash/_lgiftmana` 并列）；
  · 物品 id 常量 `ID_DINGZI / ID_TIECHUI / ID_MUBAN / ID_TIEKUAI`（**钉子/铁锤/木板/铁块**
    —— 与 70 类 6/7/8/9 的命名顺序**独立吻合**，是对上面名字表的又一条旁证）；
  · UI 类 `WaDiShopWindow / WaDiSingleBuyWindow / WndBuyTool / WndCashBuy`、
    响应结构 `Activity_Buy_Info`（`buy_info{buy_id,buy_index,buy_price,buy_title,buy_count}`）；
  · 中文文案：「**您的现金不足**」「单价:」「总价:」「**超过最大购买数量**」「每小时限发售100张」
    以及**工具 SKU 串**：锄头x10/15/20/25/30/40/50、一字型炸药x5…x40、九宫格炸药x2…x30
    （= 商店卖的整包工具）。
  ⚠️ **买工具的接口尚未定位**：不是 `card_user_dig` 的任何 act（全 system busy）；
    `card_market_mana_buy / magic_diamond_buy / midas_mana_buy` 探活返 `-1001 请重新登陆`
    （**host 或参数不对**，很可能是 `card.qzone.qq.com` 系或需活动参数）；
    `card_act_dig`（旧的挖宝活动端点）在、但返 `-1110 activity is end`（当前无配置）。
    → **要接通商店必须抓一次真实购买 HAR**（或按 SWF 的 `buy_index/buy_id` 参数继续探）。
  · 挖地内经济字段（act=1 顶层）：`cash`=**现金**（挖地出产，71 也是它）／
    `kuangshi_bi`=矿石币／`sui_jinshi`=碎晶石／`ksb_chou_cnt`=抽奖次数。
- **道具箱** = act=1 的 `new_prop` + `new_card`（id → 卡名）+ `vt_cang_small/mid/big`
  （仓库材料包）+ `vt_up_*`（升级材料包）。
  ⚠️ **2026-09-21 纠错（推翻本段旧结论）**：`new_prop[].id` 是**大类 70 的类内编号**，
  **不是** propCard 的 id。旧代码拿 propCard.xml 取名 → 挖地「道具卡」名字**全错**，
  被用户抓出。解析入口：`dig_auto.dig_prop_name()`（=`iid.item_name(70, id)`），
  **不要再碰 propCard**。
- **70 类 = 挖地道具/材料，共 11 件 = id 2..12**（实测 act=1 `new_prop` 正好下发 2..12，
  连 num=0 的也下发）。名字表从客户端 SWF 内嵌串里捞（官方静态配置查不到），
  证据链（**三重吻合**）：
  1. act=1 `new_prop` 的 num 与用户仓库截图**逐条相等**：3=114 / 4=111 / 5=79 /
     6=20 / 7=20 / 9=86 / 12=2 ↔「蓝晶石114、绿晶石110、红晶石78、钉子20、铁锤20、
     铁块86、铜爵2」；用户另直接说过 `70_5=红晶石`、`70_4=绿晶石`、`114 个是蓝晶石`。
  2. 名字来自 `PropToolsModule_v_49.swf` 的内嵌名字串，**一条连续的 12 条**：
     `帝王蓝瓷(文物) 蓝晶石 绿晶石 红晶石 钉子 铁锤 木板 铁块 葫芦万代盒(文物)
     细晶瓷(文物) 铜爵(文物) 紫晶石`。
  3. 把这条串按 id 2..12 对齐 → 7 个有数量的锚点（3/4/5/6/7/9/12）**全部落在正确位置**
     （尤其 `铁块=9`、`铜爵=12` 只能由这一种对齐解释）。
  → 结论：`2=帝王蓝瓷 3=蓝晶石 4=绿晶石 5=红晶石 6=钉子 7=铁锤 8=木板 9=铁块
  10=葫芦万代盒 11=细晶瓷 12=铜爵`，写进 `data/item_names_extra.json`（显示名不带 `(文物)`，
  因为用户截图里写的是「铜爵」）。
  ⚠️ **`紫晶石` 紧跟在铜爵之后，但 `new_prop` 没有 id 13 → 不算 70 类，暂不写名**。
  ⚠️ `WaDiModule_v_32.swf` 里另有一条**不含**钉子/铁锤/木板/铁块的同类串（少 4 项），
  那是**挖地奖励预览表**，**不是** 70 类的 id 表 —— 别拿它对齐（会把铜爵错标成 8）。
- 定位这类「官方配置里没有的名字」的工具：`tools/swf_abc.py <swf url|路径> <关键字>`
  （解压 SWF → 找 DoABC → 按长度前缀对齐解码字符串表 → 反向回溯到表头并打印邻居）。
  ⚠️ 坑：ABC 的 int/uint/double 池**不能按计数直算长度**（实测 `int_count=322` 之后全程
  失步）→ 该工具故意**不解析池头**，改用「长度前缀一致性 + 反向回溯」。
- **48_1148 = 积分**（挖地上下文）：挖地额外产出数字 1/5/10/50。activity_info id=3
  `wadihuodong` 的 `jf_name="积分"` + SWF 文案实证；`item_dict` 通用表给 48_1148 起的是别的
  名字，故 `dig_auto._DIG_GIFT_OVERRIDE` 显式覆盖为「积分」。**未知道具**现在为空。
  ⚠️ **2026-09-22 踩坑**：光在 `_DIG_GIFT_OVERRIDE` 里覆盖**不够** —— `reward_text()` 当时
  自己裸调 `iid.item_name(t,i)`，**绕过了 `_gift_name()`**，于是覆盖形同虚设，
  日志里积分一直显示成卡表里 48 类 1148 的卡名「**时髦抢镜**」（用户抓出）。
  **铁律：奖励串取名只有一个入口 `_gift_name()`（含 override + 48/69 卡名兜底），
  任何新代码都不许再裸调 `iid.item_name`。**
- 挖地奖励文案走 `dig_auto.reward_text()`（它内部调 `_gift_name`；**不要**用 `iid.summarize`）：
  后者对 48/69（编号即卡 id）没有卡名兜底，会把挖出来的卡显示成「未知道具#48_1148」。

## 礼物 / 变卡（`backend/gift.py`，2026-09-21 落地 · songli.har + 真机验证）

**域名不同**：这组端点全在 **`card.qzone.qq.com`**（不是 `card.show.qq.com`），
走 `mc_api.LoginSession.qzone()`（自动带 `appid=365`，判别 JSONP/XML）。

| 端点 | 方法 | 作用 |
|---|---|---|
| `freegift/freegift_send` | POST | 送礼：`friends="uin;uin"`、`freegift_id=81`（送 48 面卡） |
| `freegift/freegift_get_list` | GET | 礼物箱列表（上限 50，超出按 time 丢老件） |
| `freegift/freegift_get_frds` | GET | 好友列表（送礼 uin 池） |
| `freegift/freegift_take` | POST | 收礼：`action` + `index_id` → 产出 48 面礼物卡 |
| `card_user_transfer_card` | POST | 变卡（魔变）：`srcid` → `dstid` |

- **响应格式**：freegift 系列是 **JSONP 包在 `<html><script>` 里**（`frameElement.callback({...})`
  或裸 `_Callback({...})`），**以 `<` 开头** → `parse_qzone()` 必须**先抽 callback 再退化 XML**，
  否则会被 `parse_response` 当坏 XML 丢掉（2026-09-21 修）。成功码取 `ecode`。
  `card_user_transfer_card` 是标准 `<QQHOME code result>`。
- **收礼产出的 itemid 不固定**：`freegift_id=81+action2→7773(蚕丝)`、`82→7775(金丝线)`、
  `84+action1→7774(五彩线)`、（实测还见过 **7771**）。都是 **48 面礼物卡**（card_info
  theme=487：蚕丝/五彩线/金丝线/羊毛…）。⚠️ **禁止写死白名单**（曾写死 (7773,7774) 把
  真实成功的 7775 判成失败，卡死整条链路）。判定只看 `code==0 and itemid>0`。
- **变卡必须带源卡的「真实槽位」**：`slotid` = 槽号，`slottype` = **0 换卡箱 changebox /
  1 保险箱 storebox**（同 `card_plan.parse_user_cards`）。⚠️ 固定 `slotid=1,slottype=0` →
  **-32002「找不到相应的记录」**（HAR 里 7773 恰在 changebox slot=1 所以碰巧成功）。
  收礼产出的卡落在 **storebox** → 必须读 mainpage 定位真实 slotid/slottype。
  入口：`gift.change_any()`（自动定位）／`gift.take_and_change()`（收礼+变卡一步）。
- **变卡成功率约 50%**（服务端判定）：成功 = `code=0` 且响应带 `result="1"`；失败 = `code=0`
  但无 `result="1"`（**不是报错**，前端记为 warn「可再点一次」）。
- **变出的 40 面卡**（theme 1810：26116 移液器 / 26117 涡旋仪 / 26118 水浴锅）落进卡箱，
  自动炼卡引擎本来就从 changebox/storebox 取素材 → **引擎零改动**即可用。
- **道具卡变卡**（100/200/600 面值卡）：HAR 形态是 `prop=<id>` + `srcid=<id>` +
  `slotid=0/slottype=0`；`/api/props` 给出 `subtype`（7=100面值卡、8=200、10=600）。
- **接口**：`/api/gift/{friends,send,inbox,take,take_change,change}`。
  ⚠️ 2026-09-21 用户定调后**已删掉**独立页与 `/api/gift/auto/*`（不再做独立自动化）：
  送礼进「一键日常」、手动变卡进「卡箱」页、自动收礼+变卡并入「自动炼卡」勾选项。

## 礼物/变卡 的归位（2026-09-21 用户明确要求，**推翻 09-21 早先的独立页设计**）
用户原话要点：礼物变卡没必要单独拿出来；收礼+变卡应当**做成自动炼卡中的一环、可勾选**，
因为自动炼卡本来就在炼 40 面额的卡，直接变出来就**不用买卡也不用炼卡**。落地：
1. **删掉**导航「礼物/变卡」+ `#pane-gift` + `GiftAuto` 后台循环 + `/api/gift/auto/*`
   （前端 app.js 里整个 gift 模块与 `switchPane` 的 gift 钩子一并删除）。
2. **送礼 → 首页一键日常**（`daily.ACTIONS` 的 `sendgift`）：⚠️ 实测**每次调用最多 5 个好友**
   （`gift.SEND_BATCH=5`，>5 人会不生效）；**每天上限 50 次**（`gift.DAILY_CAP=50`，
   用户 2026-09-24 从游戏界面确认）。`_load_daily/_save_daily/gift_daily_remaining` 用
   `/tmp/mc_gift_daily.json` 跟踪本工具当天已送人数（按日期重置），跨多次点击/重启不超 50；
   游戏内手动送不计入，但 `freegift_get_frds` 额度用尽返回空列表时会如实报「无可送」。
   每日送礼按「当日剩余额度」从 freegift 好友池取前 N、按 exp 降序（活跃高等级优先）。
3. **手动变卡 → 卡箱页**（`#pane-cardbox`）：每张卡多一个「变卡」按钮（源 = 箱里那张卡，
   后端 `change_any` 自动定位槽位）；工具栏加「变卡目标」输入（默认 26117）+
   「用道具卡变」按钮（读 `/api/props` 选 subtype，请求带 `prop=srcid`，
   **走 `change_card` 不走 `change_any`** —— 道具卡在道具箱、不占卡箱槽位，HAR 里 slotid=0）。
   **目标卡只要是"面额比源卡小"的卡即可，不必和自动炼卡同一套**；某些套卡本身不支持变 →
   服务端直接拒，按返回码报错，**不写死支持清单**。
4. **自动收礼+变卡 → 自动炼卡的一个环节**：`AutoRefine.gift_drain`（默认关）+ `gift_dst/
   gift_upper/gift_lower`，在 `tick()` 的**第 1.5 步**（收卡之后、`# 2) 重新取状态` 之前）调用
   `gf.drain_gift_box()`：箱件数 ≥ upper(45) 时从最老的收礼+变卡，降到 ≤ lower(25) 停手。
   紧接的第 2 步本来就会重读状态 → **变出的卡自动进 stock，引擎当素材用**，不用额外重读。
   变卡 50% 失败只记日志，**绝不让炼卡主流程因此中断**。前端勾选项在自动炼卡面板
   （`auto-gift-drain` + 目标/满/止三个输入），参数经 `/api/auto/start` 的
   `gift_drain/gift_dst/gift_upper/gift_lower` 传入。

## 挖地自动算法（`backend/dig_plan.py`，2026-09-21 落地）
**分工**：`dig_plan.py` 只做规划（**纯函数**，拿 `dig_auto.parse_grid()` 的 rows 算下一个动作），
`dig_auto.auto_loop()` 只负责发请求 + 记日志。旧的 `_pick_cell`/`_pick_tnt_cell`（最深行优先 /
只在没得挖时才用炸药）**已删** —— 那套完全没利用新探明的机制。

**目标优先级**（用户 2026-09-21 原话）：① 不让未探明地块跑出范围（硬约束）→ ② 用最少工具
**先探明** → ③ 用最少工具**挖开所有有道具的地** → ④ **全部收取** → ⑤ 再向下挖
（炸药能直接炸掉石头，其他工具要两次）；**地里挖出的炸弹优先使用**。

**打分**（`plan()` 穷举「每个合法落点 × 有库存的工具」，取 `收益/代价` 最大）：
`1000×新探明格 + 500×新挖平的礼物格 + 300×挖平的是工具/炸弹礼物 + 150×新开空地(不含礼物格)`；
`代价` = 消耗工具个数（**锄头挖石 = 2**，其余 1）。零收益动作直接丢弃（否则会原地空转，踩过）。

**三档选择**：tier0 = 地里的炸弹（`gift_type∈{66,67,68}` 且未挖平）**无条件优先**；
tier1 = 所有「不会丢东西」的动作里取最优；安全池空时退而只做**不推进 dig_index** 的动作。

**硬约束实现**：动作模拟后算出 `D2 = dig_index`，检查 `行 ≤ D2-5+(margin-1)` 是否"全探明且
礼物格已挖平"，有例外就标 `unsafe` 并**排除**。`clear_margin` 默认 1（多留一行缓冲），
从前端「清账缓冲」传入。`auto_loop` 每轮开头只要 `pending>0` 就 `act=18` —— "推进前先收"
是防丢礼物的唯一保险。
- **离线验收**：`tools/dig_plan_check.py`（21 项断言：机制单测 + 真实状态单步 + 24 步推演
  「unsafe 命中必须为 0」+ 硬约束单测）。用真实状态跑需先把状态存成文件：
  `curl -s localhost:8000/api/dig/state > tools/dig_state_sample.json`。
- **真机一致性已验证**：同一份状态，离线推演的前 4 步（落点 + 探明数 + 下推）与真机
  `auto_loop` 实际执行**逐步完全一致** → 规划器的模拟模型对得上服务端。
- `dig_type=4`（超级炸药）在代码里是 tier0 + **失败回退**（失败的落点进 `bomb_bad` 不再重试），
  因为它**尚未实证**（见上面机制第 8 条）。
  自动循环：礼物箱件数 ≥ upper（默认 45）→ 收礼+变卡，降到 ≤ lower（默认 25）停，
  勾选「优先取礼物卡变」即启用（`GiftAuto` 后台任务）。
- **送礼进日常**：`daily.ACTIONS` 加 `sendgift`（`_daily_send_gift` → 送 48 面卡给全部好友）。

## 前端与工具链约定（2026-09-22 从 MEMORY.md 迁入，MEMORY 只留铁律、细节看这里）
- 改 `frontend/app.js` **必须 bump `index.html` 的 `?v=`**（当前 `20260923a`）。响应头
  `Cache-Control: no-store` →「界面没变化」基本**不是**浏览器缓存，别往缓存上推
  （已用 `curl` 比对本地/服务端 md5 证实一致 → 缓存排除后必须转向「渲染路径/DOM 是否真改了」）。
- **删 DOM 块必须 grep 全前端确认无残留引用**（`#pane-gift` 与整个 gift 模块已删，
  `switchPane` 的 gift 钩子一并删）；删完用「去 script/style 后数 `<div>` 与 `</div>` 是否相等」
  快速验 HTML 结构没被切坏。
- **同一文件的多处修改必须串行 Edit**（并行会互相覆盖，且两边都报 success），改完逐个 grep 校验。
- **一份全局日志、多处可见**：`log()` 渲染到所有 `[data-op-log]` 容器（总览 `#log` + 灵宠页全局日志）。
  **新功能的结果一律走 `log()`，不要再建页面私有的结果列表**（抽奖/翻牌已按此并入总览）。
- **切 tab 必须零请求**：`act=1` 一份数据即含三套玩法状态与解锁进度（缓存 `spLastData`/`spRecent`），
  `spSetMode()` 只用缓存重渲染。
- **分栏**统一 `grid-template-areas` + 每栏 `.ex-col`/`.sp-col`（`:nth-child(n)` 定 area），各栏独立滚动；
  ≤1240px 两行、≤1080px 单栏。**`.ex-side` 已删，别再引用**。长列表不设 max-height，滚动交栏内
  `.lst`（避免嵌套滚动）。现有分栏：换卡页三栏 `"main auto log"`；灵宠页三栏 `"set run glob"`；
  自动炼卡页两栏 `"run log"`（`.au-grid`）；猫咪旅行页两栏 `.au-grid`（`#pane-travel`，操作日志走全局
  `[data-op-log]`，无私有日志）；**抽奖/翻牌已并入总览**（`#pane-lottery` 与导航项已删，`#lottery` hash 重定向 overview）。
- 每页私有「本次运行日志」要有 `清空` 按钮并配一个 `xxxCleared` 标记：轮询（3 秒级）会把清掉的日志
  重新填回来，**只在下次「启动」时复位**（灵宠 `spRendered` / 自动炼卡 `autoLogCleared`）。
- **灵宠页 `#sp-all` 横向三格**同块显示三套状态，是唯一入口 + 状态 + 操作区：**当前选中格内**才显示
  该玩法专属操作（⚡体力 / 刷新 / 手动收[map·island] / 单次历练[across] / 开始·停止自动），其余格纯状态；
  切换零请求；按钮态由 `spPoll→spRenderAll` 按 `spAutoModes[mode].running` 刷新；`spHarvest/spOnce/
  spStart/spStop` 均收 `mode` 参数。**关卡号语义不同**：大地图 = `section_id`（章内 1..N），另两套 = `seq`。
- **改 UI 前先用 Grep 定位用户实际看到的渲染函数**：同一功能常有多条渲染路径（如「卡库合成路径」走
  `renderChain`+`card_chain`、「卡箱弹窗」走 `synMatHtml`+`synthesis`），凭记忆改会改错路径、
  用户反馈「没变化」。先 grep 函数名/调用处确认改动落在正确路径，再动手。
- **「重复/太乱」类反馈要往数据层想，别只改排列**：用户说「每个卡显示多次」时连改两轮只是换排布
  （嵌套树→平铺块），16 个配方块里同一张材料卡照样重复七八遍 → 真解法是**换数据形状**（每张卡只出现一次）。
- **套卡合成信息只有一套渲染**：`renderSynTiers` + `mountSynView` + `synSelect`（卡库套卡明细 / 卡箱弹窗共用），
  数据来自 `/api/card/synthesis` 的 `tiers`（按面值分层，每层 `{price,secs,cards[]}`，每卡含 `own/target/
  refinable/craft/mats`）。**每张卡只出现一次**，材料**在上方分层清单里就地变色框出**：选中卡 `.tchip.sel`
  蓝框、它需要的材料统一 `.tchip.mat` 橙框（**所有材料同色，不按配方顺序分色**；点基础素材卡则把用到它的
  父卡 `.pframe` 灰虚框），格内角标（`.mn` ×需求数 / `.mh.ok|no` / 可买时 `syn-buy`）；底部 `.syn-act`
  只剩一行「已选卡 + 耗时/持有 + 炼卡」。材料不属本套卡时兜底显示在动作行。旧 `renderChain`/
  `renderSynNodesHtml`/`synMatHtml`/`aggMats`/`fmtSynSecs`/`.chain` CSS 已删。
- **渲染类改动用 jsdom 离线跑真实渲染函数验收**（`NODE_PATH=<node workspace>/node_modules` 跑）：
  `tools/syn_render_check.js`（参数=卡 id，断言「卡片格数 == 套卡张数」）；
  `tools/change_dd_check.js`（stub `/api/cards/themes`+`/api/cards/theme/{tid}`+`/api/props` 跑真实
  `initChangeForm`，四级下拉全链路 27 项：解禁联动 / 换星级清下游 / 卡名不截断 / CSS 宽度规则）；
  `tools/dig_grid_check.js`（真实 `/api/dig/state` + `renderDigGrid`，断言 7 行×7 列=49 格）。
  ⚠️ harness 坑：`renderMaterials` 是 async（首次要 fetch props），采样前要等 ~250ms，否则把「还没拉到」
  误判成「无材料」（故 `initChangeForm` 开头**预热** `await propMaterials()`）；道具卡面值在**名称**里
  （「100面值卡」），`.pk-f` 是数量 `×N`，别拿它当面值。
- **「长列表选择器」一律别用原生 `<select>`**（500 个 option 不能搜、无分组 = 用户原话"根本不好选"）：
  改成「搜索框 + 可点列表」（CSS 见 index.html `.cb-pick/.pk*`）。
- **变卡选择器 = 四级内联浮动下拉**（2026-09-24 定型）：卡箱页 `#cb-change-bar` 一行四个 `.cb-dd`
  —— **① 星级 → ② 套卡（标记已有）→ ③ 目标卡（按面额升序）→ ④ 材料卡** + 「🪄 变卡」按钮 + 提示行。
  演进：09-22 三级下钻（大弹窗 `#change-modal`）→ 09-23 推翻弹窗改内联三下拉（**星级这级丢了**）
  → 09-24 用户指出后**加回星级，恢复四级**：`starSet` 由 `themes.map(t=>t.star)` 去重（过滤 star=0）升序生成，
  选星后套卡列表 `filter(t=>t.star===selStar)`；未选星级时套卡按钮 `disabled`，选星才解禁；换星清空下游选择。
  **下拉浮层宽度按最长名字自适应**：`.cb-dd-list{width:max-content;max-width:calc(100vw - 28px)}` +
  `.pk-n` **禁止 `text-overflow:ellipsis`**（保留 `white-space:nowrap`）—— 用户明确「名字显示全，不要用 …」。
  「星→套卡」用 `/api/cards/themes?transferable=1&limit=2000`（每套带 `star`/`n`/**`have`=已持有卡种数**/
  `theme_buyable`，「已有」= `have>0`、`have>=n`→「已集齐」）；「套卡→卡」用 `/api/cards/theme/{tid}`（按 `price` 升序）。
  ⚠️ **`transferable` 是套卡级属性**（不是卡级）→ 套卡可转则整条套卡的卡都能当目标；**可变套卡 664 套、
  可变卡 14208 张**，而 `/api/cards/q` 有 **500 上限** → 必须**按需拉**，别想一次拉全。
  材料卡：**道具卡段**（100/200/600，面值 ≥ 目标）+ **卡箱卡按套卡分组**（组内按面额升序，标「换卡箱/保险箱」）
  ——卡箱卡来自 `/api/state` 的 `changebox`/`storebox`，**自带 `theme` 字段**，无需额外接口。
  变卡材料**必须面值 ≥ 目标**（道具卡也一样，否则 -32002）；卡箱卡走 `change_any` **自动定位真实槽位**，前端只传 `srcid`。
- **挖地网格只显示 7×7**（2026-09-22 用户指出）：`/api/dig/state` 的 `rows` 是 **8 行**（dig_index−5…+2），
  **最顶那行 types 恒全 0**（行滚到窗口顶后服务端把整行 type→0），游戏不显示它 → `renderDigGrid` **取最后 7 行**。
  被隐藏那行仍在窗口内，`act=18` 仍收得到礼物（收获本就是全局 act=18），不丢东西。

## 每日比赛（`card_travel_elf_competition` / `_pvp` / `_union`）· 2026-09-25 bisai.har 实证
数据层 `backend/match.py`，独立页 `#pane-match`（subnav `data-pane="match"`）。**全部只读，无任何写操作。**

- **HAR 解析Tip**：Charles 导出 HAR 的 POST 体在 `request.postData.params`（list of {name,value}），
  **不是** `postData.text`；响应在 `response.content.text`（明文，未 base64）。
- act 表：competition **act=1** 主页状态（`travel_elf_arr` 猫 / `talent_score` / `performe_num` 每日期望次数 /
  `perform_limit_ts` 重置 / `clover_num` 三叶草）/ **act=6** 装备属性 39KB /
  **act=20** 金卡限购 `gold_buy_id1..6`；pvp **act=14,rank_id=0** → **日**榜 1000 人
  （`uin/nick/score/ce/ts/getzan/zan`，`day_ts`＝当天 0:00）；union **act=1** 工会 41 字段
  （`members` 是 `{uin: "等级,时间,昵称,贡献,…"}` 逗号串，**各段含义未证实 → 不拆分**）/ act=22 远征。
- **PVP 点赞**：`zan` 非空的恰好 10 人＝**今日可点赞对象**（`getzan` 恒 0），`my_zan_num`＝今日已赞数。
  HAR 里没有「点赞」的 act。榜单返回 1000 人但接口一次 78KB，**前端只取前 N**。
- ⚠️ **`opuin` 必须传纯数字**（2026-09-25 修正）：`session.uin` 是补零格式（`0004821992`），
  直接当 `opuin` 传，服务端照样回 `code=0` 但 data 从 29 key 缩到 11 key、`travel_elf_arr=[]`、
  `talent_score=0`。**「未开通」是传参错误造成的假象**，统一走 `match._plain_uin()` 去前导零。
- ⚠️ 本域响应**没有 `data` 包装**（字段在顶层），`/api/state` 那类才在 `data` 里；用 `r["data"]` 会取到 None。
- 后续补的三块（同一 HAR 里都有）：union **act=22 ＝ 公会战(GVG)** —— 我方 `level`/`progress`、
  敌方 `e_levelid`/`e_name`/`e_progress`/`enemy_uinon`、关卡领取位 `levelgetbits`、
  `gift_record`（已赠逗号串，含自己 uin＝今日已参与）；`card_worldsanguo`（**西游三国**）
  act=1 主界面（`levelId`/`freenum` 每日次数/`formation` 技能/`logData` 86 条关卡记录/宝箱数）、
  act=7 每日任务；`card_worldexplore`（世界探索）act=1 布阵 `formationList`/`currLevelData`。
  → `/api/match/{gvg,sanguo,explore}`，前端面板在 `#pane-match` 下半部分。
- ⚠️ HAR 里**没有「打比赛/提交成绩」的 act**，只有进页面 + 看装备 + 买金卡 → 写操作（打比赛/公会战出击/
  三国闯关）必须再抓一次**真实点击**的包（用户 09-25 当天任务已做完，约定次日补抓）。
- 字段语义未证实的（`dzs`/`ele`/`hbs`/`jhs`/`tadd`/union members 各段）**原样透出**，不硬译中文。
- 回归 `tools/match_page_check.js`：jsdom 真 DOM **45 项**（状态/猫咪/PVP/工会/金卡/公会战/西游三国渲染、
  手动按钮 busyBtn 即时反馈、6 个端点覆盖），
  运行 `NODE_PATH=<workspace>/node_modules <managed node> tools/match_page_check.js`。
  〈坑：新加的 `loadXxx` 要在脚本的 `return {}` 导出列表里显式加入并手动 `await` 调用，否则接口覆盖断言 FAIL〉

## 验收 / 构建 / 运维（2026-09-28 自 MEMORY.md 迁入，MEMORY 只留一行索引）
- **冒烟三件套**：`tools/mp_nesting_check.py`（DOM 祖先链 + div 配平）/ `tools/msub_check.js`（tab 切换，48 项）/ `tools/match_runall_check.js`（96 项）。jsdom 末尾必须 `w.close(); process.exit()`，否则 setInterval 让 node 挂住（exit 137）。**查中文关键词一律用 Grep 工具**，Bash grep 对中文不稳定（同日两次空结果假阴性）。
- **jsdom 盲区**：查不出「DOM 嵌套层级错误」和「CSS 层叠覆盖」→ 布局类改动必须**真实浏览器 CDP** 验收（getBoundingClientRect + 截图）。本机 9222 常驻 headless Chrome 带登录态可复用；自己新起的 headless 在沙箱会因 GPU 进程失败自杀。CDP 命令必须非沙箱跑；`/json/new?url=` 开不出页面，用 `/json/list` + `Page.navigate`。headless 有缓存，改完要 `Page.reload({ignoreCache:true})` 才生效。
- **只看宽屏量布局会漏 bug**（09-28 踩：本机容器 813px 一切正常，用户窗口略窄第 4 只猫就折行）。量响应式栅格一定要**额外把容器压窄再量一遍**（直接改父元素 `style.width` 比 `Emulation.setDeviceMetricsOverride` 稳）。
- **CDP 脚本：`ws.close()` 必须放在所有量测之后**。close 早于后续调用 → 请求永不回包，脚本**静默无输出**（不是报错，极难发现）。
- **cd 数配平 ≠ 嵌套正确**（错错相抵）：新增 pane/移动 DOM 后必须跑 mp_nesting_check。
- **整块替换 DOM 时边界必须含外层闭合标签**，按全文件数配平并与 git HEAD 对比。
- 大文件批量改造用 Python 脚本「整段函数替换 + 括号配平」一次落盘（见 `tools/reapply_busybtn.py`）。若 HEAD 不含当前功能则**不能 `git checkout` 回滚**。
- **同一文件多处修改必须串行 Edit**（并行会互相覆盖且都报 success）。
- **jsdom 里 `w.eval("await f()")` 的 await 不生效**（eval 非模块无顶层 await）→ 必须 `w.eval("(async()=>{ await f(); render(); })()")`，否则断言到初始空值（假 PASS）。断言要放在 setTimeout 回调内（顶层 await 还会触发 `ERR_AMBIGUOUS_MODULE_SYNTAX`）。Node 22 自带全局 fetch，别 require node-fetch。
- 静态 mount 必须放 API 路由之后：`app.mount("/", StaticFiles(html=True))` 在前会先吞掉 `/api/*`（GET→404、非 GET→405）。
- 重启后**只信新端点、不信 `server.sh start` 的 echo**（旧进程占端口照样打印 started，真因在日志 `Errno 48`）。
- **不要随便杀未知服务/进程**，端口/进程与预期不符**先问清**。`:8000`=本项目（重启 `server.sh stop/start`，stop 只杀 :8000 监听者）、`:8777`=agent-chat（用户常驻）、`:9222`=headless Chrome。沙箱 `ps` 被禁 → 用 `psutil`+`lsof` 或 `pgrep -f "port 8000"`。
- **纯前端改动不要重启服务**（会清掉内存里的引擎实例）。
- **本项目要正常提交，不要攒着**。提交前扫敏感词（cookie/token/password/session/Authorization）；多步改动按主题分多个 commit。
- 每日比赛页 5 个 tab（09-28 收敛）：猫窝日常 / 出战（比赛）/ 工会·公会战 / 西游记 / 三国战役。二级 `.msub-bar` + `switchMatchSub(key)` + 单个 `matchSubTimer` 按需加载；竞技场状态块住猫窝日常、日常停留随轮询刷新。二级 tab 栏要 `flex-wrap`（三个一长就换行）。

## 三国卡箱与卖卡（2026-09-28 实证，从 MEMORY.md 移入）
- 三国 `card_worldsanguo` act=1 `freenum`=今日已出战次数（3 封顶，剩余=3−freenum）。一键出战 `run_sanguo_raid`：act=16 读阵容→act=11（mode 0），失败即停，落盘 `data/sanguo_raid.json`，回归 `tools/sanguo_raid_sim.py`。act=9 关卡表是字典非数组；act=16 与 act=11 的 levelid 解耦。
- **关卡↔层**：levelid 末位 n 掉**卡库层 n** 的卡；**游戏界面层号 = 6 − 卡库层号**（游戏第1层=卡库层5 boss 1 张黄巾起义=张角关/205 掉；游戏第5层=卡库层1 八张）。每层两套各 8/5/4/3/1 张。对外一律 `_sg_g()`；推荐打**同大关**小关（204→202 补层2 太平道）。
- 卡箱：act=1 带 ex_box_data(15)/safe_box_data(12)；**卖卡 act=5 `cards={箱子}_{卡位}_{卡id}`，0=关卡卡箱、1=保险箱**，错参回 -311 安全。重复判据=**同名卡重复**（位置值只编码层）；收集进度按卡 id **跨两箱去重**（he+hs 相加会把缺卡顶掉）。推荐按「单套+层」挑 gap 最小；当前关只认顶层 levelId（levelData[0] 是旧残留）。上交整套接口未抓到，别盲试。实现 `plan_box/plan_sg_layers/sanguo_sell/auto_sell_spare` + `GET /api/match/sanguo-box`，回归 `tools/sg_box_sim.py`（40 项）。
- union（端点 `card_travel_elf_union`）。**09-29 定稿的 act 语义（经四次纠错，均以用户实证为准）**：**1**=工会信息(读)；**14**=按 uin 批量查公会归属(**只读**，游戏里没有「全员点赞」)；**15**=**摇水果计数(读)：num=今日已摇、resetnum=每日上限 3**；**16**=**摇水果重置(写)：返回 5 位数水果布局（解读未证实仅如实显示；用户定调每摇前重置 1 次）**；**17**=**摇水果(写，每日 3 次，第 4 次服务端 busy 拒绝；此前误标「领奖」)**；**19**=抢红包列表(读)；**20**=抢红包(写，{id:1|2|3})；**21**=**商店兑换(写，对应 act=30 商店；每周刷新 1 次、每件 maxnum=1、无日限额——服务端从不拒绝即因周库存；每日兑 1 次是用户策略=完成「商店兑换 1 次」每日任务，多兑白烧周库存 → 本地策略闸 1 次/日；此前误标出兵/摇水果/捐献/红包石兑换)**；**22**=公会战信息(读)；**23**=公会战出战(写，{cats,eqs}→battle_data.iswin，耗挑战次数，此前误标换装)；**25**=候选对手列表(读)；**27**=对手名单(读)；**30**=商店(读)；**39**=读阵容(读)。**公会战挑战次数没有服务端字段**（15 的 num 是摇水果），只显示本工具已出战次数。
- **抢红包全链路（2026-09-29 hongbao.har 实证）**：**3 个红包对应 redpkggetlist1/2/3**（每份=`uin_金额_时间戳_昵称` 已领记录），**自己 uin 不在 listN 里 = 第 N 个未领**；领取写接口 **`act=20 {id:1|2|3}`** → `content="92_62_6"`，领后 listN 多出自己。已领判定后只发未领的，不盲发。实现 `union_redpacket_grab`。
- **一键工会最终形态（09-29 四次修正定稿）**：`run_union(do_shake/do_exchange/do_redpacket/do_battle 全默认 True)` = **摇水果(16+17)+金币兑换红包石(21，每日1次)+抢红包(19+20)+公会战出战(23，ensure_mood 后真打)**。摇水果/出战均有**本地硬闸 3 次/日**，兑换红包石**硬闸 1 次/日**（`data/union_quota.json` 跨日清零，超限不发请求，battle 超限连喂猫都不做）+ 服务端限额双保险。路由：`union-shake`(act=17)/`union-exchange`(act=21)/`union-battle`/`union-redpacket`/`union-all(shake/exchange/redpacket/feed/battle)`/`union-quota`(只读计数)。⚠ 旧 `/api/match/equip` 路由仍调 act=23（每次点击真打一场），待用户确认处置。
- act=17 摇水果第 4 次=-2 busy（服务端限额实证）。act=14 竞技榜(pvp) 1000 人乱序无名次→后端按 score 重排；可赞对象每天轮换；日榜 tab 已删，竞技信息并入猫窝日常。pvp 的「点赞」(pvp act=22 带 rank_uin)是竞技场功能、与 union 无关。
- pvp act=22 点赞必须带 `rank_uin`（act=14 榜里 zan 非空条目取），只发 rank_idx 会 busy 且无效果；competition 端参数不同，照抄 HAR 会静默失败。
- 每日比赛 act=4 的 `id` 从 act=2 实时读，每场都变，绝不写死；**穿装 = `act=4` 的 `ids=<4件最优装 '|' 连接>`**（bisai1 实证 `ids=10663|20463|30122|40141`），由 `best_equip(衣橱 act=6, 主题)` 算，`run_match_all` 每场重算重发——这就是用户说的「一键穿装」。**竞技(竞技) 穿装 = `act=27`（持久化，无参数，服务端自动给竞技猫穿最优装并回写保存）**：bisai2 实证 `act=1`(开对手列表)→`act=27`(一键穿装)→`act=3`(设出战猫阵容 ids=3,4,2)→`act=8`(打)。**竞技穿装与每日比赛不共享、且持久保存**（capture 里开局猫已穿好、全程 equip_arr 不变 = 已保存），故 arena_auto 只在开局调一次 `act=27`，不必每场重穿。⚠️ `act=27` 我曾在 09-30 误判成「只读」——它无参数返回 equip_arr 实为服务端算最优装后回写，是写动作（持久化）。**2026-09-30 活会话实测坐实**：实调 act=27/act=3，二者调前调后 equip_arr 均不变；但 cat3 当前装备 `[10493,20473,30403,40353]` 与 `best_equip(今天 week_theme {3:20,4:20,7:20,8:20,11:20,12:20})` 算出的最优**完全一致** → 证明猫已被穿成新主题最优，act=27 确为写动作且幂等（已最优再调即不变，这正是「调了没变」的真相）。`act=3(ids=3,4,2)` 是设 `battle_arr` 出战阵容，**非穿装**（用户曾猜 act=3 是穿装，实测否）。**用户抓包序列 `act=27 → act=2` 中的 `act=2` 即 bisai2 的 `opuin=4821992`（看对手详情），非穿装写动作**——一键换装是 27 写 + 2 顺带看对手展示。唯一「手动单件装备」写动作是比赛 `act=29 equip_id=<单件>`（非一键最优）。抓包在 `~/Downloads/bisai*.har`。**2026-09-30 用户改衣后第 3 次活会话复测**：单独调 `act=27` 前后 3 猫 `equip_arr` 仍完全一致（幂等，因当前已是最优）；`act=2(opuin)` 也非写。→ 结论维持 `DRESS_ACT=27`。要证伪「27是写」只有一招：改**竞技衣橱里某件处于当前最优组合的装备**后再调27，看变不变（本次不变是因为用户改的衣服不影响竞技当前最优 / 或改的不是竞技衣橱）。
- arena_auto（PVP）的 `dress_cat()` 调 `DRESS_ACT=27`（竞技一键穿装，持久化）：开局只调一次（`dress_cat()` 无参数，服务端自动穿最优装并保存），**严禁每场重穿、严禁复用每日比赛 best_equip/act=4**（两套机制不共享、竞技持久每日比赛不持久）。改完须重启 :8000（无 --reload）。
- **竞技主题（每周三更新、持续一周）= `pvp act=1 week_theme`**（6 项 `{id,val}`）。显示在猫窝日常「竞技比赛」块（`home-arena-theme`）：后端 `/api/match/pvp-extra` 路由对 `state.data` 加 `week_theme_text`（复用 `match._fmt_week_theme` 转中文，如「朗诵 20% · 演奏 20% · …」），前端 `renderHomeArena` 读它渲染。注意：竞技主题与每日比赛主题（`competition act=2`，每场随机）是**两套不同数据源**，别混。改前端 bump `index.html` 的 `?v=`。
- **心情前置铁律（09-27 定调 + 09-29 收紧）**：所有「用到猫的一键功能 / 猫出战」出战前都必须先 `ensure_mood(MOOD_TARGET=80)`（= `cathome.feed_smart` 把猫喂到 80，不够才喂、够则跳过），喂失败即中止、不烧次数。已接：competition_fight / run_match_all（比赛）· pvp_fight（PVP 挑战）· run_sanguo_raid（三国出战）· union_battle（公会战出战 act=23，出战前 ensure_mood）· run_xiyou（西游出发）· arena_auto（竞技）。摇水果(act=17)不带猫、不需喂猫。**新增任何猫出战一键都要先 ensure_mood，禁止漏**。
- **竞技 arena_auto 的 system busy（09-29 三次修正·以落盘为裁判）**：每天 free_pvp_num 上限 4 场（surplus=剩余）。初版把 busy 当纯限流；二版加 3s 冷却+重试仍一墙失败。19:04 落盘实证：quota_before free=2/surplus=2/score=1911 → after free=4/surplus=0/score=1933(+22)，**服务端实际又打了 2 场、本地只记到 1 场**——fight 返回 busy 时**服务端可能已处理**（扣次数+加分，响应却 busy）；且 surplus=0 后 busy=「次数不允许」非限流（铁律：失败 ≠ 限流）。现行逻辑：busy 一律先回读 act=1——free_pvp_num 增加→如实记「战斗实际已发生（胜负以游戏内为准）」并计 fights；surplus=0→立即整轮结束；整轮 busy≥3（BUSY_MAX）停；free_seen 跟踪已打场数。HAR（bisai2）成功连打间隔 2.6~3s，FIGHT_COOLDOWN=3.0 保留。改完须重启 :8000（无 --reload）。
- **工会次数显示铁律（09-29 四次修正·定稿）**：union act=15 `num/resetnum` = **今日已摇水果/每日上限**（实锤：act=17 三次成功后第 4 次服务端 busy，num=3/resetnum=3）——**不是公会战挑战计数**（今日挑战恰好也用完 3 次才造成误判）。`renderGvgFightCnt` 挑战只显示「本工具今日已出战 N 次」（localStorage）；`renderUnionShakeCnt` 摇水果剩余优先用 act=15 服务端真值。禁臆造服务端剩余量，以游戏内为准。**方法论教训：把某 act 标成「限 N 次的玩法」前，必须先拿到「服务端会拒绝」的证据；「每次都成功」本身就是排除限次玩法的证据**。
- **工会每日限次硬闸（09-29 定稿）**：摇水果(act=17)与公会战出战(act=23)本地限额 3 次/日、**金币兑换红包石(act=21) 1 次/日**（`SHAKE_DAILY_LIMIT`/`BATTLE_DAILY_LIMIT`/`EXCHANGE_DAILY_LIMIT`），计数落盘 `data/union_quota.json`（跨日自动清零，周一自然恢复周 7 次节奏），**超限直接不发请求**（battle 超限连喂猫都不做；exchange 已兑当日一键里该步显示 blocked 并跳过）。只读查询路由 `GET /api/match/union-quota`。**本地计数只统计本工具发起的请求，游戏内手动操作感知不到**，真实剩余以游戏内为准。

## 西游记探索（2026-09-28 xiyou.har 实证）
- 端点 `card_worldexplore`（⚠ 曾与 `EP_WORLD_XIYOU` 同值并存是坑源，已删后者）。**西游 ≠ 三国，代码分开**。
- act 表：1 主状态（currLevelData/logData/formationList/freepowerbit/shoplist/ex|new|safe_box_data/maxLevelId/nlb,sjb,txd,vlb）；5 出发 `type=3&time=5&levelid=N&teamid=0`（type=3=高级探索，最长 24h）；6 收收获点 `levelid&index&getall=0`（24 个，回 `content="121_卡id_1"`、isgetall）；9 卖卡 `cards=箱_格_卡id`+快照（box=2 `new_box_data&new_box_num`；**box=1 safe 同构参数 09-29 盲试 8/8 成功**：cards=1_格_卡id+safe_box_data+safe_box_num；ex 未实证）；15 每日任务 task_bits/task_detail；16 领奖 `id=N`（回 content="48_1599_10,92_32_1…"；**busy 可能=已领**：服务端已处理响应却 busy，bits 为证）；20/24/26/28 只透出；7 商店 buyid（产出材料，未接一键）。
- **大关↔套卡（实证）**：levelid=N ↔ theme 1603+N（1604 花果山/1605 天宫/1606 五庄观）。层号口径同三国 `_xy_g()=6−卡库层`。
- 错误码 -30009「您的卡箱已满，请先清理西游卡箱」= 收获点前满箱信号，run 流程先卖重复再收。
- **task_bits = 已领掩码**（HAR 实证：每成功领一个置一位，响应 bits 1→5→7 递增；09-29 曾读反成「可领」→ 误重领、漏显示）。领过就跳过 act=16。
- **收获点要等探索跑完才解锁**：探索中 act=6 返回「关卡未完成」（非瞬时错误，别重试）。run_xiyou 加 `remain>0` 守卫直接跳过收获。
- **run_xiyou 顺序（09-29 用户定调）**：收完上一轮 → 出发新 24h（running 时不重复出发）→ 领任务奖；卡箱满先**跨箱**卖重复（new+safe 合并计数留 1 份，低级卡=卡库层号小先卖）。

- 三箱：new(15格带ts)/safe(17格 ts全0)/ex(6格)。**dupes 按 card_id 聚合**（同卡同箱 3 份只算 1 组、卖第 2/3 份；按名字再扫会把 3 份拆 2 组、卖点指错——与三国「同名才重复」方向相反，西游必须 card_id）。
- **真机 formationList 是两层嵌套 [[{catid,ce,eqlist,index}]]**，`_formation_flat()` 取外层第 0 项，否则前端布阵全 undefined。
- 实现 `backend/xiyou.py`（xiyou_state/task_list/plan_xiyou/start/get/sell/claim_task/run_xiyou/_xy_clear_dupes，落盘 data/xiyou_run.json）；路由 `/api/match/xiyou(-start/-get/-sell/-task/-run)`；`raw` 是 plan_xiyou() 输出（非 act=1 原始包）。回归 `tools/xiyou_sim.py`（23 项，FakeSession 按 HAR 行为回话）。
- 踩坑：_resp 的 code 两级查找（顶层/被 _json_of 提到 data）；套名在 theme_card_list（1681 条）不在 theme_list；_XY_THEMES 必须 global。
- 待用户拍板：三国一键出战是否优先打推荐小关；西游商店买能量是否接一键。

## 三国「关号 ↔ 大关 ↔ 层 ↔ 套」实证（2026-09-29 真机核实，改推荐逻辑前必读）
- **关号编码 = 大关×100 + 卡库层号**（实证链，非推测）：
  · `act=9` 返回 56 条 id：`1..9`（9 张小地图）+ `101-104` / `201-204` / … / `901-904`（9 大关 × 各 4 小关）+ `5001-5014` 计数位。**每大关只有 4 个小关**。
  · 102 关 09-28 13:36 的 `sitem=126_25107_1`（25107 黄巾力士，套 1745 pos2502＝卡库层 2）与 `ex_box_data` 里 25107 的 `ts=1790573738` **完全同秒** → 102 掉卡库层 2 的卡。
  · 203 关（logData 5 次）× `ex_box_data` 25131「吕布投董」（套 1746 pos3503＝卡库层 3）ts=1789470753 吻合 → 203 掉卡库层 3。
  · ⇒ **末位 = 卡库层号；首位 = 第几大关**。
- **套 ↔ 大关**（套名来自 `theme_card_list`，权威）：**第 1 大关 = 套 1745「黄巾之乱」**（16 张，层1 8张退守广宗…层5 1张黄巾起义 25116）；**第 2 大关 = 套 1746「虎牢关」**（17 张，吕布线：层1 温酒斩华雄/三英战吕布/赤兔追风/方天画戟、层3 吕布投董、层5 董卓进都）；第 3 大关 = 1747「界桥之战」。
- 真机现状（09-29 09:13）：`levelId=204`（第 2 大关层 4），`logData` 93 条分布 `101:65 / 102:19 / 103:4 / 203:5`，**104 一次没打**；
  合并卡箱+保险箱后 **1745 缺 25108 太平道（层2）+ 25109 天下大吉（层3）** ⇒ 用户「第一大关还没过」**实证成立**。
- **★ 推荐逻辑 bug（本次挖出，尚未修）**：`plan_sg_layers` 把 1745 与 1746 一起塞进 `spec`，然后**一律用 `chapter + layer` 生成关号**（chapter=当前大关=200）。
  后果：1745（第 1 大关）的缺口被标成第 2 大关的关号 —— 真机推荐输出 `theme1745 layer2 levelid=202 缺「太平道」`，
  但 **202 掉的是 1746 层 2 的卡（潘凤/祖茂/袁绍成盟主/诸侯会盟），永远补不到太平道**；要补太平道/天下大吉必须打 **102 / 103**。
  正确做法：关号需按**该套所属大关**生成（1745→100+layer、1746→200+layer），不能共用 chapter。
  历史坑源：REFERENCE 旧文与 commit 信息里的「204→202 补层2 太平道」即此 bug 的产物，**已作废**。
- 关卡**名字**（「吕布」等）不在 act=1/act=9 里（只有 bossid=数字，与 levelid 同值）；本地无 SWF 资源可查 → 界面标题与掉落卡不同源，别拿卡名反推关卡名。


---

## 2026-10-30 归档：MEMORY.md 压缩前原文（22KB，逐字保留，压缩后主文件只留铁律+指针）

# 魔法卡片助手 · 项目长期记忆

（深细节：`REFERENCE.md`、`docs/engine_v2_仿真报告.md`、`tests/`、`git log`；流水：`YYYY-MM-DD.md`。本文件只放铁律与已实锤结论）

## 方法论
- 现象>推测：冲突以现象为裁判；禁单点采样下全局结论、禁把推测写成常量。
- ★授权边界：写操作兜底先回查定调原文（`git log -S`）。高面值卡永不自动卖，只卖 10 面底卡。
- 编号撞车：同 id 不同字典含义不同，先确认大类与 id 空间；奖励串取名只用 `dig_auto._gift_name()`。
- `card_*` 的 opuin 必须纯数字（`match._plain_uin()`）。`card.qzone` 无 data 包装 → `json.loads(response.content.text)`。
- 写动作探活：`act≠查询` 必先看抓包（曾烧租箱卡 6 张）。失败≠限流；配额字段禁臆测，整份落盘。
- ★多套并行=共享资源（卡箱·炉位）→ 第一动作推 scheduling 边界（空炉、溢出窗口、箱够不够、双套死锁）。
- ★★ **多套并行首测（2026-10-28）**：健康——无死锁、炼次/买卡 = 各套单跑之和（无重复活）、`178+85+290` 那次 err（箱满买不进）靠满箱死锁修复兜住、slow but done。**但 makespan 对 tids 顺序敏感**：三套全排列最优 178+290+85 **16.29h** / 最差 178+85+290 **20.64h**（差 27%）；85+290 差 3.14h、178+290 差 1.26h、85+178 顺序无关。生产 `queue=list(tids)`=前端勾选顺序 → 真实存在。★**无单一静态规则**（慢套先/箱峰值低者先/buy 量 都在别的组合反向），且**箱不是瓶颈**（更优解峰值反而更高、1/1303 tick 顶满）→ 真因是 4 炉位前期分配 = 在线 RCPSP。→ **不做经验排序补丁**（同「优化初始序无效」铁律），根治需发 op 时跨套关键链优先（大改）。
- ★本质=**在线 RCPSP** → 滚动重规划（每决策点从当前态重算）；禁 god-function（planner/executor 分离）。

## 各玩法（API 事实，细节见 `REFERENCE.md`）
- 卡箱：changebox **18 硬顶** / storebox 200（cur=168 真上限）。40 面卡是每套消耗的素材，sets 只放大顶层收藏卡。
- 变卡：失败也烧源卡；礼物卡只变 40 面；主排序=持有少优先。★魔变：材料面额 **≥** 目标，仅 100/200/600 面道具卡可当材料，百变卡 #1162(9999) 万能。变卡排除挖地/西游记/三国战役，合格目标 34→17 套。
- ★★ `theme_compose_list` 行 = `[tid, state, target, m1, m2, m3, secs]`（r[0] 是**套 id 不是卡 id**）。判上下架/可变用 `card_query.list_themes(limit=5000)` 的 `offline`/`transferable`/`composable`（默认 400 老套查不到）。
- ★net_plan：每张套内卡（含 40 面中间层）按 sets 算 1 份需求。★自动炼卡：炉 freed 优先拿箱内已有卡合成上层，绝不现买现造缺的 40 面；两阶段（先 `allow_buy=False` 只合成）。
- ★偷炉选友：`card_user_list` uin 升序、含 `exp` 无 level；`_pick_steal_friend` 从 `friends_by_exp[:12]` 扫首个有空主炉者。
- ★简易箱 `card_user_temp_slot`：去 `appid`、Flash 头、`g_tk` 由 skey 算、POST `act=1`。**code 不可靠**（成功也返 -1/-2/-3）：收卡成功=收后炉 card_id 归 0；投卡成功=card_id>0。`act=3` 挪卡 type=1(简易→保险箱)/0(反向)。
- 判满唯一真源=`card_collection_get` 的 `qqshow_str`；`cnt`/`num` 都不能判满。套卡「已炼 N 套」=`card_mini_get` 的 num，show_total 真源=`theme_card_list` 的 t[9]。
- 珍藏阁 `card_user_zcg`：6 铺子/3 免费炉，只投 10 面（280/304/307 是素材），绝不调加速。灵宠邀请 `act=9` 发起→~1min→`act=10` 领取，每天 3 次。

## ★ 买卡铁律
- `_prepare_turn`：**就地为一炉补买，绝不预囤**（真机囤卡后果：bought=636/炉全空）。
- DP 版 `jit_need` = 骨架头 n 波 refine op 的 feeders 缺口并集；不在其中不买、箱+炉+在途≥缺口不买、同 tick 同 cid 最多 1 张。`n_wave=max(due_window, base_quota//3)`。★两侧悬崖：`2×due_window`(8)→3★ 277h 死锁；`base_quota//3`(1~3)→2★ 27.02h。
- ★★ **buy_free 三条**（da75c6c）：①兜底买卡判据必须「非底卡全在箱只差底卡」，不能用「炉全空」；②骨架里买卡不占 shop 槽但有 1 tick 到货延迟；③**执行层加门一律更差**（超采/收藏留底/同 tick 同 cid/骨架错峰/sets=1 全试过）——剩余差异在 solver `vec_prio`。
- ★★ buy op **双重身份都不能留 op**（aa5ad66）：留 op 会让 cursor 钉死索引 0。正确=删 op + `buy0_map` 兜底；`exhausted` 必须要求 `len(ops)>0`；空计划用 `state_sig` 触发重解。
- ★★ **改口径必须全链路同口径**（aa5ad66）：UB / dfs 买卡 / `_chain_avail_rel` 下界 / 执行层 sametick 四处一起翻，否则 UB 变紧→剪枝更狠→退化成 UB 贪心。
- ★★ ~~多套亏损第二处缺陷：中间层 `net_plan` 漏算~~ **已证伪（00bfd88）**：`need={cid: max(1,sets) for cid in self.ids}`（2026-10-11 的 all-ids 口径）已覆盖中间层收藏需求。8 个手算场景逐一吻合；`tests/_verify_netplan.py` 10 套型×300 随机状态全守恒（含「底卡不得多买」严格判据）。★判据只查缺不查余——在炉/库存超量的卡 net_plan 无权撤回，「余」是必然。别再当缺陷查。
- ★★ **`MC_BUY_FREE` 默认开**（5246872）：1~4★ 单轮与全部多轮不劣于 AD（85×1 4.45/4.45、290×1 14.43/14.44、59×1 45.41/45.86、60×1 50.83/50.85、178×2 3.79/3.84、290×2 21.90/21.93、178×3 5.69/7.03、85×3 13.19/13.84）。`MC_BUY_FREE=0` 回 AD 对照。
- ★ **5★ 单轮 BF 略差 +0.34h**（709×1：AD 102.50 / BF 102.84，0.33%）。已归因，**别再重查**：投炉 cid 与时刻两档**逐次完全相同**、取卡被拒 0、腾箱 0、err 0；唯一差别是收工时刻 t8200 vs t8227 —— AD 48 次重解能提前认定「活已够」收手，BF 只解 5 次，静态计划还挂着最后一张多余活，陪等到出炉。强制滚动重解（22 次解）结果一位不变 → 不是「不纠正」，是没有可纠正空间。修法必须动收尾判定，收益 0.33%，不做。
- ★★ **事件驱动重解**（5246872，`MC_EVENT_RESOLVE` 默认 1、按「剩余轮数≥2」启用）：BF 下 ops 无 buy op → 「骨架推进」这个唯一重解信号源消失 → 290×2 全程只解 6 次（AD 178）→ 骨架一次定终身。改法=`_run_plan` 三处投炉成功后置 `_dirty`，`decide` 见 `_dirty and _left_rounds>=2` 重解。**强制全局开会让 290×1 +1.22h**（单轮静态序更好），故限多轮。
- 排除项（别再重跑）：BF 加大预算**无效**（0.1/1/4s 三档同为 23.80h、4259 开工同为 1154 → 不是搜索深度）；留 buy op 修好 290×2 但 85×1 +0.58/290×1 +1.84；每 tick 解修好 290×2/4★ 但 290×1 +0.59、4★ 墙钟 247s。

## ★ 1★ 套尺码
- 9 张：178 蜻蜓款款飞 / 179 瓢虫物语 / 185 橡皮泥的记忆 / 922 果味酸奶 / 1652 七夕情人节。13 张：85 国粹京剧（684~696）。
- 实测 9 张套 5.10h/买 30/峰值 13 格；13 张套 7.08h/买 57/峰值 17 格（旧口径 ×1.0）。上交要整套各 1 张同时持有 → `还缺张数 > 两箱总空位` 数学不可能，启动预检硬拦（main.py）。
- ★★「1★ 需 ≥13 格」已推翻：13 是 distinct 张数不是箱位需求（18/9/13 格 makespan 7.06/7.08/7.04 无差）。炼卡只要局部 3 张配方同时在箱。
- 1★ 单套底卡投入 = **27 张**（684:6 685:7 686:6 687:4 688:4）；40 面配方 3600s、顶层 14400s。逐卡配方见 `REFERENCE.md`。

## ★★ 真机硬约束
- **取卡要 2 个换卡箱空位**（两种报错：`您的卡片箱满了…` / 不含「满」字的 `请空出两个卡箱的位置`）；被拒时炉不消失、卡不丢。
- ★★ **满箱死锁链（43f0b38）**：箱满→取不出→炉位不释放→10 面烧不掉→永远满。修：①收卡被拒立刻 `_make_room(2)` 重试；②`_make_room` 可卖**套内 10 面**（同 cid 重复优先、单张垫后，买回同价无损），40 面以上绝不动（阈值 `face<=10`）；③腾位失败**不要重读 mainpage**（连打被限流→snap=None→提前 break）；④DP/贪心买卡闸都留 2 格取卡位。
- ★箱容量语义：`cur`=已开通格数（真上限），`max`=可开总量。两箱都真满。
- ★★ **炉位语义（2026-10-28 纠正，旧「主炉≥5」作废）**：`<stovebox cur="7" max="7" stovenum="3">` + 3 个 `<stove id=0/1/2>` → **3 主炉 + 7 卡位 + 1 偷炉**。`machines=3` 正确（旧「slot 0~4 同时在炼」是把**排队卡**当炉，`live_contract_check` 同错待修）。★一个炉可排队多张（链式接续），投下即锁定、不能反悔；`<stove>` 只报每炉当前那张 endtime。
- ★★ **炼卡时长 = 表值 × 0.6232**（`constants.TIME_SCALE`，四卡逐秒吻合）。来源=炉 lv9+strong30+精灵，单帧拆不开。已接进 `auto_refine._lib()`、`parse_full`、`sched_cmp/recipes`，`MC_TIME_SCALE=1.0` 回旧口径。
- ★★ **bench**（18/200、3 主炉+偷炉、45s/tick、单轮、err=0）：默认 BF（5246872）1★85 **4.45** / 2★290 **14.43** / 3★59 **45.41** / 4★60 **50.83** / 5★709 **102.84**；AD（`MC_BUY_FREE=0`）4.45/14.44/45.86/50.85/102.50。多轮 BF：178×2 3.79 / 178×3 5.69 / 85×3 13.19 / 290×2 21.90。旧 ×1.0 口径 7.08/23.07/72.80/79.46/164.89 仅历史对照。
- ★★★ **排队位：任何场景都不该用**（2026-10-28 全量验证，别再重开）。零产能增益（仅省「出炉→取卡→投料」空转，4★ 该窗口 0/4067 tick）→ 收益上限 0，只剩锁料成本；五档全负（+0.67/+3.70/+8.66/+10.30/+7.05h）。离线缓冲假设也否（K=100 +22.45 / K=200 +50.40h）。→ `_main_cap = machines − 在炼主炉数` 是正确终态，`MC_MAIN_QUEUE=1` 仅消融。
- ★★★ **`sets` 封顶用「换卡箱空位 ÷ 套内张数」，不是总容量**（01b3c65，真机爆箱事故）。1★ sets 原=`max(囤得最多那张40面)`、cap 只看炉位 → 40 面囤到 5~6 张就 sets=6 → 一次铺开 6 套 ≈18 张半成品，而顶层要**三张** 40 面全齐才开炉 → 只进不出 → 塞满 18 格 → 底卡买不进 → 炉清空后永久停摆，且**正反馈**（囤越多 sets 越大，真机保险箱 168 格能堆到 13 张）。★用户纠正：一套卡是**滚动消耗**的，只要空位够一套流转就能一直炼（mr=0 不限轮 = 无限套），该限的只是**在途并行度**，且必须用**空位** `cur-used` 而非总容量。修后 178 贪心 mr=0：11.25h 从 0 套/18-18 → 3 套；375h 交 108 套 / 3.47h 每套、箱稳 7/18；85 → 59 套 / 6.36h、箱 13/18。★只改 `free_stove` 炉位口径无效（仍 18/18）→ **箱位才是硬约束**。DP 全档零回归（单轮五档 + 多轮 + 三套 16.29/20.64 逐位不变；85x3 13.19→13.82、290x2 21.90→21.26）。
- ★ 上交瞬间必须**整套各 1 张同时在箱** → 「空位 9 = 无限套」只对 9 张套（178）成立；13 张套（85）空位<13 数学不可能完成一套。
- ★★ **bench 口径漏洞**：此前全部 bench/探针是 `use_dp=True, max_rounds=1`，而生产默认是**不勾 DP + mr=0 不限轮** → 这条组合从未被测，1★ 就爆箱。**以后 bench 必须含 `dp=False, mr=0` 一档**（贪心路径的 `free_stove` 拿的是卡位数 7，DP 侧 `_main_cap` 早收窄到 ≤3，两条路径口径本就不同）。
- ★ 贪心路径仍死锁：3★/4★（`_make_room` keep_ids 保护全部在炼套卡 → 卖不动）、双套 1★（9+13=22 张 > 18 格物理装不下）→ **多套/多星一律勾 DP**。
- ★★ **决策间隔是第一性能变量**：同 4★ K=1(45s) 50.85h → K=10 50.89 → K=30 55.79 → K=100 93.76 → K=200 **168.76h**。生产 `auto_refine.interval=45.0` 正确；任何轮询退避/掉线重连都要按此曲线评估。
- ★ `endtime` 解析：首选 `btime+locktime`，其次按 `card.stove` 对齐 `<stove>`（按 slot 对会错配，晚 2243s）。
- ★★ **精灵饿着不用建模**（2026-10-28 结案，别再开）。①在炉卡时长走真机 `btime+locktime`（`world.py:114`）+ `rem=endtime-now` 进骨架（`dp_controller:728`）→ **与精灵无关，永远精确**；`0.6232` 只用于「还没投的卡」的预测。②故意让模型按 0.6232 估、真机按 1.0 跑（系数估错 1.6×）：85×1 7.04 vs 全知 7.08、290×1 23.06 vs 23.07 → **代价 −0.01~−0.04h ≈ 0**。因所有时长同比缩放，排序几乎不变，只有 tick/橱窗/视野这类绝对常量受影响，而它们已被执行层闸兜住。→ 饿着只是「整体真的变慢」，不是算法问题。
- ★ 精灵/炉读数（2026-10-28 真机）：`<elf bname=1 type=1 lost=0 losttime=576000 hunger=0 powertype=1 power=5 exp=78948 hungertime=1793635200 lv=11>`、`<stove id=0/1/2 lv=9 strong_lv=30>`、`<stovebox cur=7 max=7 stovenum=3>`。**hungertime 是未来的饿了时刻**（1793635200 = 2026-11-03 00:00）→ 可提前知道何时掉加成。后端目前**完全不解析 elf 节点**（无 `card_elf_feed` 喂食端点，自动喂食做不到）。
- `reserve` 禁无条件 +2（炉剩 2000s 也吃 2 格 → 整 tick 零动作）。现为 `soon + (下 tick 内到点的炉?1:0)`；底卡额度 `max(1, 空主炉+本tick空出)×3`。
- 「缺口优先」排序证伪（3★/4★ 750h）。4★ 紧箱死锁已修（7b4fc46）：`due_window=min(2×(炉+偷)+2, max(2, change_cur//4))`（18→4）+ `_jit_due` 返 `protect` + 挪箱后补 `avail_slots`。★3合1 是配方指定的 **3 张不同卡**。
- ★★ 默认路径（不勾 DP）在 3★/4★ 必卡死，根因 `_make_room` 的 keep_ids 保护全部在炼套卡 → sells=0。DP 是 18 格硬顶下唯一能跑通的高星路径。
- 生产 DP 冒烟 `probe_dp_adapter.py`：委托 VirtualSession 当服务端（静态 XML 已失效）。

## ★ 仿真=生产（2026-10-26「要接就一起接」）
- `Engine` = `VirtualSession` + 生产 `AutoRefine.tick()`，唯一替换网络+时钟；`planner/executor/dp_adapter` 已删。虚拟时钟每 tick 必须推进（否则 `time.time()`=0 → 偷炉冷却/缓存失真）。
- ★虚拟层语义必须与真实 API 逐参对齐，**禁自造反语义**：`sell_card` 曾把 `box_type=1` 当保险箱（真机 1=换卡箱）→ 腾箱 705 次全败，把 4★ 死锁掩盖成「能跑」。
- 已模拟：变卡（槽位+材料面额≥目标+源卡先烧+默认 50% 失败）、炼卡（槽位/配方一致/偷炉只 40 面）、未到点取卡 -32001 且炉不消失、上交先校验整套、`mainpage(opuin=好友)`。
- 未模拟：金币恒 0（真机 `mainpage.<user money=>` 可读，实测 35 万；底卡 20 金/张）；翻牌/抽卡不产卡；每日上限、卡箱扩容；好友只有一个且 3 主炉恒空。
- ★★ **礼物箱已模拟**（842161d）：`VirtualSession.gift_box` + `EP_FREEGIFT_LIST/TAKE`，`run_sim(gift_box=N, gift_drain=)`。★注意 `gift=N`（卡箱塞 N 张 48 面卡）与 `gift_box=N`（礼物箱件数）是两回事。收礼产物按 `_add_to_box` 进卡箱（依据 locate_card「7773 在 changebox slot=1」）。未实证 freegift_id 按 7773 简化。
- ★★ **收礼变卡实测**（礼物箱 46 件、DP、err=0）：178×1 3.16→**2.52** / 85×1 4.45→**2.52** / 290×1 14.43→**12.53** / 178×3 5.69→**5.04**。机理=变出的 40 面卡直接替代「买10面+炼40面」，炼次与买卡同步降（85×1 炼12→3、买52→13）。对照：礼物箱 20 件（< upper 45）开=关逐位相同 → 阈值正确。多轮收益小是正常的（礼物只够第一轮，收 11 件→35 低于阈值）。★真机约束：收礼进卡箱，而真机三箱全满 → 必须先腾箱才能消耗礼物。
- 扰动：`CHAOS_SAFE` 默认（只含变卡 50%）/ `MC_SIM_CHAOS=1` 压力档（无实测依据，禁外推）/ `--steady` 全关。变卡 50% vs 100%：2★ 24.01 vs 19.01h。
- ★买卡空位**只认换卡箱** `change_free`，三处都查：`_run_plan` 买卡闸、`_album_prebuy`、`_box_compaction`。决策前必须 `_read_state()` 重读。

## ★★ 执行层资源分配
- **先定炉位再占喂料槽**：旧 `_run_plan` 先 `take_slot` 全部 feeders 再判炉位、不够就 `continue` 却不归还 → 同 tick 后续 op 的 `has_slot` 全假（记账 bug）。
- **主炉只给不可偷炉的卡**：`is_40=True` 走偷炉，4h/顶层只能主炉。判据=主炉余量 ≤「后面到期且料齐的不可偷炉 op 数」→ 40 面卡让位；「料齐」含「≤2 tick 内出炉」；视野按时间（1h）。`MC_MAIN_RESERVE=0` 可关（默认开）。4★ 81.47→**79.46h**。
- ★`MC_ORDER_ONLY`（去 start 门）2★ +0.96h **不是缺陷**：引擎只有单 plan，不存在跨 plan 竞争；ORDER 每 tick 试遍全部 op、能发就发，炼次与 AD 相同而慢 0.96h → 差异全来自「抢料抢炉」，start 门是被证明有效的约束。默认关。
- ★★ **多轮「本轮排满→ops 空→执行层停手」（ef8191a）**：`_calc_sets` 对 1★ 按 `max(各 40 面持有)` 算 sets=1 → `net_plan(sets=1)` 把「顶层已在炉」算已持有 → 净需求归零 → 空 ops → 整 tick 零动作（178×3 曾 70% tick）。修法 `pipe_sets`：净需求 0 + 有剩余轮次（`max_rounds=0` 按 99）+ `change_free≥6` → sets 提到 2（只多一环，铺满会叠箱→collect 不触发→永不重解）。
  ★★ **必须与 `buy_free` 绑定**（默认 `pipe_sets = buy_free`）：BF 178×2 11.05→6.04 / 178×3 17.06→9.05；AD 178×2 **+0.99 退化**（buy 占 shop 串行槽）。
- ★★ **290×2 的 +1.28h 已结案（5246872）**：旧判断「宽 ready 集过度排产」**错** —— 两档投炉 60 次、4251~4259 逐 cid 数值完全相同。真因=骨架不纠正：首解把 46 张 0.625h 短活按 50tick/批 连排到 tick 551，2.5h 中间层挤到 554/754/954（AD 204/652/654）→ 顶层 4259 开工 1154 vs 853。两档 solver 预测几乎相同（opt 83184/83049s）→ 不是解差，是 BF 执行慢 0.72h 且不再纠正。修后 21.90h。
- ★ **炼次差异不是「多干活」**：`stats["refined"]` 与仿真 `_do_refine` 成功计数不同口径（290×2 37 vs 60），判「过度排产」必须用「投炉 cid 级分布」而不是 refined 计数。

## ★★ 静态序 vs 滚动执行（四次独立复现）
- joint_dp 静态联合序 −22~40h；纯 DP 静态解 4★ 反输；CP 精确骨架 2★ 22.01→24.12h；CP 顺序 tie-break 23.10→24.07h。机理：静态序更好 → 头动作更少被卡 → 重解 5→2 → 自适应纠错被浪费。→ 所有「优化初始序」补丁无效。
- CP 轨迹差分：最优解**刻意空转**（CP 68.1% vs 引擎 76.5%）→ 利用率不是目标。`start`=「最优序下最早可开工时刻」（下界），当上界用会空等；真作用是抑制未到期就买。两开关默认关只消融。CP-SAT 定位=标尺+规则挖掘，**不作骨架来源**。
- ★人手干预：计划自愈（不重复炼），但投计划外 +1.5h/张；单轮投计划第 1 张 **+0.80h**（`start=1440s` vs `elapsed=1305s`，提前 135s→多买 7 张底卡→重解 +14，放大 21×）。多轮 17 点扫描：`plan_ops≥66` 时 Δ=0（1★ 甚至 −0.65h），但 2★ 后段 +0.66~2.53h、3★ 刚重解时 **+13.71h**。→ 人靠不住，正解是 `pipe_sets`。

## 调度评测纪律
- 截断判定：节点数恰好=`node_budget+1`；「修复带来收益」必须同预算旧vs新对照。禁并发跑 2s 求解。单次失败禁当普遍结论。
- 五档净收益 1★99% / 2★≈最优 / 3★16% / 4★90% / 5★32%，禁据 1/2/3 星外推。真实交付 gap 1★1.0%/2★4.7%/3★6.1%/4★5.6%/5★0.35%。
- `solve` 的 `time_budget` 守卫必须 `time.perf_counter()`；预算按解算次数分层（默认 `0.1/cold=2.0/warm_solves=8`，4★ 墙钟 237→28s）。★字面「每 tick 都解」是错的（4★ 82.72h）。
- `vec_prio` 必须用「向上 cp」（向下口径 4★ 84.08h → 77.45h）。
- `sched_dp` 2 空格缩进；`_sim_faithful` 必须托管 python（含 httpx）。

## sched_dp 正确性（改后必跑 `tests/_verify_sched.py`）
①dfs 返回值含 while 内推进的 `t_rel`；②memo 双链；③rec 库存恢复 `st[j]+=c*k`；④LB 剪枝依赖绝对 `best["val"]`，不落 memo；⑤DP 值必须独立模拟器复算；⑥LDS 截断常比全量好，anytime incumbent 才是交付值；⑦紧箱两层：执行层 JIT 门 + `_box_compaction`。

## 前端铁律
- 改 `app.js` 必 bump `index.html` 的 `?v=`；no-store。按钮放所属子玩法模块，手动操作即时响应、与轮询解耦。文案禁代码标识。一键多操作走 SSE。
- 后端 `need_slots` = 一套 distinct 卡张数（1★=13），**不是箱位需求**；前端「集齐 1 轮需 N 格」易被误读。

## 验收/运维
- 改 backend 必须 `./server.sh stop && start` 真重载（`/api/auto/stop|start` 不重载模块）。改完必须实际 `git commit`；scratch 脚本（`_probe_*`/`tools/`）默认不提交不删除。
- 勿杀未知服务：`:8000` 本项目 / `:8777` agent-chat / `:9222` headless Chrome。
- ★本机 Bash `grep "a\|b"` 的 `\|` alternation **不生效**（静默空 → 误判「没这个东西」）。多 pattern 用 Grep tool 或 `grep -e`。
- ★真机口径核对先跑 `tools/live_contract_check.py`（只读 10 秒）。
- ★**httpx 探针必须 `headers={"Connection": "close"}`**（复用连接第 2 个请求起 404）。手动接口：`/api/box/sell?card_id&slot&box_type=1`、`/api/refine/get?slotid=N`、`/api/card/refine?target=CID`、`/api/card/buy?card_id&theme_id`、`/api/box/move?card_id&slot&from_box`。

## 未闭环
1. 多套 + BF：178×3 0.02h 噪声级退化未深究。
2. 5★ 单轮 BF +0.34h 的收尾判定（不改，仅记录）。
3. 待办池：`MC_SIM_TAKE2` 保取卡位、金币/魔力退避、翻牌产出、每日上限、多 tid 并行顺序敏感（见上）。
（`live_contract_check` 主炉≈5 与 `net_plan` 中间层漏算已于 2026-10-28 结案，见 fe50cce / 00bfd88。）

## 【归档 2026-11-04 19:20】MEMORY.md 精简前原文（逐字，勿删）

````markdown
# 魔法卡片助手 · 长期记忆（铁律 + 指针）

深细节看 `.workbuddy/memory/REFERENCE.md`（含 2026-10-30 归档的完整旧版 MEMORY.md 逐字原文）、`docs/`、`tests/`、`git log`；流水看 `YYYY-MM-DD.md`。

## 方法论
- 现象>推测，禁单点采样下全局结论；授权边界：写操作先回查定调原文（`git log -S`）。**高面值卡永不自动卖**，只卖 ≤10 面底卡。
- ★本机 Bash `grep "a\|b"` 的 alternation **不生效**（静默空 → 误判「没这个东西」）→ 用 Grep tool 或 `grep -e`。
- 只读排查先跑 `tools/live_contract_check.py`；httpx 探针必须 `headers={"Connection":"close"}`。

## 会话/多账号架构（2026-10-31 落地）
- **同端口多账号**：后端不再用全局单例 `_session`，改为 `SESSIONS[token→LoginSession]` + `SessionProxy`（contextvar 路由，并发安全）。路由层零改动，所有 API 自动按 `X-MC-Token` 请求头路由（前端存 `sessionStorage`，**按标签页隔离**）；cookie 仅作兼容兜底。
- **登录流程**：前端 `begin` 拿 `lid` → `qr(lid)`/`poll(lid)` 各自独立取码；`poll` 确认后生成随机 `token`、`Set-Cookie: mc_token`（HttpOnly，30天，**仅兼容兜底**）并把 `token` 返回前端 → 前端存 `sessionStorage` 经 `X-MC-Token` 头携带；落盘 `SESSIONS_DIR/<token>.json`。无 token 走 `default` 账号（向后兼容旧前端/直连 API）。
- **隔离生效条件（关键）**：⚠️ **2026-10-31 起前端改为 `sessionStorage` + `X-MC-Token` 头，从设计上实现「同域名多个普通标签页各登各号、互不干扰」（对齐 Yearning）**：token 存 `sessionStorage`（按标签页隔离，刷新/重开同标签仍在、关标签即清），每次请求由全局劫持的 `fetch` 自动注入 `X-MC-Token` 头；后端优先认该头、cookie 仅兜底。因此**普通开两个标签页即可各登各号，无需无痕/容器**；关掉一个标签不影响另一个。→ 不要再默认断言"同浏览器标签页会互相顶、得用无痕"。后端不依赖浏览器共享与否：每次登录签发独立 token，session/引擎/日志全按 token 索引，两种浏览器行为都正确。
  - **Yearning 自身隔离的机制（2026-10-31 查证）**：`cookieY/Yearning` 是 Go 后端 + Vue 前端（`webpage/` 工程编译进二进制）。其 Vue 前端把登录 token 存**浏览器本地存储**（sessionStorage/JS header），**不用 cookie**——所以"只有 yearning 隔离、别的站共享"是 sessionStorage 类**按标签页隔离**机制的特征（localStorage/cookie 都会共享）。⚠️ **关键区分**：我们的魔法卡片是**独立前端 + httpOnly cookie（`mc_token`）**，同源 cookie 默认**跨标签页共享**，所以我们的 app **不会**自己隔离标签页（与 Yearning 相反）。若用户实测魔法卡片也隔离，必是**部署/访问方式**所致（不同端口/路径=不同源、或浏览器容器），**不是前端代码**（app.js 不碰 document.cookie/sessionStorage）。GitHub 代码搜索需登录，未拿到 Yearning 前端原文第几行，但行为特征已锁定机制。
- 各账号引擎隔离：`auto_exchange.get_engine` / `spirite_auto.get_engine` 缓存键改为真实 session；自动炼卡/换卡/灵宠按账号各跑各的。
- 持久化：`mc_api.SessionProxy.load_cookies`→`_save` 落 `persist_file`；启动时 `_load_sessions` 扫描 `SESSIONS_DIR`。旧单文件 `/tmp/mc_session.json` 迁为 `default` 账号（**复制**保留源作备份）。`MC_SESSIONS_DIR`/`MC_SESSION_FILE` 可环境变量覆盖。`server.sh` 仍支持 `start [端口] [session文件]` 多实例（与同端口方案并存）。
- ⚠️ `/api/me` 探针判腾讯过期（-1001）会 `_session.clear()` 删落盘文件 → 重扫一次即可（卡在腾讯侧不丢）。迁移后若旧 skey 已过期，首次 `/api/me` 会清掉 `default.json`，属正常。
- **日志按账号隔离（2026-10-31）**：`mc_api` logger 改为按 `LOG_UIN` contextvar 分流——每条日志行带 `[uin]` 标记；落到 `<LOG_DIR>/global.log`（所有账号聚合，与 qq 无关的总览）+ `<LOG_DIR>/<uin>.log`（仅该账号）。`LOG_DIR` 默认 `/tmp/mc_logs/<PORT>`（`MC_LOG_DIR` 可覆盖）。请求由 `session_middleware` 设 `LOG_UIN=s.uin`。
  - **引擎运行流水也落文件（2026-10-31 收尾）**：新增 `engine_logger`（`mc.engine`），**只挂 `<uin>.log` + stdout、不挂 global**；三引擎 `auto_refine`/`auto_exchange`/`spirite_auto` 的 `_log` 全部改写 `engine_logger`（写前 `LOG_UIN.set(uin)`）。前端界面那条滚动日志由此存档，重启可查。另保留 stdout handler → `server.sh` 重定向到 `/tmp/mc_<PORT>.log` 作实时总览。

## 真机硬约束（API 事实）
- 卡箱：changebox **18 硬顶**、storebox `cur=168`(max 200)；**买卡只落换卡箱**。
- 炉：**主炉台数 = `<stovebox stovenum>`，不是 cur**（cur 恒 7 是卡位总数，含偷炉位）。默认 3 主炉 + 7 卡位 + 1 偷炉；**道具卡可把主炉 3→4**（2026-11-04 真机：`stovenum="4"`、`<stove id="0..3">` 4 个、新炉 `strong_lv=0`，cur 仍 7 不变）。一个炉可排队多张、投下即锁定不可反悔。
  ★★ **卡位真相（2026-11-04 用户点破，两份真机报文交叉核对）**：`cur="7"` = **5 主卡位（slot 0~4）+ 1 偷炉位（slot 6）+ 1 个 VIP 卡位（slot 5，未开通→服务端不列）**。用户没开 VIP → 实际能用 6 个。**别把 slot5 当死格子**：开通 VIP 后主卡位 5→6，`free_main`（数出来的）自动 +1，引擎无需改。**加炉道具不是加卡位，而是把「排队位」升成「炉位」**：3 炉 = 3 炼 + 2 排（09-21 报文：炉1 挂 2 张、炉2 挂 2 张、炉0 挂 1 张）／4 炉 = 4 炼 + 1 排（11-04 报文：4 张各占一台炉，只剩 slot3 空）／**5 炉 = 5 炼 + 0 排**（道具最多 +2）。→「每炉 2 卡位（1 炼+1 排）」只在 2~3 炉时近似成立；**硬约束是 `free_main = 主卡位数 - 占位卡数`**，并发取 stovenum。5 炉时 `MC_MAIN_QUEUE=1` 的排队消融自动失效。
  ★ **加炉道具到期**：mainpage **不下发任何到期时间**（`stove_acc_28`/`stove_acc_ts` 恒 0、`stove_jiasukaTs` 恒 0；唯一时间戳 `tem_slot_end_time` 是**简易箱**到期，别搞错）。到期信号只有 `stovenum` 变小。因炉位是从排队位升上来的，到期=降回排队位，那张卡仍在合法卡位上 → 大概率继续炼完（用户观察一致），不会丢。引擎已做：`_busy_stoves` 只数合法炉号 `0..machines-1`（超额炉不占并发预算，否则 `machines-busy` 变负 → 额度 0 → 停摆/永久冻死），machines 减小时打《缩容取证》日志（超额炉卡的 id/flag/剩余），首次真机到期即可定性。
  ★ 引擎并发炉数必须每 tick 取 `cap.stove.stovenum`（`auto_refine._dp_refine_all` 同步 `dp_ctrl.machines`，变化记「主炉台数 3 → 4」日志）；**禁写死 3**。`_main_cap` 判「几台炉在忙」**必须按 `Furnace.stove` 炉号去重**——按卡数会把「1 在炼 + 1 排队」算成 2 台 → `machines - running` 变负 → 投料额度恒 0、引擎彻底不动（这是 2026-11-04 才发现的隐藏雷，用户往炉里手投一张排队长达数小时无人察觉）。4 炉基准（18/200/1 轮）：1★85 4.45→3.79h、2★290 14.43→13.76h；3 炉零变化。
- **取卡要 2 个换卡箱空位**；被拒时炉不消失、卡不丢（腾箱后重试即可）。
- 炼卡时长 = 表值 × **0.6232**，只用于「还没投的卡」的预测；在炉卡走 `btime+locktime`，与精灵无关、永远精确。
- 上交瞬间必须**整套各 1 张同时在箱**（13 张套空位<13 数学不可能完成一套）。
- 卡友（搜指定套卡查正在炼的人）：接口 `card_user_theme_list(tid)`（EP_THEME_LIST）每页 100 人（10 个 `<node>`，node.uin 用 `&#x7C;`(|) 打包 10 个 QQ，必须 `html.unescape` 再 split）。**分页是环形的**：翻到末页后再翻会绕回某早前页（返回同一批人），**不会返回空**；游戏原生界面「下一页」到末页后也会跳回第一页（用户 2026-10-30 亲见）。**部分主题被服务端封顶=逻辑上仅 1 页**（如「床」1818：page1 恒==page0，无论 page 参数怎么传都返回同一批 100 人；蜻蜓 178 是多页套、正常翻 60~120 页累计数千人）。
  ★「设了页数却还只查 100」= 该套被腾讯**封顶**（非工具 bug）：`theme_peers`/`gather_peers` 的 `max_page` 对会分页的套完全有效（178 拿数千人）。**识别与停法（2026-10-30 修正 `gather_peers`）**：始终探测 page1，若 page1==page0 即判封顶（标记 `capped_tids` 上报前端提示）；深页并入全局去重后 **0 新增**即判「环形绕回/翻完」并停止（不能只跟 page0 比——绕回点可能是中间页，否则 178 类多页套会死循环）；`max_page==1` 时非封顶套只取第 1 页（尊重设置）。`gather_peers` 返回 `(peers, capped_tids)`，三处调用点（/api/exchange/peers、/api/exchange/scan、auto_exchange）均上报 `peers_capped`；前端在找卡友/扫描/自动换卡三处提示「该套翻页无效、最多约100人、腾讯限制」。
  **多套策略**：priority_tid（=主套 `tid`，即「要换的那套」）先完整翻完，其余源套（`tids` 里其余的，即「炼其他套的人、箱里可能有主套卡」）再**逐页交替**翻。`auto_exchange.max_peers` 仍作人数上限兜底。前端选项「查卡友页数」默认 1（=旧行为≈100人），0=不限页；加在两处（找卡友/扫描工具条 + 自动换卡块）。
- 好友列表 `card_user_list`（EP_USER_LIST）≠ 卡友：固定返回约 194 人（QQ 好友中玩过本游戏者），不分页、忽略分页参数。★**列表里含本人**（uin 无前导 0，`self.s.uin` 是 `000xxxx`）→ 偷炉挑好友必须按**数值**判「是不是我」，只比字符串会发出 `opuin=自己`，被拒「魔法学院规定不允许偷自己家的炉子！」，还冷却 120s + 把自己拉黑 30 分钟（2026-11-03 修）。
- ★**换卡箱会被「非本套杂卡」永久锁死**（2026-11-03）：`_box_compaction` 原只卖多余 10 面底卡 + 挪本套合成卡，杂卡 `_owner.get(cid)=None` 直接跳过 → change_free 恒 0~2 → 买卡预算 0 → `_run_plan` 卡在第一个 buy op，表现「引擎在跑但一分钟不动」。已加第 3 段：箱紧 + 保险箱有空位时杂卡**按面额降序挪保险箱（只挪不卖）**，腾到够一次「买 3 张立即投炉」即停。保险箱满（168/168）时无能为力，只能用户自己清。

## 调度引擎（本质=在线 RCPSP，滚动重规划）
- 禁 god-function，planner/executor 分离。「优化初始序」类补丁**一律无效**（四次独立复现）；CP-SAT 只作标尺不作骨架来源。
- `sets` 封顶 = **换卡箱空位 // 套内张数**（01b3c65，真机爆箱事故）。成因：40 面囤到 6 张 → sets=6 → 18 张半成品正好塞满 18 格 → 底卡买不进 → 炉清空后永久停摆，且**正反馈**。只改炉位口径无效，箱位才是硬约束。
- 排队位任何场景都不该用（五档全负，收益上限 0）。`reserve` 禁无条件 +2（预算归零才是 750h 卡死真因）。
- 买卡空位只认 `change_free`（`_run_plan` 闸 / `_album_prebuy` / `_box_compaction` 三处）；决策前必须 `_read_state()` 重读。
- **决策间隔是第一性能变量**：K=1 50.85h → K=200 168.76h；生产 `interval=45.0` 正确。
- ★**最高优先级规则（2026-11-02 用户定调）**：**任何操作会卡住「集齐一套」就必须延后**。统一判据 `_collect_tight(st,stock)`＝凑齐还缺张数，**仅当两箱空位 ≤ 该缺口（零余量边界态）才有值**；空位充裕/已凑齐恒 0 → 正常场景一律不延后（延后只用在真装不下时，不白拉长时间）。辅助 `_is_collect_essential(cid,stock,pending,as_take=False)`：**问 `net_plan` 净需求树还要不要它**（箱内+在炉之外），runs/buy 里出现即必需。⚠️**distinct 口径是错的**（2026-11-03 真机冻死）：tid=321 的 `小头鼠海豚` 一个 id 要 7 张（1 进册+6 当三个 40 面中间卡的共用素材），箱里有 3 张就判"不缺" → 预买全延后 → 底卡进不来 → 素材不齐/偷炉没料/出炉无卡可投，引擎 35 分钟零动作。`as_take=True`（**只有取卡/炉内卡用**）才把这张从在途扣掉——否则炉里那张正当需求的卡会被判"不需要"；买卡/变卡不扣。`_net_now` 按家底签名缓存 net_plan。★**逃逸阀**：`_dp_refine_all` 本 tick 无 refine 动作时不延后预买（没东西可投时保格位＝纯冻死）。`_free2(st)`（保险箱认 `cur` 不认 `max`）。已接入：①收卡 `_should_defer_take` ②收礼变卡 `_gift_drain`（变卡目标**全**非必需才整套延后）③DP 预买（边界态跳过囤卡）；**买素材豁免于上述延后闸**，但受另一条独立铁律约束（见下）。
- ★**买底卡铁律（2026-11-03 用户定调）**：「卡没集齐时，卡箱一个 10 面的卡都不要买，放那也没用，纯占位；只有收了最后一张在炼的卡时，才需要一次把 10 面买齐上交。」落地口径（`dp_controller._run_plan`）：**只有一条** —— `abs_slots==0`（无空主炉、无 1 tick 内空出的主炉）→ `base_quota=0`，且**没有 `change_free≥6` 的囤卡口子**（箱宽松也不囤）。原有 `jit_need` 取「头 n_wave(=due_window=4) 个 refine op」的底卡需求本来就等于本 tick 能投的炉数（3 主 + 1 偷），买的料同 tick 即被消耗、不存在囤积；真机「买了 8 张 10 面炉还要 20~95 分钟才出」的根因只是 `abs_slots==0` 时的保底 3 张。唯一一次批量买 10 面＝`_album_prebuy`（非底卡全在箱且本套无在途）。
  ⚠️**别加「该 refine op 的非底卡喂料必须已在箱/在途才买它的料」这类前置过滤**（2026-11-03 试过又回退）：链条初期恒不成立 → `jit_need` 恒空 → 只走兜底 → **一次只投 1 炉（原 4 炉）**，1★85 4.45→5.04h。用户当场点破「1 tick 45s，一炉最多多 3 tick，怎么会多 0.6h」——用 `backend/_probe_buywave_trace.py` 逐 tick 追踪证实：多的 47 tick 是 **t=13770~15500 连续 78 tick 零动作死等**，与买卡物流无关。**教训：基准出现「远超 tick 量级」的差异时，别用机制直觉解释，直接打逐 tick 时间线。**
  ⚠️也别改成「整波买不齐就整波不买」——真机换卡箱常只剩 1~2 格，整波要 3 格会死锁（腾箱要先买、买要先腾箱）。
  基准零代价（18/200 单套 1 轮）：1★85 4.45、2★290 14.43、3★59 44.99（原 45.41）。\n- ★**额度按「炉」成组花，不按卡种堆量（2026-11-04 用户问「为什么不在买底卡的同时投炉」）**：买卡与投炉本来就同 tick（`buy_sametick`+虚拟槽，执行层买完重读真实 slot），\n  曾经的病是 `jit_need` 按卡种累加 + `sorted(-need)` 让本 tick 额度（一个空主炉只有 3 张）全砸在同一张卡上 → 一炉永远凑不齐，空主炉白等 2~3 tick。\n  现在 pass 1 建 `waves`（一波=一个 refine op 的整套底卡），先按波填齐再按缺口补。探针 `backend/_probe_buy_group.py`：OLD 买 `[4934×4,4932×2]` 投炉 0；NEW 买 `[4929,4930,4934, 4934,4933,4932]` 投炉 1。


## 已结案（别再查）
- `net_plan` 中间层漏算 → **证伪**（00bfd88，all-ids 口径已覆盖；判据只查缺不查余）。
- 290×2 +1.28h → 真因是骨架不纠正，已修（5246872，事件驱动重解仅 ≥2 轮启用）。
- 5★ BF +0.34h → 收尾判定差异，无纠正空间，收益 0.33%，不做。
- 精灵饿着 → 在炉卡走真机 endtime，系数估错 1.6× 代价 ≈0，不建模。
- 「取卡 2 格会让 3★/4★ 750h 卡死」→ **过期**（2026-10-30 实测：全档逐位相同，仅 5★ +1.61h，err=0）。

## 基准（DP、45s/tick、取卡 2 格）
单轮：1★85 **4.45** / 2★290 **14.43** / 3★59 **45.41** / 4★60 **50.83** / 5★709 **104.45**
多轮（BF）：178×2 3.79 / 178×3 5.69 / 85×3 13.18 / 290×2 21.26
收礼变卡（礼物箱 46 件）：178×1 **2.52** / 85×1 **2.52** / 290×1 12.53 / 178×3 5.04
`MC_SIM_TAKE2=0` 回旧 1 格口径、`MC_BUY_FREE=0` 回 AD，仅供历史对照。

## 验收/运维
- 改 backend 必须 `./server.sh stop && start` 真重载（`/api/auto/*` 不重载模块）；改完必须实际 `git commit`（分批：代码+测试 / 文档）；scratch 脚本不提交不删除。
- 勿杀未知服务：`:8000` 本项目 / `:8777` agent-chat / `:9222` headless Chrome。
- ★OCI 云部署铁律：新开对外端口必须**两步都做**——①实例 `sudo firewall-cmd --permanent --add-port=<port>/tcp && sudo firewall-cmd --reload` ②云控制台 Security List 加 TCP 入站。单纯 `--host 0.0.0.0` 监听 ≠ 对外可达；`command -v iptables` 在 opc PATH 下会误报"无防火墙"（实际 firewalld 开着、iptables 是其后端，须用 `sudo /sbin/iptables -S` 绝对路径查证）。实例 `IN_public_allow` 默认只放行 22/80/8000/9784/13880-13891，新端口务必显式加。SSH：140.83.54.40:22 opc，密钥 ~/Downloads/ssh-key-2021-12-04.key。
- 前端改 `app.js` 必 bump `index.html` 的 `?v=`；按钮即时响应、文案禁代码标识。
- **bench 必须含 `dp=False, mr=0` 一档**（生产默认；曾整条漏测 → 1★ 爆箱；贪心 `free_stove` 拿卡位 7、DP `_main_cap` ≤3，两条路径口径不同）。
- ★生产默认不勾 DP = **旧引擎**。用户 2026-10-30 定调：只留作 A/B 对照，**不再修**；DP 稳定后直接删掉。所以贪心路径的死锁不必再管，多套/多星一律勾 DP。

## 未闭环
1. 多套顺序敏感 —— **用户 2026-10-30 决定记录不修**（3 套真机机会不大）。数字分档：
   - **3 套**（178+290+85 全排列）**16.29~20.64h，差 4.35h / 27%**（最优 178+290+85、最差 178+85+290）。
   - **2 套**幅度小得多：`85+290` 16.25 vs 19.39（差 3.14h / 19%，要 290 先）、`178+290` 15.62 vs 16.89（差 1.27h / 8%）、**`178+85` 顺序无关 6.91h**。
   - ★ 真机最可能的组合（1★+1★，即 178+85）本就顺序无关 → 实际风险低。只有混 1★+2★（85+290）才明显。
   - 真因=4 炉位前期分配（在线 RCPSP），无单一静态规则（慢套先/箱峰值低者先/buy 量 三次尝试全都反向）。
     根治需发 op 时跨套关键链优先（大改）。**别再重查归因。**
2. 金币/魔力余额与退避、翻牌产出、每日上限未建模。
3. 收礼 `freegift_id` 83/86/7 未实证（按 7773 简化）；后端仍不解析 `<elf>` 节点，无喂食端点。
4. 贪心 `_make_room` 死角仍在（`keep_ids` 保护在炼套卡 → 卖不动）——按上条不再修。

## 会话约定（2026-11-02 用户明确）
- **默认指本地 8000**：用户说「这个站/账号/启动不了/卡箱/炼卡」等未专门说「云端」时，一律指**本地 8000**（`SESSIONS_DIR=/tmp/mc_sessions/` 多文件 + server.sh 默认 session 文件 `/tmp/mc_session.json`；旧单文件迁为 `default` 账号）。只有用户明确说「云端/140.83.54.40/8001」才指 OCI。之前多次误去 140.83.54.40 查本地账号现象，属方向错——以后先默认本地排查。
````


## 归档：2026-11-09 版 MEMORY.md（精简前逐字原文）

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
- **隔离现状清单（2026-11-06 全量排查，含落盘状态）**：引擎 `AUTOS[token]`（炼卡）/ `_AUTO_EX[token]`（换卡）/ `_ENGINES[(token,mode)]`（灵宠×4 玩法）/ `_autos[token]`（挖地）✅；日志 `_UI_LOGS`/`_MATCH_LOGS`/`LOG_UIN` ✅。已修单例：`_travel_aux_cache`、`dig_auto._auto`、`_inv_cache`(8s) / `_mini_cache`(60s) / `_collection_cache`(120s)、`_home_all_running`。统一走 `main._acct_bucket(store)`。`gift._face_of/_name_cache`、`_MODE_PROP_FETCH` 是静态卡数据，无需分桶。
- ★★ **前端页面级单例也必须按账号失效（2026-11-09）**：同标签页「沿用会话」`resumeToken` 切号**不刷新页面**，`initAutoOnce` 的 `autoInitDone` 与偷炉下拉的 `stealFriendsLoaded` 都是模块级布尔 → 第二个账号永远显示第一个账号的好友池（后端 `/api/players` 按 token 实测 4821992=194 人 / 3822843=20 人，无责）。修法：`showDash()` 里 `resetAccountScopedCache()` 作废 `travelFriendsCache`/`exPayCache`/`exPaySel`/`homeStateCache`，`loadStealFriends(true)` 按 token 重拉 + `stealFriendsPending` 并发去重 + 响应回来再校验 token；「常去好友QQ」按 token 存 sessionStorage。**新增任何 `let xxxLoaded/xxxCache` 要么按 token 分桶，要么进 `showDash` 的统一失效清单**。静态卡数据缓存（`priceChipsLoaded`/`_propsCache`/`cardCache`）不用分。
- ★★ **落盘状态也必须按账号分（2026-11-06 `aff9f66`）**：内存分桶只解决一半 —— 同端口多号共进程，凡是写**固定路径文件**的日计数/限次/缓存都会串。用户实测：A 号送礼送满 50 人 → B 号点一键日常直接被本地闸拦下、0 请求。统一走新建的 `backend/acct.py`：`acct_key(session)`（**uin 优先**：额度/限次是游戏账号级，同号多 token 本就该共享，顺带合并 default 同号的坑）与 `acct_path(p, session)`（`x.json` → `x.<uin>.json`）。已改：送礼日计数 `/tmp/mc_gift_daily.<uin>.json`、工会日限次 `data/union_quota.<uin>.json`（摇水果3/公会战3/兑换1）、比赛主题 `/tmp/mc_match_theme.<uin>.json`、出战记录 `/tmp/mc_match_race.<uin>.jsonl`、猫家/工会/三国运行历史 `data/*.<uin>.json`、挖地 `_unknown` 按 `acct_key` 分桶；前端 localStorage（公会战已出战计数、好友池开关）key 加 token 前缀（localStorage 是整个浏览器共享，只有 sessionStorage 按标签页）。
- **登记规则**：新增任何「每日 N 次 / 已领过 / 上次用过」类状态，**一律 `acct.acct_path()` 落盘 + 函数收 `session=None` 参数逐点传参**，不要图省事写 `/tmp/xxx.json`。日志类可共用文件，但每行必须带 `uin=`（如 `buylog`）——共用可以，「分不清是哪个号」不行。
- 全局限流器（`travel_elf_auto._last_req_ts`、`dig_auto._last_req_ts`，最小请求间隔）**故意共享**：它保护的是整个 IP 不被服务端限流，不是账号状态。`exchange_peers.json`（手动卡友名单）是用户输入，默认跨号共享。
- ⚠️ **default 与真 token 可能是同一个 QQ 号**（`/tmp/mc_sessions/default.json` 当前 = 4821992，与 `7k2x…` 同号）：无 token 的请求会落到 default 的**另一个 LoginSession**，若两边各起一个引擎就是同号双引擎抢炉/抢卡。排查串号先看这个。
- 日志按账号：`LOG_UIN` contextvar 分流 → `<LOG_DIR>/global.log`（聚合）+ `<LOG_DIR>/<uin>.log`；引擎流水走 `engine_logger` 只落 `<uin>.log` + stdout（`server.sh` 另有 `/tmp/mc_<PORT>.log` 总览）。`LOG_DIR` 默认 `/tmp/mc_logs/<PORT>`。
- ⚠️ `/api/me` 探针判 -1001 会 `clear()` 删落盘文件 → 重扫一次即可。
- ⚠️ 界面日志（`<token>_op.log`）与引擎日志（`<uin>.log`）是两套；「跑一轮」走 SSE 直推前端，**不落引擎日志**，只有 `_log()` 才落盘。

## 真机硬约束（API 事实）
- ★**集齐奖励字段（2026-11-06 用户实测钉死）**：卡库 `theme_card_list` 的 **t[6] = 金币、t[7] = 经验**。用户交「时尚女套装」(tid=45) 实测 `user.money +100`、`user.exp +200`，与该套 t[6]=100 / t[7]=200 完全对上（此前只把 t[6] 当金币用，t[7] 一直没解读）。规律：老套两值不同（1★ 100/200、2★ 常见 200/500、588/888），3★ 以上新套基本相等（888/888、5668/5668、8888/8888），顶格收集套 19999。落地：`card_query.indexes()` 出 `gold`/`exp`、`ThemePlan.gold/exp`（`prize` 保留成 gold 别名）、前端卡册列表与详情显示「集齐 N 金币 / N 经验」。`card_collection_add` 响应仍是空体，别指望从响应里取奖励。
- 卡箱：changebox **18 硬顶**、storebox `<cur="168" max="200">` —— **cur 是已开通容量（168）**，max 是没花钱扩的上限；`nor` 才是普通卡数。简易箱 15 格。买卡只落换卡箱。**卡张数按两箱合计算**（保险箱的卡照样能投炉、能上交）。
- ★**落箱规则（用户 2026-11-05）**：**炼成的卡优先进保险箱，保险箱满了才溢到换卡箱；只有买的 10 面底卡一定进换卡箱**。所以「还剩几格」必须按**两箱合计**算（`_free2`），只看换卡箱会漏掉「保险箱满 = 换卡箱也腾不出格」。跨箱只能 换卡箱→保险箱（`move_card(from_box=0)`）与 保险箱↔简易箱，**换卡箱不能直接进简易箱**。
- ★**箱位闸（2026-11-05，`56cca35`）**：`两箱空位 < 本套还缺张数` → `_calc_sets` 返回 **0 套**（`net_plan(sets<=0)` 必须返回空；`max(1,sets)` 会抬回 1 → 超炼）。同一口径要同时拦「收尾买底卡」与「`_make_room` 卖 10 面底卡」，否则卖一张买一张空转、缺口不减。
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
- ★**中间卡料齐即向上炼（2026-11-08 定调 `cdf4753`，2026-11-09 修正 `9dd2deb`）**：执行层 pass 2a（`MC_MERGE_READY`）只收 feeders 全非底卡的 refine op（3→1 净腾 2 格），绕过 `start` 门占空主炉、复用 `early_ok` 预留、标记 issued + `_dirty`；底层 40 面 op（喂料是买的 10 面底卡）绝不进。**11-08 的「囤够 `MC_MERGE_STACK`=2 组」门槛已证伪并删除**：真机复现 tid=890 时它让 150 面早 1.28h，却把 540 顶层**推后 1.22h、完工 +1.23h**（挑中小米5/大麦3 这类富余支路，抢主炉炼非关键路径）；不是「换箱位的代价」，是抢错炉。现方案 A：只发 `cp` 最高档（cp=自身时长+最长前驱链，越顶层越大）、每 tick 至多 `MC_MERGE_PER_TICK`(1) 个。890（3 主炉）完工 15.65→15.01h、540 首炼 6.29→5.65h、箱峰值 29→23、40 面峰值 20→13；5 主炉完工锁死 13.75h 但箱峰值 17→15。⚠️ **A 未消退化**（与无条件 stack=1 相同）：3 主炉 85 +0.59h / 290 +1.21h，5 主炉 60 +1.22h / 709 +1.59h；真机 5 主炉口径下 85/290/59/890 均不退化。根因：CP 目标只有 makespan、`box_cap=change_cur+store_cur` 且仅当 `Σneed0>box_cap` 才启用 → 箱位从不进模型，DP 只在「炉位数+前驱」下最优，真机不是。下一步候选：箱位压力闸门 / 按链深自适应。
- `sets` 封顶 = **换卡箱空位 // 套内张数**（真机爆箱事故，箱位才是硬约束，改炉位口径无效）。排队位任何场景都不该用。`reserve` 禁无条件 +2。
- ★★ **用户 2026-11-09 定调的验收线（跨轮重叠 vs 单轮超炼）**：**跨轮重叠换时间、必然吃箱位，可以接受**；跨轮重叠等价于「同时炼 2 套同星卡」，因此**峰值 < 套内卡数 N × 2 都算可接受**；**一定不能是单轮超炼**（把 1 套的料按多套重复炼）。判别：`sets=2` 的两套完整净需求 = 合法跨轮重叠；`sets` 被 `_calc_sets_raw` 按囤卡 `max(have)` 放大到 5~6 而只为凑 1 套 = 单轮超炼。⚠️ 现状 `_calc_sets` 只有 `sets=min(sets, free//N)` 箱位封顶，186 格两箱、N=13 时上限约 12 套 → **没有 2 的硬上限，这条验收线尚未落地**。模型数据（W=2，1~5 星 × K=2~5 = 20 档）：20/20 时间更短（平均 −24%），峰 < 2N 为 17/20，越线 3 档全是 **5★67 的 K=2/3/4**（87/76/70 vs 2N=70）；W=2 峰 ≤ N 为 0/20。
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
- ★前端类名 `sel` 是双用途：`<select class="sel">` 的下拉样式 + JS 给 `.it`/`.tchip`/`.pk` 加的选中态。任何**通用**下拉样式必须写成 `select.sel`，否则 `.sel{max-width:340px}` 会把选中行压短（2026-11-07 实测：配合套卡首行 nowrap + 名称可收缩，直接把套名压到 0 宽）。套卡首行名称必须 `flex:0 0 auto`（不参与收缩），宁可裁尾部徽章也不能压没名字。


===== MEMORY.md 归档快照 2026-11-11 14:15（压缩前逐字原文）=====
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
- ★ 灵宠状态面板**双数据源**坑（2026-11-11 `20261111a`）：`spPoll()` 3s 用 `/api/spirite/auto/status` 的 `engine.last_state`，`spTick()` 15s + `spRefresh()` 用 `/api/cat/travel` 真机完整数据，两路交替写同一份 DOM。`last_state` 原本只存 `free/fri_used/fri_ts` 别名，而 `spRenderAll` 读 `free_explore_cnt/today_fri_explore_cnt/fri_explore_ts` → 「剩 5」被刷成 0、15s 后刷回 5。修法：①`_snapshot` 补齐前端契约字段（值原样透出，空壳不造 0）；②`spMergeState()` 守卫：缺失/非数字沿用上一份有效值、**0 是合法值不回退**，钱包/碎片/灵宠列表空壳不闪 `-`；③`spPoll` 遍历 `Object.keys(modes)`（原写死 3 个**漏 invite** → 只跑邀请玩法时首次轮询就清掉 `spTimer`，界面像秒停），`showDash()` 切号后 `spPoll(false)` 按新 token 重续轮询且不重放引擎历史日志。后端 `_ENGINES[(token,mode)]` 分桶本身没问题，**不是串号**。回归 `backend/engine_v2/tests/_verify_sp_state.py`、`tools/_check_sp_poll.js`。
- 故意共享：全局限流器（`_last_req_ts`，护 IP）、`exchange_peers.json`（用户输入）、静态卡数据（`priceChipsLoaded`/`allPrices`/`cardCache`/`spTable` 等官方配置表/`gift._name_cache`/`_MODE_PROP_FETCH`）。⚠️ **`_propsCache` 不是静态数据**（`/api/props` 账号道具库存，2026-11-09 复核纠正），必须按账号失效。
- 日志：`LOG_UIN` → `global.log` + `<uin>.log`；界面日志 `<token>_op.log` 是另一套；「跑一轮」SSE 不落引擎日志，只有 `_log()` 落盘。
- 坑：`/api/me` 探针判 -1001 会 `clear()` 删落盘文件（重扫即可）；default 无 token 可能与真 token 同 QQ 号 → 双引擎抢炉。

## 真机硬约束（API 事实）
- ★ **主界面左侧「活动/任务列表」= 服务端大池子按周/月轮换**（2026-11-11 复核，用户纠正我「没抓包」的错判）：`card_run_base`（无参 POST）的 `version_list.activityInfo` → 免登录静态 XML `appimg2.qq.com/card/mk/activity_info_v_<ver>.xml`（当前 **v_1288**，264KB）：`Activity_Main_Info` 63 条活动池 + `Activity_Exchange_Info` 66 条带起止时间 + `Activity_Rule_Info` 48 条 HTML 规则 + `dzp_config`(转盘) + `ac_item` 348 条。现成解析 `backend/item_dict.py`（`parse_version_list`/`_fetch`/`DEFAULT_URLS`，⚠️ 多 `<?xml?>` 头须剔除包 `<wrap>`）。周任务每周三 0 点刷新，周/月/双月/超长期并存（实证 `mmgj` 11-11~11-17 / `xiaofei1` 11 月 / `mofashangdian` 10-14~12-09 / `yincanggoumai` 2021~2030）。**别再说需要抓包**：2026-09-15 主界面启动抓包 CSV 就在 `~/Downloads/card.qzone.qq.com抓包`，结论在 `2026-09-15.md:67` / `REFERENCE.md:3`。活动表只是规则配置，**实时进度与领奖端点仍未定位**（`card_monthtask` 等 -1 缺参、`card_act_travel` = activity is end）。
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
