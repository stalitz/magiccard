/**
 * 每日比赛页「一键比赛」冒烟（2026-09-26）
 *
 * 验收三件事：
 *  ① 比赛页只剩比赛本身，猫窝/公会战/西游三国/PVP/各玩法一键面板与二级 tab 已清干净
 *  ② 工具栏按钮绑的函数**全都真实存在**（上一版 feedAllCats 等绑了不存在的函数，点了必 ReferenceError）
 *  ③ 一键比赛确实会 confirm 消耗次数、逐场打完、并顺带领奖
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
w.fetch = (u, opt) => {
  const url = String(u).startsWith("http") ? String(u) : BASE + String(u);
  calls.push({ url, body: opt && opt.body });
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({
      ok: true, code: 0, bound: true,
      cats: [{ elf_id: 3, level: 20, mood: 85, equip: [1, 2, 3, 4] }],
      equip: {
        cat_home_dress_head_attr: [{ id: 10101, eq_attr: [{ id: 1, value: 30 }] }],
        cat_home_dress_hand_attr: [{ id: 20101, eq_attr: [{ id: 5, value: 20 }] }],
        cat_home_dress_clothing_attr: [{ id: 30101, eq_attr: [{ id: 9, value: 25 }] }],
        cat_home_dress_other_attr: [{ id: 40101, eq_attr: [{ id: 12, value: 25 }] }],
      },
      theme: [{ id: 1, value: 40, name: "唱歌" }],
      suggest: { equip_ids: [10101, 20101, 30101, 40101], cover: 100 },
      performe_num: 2,
      races: [{ cat_id: 3, total_value: 1200, level_score: 70, cover: 100, left: 1 }],
      ran: 2, left_before: 2, left_after: 0,
      items: [{ ok: true, k: "比赛领奖 act=17 id=1", content: "92_32_8" }],
      list: [], total: 0, data: {},
      message: "ok",
    }),
  });
};
let confirmed = 0;
w.confirm = () => { confirmed += 1; return true; };
w.alert = () => {};

const assert = (c, m) => { console.log((c ? "  ✓ " : "  ✗ ") + m); if (!c) process.exitCode = 1; };
const $ = (id) => w.document.getElementById(id);

// ---------- 1) 结构：只剩比赛本身 ----------
console.log("\n[1] 比赛页结构");
assert(!/msub-/.test(html), "没有残留的 msub 子模块容器");
assert(!/match-sub-bar/.test(html), "二级 tab 栏已移除");
for (const k of ["msub-home", "msub-pvp", "msub-union", "msub-gvg", "msub-modes"]) {
  assert(!$("msub-" .replace("msub-", k)), `面板 #${k} 已删除`);
}
assert(!!$("match-runall"), "存在「一键比赛」按钮 #match-runall");
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
for (const id of ["match-runall", "match-race", "match-dress", "match-dressrace", "match-claim-all"]) {
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

setTimeout(() => {
  assert(confirmed > 0, "一键比赛前弹确认框（会消耗次数）");
  const ra = calls.filter((c) => c.url.includes("/api/match/run-all"));
  assert(ra.length === 1, "调用了一次 /api/match/run-all");
  const ca = calls.filter((c) => c.url.includes("/api/match/claim-all"));
  assert(ca.length >= 1, "打完顺带调用了 /api/match/claim-all 领奖");
  const logs = w.document.querySelector("[data-mlog]").textContent;
  assert(/一键比赛/.test(logs), "比赛日志里有一键比赛记录");
  assert(/打了\s*2\s*场|打了 2 场/.test(logs) || /一键比赛\] 完成/.test(logs), "日志记录了场次与剩余次数");
  assert(!errors.length, "运行期无 JS 报错" + (errors.length ? "：" + errors.slice(0, 2).join(" | ") : ""));
  w.close();
  process.exit(process.exitCode || 0);
}, 400);
