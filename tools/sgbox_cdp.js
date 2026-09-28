/* CDP 真机验收：三国「关卡卡箱」面板（只读，不动卡）。
   本机没开 8000 页面时临时开一个 1440x900 测试标签，跑完 /json/close 只关自己开的。 */
const fs = require("fs");
const http = require("http");
const PORT = 9222;
const BASE = "http://127.0.0.1:8000/";
const OUT = "/tmp/mc_sg";

const get = (p, raw) => new Promise((res, rej) => {
  http.get(`http://127.0.0.1:${PORT}${p}`, (r) => {
    let b = "";
    r.on("data", (c) => (b += c));
    r.on("end", () => (raw ? res(b) : (() => { try { res(JSON.parse(b)); } catch (e) { res(null); } })()));
  }).on("error", rej);
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  let list = await get("/json/list");
  let page = list.find((t) => t.type === "page" && /127\.0\.0\.1:8000/.test(t.url));
  let opened = null;
  if (!page) {
    const made = await new Promise((res, rej) => {
      const m = http.request({
        host: "127.0.0.1", port: PORT, method: "PUT",
        path: `/json/new?width=1440&height=900&url=${encodeURIComponent(BASE)}`,
      }, (r) => { let b = ""; r.on("data", (c) => (b += c)); r.on("end", () => res(b)); });
      m.on("error", rej); m.end();
    });
    await sleep(1200);
    list = await get("/json/list");
    page = list.filter((t) => t.type === "page" && /127\.0\.0\.1:8000/.test(t.url)).pop();
    opened = page;
    console.log("临时打开测试标签：", made.trim().slice(0, 60));
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
    if (r.result && r.result.exceptionDetails) {
      throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
    }
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  await send("Page.enable");
  await send("Page.navigate", { url: BASE });
  await sleep(3500);
  // 不先 switchPane('match') 整块在折叠侧栏里，量出来 width=0（截图会报 0 width）
  await evaluate(`switchPane('match'); switchMatchSub('sanguo'); true`);
  await sleep(3000);

  const info = await evaluate(`(() => {
    const b = document.getElementById("match-sgbox-box");
    const btns = [...document.querySelectorAll('[data-act="mm-sgsell-all"]')];
    return {
      cnt: (document.getElementById("match-sgbox-cnt") || {}).textContent || "",
      len: b ? b.textContent.length : -1,
      txt: b ? b.textContent.replace(/\\s+/g, " ").slice(0, 600) : "",
      nBtn: btns.length,
      btns: btns.map((x) => ({ box: x.dataset.box, t: x.textContent.trim() })),
      layer4: (b || "").textContent.indexOf("第 4 层") >= 0,
      safeBlock: (b || "").textContent.indexOf("保险箱") >= 0
    };
  })()`);
  console.log("面板计数：" + info.cnt);
  console.log("面板文本：" + info.txt);
  console.log("卖卡按钮 " + info.nBtn + " 个：" + JSON.stringify(info.btns, null, 0));
  console.log("面板含第 4 层行：" + info.layer4 + "　含保险箱段：" + info.safeBlock);

  const shot = await evaluate(`(() => {
    document.querySelector("#match-sgbox-box").scrollIntoView({ block: "center" });
    const r = document.querySelector("#msub-sanguo").getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  })()`);
  await sleep(400);
  const png = await send("Page.captureScreenshot", {
    format: "png",
    clip: { x: Math.max(0, shot.x), y: Math.max(0, shot.y), width: shot.w,
      height: Math.min(Math.max(shot.h, 200), 1000), scale: 1 },
  });
  console.log("截图返回类型：" + typeof png + " keys=" +
    (png && typeof png === "object" ? Object.keys(png).join(",") : String(png).slice(0, 120)));
  const f = OUT + "/sgbox.png";
  if (!png.result || !png.result.data) {
    console.log("截图失败，原始返回：" + JSON.stringify(png).slice(0, 300));
    process.exit(1);
  }
  fs.writeFileSync(f, Buffer.from(png.result.data, "base64"));
  console.log("截图：" + f);

  ws.close();
  if (opened) { await get("/json/close/" + opened.id, true); console.log("已关闭临时测试标签"); }
  process.exit(0);
})();
