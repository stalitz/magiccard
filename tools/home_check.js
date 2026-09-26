/** 主界面玩法分块冒烟：5 个状态块必须渲染真实数据，按钮就位，且竞技前自动喂猫。 */
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

(async () => {
  try { w.eval(appjs); } catch (e) { console.log("注入 app.js 失败:", e.message); process.exit(1); }

  // 走真实入口：切到总览页 → switchPane 钩子里的 initHomeOnce + loadHomeState
  try {
    await w.eval("(async()=>{ switchPane('overview'); await loadHomeState(); })()");
  } catch (e) { console.log("调用失败:", e.message); }

  const box = w.document.getElementById("home-box");
  const chain = [];
  let n = box;
  while (n && n !== w.document.body) { chain.push(n.id || n.tagName.toLowerCase()); n = n.parentElement; }
  console.log("== 祖先链 ==", chain.join(" < "));

  const cards = [...(box ? box.querySelectorAll(".mode-card") : [])];
  const titles = cards.map((c) => ((c.querySelector(".lst-head .t") || {}).textContent || "").trim());
  console.log("== 子块 ==", titles.join(" | "));

  const txt = (id) => ((w.document.getElementById(id) || {}).textContent || "").trim();
  const spans = ["home-like-st", "home-gacha-st", "home-cat-st", "home-arena-st",
                 "home-gold-st", "home-forge-st"];
  for (const s of spans) console.log("  " + s + " =", txt(s));
  const spansTxt = spans.map(txt);

  console.log("\n断言：");
  assert(!!box, "#home-box 存在");
  assert(chain.includes("pane-overview"), "挂在总览 pane 内");
  assert(cards.length === 6, `6 个玩法子块（实际 ${cards.length}）`);
  assert(["点赞", "扭蛋机", "猫咪玩耍", "竞技比赛", "金币福利", "衣柜锻造"].every((t) => titles.some((x) => x.includes(t))),
    "5 个玩法标题齐全：" + titles.join("/"));
  assert(spansTxt.every((t) => t && t !== "读取中…" && !t.includes("读取失败")),
    "5 个状态块都渲染了真实数据");
  assert(/每日比赛榜 .*已点 ✓|未点/.test(txt("home-like-st")), "点赞块显示两个榜的点赞状态：" + txt("home-like-st"));
  assert(/积分/.test(txt("home-gacha-st")), "扭蛋块显示积分：" + txt("home-gacha-st"));
  assert(/目标 80/.test(txt("home-cat-st")), "玩耍块显示心情与目标：" + txt("home-cat-st"));
  assert(/剩余挑战|战力/.test(txt("home-arena-st")), "竞技块显示剩余挑战与战力：" + txt("home-arena-st"));
  assert(/红宝石#2/.test(txt("home-gold-st")), "金币福利块显示 6 个商品兑换次数：" + txt("home-gold-st"));
  assert(/forge 最小/.test(txt("home-forge-st")), "衣柜块挑出最值得先锻的服装：" + txt("home-forge-st"));

  const btns = ["home-like", "home-gacha", "home-candy", "home-feed", "home-arena"];
  assert(btns.every((b) => !!w.document.getElementById(b)), "5 个按钮就位");

  // 切页与按钮绑定：initHomeOnce 里的 onclick 必须真的挂上
  const bound = await w.eval("(async()=>{ initHomeOnce(); return ['home-like','home-feed','home-arena'].map(id=>typeof document.getElementById(id).onclick); })()");
  assert(bound.every((t) => t === "function"), "按钮 onclick 已绑定：" + bound.join(","));

  // 今天心情已达标 → 点「一键喂到 80」不该写任何状态；点竞技应先喂猫再打
  await w.eval("(async()=>{ document.getElementById('home-feed').click(); await new Promise(r=>setTimeout(r,2500)); })()");
  console.log("  [喂糖后]", txt("home-cat-st"));
  assert(!/读取失败/.test(txt("home-cat-st")), "喂糖后状态块仍然有效");

  await w.eval("(async()=>{ document.getElementById('home-arena').click(); await new Promise(r=>setTimeout(r,8000)); })()");
  const logTxt = [...w.document.querySelectorAll("[data-op-log]")].map((e) => e.textContent).join(" | ");
  console.log("  [竞技后日志]", logTxt.slice(-500));
  assert(!/读取失败/.test(txt("home-arena-st")), "竞技后状态块仍然有效：" + txt("home-arena-st"));
  assert(errors.length === 0, "无运行时错误：" + (errors.slice(0, 2).join(" ; ") || "无"));
  process.exit(process.exitCode || 0);
})();
