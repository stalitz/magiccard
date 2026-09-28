/**
 * 真机（headless Chrome + CDP）验收：竞技场并入猫窝日常后的布局与渲染。
 *
 * jsdom 查不出 CSS 层叠 / flex 塌陷，所以这里走真实布局：
 * 量二级 tab 条是否被压扁、量「竞技场 · 每日」块在猫窝日常里的实际 rect 与文本。
 *
 * 用法（复用本机 9222 上带登录态的 headless Chrome，只观测不动它）：
 *   node tools/arena_home_cdp.js
 * 默认走**服务端真实数据**（需要 :8000 已登录）。
 */
const http = require("http");
const fs = require("fs");

const CDP = process.env.CDP_PORT ? `http://127.0.0.1:${process.env.CDP_PORT}` : "http://127.0.0.1:9222";
const URL_PAGE = "http://127.0.0.1:8000/";
const OUT = "/tmp/mc_arena";

function get(path) {
  return new Promise((resolve, reject) => {
    http.get(CDP + path, (res) => {
      let b = "";
      res.on("data", (c) => (b += c));
      res.on("end", () => resolve(JSON.parse(b)));
    }).on("error", reject);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const list = await get("/json/list");
  const page = list.find((t) => t.type === "page");
  if (!page) throw new Error("没有找到 page target，Chrome 起好了吗？");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let nextId = 1;
  const pend = new Map();
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
  });
  await new Promise((r) => ws.addEventListener("open", r));
  const send = (method, params) => new Promise((resolve) => {
    const id = nextId++;
    pend.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  const evaluate = async (expr) => {
    const r = await send("Runtime.evaluate", {
      expression: expr, awaitPromise: true, returnByValue: true,
    });
    if (r.result && r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails));
    return r.result && r.result.result ? r.result.result.value : null;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: URL_PAGE });
  await sleep(1200);
  // headless 会缓存旧 index.html/app.js，必须忽略缓存重载才看得到这次改动
  await send("Page.reload", { ignoreCache: true });
  await sleep(3000);

  const m = await evaluate(`(async () => {
    switchPane('match');
    switchMatchSub('home');
    await new Promise(r => setTimeout(r, 300));
    await loadMatchPvp();
    await loadMatchPvpExtra();
    await new Promise(r => setTimeout(r, 300));
    const bar = document.getElementById('match-sub-bar');
    const barR = bar.getBoundingClientRect();
    const arena = document.getElementById('match-arena');
    const aR = arena.getBoundingClientRect();
    const box = document.getElementById('match-arena-box');
    const btns = [...arena.querySelectorAll('[data-act]')].map(b => b.textContent.trim());
    return {
      tabs: [...bar.querySelectorAll('.tab')].map(b => b.textContent.trim()),
      barW: Math.round(barR.width), barH: Math.round(barR.height),
      barScrollW: bar.scrollWidth, barClientW: bar.clientWidth,
      arenaW: Math.round(aR.width), arenaH: Math.round(aR.height),
      arenaVisible: aR.width > 200 && aR.height > 60 && aR.top >= 0,
      btns,
      cnt: (document.getElementById('match-arena-cnt') || {}).textContent || '',
      boxText: box ? box.textContent.replace(/\\s+/g, ' ').slice(0, 260) : '(no box)',
      oldPvpTab: !!document.getElementById('msub-pvp'),
      oldBox: !!document.getElementById('match-pvp-box'),
    };
  })()`);
  console.log(JSON.stringify(m, null, 1));

  const shot = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(`${OUT}/home_arena.png`, Buffer.from(shot.result.data, "base64"));
  console.log("截图已存到 " + OUT + "/home_arena.png");
  ws.close();
  process.exit(0);
})().catch((e) => { console.error("ERR", e); process.exit(1); });
