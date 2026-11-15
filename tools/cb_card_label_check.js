/** 卡箱卡片标签顺序冒烟：套卡名必须在卡名前面、同字号（12.5px），
 *  且不再出现于下面那行的小字里。
 *  改动动因（2026-11-15 用户）：套名 11px 灰色混在「面值/星级」里，看不清、
 *  无法快速定位到套卡。
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
const dom = new JSDOM(html, { runScripts: "outside-only", url: BASE, virtualConsole: vc });
const w = dom.window;
w.alert = () => {};
w.confirm = () => true;

const assert = (c, m) => { console.log((c ? "  [PASS] " : "  [FAIL] ") + m); if (!c) process.exitCode = 1; };

const ST = {
  ok: true,
  now: 1794755000,
  changebox: {
    filled: 2, cur: 17, max: 18,
    cards: [
      { slot: 0, id: 23927, name: "斗巧", price: 40, theme: "七夕情人节", star: 1 },
      { slot: 1, id: 19522, name: "百变卡", price: 9999, theme: "", star: 5 },
    ],
  },
  storebox: { filled: 0, cur: 59, max: 200, cards: [] },
  simplebox: { filled: 0, cur: 15, max: 15, cards: [] },
};
w.fetch = async (u) => ({ json: async () => ST });

(async () => {
  try { w.eval(appjs); } catch (e) { console.log("注入 app.js 失败:", e.message); process.exit(1); }
  await w.eval("(async()=>{ await loadCardBox(); })()");

  const box = w.document.getElementById("cb-change");
  const cards = box.querySelectorAll(".cb-card");
  assert(cards.length === 2, "换卡箱渲染出 2 张卡（实际 " + cards.length + "）");

  const c0 = box.querySelector('.cb-card[data-cid="23927"]');
  assert(!!c0, "找到「斗巧」（cid 23927）那张卡");
  const r1 = c0.querySelector(".cbtrow"), r2 = c0.querySelector(".cbnrow");
  assert(!!r1 && !!r2, "渲染出两行：星级+套名 / 卡名+面值");
  const k1 = r1 ? [...r1.children].map((n) => n.className) : [];
  const k2 = r2 ? [...r2.children].map((n) => n.className) : [];
  console.log("  第一行：", k1.join(" | "), "→", r1 ? r1.textContent.trim() : "");
  console.log("  第二行：", k2.join(" | "), "→", r2 ? r2.textContent.trim() : "");
  assert(k1.join(",") === "cbstar,cbt", "第一行 = 星级(小灰) + 套卡名");
  assert((r1.querySelector(".cbt") || {}).textContent === "七夕情人节", "套名文本 = 七夕情人节");
  assert((r1.querySelector(".cbstar") || {}).textContent === "1★", "星级文本 = 1★");
  assert(k2.join(",") === "cnm,cbprice", "第二行 = 卡名 + 面值(小灰)");
  assert((r2.querySelector(".cnm") || {}).textContent === "斗巧", "卡名文本 = 斗巧");
  assert(/面值\s*40/.test((r2.querySelector(".cbprice") || {}).textContent || ""), "面值文本 = 面值 40");

  // 无主题的卡：第一行只剩星级（若有），不渲染空套名
  const c1 = box.querySelector('.cb-card[data-cid="19522"]');
  assert(!c1.querySelector(".cbt"), "无主题卡不渲染空套名节点");
  assert((c1.querySelector(".cnm") || {}).textContent === "百变卡", "无主题卡仍显示卡名");
  assert((c1.querySelector(".cbstar") || {}).textContent === "5★", "无主题卡仍显示星级");

  // 字号：套名/卡名 12.5px 大字，星级/面值 11px 小灰
  const sz = (el) => (el ? w.getComputedStyle(el).fontSize : "");
  const col = (el) => (el ? w.getComputedStyle(el).color : "");
  const big = sz(r1.querySelector(".cbt")), big2 = sz(r2.querySelector(".cnm"));
  const sm1 = sz(r1.querySelector(".cbstar")), sm2 = sz(r2.querySelector(".cbprice"));
  console.log("  字号：套名", big, "卡名", big2, "星级", sm1, "面值", sm2);
  if (big && sm1) {
    assert(parseFloat(big) === 12.5 && parseFloat(big2) === 12.5, "套名与卡名 12.5px");
    assert(parseFloat(sm1) === 11 && parseFloat(sm2) === 11, "星级与面值 11px 小字");
    assert(col(r1.querySelector(".cbstar")) === col(r2.querySelector(".cbprice")),
      "星级与面值同色（灰）");
  } else {
    assert(/\.cbt\{[^}]*font-size:12\.5px/.test(html) && /\.cbstar\{[^}]*font-size:11px/.test(html),
      "CSS 文本里有 12.5px 套名 / 11px 星级（jsdom 未能计算）");
  }

  assert(!errors.length, "无脚本异常：" + errors.slice(0, 3).join(" / "));
  process.exit(process.exitCode || 0);
})();
