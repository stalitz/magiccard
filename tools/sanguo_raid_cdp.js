/**
 * 真机（headless Chrome + CDP）验收：三国「一键出战」按钮 + 今日出战次数。
 *
 * 只观测、不点按钮（今日次数已打满时点了也只会 skipped）。
 * 用法：node tools/sanguo_raid_cdp.js      数据走服务端真实接口（:8000 需已登录）
 */
const http = require("http");
const fs = require("fs");

const PORT = process.env.CDP_PORT || 9222;
const CDP = `http://127.0.0.1:${PORT}`;
const BASE = "http://127.0.0.1:8000/";
const OUT = process.env.OUT || "/tmp/mc_sg";

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
const chk = (name, cond, extra) =>
  console.log((cond ? "  ✓ " : "  ✗ ") + name + (extra ? "  " + extra : ""));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const list = await get("/json/list");
  let page = list.find((t) => t.type === "page" && /127\.0\.0\.1:8000/.test(t.url));
  let mine = null;                       // 自己开的测试标签，跑完要关掉
  if (!page) {
    // 本机没开着 8000 页面时，开一个 1440×900 的测试标签（PUT 才能带参数）
    const made = await new Promise((resolve) => {
      const req = http.request({
        host: "127.0.0.1", port: PORT, method: "PUT",
        path: `/json/new?width=1440&height=900&url=${encodeURIComponent(BASE)}`,
      }, (res) => { let b = ""; res.on("data", (c) => (b += c)); res.on("end", () => resolve(JSON.parse(b || "{}"))); });
      req.on("error", resolve);
      req.end();
    });
    if (!made || !made.webSocketDebuggerUrl) {
      console.log("开不了测试标签：", JSON.stringify(made)); process.exit(1);
    }
    page = made;
    mine = made.id;
    console.log("（临时开了一个 1440×900 测试标签 " + mine + "）");
  }

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
  await send("Page.reload", { ignoreCache: true });   // 忽略缓存才看得到新 app.js
  await sleep(3500);

  // 切到「三国战役」子模块，等接口回来（rect 量测要求面板真的 display 出来）
  await evaluate(`switchPane("match"); true`);
  await sleep(600);
  await evaluate(`switchMatchSub("sanguo"); true`);
  await sleep(2500);
  const shown = await evaluate(`(() => {
    const p = document.getElementById("msub-sanguo");
    const r = p.getBoundingClientRect();
    const on = [...document.querySelectorAll(".msub.on")].map((x) => x.id);
    return { on: on, w: Math.round(r.width), h: Math.round(r.height),
      sub: window.__matchSub || null };
  })()`);
  console.log("面板状态：" + JSON.stringify(shown));
  chk("三国子模块真的显示出来了", shown.on.join(",") === "msub-sanguo" && shown.w > 100,
    JSON.stringify(shown));

  const info = await evaluate(`(() => {
    const btn = document.querySelector("[data-act='mm-raid-sg']");
    const r = btn ? btn.getBoundingClientRect() : null;
    return {
      btnText: btn ? btn.textContent : null,
      btnRect: r ? { w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.y) } : null,
      box: document.getElementById("match-sanguo-box") ? document
        .getElementById("match-sanguo-box").textContent : null,
      toolbar: document.querySelector("#msub-sanguo .lib-toolbar") ? document
        .querySelector("#msub-sanguo .lib-toolbar").textContent : null,
    };
  })()`);

  // 是否存在重复按钮 / 按钮是不是真的在三国 tab 内
  const dup = await evaluate(
    `[...document.querySelectorAll("[data-act='mm-raid-sg']")].length`);
  const inside = await evaluate(
    `!!document.querySelector("[data-act='mm-raid-sg']").closest("#msub-sanguo")`);

  console.log("按钮文案：" + info.btnText);
  console.log("工具栏：" + info.toolbar);
  console.log("状态块：" + (info.box || "").slice(0, 160));
  chk("一键出战按钮存在且有可见尺寸", !!info.btnRect && info.btnRect.w > 40 && info.btnRect.h > 14,
    JSON.stringify(info.btnRect));
  chk("按钮只在三国子模块内一份", dup === 1 && inside, "count=" + dup + " inside=" + inside);
  chk("按钮文案反映真实剩余次数",
    /一键出战（今日(剩 \d 次|已出满)）/.test(info.btnText || ""), info.btnText);
  chk("状态块有「今日已出战」与「今日可再出战」",
    /今日已出战\s*\d+ \/ 3 次/.test(info.box || "") &&
    /今日可再出战\s*\d+ 次/.test(info.box || ""));

  // 截图：先滚到块可见，clip 必须落在视口内
  const shot = await evaluate(`(() => {
    document.querySelector("#msub-sanguo").scrollIntoView({ block: "start" });
    const r = document.getElementById("msub-sanguo").getBoundingClientRect();
    return { x: Math.max(0, Math.round(r.x) - 4), y: Math.max(0, Math.round(r.y) - 8),
      w: Math.round(r.width) + 8, h: Math.min(1400, Math.round(r.height) + 16) };
  })()`);
  await sleep(500);
  const clip = { x: shot.x, y: shot.y, width: Math.min(shot.w, 1400),
    height: Math.min(shot.h, 800), scale: 1 };
  const png = await send("Page.captureScreenshot", { format: "png", clip });
  if (!png.result || !png.result.data) {
    console.log("截图失败：" + JSON.stringify(png).slice(0, 200));
  } else {
    const f = OUT + "/sanguo.png";
    fs.writeFileSync(f, Buffer.from(png.result.data, "base64"));
    console.log("截图：" + f + " clip=" + JSON.stringify(clip));
  }

  ws.close();
  if (mine) {
    await new Promise((resolve) => {
      http.get(`http://127.0.0.1:${PORT}/json/close/${mine}`, (res) => {
        res.on("data", () => {}); res.on("end", resolve);
      }).on("error", resolve);
    });
    console.log("（已关闭测试标签 " + mine + "）");
  }
  process.exit(0);
})();
