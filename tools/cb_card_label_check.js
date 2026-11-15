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
  const line = c0.querySelector(".cbn");
  const kids = line ? [...line.children].map((n) => n.className) : [];
  console.log("  第一张标签行子元素：", kids.join(" | "),
              "→", line ? line.textContent.trim() : "(缺)");
  assert(kids[0] === "cbt", "套名节点排在卡名前面（顺序 " + kids.join(",") + "）");
  assert(kids[1] === "cnm", "卡名节点紧随其后");
  assert((c0.querySelector(".cbt") || {}).textContent === "七夕情人节", "套名文本 = 七夕情人节");
  assert((c0.querySelector(".cnm") || {}).textContent === "斗巧", "卡名文本 = 斗巧");

  // 下方小字行不应再重复套名
  const meta = c0.querySelector(".cbm").textContent;
  assert(!/七夕情人节/.test(meta), "面值/星级行不再重复套名：" + meta.replace(/\s+/g, " ").trim());

  // 无主题的卡（百变卡）不应出现空的套名节点
  const c1 = box.querySelector('.cb-card[data-cid="19522"]');
  assert(!c1.querySelector(".cbt"), "无主题卡不渲染空套名节点");
  assert((c1.querySelector(".cnm") || {}).textContent === "百变卡", "无主题卡仍显示卡名");

  // 字号一致（jsdom 能算就算，算不出退回查 CSS 文本）
  const sz = (el) => (el ? w.getComputedStyle(el).fontSize : "");
  const a = sz(c0.querySelector(".cbt")), b = sz(c0.querySelector(".cnm"));
  if (a && b) {
    assert(a === b, `套名与卡名同字号（${a} vs ${b}）`);
    assert(parseFloat(a) >= 12.5, `标签字号不小于 12.5px（${a}）`);
  } else {
    const cssOk = /\.cbn\{[^}]*font-size:12\.5px/.test(html);
    assert(cssOk, "CSS 里标签行字号 12.5px（jsdom 未能计算，改查样式文本）");
  }

  assert(!errors.length, "无脚本异常：" + errors.slice(0, 3).join(" / "));
  process.exit(process.exitCode || 0);
})();
