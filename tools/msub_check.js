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
const onKeys = () => ["race", "home", "pvp", "union", "gvg", "modes"]
  .filter((k) => panel(k) && panel(k).classList.contains("on"));

(async () => {
  try { w.eval(appjs); } catch (e) { console.log("注入 app.js 失败:", e.message); process.exit(1); }

  console.log("== 子模块结构 ==");
  const keys = ["race", "home", "pvp", "union", "gvg", "modes"];
  keys.forEach((k) => assert(!!panel(k), `#msub-${k} 存在`));
  assert(onKeys().join(",") === "race", `默认只显示「出战」子模块（当前：${onKeys().join(",") || "空"}）`);

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
  assert(inSub("match-pvp-box", "pvp"), "PVP 日榜在 msub-pvp 内");
  assert(inSub("match-union-box", "union"), "工会在 msub-union 内");
  assert(inSub("match-gvg-box", "gvg"), "公会战在 msub-gvg 内");
  assert(inSub("match-modes-box", "modes"), "各玩法一键在 msub-modes 内");

  console.log("== 首次进入：只加载「出战」子模块 ==");
  calls.length = 0;
  try { await w.eval("(async()=>{ switchPane('match'); await new Promise(r=>setTimeout(r,60)); })()"); }
  catch (e) { console.log("  切换页面失败:", e.message); }
  console.log("  本轮请求：", calls.map((c) => c.replace(BASE, "")).join(" , ") || "（无）");
  assert(calls.some((c) => c.includes("/api/match/state")), "已加载比赛状态（出战子模块）");
  assert(!calls.some((c) => c.includes("/api/match/pvp")), "没有抢先加载 PVP 日榜");
  assert(!calls.some((c) => c.includes("/api/match/union")), "没有抢先加载工会");
  assert(!calls.some((c) => c.includes("/api/match/sanguo")), "没有抢先加载西游三国");

  console.log("== 点击 tab：切到「PVP 日榜」才拉 PVP ==");
  calls.length = 0;
  try {
    await w.eval("(async()=>{ switchMatchSub('pvp'); await new Promise(r=>setTimeout(r,60)); })()");
  } catch (e) { console.log("  切换失败:", e.message); }
  assert(onKeys().join(",") === "pvp", `只显示 PVP 子模块（当前：${onKeys().join(",")}）`);
  assert(calls.some((c) => c.includes("/api/match/pvp")), "切过去才拉 PVP 接口");
  assert(!calls.some((c) => c.includes("/api/match/state")), "没把比赛状态又拉一遍");

  console.log("== 点击 tab：公战 / 西游三国 一起 ==");
  calls.length = 0;
  try {
    await w.eval("(async()=>{ switchMatchSub('gvg'); await new Promise(r=>setTimeout(r,60)); })()");
  } catch (e) { console.log("  切换失败:", e.message); }
  assert(onKeys().join(",") === "gvg", "只显示公战子模块");
  assert(calls.some((c) => c.includes("/api/match/gvg")), "拉公会战接口");
  assert(calls.some((c) => c.includes("/api/match/sanguo")), "拉西游三国接口");

  console.log("== 渲染不报错 ==");
  try {
    await w.eval("(async()=>{ await loadMatchState(true); await loadMatchPvp(); await loadMatchUnion();"
      + " await loadMatchGold(); await loadMatchGvg(); await loadMatchSanguo();"
      + " await loadMatchModes(); await loadMatchLog(); renderMatchState(); })()");
    assert(true, "8 个渲染函数全部跑通，无异常");
  } catch (e) { assert(false, "渲染抛错：" + e.message); }

  assert(errors.length === 0, "无 jsdom 运行时错误" + (errors.length ? "：" + errors.slice(0, 3).join(" | ") : ""));
  console.log(process.exitCode ? "\n===== 有 FAIL =====" : "\n===== 全部 PASS =====");
  // 页面里有 setInterval（60s 轮询），不显式退出会一直挂着
  w.close();
  process.exit(process.exitCode || 0);
})();
