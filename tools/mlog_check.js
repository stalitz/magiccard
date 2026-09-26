/**
 * 每日比赛页「右栏专属日志」冒烟（2026-09-26）
 * 验收：日志栏常驻在比赛页（不随子模块切换消失/错位）、只记比赛页各玩法的日志、
 * 别的页的 log() 不会混进来、清空按钮生效、失败也落一条。
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

w.fetch = (u) => {
  const url = String(u).startsWith("http") ? String(u) : BASE + String(u);
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
const box = () => w.document.getElementById("match-mlog-box");
const lines = () => [...(box() ? box().querySelectorAll(".ln") : [])];
const globalLogLines = () => {
  const g = w.document.getElementById("log");
  return g ? [...g.querySelectorAll("div")].filter((d) => d.className.indexOf("t ") >= 0
    || (d.firstElementChild && d.firstElementChild.className === "t")) : [];
};
const parentIds = (el) => {
  const out = [];
  let n = el && el.parentElement;
  while (n && n !== w.document.body) { out.push(n.id || n.className); n = n.parentElement; }
  return out;
};

(async () => {
  try { w.eval(appjs); } catch (e) { console.log("注入 app.js 失败:", e.message); process.exit(1); }
  // 先走一遍 switchPane 触发 initMatchOnce（清空按钮的绑定在那儿）
  await w.eval("(async()=>{ try{ localStorage.removeItem('mc_match_log'); localStorage.removeItem('mc_op_log'); }catch(e){} switchPane('match'); })()");

  console.log("== 位置：日志栏常驻比赛页 ==");
  assert(!!box(), "#match-mlog-box 存在");
  assert(!!w.document.querySelector("#match-mlog-box[data-mlog]"), "有 data-mlog 标记（渲染器认它）");
  assert(parentIds(box()).indexOf("pane-match") >= 0, "挂在 #pane-match 下（不在任何子模块里）");
  const ms = ["race", "home", "pvp", "union", "gvg", "modes"].filter((k) => {
    let n = box();
    while (n && n !== w.document.body) { if (n.id === "msub-" + k) return true; n = n.parentElement; }
    return false;
  });
  assert(ms.length === 0, "不被任何子模块包裹（切 tab 不会把它带走）：命中 " + (ms.join(",") || "无"));
  assert(!!w.document.getElementById("mlog-clear"), "清空按钮存在");

  console.log("== 比赛页各玩法的日志落到右栏 ==");
  try { w.eval("mlog('[竞技场] 挑战 AAA → 胜利', 'ok')"); } catch (e) { assert(false, "mlog 抛错：" + e.message); }
  assert(lines().length === 1, "写入 1 条（当前 " + lines().length + "）");
  assert(/^\[\d/.test(lines()[0] ? lines()[0].textContent : ""), "行内含时间戳：" + (lines()[0] || {}).textContent);
  assert(lines()[0] && lines()[0].className.indexOf("ok") >= 0, "成功类 ok");
  try { w.eval("mlog('[工会] 出兵 → system busy', 'warn')"); } catch (e) {}
  assert(lines().length === 2 && lines()[1].className.indexOf("warn") >= 0, "第二条 warn 也进来了");
  try { w.eval("mlogFail('PVP 日榜', { ok:false, message:'网络错误' })"); } catch (e) {}
  assert(lines().length === 3 && lines()[2].className.indexOf("err") >= 0, "读接口失败落一条 err");
  assert((w.document.getElementById("mlog-cnt") || {}).textContent === "3 条", "计数显示 3 条");

  console.log("== 独立性：别的页的全局日志不混进右栏 ==");
  const before = lines().length;
  try { w.eval("log('[挖地] 投石成功', 'ok')"); } catch (e) { assert(false, "log 抛错：" + e.message); }
  assert(lines().length === before, "总览页全局 log() 不会写进比赛日志栏");
  const raw = () => JSON.parse(w.localStorage.getItem("mc_match_log") || "[]");
  assert(raw().length === 3 && raw().every((e) => /^\[(竞技场|工会|PVP)/.test(e.msg)),
    "比赛日志只存自己的 key（mc_match_log / 3 条：竞技场·工会·PVP）");
  assert((JSON.parse(w.localStorage.getItem("mc_op_log") || "[]") || []).some((e) => String(e.msg).indexOf("[挖地]") >= 0),
    "全局日志（mc_op_log）里仍然有挖地那条");

  console.log("== 切子模块后日志栏不受影响 ==");
  try {
    await w.eval("(async()=>{ switchMatchSub('modes'); await new Promise(r=>setTimeout(r,40)); "
      + " switchMatchSub('race'); await new Promise(r=>setTimeout(r,40)); })()");
  } catch (e) { assert(false, "切换抛错：" + e.message); }
  assert(lines().length === 3, "切两次子模块后仍在（" + lines().length + " 条）");

  console.log("== 清空按钮 ==");
  const cb = w.document.getElementById("mlog-clear");
  cb.click();
  assert(lines().length === 0, "点清空后右栏为空");
  assert(!!box().querySelector(".empty-tip"), "显示空态提示");
  assert(!w.localStorage.getItem("mc_match_log"), "本地存储也清掉了（清空按钮已绑定）");
  assert((w.document.getElementById("mlog-cnt") || {}).textContent === "0 条", "计数归零");

  console.log("== 进比赛页会把上次记录填回来 ==");
  try { w.eval("mlog('[每日比赛] 装备详情：已读取 6 组属性', 'ok')"); } catch (e) {}
  try { w.eval("mlogHistory()"); } catch (e) { assert(false, "mlogHistory 抛错：" + e.message); }
  assert(lines().length === 1, "mlogHistory 后 1 条");
  // 模拟重新进页面：清掉 DOM 内容再回填
  box().innerHTML = "";
  try { w.eval("mlogHistory()"); } catch (e) {}
  assert(lines().length === 1, "重新进入后记录仍在（localStorage 持久化）");

  console.log("== 渲染不报错 ==");
  assert(errors.length === 0, "无 jsdom 运行时错误" + (errors.length ? "：" + errors.slice(0, 3).join(" | ") : ""));
  console.log(process.exitCode ? "\n===== 有 FAIL =====" : "\n===== 全部 PASS =====");
  w.close();
  process.exit(process.exitCode || 0);
})();
