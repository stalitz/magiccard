/**
 * 每日比赛页「子模块」结构冒烟（2026-09-26）
 * 验收：六个子模块 panel 存在且互斥显示；默认只加载「出战」子模块；
 * 切到某个子模块才拉它自己的接口；每个卡片仍在自己那层的 msub 内（不错位）。
 */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "frontend/index.html"), "utf-8");
const appjs = fs.readFileSync(path.join(ROOT, "frontend/app.js"), "utf-8");
const BASE = "http://127.0.0.1:8000";

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push(String(e && e.message)));
vc.on("error", (...a) => errors.push(a.join(" ")));

const dom = new JSDOM(html, { runScripts: "outside-only", url: BASE, virtualConsole: vc });
const w = dom.window;

const calls = [];
w.fetch = (u) => {
  const url = String(u).startsWith("http") ? String(u) : BASE + String(u);
  calls.push(url);
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({
      ok: true, code: 0, bound: true, cats: [{ elf_id: 1, level: 5, worn: [], equip: [] }],
      likes: { match: 0, arena: 0 }, gacha: { free_used: 0, score: 0 },
      list: [], data: { list: [] }, message: "ok",
    }),
  });
};
w.confirm = () => true;
w.alert = () => {};

const assert = (c, m) => { console.log((c ? "  ✓ " : "  ✗ ") + m); if (!c) process.exitCode = 1; };
const panel = (k) => w.document.getElementById("msub-" + k);
// 2026-09-27：西游记 / 三国战役拆成独立 tab，key 列表要同步
// 2026-09-28：「竞技场 · 日榜」并入猫窝日常（榜单没展示价值，点赞/挑战都归一键全部）→ 5 个 tab
const SUB_KEYS = ["home", "race", "union", "xiyou", "sanguo"];
const onKeys = () => SUB_KEYS
  .filter((k) => panel(k) && panel(k).classList.contains("on"));

(async () => {
  try { w.eval(appjs); } catch (e) { console.log("注入 app.js 失败:", e.message); process.exit(1); }

  console.log("== 子模块结构 ==");
  const keys = SUB_KEYS;
  keys.forEach((k) => assert(!!panel(k), `#msub-${k} 存在`));
  assert(onKeys().join(",") === "home", `默认只显示「猫窝日常」主界面（当前：${onKeys().join(",") || "空"}）`);

  const tabs = [...(w.document.getElementById("match-sub-bar") || { querySelectorAll: () => [] })
    .querySelectorAll(".tab")].map((b) => b.getAttribute("data-msub"));
  assert(tabs.join(",") === keys.join(","), `tab 顺序与 panel 一致：${tabs.join(" > ")}`);

  // 每张卡片仍嵌在它自己的 msub 里（防 DOM 错位）
  const inSub = (id, sub) => {
    const el = w.document.getElementById(id);
    if (!el) return false;
    let n = el;
    while (n && n !== w.document.body) { if (n.id === "msub-" + sub) return true; n = n.parentElement; }
    return false;
  };
  assert(inSub("match-state-box", "race"), "比赛状态在 msub-race 内");
  assert(inSub("home-box", "home"), "猫窝 daily 在 msub-home 内");
  assert(inSub("match-union-box", "union"), "工会在 msub-union 内");
  assert(inSub("match-gvg-box", "union"), "公会战合并进 msub-union（不再单独一个 tab）");
  // 竞技场：2026-09-28 从独立 tab 并入猫窝日常，只留状态块 + 动作钮，不再列榜单
  assert(inSub("match-arena-box", "home"), "竞技场状态块在「猫窝日常」内（原 msub-pvp 已删）");
  assert(!inSub("match-arena-box", "pvp"), "不再存在 msub-pvp 容器");
  assert(!!w.document.querySelector("#match-arena [data-act='mm-arena-zan']"),
    "竞技场留了「点赞（第一个可赞）」手动入口");

  // 按钮归属（2026-09-26 定调）：顶部工具栏一个按钮都不留，功能按钮全部下沉到所属子模块
  const topBtns = w.document.querySelectorAll("#pane-match > .lib > .lib-toolbar button").length;
  assert(topBtns === 0, `一级工具栏不留按钮（当前 ${topBtns} 个）`);
  const resEl = w.document.getElementById("match-res");
  assert(resEl && resEl.closest("#match-sub-bar"), "状态文字 #match-res 已并入 tab 条");
  assert(w.document.querySelectorAll("#match-sub-bar .tab").length === SUB_KEYS.length,
    `tab 条 ${SUB_KEYS.length} 个子玩法 tab（含独立的西游记 / 三国战役）`);
  ["match-runall", "match-dressrace", "match-race", "match-dress",
    "match-equip", "match-refresh"].forEach((id) =>
    assert(inSub(id, "race"), `#${id} 归位到「出战（比赛）」子模块`));
  // 一键领奖跨 6 个玩法，不属于单一子玩法 → 放「猫窝日常」主界面
  assert(inSub("claim-all", "home"), "#claim-all 归位到「猫窝日常」主界面");
  assert(!w.document.querySelector("#match-claim-all"),
    "出战面板不再挂 match-claim-all（已改名 claim-all 并移走）");
  // 猫卡片挂的是 act=1 的 equip_arr（竞技常驻装），每日比赛每次上台重新穿装不读它
  // → 属于竞技，归「猫窝日常」；出战面板只留每日比赛自己的东西
  assert(inSub("match-cats", "home"), "#match-cats 归位到「猫窝日常」（竞技常驻装列表）");
  assert(!inSub("match-cats", "race"), "#match-cats 不再留在「出战（比赛）」里");

  console.log("== 首次进入：只加载「猫窝日常」 ==");
  calls.length = 0;
  try { await w.eval("(async()=>{ switchPane('match'); await new Promise(r=>setTimeout(r,60)); })()"); }
  catch (e) { console.log("  切换页面失败:", e.message); }
  console.log("  本轮请求：", calls.map((c) => c.replace(BASE, "")).join(" , ") || "（无）");
  assert(calls.some((c) => c.includes("/api/home/state")), "已加载猫窝日常状态");
  // 竞技场状态块就在猫窝日常，日常停留就跟着拉（不再等切 tab）
  assert(calls.some((c) => c.includes("/api/match/pvp")), "猫窝日常已带拉竞技场状态（act=14 我的名次）");
  assert(calls.some((c) => c.includes("/api/match/pvp-extra")), "猫窝日常已带拉竞技场次数/对手");
  assert(!calls.some((c) => c.includes("/api/match/union")), "没有抢先加载工会");
  // 2026-09-27：西游记 / 三国战役各自一个 tab，进页面不许抢先拉
  assert(!calls.some((c) => c.includes("/api/match/sanguo")), "没有抢先加载三国战役");
  assert(!calls.some((c) => c.includes("/api/match/explore")), "没有抢先加载西游记");

  console.log("== 切到「出战（比赛）」：只拉比赛自己的接口 ==");
  calls.length = 0;
  try {
    await w.eval("(async()=>{ switchMatchSub('race'); await new Promise(r=>setTimeout(r,60)); })()");
  } catch (e) { console.log("  切换失败:", e.message); }
  assert(onKeys().join(",") === "race", `只显示出战子模块（当前：${onKeys().join(",")}）`);
  assert(calls.some((c) => c.includes("/api/match/state")), "切过去才拉比赛状态");
  assert(calls.some((c) => c.includes("/api/match/race-log")), "同时拉出战记录");
  assert(!calls.some((c) => c.includes("/api/match/union")), "比赛 tab 不拉工会");
  assert(!calls.some((c) => c.includes("/api/match/sanguo")), "比赛 tab 不拉三国");

  console.log("== 点击 tab：工会（含公会战，一个 tab 一次拉全） ==");
  calls.length = 0;
  try {
    await w.eval("(async()=>{ switchMatchSub('union'); await new Promise(r=>setTimeout(r,60)); })()");
  } catch (e) { console.log("  切换失败:", e.message); }
  assert(onKeys().join(",") === "union", "只显示工会子模块");
  assert(calls.some((c) => c.includes("/api/match/union")), "拉工会接口");
  assert(calls.some((c) => c.includes("/api/match/gvg")), "同时拉公会战（合并进同一 tab）");
  assert(!calls.some((c) => c.includes("/api/match/sanguo")), "工会 tab 不拉三国战役");

  console.log("== 点击 tab：西游记 / 三国战役 各拉各的 ==");
  calls.length = 0;
  try {
    await w.eval("(async()=>{ switchMatchSub('xiyou'); await new Promise(r=>setTimeout(r,60)); })()");
  } catch (e) { console.log("  切换失败:", e.message); }
  assert(onKeys().join(",") === "xiyou", "只显示西游记子模块");
  assert(calls.some((c) => c.includes("/api/match/explore")), "西游记只拉 explore 端点");
  assert(!calls.some((c) => c.includes("/api/match/sanguo")), "西游记 tab 不碰三国端点");

  calls.length = 0;
  try {
    await w.eval("(async()=>{ switchMatchSub('sanguo'); await new Promise(r=>setTimeout(r,60)); })()");
  } catch (e) { console.log("  切换失败:", e.message); }
  assert(onKeys().join(",") === "sanguo", "只显示三国战役子模块");
  assert(calls.some((c) => c.includes("/api/match/sanguo")), "三国战役只拉 sanguo 端点");
  assert(!calls.some((c) => c.includes("/api/match/explore")), "三国 tab 不碰西游端点");

  console.log("== 渲染不报错 ==");
  try {
    await w.eval("(async()=>{ await loadMatchState(true); await loadMatchPvp(); await loadMatchPvpExtra();"
      + " await loadMatchUnion(); await loadMatchGold(); await loadMatchGvg();"
      + " await loadMatchXiyou(); await loadMatchSanguo(); await loadMatchSanguoLevels();"
      + " await loadMatchLog(); renderMatchState(); })()");
    assert(true, "10 个渲染函数全部跑通，无异常（modes 已删）");
  } catch (e) { assert(false, "渲染抛错：" + e.message); }

  assert(errors.length === 0, "无 jsdom 运行时错误" + (errors.length ? "：" + errors.slice(0, 3).join(" | ") : ""));
  console.log(process.exitCode ? "\n===== 有 FAIL =====" : "\n===== 全部 PASS =====");
  // 页面里有 setInterval（60s 轮询），不显式退出会一直挂着
  w.close();
  process.exit(process.exitCode || 0);
})();
