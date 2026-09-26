/** 变卡目标「自动挑」的前端冒烟：面板必须显示自动目标，且不再出现手填目标框。
 *  2026-09-26 用户定调「不要指定某个卡，就变在炼那套里缺的 40 面卡」。 */
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
w.fetch = (u, opt) => globalThis.fetch(String(u).startsWith("http") ? String(u) : BASE + String(u), opt);
w.confirm = () => true;
w.alert = () => {};

const assert = (c, m) => { console.log((c ? "  [PASS] " : "  [FAIL] ") + m); if (!c) process.exitCode = 1; };
const txt = (id) => ((w.document.getElementById(id) || {}).textContent || "").trim();

(async () => {
  try { w.eval(appjs); } catch (e) { console.log("注入 app.js 失败:", e.message); process.exit(1); }

  console.log("== 启动行 ==");
  console.log("  目标显示区 =", txt("auto-gift-dst"));
  assert(txt("auto-gift-dst") === "自动（缺什么变什么）",
    '没启动时显示「自动（缺什么变什么）」：' + txt("auto-gift-dst"));
  const inp = w.document.getElementById("auto-gift-dst");
  assert(!inp || !inp.tagName || inp.tagName !== "INPUT",
    "手填目标卡输入框已移除（不再指定某个卡）");

  // 造一份引擎在跑的 status：目标 = 七夕情人节的 40 面卡「种生求子」
  const st = {
    running: true,
    box_gift: {
      enabled: true, dst: 23925, dst_name: "种生求子", dst_face: 40, dst_theme: "七夕情人节",
      target: "种生求子（七夕情人节 40面，还差 2）", useful: true, cooldown_remain: 0,
      last: { changed: 1, last: "#7773 → 23925 成功", message: "卡箱礼物卡 5 张：变成功 1 张；#7773 → 23925 成功" },
    },
    gift: {
      enabled: true, dst: 23926, dst_name: "求姻缘", dst_face: 40, dst_theme: "七夕情人节",
      target: "求姻缘（七夕情人节 40面，还差 2）", upper: 45, lower: 25, last: null,
    },
    // 「缺」按整套 40 面卡的总量算（不是一张都没有才算缺）
    face40: [{ tid: 1652, theme: "七夕情人节", need_total: 5, have: 1,
               gap: { 23925: 2, 23926: 2, 23927: 1 }, kinds: 3,
               ids: [23925, 23926, 23927] }],
    log: [],
  };
  try {
    await w.eval("(async()=>{ renderAuto(" + JSON.stringify(st) + ", 0); })()");
  } catch (e) { console.log("渲染异常:", e.message); process.exitCode = 1; }

  const body = ((w.document.getElementById("auto-body") || {}).textContent || "");
  console.log("\n== 自动炼卡面板 ==", body.replace(/\s+/g, " ").slice(0, 300));

  assert(/自动变卡目标/.test(body), "面板出现「自动变卡目标」格");
  assert(/种生求子/.test(body), "显示自动挑中的卡名（种生求子）");
  assert(/40/.test(body), "标注面额 40");
  assert(!/26117/.test(body), "不再展示写死的 26117（那属 theme 1810，本套用不上）");
  assert(/收礼变卡目标/.test(body) && /求姻缘/.test(body), "收礼变卡目标也跟着自动挑");
  assert(txt("auto-gift-dst") === "#23925 种生求子（40面）",
    "启动行同步显示会自动变哪张：" + txt("auto-gift-dst"));

  // 2026-09-26：把「缺」的口径显示出来 —— 整套要几张 40 面、已有几张、还差多少
  assert(/40 面缺口/.test(body), "面板出现「40 面缺口」格");
  assert(/七夕情人节 40面 1\/6（还差 5）/.test(body.replace(/\s+/g, " ")),
    "按整套总量显示缺口（已有 1 / 共需 6、还差 5）："
    + (body.match(/40 面缺口[^]{0,80}/) || [""])[0]);
  assert(/礼物卡只变 40 面/.test(body), "注明礼物卡只能变 40 面");

  // 引擎没在跑 / 当前不缺套内卡时的兜底文案
  const st2 = JSON.parse(JSON.stringify(st));
  st2.box_gift.dst = null; st2.box_gift.dst_name = ""; st2.box_gift.dst_face = 0;
  st2.box_gift.target = "";
  try {
    await w.eval("(async()=>{ renderAuto(" + JSON.stringify(st2) + ", 0); })()");
  } catch (e) { console.log("兜底渲染异常:", e.message); process.exitCode = 1; }
  const body2 = ((w.document.getElementById("auto-body") || {}).textContent || "");
  assert(/暂无（本轮不缺套内卡）/.test(body2), "没缺套内卡时明说不动手：" + body2.replace(/\s+/g, " ").slice(0, 120));

  assert(errors.length === 0, "无 JS 报错：" + errors.slice(0, 2).join(" | "));
  console.log("\n结果见上方 [PASS]/[FAIL]");
  process.exit(process.exitCode || 0);   // renderAuto 会开轮询定时器，必须显式退出
})();
