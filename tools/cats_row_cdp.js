/**
 * 真机（headless Chrome + CDP）验收：出战猫咪横排 + 中文装扮名。
 *
 * jsdom 量不出真实布局（grid 是否真排成一行、卡片有没有被压扁），
 * 所以这里走真实渲染：量每张猫卡 rect、统计占了几行、检查文本里还有没有 # 编号。
 *
 * 用法（复用本机 9222 上带登录态的 headless Chrome，只观测不动它）：
 *   node tools/cats_row_cdp.js
 * 数据走服务端真实接口（需要 :8000 已登录）。
 */
const http = require("http");
const fs = require("fs");

const PORT = process.env.CDP_PORT || 9222;
const CDP = `http://127.0.0.1:${PORT}`;
const BASE = "http://127.0.0.1:8000/";

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
  const list = await get("/json/list");
  const page = list.find((t) => t.type === "page" && /127\.0\.0\.1:8000/.test(t.url));
  if (!page) { console.log("没找到 8000 的页面 target"); process.exit(1); }

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
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 500));
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: BASE });
  await sleep(1500);
  // headless 会缓存旧 index.html/app.js，必须忽略缓存重载才看得到本次改动
  await send("Page.reload", { ignoreCache: true });
  await sleep(3000);

  const r = await evaluate(`(async () => {
    // 不先 switchPane('match') 的话整块在折叠侧栏里，量出来全是 0
    switchPane('match');
    switchMatchSub('home');
    await new Promise(r => setTimeout(r, 2500));
    const box = document.getElementById('match-cats');
    if (!box) return { err: 'no #match-cats' };
    const cards = [].slice.call(box.querySelectorAll('.cat-card'));
    const rects = cards.map(function (c) {
      var q = c.getBoundingClientRect();
      return { x: Math.round(q.x), y: Math.round(q.y), w: Math.round(q.width), h: Math.round(q.height) };
    });
    const rowEl = box.querySelector('.cat-row');
    const row = rowEl ? rowEl.getBoundingClientRect() : null;
    return {
      n: cards.length,
      rects: rects,
      lines: new Set(rects.map(function (q) { return q.y; })).size,
      rowW: row ? Math.round(row.width) : null,
      boxW: Math.round(box.getBoundingClientRect().width),
      txt: box.textContent.replace(/\\s+/g, ' ').trim().slice(0, 500),
      hasHash: box.textContent.indexOf('#') >= 0
    };
  })()`);

  // 先滚到可见再量坐标（clip 是相对视口的，不滚就截到别处）
  await evaluate(`document.getElementById('match-cats').scrollIntoView({ block: 'center' })`);
  await new Promise((r) => setTimeout(r, 300));
  const clip = await evaluate(`(() => {
    const b = document.getElementById('match-cats').getBoundingClientRect();
    return { x: Math.max(0, b.x - 10), y: Math.max(0, b.y - 30),
             width: Math.min(b.width + 20, 900), height: b.height + 40, scale: 2 };
  })()`);
  const shot = await send("Page.captureScreenshot", { format: "png", clip });
  fs.mkdirSync("/tmp/mc_cats", { recursive: true });
  fs.writeFileSync("/tmp/mc_cats/cats.png", Buffer.from(shot.result.data, "base64"));
  console.log("截图已存 /tmp/mc_cats/cats.png");

  ws.close();
  if (!r || r.err) { console.log("取值失败：", JSON.stringify(r)); process.exit(1); }
  console.log("卡片数 " + r.n + "，占 " + r.lines + " 行");
  console.log("容器宽 " + r.boxW + " / 横排宽 " + r.rowW);
  console.log("卡片 rect：" + JSON.stringify(r.rects));
  console.log("文本：" + r.txt);
  if (r.n >= 2 && r.lines === 1) console.log("OK " + r.n + " 只猫排成一行（卡宽 " + r.rects[0].w + "px）");
  else console.log("BAD 不是单行：" + r.lines + " 行");
  console.log(r.hasHash ? "BAD 文本仍含 # 编号" : "OK 无 # 编号");
  process.exit(0);
})();
