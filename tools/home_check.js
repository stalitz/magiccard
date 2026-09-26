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

  // 走真实入口：切到「每日比赛」页 → switchPane 钩子里的 initHomeOnce + loadHomeState
  // （玩法分块属于每日比赛页，练卡总览页不该出现）
  try {
    await w.eval("(async()=>{ switchPane('match'); await loadHomeState(); })()");
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
  assert(chain.includes("pane-match"), "挂在「每日比赛」pane 内（不是练卡总览）：" + chain.join(" < "));
  assert(!chain.includes("pane-overview"), "总览页里没有这个块（练卡总览必须干净）");
  assert(cards.length === 6, `6 个玩法子块（实际 ${cards.length}）`);
  assert(["点赞", "扭蛋机", "猫咪玩耍", "竞技比赛", "金币福利", "衣柜锻造"].every((t) => titles.some((x) => x.includes(t))),
    "6 个状态格标题齐全：" + titles.join("/"));
  assert(spansTxt.every((t) => t && t !== "读取中…" && !t.includes("读取失败")),
    "5 个状态块都渲染了真实数据");
  assert(/每日比赛榜 .*已点 ✓|未点/.test(txt("home-like-st")), "点赞块显示两个榜的点赞状态：" + txt("home-like-st"));
  assert(/积分/.test(txt("home-gacha-st")), "扭蛋块显示积分：" + txt("home-gacha-st"));
  assert(/目标 80/.test(txt("home-cat-st")), "玩耍块显示心情与目标：" + txt("home-cat-st"));
  assert(/剩余挑战|战力/.test(txt("home-arena-st")), "竞技块显示剩余挑战与战力：" + txt("home-arena-st"));
  assert(/红宝石 已兑 7\/7/.test(txt("home-gold-st")), "金币福利块显示红宝石已兑次数与上限：" + txt("home-gold-st"));
  assert(/建议锻：手部 20072（要材料 1）/.test(txt("home-forge-st")), "衣柜块挑出未锻且最省材料的一件：" + txt("home-forge-st"));

  // 用户要的是「一键全部」，不是分开的手动按钮
  const manual = ["home-like", "home-gacha", "home-candy", "home-feed", "home-arena"];
  assert(!manual.some((id) => w.document.getElementById(id)), "分开的手动按钮已全部移除");
  assert(!!w.document.getElementById("home-all"), "一键全部按钮就位");
  assert(!!w.document.getElementById("home-all-st"), "一键全部的运行提示位就位");

  // 切页与按钮绑定：initHomeOnce 里的 onclick 必须真的挂上
  const bound = await w.eval("(async()=>{ initHomeOnce(); return typeof document.getElementById('home-all').onclick; })()");
  assert(bound === "function", "一键全部 onclick 已绑定：" + bound);

  // 实点一键全部（今天多数玩法已完成，应全部走「跳过」而不是报错）
  await w.eval("(async()=>{ document.getElementById('home-all').click(); await new Promise(r=>setTimeout(r,20000)); })()");
  console.log("  [一键全部后]", txt("home-arena-st"));
  // 2026-09-26：一键全部属于每日比赛页（猫窝日常子模块），日志改走比赛页右栏[data-mlog]
  const logTxt = [...w.document.querySelectorAll("[data-mlog]")].map((e) => e.textContent).join(" | ");
  console.log("  [一键全部日志·比赛页右栏]", logTxt.slice(-500));
  assert(/一键全部/.test(logTxt), "一键全部写出了执行日志（比赛页右栏）");
  assert(!/读取失败/.test(txt("home-arena-st")), "一键全部后状态块仍然有效：" + txt("home-arena-st"));
  assert(errors.length === 0, "无运行时错误：" + (errors.slice(0, 2).join(" ; ") || "无"));
  process.exit(process.exitCode || 0);
})();
