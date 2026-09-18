const $ = (id) => document.getElementById(id);

// 前端版本：与 index.html 里 <script src="/app.js?v=..."> 保持一致。
// 启动时打进操作日志，便于一眼确认浏览器有没有吃到新 JS（排查「改了没生效」）。
const APP_VER = "20260915h";

let pollTimer = null;
let stateTimer = null;
let loggedIn = false;
// 轮询状态追踪：本张二维码是否已进入「已扫描」、以及已扫描后连续收到 67 的次数
let sawScanned = false;
let expiredAfterScan = 0;
const POLL_NORMAL = 1200;   // 基础轮询间隔
const POLL_FAST = 500;      // 已扫描后加速，抓住短暂的确认态(0)
const POLL_SLOW = 3000;     // 出现非等待态后降速「继续」轮询（绝不停止，避免漏掉确认态 0）

// 卡库页状态
const libState = {
  mode: "price",          // price | theme
  price: "", kw: "", star: 0, ownOnly: false,
  module: "main",         // 卡册：main=普通卡 | 挖地 | 猫咪旅行 | 猫咪探险·西游记 | 猫咪探险·三国战役
  curThemeTid: null,
  openChains: new Set(),  // 已展开合成路径的 card id
};

// ---------- 工具 ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDur(sec) {
  if (!sec) return "";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h > 0 ? `${h}小时${m ? m + "分" : ""}` : `${m}分钟`;
}
function stars(n) {
  const v = Number(n) || 0;
  if (v <= 0) return '<span class="stars">☆</span>';
  return `<span class="stars">${"★".repeat(v)}</span>`;
}
// 套卡分类标签：主卡箱(发行/下架/闪卡) + 独立模块(挖地/猫咪旅行/猫咪探险·西游记/三国战役)
function kindTag(kind) {
  const map = {
    "在售": ["on", "发行"],
    "绝版": ["off", "下架"],
    "闪卡": ["flash", "闪卡"],
    "挖地": ["mod", "挖地"],
    "猫咪旅行": ["mod", "猫咪旅行"],
    "猫咪探险·西游记": ["mod", "西游记"],
    "猫咪探险·三国战役": ["mod", "三国战役"],
    "特殊": ["base", "特殊"],
  };
  const m = map[kind] || ["off", kind || "未知"];
  return `<span class="tag ${m[0]}">${m[1]}</span>`;
}
// 套卡属性标签：可合成/免合成（有无配方）+ 可变/不可变（能否变卡得到）
function attrTags(o) {
  if (o === null || o === undefined) return "";
  const c = o.composable
    ? '<span class="tag comp" title="能从素材卡一层层合成">可合成</span>'
    : '<span class="tag nocomp" title="无合成配方，只能抽卡/收集/变卡/交换获得">免合成</span>';
  const t = o.transferable
    ? '<span class="tag trans" title="可用≥面额的其他卡变卡得到">可变</span>'
    : '<span class="tag notrans" title="不能通过变卡得到">不可变</span>';
  return c + t;
}

// ---------- 操作日志（持久化到 localStorage，刷新/重开仍在） ----------
const LOG_KEY = "mc_op_log";
const LOG_MAX = 300;

function logLoad() {
  try {
    const raw = localStorage.getItem(LOG_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}
function logSave(entries) {
  try {
    localStorage.setItem(LOG_KEY, JSON.stringify(entries.slice(-LOG_MAX)));
  } catch {}
}
function logRender(entries) {
  // 渲染到所有带 data-op-log 的容器（总览 #log + 灵宠页全局日志镜像），实现「一份全局日志、多处可见」。
  const boxes = document.querySelectorAll("[data-op-log]");
  boxes.forEach((box) => {
    box.innerHTML = "";
    if (!entries.length) {
      const tip = document.createElement("div");
      tip.className = "empty-tip";
      tip.textContent = "无记录。";
      box.appendChild(tip);
      return;
    }
    for (const e of entries) {
      const line = document.createElement("div");
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = `[${e.ts}] `;
      const m = document.createElement("span");
      m.className = e.cls || "info";
      m.textContent = e.msg;
      line.appendChild(t);
      line.appendChild(m);
      box.appendChild(line);
    }
    box.scrollTop = box.scrollHeight;
  });
}
function logClear() {
  try { localStorage.removeItem(LOG_KEY); } catch {}
  logRender([]);
}
function log(msg, cls = "info") {
  const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const entries = logLoad();
  entries.push({ ts, msg, cls });
  logSave(entries);
  logRender(entries);
}
function logHistory() {
  logRender(logLoad());
}

// ---------- 视图切换 ----------
function showLogin() {
  $("login-view").classList.remove("hidden");
  $("dash-view").classList.add("hidden");
  $("top-user").textContent = "";
}
// 会话失效时强制回到扫码登录（清除已登录标记、停掉轮询、弹出二维码）
function forceLogin(reason) {
  loggedIn = false;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (stateTimer) { clearInterval(stateTimer); stateTimer = null; }
  showLogin();
  loadQr(reason);
}
function showDash(uin) {
  loggedIn = true;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  $("login-view").classList.add("hidden");
  $("dash-view").classList.remove("hidden");
  $("dash-uin").textContent = uin || "(未知)";
  $("top-user").textContent = uin ? `QQ ${uin}` : "";
  logHistory();  // 登录后先恢复历史操作日志（刷新/重开仍在）
  log(`已进入主面板，当前账号 ${uin || ""}`, "ok");
  switchPane("overview");
  loadState();
  if (stateTimer) clearInterval(stateTimer);
  if ($("auto-refresh").checked) stateTimer = setInterval(loadState, 60000);
}

// ---------- 二维码登录 ----------
async function loadQr(reason) {
  // 防御：按钮 onclick=loadQr 会把 PointerEvent 当 reason 传入，导致
  // setQrStatus 显示 "[object PointerEvent]"（2026-09-11 用户报告）。
  // 统一把非字符串 reason 收敛成可读文案。
  let msg = "请使用手机 QQ 扫码";
  let cls = "";
  if (typeof reason === "string" && reason) {
    cls = "warn";
    if (reason === "refresh") msg = "二维码已刷新，请扫码";
    else if (reason === "auto-refresh") msg = "二维码已自动刷新，请扫码";
    else msg = reason;
  }
  try {
    log("正在获取二维码…", "info");
    const r = await fetch("/api/login/qr?force=1");
    const d = await r.json();
    $("qr-img").src = d.qr;
    setQrStatus(msg, cls);
    log(`二维码已生成（qrsig_tail=${d.qrsig_tail || "?"}）等待扫码`, "info");
    startPoll();
  } catch (e) {
    log("获取二维码失败：" + e, "err");
  }
}
function setQrStatus(text, cls) {
  const el = $("qr-status");
  el.textContent = text;
  el.className = "status" + (cls ? " " + cls : "");
}
function startPoll(interval = POLL_NORMAL) {
  sawScanned = false;
  expiredAfterScan = 0;
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOnce, interval);
}
async function pollOnce() {
  if (loggedIn) return;
  let res;
  try {
    res = await (await fetch("/api/login/poll")).json();
  } catch { return; }
  const s = res.status;
  if (s === "scanned") {
    sawScanned = true;
    expiredAfterScan = 0;
    setQrStatus("已扫描，请在手机上点击确认", "warn");
    log("手机已扫描，待确认", "warn");
    // 已扫描：加速轮询，抓住确认态(0)那一下短暂窗口，避免漏掉导致前端误判已失效
    if (pollTimer) { clearInterval(pollTimer); pollTimer = setInterval(pollOnce, POLL_FAST); }
  } else if (s === "ok") {
    log(`登录成功！账号 ${res.uin}`, "ok");
    showDash(res.uin);
  } else if (s === "expired") {
    // 兜底1：先探测一次会话，避免「漏掉确认态 0 但 cookie 其实已到」被误判为失败
    try {
      const me = await (await fetch("/api/me")).json();
      if (me && me.logged_in) {
        log("检测到实际已登录（确认窗口被漏掉），进入主面板", "ok");
        showDash(me.uin);
        return;
      }
    } catch {}
    // 兜底2：绝不在轮询里自动重新取码（取新码会让用户正在扫的旧码立即失效，
    // 2026-09-11 实测：扫完码页面自动换码 → 手机提示"二维码已失效、登录不了"）。
    // 兜底3：也绝不把轮询停死。降速继续轮询即可——万一某个"已扫描待确认"状态
    // 被识别成 expired，手机点确认后的 code 0 仍能被收到并完成登录。
    expiredAfterScan++;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = setInterval(pollOnce, POLL_SLOW); }
    if (sawScanned) {
      setQrStatus("登录确认中…若长时间无反应请点「刷新二维码」", "warn");
      log(`已扫描，确认中（降速继续轮询 ${expiredAfterScan}）`, "info");
    } else {
      setQrStatus("二维码可能已失效，请点「刷新二维码」重新获取", "warn");
      log("二维码可能已失效（已降速继续轮询，也可点「刷新二维码」）", "warn");
    }
  }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !loggedIn && !pollTimer) startPoll();
});

// ---------- 快速登录 ----------
async function checkQuick() {
  const info = $("quick-info");
  const btn = $("quick-launch");
  try {
    const d = await (await fetch("/api/login/quick")).json();
    if (d.available) {
      info.textContent = `发现本机 QQ：${d.uin || ""}，可一键登录`;
      info.className = "status ok";
      btn.textContent = "一键快速登录";
      btn.classList.remove("hidden");
      btn.onclick = () => doQuickLogin();
    } else {
      info.textContent = d.reason || "未检测到本机 QQ 一键登录能力";
      info.className = "status warn";
      btn.textContent = "唤起本机 QQ";
      btn.classList.remove("hidden");
      btn.onclick = () => { window.location.href = "tencent://"; };
    }
    log("快速登录检测：" + (d.available ? "可用" : "当前环境不支持"), d.available ? "ok" : "warn");
  } catch {
    info.textContent = "检测失败，请使用扫码登录";
    info.className = "status warn";
    log("快速登录检测失败", "err");
  }
}
async function doQuickLogin() {
  alert("当前环境暂不支持纯 web 一键登录，请使用二维码登录。");
}

document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    const tab = t.dataset.tab;
    $("tab-qr").classList.toggle("hidden", tab !== "qr");
    $("tab-quick").classList.toggle("hidden", tab !== "quick");
    if (tab === "quick") checkQuick();
  };
});

$("qr-refresh").onclick = () => loadQr("refresh");
$("logout").onclick = async () => {
  try { await fetch("/api/logout"); } catch {}
  location.reload();
};

// ---------- 子页切换 ----------
function switchPane(pane) {
  document.querySelectorAll(".subnav .btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.pane === pane));
  $("pane-overview").classList.toggle("hidden", pane !== "overview");
  $("pane-lib").classList.toggle("hidden", pane !== "lib");
  $("pane-cardbox").classList.toggle("hidden", pane !== "cardbox");
  $("pane-auto").classList.toggle("hidden", pane !== "auto");
  $("pane-exchange").classList.toggle("hidden", pane !== "exchange");
  $("pane-lottery").classList.toggle("hidden", pane !== "lottery");
  $("pane-spirite").classList.toggle("hidden", pane !== "spirite");
  const inOverview = pane === "overview";
  $("refresh-btn").classList.toggle("hidden", !inOverview);
  $("auto-refresh").classList.toggle("hidden", !inOverview);
  if (pane === "overview") ovLoadProps();
  if (pane === "lib") initLibOnce();
  if (pane === "cardbox") initCardBoxOnce();
  if (pane === "auto") initAutoOnce();
  if (pane === "exchange") initExchangeOnce();
  if (pane === "lottery") initLotteryOnce();
  if (pane === "spirite") { initSpiriteOnce(); spStartLive(); }
  else spStopLive();
}
document.querySelectorAll(".subnav .btn").forEach((b) => {
  b.onclick = () => switchPane(b.dataset.pane);
});

// ---------- 游戏状态（总览） ----------
function fmtLeft(sec) {
  if (sec <= 0) return "已完成";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h > 0 ? `${h}小时${m}分` : (m > 0 ? `${m}分${s}秒` : `${s}秒`);
}
function chip(k, v, small = "") {
  return `<div class="stat-chip"><span class="k">${k}</span><span class="v">${v}${small ? `<small>${small}</small>` : ""}</span></div>`;
}

// 炉位上的「取消 / 取卡」按钮。取消后素材卡会原样退回卡箱，不会消失。
async function onSlotAction(e) {
  const b = e.currentTarget, act = b.dataset.act, slot = b.dataset.slot;
  const isCancel = act === "cancel";
  if (!confirm(isCancel
      ? `取消炉位 ${slot} 的炼制？素材卡会退回卡箱。`
      : `取出炉位 ${slot} 已炼好的卡？`)) return;
  b.disabled = true;
  try {
    const url = isCancel ? `/api/card/cancel?slot=${slot}`
                         : `/api/refine/get?slotid=${slot}`;
    const d = await (await fetch(url, { method: isCancel ? "POST" : "GET" })).json();
    log(`${isCancel ? "取消炼卡" : "取卡"} 炉位 ${slot}：${d.ok ? "成功" : "失败 " + (d.message || d.code || "")}`,
        d.ok ? "ok" : "err");
    // 取「自己炉位」的卡会派生 1 次翻牌机会，后端已顺手翻掉（main.py refine_get）：
    // 不在这里补记，就会出现「牌翻了、次数没了、日志里却什么都没有」。
    // 偷炉取卡走 get_steal_card 直接返回，不带 pai 字段，故用 in 判断区分。
    if (!isCancel && "pai" in d) {
      log(`翻牌（取卡附带）：${d.pai ? "成功" : "失败"} ${d.pai_message || ""}`,
          d.pai ? "ok" : "err");
    }
  } catch (err) {
    log("操作异常：" + err, "err");
  }
  await loadState();
}

async function loadState() {
  const box = $("state-box");
  try {
    const d = await (await fetch("/api/state")).json();
    if (d.need_login || d.code === -1001 || /重新登陆/.test(d.message || "")) {
      forceLogin("会话已过期（skey 失效），请重新扫码登录");
      return;
    }
    // 本地连接断开 / 腾讯不可达：保持登录态，仅提示重试，绝不强制重新扫码
    // （用户 2026-09-11：连接断了 ≠ 会话过期，不能把"连不上"误判成"要重新登录"）
    if (d.conn_error || d.code === -1 || /连接腾讯|超时|网络抖动/.test(d.message || "")) {
      box.innerHTML = `<div class="err" style="grid-column:1/-1">连接异常：${d.message || ""}（稍后点刷新重试，无需重新登录）</div>`;
      log("连接腾讯失败（疑似本地网络/连接断开），保持登录态，未强制重新扫码", "warn");
      return;
    }
    if (!d.ok) {
      box.innerHTML = `<div class="err" style="grid-column:1/-1">读取失败：code=${d.code ?? "?"} ${d.message || ""}</div>`;
      log(`读取游戏状态失败：code=${d.code ?? "?"} ${d.message || ""}`, "err");
      return;
    }
    const u = d.user || {};
    const sb = d.stovebox || {}, cb = d.changebox || {}, st = d.storebox || {};

    const slots = (sb.cards || []).map((c) => {
      if (!c.id) return `<span class="slot empty"><b>空位</b><span class="sname">可用</span></span>`;
      // 偷炉：用好友空炉炼自己的卡（card@slottype="steal"）。卡炼好后好友若不管，
      // 可收到自己卡箱；若好友发现收走则偷失败。因此仍能取卡，只是有被收走风险。
      const steal = c.slottype === "steal";
      const cls = c.flag === 2 ? "done" : "busy";
      const txt = c.flag === 2 ? (steal ? "可取卡（偷炉）" : "可取卡") : `剩 ${fmtLeft(c.left)}`;
      const star = c.star ? "★".repeat(c.star) : "无星";
      const pub = c.publish ? new Date(c.publish * 1000).toISOString().slice(0, 10) : "";
      const future = c.publish && c.publish * 1000 > Date.now();
      const matTxt = (c.mats || []).length
        ? `起炼素材 ${[...new Set(c.mats)].join("/")}` : "";
      const tags = [
        c.theme || "未知套卡",
        star,
        `面值 ${c.price || "?"}`,
        pub ? (future ? `${pub} 待上架` : `${pub} 上架`) : "",
        matTxt,
        c.starter ? "商店可炼" : (matTxt ? "需高级素材" : ""),
        c.flash ? "闪卡" : "",
        steal ? "【偷炉·好友可能收走】" : "",
      ].filter(Boolean).join(" · ");
      // flag=2 已完成可取出（偷炉也能取，好友不管则归自己）；炼制中才给取消按钮。
      let btn;
      if (c.flag === 2) {
        btn = `<button class="sact" data-act="get" data-slot="${c.slot}">取卡</button>`;
      } else {
        btn = `<button class="sact danger" data-act="cancel" data-slot="${c.slot}">取消</button>`;
      }
      const note = steal
        ? `<span class="snote">偷炉：好友可能收走导致失败；好友不管则入自己卡箱</span>`
        : "";
      return `<span class="slot ${cls}" title="card_id=${c.id}">
        <b>${esc(c.name || "卡" + c.id)}</b><span class="sname">${txt}</span>
        <span class="smeta">${esc(tags)}</span>${note}${btn}</span>`;
    }).join("") || '<span class="slot empty">无炼卡位数据</span>';

    const stoves = (d.stoves || []).map((s) => {
      const left = s.endtime - d.now;
      return { id: s.id, state: left <= 0 ? "空闲" : `炼制中 ${fmtLeft(left)}` };
    });
    const busyN = stoves.filter((s) => s.state !== "空闲").length;
    const stoveTxt = stoves.length
      ? `炼炉 ${stoves.map((s) => `#${s.id} ${s.state}`).join("、")}`
      : "无炼炉数据";
    const doneSlots = (sb.cards || []).filter((c) => c.id !== 0 && c.flag === 2).length;

    box.innerHTML =
      chip("昵称", u.nick || "-", `QQ ${u.uin || "-"}`) +
      chip("金币", (u.money || 0).toLocaleString()) +
      chip("经验", (u.exp || 0).toLocaleString()) +
      chip("随机抽卡", `${u.randchance || 0} 次`) +
      chip("炼卡位", `${sb.cur || 0} / ${sb.max || 0}`, doneSlots ? `${doneSlots} 位可取` : "") +
      chip("换卡箱", `${cb.filled ?? 0} / ${cb.cur || 0}`,
           cb.max > cb.cur ? `未开通 ${cb.max - cb.cur} 格` : "") +
      chip("保险箱", `${st.filled ?? 0} / ${st.cur || 0}`,
           st.max > st.cur ? `未开通 ${st.max - st.cur} 格` : "") +
      chip("炼炉", busyN ? `${busyN} 个炼制中` : "全部空闲") +
      `<div class="stat-chip" style="grid-column:1/-1">
         <span class="k">炼卡位明细</span>
         <div class="slot-list">${slots}</div>
       </div>` +
      `<div class="stat-chip" style="grid-column:1/-1">
         <span class="k">${stoveTxt}</span>
       </div>`;
    box.querySelectorAll(".sact").forEach((b) => b.onclick = onSlotAction);
    log(`状态已刷新：${u.nick || ""} 金币 ${(u.money || 0).toLocaleString()}${busyN ? ` / ${busyN} 炉炼制中` : ""}`, "ok");
  } catch (e) {
    box.innerHTML = `<div class="err" style="grid-column:1/-1">请求失败：${e}</div>`;
    log("读取游戏状态异常：" + e, "err");
  }
}
$("refresh-btn").onclick = loadState;
$("auto-refresh").onchange = (e) => {
  if (stateTimer) { clearInterval(stateTimer); stateTimer = null; }
  if (e.target.checked) stateTimer = setInterval(loadState, 60000);
  log(e.target.checked ? "已开启自动刷新（60 秒）" : "已关闭自动刷新", "info");
};

$("probe-btn").onclick = async () => {
  const btn = $("probe-btn");
  btn.disabled = true; btn.textContent = "探测中…";
  log("开始探测魔法卡片真实接口…", "info");
  try {
    const d = await (await fetch("/api/probe")).json();
    if (d.hosts_found && d.hosts_found.length)
      log("入口页出现的主机：" + d.hosts_found.join("、"), "info");
    else if (d.page_error) log("拉取入口页失败：" + d.page_error, "err");
    (d.tests || []).forEach((t) => {
      if (t.error) log(`${t.base} → 错误：${t.error}`, "err");
      else log(`${t.base} → HTTP ${t.status}　${(t.head || "").replace(/\s+/g, " ").slice(0, 70)}`, t.status === 200 ? "ok" : "warn");
    });
    log("探测完成：card.show.qq.com 活着，返回标准 XML。", "ok");
  } catch (e) {
    log("探测请求失败：" + e, "err");
  } finally {
    btn.disabled = false; btn.textContent = "探测接口（调试）";
  }
};

// ============================================================
// 换卡（手动模式）
// ============================================================
const exState = {
  inited: false,
  // 目标套**可多选**：themeIds 为 Set<tid>，themeNames 为 tid→套名映射。
  // themeTid/themeName 保留为「主套」（自动换卡等单套逻辑用），取值 = 已选中的第一套。
  themeIds: new Set(),
  themeNames: {},
  themeTid: 0,
  themeName: "",
  price: "",
  apiPeers: [],        // 服务器按主题搜到的卡友（多套时是各套并集）
  manualPeers: [],     // 手动补充（持久化在服务端）
  manualText: "",
  themeCards: [],      // 所有已选目标套的卡（合并，含 theme_name）
  pickCards: new Set(), // 用户点名指定的卡（可跨套）
  cardKw: "",
  results: null,       // 最近一次扫描结果
};
let scanTimer = null;  // 扫描进行中的计时器

async function initExchangeOnce() {
  if (exState.inited) return;
  exState.inited = true;
  bindExchangeEvents();
  // 拉一次已有手动名单
  try {
    const d = await (await fetch("/api/exchange/peers?tid=0")).json();
    if (d.ok) exState.manualPeers = d.manual_peers || [];
  } catch {}
  renderManualPeers();
}

function bindExchangeEvents() {
  $("ex-theme-search").onclick = searchThemes;
  $("ex-theme-kw").addEventListener("keydown", (e) => {
    if (e.key === "Enter") searchThemes();
  });
  $("ex-price").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doScan();
  });
  $("ex-peers-btn").onclick = fetchPeers;
  $("ex-scan-btn").onclick = () => doScan(false);
  $("ex-reset").onclick = clearResults;
  $("ex-autoex-start").onclick = exAutoStart;
  $("ex-autoex-stop").onclick = exAutoStop;
  $("ex-pay-load").onclick = exLoadPayCards;
  $("ex-pay-price").addEventListener("input", () => { if (exPayCache) exRenderPay(); });
  $("ex-pay-all").onclick = exShowAllPay;
  // 指定卡：判空保护——若浏览器仍缓存旧 index.html（DOM 里还没有这两个元素），
  // 不能因为取不到元素就让整个换卡面板的绑定中断
  const ckw = $("ex-card-kw");
  if (ckw) {
    ckw.addEventListener("input", () => {
      exState.cardKw = ckw.value;
      exRenderCardPick();
    });
    ckw.addEventListener("keydown", (e) => { if (e.key === "Enter") doScan(); });
  }
  const cclr = $("ex-card-clear");
  if (cclr) {
    cclr.onclick = () => {
      exState.pickCards = new Set();
      exState.cardKw = "";
      if (ckw) ckw.value = "";
      exRenderCardPick();
    };
  }
  // 日志面板（右栏）与「只看可换」开关：判空保护同 ckw——旧缓存 index.html 里没这些元素
  const lclr = $("ex-log-clear");
  if (lclr) {
    lclr.onclick = () => {
      const box = $("ex-msgs");
      if (box) box.innerHTML = "";
      exMsg("日志已清空", "info");
    };
  }
  const hl = $("ex-hide-locked");
  if (hl) hl.onchange = () => { if (exState.results) renderScan(exState.results); };
}

// ---------- 自动换卡 ----------
let exAutoTimer = null;

function exAutoTidText() {
  $("ex-autoex-tid").textContent = exState.themeTid
    ? `${exState.themeName || "#" + exState.themeTid}（#${exState.themeTid}）` : "未选（用上方搜套）";
}

// 指定付出卡：勾选后只用这些卡去换，且可全部换完（不保留最后一张）
//
// 2026-09-10 修：此前列表会「静默」按面值过滤 —— 面值取自自动面板的面值框，
// 为空时还会回落到上方**手动换卡**面板的面值框。于是「加载我的卡」经常只显示
// 寥寥几行（如面值 82 → 4 种），看着就像卡没加载全。现在：
//   ① 过滤只认本行自己的 #ex-pay-price，不再跨面板继承；
//   ② 汇总行永远写明「共 N 张 / M 种」及过滤状态，绝不再静默；
//   ③ 提供「显示全部」一键回到全量。
let exPayCache = null;   // {all:[卡...], cbN, sbN, kinds}
let exPaySel = new Set();  // 已勾选的卡 id（独立于面值过滤，切过滤不丢）

async function exLoadPayCards() {
  const box = $("ex-pay-list");
  if (!box) return;
  box.textContent = "加载中…";
  try {
    const st = await (await fetch("/api/state")).json();
    const cb = (st.changebox || {}).cards || [];
    const sb = (st.storebox || {}).cards || [];
    const all = [...cb, ...sb];
    exPayCache = {
      all, cbN: cb.length, sbN: sb.length,
      kinds: new Set(all.map((c) => Number(c.id || 0)).filter(Boolean)).size,
    };
    // 清掉已经不在箱里的勾选（卖了/换出去了）
    const owned = new Set(all.map((c) => Number(c.id || 0)));
    exPaySel = new Set([...exPaySel].filter((id) => owned.has(id)));
    exRenderPay();
  } catch (e) {
    box.textContent = "加载失败：" + (e.message || e);
  }
}

// 列表容器上挂一次委托：勾选/取消只更新选择集与汇总行，不重渲染（不丢滚动位置）
function bindPayListOnce() {
  const box = $("ex-pay-list");
  if (!box || box.dataset.payBound) return;
  box.dataset.payBound = "1";
  box.addEventListener("change", (e) => {
    const cb = e.target;
    if (!cb || !cb.classList || !cb.classList.contains("ex-pay-cb")) return;
    const id = Number(cb.value);
    if (!id) return;
    if (cb.checked) exPaySel.add(id); else exPaySel.delete(id);
    exUpdatePaySum();
  });
}

// 从缓存渲染（输入面值即时过滤，不重复请求 /api/state）
function exRenderPay() {
  const box = $("ex-pay-list");
  if (!exPayCache || !box) return;
  bindPayListOnce();
  const price = exPayPrice();
  const m = new Map();
  for (const c of exPayCache.all) {
    const cid = Number(c.id || 0);
    if (!cid) continue;                                    // 空槽位
    if (price && Number(c.price || 0) !== price) continue; // 仅按本行面值框过滤
    if (!m.has(cid)) m.set(cid, { id: cid, name: c.name || ("卡" + cid), price: Number(c.price || 0), n: 0, theme: (c.theme || "未知道具") });
    m.get(cid).n += 1;
  }
  const rows = [...m.values()].sort((a, b) => b.price - a.price || b.n - a.n || a.id - b.id);
  // 按套卡分组
  const groups = new Map();
  for (const c of rows) {
    const t = c.theme || "其他";
    if (!groups.has(t)) groups.set(t, []);
    groups.get(t).push(c);
  }
  box.style.color = "";
  box.innerHTML = [...groups.entries()].map(([tn, cs]) =>
    `<div class="pay-group"><div class="pay-group-h"><span>${esc(tn)}</span><span class="pay-gn">${cs.length} 种</span></div>`
    + `<div class="pay-items">${cs.map((c) => `<label class="pay-it"><input type="checkbox" class="ex-pay-cb" value="${c.id}"`
        + `${exPaySel.has(c.id) ? " checked" : ""}>`
        + `<span class="pay-nm" title="${esc(c.theme)} · ${esc(c.name)}">${esc(c.name)}</span>`
        + `<span class="pay-meta">${c.price}×${c.n} <span style="color:var(--muted);font-size:11px">#${c.id}</span></span></label>`).join("")}</div></div>`
  ).join("") || `<span style="color:var(--muted)">没有${price ? `面值 ${price} 的` : ""}可付出的卡</span>`;
  exUpdatePaySum(rows.length);
}

function exPayPrice() {
  const el = $("ex-pay-price");
  return Number(((el && el.value) || "").trim() || 0);
}

function exUpdatePaySum(nRows) {
  const sum = $("ex-pay-sum"), btn = $("ex-pay-all");
  if (!sum || !exPayCache) return;
  const price = exPayPrice();
  const n = nRows === undefined
    ? [...document.querySelectorAll(".ex-pay-cb")].length : nRows;
  if (btn) btn.classList.toggle("hidden", !price);
  sum.innerHTML = `已载入 <b>${exPayCache.all.length}</b> 张 / <b>${exPayCache.kinds}</b> 种`
    + `（换卡箱 ${exPayCache.cbN} 张 + 保险箱 ${exPayCache.sbN} 张）`;
  if (price) {
    sum.innerHTML += ` ｜ <span style="color:#a1580a">已按面值 ${price} 过滤，列出 <b>${n}</b> 种`
      + `（该面值的卡这次才能用）</span> · <a href="#" id="ex-pay-all2">显示全部</a>`;
    const a = $("ex-pay-all2");
    if (a) a.onclick = (e) => { e.preventDefault(); exShowAllPay(); };
  } else {
    sum.innerHTML += " ｜ 显示全部";
  }
  if (exPaySel.size) sum.innerHTML += ` ｜ 已勾选 <b>${exPaySel.size}</b> 种（切面值不丢）`;
}

function exShowAllPay() {
  const el = $("ex-pay-price");
  if (el) el.value = "";
  exRenderPay();
}

function exPaySelected() {
  return [...exPaySel].filter(Boolean);
}

async function exAutoStart() {
  if (!exState.themeTid) { exMsg("请先在上方搜并选定目标套卡", "err"); return; }
  const price = ($("ex-autoex-price").value || "").trim() || ($("ex-price").value || "").trim();
  const body = {
    tid: exState.themeTid,
    price: Number(price || 0),
    want: Number(($("ex-autoex-want").value || "1").trim() || 1),
    max_peers: Number(($("ex-autoex-max").value || "0").trim() || 0),
    dry_run: !!$("ex-autoex-dry").checked,
  };
  // 多选源套：复用「想收的套」多选，作为玩家池来源（可跨套换指定卡）。
  const srcTids = exTargetTids();
  if (srcTids.length) body.tids = srcTids;
  // 多选指定卡：复用「指定卡」多选，作为要换入的目标（可跨套）。
  const tcards = [...exState.pickCards].filter(Boolean);
  if (tcards.length) body.card_ids = tcards;
  const pay = exPaySelected();
  if (pay.length) body.pay_card_ids = pay;
  $("ex-autoex-start").disabled = true;
  try {
    const d = await (await fetch("/api/exchange/auto/start", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })).json();
    if (!d.ok) { exMsg("启动失败：" + (d.message || ""), "err"); return; }
    const srcTxt = (body.tids && body.tids.length > 1)
      ? `${body.tids.length} 个源套（#${body.tids.join("/")}）`
      : `主套 #${body.tid}`;
    const tgtTxt = (body.card_ids && body.card_ids.length)
      ? `指定 ${body.card_ids.length} 张卡（跨套）`
      : `主套全部卡${body.price ? " 面值" + body.price : ""}`;
    exMsg(`自动换卡已启动（${body.dry_run ? "模拟" : "真实"}，${srcTxt}，扫玩家箱换入：${tgtTxt}，每张 ${body.want} 张，`
      + `付出：${pay.length ? `指定 ${pay.length} 种（可全换完）` : "自动挑多余的（每种留 1 张）"}）`, "ok");
    $("ex-autoex-stop").disabled = false;
    if (exAutoTimer) clearInterval(exAutoTimer);
    exAutoTimer = setInterval(exAutoPoll, 3000);
    exAutoPoll();
  } catch (e) {
    exMsg("启动异常：" + (e.message || e), "err");
  } finally {
    $("ex-autoex-start").disabled = false;
  }
}

async function exAutoStop() {
  await fetch("/api/exchange/auto/stop", { method: "POST" });
  exMsg("已请求停止自动换卡", "info");
}

async function exAutoPoll() {
  try {
    const d = await (await fetch("/api/exchange/auto/status")).json();
    renderExAuto(d);
    if (!d.running && exAutoTimer) {
      clearInterval(exAutoTimer); exAutoTimer = null;
      $("ex-autoex-start").disabled = false;
      $("ex-autoex-stop").disabled = true;
      if (d.message) exMsg(d.message, d.need_total === 0 ? "ok" : "warn");
    } else {
      $("ex-autoex-start").disabled = true;
      $("ex-autoex-stop").disabled = false;
    }
  } catch {}
}

function renderExAuto(d) {
  if (!d) return;
  $("ex-autoex-state").textContent = d.running
    ? `运行中 ${d.scanned}/${d.peers_total} 卡友 · 已换 ${d.exchanged}`
    : (d.finished ? `已结束 · 换入 ${d.exchanged} 张` : "未运行");
  const need = d.need || [];
  $("ex-autoex-need").innerHTML = need.length
    ? `还缺 ${d.need_total} 张：` + need.map((r) =>
        `<span class="tag base" style="margin-right:4px">${esc(r.name)} ${r.price}（有${r.own} 缺${r.need}）</span>`).join("")
    : (d.finished ? "目标已凑齐，没有缺口" : "");
  const diag = d.finished && d.need_total
    ? `\n看到缺的卡 ${d.seen_target || 0} 次｜被锁 ${d.miss_locked || 0}｜无卡可付 ${d.miss_no_pay || 0}\n`
    : "";
  $("ex-autoex-logs").textContent = diag + (d.logs || []).map((l) => {
    const t = new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
    return `[${t}] ${l.ok ? "✓" : "✗"} ${l.action} ${l.detail || ""}`;
  }).join("\n");
}

// 换卡页的操作日志：写进右栏常驻面板 #ex-msgs（原来是 prepend 到 #ex-body，
// 与结果列表同处一个滚动区 —— 往下拉去点交换就看不见日志了，2026-09-18 用户反馈后改）。
// 新消息在最上面，面板自身滚动，故永远可见。
function exMsg(text, cls = "warn") {
  const box = $("ex-msgs") || $("ex-body");
  if (!box) return;
  const m = document.createElement("div");
  m.className = `ex-msg ${cls}`;
  const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  m.innerHTML = `<span class="ts">${ts}</span>${esc(text)}`;
  box.insertBefore(m, box.firstChild);
  // 只留最近 200 条，避免长时间挂机后 DOM 无限增长
  while (box.children.length > 200) box.removeChild(box.lastChild);
  const cnt = $("ex-log-cnt");
  if (cnt) cnt.textContent = `${box.children.length} 条`;
  log(`[换卡] ${text}`, cls === "ok" ? "ok" : (cls === "err" ? "err" : "info"));
}

async function searchThemes() {
  const kw = ($("ex-theme-kw").value || "").trim();
  if (!kw) return;
  const box = $("ex-theme-cands");
  box.classList.remove("hidden");
  box.innerHTML = '<span class="chip" style="cursor:default">搜索中…</span>';
  try {
    const d = await (await fetch(`/api/cards/themes?q=${encodeURIComponent(kw)}&limit=60`)).json();
    if (!d.themes || !d.themes.length) {
      box.innerHTML = '<span style="color:var(--muted);font-size:12.5px">没有匹配的套卡</span>';
      return;
    }
    box.innerHTML = d.themes.map((t) =>
      `<span class="chip" data-tid="${t.tid}" data-name="${esc(t.name)}">${esc(t.name)} ${kindTag(t.kind)}</span>`
    ).join("");
    // 目标套可多选：不必清空之前的 active，同时搜索结果也要能反映「哪些已被选中」。
    // 这个同步在别处（已选行点 × 移除）也会发生，故抽成公共函数。
    exMarkThemeCands();
    box.querySelectorAll(".chip[data-tid]").forEach((c) => {
      c.onclick = () => {
        const tid = Number(c.dataset.tid);
        if (exState.themeIds.has(tid)) removeTargetTheme(tid);
        else addTargetTheme(tid, c.dataset.name);
      };
    });
  } catch (e) {
    box.innerHTML = `<span style="color:var(--err)">搜索失败：${esc(e.message || e)}</span>`;
  }
}

// ---------- 目标套（可多选） ----------
// 为什么要多套：腾讯只能按套搜卡友（theme_peers），而**在炼某套卡的卡友，换卡箱里
// 往往也放着别的套的卡**。多选后后端会把这些套的玩家池并起来，命中任一目标套的卡都列出。
function exTargetTids() {
  return [...exState.themeIds].filter((t) => Number(t) > 0);
}

function addTargetTheme(tid, name) {
  tid = Number(tid);
  if (!tid) return;
  exState.themeIds.add(tid);
  exState.themeNames[tid] = name || exState.themeNames[tid] || `#${tid}`;
  syncTargetThemes();
  const n = exTargetTids().length;
  exMsg(n > 1
    ? `已加入目标套「${exState.themeNames[tid]}」，当前共 ${n} 套。扫描时会把这些套的卡友池合并再筛。`
    : `目标套卡已设为「${exState.themeNames[tid]}」。可继续搜别的套加入多选。`, "ok");
}

function removeTargetTheme(tid) {
  tid = Number(tid);
  exState.themeIds.delete(tid);
  delete exState.themeNames[tid];
  syncTargetThemes();
  const n = exTargetTids().length;
  exMsg(n ? `已移除该目标套，当前还剩 ${n} 套。` : "已清空目标套：请先用上方「搜套」选择想收的套卡。", "warn");
}

/** 单选某套（保留兼容入口：等价于「只保留这一套」）。 */
function selectTargetTheme(tid, name) {
  tid = Number(tid);
  exState.themeIds = new Set(tid ? [tid] : []);
  exState.themeNames = tid ? { [tid]: name || `#${tid}` } : {};
  syncTargetThemes();
}

/** 让「搜索结果」里的套卡 chip 与已选集合保持一致的选中态。 */
function exMarkThemeCands() {
  const box = $("ex-theme-cands");
  if (!box) return;
  box.querySelectorAll(".chip[data-tid]").forEach((x) => {
    x.classList.toggle("active", exState.themeIds.has(Number(x.dataset.tid)));
  });
}

function syncTargetThemes() {
  const ids = exTargetTids();
  const first = ids[0] || 0;
  exState.themeTid = first;                       // 主套（自动换卡等单套逻辑用）
  exState.themeName = first ? (exState.themeNames[first] || "") : "";
  exState.apiPeers = [];
  clearResults();
  const t = $("ex-target");
  if (t) {
    const nm = ids.map((i) => exState.themeNames[i] || `#${i}`);
    t.textContent = !ids.length ? "未选目标套"
      : ids.length === 1 ? `目标：${nm[0]}（#${first}）`
      : `目标：${ids.length} 套（${nm.slice(0, 3).join("、")}${nm.length > 3 ? "…" : ""}）`;
  }
  exAutoTidText();
  $("ex-cnt").textContent = "";
  hideAutoPeers();
  renderPickedThemes();
  exMarkThemeCands();   // 从「已选行」移除时，搜索结果的高亮也要同步撤掉
  // 目标套变了，卡候选要按新集合重建；已指定的卡若已不在目标套内会自动取消。
  exLoadThemeCards(ids);
}

function renderPickedThemes() {
  const wrap = $("ex-theme-picked");
  if (!wrap) return;
  const head = '<span style="color:var(--muted);font-size:12.5px">已选目标套</span>';
  const ids = exTargetTids();
  if (!ids.length) {
    wrap.classList.add("hidden");
    wrap.innerHTML = head;
    return;
  }
  wrap.classList.remove("hidden");
  wrap.innerHTML = head + ids.map((tid) => {
    const nm = exState.themeNames[tid] || `#${tid}`;
    return `<span class="chip active" data-pick-tid="${tid}" title="点击移除该目标套"
      >${esc(nm)} <span style="opacity:.7">#${tid}</span>
      <b data-del-tid="${tid}" style="margin-left:4px;cursor:pointer;font-weight:600"
         title="移除">×</b></span>`;
  }).join("") + (ids.length > 1
    ? `<span style="font-size:12px;color:var(--muted)">玩家池取各套并集，命中任一目标套的卡都会列出</span>`
    : "");
  wrap.querySelectorAll("[data-del-tid]").forEach((el) => {
    el.onclick = (ev) => { ev.stopPropagation(); removeTargetTheme(Number(el.dataset.delTid)); };
  });
  wrap.querySelectorAll(".chip[data-pick-tid]").forEach((el) => {
    el.onclick = () => removeTargetTheme(Number(el.dataset.pickTid));
  });
}

// ---------- 指定卡（可多选） ----------
// 腾讯没有「按卡搜人」的接口 —— 搜卡友只能按套（card_user_theme_list(tid)）。
// 所以「找出有某张卡的人」的真实含义是：在该套的玩家池里，筛出换卡箱正放着这张卡的人。
// 指定卡只影响「筛选条件」，不改变搜索范围（池外的人依然搜不到）。
// 指定卡候选人 = 所有已选目标套的卡合并（跨套选卡是有效用法），带 theme_name 便于分辨。
// 多套并行拉取，用 token 防止慢请求覆盖快请求的结果。
let exCardsToken = 0;

async function exLoadThemeCards(tids) {
  const ids = (Array.isArray(tids) ? tids : [tids]).filter((t) => Number(t) > 0);
  const token = ++exCardsToken;
  exState.themeCards = [];
  exRenderCardPick();
  const wrap = $("ex-card-wrap");
  if (wrap) wrap.classList.toggle("hidden", !ids.length);
  if (!ids.length) { if ($("ex-card-cands")) $("ex-card-cands").classList.add("hidden"); return; }
  const sel = $("ex-card-sel");
  if (sel) {
    sel.textContent = `正在加载 ${ids.length} 套的卡列表…`;
    sel.style.color = "var(--muted)";
  }
  try {
    const groups = await Promise.all(ids.map(async (tid) => {
      try {
        const d = await (await fetch(`/api/cards/theme/${tid}`)).json();
        return (d && d.cards ? d.cards : []).map((c) => ({
          id: Number(c.id), name: String(c.name || ""), price: c.price,
          theme_id: tid, theme_name: exState.themeNames[tid] || `#${tid}`,
        }));
      } catch {
        return [];
      }
    }));
    if (token !== exCardsToken) return;   // 期间又换了一次目标套，丢弃本次结果
    exState.themeCards = groups.flat();
    // 目标套变过之后，把「已不再属于任何目标套」的指定卡取消，
    // 否则界面显示已选 N 张、后端却把它们剔除，看不出为何扫不到东西。
    const valid = new Set(exState.themeCards.map((c) => c.id));
    exState.pickCards = new Set([...exState.pickCards].filter((id) => valid.has(id)));
    if (!exState.themeCards.length && sel) {
      sel.textContent = "这些套的卡列表加载失败";
      sel.style.color = "var(--err)";
    }
  } catch (e) {
    if (sel && token === exCardsToken) {
      sel.textContent = "卡列表加载失败：" + (e.message || e);
      sel.style.color = "var(--err)";
    }
    return;
  }
  exRenderCardPick();
}

function exRenderCardPick() {
  const box = $("ex-card-cands");
  if (!box) return;
  const kw = (exState.cardKw || "").trim().toLowerCase();
  const list = exState.themeCards.filter((c) => !kw || c.name.toLowerCase().includes(kw));
  if (!list.length) {
    box.classList.remove("hidden");
    box.innerHTML = `<span style="color:var(--muted);font-size:12.5px">${exState.themeCards.length ? "没有匹配的卡名" : "（暂无该套卡列表）"}</span>`;
    exCardSelText();
    return;
  }
  box.classList.remove("hidden");
  const multi = exTargetTids().length > 1;   // 多套时给卡名加上所属套前缀，避免同名/串套难辨
  box.innerHTML = list.map((c) =>
    `<span class="chip ${exState.pickCards.has(c.id) ? "active" : ""}" data-cid="${c.id}"
       title="点击选中/取消（${multi ? `套：${c.theme_name || ""}，` : ""}面值 ${c.price}）"
       >${multi ? `<span style="opacity:.6;font-size:11px">${esc(c.theme_name || "")}</span> ` : ""
       }${esc(c.name)} <span style="opacity:.75">${c.price}</span></span>`
  ).join("");
  box.querySelectorAll(".chip[data-cid]").forEach((el) => {
    el.onclick = () => {
      const cid = Number(el.dataset.cid);
      if (exState.pickCards.has(cid)) exState.pickCards.delete(cid);
      else exState.pickCards.add(cid);
      el.classList.toggle("active", exState.pickCards.has(cid));
      exCardSelText();
    };
  });
  exCardSelText();
}

function exCardSelText() {
  const el = $("ex-card-sel");
  if (!el) return;
  const n = exState.pickCards.size;
  if (!n) {
    el.textContent = exTargetTids().length
      ? "未指定：目标套全部卡都算命中（可从下方点选若干张）"
      : "未指定：请先选择目标套";
    el.style.color = "var(--muted)";
    return;
  }
  const names = exState.themeCards.filter((c) => exState.pickCards.has(c.id)).map((c) => c.name);
  el.textContent = `已指定 ${n} 张：${names.slice(0, 4).join("、")}${names.length > 4 ? ` 等 ${n} 张` : ""}`;
  el.style.color = "var(--qq-blue-d)";
}

function hideAutoPeers() {
  const w = $("ex-auto-wrap");
  if (w) { w.classList.add("hidden"); w.innerHTML = ""; }
}

function renderAutoPeers(list) {
  const w = $("ex-auto-wrap");
  if (!list || !list.length) { hideAutoPeers(); return; }
  w.classList.remove("hidden");
  const first = list.slice(0, 12);
  const more = list.length - first.length;
  w.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--muted);margin:2px 0 4px">
      <b style="color:var(--text)">自动卡友（${list.length} 人）</b>
      <span>正在玩该套，可直接扫描；也可复制下面 QQ 到上方手动名单长期留存</span>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;line-height:1.9">
      ${first.map((u) => `<span class="chip" style="cursor:default;font-size:12px;padding:1px 9px">${u}</span>`).join("")}
      ${more > 0 ? `<span style="font-size:12px;color:var(--muted);align-self:center">…等 ${more} 人</span>` : ""}
    </div>`;
}

async function fetchPeers() {
  const tids = exTargetTids();
  if (!tids.length) { exMsg("请先搜并选择想收的套卡（可多选）", "err"); return; }
  const btn = $("ex-peers-btn");
  btn.disabled = true; btn.textContent = "拉取中…";
  try {
    const d = await (await fetch(`/api/exchange/peers?tids=${encodeURIComponent(tids.join(","))}`)).json();
    if (!d.ok) throw new Error(d.message || "拉取失败");
    exState.apiPeers = d.api_peers || [];
    exState.manualPeers = d.manual_peers || [];
    renderManualPeers();
    renderAutoPeers(exState.apiPeers);
    const auto = exState.apiPeers.length;
    const man = exState.manualPeers.length;
    $("ex-cnt").textContent = `自动卡友 ${auto} 人 + 手动 ${man} 人`;
    const nm = tids.length > 1
      ? `${tids.length} 套（${tids.map((i) => exState.themeNames[i] || "#" + i).join("、")}）`
      : `「${exState.themeNames[tids[0]] || "#" + tids[0]}」`;
    exMsg(`找到 ${auto} 个正在玩${nm}的卡友（多套已合并去重）${man ? `，加手动名单共 ${auto + man} 人` : ""}。点「扫描卡友换卡箱」开始扫描。`, "ok");
  } catch (e) {
    exMsg("拉取卡友失败：" + (e.message || e), "err");
  } finally {
    btn.disabled = false; btn.textContent = "找该套卡友";
  }
}

function renderManualPeers() {
  const wrap = $("ex-friends-wrap");
  wrap.classList.remove("hidden");
  wrap.style.width = "100%";
  // 若已有输入框，先取回当前值，避免重建丢输入
  const cur = $("ex-manual-ta");
  if (cur) exState.manualText = cur.value;
  const list = [...exState.manualPeers];
  // 编辑状态：简单文本框（每行一个 QQ）+ 保存按钮
  if (exState.manualText === null) exState.manualText = list.join("\n");
  wrap.innerHTML = `
    <div class="ex-mh" style="display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--muted);margin:2px 0 8px">
      <b style="color:var(--text)">手动卡友 QQ</b>
      <span>每行一个（可留空：仅用自动卡友）</span>
    </div>
    <div class="ex-mrow" style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start;width:100%">
      <textarea id="ex-manual-ta" rows="3"
        style="flex:1 1 320px;min-width:280px;border:1px solid var(--line);border-radius:8px;padding:8px 10px;
               font-size:13px;font-family:ui-monospace,Menlo,monospace;resize:vertical"
        placeholder="例如：&#10;10001&#10;20002"></textarea>
      <div style="display:flex;flex-direction:column;gap:6px;flex:0 0 auto">
        <button id="ex-manual-save" class="btn sm">保存名单</button>
        <button id="ex-manual-scan" class="btn sm ghost">仅扫手动</button>
      </div>
    </div>`;
  $("ex-manual-ta").value = exState.manualText;
  $("ex-manual-save").onclick = async () => {
    const lines = ($("ex-manual-ta").value || "").split("\n").map((s) => s.trim()).filter(Boolean);
    try {
      const d = await (await fetch("/api/exchange/manual", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ peers: lines }),
      })).json();
      if (d.ok) {
        exState.manualPeers = d.manual_peers || [];
        exState.manualText = exState.manualPeers.join("\n");
        exMsg(`手动卡友名单已保存：${exState.manualPeers.length} 人`, "ok");
        $("ex-cnt").textContent = `自动卡友 ${exState.apiPeers.length} 人 + 手动 ${exState.manualPeers.length} 人`;
      }
    } catch (e) {
      exMsg("保存失败：" + (e.message || e), "err");
    }
  };
  $("ex-manual-scan").onclick = () => doScan(true);
}

function peersForScan(manualOnly = false) {
  const lines = exState.manualText !== null && $("ex-manual-ta")
    ? ($("ex-manual-ta").value || "").split("\n").map((s) => s.trim()).filter(Boolean)
    : exState.manualPeers;
  const manual = lines.filter((s) => /^\d+$/.test(s));
  return { api: manualOnly ? [] : exState.apiPeers, manual };
}

async function doScan(manualOnly = false) {
  const tids = exTargetTids();
  if (!tids.length) { exMsg("请先在上方搜并选择想收的套卡（可多选）", "err"); return; }
  const { api, manual } = peersForScan(manualOnly);
  // 非仅扫手动：后端 use_api=true 会自动重新拉该套卡友，无需前端先存名单。
  // 仅扫手动：必须至少有一个手动 QQ。
  if (manualOnly && !manual.length) {
    exMsg("手动名单为空：先在下方填写卡友 QQ 并点「保存名单」", "err");
    return;
  }
  const peers = [...api, ...manual];
  const btn = $("ex-scan-btn");
  btn.disabled = true;
  btn.textContent = manualOnly ? "扫描中…" : `扫描中（自动拉取该套卡友）…`;
  const names = exState.themeCards
    .filter((c) => exState.pickCards.has(c.id)).map((c) => c.name);
  const pickMsg = names.length ? `（只找有指定卡的：${names.join("、")}）` : "";
  const multiMsg = tids.length > 1 ? `${tids.length} 套合并扫描` : "拉取该套在玩玩家";
  exMsg((manualOnly
    ? `开始扫描 ${manual.length} 个手动卡友的换卡箱，约需 ${Math.max(3, Math.round(manual.length / 6))} 秒…`
    : `开始扫描：${multiMsg}并逐个读取换卡箱（套装多了玩家池也会变大。服务器限流约 15 人/10 秒，请稍候…）`) + pickMsg, "info");
  const t0 = Date.now();
  if (scanTimer) clearInterval(scanTimer);
  scanTimer = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    $("ex-cnt").textContent = `扫描中…已 ${s} 秒（限流保护，勿重复点击）`;
  }, 1000);
  try {
    const d = await (await fetch("/api/exchange/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        theme_id: tids[0],
        // 多套目标：后端把这些套的卡友池合并，命中任一目标套的卡都列出
        theme_ids: tids,
        price: Number(exState.price) || 0,
        // 指定卡（可多张、可跨套）：只列出换卡箱里正放着这些卡的人；空数组 = 目标套全部卡
        card_ids: [...exState.pickCards],
        peers: manualOnly ? manual : [],
        use_api: !manualOnly,
        manual_peers: manual,
        concurrency: 5,
        stop_after: Number(($("ex-stop") || {}).value) || 0,
        // 好友池（默认开，可记忆）：并入 card_user_list 的好友名单（~194 人，含小号），
        // 覆盖「没在玩目标套、但换卡箱里恰好有目标卡」的人；与套卡池零重叠
        include_friends: !!(($("ex-all-players") || {}).checked),
      }),
    })).json();
    if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
    if (!d.ok) throw new Error(d.message || "扫描失败");
    exState.results = d;
    renderScan(d);
  } catch (e) {
    if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
    exMsg("扫描失败：" + (e.message || e), "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "扫描卡友换卡箱";
  }
}

function clearResults() {
  exState.results = null;
  $("ex-cnt").textContent = "";
  $("ex-empty").classList.remove("hidden");
  $("ex-reset").classList.add("hidden");
  const old = $("ex-result-list");
  if (old) old.remove();
}

function renderScan(d) {
  $("ex-empty").classList.add("hidden");
  $("ex-reset").classList.remove("hidden");
  const old = $("ex-result-list");
  if (old) old.remove();
  const list = document.createElement("div");
  list.id = "ex-result-list";
  list.style.display = "flex";
  list.style.flexDirection = "column";
  list.style.gap = "10px";
  $("ex-body").appendChild(list);

  const tNames = (d.target_names && d.target_names.length)
    ? d.target_names : [d.target_name].filter(Boolean);
  const targetText = tNames.length > 1 ? `${tNames.join("、")} 等 ${tNames.length} 套` : (tNames[0] || "");
  let html = "";
  const priceTxt = d.price ? `面值 ${d.price} 的` : "全部面值的";
  $("ex-cnt").textContent =
    `「${targetText}」${priceTxt}卡：扫 ${d.scanned}/${d.peers_total} 人，` +
    `${d.usable_users ?? d.hit_users} 人现在就能换` +
    (d.locked_users ? `，另有 ${d.locked_users} 人只有已锁的卡（共 ${d.locked_n} 张）` : "") +
    (d.stopped_early ? `，已凑够 ${d.usable_n} 个可换目标提前收工` : "") +
    (d.errors_n ? `，${d.errors_n} 人读取失败` : "") + `（${d.elapsed}s）`;

  if (d.errors_n) {
    html += `<div class="ex-msg warn">${d.errors_n} 个卡友主页读取失败（可能对方未开卡主页/网络抖动）。<br>${d.errors.slice(0, 8).map((e) => `QQ ${e.uin}：${esc(e.error)}`).join("<br>")}</div>`;
  }
  if (!d.rows || !d.rows.length) {
    html += `<div class="empty-tip" style="padding:30px 16px">${d.errors_n ? "" : "扫描完成，但"}没人在换卡箱放出「${esc(targetText)}」的${priceTxt}卡。<br>换个时间再扫（换卡箱里的卡会被换走/上锁），或补充手动卡友、多选几个目标套。${d.my_exch && d.my_exch[0] ? "" : ""}</div>`;
    list.innerHTML = html;
    return;
  }

  const myExch = (d.my_exch || []).filter((x) => x);
  const myExchTxt = myExch.length
    ? `我的换卡设置：想收 ${myExch.join(",")}`
    : "";
  if (myExchTxt) html += `<div class="ex-msg ok">${esc(myExchTxt)}（别人来我家时按此给我套卡）</div>`;
  html += `<div class="ex-msg info" style="background:#eef4fa;color:#2b6cb0">换卡规则（实测）：对方换卡箱里未上锁的卡才能换；我付出的卡面值必须相同，并且——<b>只有付出的卡属于「商店能买到的套卡」时，其主题才必须在对方「想收套」里</b>；下架/绝版套（如纸寿千年、名家）商店买不到，<b>不受此限，同面值即可换</b>。对方<b>未设置想收套 = 不限制</b>，任何同面值卡都能换。</div>`;

  list.innerHTML = html;

  d.rows.forEach((row) => {
    // 「有货但换不了」（命中的卡全被对方上锁）默认也列出、但标锁：
    // 此前后端直接把上锁卡过滤掉，界面上完全没有它们 —— 用户只能靠真点交换拿报错，
    // 才知道「这卡锁着 / 已经被换走了」（2026-09-18 反馈）。勾「只看可换」可隐藏。
    const usable = Number(row.usable ?? row.hits.filter((h) => (h.srcs || []).length).length);
    const hideLocked = !!($("ex-hide-locked") || {}).checked;
    if (hideLocked && !usable) return;
    list.appendChild(peerBlock(row, tNames));
  });

  // 勾了「只看可换」却一个人都没剩下：明确说清是被过滤掉的，别让用户以为扫描无结果
  if (!list.children.length) {
    const tip = document.createElement("div");
    tip.className = "empty-tip";
    tip.style.padding = "24px 16px";
    tip.textContent = `扫描到 ${d.rows.length} 位有货卡友，但他们的卡现在都锁着，没有能立刻换的。取消勾选「只看可换」可查看这些已锁的卡（带解锁时间）。`;
    list.appendChild(tip);
  }

  bindPeerEvents(list);
}

// ---------- 换卡结果渲染（左栏） ----------
// 布局见 index.html .ex-grid：结果在左栏、日志在右栏，各自独立滚动。
// 换卡结果**就地**显示在点过的那一行（.ex-res），不必再去日志里翻；
// 日志仍同步留档（exMsg），失败时还能一键「↻ 重扫此人」刷新对方换卡箱实时状态。
function fmtClock(tsSec) {
  return new Date(tsSec * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

function scanTargetNames(d) {
  const n = (d && d.target_names) || [];
  return n.length ? n : exTargetTids().map((t) => exState.themeNames[t] || `#${t}`);
}

/** 单张命中卡的 HTML。tNames = 目标套名数组（多套扫描时用来标注卡属于哪套）。 */
function exHitHtml(row, h, tNames) {
  // 能不能换，一律以后端算好的 h.srcs（我方可付出的卡）为准 —— 判定逻辑集中在
  // exchange._hit_from_box，前端不再自己用 want_theme_id 当门槛（那样会把
  // 「未设置想收套」和「下架套」误判成不可换）。
  const wantThemes = h.want_themes || [];
  const wantTxt = wantThemes.map((x) => x.name).join("、");
  // 锁：h.locked 由后端标注（锁定卡 srcs 必为空）；unlock 兜底兼容旧响应
  const lockedNow = !!h.locked || h.unlock > Math.floor(Date.now() / 1000);
  const hasSrc = !lockedNow && (h.srcs || []).length > 0;
  const srcOpts = hasSrc
    ? `<select data-friend="${row.uin}" data-dst="${h.id}_${h.slot}" data-src="">
         <option value="">选择付出的卡…</option>
         ${h.srcs.map((s) => `<option value="${s.id}_${s.slot}_1">${esc(s.name)}（面值 ${s.price}，${esc(s.theme_name)}）</option>`).join("")}
       </select>
       <button class="btn sm primary ex-swap" data-friend="${row.uin}"
               data-dst-id="${h.id}" data-dst-slot="${h.slot}">交换</button>`
    : "";
  const note = [];
  if (!wantThemes.length) note.push('<span class="tag" style="background:#e8f6ee;color:#1a7f4b">对方不限想收套 · 同面值即可换</span>');
  if (lockedNow) note.push(`<span class="tag off">🔒 已锁至 ${fmtClock(h.unlock)}</span>`);
  const nopay = lockedNow
    ? '<span class="ex-nopay" style="color:var(--warn)">锁定期内不可换：记下解锁时间再来，或点右上「↻ 重扫此人」刷新</span>'
    : (hasSrc ? ""
      : `<span class="ex-nopay">（我方可付卡里没有面值 ${h.price} 的${wantTxt ? `，或受对方想收「${esc(wantTxt)}」限制` : ""}）</span>`);
  // 多套扫描时标出该卡属于哪个目标套，否则同名/相近卡名看不出来源
  const themeTag = (tNames.length > 1 && h.theme_name)
    ? `<span class="tag base" title="所属套卡">${esc(h.theme_name)}</span>` : "";
  return `<div class="ex-hit" data-slot="${h.slot}" data-id="${h.id}">
    <span class="cnm">${esc(h.name)} <span class="v-badge" style="background:#eef4fa;color:#66738a">面值 ${h.price}</span>${themeTag}</span>
    <span class="pay" style="font-size:12px">${wantTxt ? `对方想收：${esc(wantTxt)}` : ""}${nopay}</span>
    ${note.join(" ")}
    ${srcOpts}
    <span class="ex-res"></span>
  </div>`;
}

/** 一个卡友的整块（卡友信息 + 命中卡列表 + 单人重扫）。 */
function peerBlock(row, tNames) {
  const usable = Number(row.usable ?? row.hits.filter((h) => (h.srcs || []).length).length);
  const lockedN = Number(row.locked_n ?? row.hits.filter((h) => h.locked).length);
  const peerEl = document.createElement("div");
  peerEl.className = "ex-peer";
  peerEl.dataset.uin = row.uin;
  peerEl.innerHTML = `<div class="ph">
      <span class="nick">${esc(row.nick)}</span>
      <span class="qq">QQ ${row.uin}</span>
      <span class="want">他想要的套：${(row.hits[0].want_themes || []).length ? esc(row.hits[0].want_themes.map((x) => x.name).join("、")) : "不限（任何套卡均可）"}</span>
      <span style="color:var(--muted);font-size:12px">
        ${usable} 张可换${lockedN ? ` · ${lockedN} 张已锁` : ""}
      </span>
      <button class="btn sm ghost rescan" data-uin="${row.uin}"
              title="只重扫这一位卡友（1 次请求，秒回）。换卡失败、或卡被别人换走后用它刷新实时状态，不必重扫全部卡友">↻ 重扫此人</button>
    </div>
    <div class="hits">${row.hits.map((h) => exHitHtml(row, h, tNames)).join("")}</div>`;
  return peerEl;
}

function bindPeerEvents(scope) {
  scope.querySelectorAll(".ex-swap").forEach((btn) => { btn.onclick = () => onSwapClick(btn); });
  scope.querySelectorAll(".ex-rescan").forEach((btn) => { btn.onclick = () => rescanPeer(btn); });
}

/** 交换：结果就地写进该行（成功/失败都在原地可见），日志同步留档。 */
async function onSwapClick(btn) {
  const hit = btn.closest(".ex-hit");
  const sel = hit.querySelector("select");
  const res = hit.querySelector(".ex-res");
  if (!sel || !sel.value) {
    if (res) { res.className = "ex-res err"; res.textContent = "请先选择付出的卡"; }
    exMsg("请先选择要付出的卡", "err");
    return;
  }
  const [srcId, srcSlot] = sel.value.split("_");
  if (!confirm(`确认用这张卡交换？\n付出：${sel.options[sel.selectedIndex].text}\n换入：${hit.querySelector(".cnm").textContent.trim()}\n\n交换会真实消耗付出卡。`)) return;
  const dstId = Number(btn.dataset.dstId), dstSlot = Number(btn.dataset.dstSlot);
  btn.disabled = true; sel.disabled = true;
  if (res) { res.className = "ex-res"; res.textContent = "交换中…"; }
  const d = await doSwap(btn.dataset.friend, dstId, dstSlot, Number(srcId), Number(srcSlot));
  if (d && d.code === 0) {
    if (res) { res.className = "ex-res ok"; res.textContent = "✅ 已换入"; }
    hit.classList.add("dead");
    // 这一格已经被换掉，不能再点（想再换同一张请重扫该卡友）
    if (sel) sel.remove();
    btn.remove();
  } else {
    const msg = (d && (d.message || `code=${d.code}`)) || "请求异常";
    if (res) { res.className = "ex-res err"; res.textContent = `❌ ${msg}`; }
    btn.disabled = false; sel.disabled = false;
  }
}

/** 只重扫一位卡友（1 次请求），用于交换失败/卡被换走后刷新实时状态。 */
async function rescanPeer(btn) {
  const peerEl = btn.closest(".ex-peer");
  const uin = btn.dataset.uin || (peerEl && peerEl.dataset.uin);
  if (!peerEl || !uin) return;
  const tids = exTargetTids();
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = "重扫中…";
  try {
    const d = await (await fetch("/api/exchange/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        theme_id: tids[0] || 0, theme_ids: tids,
        price: Number(exState.price) || 0,
        card_ids: [...exState.pickCards],
        peers: [uin], use_api: false, include_friends: false, concurrency: 1,
      }),
    })).json();
    const row = d && d.ok && (d.rows || [])[0];
    if (row) {
      const el = peerBlock(row, scanTargetNames(d));
      peerEl.replaceWith(el);
      bindPeerEvents(el);
      exMsg(`已重扫 QQ ${uin}：${row.usable || 0} 张可换${row.locked_n ? `，${row.locked_n} 张已锁` : ""}。`, "ok");
    } else {
      peerEl.classList.add("dead");
      exMsg(`重扫 QQ ${uin}：换卡箱里已无目标卡（被别人换走 / 已上锁 / 已撤下）。`, "warn");
      btn.disabled = false; btn.textContent = old;
    }
  } catch (e) {
    exMsg("重扫失败：" + (e.message || e), "err");
    btn.disabled = false; btn.textContent = old;
  }
}

async function doSwap(friend, dstId, dstSlot, srcId, srcSlot) {
  exMsg(`正在与 QQ ${friend} 交换…`, "info");
  try {
    const d = await (await fetch("/api/exchange/do", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ friend, dst_slot: dstSlot, dst_id: dstId,
                             src_slot: srcSlot, src_id: srcId, src_type: 1 }),
    })).json();
    if (d.code === 0) {
      exMsg(`✅ 交换成功！换入 ${dstId}`, "ok");
      log(`换卡成功：与 ${friend} 交换卡 ${dstId}`, "ok");
    } else {
      exMsg(`交换失败：${d.message || ("code=" + d.code)}`, "err");
      log(`换卡失败（${friend}，dst=${dstId}）：${d.message || d.code}`, "err");
    }
    return d;
  } catch (e) {
    exMsg("交换请求异常：" + (e.message || e), "err");
    return null;
  }
}

// ============================================================
// 卡库查询
// ============================================================
let priceChipsLoaded = false;

async function initLibOnce() {
  if (priceChipsLoaded) return;
  priceChipsLoaded = true;
  try {
    const d = await (await fetch("/api/cards/prices")).json();
    if (d.ok && d.prices && d.prices.length) {
      const box = $("lib-pricechips");
      box.innerHTML = d.prices.map((p) =>
        `<span class="chip" data-price="${p.price}" title="共 ${p.n} 张">面值 ${p.price}</span>`).join("");
      box.querySelectorAll(".chip").forEach((c) => {
        c.onclick = () => {
          box.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
          c.classList.add("active");
          $("lib-price").value = c.dataset.price;
          runCardQuery();
        };
      });
    }
  } catch { /* 非关键 */ }
  // 模式切换默认动作
  setLibMode("price");
  runCardQuery();
}

function setLibMode(mode) {
  libState.mode = mode;
  document.querySelectorAll("#lib-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === mode));
  const isPrice = mode === "price";
  $("lib-price").classList.toggle("hidden", !isPrice);
  $("lib-pricechips").classList.toggle("hidden", !isPrice);
  $("lib-own-wrap").classList.toggle("hidden", !isPrice);
  $("lib-sep1").classList.toggle("hidden", !isPrice);
  $("lib-themefilter").classList.toggle("hidden", isPrice);
  $("lib-modfilter").classList.toggle("hidden", isPrice);
  $("lst-title").textContent = isPrice ? "面值查卡" : "浏览套卡";
  $("lib-detail").innerHTML = '<div class="empty-tip">在左侧点一套卡，这里显示每张卡的面值与合成路径。</div>';
  $("dt-title").textContent = "套卡明细";
  $("dt-sub").textContent = "";
  if (isPrice) runCardQuery();
  else runThemeQuery();
}
document.querySelectorAll("#lib-mode button").forEach((b) => {
  b.onclick = () => setLibMode(b.dataset.mode);
});
$("lib-go").onclick = () => {
  if (libState.mode === "price") runCardQuery();
  else runThemeQuery();
};
$("lib-kw").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("lib-go").click();
});
$("lib-price").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("lib-go").click();
});
$("lib-own").onchange = (e) => {
  libState.ownOnly = e.target.checked;
  runCardQuery();
};
document.querySelectorAll("#lib-modfilter .chip").forEach((c) => {
  c.onclick = () => {
    document.querySelectorAll("#lib-modfilter .chip").forEach((x) => x.classList.remove("active"));
    c.classList.add("active");
    libState.module = c.dataset.m;
    runThemeQuery();
  };
});
document.querySelectorAll("#lib-themefilter .chip").forEach((c) => {
  c.onclick = () => {
    document.querySelectorAll("#lib-themefilter .chip").forEach((x) => x.classList.remove("active"));
    c.classList.add("active");
    libState.star = Number(c.dataset.s);
    runThemeQuery();
  };
});

async function runCardQuery() {
  const price = ($("lib-price").value || "").trim();
  const kw = ($("lib-kw").value || "").trim();
  if (!price && !kw) {
    $("lib-list").innerHTML = '<div class="empty-tip">输入面值（如 40）或卡名关键词后点「查」。</div>';
    $("lst-cnt").textContent = ""; $("lst-foot").textContent = "";
    return;
  }
  libState.price = price; libState.kw = kw;
  $("lib-list").innerHTML = '<div class="empty-tip"><span class="spinner"></span>查询中…</div>';
  try {
    const params = new URLSearchParams();
    if (price) params.set("price", price);
    if (kw) params.set("kw", kw);
    params.set("owned_only", libState.ownOnly ? 1 : 0);
    params.set("limit", 400);
    const d = await (await fetch(`/api/cards/q?${params}`)).json();
    if (!d.ok) throw new Error(d.message || "查询失败");
    $("lst-cnt").textContent = d.limited
      ? `命中 ${d.total}，仅列前 ${d.shown}`
      : `命中 ${d.total} 张`;
    $("lst-foot").textContent = "排序：持有数 → 上架新套；点行看全套明细与合成路径。";
    if (!d.cards.length) {
      $("lib-list").innerHTML = '<div class="empty-tip">没有匹配的卡。<br>换一个面值，或去掉「只看我持有的」再试。</div>';
      return;
    }
    $("lib-list").innerHTML = d.cards.map((c) => {
      const tags = [
        c.refinable ? '<span class="tag refinable">可炼</span>' : '<span class="tag base">素材/不可炼</span>',
        kindTag(c.kind),
        attrTags(c),
      ].join("");
      const rec = c.recipe
        ? `<div class="sub"><span class="tag refinable" style="margin-right:4px">配方</span>${esc(c.recipe.mats.join(" + "))}　耗时 ${fmtDur(c.recipe.secs)}</div>`
        : (c.own ? "" : '<div class="sub">基础素材卡（无合成配方，只能抽卡/获得）</div>');
      return `<div class="it" data-tid="${c.theme_id}" data-cid="${c.id}">
        <div class="r1"><span class="nm">${esc(c.name)}</span>
          ${tags}
          <span class="own-badge">${c.own ? `持 ${c.own}` : "未持有"}</span>
          <span class="meta">${esc(c.theme_name)} ${stars(c.star)}</span>
          <span class="v-badge">面值 ${c.price}</span>
          ${(c.price == 10 && c.theme_buyable)
            ? `<button class="mini-btn buy-btn" data-act="buy" data-cid="${c.id}" data-tid="${c.theme_id}">买</button>`
            : ""}</div>
        ${rec}</div>`;
    }).join("");
    $("lib-list").querySelectorAll(".it").forEach((el) => {
      el.onclick = () => openTheme(el.dataset.tid, el.dataset.cid);
      el.querySelectorAll('.mini-btn[data-act="buy"]').forEach((b) => {
        b.onclick = (ev) => {
          ev.stopPropagation();
          doBuy(Number(b.dataset.cid), Number(b.dataset.tid), b);
        };
      });
    });
  } catch (e) {
    $("lib-list").innerHTML = `<div class="empty-tip err">查询失败：${esc(e.message || e)}</div>`;
    log("卡库查询失败：" + (e.message || e), "err");
  }
}

async function runThemeQuery() {
  const kw = ($("lib-kw").value || "").trim();
  $("lib-list").innerHTML = '<div class="empty-tip"><span class="spinner"></span>加载套卡…</div>';
  try {
    const params = new URLSearchParams();
    if (kw) params.set("q", kw);
    if (libState.star) params.set("star", libState.star);
    if (libState.module) params.set("module", libState.module);
    params.set("limit", 2000);
    const d = await (await fetch(`/api/cards/themes?${params}`)).json();
    const isMain = libState.module === "main";
    $("lst-title").textContent = "浏览套卡";
    $("lst-cnt").textContent = `${d.shown} / ${d.total} 套`;
    $("lst-foot").textContent = isMain
      ? "按 发行/下架/闪卡 分组，组内按新上架排序；点套卡看明细与合成路径。"
      : "独立卡册（不在主卡箱显示），按新上架排序；点套卡看明细与合成路径。";
    if (!d.themes.length) {
      $("lib-list").innerHTML = '<div class="empty-tip">没有匹配的套卡，换个关键词/星级。</div>';
      return;
    }
    // 按 kind 分组：主卡箱(发行→下架→闪卡)在前，独立模块(挖地/猫咪旅行/猫咪探险)单独列表
    const groups = isMain
      ? [
        { key: "在售", label: "发行", cls: "on" },
        { key: "绝版", label: "下架", cls: "off" },
        { key: "闪卡", label: "闪卡", cls: "flash" },
      ]
      : [{ key: libState.module, label: libState.module, cls: "mod" }];
    let html = "";
    for (const g of groups) {
      const items = d.themes.filter((t) => t.kind === g.key);
      if (!items.length) continue;
      html += `<div class="lib-group"><div class="lib-group-title">` +
        `<span class="tag ${g.cls}">${g.label}</span>` +
        `<span class="meta">${items.length} 套</span></div>`;
      html += items.map((t) => `<div class="it" data-tid="${t.tid}">
        <div class="r1"><span class="nm">${esc(t.name)}</span> ${stars(t.star)}
          ${kindTag(t.kind)}${attrTags(t)}<span class="meta">${t.n} 张 · 面值 ${t.pmin}~${t.pmax}</span></div>
        <div class="sub">主题 #${t.tid}</div></div>`).join("");
      html += `</div>`;
    }
    $("lib-list").innerHTML = html;
    $("lib-list").querySelectorAll(".it").forEach((el) => {
      el.onclick = () => openTheme(el.dataset.tid);
    });
  } catch (e) {
    $("lib-list").innerHTML = `<div class="empty-tip err">加载失败：${esc(e.message || e)}</div>`;
  }
}

async function openTheme(tid, focusCid = null) {
  libState.curThemeTid = Number(tid);
  document.querySelectorAll("#lib-list .it").forEach((el) =>
    el.classList.toggle("sel", Number(el.dataset.tid) === Number(tid)));
  const detail = $("lib-detail");
  detail.innerHTML = '<div class="empty-tip"><span class="spinner"></span>加载套卡明细…</div>';
  try {
    const d = await (await fetch(`/api/cards/theme/${tid}`)).json();
    if (!d.ok) throw new Error(d.message || "加载失败");
    const t = d.theme;
    $("dt-title").textContent = `${t.name}`;
    $("dt-sub").textContent = `主题 #${t.tid} · ${t.n} 张`;
    detail.innerHTML =
      `<div class="card-row"><div class="head">${esc(t.name)}　${stars(t.star)}
         ${kindTag(t.kind)}${attrTags(t)}
         <span class="meta" style="margin-left:auto;color:var(--muted);font-size:12px">共 ${t.n} 张卡 · 面值区间以卡为准</span></div>
       </div>` +
      d.cards.map((c) => {
        const rec = c.recipe
          ? `<div class="recipe">炼成 ${esc(c.name)} ＝ ${c.recipe.mats.map((m) => `${esc(m.name)}<span class="v-badge" style="background:#eef4fa;color:#66738a">${m.price}</span>`).join(" + ")}　<span class="eq">耗时 ${fmtDur(c.recipe.secs)}</span></div>`
          : '<div class="recipe">基础素材卡 · 无配方（靠抽卡/活动获得，用于合成上面的卡）</div>';
        const used = c.used_by.length
          ? `<div class="recipe">↳ 被用作素材去炼：${c.used_by.map((u) => `${esc(u.name)}<span class="v-badge" style="background:#eef4fa;color:#66738a">${u.price}</span>`).join("、")}</div>`
          : "";
        return `<div class="card-row" data-cid="${c.id}">
          <div class="head">
            <span class="nm" style="font-weight:600">${esc(c.name)}</span>
            <span class="v-badge">面值 ${c.price}</span>
            ${c.own ? `<span class="own-badge">持 ${c.own}</span>` : ""}
            ${c.recipe ? '<span class="tag refinable">可炼</span>' : '<span class="tag base">素材</span>'}
            <span style="margin-left:auto;display:flex;gap:6px;align-items:center">
              ${(c.price == 10 && d.theme_buyable)
                ? `<button class="mini-btn buy-btn" data-act="buy">买</button>`
                : ""}
              <button class="mini-btn ${libState.openChains.has(c.id) ? "chain-on" : ""}" data-act="chain">${libState.openChains.has(c.id) ? "收起路径" : "合成路径"}</button>
            </span>
          </div>
          ${rec}${used}
          <div class="chain-wrap"></div>
        </div>`;
      }).join("");

    detail.querySelectorAll(".mini-btn").forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const row = btn.closest(".card-row");
        const cid = Number(row.dataset.cid);
        if (btn.dataset.act === "buy") {
          doBuy(cid, t.tid, btn);
          return;
        }
        toggleChain(row, cid, btn);
      };
    });
    if (focusCid) {
      const targetRow = detail.querySelector(`.card-row[data-cid="${focusCid}"]`);
      if (targetRow) {
        targetRow.scrollIntoView({ block: "center", behavior: "smooth" });
        const b = targetRow.querySelector('.mini-btn[data-act="chain"]');
        if (b && !libState.openChains.has(Number(focusCid))) b.click();
      }
    }
  } catch (e) {
    detail.innerHTML = `<div class="empty-tip err">加载失败：${esc(e.message || e)}</div>`;
    log("加载套卡明细失败：" + (e.message || e), "err");
  }
}

async function toggleChain(row, cid, btn) {
  const wrap = row.querySelector(".chain-wrap");
  if (libState.openChains.has(cid)) {
    libState.openChains.delete(cid);
    btn.textContent = "合成路径";
    btn.classList.remove("chain-on");
    wrap.innerHTML = "";
    return;
  }
  btn.textContent = "…";
  try {
    const d = await (await fetch(`/api/cards/card/${cid}`)).json();
    if (!d.ok) throw new Error(d.message || "加载失败");
    libState.openChains.add(cid);
    btn.textContent = "收起路径";
    btn.classList.add("chain-on");
    wrap.innerHTML = renderChain(d.chain);
  } catch (e) {
    btn.textContent = "合成路径";
    wrap.innerHTML = `<div class="err" style="font-size:12px">加载失败：${esc(e.message || e)}</div>`;
  }
}

function renderChain(node) {
  const lines = [];
  const walk = (n, d) => {
    if (!n || !n.recipe) return;
    for (const r of n.recipe) {
      const m = r.mat;
      lines.push(`<div class="lv" style="padding-left:${d * 16}px"><span class="arrow">←</span> <b>${esc(m.name)}</b> <span class="v-badge" style="background:#eef4fa;color:#66738a">面值 ${m.price}</span> ${m.recipe ? "" : '<span class="tag base">基础素材</span>'}</div>`);
      walk(m, d + 1);
    }
  };
  walk(node, 0);
  return lines.length ? `<div class="chain">${lines.join("")}</div>` : "";
}

// ---------- 自动炼卡页 ----------
let autoInitDone = false;
let autoTimer = null;

function initAutoOnce() {
  if (autoInitDone) return;
  autoInitDone = true;
  $("auto-start").onclick = autoStart;
  $("auto-stop").onclick = autoStop;
  $("auto-req-refresh").onclick = autoRefresh;
  $("auto-theme").onchange = () => { $("auto-tid").value = ""; autoRefresh(); };
  $("auto-tid").oninput = () => { $("auto-theme").value = ""; };
  if ($("auto-queue-add")) $("auto-queue-add").onclick = autoQueueAdd;
  if ($("auto-queue-tid")) $("auto-queue-tid").onkeydown = (e) => {
    if (e.key === "Enter") autoQueueAdd();
  };
  loadAutoOptions();
  autoRefresh();
}

// 运行中往队列追加一套卡：长炉时（如魏书 10h）炉位不闲着，可以同时推进下一套
async function autoQueueAdd() {
  const tid = ($("auto-queue-tid").value || "").trim();
  if (!tid) { log("追加队列：请填套卡 ID", "err"); return; }
  try {
    const d = await (await fetch(`/api/auto/queue/add?tid=${encodeURIComponent(tid)}`, { method: "POST" })).json();
    if (d.ok) {
      log(`已加入队列：${d.plan.name}（队列 ${(d.queue || []).length} 套）`, "ok");
      $("auto-queue-tid").value = "";
    } else {
      log(`加入队列失败：${d.message || ""}`, "err");
    }
  } catch (e) { log("加入队列异常：" + e, "err"); }
  autoRefresh();
}

async function loadAutoOptions() {
  const sel = $("auto-theme");
  try {
    const r = await (await fetch("/api/auto/options?profit_only=1")).json();
    const items = (r.items || []).sort((a, b) => b.rate - a.rate);
    sel.innerHTML = items.length ? items.map((it) =>
      `<option value="${it.tid}">${esc(it.name)}（${it.star}★ · 净赚${it.profit}/轮 · ${it.rate}/时）</option>`).join("")
      : `<option value="">无正收益可刷套</option>`;
    log(`可自动炼卡的发行套已加载：${items.length} 套（净赚>0）`, "ok");
  } catch (e) {
    sel.innerHTML = `<option value="">加载失败</option>`;
    log("加载可刷套卡失败：" + e, "err");
  }
}

async function autoStart() {
  const tid = $("auto-theme").value || $("auto-tid").value.trim();
  if (!tid) { log("自动炼卡：请先选择一套卡（下拉或输入套卡ID）", "err"); return; }
  const buy = $("auto-buy").checked ? 1 : 0;
  const rounds = parseInt($("auto-rounds").value, 10) || 0;
  // 偷炉（借好友空炉多并行一炉）：开关 + 常去好友 QQ。留空则后端从好友列表最后一名往回找空炉。
  const steal = $("auto-steal").checked ? 1 : 0;
  const sf = ($("auto-steal-friend").value || "").trim();
  $("auto-start").disabled = true;
  try {
    const qs = `tid=${encodeURIComponent(tid)}&buy=${buy}&rounds=${rounds}&steal=${steal}`
      + (sf ? `&steal_friend=${encodeURIComponent(sf)}` : "");
    const d = await (await fetch(`/api/auto/start?${qs}`, { method: "POST" })).json();
    if (d.ok) {
      log(`自动炼卡启动：${d.plan.name}（${d.plan.base_cards} 张底卡 / 集齐 1 轮需 ${d.plan.need_slots} 格）`, "ok");
      startAutoPoll();
    } else {
      log(`自动炼卡未启动：${d.message || ""}`, "err");
    }
  } catch (e) { log("自动炼卡启动异常：" + e, "err"); }
  $("auto-start").disabled = false;
  autoRefresh();
}

async function autoStop() {
  try {
    const d = await (await fetch("/api/auto/stop", { method: "POST" })).json();
    log("已请求停止自动炼卡", "warn");
  } catch (e) { log("停止异常：" + e, "err"); }
  clearAutoPoll();
  autoRefresh();
}

function startAutoPoll() {
  clearAutoPoll();
  autoTimer = setInterval(autoRefresh, 3000);
}
function clearAutoPoll() {
  if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
}

async function autoRefresh() {
  let st = null;
  try {
    const d = await (await fetch("/api/auto/status")).json();
    if (d && d.theme) st = d;         // 启动过（运行中或已停）：带计划与统计
    else if (d && d.running === false) st = null;
  } catch (e) { log("读取自动炼卡状态异常：" + e, "err"); }
  let bx = null;
  try {
    const d = await (await fetch("/api/state")).json();
    if (d.ok) {
      const cb = d.changebox || {}, sb = d.storebox || {};
      bx = {
        changeFree: Math.max(0, (cb.cur || 0) - (cb.filled || 0)),
        changeCur: cb.cur || 0, changeMax: cb.max || 0, changeUsed: cb.filled || 0,
        storeFree: Math.max(0, (sb.cur || 0) - (sb.filled || 0)),
        storeCur: sb.cur || 0, storeMax: sb.max || 0, storeUsed: sb.filled || 0,
      };
    }
  } catch { /* 空位展示失败不致命 */ }

  const running = !!(st && st.running);
  $("auto-start").classList.toggle("hidden", running);
  $("auto-stop").classList.toggle("hidden", !running);
  if (running && !autoTimer) startAutoPoll();
  if (!running) clearAutoPoll();
  renderAuto(st, bx);
}

function renderAuto(st, bx) {
  const body = $("auto-body");
  if (!body) return;
  const running = !!(st && st.running);
  const p = st ? st.theme : null;                 // {tid,name,giftid,ok,cards,recipes,base_cards,need_slots}
  const sts = st ? st.stats || {} : {};
  const head = [];
  // 运行徽标 + 计划
  const stat = (k, v, small = "") =>
    `<div class="stat-chip"><span class="k">${k}</span><span class="v">${v}${small ? `<small>${small}</small>` : ""}</span></div>`;
  if (p) {
    head.push(`<span class="run-head" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span class="dot ${running ? "run" : "idle"}"></span><b>${esc(p.name)}</b>${stars(p.star ?? 0)}
      <span class="tag ${p.ok ? "comp" : "nocomp"}">${p.ok ? "可买底卡刷" : "底卡不全可买"}</span>
      <span style="color:var(--muted);font-size:12px">${running ? "运行中（每45秒一个周期）" : "已停止"}</span></span>`);
    head.push(`<div class="state-box">` +
      stat("套内卡", p.cards, "张") +
      stat("配方执行", p.recipes, "次/轮") +
      stat("底卡需求", p.base_cards, "张·随炼随买") +
      stat("收尾占格", p.need_slots, "格") +
      stat("已集齐轮次", sts.collected ?? 0, "轮") +
      stat("炼卡", sts.refined ?? 0, "次") +
      stat("买卡", sts.bought ?? 0, "张") +
      stat("随机抽卡", st.randchance ?? 0, "次") +
      stat("错误", sts.errors ?? 0, sts.errors ? "⚠" : "") +
      `</div>`);
    // 队列（多套并行）：每套各一行，空炉位按轮转分给各套能立刻提交的炉次
    const themes = st.themes || [];
    if (themes.length) {
      const rows = themes.map((t, i) => {
        const done = (t.recipes === 0 && t.base_cards === 0);
        const act = (st.queue || [])[0] === t.tid;
        return `<tr class="${act ? "q-act" : ""}">
          <td class="q-i">${i + 1}</td>
          <td>${esc(t.name)}${stars(t.star ?? 0)}</td>
          <td class="num">${t.cards}</td>
          <td class="num">${t.recipes}</td>
          <td class="num">${t.base_cards}</td>
          <td class="num">${t.rounds ?? 0}</td>
          <td>${done ? `<span class="tag comp">已凑齐待上交</span>` : (t.ok ? "" : `<span class="tag nocomp">底卡非全10面值</span>`)}</td>
        </tr>`;
      }).join("");
      head.push(`<div class="q-wrap"><div class="q-head">套卡队列（${themes.length} 套并行 · 空炉位轮转分配）+  <span style="color:var(--muted);font-size:12px">续炼其他套：在右上「追加队列」填套卡ID</span></div>
        <table class="q-table"><thead><tr><th>#</th><th>套卡</th><th>套内卡</th><th>还需炼</th><th>还需买底卡</th><th>已集齐</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div>`);
    }
  } else {
    head.push(`<span class="dot idle"></span> 未启动（选择套卡后点「启动」，或手动输入套卡ID）`);
  }
  // 空位
  if (bx) {
    const totFree = bx.changeFree + bx.storeFree;
    const locked = Math.max(0, bx.changeMax - bx.changeCur) + Math.max(0, bx.storeMax - bx.storeCur);
    const need = p ? p.need_slots : null;
    const enough = need == null || totFree >= need;
    head.push(`<div class="state-box" style="margin-top:10px">` +
      stat("换卡箱", `${bx.changeUsed} / ${bx.changeCur}`, `剩 ${bx.changeFree}`) +
      stat("保险箱", `${bx.storeUsed} / ${bx.storeCur}`, `剩 ${bx.storeFree}`) +
      stat("可用空位合计", totFree, locked ? `未开通 ${locked} 格` : "") +
      (need ? stat("本轮峰值需求", need, enough ? "空位够" : "空位不足！") : "") +
      `</div>`);
    if (need && !enough) {
      head.push(`<div class="ex-msg err" style="margin-top:8px">卡箱空位不够启动：可用 ${totFree} 格 < 需 ${need} 格。先整理卡箱（集齐上交 / 腾位），或输入需要更少底卡的套卡。</div>`);
    }
  }
  // 偷炉状态：独立于主炉的并行位（2026-09-18 起主炉满也会尝试偷炉）
  const sl = (st && st.steal) || null;
  if (sl) {
    const last = sl.last_steal;
    const cd = Math.max(0, Math.round((sl.cooldown_until || 0) - Date.now() / 1000));
    const lastTxt = last
      ? `${esc(last.name || "")}<small>@${esc(String(last.friend || ""))} ${esc(last.msg || "")}</small>`
      : "还没偷过";
    head.push(`<div class="state-box" style="margin-top:10px">` +
      stat("偷炉", sl.enabled ? "开启" : "关闭",
           sl.preferred ? `常去 ${esc(sl.preferred)}` : "自动找空炉") +
      stat("偷炉冷却", cd > 0 ? `${cd}s` : "无", cd > 0 ? "失败后短暂停" : "") +
      stat("上次偷炉", lastTxt) +
      (sl.blocked ? stat("本轮未偷", esc(sl.blocked), "为什么没偷就看这里") : "") +
      `</div>`);
    if (last && last.code !== 0) {
      head.push(`<div class="ex-msg err" style="margin-top:8px">上次偷炉失败：${esc(last.msg || "")}`
        + `（错误码 ${esc(String(last.code))}）`
        + (last.code === -33031
            ? " —— 「同时只能偷一个炉子」是全局限制（好友炉上已挂着我的一炉），取回后自动恢复，不是这家好友不能用。"
            : "") + `</div>`);
    }
  }
  // 最近日志
  let logsHtml = "";
  if (st && (st.log || []).length) {
    logsHtml = `<div class="auto-log">` + st.log.map((l) => {
      const t = new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
      const cls = l.ok ? "aok" : (l.action === "异常" || l.action === "完成" ? "aerr" : "aok");
      const okTxt = l.ok ? "✓" : "✗";
      return `<span class="at">[${t}]</span> ${okTxt} ${esc(l.action)}：${esc(String(l.detail))}`;
    }).reverse().join("\n") + `</div>`;
  }
  body.innerHTML = head.join("") + logsHtml;
}

// ---------- 卡箱页（换卡箱 / 保险箱：查看、卖卡、互移） ----------
let cardboxInitDone = false;
let cbState = { change: null, store: null };

function initCardBoxOnce() {
  if (cardboxInitDone) return;
  cardboxInitDone = true;
  $("cb-refresh").onclick = () => loadCardBox(true);
  // 点卡箱里任意一张卡 -> 弹窗显示所属套卡的合成路径；卖/移按钮区域不触发
  document.getElementById("pane-cardbox").addEventListener("click", (e) => {
    if (e.target.closest(".cbacts")) return;
    const card = e.target.closest(".cb-card");
    if (!card) return;
    const cid = Number(card.dataset.cid);
    if (cid) openSynth(cid);
  });
  loadCardBox();
}

async function loadCardBox(withLog = false) {
  const hint = $("cb-hint");
  if (hint) hint.textContent = "读取中…";
  try {
    const d = await (await fetch("/api/state")).json();
    if (!d.ok) {
      if (hint) hint.textContent = `读取失败：${d.message || d.code || ""}`;
      log(`读取卡箱失败：${d.message || d.code || ""}`, "err");
      return;
    }
    cbState.change = d.changebox || null;
    cbState.store = d.storebox || null;
    renderCardBox();
    if (withLog) log("卡箱已刷新", "ok");
  } catch (e) {
    if (hint) hint.textContent = "请求异常：" + e;
    log("读取卡箱异常：" + e, "err");
  }
}

function themeHue(t) {
  let h = 0;
  for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0;
  return h % 360;
}

function cbCardHtml(c, boxType) {
  // 保险箱（boxType=1）的卡不能直接卖，必须先移回换卡箱，所以不给「卖」按钮。
  const inStore = boxType === 1;
  const toName = inStore ? "移回换卡箱" : "存入保险箱";
  const sellBtn = inStore
    ? ""
    : `<button class="btn sm ghost" data-act="sell" data-box="${boxType}" data-slot="${c.slot}" data-id="${c.id}" data-price="${c.price ?? 0}">卖 ${c.price ?? 0}</button>`;
  const theme = c.theme || "";
  const style = theme
    ? ` style="border-left:4px solid hsl(${themeHue(theme)},55%,55%);background:hsl(${themeHue(theme)},72%,97%)"`
    : "";
  return `<div class="cb-card" data-cid="${c.id}"${style}>
    <div class="cbn" title="${esc(c.name || "")}">${esc(c.name || ("卡#" + c.id))}</div>
    <div class="cbm"><span>面值 ${c.price ?? 0}</span>${theme ? `<span>${esc(theme)}</span>` : ""}${c.star ? `<span>${c.star}★</span>` : ""}</div>
    <div class="cb-route">点击查看合成路径 ▸</div>
    <div class="cbacts">
      ${sellBtn}
      <button class="btn sm ghost" data-act="move" data-box="${boxType}" data-slot="${c.slot}" data-id="${c.id}">${toName}</button>
    </div>
  </div>`;
}

function cbSortCards(cards) {
  // 同套卡聚在一起（按套名），套内按面额从大到小；不加分标题，不增加滚动长度
  return [...(cards || [])].sort((a, b) =>
    (a.theme || "").localeCompare(b.theme || "", "zh")
    || (b.price || 0) - (a.price || 0)
    || (a.name || "").localeCompare(b.name || "", "zh"));
}

function renderCardBox() {
  const ch = cbState.change || {}, st = cbState.store || {};
  $("cb-change-cap").textContent = `${ch.filled ?? 0} / ${ch.cur ?? 0} 格` +
    (ch.max > ch.cur ? `（未开通 ${ch.max - ch.cur}）` : "");
  $("cb-store-cap").textContent = `${st.filled ?? 0} / ${st.cur ?? 0} 格` +
    (st.max > st.cur ? `（未开通 ${st.max - st.cur}）` : "");
  $("cb-change").innerHTML = (ch.cards || []).length
    ? cbSortCards(ch.cards).map((c) => cbCardHtml(c, 0)).join("")
    : `<div class="cb-empty">换卡箱没有卡</div>`;
  $("cb-store").innerHTML = (st.cards || []).length
    ? cbSortCards(st.cards).map((c) => cbCardHtml(c, 1)).join("")
    : `<div class="cb-empty">保险箱没有卡</div>`;
  $("cb-hint").textContent = `换卡箱 ${ch.filled ?? 0} 张 / 保险箱 ${st.filled ?? 0} 张`
    + (st.filled ? `　│　保险箱的卡不能直接卖，需先「移回换卡箱」` : "");
  document.querySelectorAll("#pane-cardbox .cbacts button").forEach((b) => b.onclick = onCbAction);
}

async function onCbAction(e) {
  const b = e.currentTarget;
  const act = b.dataset.act;
  const box = Number(b.dataset.box);
  const slot = Number(b.dataset.slot);
  const id = Number(b.dataset.id);
  b.disabled = true;
  try {
    if (act === "sell") {
      const price = Number(b.dataset.price || 0);
      if (!confirm(`确定卖掉「${b.closest(".cb-card").querySelector(".cbn").textContent}」？可得 ${price} 金。`)) {
        b.disabled = false; return;
      }
      const bt = box === 0 ? 1 : 2;  // 1=换卡箱 / 2=保险箱
      const d = await (await fetch(`/api/box/sell?card_id=${id}&slot=${slot}&box_type=${bt}`, { method: "POST" })).json();
      log(`卖卡 slot ${slot}（${box === 0 ? "换卡箱" : "保险箱"}）：${d.ok ? `成功 +${price} 金` : "失败 " + (d.message || d.code)}`,
          d.ok ? "ok" : "err");
    } else {
      const toName = box === 0 ? "保险箱" : "换卡箱";
      // 移卡是双向可逆操作，不需要二次确认，直接移
      const d = await (await fetch(`/api/box/move?card_id=${id}&slot=${slot}&from_box=${box}`, { method: "POST" })).json();
      log(`移卡 slot ${slot} → ${toName}：${d.ok ? d.message : "失败 " + (d.message || d.code)}`, d.ok ? "ok" : "err");
    }
    await loadCardBox();
  } catch (err) {
    log("卡箱操作异常：" + err, "err");
    b.disabled = false;
  }
}

// ---------- 合成路径弹窗（点卡箱任意卡 -> 显示所属套卡的合成路径 + 一键炼卡）----------
function closeSynth() {
  $("synth-modal").classList.add("hidden");
  $("synth-body").innerHTML = "";
}

function fmtSynSecs(s) {
  s = Number(s) || 0;
  if (s < 60) return s + "秒";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h}小时${m ? m + "分" : ""}`;
  return `${m}分`;
}

function synMatHtml(m, tid) {
  let h = `<div class="syn-mat${m.owned ? " has" : " miss"}">
    <span class="dot"></span>${esc(m.name)} <span class="mp">${m.price || 0}面额</span>
    <span class="cnt">${m.owned ? "×" + m.count : "缺"}</span>`;
  if (m.buyable) {
    h += `<button class="mini-btn buy-btn syn-buy" data-cid="${m.card_id}" data-tid="${tid}" title="这张是未下架在售的 10 面额卡，可直接从商店买一张补齐">买</button>`;
  }
  if (m.children && m.children.length) {
    h += `<div class="syn-children">${m.children.map((c) => synMatHtml(c, tid)).join("")}</div>`;
  }
  h += `</div>`;
  return h;
}

async function openSynth(cardId) {
  $("synth-modal").classList.remove("hidden");
  $("synth-body").innerHTML = '<div class="syn-empty">加载合成路径…</div>';
  try {
    const d = await (await fetch(`/api/card/synthesis?card_id=${cardId}`)).json();
    if (!d.ok) {
      $("synth-body").innerHTML = `<div class="ex-msg err">${esc(d.message || "生成失败")}</div>`;
      return;
    }
    const th = d.theme || {};
    let h = `<div class="synth-theme">📦 ${esc(th.name || "套卡")}（套卡#${th.id ?? ""}）</div>`;
    h += `<div class="synth-sub">该套卡所有「可合成」目标的合成路径如下。带「炼」按钮 = 你卡箱已集齐它的 3 张下级卡`
      + (d.free_stoves > 0 ? `，且当前有 ${d.free_stoves} 个空炉` : "，但当前炉位已满")
      + `。点「炼」直接进炉开炼（会真实消耗素材卡）。</div>`;
    if (!d.nodes || !d.nodes.length) {
      h += `<div class="syn-empty">该套卡没有可导出的合成配方（可能都是基础卡）。</div>`;
    } else {
      for (const n of d.nodes) {
        const isClick = n.card_id === d.clicked_card_id;
        h += `<div class="syn-node${isClick ? " on" : ""}">
          <div class="syn-head">
            <span class="syn-name">${esc(n.name)}</span>
            ${n.price ? `<span class="syn-pr">${n.price}面额</span>` : ""}
            <span class="syn-secs">${fmtSynSecs(n.secs)}</span>
            ${n.refinable ? `<span class="tag ok">可炼</span>` : `<span class="tag no">缺料</span>`}
            ${n.refinable
              ? (d.free_stoves > 0
                  ? `<button class="btn sm primary syn-refine" data-tid="${n.card_id}">炼卡</button>`
                  : `<button class="btn sm primary syn-refine" data-tid="${n.card_id}" disabled title="当前炉位已满（自动炼卡占满主炉），腾出空炉后即可炼">炉满</button>`)
              : ""}
          </div>
          <div class="syn-mats">${n.materials.map((m2) => synMatHtml(m2, th.id)).join("")}</div>
        </div>`;
      }
    }
    $("synth-body").innerHTML = h;
    $("synth-body").querySelectorAll(".syn-refine").forEach((b) => {
      b.onclick = async (ev) => {
        ev.stopPropagation();
        await doRefine(Number(b.dataset.tid), b);
      };
    });
    // 缺料且可买的 10 面额下级卡：直接买一张补齐，然后重开弹窗刷新持有状态
    $("synth-body").querySelectorAll(".syn-buy").forEach((b) => {
      b.onclick = async (ev) => {
        ev.stopPropagation();
        const ok = await doBuy(Number(b.dataset.cid), Number(b.dataset.tid), b);
        if (ok) {
          await loadCardBox();
          await openSynth(cardId);
        }
      };
    });
  } catch (e) {
    $("synth-body").innerHTML = `<div class="ex-msg err">请求异常：${esc(e)}</div>`;
  }
}

async function doRefine(targetId, btn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "炼制中…";
  try {
    const r = await fetch(`/api/card/refine?target=${targetId}`, { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      log(`手动炼卡 ${targetId}：${d.message || "已提交"}`, "ok");
      await loadCardBox();
      closeSynth();
    } else {
      log(`手动炼卡 ${targetId} 失败：${d.message || d.code || "未知错误"}`, "err");
      btn.disabled = false;
      btn.textContent = old;
    }
  } catch (e) {
    log("手动炼卡异常：" + e, "err");
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function doBuy(cardId, themeId, btn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "购买中…";
  try {
    const r = await fetch(`/api/card/buy?card_id=${cardId}&theme_id=${themeId}`, { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      log(`买卡 ${cardId}（套${themeId}）：成功`, "ok");
      // 刷新卡库持有数
      if (libState.curThemeTid) openTheme(libState.curThemeTid);
      return true;
    } else {
      log(`买卡 ${cardId}（套${themeId}）失败：${d.message || d.code || "未知错误"}`, "err");
      btn.disabled = false;
      btn.textContent = old;
      return false;
    }
  } catch (e) {
    log("买卡异常：" + e, "err");
    btn.disabled = false;
    btn.textContent = old;
    return false;
  }
}

// ---------- 抽奖/翻牌页 ----------
let lotteryInitDone = false;
let lotState = { randchance: 0, running: false, results: [] };
let lotTimer = null;
const LOT_KEY = "mc_lot_log";

function lotLoad() {
  try { return JSON.parse(localStorage.getItem(LOT_KEY)) || []; } catch { return []; }
}
function lotSave() {
  try { localStorage.setItem(LOT_KEY, JSON.stringify(lotState.results.slice(-200))); } catch {}
}

function initLotteryOnce() {
  if (lotteryInitDone) return;
  lotteryInitDone = true;
  lotState.results = lotLoad();
  $("lot-draw-all").onclick = lotDrawAll;
  $("lot-pai-once").onclick = lotPaiOnce;
  $("lot-clear").onclick = () => {
    lotState.results = [];
    lotSave();
    renderLottery();
  };
  lotRefresh();
  // 停留在抽奖页时，每 10 秒刷新一次 randchance，避免 stale
  if (lotTimer) clearInterval(lotTimer);
  lotTimer = setInterval(() => {
    if (!document.hidden && !lotState.running) lotRefresh();
  }, 10000);
}

async function lotRefresh() {
  try {
    const d = await (await fetch("/api/state")).json();
    if (d.ok && d.user) {
      lotState.randchance = d.user.randchance || 0;
    }
  } catch (e) { log("读取抽奖状态异常：" + e, "err"); }
  renderLottery();
}

async function lotDrawAll() {
  if (lotState.running) return;
  lotState.running = true;
  $("lot-draw-all").disabled = true;
  log("一键抽卡：先刷新剩余次数…", "info");
  try {
    // 点击前强制刷新，避免前端 stale 数据导致"明明有机会却抽了0次"的误解
    await lotRefresh();
    if (lotState.randchance <= 0) {
      const tip = "当前没有随机抽卡机会（randchance=0），请等待自动恢复后再抽";
      lotState.results.push({ ts: Date.now(), type: "draw", ok: false, text: tip });
      log(tip, "err");
      lotState.running = false;
      $("lot-draw-all").disabled = false;
      renderLottery();
      return;
    }
    const d = await (await fetch("/api/random/draw", { method: "POST" })).json();
    lotState.randchance = d.remain || 0;
    const summary = `抽卡 ${d.draws} 次，得卡 ${d.cards} 张，卖出 ${d.sold} 张（+${d.gold} 金），剩余 ${d.remain} 次`;
    lotState.results.push({ ts: Date.now(), type: "draw", ok: d.ok, text: summary, detail: d });
    lotSave();
    log(summary, d.ok ? "ok" : "err");
  } catch (e) {
    lotState.results.push({ ts: Date.now(), type: "draw", ok: false, text: "请求异常：" + e });
    lotSave();
    log("一键抽卡异常：" + e, "err");
  }
  lotState.running = false;
  $("lot-draw-all").disabled = false;
  renderLottery();
}

async function lotPaiOnce() {
  log("翻牌一次…", "info");
  try {
    const d = await (await fetch("/api/pai/open", { method: "POST" })).json();
    const items = (d.items || []).map((it) => {
      return it.text || it.name || (it.cardname || "") || `道具${it.id || ""}`;
    }).join("、") || "（无奖励数据）";
    const text = `${d.message || "翻牌结束"} → ${items}`;
    lotState.results.push({ ts: Date.now(), type: "pai", ok: d.ok, text, detail: d });
    lotSave();
    log(text, d.ok ? "ok" : "err");
  } catch (e) {
    lotState.results.push({ ts: Date.now(), type: "pai", ok: false, text: "翻牌异常：" + e });
    lotSave();
    log("翻牌异常：" + e, "err");
  }
  renderLottery();
}

function renderLottery() {
  $("lot-randchance").innerHTML =
    `<span class="k">剩余机会</span><span class="v">${lotState.randchance} 次</span>`;
  const body = $("lot-body");
  $("lot-cnt").textContent = lotState.results.length
    ? `最近 ${lotState.results.length} 条` : "";
  if (!lotState.results.length) return;
  body.innerHTML = lotState.results.slice().reverse().map((r) => {
    const t = new Date(r.ts).toLocaleTimeString("zh-CN", { hour12: false });
    const icon = r.type === "draw" ? "🎲" : "🃏";
    const cls = r.ok ? "aok" : "aerr";
    const detail = r.detail ? `<pre style="margin:4px 0 0;font:11px monospace;white-space:pre-wrap">${esc(JSON.stringify(r.detail, null, 2).slice(0, 500))}</pre>` : "";
    return `<div style="border-bottom:1px solid var(--line);padding:8px 0">
      <div><span class="at">[${t}]</span> <span class="${cls}">${icon} ${esc(r.text)}</span></div>${detail}</div>`;
  }).join("");
}

// ---------- 换卡「好友池」开关（默认开，选择持久化） ----------
const FRIEND_POOL_KEY = "mc.friendPool";

function initFriendPoolPref() {
  const cb = $("ex-all-players");
  if (!cb) return;
  let on = true; // 默认开启：好友名单里含自己的小号，是有效换卡对象
  try {
    const v = localStorage.getItem(FRIEND_POOL_KEY);
    if (v !== null) on = v === "1";
  } catch {}
  cb.checked = on;
  cb.addEventListener("change", () => {
    try { localStorage.setItem(FRIEND_POOL_KEY, cb.checked ? "1" : "0"); } catch {}
  });
}

(async () => {
  initFriendPoolPref();
  log(`前端已加载 v${APP_VER}`, "info");
  let me = null;
  try {
    me = await (await fetch("/api/me")).json();
    if (me.logged_in) {
      log(`检测到已有登录态（${me.uin}）`, "ok");
      showDash(me.uin);
      // 支持 URL hash 直接打开某个子页（默认 #overview）
      const hp = (location.hash || "").replace("#", "");
      if (hp && ["overview", "lib", "auto", "exchange", "lottery"].includes(hp)) {
        setTimeout(() => switchPane(hp), 50);
      }
      return;
    }
    // 未登录 / 会话已过期：直接弹出扫码（无需手动点退出）
    if (me.expired) log("检测到会话已过期（skey 失效），请重新扫码登录", "warn");
  } catch {}
  showLogin();
  loadQr(me && me.expired ? "会话已过期（skey 失效），请重新扫码登录" : undefined);
})();

// ---------- 灵宠探险 自动循环 ----------
// 参数语义（2026-09-14 用户实证校正，此前写反了）：
//   pass_id    = 大地图序号：1=文明古国 … 6=世界古遗迹 … 11=悬崖之巅（未开放）
//   section_id = 该大地图内的小关序号
// 依据：用户在大地图 6 第 9 小关时，act=1 返回 section_id=9 / pass_id=6，两项各自吻合。
let spInited = false;
let spTimer = null;
let spRendered = new Set();
// 用户是否手动改过「1 级地图/2 级地图」下拉。**按玩法分别记**：
// 没改过就把下拉定位到「能去的最新关」——大地图用服务端 act=1 的当前关，
// 穿越/荒岛用后端记录的最深关（act=1 里没有它们的进度字段，见 spirite_progress 模块）。
let spPicked = { map: false, across: false, island: false };
let spRecent = null;  // /api/cat/travel 的 recent 字段缓存（默认选中用）
let spPrevTs = 0;   // 上一轮探测到的 explore_ts，用于发现「探险结束」转场（含游戏内完成）
let spAutoModes = null;  // /api/spirite/auto/status 的三套玩法各自运行态（独立、互不干扰）

// 名字表：act=1 只下发编号，名字全由后端 /api/spirite/maps 提供（数据源 = 官方 soul_elf 配置）：
//   table   = 大关 → 小关（官方顺序，小关名即卡名）
//   spirits = 灵宠官方名表 id→name（16 只：金木水火土风光电 × 雄雌）
// 「灵宠探险」入口下并列三套玩法（官方 soul_elf 里就是三个独立容器），界面**同页切换**、不另开页：
//   map    = 大地图     pass_mainlist/pass_sectionlist  11 章 122 关（状态 explore.*）
//   island = 荒岛       c_island                         8 章  48 关（状态 hy_*，已证实）
//   across = 穿越丛林   c_across                        11 章  62 关（状态未确认）
// 三者的「关卡号」语义不同：大地图是 section_id（章内 1..N），另两套是**连续编号 seq**（跨章不重置）。
let spMode = "map";
let spTable = {};
let spMapIds = [];
let spIslands = {};        // 荒岛章节表（键=chapter_id）
let spAcross = {};         // 穿越丛林章节表（键=chapter_id）
let spSpirits = {};
let spSpiritesData = [];   // act=1 的 spirites（id/lv），用于名字表就绪后重建列表

// 灵宠名：直接用官方 spirit_maplist（如 9 →「土灵宠（雄）」），查不到才退回「灵宠N」。
function spLabelName(id, fallback) {
  return spSpirits[String(id)] || fallback || ("灵宠" + id);
}

// 灵宠勾选列表：显示官方名（「土灵宠（雄）」等）。名字表可能后到，故可重复调用重建，
// 重建时保留已勾选项。
function spRenderSpirites(list) {
  if (list) spSpiritesData = list;
  const box = $("sp-spirites");
  if (!box || !spSpiritesData.length) return;
  const keep = new Set([...box.querySelectorAll(".sp-sp:checked")].map((c) => c.value));
  box.innerHTML = spSpiritesData.map((s) =>
    '<label class="check" style="white-space:nowrap">'
    + '<input type="checkbox" class="sp-sp" value="' + s.id + '"'
    + (keep.has(String(s.id)) ? " checked" : "") + ">"
    + esc(spLabelName(s.id, "灵宠" + s.id)) + "（lv" + s.lv + "）</label>").join("");
}

// 大地图/小关 → 可读文本，如「世界古遗迹 · 第9关 狮身人面像」
function spPlaceText(passId, sectionId) {
  const t = spTable[String(passId)] || {};
  const nm = t.name || ("地图 " + passId);
  if (!sectionId) return nm;
  const st = (t.stages || []).find((s) => s.index === Number(sectionId));
  return nm + " · 第" + sectionId + "关" + (st ? " " + st.name : "");
}

// 三套玩法共用那两个下拉：第一个装「章节」、第二个装「关卡」。
// 切换玩法时只需换填充源与标签文案（见 spSetMode），不新开页。
const SP_MODE_LABELS = {
  map: ["大地图", "小关"],
  island: ["荒岛章节", "关卡"],
  across: ["穿越章节", "关卡"],
};

// 当前玩法下的章节列表 → [[value, name], ...]（value = 大地图 pass_id / 另两套 chapter_id）
function spChapters() {
  if (spMode === "island") return Object.entries(spIslands).map(([k, v]) => [k, v.name]);
  if (spMode === "across") return Object.entries(spAcross).map(([k, v]) => [k, v.name]);
  return spMapIds.map((m) => [m, (spTable[String(m)] || {}).name || ""]);
}

// 某章节下的关卡列表（原始 dict；大地图=stages，另两套=sections）
function spSections(chapterId) {
  if (spMode === "island") return (spIslands[String(chapterId)] || {}).sections || [];
  if (spMode === "across") return (spAcross[String(chapterId)] || {}).sections || [];
  return (spTable[String(chapterId)] || {}).stages || [];
}

// 关卡在接口里的编号：大地图 = section_id（章内 1..N）；荒岛/穿越 = seq（连续编号）
function spStageValue(s) {
  return spMode === "map" ? s.index : s.seq;
}

// 穿越丛林：连续编号 seq → {chapter, index, name}。
// act=11 的参数是 (1 级地图编号, 2 级地图编号)，后端再转成 id=1级-1、sub_id=2级-1。
// 而下拉里存的是连续 seq，所以这里必须反查一次，别直接拿 seq 当 index 发。
function spAcrossBySeq(seq) {
  for (const [cid, ch] of Object.entries(spAcross || {})) {
    for (const s of (ch.sections || [])) {
      if (Number(s.seq) === Number(seq)) {
        return { chapter: Number(cid), index: Number(s.index), name: s.name,
                 chapterName: ch.name, place: `${ch.name} · ${s.name}` };
      }
    }
  }
  return null;
}

// (1 级地图编号, 2 级地图编号) → 连续 seq（穿越的下拉存 seq）
function spAcrossSeqByCi(chapter, index) {
  const ch = (spAcross || {})[String(chapter)] || {};
  const s = (ch.sections || []).find((x) => Number(x.index) === Number(index));
  return s ? Number(s.seq) : 0;
}

// 当前玩法「能去的最新关」→ {chapter, value}（下拉的值）：
//   map    —— 服务端 act=1 的 (pass_id, section_id)，权威
//   island —— 后端记录的最深关 seq（反查它属于哪个 1 级地图）
//   across —— 后端记录的最深关 (chapter, index)，换算成下拉用的 seq
function spDefaultStage() {
  const r = spRecent || {};
  if (spMode === "map") {
    const m = r.map || {};
    if (!m.pass_id) return null;
    return { chapter: String(m.pass_id), value: String(m.section_id || 1) };
  }
  if (spMode === "island") {
    const seq = Number((r.island || {}).seq || 0);
    if (!seq) return null;
    for (const [cid, ch] of Object.entries(spIslands || {})) {
      if ((ch.sections || []).some((s) => Number(s.seq) === seq)) {
        return { chapter: String(cid), value: String(seq) };
      }
    }
    return null;
  }
  // across 的 chapter/seq 由后端按 official open_c/open_s 推导后下发（最新可去的关）
  const a = r.across || {};
  if (!a.chapter || !a.seq) return null;
  return { chapter: String(a.chapter), value: String(a.seq) };
}

// 当前玩法的「已解锁关卡」集合（来自 act=1 的 recent，由 official open_c/open_s + 灵宠等级推导）。
// 大地图返回 null —— 它的进度由服务端 act=1 直接给（pass_id/section_id），不做未解锁标注。
function spUnlockedSet() {
  const r = (spRecent || {})[spMode] || {};
  if (spMode === "map" || !Array.isArray(r.unlocked)) return null;
  return new Set(r.unlocked.map(Number));
}

// 章节（大关）是否锁定不可选
function spChLocked(chVal) {
  if (spMode === "map") {
    const m = (spRecent || {}).map || {};
    if (!m.pass_id) return false;
    return Number(chVal) > Number(m.pass_id);   // 超出当前章的整章未开放
  }
  // 荒岛/穿越：整章没有任何已解锁关卡则置灰
  const secs = spSections(chVal);
  const un = spUnlockedSet();
  if (!un) return false;
  return !secs.some((s) => un.has(Number(spStageValue(s))));
}

// 小关是否锁定不可选
function spSecLocked(chVal, sec) {
  if (spMode === "map") {
    const m = (spRecent || {}).map || {};
    if (!m.pass_id) return false;
    const C = Number(chVal), v = Number(spStageValue(sec));
    if (C < Number(m.pass_id)) return false;            // 已通关章：全可重玩
    if (C > Number(m.pass_id)) return true;             // 整章未开放
    return v > Number(m.section_id || 0);               // 当前章内：只到已抵达关
  }
  const un = spUnlockedSet();
  if (!un) return false;
  return !un.has(Number(spStageValue(sec)));
}

// 把两个下拉定位到默认关（用户没手动改过时才调用）
function spApplyDefault() {
  const def = spDefaultStage();
  if (!def || !def.value) return false;
  spBuildMaps(def.chapter);
  spBuildStages(def.chapter, def.value);
  return true;
}

function spBuildMaps(keepVal) {
  const sel = $("sp-map");
  if (!sel) return;
  const cur = keepVal || sel.value;
  sel.innerHTML = "";
  for (const [val, nm] of spChapters()) {
    const o = document.createElement("option");
    o.value = val;
    o.textContent = val + (nm ? " · " + nm : "");
    if (spChLocked(val)) { o.disabled = true; o.style.color = "#9aa0a6"; }
    sel.appendChild(o);
  }
  if (cur && [...sel.options].some((o) => o.value === String(cur))) sel.value = String(cur);
}

// 关卡下拉：只放「编号 · 名字」+ 未解锁标注；需求（最多N只/HP/等级）与奖励见下方 sp-stage-info 详情框。
function spBuildStages(chapterId, keepVal) {
  const sel = $("sp-pass");
  if (!sel) return;
  const cur = keepVal || sel.value;
  const secs = spSections(chapterId);
  const opts = secs.length
    ? secs.map((s) => {
        const v = spStageValue(s);
        const locked = spSecLocked(chapterId, s);   // 不能去的关 → 置灰 + 标注
        // 关卡的「需求（最多N只 / HP / 等级）/ 奖励」移到下方 sp-stage-info 详情框，
        // 下拉里只留「编号 · 名字」，避免文字过长出现横向滚动条。
        return [v, v + " · " + s.name + (locked ? " · 未解锁" : "")];
      })
    : Array.from({ length: 20 }, (_, i) => [i + 1, "第 " + (i + 1) + " 关"]);
  sel.innerHTML = "";
  for (const [val, text] of opts) {
    const o = document.createElement("option");
    o.value = val;
    o.textContent = text;
    if (text.endsWith("未解锁")) { o.disabled = true; o.style.color = "#9aa0a6"; }
    sel.appendChild(o);
  }
  if (cur && [...sel.options].some((o) => o.value === String(cur))) sel.value = String(cur);
  spStageInfo();
}

// 选中关卡的官方详情（解锁条件 / 需求 / 奖励），三套玩法字段不同
function spStageInfo() {
  const box = $("sp-stage-info");
  if (!box) return;
  const secs = spSections($("sp-map").value);
  const cur = secs.find((s) => String(spStageValue(s)) === String($("sp-pass").value));
  if (!cur) { box.innerHTML = '<span style="opacity:.7">（该玩法暂无可选关卡）</span>'; return; }
  const bits = [];
  if (spMode === "map") {
    bits.push("最多 " + cur.max_spirite_num + " 只灵宠");
    if (cur.blood_min) bits.push("血量 " + cur.blood_min + "~" + cur.blood_max);
    if (cur.need_gold_key) bits.push("解锁 金钥匙" + cur.need_gold_key + " / 藏宝图" + cur.need_map);
  } else if (spMode === "island") {
    bits.push("需 " + cur.elf_num + " 只灵宠");
    bits.push("灵宠血量 " + cur.elf_hp);
    // open_c/open_s = **解锁条件**（大地图第 open_c 章第 open_s 关需已通过），
    // 不是本关坐标 —— 全表据此判定「能去的最新关」，见 backend/spirite_progress.py。
    bits.push("解锁：大地图 " + cur.open_c + "-" + cur.open_s + " 关已通过");
    bits.push("奖励 五彩石" + (cur.l_wcs || 0) + " / 金粉" + (cur.l_jf || 0));
  } else {
    bits.push("需 " + cur.elf_num + " 只灵宠");
    bits.push("灵宠等级 ≥ " + cur.elf_lv);
    // 本关坐标 = 自己的 (1 级地图, 2 级地图序号)，即 act=11 的 id/sub_id+1。
    bits.push("1 级地图 " + $("sp-map").value + " · 2 级地图 " + cur.index);
    bits.push("解锁：大地图 " + cur.open_c + "-" + cur.open_s + " 关已通过");
  }
  box.innerHTML = "<div><b>" + esc(cur.name) + "</b>（编号 " + spStageValue(cur) + "）</div>"
    + "<div>" + bits.join(" · ") + "</div>"
    + (cur.award
      ? '<div style="opacity:.75">' + esc(cur.award)
        + (spMode === "across" ? '<span style="opacity:.8">（套卡碎片按概率掉落）</span>' : "")
        + "</div>"
      : "");
}

// 切换子玩法：只换下拉内容与标签，页面不动（三套同页）
function spSetMode(mode) {
  spMode = mode;   // 每个玩法各自记「是否手动改过」，切换 tab 不算改动
  // 按钮语义随玩法变：穿越丛林是**即时历练**（没有收获步骤，隐藏「收」），
  // 荒岛的收获走 act=15（大地图才是 act=4）。
  const onceBtn = $("sp-once");
  if (onceBtn) onceBtn.style.display = (mode === "across") ? "" : "none";
  const hb = $("sp-harvest");
  if (hb) {
    hb.style.display = (mode === "across") ? "none" : "";
    hb.textContent = (mode === "island") ? "荒岛收" : "手动收";
  }
  for (const b of document.querySelectorAll(".sp-tab")) {
    const on = b.dataset.mode === mode;
    b.classList.toggle("primary", on);
    b.classList.toggle("ghost", !on);
  }
  const lb = SP_MODE_LABELS[mode] || SP_MODE_LABELS.map;
  if ($("sp-lab1")) $("sp-lab1").textContent = lb[0];
  if ($("sp-lab2")) $("sp-lab2").textContent = lb[1];
  spBuildMaps();
  spBuildStages($("sp-map").value);
  if (!spPicked[spMode]) spApplyDefault();   // 定位到「能去的最新关」
  // ⚠️ 切 tab **不发任何网络请求**：act=1 一份数据就含三套玩法的状态与解锁进度
  //（已缓存在 spLastData / spRecent），这里只用缓存立即重渲染状态条 + 三行面板。
  // 之前这里不重渲染，切完 tab 要等下一轮轮询（8 秒）状态才变，看起来像"在加载"。
  if (spLastData) spRenderState(spLastData);
}

async function spLoadTable() {
  try {
    const d = await (await fetch("/api/spirite/maps")).json();
    if (!d.ok) return;
    spTable = d.table || {};
    spMapIds = d.maps || [];
    spIslands = d.islands || {};
    spAcross = d.across || {};
    spSpirits = {};
    for (const s of (d.spirits || [])) spSpirits[String(s.id)] = s.name;
    spBuildMaps($("sp-map").value);
    spBuildStages($("sp-map").value, $("sp-pass").value);
    if (!spPicked[spMode]) spApplyDefault();   // 表就绪后再定位一次（首次进页面时 recent 可能先到）
    spRenderSpirites();   // 名字表就绪 → 重建勾选列表（把「灵宠N」换成官方名）
  } catch (e) { /* 静默：下拉退回数字 */ }
}

function initSpiriteOnce() {
  if (spInited) return;
  spInited = true;
  $("sp-refresh").addEventListener("click", spRefresh);
  $("sp-harvest").addEventListener("click", spHarvest);
  $("sp-start").addEventListener("click", spStart);
  $("sp-once").addEventListener("click", spOnce);
  $("sp-stop").addEventListener("click", spStop);
  $("sp-clearlog").addEventListener("click", () => {
    $("sp-log").innerHTML = '<div class="empty-tip">日志已清空。</div>';
    spRendered = new Set();
  });
  $("sp-clearglob").addEventListener("click", () => {
    logClear();
    spSay("全局操作日志已清空。", "info");
  });
  // 换 1 级地图 → 标记「改过」并重建 2 级地图下拉（换成那张图的关卡）
  $("sp-map").addEventListener("change", () => {
    spPicked[spMode] = true;
    spBuildStages($("sp-map").value);
  });
  // 子玩法切换（大地图 / 荒岛 / 穿越丛林）——三套同页，只换下拉内容
  for (const b of document.querySelectorAll(".sp-tab")) {
    b.addEventListener("click", () => spSetMode(b.dataset.mode));
  }
  // 换 2 级地图 → 同样标记为「改过」（此后刷新不再覆盖用户的选择）
  $("sp-pass").addEventListener("change", () => { spPicked[spMode] = true; });
  // 一次探险当前按最多 2 只（官方 max_spirite_num 有 1/2/4 三档，4 只的关卡暂未开）
  $("sp-spirites").addEventListener("change", (e) => {
    if (!e.target.classList || !e.target.classList.contains("sp-sp")) return;
    const cs = [...$("sp-spirites").querySelectorAll(".sp-sp:checked")];
    if (cs.length > 2) {
      e.target.checked = false;
      spSay("一次探险最多派 2 只灵宠", "warn");
    }
  });
  spLoadTable();
  spRefresh();
  spPoll();
}

function spSay(msg, kind) {
  const box = $("sp-log");
  if (!box) return;
  const tip = box.querySelector(".empty-tip");
  if (tip) tip.remove();
  const t = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const color = kind === "err" ? "#c0392b" : kind === "warn" ? "#b8860b"
    : kind === "ok" ? "#1e7e34" : "var(--muted)";
  const div = document.createElement("div");
  div.style.cssText = "padding:3px 0;border-bottom:1px dashed var(--line);font-size:12.5px;color:" + color;
  div.textContent = "[" + t + "] " + msg;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// 「能去的最新关」+ 卡点提示（由后端按 official open_c/open_s + 灵宠等级推导，见 spirite_progress）。
function spLatestTxt(v) {
  if (!v || !v.seq) return "";
  let s = "能去的最新关：" + (v.name || ("编号 " + v.seq)) + "（编号 " + v.seq + "）";
  const b = v.blocked;
  if (b && b.reason) s += " ｜ 下一关「" + b.name + "」" + b.reason;
  return s;
}

// 三套玩法状态**一次全部渲染**（同一份 act=1 就含全部玩法的状态与进度），
// 所以切换 tab 只需重渲染高亮，不需要再打接口（见 spSetMode）。
function spRenderAll(d) {
  const box = $("sp-all");
  if (!box) return;
  const r = spRecent || {};
  const ex = d.explore || {}, isl = d.island || {}, ac = d.across || {};
  const now = Date.now() / 1000;
  const names = (arr) => (arr || []).map((x) => spLabelName(x.id, "灵宠" + x.id)).join(" + ");

  const mts = ex.explore_ts || 0, mput = ex.put_spirites || [];
  const mStage = spPlaceText(d.pass_id, d.section_id);
  const mapTxt = (mts && mput.length)
    ? "进行中 " + Math.floor((now - mts) / 60) + " 分 · " + names(mput) + " @ " + mStage
    : "无进行中 · 当前 " + mStage;

  const its = isl.explore_ts || 0, iput = isl.spirites || [];
  const islMax = Number(isl.daily_max) || 5, islUsed = Number(isl.today_cnt ?? 0);
  const islLeft = Math.max(0, islMax - islUsed);
  const islTxt = (its && iput.length)
    ? "进行中 " + Math.floor((now - its) / 60) + " 分 · " + names(iput)
      + " @ " + (isl.place || ("编号 " + isl.seq))
    : "今日 " + islUsed + "/" + islMax + " 次 · 剩 " + islLeft + " 次";

  const acMax = Number(ac.daily_max) || 10, acUsed = Number(ac.used ?? 0);
  const acLeft = Math.max(0, acMax - acUsed);
  const acTxt = "今日 " + acUsed + "/" + acMax + " 次 · 剩 " + acLeft + " 次 · 即时到账";

  const rows = [
    ["map", "大地图", mapTxt, ""],
    ["island", "荒岛", islTxt, spLatestTxt(r.island)],
    ["across", "穿越丛林", acTxt, spLatestTxt(r.across)],
  ];
  box.innerHTML = rows.map(([mode, nm, txt, extra]) => {
    const running = !!(spAutoModes && spAutoModes[mode] && spAutoModes[mode].running);
    return '<div class="sp-all-row' + (spMode === mode ? " on" : "") + '" data-mode="' + mode + '">'
      + '<span class="n">' + esc(nm) + '</span><span class="v">' + esc(txt) + "</span>"
      + (extra ? '<span class="x">' + esc(extra) + "</span>" : "")
      + (running ? '<span class="x" style="color:#1e7e34;font-weight:600">● 自动中</span>' : "")
      + "</div>";
  }).join("");
  for (const el of box.querySelectorAll(".sp-all-row")) {
    el.addEventListener("click", () => spSetMode(el.dataset.mode));
  }
}

function spRenderState(d) {
  spLastData = d;
  $("sp-free").textContent = d.free_explore_cnt ?? "-";
  $("sp-power").textContent = d.power ?? "-";
  spRenderWallet(d);
  const ex = d.explore || {};
  const isl = d.island || {};
  const ac = d.across || {};
  const curK = $("sp-cur-k");
  let txt = "";
  let active = false;   // 是否有进行中的探险（决定是否允许「收」按钮）
  if (spMode === "across") {
    // 穿越丛林 = **即时历练**，没有"进行中"状态；这里显示今日已用次数。
    // 计数器是 torrid_zone_explore_cnt（实测 act=11 成功一次 +1），上限 10（VIP 15）。
    if (curK) curK.textContent = "今日历练";
    const am = Number(ac.daily_max) || 10, au = Number(ac.used ?? 0);
    txt = au + "/" + am + " 次（剩 " + Math.max(0, am - au) + " 次，每次即时到账，无需收获）";
  } else if (spMode === "island") {
    if (curK) curK.textContent = "荒岛探险";
    const iits = isl.explore_ts || 0;
    const iput = isl.spirites || [];
    if (iits && iput.length) {
      const el = Date.now() / 1000 - iits;
      const imins = Math.floor(el / 60);
      const ready = el >= 180;   // 荒岛探险 3 分钟
      const inames = iput.map((x) => spLabelName(x.id, "灵宠" + x.id)).join(" + ");
      txt = (ready ? "已结束 · 待收获（" : "进行中（") + imins + " 分）｜ " + inames
          + " @ " + (isl.place || ("编号 " + isl.seq));
      active = true;
    } else {
      const im = Number(isl.daily_max) || 5, iu = Number(isl.today_cnt ?? 0);
      txt = "无（今日已探险 " + iu + "/" + im + " 次 · 剩 " + Math.max(0, im - iu) + " 次）";
    }
  } else {
    if (curK) curK.textContent = "当前探险";
    const ts = ex.explore_ts || 0;
    const put = ex.put_spirites || [];
    const where = spPlaceText(d.pass_id, d.section_id);
    txt = "无（当前 " + where + "）";
    if (ts && put.length) {
      active = true;
      const elapsed = Date.now() / 1000 - ts;
      const mins = Math.floor(elapsed / 60);
      const ready = elapsed >= 300;   // 约 5 分钟后可收
      const names = put.map((x) => spLabelName(x.id, "灵宠" + x.id)).join(" + ");
      txt = (ready ? "已结束 · 待收获（" : "进行中（") + mins + " 分）｜ " + names + " @ " + where;
    }
  }
  $("sp-cur").textContent = txt;
  const hb = $("sp-harvest");
  if (hb) hb.disabled = !active;
  // 探测「探险结束」转场（含游戏内完成）：仅在本页实时轮询中、且确有"进行中→无"跳变时才记，
  // 这样即便用户在游戏里把灵宠收了，只要灵宠页开着，全局日志也能留下一笔。
  const tsNow = (spMode === "island" ? (isl.explore_ts || 0) : (ex.explore_ts || 0));
  if (spLiveTimer && spPrevTs && !tsNow) {
    log("灵宠探险结束（状态转为「无」，可收获或已收）", "warn");
  }
  spPrevTs = tsNow || 0;
  spRenderAll(d);   // 三套玩法状态同屏（切换 tab 不需要重新取数）
}

// 轻量轮询：只刷新状态条（不碰下拉/灵宠列表），用于手动派发后界面自动跟进。
async function spTick() {
  try {
    const d = await (await fetch("/api/cat/travel")).json();
    if (d.ok) spRenderState(d);
  } catch (e) { /* 静默 */ }
}

let spLiveTimer = null;
function spStartLive() {
  if (spLiveTimer) return;
  // 15 秒一轮，且页面不可见（后台标签页）时跳过：act=1 每次都要走一趟游戏服务端，
  // 没必要高频轮询；要看最新状态点「刷新状态」即可。
  spLiveTimer = setInterval(() => { if (!document.hidden) spTick(); }, 15000);
}
function spStopLive() {
  if (spLiveTimer) { clearInterval(spLiveTimer); spLiveTimer = null; }
}

// 灵宠专用资源：碎片 + 银锭/百变丹/金钥匙/藏宝图/灵力果
// （官方 soul_elf 奖励清单原文：「卡片碎片｜银锭、百变丹、金钥匙、藏宝图、灵力果等」）。
// 随 /api/cat/travel（act=1）一起下发，由 spRenderState 每轮自动刷新。
function spRenderWallet(d) {
  const box = $("sp-wallet");
  if (!box) return;
  const chips = [];
  const pc = d.pieces || {};
  if (pc.total !== undefined) {
    chips.push("碎片 " + (pc.kinds || 0) + " 种 / " + (pc.total || 0) + " 个");
  }
  for (const w of (d.wallet || [])) chips.push(esc(w.name) + " " + w.count);
  box.innerHTML = chips.length
    ? chips.map((t) => '<span class="stat-chip" style="padding:3px 8px">' + t + "</span>").join("")
    : '<span style="color:var(--muted)">-</span>';
}

// 炼卡道具（/api/props → card_market_props_info，全是 type=1 的炼卡道具卡）——
// **与灵宠探险无关**（灵宠道具是碎片/银锭/百变丹/金钥匙/藏宝图/灵力果，见 spRenderWallet），
// 所以归总览页，不再放在灵宠页。
async function ovLoadProps() {
  const box = $("ov-props");
  if (!box) return;
  try {
    const d = await (await fetch("/api/props")).json();
    const ps = d.props || [];
    box.innerHTML = ps.length
      ? ps.map((p) => '<span class="stat-chip" style="padding:3px 8px">'
          + esc(p.name) + " ×" + p.count + "</span>").join("")
      : '<span style="color:var(--muted)">无</span>';
  } catch (e) { box.textContent = "读取失败"; }
}

// 收获明细：奖励名一律由后端翻译（backend/prop_dict.py → item_dict，官方静态配置）。
// 后端没给 items 时才退回**只显示大类编号**——绝不在前端硬编码名字：
// 旧兜底表 {56:卡片碎片, 57:灵力果, 59:金钥匙} 既漏了 58 藏宝图/72 银锭/73 百变丹，
// 又把 57 的初/中/高三档混成一个，本身就是错的（58 还被写成「高级灵力果」）。
function spFmtSend(send, items) {
  if (items && items.length) return items.map((x) => x.text).join("，");
  return (send || []).map((x) => {
    const i = Number(x.id) || 0;
    return "道具" + (i ? x.type + "_" + i : x.type) + "×" + x.num;
  }).join("，");
}

// 手动收取已结束的探险。两套玩法的收获接口**不是同一个**：
//   大地图 → act=4（无进行中时返回 -5）
//   荒岛   → act=15（无参数；实测会把 hy_explore_ts 归零并真实入包）
async function spHarvest() {
  const hb = $("sp-harvest");
  if (hb) hb.disabled = true;
  const isIsland = (spMode === "island");
  const url = isIsland ? "/api/spirite/island/harvest" : "/api/cat/harvest";
  const tag = isIsland ? "荒岛" : "";
  try {
    const d = await (await fetch(url, { method: "POST" })).json();
    if (d.ok) {
      const send = (d.result && (d.result.send || (d.result.data && d.result.data.send))) || [];
      const detail = (d.items && d.items.length) ? spFmtSend(send, d.items)
        : (d.result && d.result.content ? d.result.content
          : (send.length ? spFmtSend(send) : "成功（无明细）"));
      spSay(tag + "收获成功：" + detail, "ok");
      log("灵宠" + tag + "收获成功：" + detail, "ok");
    } else {
      const msg = tag + "收获失败：code=" + d.code + " " + (d.message || "");
      spSay(msg, (d.code === -5 || d.code === -2) ? "warn" : "err");
      log("灵宠" + msg, (d.code === -5 || d.code === -2) ? "warn" : "err");
    }
    spTick();   // 收获后刷新灵宠资源（碎片/银锭/百变丹/金钥匙/藏宝图）
  } catch (e) { spSay("收获异常：" + e, "err"); }
}

// 穿越丛林「单次历练」：act=11，**即时到账**（没有收获步骤）。
// 参数由后端按 (1 级地图, 2 级地图) 换算成 id=1级-1、sub_id=2级-1（0-based，对应官方 open_c/open_s）。
async function spOnce() {
  if (spMode !== "across") { spSay("单次历练只在「穿越丛林」玩法下可用", "warn"); return; }
  const seq = Number($("sp-pass").value || 0);
  const found = spAcrossBySeq(seq);
  if (!found) { spSay("请选择要历练的关卡", "warn"); return; }
  const btn = $("sp-once");
  if (btn) btn.disabled = true;
  try {
    const d = await (await fetch("/api/spirite/across/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter: found.chapter, index: found.index }),
    })).json();
    if (d.ok) {
      spSay("历练成功：" + (d.text || d.content) + "（" + d.label + "）", "ok");
      log("灵宠穿越历练：" + d.label + " → " + (d.text || d.content), "ok");
    } else {
      spSay("历练失败：" + (d.message || ("code=" + d.code)) + "（该关可能未解锁）", "warn");
      log("灵宠穿越历练失败：" + (d.message || d.code) + " " + (d.label || ""), "warn");
    }
    spTick();
  } catch (e) { spSay("历练异常：" + e, "err"); }
  finally { if (btn) btn.disabled = false; }
}

async function spRefresh() {
  try {
    const d = await (await fetch("/api/cat/travel")).json();
    if (!d.ok) { spSay("查询失败：" + (d.message || d.code), "err"); return; }
    // 先存进度缓存，再渲染 —— 下拉的「未解锁」标注与默认定位都要用它
    if (d.recent) spRecent = d.recent;
    spRenderState(d);
    // 名字表还没拿到（首次进入或接口慢）就先补一次，保证下拉与灵宠名都有
    if (!spMapIds.length || !Object.keys(spSpirits).length) await spLoadTable();
    // 默认定位到「能去的最新关」：map = act=1 的 (pass_id, section_id)；
    // across/island = 后端按官方 open_c/open_s + 灵宠等级推导（不再存任何本地进度值）。
    // 用户手动改过就不覆盖。
    if (!spPicked[spMode]) spApplyDefault();

    spRenderSpirites(d.spirites);
    spSay("状态：免费 " + d.free_explore_cnt + " 次 / 灵力 " + d.power
      + " / 灵宠 " + (d.spirites || []).length + " 只", "info");
  } catch (e) { spSay("查询异常：" + e, "err"); }
}

async function spStart() {
  // 三套玩法共用同一个自动引擎，由 mode 分派（后端 spirite_auto._loop）：
  //   map    act=3 出发 / act=4 收获（≥5 分钟）
  //   island act=14 出发（id=连续编号 1..48）/ act=15 收获（3 分钟）
  //   across act=11 即时历练（无收获；id=1 级地图-1、sub_id=2 级地图-1）
  // ⚠️ 三套玩法的关卡编号语义不同（map=章内 section_id、island/across=连续 seq），
  //    所以这里按模式取不同的下拉值，绝不能混用。
  const checked = [...document.querySelectorAll(".sp-sp:checked")].map((c) => Number(c.value));
  const body = {
    mode: spMode,
    spirite_ids: checked,
    auto_dispatch: $("sp-autodispatch").checked,
    interval: Number($("sp-interval").value || 30),
    dry_run: $("sp-dryrun").checked,
  };
  let where = "";
  if (spMode === "map") {
    body.pass_id = Number($("sp-map").value || 1);      // 大地图（下拉 #sp-map 存的是 pass_id）
    body.section_id = Number($("sp-pass").value || 1);  // 该大地图内的小关
    body.spirite_num = checked.length ? Math.min(2, checked.length) : 2;
    body.min_wait = Number($("sp-minwait").value || 300);
    where = `地图${body.pass_id}/小关${body.section_id}`;
  } else if (spMode === "island") {
    body.island_seq = Number($("sp-pass").value || 0);  // 连续编号（act=14 的 id）
    body.min_wait = Number($("sp-minwait").value || 180);
    if (checked.length) body.spirite_num = checked.length;
    const name = $("sp-pass").selectedOptions[0] ? $("sp-pass").selectedOptions[0].textContent : body.island_seq;
    where = `荒岛「${name}」`;
  } else {
    const found = spAcrossBySeq(Number($("sp-pass").value || 0));
    if (!found) { spSay("请先选择穿越丛林的关卡", "warn"); return; }
    body.across_chapter = found.chapter;
    body.across_index = found.index;
    where = `穿越「${found.name}」`;
  }
  if (checked.length) where += ` 灵宠${checked.join(",")}`;
  else where += " 灵宠自动轮换";
  try {
    const d = await (await fetch("/api/spirite/auto/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).json();
    spSay(d.message || (d.ok ? "已启动" : "启动失败"), d.ok ? "ok" : "err");
    if (!d.ok) return;
    log(`开始灵宠自动探险：${where}` + (body.dry_run ? "（模拟）" : ""), "ok");
    $("sp-start").disabled = true;
    $("sp-stop").disabled = false;
    if (spTimer) clearInterval(spTimer);
    spTimer = setInterval(spPoll, 3000);
    spPoll();
  } catch (e) { spSay("启动异常：" + e, "err"); }
}

async function spStop() {
  try {
    const d = await (await fetch("/api/spirite/auto/stop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: spMode }),
    })).json();
    spSay(d.message || "已停止", d.ok ? "warn" : "info");
    if (d.ok) log("停止灵宠自动探险（" + (SP_MODE_LABELS[spMode] ? SP_MODE_LABELS[spMode][0] : spMode) + "）", "warn");
  } catch (e) { spSay("停止异常：" + e, "err"); }
}

async function spPoll() {
  try {
    const d = await (await fetch("/api/spirite/auto/status")).json();
    const modes = d.modes || {};
    spAutoModes = modes;   // 供三行状态面板显示「● 自动中」徽标
    // 聚合三套玩法的日志（带玩法前缀），去重后写入面板
    let anyRunning = false;
    const logs = [];
    for (const m of ["map", "island", "across"]) {
      const st = modes[m] || {};
      if (st.running) anyRunning = true;
      for (const lg of (st.logs || [])) logs.push({ mode: m, ...lg });
    }
    logs.sort((a, b) => (a.ts || 0) - (b.ts || 0));
    for (const lg of logs) {
      const key = lg.mode + "|" + lg.ts + "|" + lg.action + "|" + (lg.detail || "");
      if (spRendered.has(key)) continue;
      spRendered.add(key);
      const tag = (SP_MODE_LABELS[lg.mode] ? SP_MODE_LABELS[lg.mode][0] : lg.mode) + " ";
      const txt = tag + lg.action + (lg.detail ? "：" + lg.detail : "");
      spSay(txt, lg.ok ? "ok" : "warn");
      if (!/进行中/.test(lg.action)) log("灵宠·" + txt, lg.ok ? "ok" : "warn");
    }
    // 当前玩法的运行态 → 按钮 + 状态条
    const cur = modes[spMode] || {};
    $("sp-harv").textContent = cur.harvests ?? 0;
    if (cur.state && (cur.state.pass_id !== undefined || cur.state.explore)) spRenderState(cur.state);
    const running = !!cur.running;
    $("sp-start").disabled = running;
    $("sp-stop").disabled = !running;
    // 刷新三行面板的「自动中」徽标（用上次完整状态，避免随轮询清空）
    if (spLastData) spRenderAll(spLastData);
    if (cur.finished && cur.message && !running) {
      const fk = "finish|" + spMode;
      if (!spRendered.has(fk)) { spRendered.add(fk); spSay(cur.message, "ok"); }
    }
    // 自维持：只要还有任意玩法在跑，就保证轮询定时器在跑（页面刚加载时若后端引擎仍在跑，
    // 单次 spPoll 也要把定时器续上，否则「自动中」徽标会卡在旧状态不更新/不消失）。
    if (anyRunning && !spTimer) spTimer = setInterval(spPoll, 3000);
    if (!anyRunning && spTimer) { clearInterval(spTimer); spTimer = null; }
  } catch {}
}
