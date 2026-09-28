/**
 * 每日比赛页冒烟（2026-09-26 v2）
 *
 * 验收四件事：
 *  ① 子玩法 tab 恢复：默认停在「猫窝日常」（主界面）、比赛是子玩法之一
 *  ② 工具栏按钮绑的函数**全都真实存在**（上一版 feedAllCats 等绑了不存在的函数，点了必 ReferenceError）
 *  ③ 一键比赛确实会 confirm 消耗次数、逐场打完、并顺带领奖
 *  ④ 比赛状态只留比赛相关字段（talent/clover/dzs/season/unionid 不再渲染）
 */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "frontend/index.html"), "utf-8");
const appjs = fs.readFileSync(path.join(ROOT, "frontend/app.js"), "utf-8");
const MAIN_SRC = fs.readFileSync(path.join(ROOT, "backend/main.py"), "utf-8");
const MATCH_SRC = fs.readFileSync(path.join(ROOT, "backend/match.py"), "utf-8");
const BASE = "http://127.0.0.1:8000";

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push(String(e && e.message)));
vc.on("error", (...a) => errors.push(a.join(" ")));

const dom = new JSDOM(html, { runScripts: "outside-only", url: BASE, virtualConsole: vc });
const w = dom.window;

const calls = [];
const HOME_STATE = {
  ok: true,
  likes: { match: 1, arena: 0 },
  gacha: { free_used: 0, score: 120 },
  cats: [{ elf_id: 3, mood: 85 }],
  pvp: { surplus: 5, flush_num: 2, my_ce: 360 },
  gold: { items: [{ id: 2, bought: 1 }, { id: 1, bought: 0 }] },
  forge: { parts: { head: [{ id: 9, forge: 0, chip: 3 }] } },
};
// 工会 / 公会战（2026-09-27 实跑取到的真实结构，不含推测字段）
const UNION = {
  ok: true, code: 0, union_name: "搞起耍", unionid: 10177, lv: 18, exp: 5450260,
  mem_num: 29, max_num: 29, coin: 4051, chip: 73, union_activity: 3,
  members: {
    "1006409757": "4,1790402280,紫旋の梦蝶,6213,96,1_81_1|2_49_1|3_49_1|4_43_1,202680,180,1790402552,3,35",
    "1016053460": "4,1790236666,xx,8692,85,1_55_1|2_55_1|3_55_1|4_55_2,107260,80,1790236691,3,0",
  },
};
const UNION_EXTRA = {
  ok: true,
  task: { ok: true, code: 0, data: { bits: 0, code: 0, data: 11234, desc: "sucess", num: 1, resetnum: 0 } },
  chip: { ok: true, code: 0, data: { code: 0, data: 11234, desc: "sucess" } },
  shop: { ok: true, code: 0, data: { bit: 0, bit2: 0, code: 0,
    data: [{ consume: 16, id: 9, maxnum: 1, type: 1 }, { consume: 80, id: 2, maxnum: 1, type: 1 }] } },
  enemies: { ok: true, code: 0, data: { code: 0, desc: "sucess",
    list: "10012_沧海月明_312232798,10015_倚栏听风雨_312196611" } },
};
const GVGV = {
  ok: true, code: 0, data: {
    code: 0, desc: "sucess", level: 8, inlv: 8, last_lv: 14, e_levelid: 9,
    e_name: "夜半钟声到客船", e_progress: 595420, enemy_uinon: 21718,
    progress: 478555, level_rank_bit: 252, level_rank_bit2: 0,
    levelgetbits: ",10100,20100,30100,40100,50100,60100,70100",
    season_gift: 0, ts: 1787673667,
    gift_record: "1260277398,1292723575,448027192",
  },
};
// 西游记（card_worldexplore）与三国战役（card_worldsanguo）是**两个端点、两套字段**，
// mock 必须分开给，否则断言会拿三国的字段去验西游记。
const XIYOU = {
  ok: true, code: 0,
  currLevelData: [{ formationid: 0, levelid: 2, origin_time: 86400, startts: 1790323040,
    stepData: [{ getts: 200005, gifts: "121_23200_1", index: 0, isget: 1 }],
    targetbit: 0, time: 86400, type: 1 }],
  logData: [{ formationid: 0, levelid: 2, origin_time: 86400, startts: 1790162822,
    stepData: [{ getts: 200008, gifts: "92_36_4", index: 0, isget: 1 }],
    targetbit: 0, time: 0, type: 1 }],
  formationList: [{ catid: 1, ce: 9041, eqlist: "10493,20162,30162,40353", index: 1 }],
  freepowerbit: [{ bit_1: 0, id_1: 1 }, { bit_2: 0, id_2: 2 }],
  luckydata: [{ id: 1, value: 66 }, { id: 2, value: 168 }],
  shoplist: [{ cnt: 0, id: 1 }],
  ex_box_num: 6, new_box_num: 15, safe_box_num: 17,
  maxLevelId: 2, nlb: 1825, sjb: 45840, txd: 170, guide: 1, newbiebit: 2, suitcard: [],
  task_bits: 0, task_detail: [{ type: 1, cnt: 0 }, { type: 2, cnt: 0 }],
};
const SANGUO = {
  ok: true, code: 0, levelId: 204, passId: 0, freenum: 0, buynum: 0,
  talent_score: 650, talent_rate: 12.0,
  levelData: [], formation: [{ id: 0, lv: 10 }],
  logData: [{ bossid: 102, levelid: 102, dropitem: "92_38_6", percent_hp: 62,
    reduce_hp: 5192, startts: 1790354523, stepData: [],
    passData: { getts: 0, isget: 0, sitem: "" } }],
  travel_elf_arr: [], img_items: [], ex_box_num: 6, safe_box_num: 0, shoplist: [],
  task_bits: 0, task_detail: [{ type: 1, cnt: 0 }],
};
// act=9 关卡表是 {levelid: {...}} 字典（不是数组），前端要自己摊平
const SANGUO_EXTRA = { ok: true,
  levels: { ok: true, code: 0, data: {
    101: { levelid: 101, bossid: 101, dropitem: "92_38_6" },
    102: { levelid: 102, bossid: 102, dropitem: "92_38_5" } } }, lineup: null };
// 竞技榜（card_travel_elf_pvp act=14）：服务端不返回名次、数组还是乱序的，
// 后端要按 score 降序重新编号；可赞的 10 人全在第 50 名之外 → 默认取 100 条。
const PVP = {
  ok: true, code: 0, day_ts: 1790524800, total: 1000, rank_id: 0,
  list: [
    { rank: 999, uin: 32650660, nick: "迅驰", score: 4904, ce: 138768, ts: 1790510364 },
    { rank: 3, uin: 285416308, nick: "烟雨濛濛", score: 4895, ce: 144093, ts: 1790510338, zan: 9672 },
  ],
  my_rank: null, my_score: 1885, my_ce: 360, my_zan_num: 0, zan_bit: 0,
  zan_targets: [{ rank: 2, uin: 32650660, nick: "迅驰", score: 4904, ce: 138768 },
                { rank: 3, uin: 285416308, nick: "烟雨濛濛", score: 4895, ce: 144093 }],
  min_score: 2248, max_score: 4922,
};
// 竞技场状态（pvp act=1）：两个次数口径都整份透出，surplus / free_pvp_num 语义未定案
const PVP_EXTRA = {
  ok: true, code: 0,
  state: { ok: true, code: 0, data: {
    surplus: 4, free_pvp_num: 0, pvp_num: 0, flush_num: 1,
    score: 312, revenge_num: 2, battle_arr: [3, 4, 2],
    enemy_list: [{ uin: 10012, name: "沧海月明", ce: 312232798 }],
  } },
  store: { ok: true, code: 0, data: { list: [{ id: 1 }] } },
};

w.fetch = (u, opt) => {
  const url = String(u).startsWith("http") ? String(u) : BASE + String(u);
  calls.push({ url, body: opt && opt.body });
  const payload = url.includes("/api/home/state") ? HOME_STATE
    : url.includes("/api/match/pvp-extra") ? PVP_EXTRA
    : url.includes("/api/match/pvp") ? PVP
    : url.includes("/api/match/sanguo-extra") ? SANGUO_EXTRA
    : url.includes("/api/match/explore") ? XIYOU
    : url.includes("/api/match/sanguo") ? SANGUO
    : url.includes("/api/match/union-extra") ? UNION_EXTRA
    : url.includes("/api/match/union") ? UNION
    : url.includes("/api/match/gvg") ? GVGV
    : {
      ok: true, code: 0, bound: true,
      cats: [{ elf_id: 3, level: 20, mood: 85, equip: [1, 2, 3, 4] }],
      equip: {
        cat_home_dress_head_attr: [{ id: 10101, eq_attr: [{ id: 1, value: 30 }] }],
        cat_home_dress_hand_attr: [{ id: 20101, eq_attr: [{ id: 5, value: 20 }] }],
        cat_home_dress_clothing_attr: [{ id: 30101, eq_attr: [{ id: 9, value: 25 }] }],
        cat_home_dress_other_attr: [{ id: 40101, eq_attr: [{ id: 12, value: 25 }] }],
      },
      theme: [{ id: 1, value: 40, name: "唱歌" }],
      attrs: { 1: "唱歌", 5: "跑动" },      // 后端下发的属性名全表
      suggest: { equip_ids: [10101, 20101, 30101, 40101], cover: 100,
                 text: "头:魔法帽(唱歌+30) 手:自拍杆(跑动+20)" },
                                            // ↑ 上面的 mock 装备 id 不在官方名表里，
                                            // 但 equip_text 由后端拼，前端只管显示
      performe_num: 2,
      races: [{ cat_id: 3, total_value: 1200, level_score: 70, cover: 100, left: 1 }],
      ran: 2, left_before: 2, left_after: 0,
      items: [{ ok: true, k: "比赛领奖 act=17 id=1", content: "92_32_8" }],
      list: [], total: 0, data: {},
      message: "ok",
    };
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
};
let confirmed = 0;
w.confirm = () => { confirmed += 1; return true; };
w.alert = () => {};

const assert = (c, m) => { console.log((c ? "  ✓ " : "  ✗ ") + m); if (!c) process.exitCode = 1; };
const $ = (id) => w.document.getElementById(id);

// ---------- 1) 结构：子玩法 tab 恢复，默认猫窝日常 ----------
console.log("\n[1] 子玩法 tab 结构");
assert(!!$("match-sub-bar"), "二级 tab 栏 #match-sub-bar 存在");
const tabs = [...w.document.querySelectorAll("#match-sub-bar .tab")].map((b) => b.getAttribute("data-msub"));
// 2026-09-27：① 西游记 / 三国战役拆成两个独立 tab（原本挤在「公会战 · 西游三国」里）
//             ② 公会战并入工会 tab、删除「各玩法一键」（功能归到各 tab）
// 2026-09-28：「竞技场 · 日榜」并入猫窝日常（榜单不展示，点赞/挑战都归一键全部）→ 5 个 tab
assert(tabs.join(",") === "home,race,union,xiyou,sanguo", "5 个子玩法 tab，猫窝日常排第一");
assert(!w.document.getElementById("msub-pvp"), "「竞技场 · 日榜」tab 已删除（状态并入猫窝日常）");
assert(!w.document.getElementById("msub-gvg"), "公会战不再单独占一个 tab");
assert(!w.document.getElementById("msub-modes"), "「各玩法一键」tab 已删除");
assert(!!$("match-gvg-box").closest("#msub-union"), "公会战块并进了工会 tab");
assert(!!w.document.querySelector('#msub-union [data-act="mm-fight-union"]'),
  "出兵按钮只有公会战块里那一个（工会工具栏不再重复放）");
assert(tabs.indexOf("xiyou") < tabs.indexOf("sanguo"), "西游记在三国战役之前");
assert(!!$("match-xiyou") && !!$("match-sanguo"), "西游记 / 三国战役的刷新按钮各自独立");
assert(!!$("match-sanguo-box").closest("#msub-sanguo"), "三国状态块归位在「三国战役」面板");
assert(!!$("match-xiyou-box").closest("#msub-xiyou"), "西游记状态块归位在「西游记」面板");
// 「各玩法一键」删掉后，原本只有它有的动作必须能在对应 tab 里找到
const raceActs = [...w.document.querySelectorAll('#msub-race [data-act="mm-claim"]')]
  .map((b) => b.getAttribute("data-only"));
assert(raceActs.indexOf("comp") >= 0, "每日比赛领奖归到「出战（比赛）」tab");
assert([...w.document.querySelectorAll('#msub-xiyou [data-act="mm-claim"]')]
  .some((b) => b.getAttribute("data-only") === "explore"), "西游记领奖归到西游记 tab");
assert([...w.document.querySelectorAll('#msub-sanguo [data-act="mm-claim"]')]
  .some((b) => b.getAttribute("data-only") === "sanguo"), "三国领奖归到三国 tab");
// 竞技场（2026-09-28 并入猫窝日常）：动作钮与状态块都住 #msub-home，榜单本身不渲染
const homeActs = [...w.document.querySelectorAll('#msub-home [data-act]')]
  .map((b) => b.getAttribute("data-act"));
assert(["mm-claim", "mm-flush", "mm-fight-pvp", "mm-arena-zan"].every((a) => homeActs.includes(a))
  && homeActs.length === 4, "竞技场 4 个动作钮都在「猫窝日常」里：" + homeActs.join(","));
assert(!$("match-arena"), "竞技场不再单占一个块（2026-09-28 用户定调）");
for (const k of tabs) assert(!!$("msub-" + k), `面板 #msub-${k} 存在`);
assert(!!$("msub-home").classList.contains("on"), "默认显示猫窝日常（主界面）");
assert(!$("msub-race").classList.contains("on"), "比赛面板默认隐藏（子玩法）");
assert(!!w.document.querySelector('#msub-home #home-all'), "猫窝一键按钮在猫窝面板里");
assert(!!$("match-runall") && !!$("match-runall").closest("#msub-race"), "一键比赛按钮归位在「出战（比赛）」子面板");
assert(!!$("match-theme-box"), "存在主题卡容器 #match-theme-box");
assert(!!$("match-log-box"), "存在出战记录容器 #match-log-box");
assert(!!w.document.querySelector("[data-mlog]"), "右侧比赛日志容器 #match-mlog-box[data-mlog] 存在");

// ---------- 2) 按钮绑定不能指向不存在的函数 ----------
// 上一版的坑：bind 到了全项目都不存在的 feedAllCats()，点下去必 ReferenceError，
// 所以这里不只看「有没有绑」，而是**真的把 app.js 注入执行 + 逐个点击**。
console.log("\n[2] 按钮绑定与实际函数对账");
const btnIds = [...html.matchAll(/<button[^>]*id="([\w-]+)"/g)].map((m) => m[1]);
const boundIds = new Set([...appjs.matchAll(/bind\("([\w-]+)"/g)].map((m) => m[1]));
const unbound = btnIds.filter((id) => !boundIds.has(id));
console.log("   页面按钮 " + btnIds.length + " 个，其中 bind 过 " + btnIds.filter((i) => boundIds.has(i)).length + " 个");
assert(!/feedAllCats/.test(appjs), "已移除绑到不存在函数的「一键喂到 80」死按钮");
for (const id of ["match-runall", "match-race", "match-dress", "match-dressrace", "claim-all"]) {
  assert(btnIds.includes(id), `页面有按钮 #${id}`);
  assert(boundIds.has(id), `#${id} 已 bind`);
}

// ---------- 3) 一键比赛行为（真的注入 app.js 再点按钮） ----------
console.log("\n[3] 一键比赛执行");
try {
  w.eval(appjs);                  // runScripts:"outside-only" 下只能手动注入
} catch (e) {
  console.log("  ✗ app.js 注入失败：" + e.message);
  process.exitCode = 1;
}
assert(typeof w.runAllMatch === "function", "runAllMatch() 已注入到 window");
w.eval(`(async () => {
  switchPane("match");            // 触发 initMatchOnce
  await new Promise(r => setTimeout(r, 80));
  document.getElementById("match-runall").click();
})()`).catch((e) => errors.push(String(e)));

setTimeout(async () => {
  // ---------- 1.5) 竞技场信息并入「点赞」「竞技比赛」两块（2026-09-28 用户定调） ----------
  console.log("\n[1.5] 竞技场信息归位（点赞块 + 竞技比赛块，不再单开块）");
  const arenaBase = calls.length;   // 后面的全局断言还要数 run-all，这里不能清 calls
  assert(!$("match-arena"), "不再有独立的「竞技场 · 每日」块");
  assert(!$("match-pvp-box"), "旧竞技榜列表容器已删除");
  w.eval("loadHomeState(); loadMatchPvp(); loadMatchPvpExtra();");
  await new Promise((r) => setTimeout(r, 80));
  const likeTxt = $("home-like-st").textContent, arenaTxt = $("home-arena-st").textContent;
  assert(/每日比赛榜 .*竞技比赛榜/.test(likeTxt), "「点赞」块仍显示两个榜的点赞状态：" + likeTxt);
  assert(/今日可赞首位 迅驰（第 2 名）/.test(likeTxt),
    "竞技榜「今日可赞首位」并入点赞块，与游戏「点赞」页对得上：" + likeTxt);
  assert(/剩余 \d+ \/ 每日免费/.test(arenaTxt), "「竞技比赛」块显示剩余场次：" + arenaTxt);
  assert(/我的名次 未上榜（榜内最低 2248）/.test(arenaTxt),
    "没进榜单时给真实原因（榜内最低分），不写「未进前 1000」这种臆断：" + arenaTxt);
  assert(/当前对手 沧海月明/.test(arenaTxt), "「竞技比赛」块显示当前第一个对手");
  assert(!/act=|_num|battle_arr/.test(likeTxt + arenaTxt),
    "两个状态行不含 act= / 字段名等代码标识：" + likeTxt + " | " + arenaTxt);
  // 手动补点：真点一次，确认走 /api/home/like（一键全部里那次才是主路径）
  w.eval(`(async () => {
    document.querySelector('#msub-home [data-act="mm-arena-zan"]').click();
    await new Promise(r => setTimeout(r, 60));
  })()`).catch((e) => errors.push(String(e)));
  assert(calls.slice(arenaBase).some((c) => c.url.includes("/api/home/like")
    && /arena/.test(c.body || "")), "「点赞竞技榜」真的调了 /api/home/like（target=arena）");
  assert(confirmed > 0, "一键比赛前弹确认框（会消耗次数）");
  const ra = calls.filter((c) => c.url.includes("/api/match/run-all"));
  assert(ra.length === 1, "调用了一次 /api/match/run-all");
  const ca = calls.filter((c) => c.url.includes("/api/match/claim-all"));
  assert(ca.length === 0, `一键比赛不再顺带领奖（实际调了 ${ca.length} 次 /api/match/claim-all）`);
  assert(!/顺带领取各玩法奖励/.test(appjs), "confirm 文案已去掉「顺带领取各玩法奖励」");
  assert(!/打满今日场次 \+ 领奖/.test(appjs), "按钮 idle 文案已去掉「+ 领奖」");
  const logs = w.document.querySelector("[data-mlog]").textContent;
  assert(/一键比赛/.test(logs), "比赛日志里有一键比赛记录");
  assert(/打了\s*2\s*场|打了 2 场/.test(logs) || /一键比赛\] 完成/.test(logs), "日志记录了场次与剩余次数");
  // 心情前置（用户 2026-09-27 定调）：用猫的一键操作必须先补心情到 80
  assert(/先把心情喂到 80/.test(logs), "一键比赛日志明说「先把心情喂到 80」（心情前置）");
  assert(/async function raceOnce[\s\S]{0,400}loadMatchState\(true\)/.test(appjs),
    "raceOnce 挑猫前读最新状态（心情会掉，不能靠旧缓存筛猫）");
  // 猫窝日常确实被默认加载过
  assert(calls.some((c) => c.url.includes("/api/home/state")), "默认子模块（猫窝日常）自动拉了 /api/home/state");
  // tab 切换：点「出战（比赛）」→ race 显示、home 隐藏
  w.eval(`switchMatchSub("race")`);
  assert($("msub-race").classList.contains("on") && !$("msub-home").classList.contains("on"),
    "切到「出战（比赛）」后 race 显示、猫窝隐藏");
  // 比赛状态只留比赛字段
  w.eval(`renderMatchState()`);
  const st = $("match-state-box").textContent;
  assert(/猫咪数量/.test(st) && /今日剩余次数/.test(st), "状态卡有比赛字段（猫咪数量/今日剩余次数）");
  assert(!/每日期望/.test(st), "状态卡不再重复显示 performe_num（旧「每日期望次数」行已删）");
  assert(!/talent_score|clover_num|season_rank|unionid|dzs/.test(st), "状态卡不再渲染 talent/clover/dzs/season/unionid");
  // 键值行样式：k 与 v 分离（不再堆在一起）
  assert(!!w.document.querySelector("#match-state-box .rv .k"), "状态行是 .rv 键值分离结构");

  // ---- 可读穿装 / 属性名（用户 2026-09-27：「一串数字不知道是啥，属性5是啥」）----
  // 属性名只能来自后端 /api/match/theme 的 attrs 表，前端不许自己编
  const themeCall = calls.find((c) => c.url.includes("/api/match/theme"));
  assert(!!themeCall, "读过 /api/match/theme（属性名与穿装名的来源）");
  assert(/attrs":\s*cd\.attr_names\(\)/.test(MAIN_SRC), "/api/match/theme 回带 attrs 属性名表");
  assert(/function attrName\([\s\S]{0,220}m\.attrs/.test(appjs),
    "attrName() 走后端 attrs 表，查不到才退回「属性N」");
  // 主题渲染显示中文属性名（用户问「属性5是啥」）
  w.eval(`renderMatchTheme()`);
  const themeBox = $("match-theme-box").textContent;
  assert(/唱歌\s*40%/.test(themeBox), "今日主题显示成中文属性名：" + themeBox.slice(0, 60));
  // 出战记录穿装列优先用可读文本，不是裸 id
  assert(/it\.equip_text/.test(appjs), "出战记录的穿装列用后端可读文本 equip_text");
  assert(/function equipBest[\s\S]{0,900}sug\.text/.test(appjs),
    "「最优穿搭建议」日志打印中文穿装（sug.text）");
  assert(/d\["equip_text"\]\s*=\s*equip_summary/.test(MATCH_SRC)
      && /"equip_text":\s*d\["equip_text"\]/.test(MATCH_SRC),
    "后端出战记录落盘 equip_text（可读穿装进出战记录）");
  // ---- 工会 / 公会战玩法（用户 2026-09-27：「把社团公会玩法做了」）----
  assert(/async def run_union\(/.test(MATCH_SRC), "后端有 run_union() 一键工会编排（领奖→点赞→出兵）");
  assert(/union_run\.json/.test(MATCH_SRC), "工会动作落盘 data/union_run.json（可复查）");
  assert(/只有第 2 段（昵称）与第 3 段（贡献）有实证/.test(MATCH_SRC),
    "成员名册只有实证过的两段被命名，其余段不硬译");
  assert(/\/api\/match\/union-all/.test(MAIN_SRC) && /\/api\/match\/union-claim/.test(MAIN_SRC)
      && /\/api\/match\/union-zan/.test(MAIN_SRC), "工会领奖/点赞/一键工会三个路由齐全");
  // 成员贡献榜：昵称 + 贡献（members 第 2/3 段）
  // 注意：app.js 里的 let 变量外部 eval 赋不进去，必须走 mock fetch 的真实链路
  w.eval(`(async () => { await loadMatchUnion(); await loadMatchUnionExtra(); await loadMatchGvg(); })()`);
  await new Promise((r) => setTimeout(r, 60));
  const memBox = $("match-union-mem-box").textContent;
  assert(/紫旋の梦蝶/.test(memBox) && /6213/.test(memBox),
    "成员贡献榜显示昵称+贡献：" + memBox.slice(0, 50));
  assert(/总贡献\s*14905/.test($("match-union-mem-cnt").textContent),
    "成员榜计数显示人数与总贡献：" + $("match-union-mem-cnt").textContent);
  assert(!!w.document.querySelector("#match-union-mem-box [data-zan]"), "成员行有「赞」按钮");
  // 工会任务 / 碎片 / 商店三块
  const ux = $("match-union-extra-box").textContent;
  // 「今日已出兵次数」两处同一个 num，合并后只在公会战块显示一次（去重）
  assert(!/今日已出兵次数/.test(ux), "工会任务块不再重复显示出兵次数（与公会战块去重）");
  assert(/工会资金11234/.test(ux), "工会资金显示真实值 11234");
  assert(/工会商店/.test(ux) && /80/.test(ux), "工会商店列出商品消耗");
  // 公会战：进度对比条 + 今日已出兵次数
  const gv = $("match-gvg-box").textContent;
  assert(/我方 478,?555/.test(gv) && /敌方 595,?420/.test(gv), "公会战显示我方/敌方进度：" + gv.slice(0, 60));
  assert(/进度占比 我方 4[45]%/.test(gv), "公会战进度占比算对（478555/(478555+595420)≈45%）");
  assert(/今日已出兵 1 次/.test($("match-gvg-fightcnt").textContent),
    "公会战区显示今日已出兵次数：" + $("match-gvg-fightcnt").textContent);
  assert(!/每日上限|上限\s*\d/.test($("match-union-extra-box").textContent),
    "不臆造出兵每日上限（界面不出现「上限 N」这类数字）");

  // ---- 西游记 / 三国战役：两个独立玩法各自一个 tab（2026-09-27）----
  // 记忆教训：app.js 里的 let 变量外部 eval 赋不进去，必须走 mock fetch 的真实链路
  w.eval("(async () => { await loadMatchXiyou(true); await loadMatchSanguo(true); await loadMatchSanguoLevels(); })()");
  await new Promise((r) => setTimeout(r, 60));
  const xb = $("match-xiyou-box").textContent;
  const xt = $("match-xiyou-team").textContent;
  const sg = $("match-sanguo-box").textContent;
  const lvTxt = $("match-sanguo-levels").textContent;
  assert(calls.some((c) => c.url.includes("/api/match/explore")), "西游记走自己的 /api/match/explore 端点");
  assert(calls.some((c) => /\/api\/match\/sanguo\?/.test(c.url)), "三国战役走 /api/match/sanguo（带任务）"
    + JSON.stringify(calls.map((c) => c.url).filter((u) => u.includes("sanguo"))));
  assert(/当前关卡\s*2/.test(xb), "西游记显示当前关卡：2");
  assert(/材料三种\s*1825 \/ 45840 \/ 170/.test(xb), "西游记显示真实材料数 1825/45840/170");
  assert(/当前关已收步数\s*1 \/ 1/.test(xb), "西游记统计当前关已收 1/1");
  assert(/闯关记录\s*1 关，已过 0 关/.test(xb), "西游记闯关记录统计（'已过'≠'已收'，不混算）");
  assert(/布阵 1 只/.test($("match-xiyou-team-cnt").textContent) && /猫 1 · 战力 9041/.test(xt),
    "西游记布阵块列出猫与战力：" + xt.slice(0, 40) + " | cnt=" + $("match-xiyou-team-cnt").textContent);
  assert(/当前关卡\s*204/.test(sg) && /每日免费次数\s*0/.test(sg),
    "三国战役显示当前关卡 204 / 每日免费 0");
  assert(/天赋分 \/ 天赋率\s*650 \/ 12/.test(sg), "三国战役显示天赋分/天赋率 650/12");
  assert(/2 关/.test($("match-sanguo-levels-cnt").textContent), "三国关卡表把字典摊平成 2 关："
    + $("match-sanguo-levels-cnt").textContent);
  assert(/关卡 101/.test(lvTxt) && /boss 101/.test(lvTxt), "三国关卡列表显示 bossid：" + lvTxt.slice(0, 40));
  // 两个玩法绝不能互相串数据
  assert(!/nlb/.test(sg), "三国面板不出现西游记字段（nlb）");
  assert(!/levelId/.test(xb), "西游记面板不出现三国字段（levelId）");

  assert(!errors.length, "运行期无 JS 报错" + (errors.length ? "：" + errors.slice(0, 2).join(" | ") : ""));
  w.close();
  process.exit(process.exitCode || 0);
}, 400);
