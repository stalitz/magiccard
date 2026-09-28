/**
 * 真机（CDP）验收：出战（比赛）页改版后的列布局。
 * 期望：左列 = 我的比赛状态（上）+ 今日主题与最优穿装（下）；
 *       右列 = 出战记录（最近 10 场），与左列并排。
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
  let page = list.find((t) => t.type === "page" && /127\.0\.0\.1:8000/.test(t.url));
  if (!page) { console.log("没找到 8000 的页面 target"); process.exit(1); }
  // 本机 Chrome 的 Emulation.setDeviceMetricsOverride 无返回，量宽屏只能新开一个指定尺寸的 target
  if (process.env.WIDE) {
    const w = Number(process.env.WIDE.split("x")[0]) || 1600;
    const h = Number(process.env.WIDE.split("x")[1]) || 1100;
    const made = await new Promise((resolve) => {
      const req = require("http").request(
        { host: "127.0.0.1", port: PORT, path: `/json/new?width=${w}&height=${h}&url=${encodeURIComponent(BASE)}`,
          method: "PUT" },
        (res) => { let b = ""; res.on("data", (c) => (b += c)); res.on("end", () => { try { resolve(JSON.parse(b)); } catch (e) { resolve(null); } }); }
      );
      req.on("error", () => resolve(null));
      req.end();
    });
    if (made && made.webSocketDebuggerUrl) page = made;
    console.log("新开宽屏 target: " + w + "x" + h);
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
    if (r.result && r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: BASE });
  await sleep(1500);
  await send("Page.reload", { ignoreCache: true });
  await sleep(3000);

  const r = await evaluate(`(async () => {
    switchPane('match');
    switchMatchSub('race');
    await new Promise(r => setTimeout(r, 2500));
    const boxOf = (id) => { const e = document.getElementById(id); return e ? e.closest('.lst') : null; };
    const rect = (id) => { const e = boxOf(id); if (!e) return null;
      const q = e.getBoundingClientRect();
      return { t: e.querySelector('.t') ? e.querySelector('.t').textContent.trim() : '',
               x: Math.round(q.x), y: Math.round(q.y), w: Math.round(q.width), h: Math.round(q.height) }; };
    const cols = document.querySelector('#msub-race .lib-body.dg-cols');
    return {
      state: rect('match-state-box'),
      theme: rect('match-theme-box'),
      log: rect('match-log-box'),
      colsW: cols ? Math.round(cols.getBoundingClientRect().width) : null,
      colsCls: cols ? cols.className : null
    };
  })()`);

  console.log("比赛状态块 " + JSON.stringify(r.state));
  console.log("今日主题块 " + JSON.stringify(r.theme));
  console.log("出战记录块 " + JSON.stringify(r.log));
  console.log("容器 " + r.colsW + " / " + r.colsCls);
  let ok = true;
  const chk = (c, s) => { console.log((c ? "OK " : "BAD ") + s); if (!c) ok = false; };
  chk(!!r.state && !!r.theme && !!r.log, "三个块都取到了");
  chk(r.state && r.theme && r.state.y < r.theme.y && Math.abs(r.state.x - r.theme.x) <= 1, "状态在上、主题在下，同一列");
  chk(r.state && r.theme && r.theme.y - (r.state.y + r.state.h) < 60, "两块紧贴叠放（间隔 < 60px）");
  chk(r.state && r.log && r.log.x > r.state.x + r.state.w - 8, "出战记录在右列（不与左列重叠）");
  chk(r.state && r.log && Math.abs(r.log.y - r.state.y) <= 4, "右列与左列顶部对齐");

  await evaluate(`document.querySelector('#msub-race .lib-body').scrollIntoView({ block: 'center' })`);
  await sleep(400);
  const clip = await evaluate(`(() => {
    const b = document.querySelector('#msub-race .lib-body').getBoundingClientRect();
    return { x: Math.max(0, b.x - 6), y: Math.max(0, b.y - 6),
             width: Math.min(b.width + 12, 1200), height: Math.min(b.height + 12, 1200), scale: 2 };
  })()`);
  const shot = await send("Page.captureScreenshot", { format: "png", clip });
  fs.mkdirSync("/tmp/mc_race", { recursive: true });
  fs.writeFileSync("/tmp/mc_race/race.png", Buffer.from(shot.result.data, "base64"));
  console.log("截图已存 /tmp/mc_race/race.png");

  ws.close();
  process.exit(ok ? 0 : 1);
})();
