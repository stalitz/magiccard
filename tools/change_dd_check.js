// 变卡表单（星级→套卡→目标卡→材料卡 四级下拉）真实 DOM 离线回归
// 运行：NODE_PATH=<workspace>/node_modules <managed node> tools/change_dd_check.js
const fs = require("fs");
const { JSDOM } = require("jsdom");
const ROOT = "/Users/stalitz/www/魔法卡片";
const html = fs.readFileSync(ROOT + "/frontend/index.html", "utf8");
const appjs = fs.readFileSync(ROOT + "/frontend/app.js", "utf8");

const s = appjs.indexOf("const VALID_PROP_FACES");
const e = appjs.indexOf("// ---------- 合成路径弹窗");
if (s < 0 || e < 0 || e <= s) { console.error("!! 抽取变卡代码块失败"); process.exit(1); }
const code = appjs.slice(s, e);

const dom = new JSDOM(html, { runScripts: "outside-only" });
const document = dom.window.document;
const $ = (id) => document.getElementById(id);
const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const logs = [];
const log = (m, t) => logs.push([t, m]);
const cbState = {
  change: { cards: [{ id: 2, name: "一个相当长的卡片名字用来验证宽度", price: 9, theme: "时尚都市", slot: 1 }] },
  store: { cards: [] },
};
const busyBtn = (btn, busy) => { if (btn) { btn.disabled = true; if (busy) btn.textContent = busy; } return () => { if (btn) btn.disabled = false; }; };

const THEMES = [
  { tid: 101, name: "时尚都市", star: 3, have: 2, n: 9 },
  { tid: 102, name: "一个名字特别特别长的套卡用于验证宽度不被截断", star: 3, have: 0, n: 9 },
  { tid: 103, name: "小型套卡", star: 1, have: 1, n: 4 },
];
const CARDS = {
  101: [{ id: 1, name: "短名", price: 1 }, { id: 2, name: "一个相当长的卡片名字用来验证宽度", price: 9 }],
  102: [{ id: 5, name: "长卡", price: 3 }],
  103: [{ id: 6, name: "小卡", price: 1 }],
};
const resp = (o) => ({ json: async () => o, ok: true });
const calls = [];
async function fetchStub(url) {
  calls.push(url);
  if (url.startsWith("/api/cards/themes")) return resp({ themes: THEMES });
  if (url.startsWith("/api/props")) return resp({ props: [{ id: 9001, name: "100面值卡", count: 5 }] });
  const m = /^\/api\/cards\/theme\/(\d+)/.exec(url);
  if (m) return resp({ cards: CARDS[m[1]] || [] });
  return resp({ ok: false });
}

const R = [];
function ok(name, cond, extra) { R.push([cond ? "PASS" : "FAIL", name, extra || ""]); }

(async () => {
  const factory = new Function(
    "document", "fetch", "$", "esc", "log", "cbState", "busyBtn",
    "return (async () => { " + code + "\n; return { initChangeForm }; })();"
  );
  const { initChangeForm } = await factory(document, fetchStub, $, esc, log, cbState, busyBtn);
  await initChangeForm();

  const starBtn = $("cb-dd-star-btn"), starList = $("cb-dd-star-list");
  const themeBtn = $("cb-dd-theme-btn"), themeList = $("cb-dd-theme-list");
  const tgtBtn = $("cb-dd-target-btn"), tgtList = $("cb-dd-target-list");
  const matBtn = $("cb-dd-mat-btn"), matList = $("cb-dd-mat-list");
  const goBtn = $("cb-change-go2"), hint = $("cb-change-hint");

  ok("① 星级下拉按钮存在", !!starBtn);
  ok("① 初始：套卡按钮 disabled（未选星级）", themeBtn.disabled === true);
  ok("① 初始：星级按钮文案", starBtn.textContent === "选择星级 ▾", starBtn.textContent);

  // 打开星级下拉
  starBtn.onclick({ stopPropagation() {} });
  ok("① 点星级 → 浮层展开", !starList.classList.contains("hidden"));
  const starEls = Array.from(starList.querySelectorAll("[data-star]"));
  ok("① 星级档位=2 档（★1/★3，star=0 被过滤）", starEls.length === 2, "实际 " + starEls.length);
  ok("① 星级档位显示套数", (starList.textContent.includes("2 套") && starList.textContent.includes("1 套")), starList.textContent.replace(/\s+/g, " ").trim());

  // 选 ★3
  starEls[1].onclick();
  ok("② 选★3 → 套卡按钮解禁", themeBtn.disabled === false);
  ok("② 星级按钮文案更新", starBtn.textContent === "星级：★★★ ▾", starBtn.textContent);
  ok("② 套卡列表只列★3（不含小型套卡）", themeList.textContent.includes("时尚都市") && !themeList.textContent.includes("小型套卡"));
  ok("② 套卡长名未被省略号处理（DOM 里是完整文本）", themeList.innerHTML.includes("一个名字特别特别长的套卡用于验证宽度不被截断"));
  ok("② 星级点击后浮层自动收起", starList.classList.contains("hidden"));

  // 选套卡 101
  const thEls = Array.from(themeList.querySelectorAll("[data-ttid]"));
  ok("③ 套卡项=2", thEls.length === 2, "实际 " + thEls.length);
  thEls.find((el) => el.dataset.ttid === "101").onclick();
  await new Promise((r) => setTimeout(r, 10));
  ok("③ 选套卡 → 目标卡按钮解禁", tgtBtn.disabled === false);
  ok("③ 目标卡列表已渲染（含卡名 + 有标记）", tgtList.textContent.includes("一个相当长的卡片名字用来验证宽度"));
  ok("③ 已有卡显示「有」标记", tgtList.textContent.includes("有"));

  // 选目标卡 id=2（price 9）
  const cEls = Array.from(tgtList.querySelectorAll("[data-cid]"));
  cEls.find((el) => el.dataset.cid === "2").onclick();
  await new Promise((r) => setTimeout(r, 10));
  ok("④ 选目标卡 → 材料卡按钮解禁", matBtn.disabled === false);
  ok("④ 材料列表含道具卡段（面值≥9）", /道具卡（面值 ≥ 9）/.test(matList.textContent), matList.textContent.replace(/\s+/g, " ").trim().slice(0, 80));
  const mEls = Array.from(matList.querySelectorAll("[data-mkey]"));
  ok("④ 材料项 ≥1（道具100面值卡）", mEls.length >= 1, "实际 " + mEls.length);

  // 选材料
  mEls.find((el) => el.dataset.mkey === "prop:9001").onclick();
  ok("⑤ 选材料 → 变卡按钮解禁", goBtn.disabled === false);
  ok("⑤ 提示文案给出映射", hint.textContent.includes("变为"), hint.textContent);
  ok("⑤ 材料按钮文案", matBtn.textContent.startsWith("材料：100面值卡"), matBtn.textContent);

  // 重新选星级应清空下游
  starBtn.onclick({ stopPropagation() {} });
  Array.from(starList.querySelectorAll("[data-star]"))[0].onclick();
  ok("⑥ 换星级 → 下游清空（套卡/目标/材料按钮文案复位）",
    themeBtn.textContent === "选择套卡 ▾" && tgtBtn.textContent === "选择目标卡 ▾" && matBtn.textContent === "选择材料卡 ▾");
  ok("⑥ 换星级 → 目标/材料按钮重新禁用", tgtBtn.disabled === true && matBtn.disabled === true);
  ok("⑥ 换星级 → 套卡列表切到★1", themeList.textContent.includes("小型套卡") && !themeList.textContent.includes("时尚都市"));

  // CSS 宽度断言（读真实 index.html 文本）
  ok("⑦ CSS：浮层宽度按最长名字(max-content)", /\.cb-dd-list\{[^}]*width:max-content/.test(html));
  ok("⑦ CSS：卡名无 text-overflow:ellipsis", !/\.cb-dd-list \.pk \.pk-n\{[^}]*ellipsis/.test(html));
  ok("⑦ CSS：卡名保留 nowrap 单行", /\.cb-dd-list \.pk \.pk-n\{[^}]*white-space:nowrap/.test(html));

  const fail = R.filter((x) => x[0] === "FAIL");
  for (const [st, name, extra] of R) console.log(`${st === "PASS" ? "✓" : "✗"} ${name}${extra ? "  → " + extra : ""}`);
  console.log(`\n合计 ${R.length} 项，FAIL ${fail.length} 项`);
  process.exit(fail.length ? 1 : 0);
})().catch((err) => { console.error("!! 脚本异常", err); process.exit(2); });
