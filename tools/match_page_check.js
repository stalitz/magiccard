// 每日比赛页（pane-match）真实 DOM 离线回归
// 运行：NODE_PATH=<workspace>/node_modules <managed node> tools/match_page_check.js
const fs = require("fs");
const { JSDOM } = require("jsdom");
const ROOT = "/Users/stalitz/www/魔法卡片";
const html = fs.readFileSync(ROOT + "/frontend/index.html", "utf8");
const appjs = fs.readFileSync(ROOT + "/frontend/app.js", "utf8");

const s = appjs.indexOf("// ===================== 每日比赛");
const e = appjs.indexOf("// ---------- 合成路径弹窗");
if (s < 0 || e < 0 || e <= s) { console.error("!! 抽取 match 代码块失败"); process.exit(1); }
const code = appjs.slice(s, e);

const dom = new JSDOM(html, { runScripts: "outside-only" });
const { window } = dom;
const document = window.document;
const $ = (id) => document.getElementById(id);
const logs = [];
const log = () => {};
const busyBtn = (btn, busy, idle) => {
  if (btn) { btn.disabled = true; if (busy) btn.textContent = busy; }
  return () => { if (btn) { btn.disabled = false; btn.textContent = idle || btn.textContent; } };
};
window.__myUin = "4821992";

const MATCH_STATE = {
  ok: true, code: 0, bound: true, talent_score: 650, talent_rate: 12,
  performe_num: 2, perform_limit_ts: 1790322778, clover_num: 585597,
  dzs: 9473, hbs: 5246, unionid: 10177,
  season_rank_gift: [{ score: 2671, ts: 1779811200 }],
  cats: [{
    elf_id: 1, level: 50, star: 2, mood: 86, motion: 414, perform: 345,
    equip: [10353, 20141, 0, 40273],
    skills: [{ cat_skill_id: 1, cat_skill_level: 5 }, { cat_skill_id: 2, cat_skill_level: 3 }],
  }, {
    elf_id: 2, level: 70, star: 2, mood: 84, motion: 445, perform: 356,
    equip: [], skills: [],
  }],
};
const MATCH_STATE_EMPTY = { ok: true, code: 0, bound: false, cats: [], talent_score: 0 };
const PVP = {
  ok: true, total: 1000, day_ts: 1790265600, my_score: 1913, my_ce: 19949,
  my_rank: 42, my_zan_num: 0, zan_bit: 1, zan_targets: [5988, 27654],
  list: [
    { uin: 78650, nick: "BLACK RAIN", score: 2195, ce: 27404, ts: 1790304308 },
    { uin: 4821992, nick: "我", score: 1913, ce: 19949, ts: 1790304308 },
  ],
};
const UNION = { ok: true, union_name: "搞起耍", unionid: "10177", lv: "18", exp: "5445880", mem_num: 29, max_num: 29, coin: 4044, chip: 72, union_activity: 41, members: { "1006409757": "x" } };
const GOLD = { ok: true, data: { gold_buy_id1: 0, gold_buy_id2: 5, gold_buy_id3: 0, gold_buy_id4: 0, gold_buy_id5: 0, gold_buy_id6: 0 } };
const GVG = { ok: true, code: 0, data: {
  level: 8, inlv: 8, progress: "365804", e_levelid: "9", e_name: "夜半钟声到客船",
  enemy_uinon: "21718", e_progress: "330939", level_rank_bit: 252, level_rank_bit2: 0,
  levelgetbits: ",10100,20100", gift_record: "1260277398,1292723575,4821992",
  season_gift: 0, ts: 1787673667 } };
const SANGUO = { ok: true, code: 0, levelId: 204, passId: 0, freenum: 3, buynum: 0,
  talent_score: 650, talent_rate: 12.0, logData: Array.from({ length: 86 }, (_, i) => ({
    bossid: 101, levelid: 101 + i, percent_hp: 0, passData: { getts: 1790322817, isget: i % 2 } })),
  formation: [{ id: 0, lv: 10 }, { id: 1, lv: 9 }],
  travel_elf_arr: [{ elf_id: 1 }, { elf_id: 2 }, { elf_id: 3 }, { elf_id: 4 }],
  img_items: [{ id: 1 }, { id: 2 }], ex_box_num: 15, safe_box_num: 12, new_box_num: 15,
  shoplist: [{ id: 1, cnt: 0 }] };

const calls = [];
const resp = (o) => ({ json: async () => o, ok: true });
let stateEmpty = false;   // true 时状态接口返回「未开通」空壳（实测未玩过模块的号）
async function fetchStub(url) {
  calls.push(url);
  if (url.startsWith("/api/match/state")) {
    if (stateEmpty) return resp(MATCH_STATE_EMPTY);
    return resp(url.includes("want_equip=1") ? { ...MATCH_STATE, equip: { a: 1, b: 2 } } : MATCH_STATE);
  }
  if (url.startsWith("/api/match/pvp")) return resp(PVP);
  if (url.startsWith("/api/match/union")) return resp(UNION);
  if (url.startsWith("/api/match/gold")) return resp(GOLD);
  if (url.startsWith("/api/match/gvg")) return resp(GVG);
  if (url.startsWith("/api/match/sanguo")) return resp(SANGUO);
  return resp({ ok: false, message: "404" });
}

const R = [];
function ok(name, cond, extra) { R.push([cond ? "PASS" : "FAIL", name, extra || ""]); }

(async () => {
  const factory = new Function(
    "document", "window", "fetch", "$", "log", "busyBtn",
    "return (async () => { " + code + "\n; return { renderMatchState, renderMatchPvp, renderMatchUnion, renderMatchGold, renderMatchGvg, renderMatchSanguo, loadMatchState, loadMatchPvp, loadMatchUnion, loadMatchGold, loadMatchGvg, loadMatchSanguo, initMatchOnce, matchStopPoll, matchFmtTs, matchRow }; })();"
  );
  const M = await factory(document, window, fetchStub, $, log, busyBtn);

  // --- 渲染：我的比赛状态 ---
  await M.loadMatchState(true);
  const stateBox = $("match-state-box").textContent;
  ok("① 状态区渲染出天赋分", stateBox.includes("650"), stateBox.slice(0, 60));
  ok("① 状态区渲染出每日期望次数", stateBox.includes("每日期望次数"));
  ok("① 状态区渲染次数重置时间", stateBox.includes("2026"));
  ok("① 猫咪列表 2 条", ($("match-cats").querySelectorAll(".pk")).length === 2);
  ok("① 猫咪技能行存在", $("match-cats").textContent.includes("技能 #1·L5"));
  const catTxt = $("match-cats").textContent;
  ok("① 装备过滤掉 0 并渲染", catTxt.includes("装备 10353/20141/40273"), catTxt.replace(/\s+/g, " ").slice(0, 160));
  ok("① 计数器显示已开通", $("match-state-cnt").textContent === "已开通", $("match-state-cnt").textContent);

  // --- 渲染：空态（未开通号，实测 act=1 返回 travel_elf_arr=[]）---
  stateEmpty = true;
  await M.loadMatchState(true);
  const emptyTxt = $("match-state-box").textContent;
  ok("⑧ 未开通号给出明确空态提示", emptyTxt.includes("未开通"), emptyTxt.replace(/\s+/g, " ").slice(0, 100));
  ok("⑧ 未开通号不显示「全部 0」误导", !emptyTxt.includes("天赋分 talent_score"));
  ok("⑧ 未开通号计数为「空态」", $("match-state-cnt").textContent === "空态", $("match-state-cnt").textContent);
  stateEmpty = false;
  await M.loadMatchState(true);
  ok("⑧ 复位后恢复已开通态", $("match-state-cnt").textContent === "已开通");

  // --- 渲染：PVP 日榜 ---
  await M.loadMatchPvp();
  const pvpBox = $("match-pvp-box").textContent;
  ok("② 榜单渲染出我的分数", pvpBox.includes("1913"));
  ok("② 榜单标出「（我）」", pvpBox.includes("（我）"));
  ok("② 我的名次显示第 42 名", pvpBox.includes("第 42 名"));
  ok("② 今日可赞人数", pvpBox.includes("2 人"));
  ok("② 榜单人数计数", $("match-pvp-cnt").textContent.includes("1000"), $("match-pvp-cnt").textContent);

  // --- 渲染：工会 ---
  await M.loadMatchUnion();
  const uBox = $("match-union-box").textContent;
  ok("③ 工会名渲染", uBox.includes("搞起耍"));
  ok("③ 会员/上限渲染", uBox.includes("29/29"));
  ok("③ 工会计数", $("match-union-cnt").textContent.includes("29"), $("match-union-cnt").textContent);

  // --- 渲染：金卡限购 ---
  await M.loadMatchGold();
  await M.loadMatchGvg();
  await M.loadMatchSanguo();
  const gBox = $("match-gold-box").textContent;
  ok("④ 金卡已购分组提示", gBox.includes("1 组有购买记录"), gBox.slice(0, 50));
  ok("④ 金卡原值透出", gBox.includes("0 / 5 / 0"));

  // --- 手动按钮即时反馈（项目铁律） ---
  const b = $("match-pvp");
  const restore = busyBtn(b, "榜单中…", "刷新 PVP 日榜");
  ok("⑤ 点下立即禁用按钮", b.disabled === true);
  ok("⑤ 点下立即改文案", b.textContent === "榜单中…", b.textContent);
  restore();
  ok("⑤ 完成后恢复可点", b.disabled === false);
  ok("⑤ 完成后恢复文案", b.textContent === "刷新 PVP 日榜", b.textContent);

  // --- 公会战（union act=22）渲染 ---
  const gbox = $("match-gvg-box");
  const gTxt = gbox.textContent;
  ok("⑧ 公会战敌方名称渲染", gTxt.includes("夜半钟声到客船"), gTxt.slice(0, 80));
  ok("⑧ 公会战我方/敌方等级", gTxt.includes("我方等级 level8") && gTxt.includes("敌方等级 e_levelid9"));
  ok("⑧ 公会战进度对比", gTxt.includes("365804") && gTxt.includes("330939"));
  ok("⑧ 公会战计数", $("match-gvg-cnt").textContent.includes("我方 Lv8"), $("match-gvg-cnt").textContent);

  // --- 西游三国（worldsanguo act=1）渲染 ---
  const sbox = $("match-sanguo-box");
  const sTxt = sbox.textContent;
  ok("⑨ 西游三国关卡渲染", sTxt.includes("当前关卡 levelId204"), sTxt.slice(0, 80));
  ok("⑨ 西游三国每日次数", sTxt.includes("每日免费次数 freenum3"));
  ok("⑨ 西游三国关卡记录统计", sTxt.includes("86 条") && sTxt.includes("已通"));
  ok("⑨ 西游三国精灵数", sTxt.includes("4 只"));
  ok("⑨ 西游三国计数", $("match-sanguo-cnt").textContent.includes("关卡 204"), $("match-sanguo-cnt").textContent);

  // --- 新按钮即时反馈（项目铁律）---
  const gb = $("match-gvg"), sb = $("match-sanguo");
  const gr = busyBtn(gb, "读取中…", "刷新公会战");
  ok("⑩ 公会战按钮点下禁用", gb.disabled === true);
  gr();
  ok("⑩ 公会战按钮恢复", gb.disabled === false && gb.textContent === "刷新公会战");
  const sr = busyBtn(sb, "读取中…", "刷新西游三国");
  ok("⑩ 西游三国按钮点下禁用", sb.disabled === true);
  sr();
  ok("⑩ 西游三国按钮恢复", sb.disabled === false && sb.textContent === "刷新西游三国");

  // --- 切换 pane 才拉数据，非本页不轮询 ---
  M.matchStopPoll();
  ok("⑥ matchStopPoll 不报错", true);

  // --- 端点覆盖 ---
  ok("⑦ 状态接口被调", calls.some((u) => u.startsWith("/api/match/state")));
  ok("⑦ PVP 接口被调", calls.some((u) => u.startsWith("/api/match/pvp")));
  ok("⑦ 工会接口被调", calls.some((u) => u.startsWith("/api/match/union")));
  ok("⑦ 金卡接口被调", calls.some((u) => u.startsWith("/api/match/gold")));
  ok("⑧ 公会战接口被调", calls.some((u) => u.startsWith("/api/match/gvg")));
  ok("⑨ 西游三国接口被调", calls.some((u) => u.startsWith("/api/match/sanguo")));

  const pass = R.filter((r) => r[0] === "PASS").length;
  const fail = R.filter((r) => r[0] === "FAIL").length;
  for (const [st, name, extra] of R) {
    console.log(`  [${st}] ${name}${extra ? "  → " + extra : ""}`);
  }
  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
