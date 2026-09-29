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

// ---------- 每日比赛页专属日志（右栏，2026-09-26）
// 与上面的总览页全局日志完全独立：只记「每日比赛页里各玩法」的操作与结果，
// 别的页（炼卡/挖地/换卡/猫窝…）的 log() 不会混进来，比赛页的也不会跑到全局日志里。
const MLOG_KEY = "mc_match_log";
const MLOG_MAX = 200;

function mlogLoad() {
  try {
    const raw = localStorage.getItem(MLOG_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}
function mlogSave(entries) {
  try { localStorage.setItem(MLOG_KEY, JSON.stringify(entries.slice(-MLOG_MAX))); } catch {}
}
// 渲染到所有带 data-mlog 的容器（结构上就是比赛页右栏一个，多挂一个也认）
function mlogRender(entries) {
  const boxes = document.querySelectorAll("[data-mlog]");
  if (!boxes.length) return;
  const cnt = $("mlog-cnt");
  if (cnt) cnt.textContent = entries.length + " 条";
  for (const box of boxes) {
    box.innerHTML = "";
    if (!entries.length) {
      const tip = document.createElement("div");
      tip.className = "empty-tip";
      tip.textContent = "记录本页各玩法的操作与结果（刷新页面仍在）。";
      box.appendChild(tip);
      box.scrollTop = 0;
      continue;
    }
    for (const e of entries) {
      const line = document.createElement("div");
      line.className = "ln" + (e.cls ? " " + e.cls : "");
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = `[${e.ts}] `;
      const m = document.createElement("span");
      m.className = "m";
      m.textContent = e.msg;
      line.appendChild(t);
      line.appendChild(m);
      box.appendChild(line);
    }
    box.scrollTop = box.scrollHeight;
  }
}
function mlogClear() {
  try { localStorage.removeItem(MLOG_KEY); } catch {}
  mlogRender([]);
}
// msg 里带玩法名（如「[竞技场] …」），直接落到比赛页右栏
function mlog(msg, cls = "info") {
  const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const entries = mlogLoad();
  entries.push({ ts, msg, cls });
  mlogSave(entries);
  mlogRender(entries);
}
function mlogHistory() {
  mlogRender(mlogLoad());
}
// 比赛页读接口：成功静默（60s 轮询会刷屏），只有失败才落一条，避免日志里全是「读取成功」
function mlogFail(name, r) {
  const msg = (r && (r.message || r.msg)) || "网络异常";
  mlog("[" + name + "] 读取失败：" + msg, "err");
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
  window.__myUin = String(uin || "");   // 每日比赛 PVP 榜用于标出「我」那一行
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
  $("pane-spirite").classList.toggle("hidden", pane !== "spirite");
  $("pane-travel").classList.toggle("hidden", pane !== "travel");
  $("pane-dig").classList.toggle("hidden", pane !== "dig");
  $("pane-match").classList.toggle("hidden", pane !== "match");
  const inOverview = pane === "overview";
  $("refresh-btn").classList.toggle("hidden", !inOverview);
  $("auto-refresh").classList.toggle("hidden", !inOverview);
  if (pane === "overview") { ovLoadProps(); initLotteryOnce(); }
  if (pane === "lib") initLibOnce();
  if (pane === "cardbox") initCardBoxOnce();
  if (pane === "auto") initAutoOnce();
  if (pane === "exchange") initExchangeOnce();
  if (pane === "spirite") { initSpiriteOnce(); spStartLive(); }
  else spStopLive();
  if (pane === "travel") initTravelOnce();
  else travelStopAuto();
  if (pane === "dig") initDigOnce();
  else digStopPoll();
  if (pane === "match") { initMatchOnce(); initHomeOnce(); }
  else matchStopPoll();
}
document.querySelectorAll(".subnav .btn").forEach((b) => {
  b.onclick = () => switchPane(b.dataset.pane);
});

// ---------- 猫咪旅行（独立玩法页，2026-09-20） ----------
let travelInited = false;
let travelAutoTimer = null;

// 每日任务名（来自 travel_day_task XML，实证）
const TRAVEL_TASK_NAMES = {
  1: "猫咪旅行次数", 2: "猫咪旅行次数", 3: "商城兑换次数", 4: "商城兑换次数",
  5: "给好友浇水次数", 6: "给好友浇水次数", 7: "收获三叶草次数", 8: "使用兑换旅行次数",
};

function travelStatusText(c) {
  const now = Math.floor(Date.now() / 1000);
  if (c.status === "traveling") return `旅行中 · 剩 ${fmtLeft(Math.max(0, c.travel_end_ts - now))}`;
  if (c.status === "returned") return "已返回 · 待收获";
  return "空闲 · 可出发";
}

function renderTravelState(r) {
  const elves = r.elves || [];
  const names = r.food_names || {};
  const traveling = elves.filter((c) => c.status === "traveling").length;
  const idle = elves.filter((c) => c.status === "idle").length;
  const returned = elves.filter((c) => c.status === "returned").length;
  // ⚠️ 口径（2026-09-20 定案，三层证据）：main.free_cnt = 今日**已用**次数（不是剩余）。
  //    ① HAR 里每次 act=5 出发 free_cnt +1；② 每日任务 index1「猫咪旅行次数」进度 == free_cnt；
  //    ③ 好友 327904123 满额帧：四猫 travel_limit_cnt 各 3、free_cnt=12 → 上限 = 猫数 × 3。
  //    每猫每日 3 次（travel_limit_cnt），剩余 = Σ max(0, 3 − 该猫已旅行次数)。
  //    ⚠️ travel_house.travel_cnt_limit=8 不是这个上限（早期误判）。另有 兑换(dui_cnt)/付费(pay_cnt) 两池。
  const used = r.free_used != null ? r.free_used : (r.free_cnt || 0);
  const limit = r.free_limit || 12;
  const perCap = r.free_per_cat_limit || 3;
  const left = r.free_left != null ? r.free_left : Math.max(0, limit - used);
  $("travel-cnt").textContent = `免费旅行次数 剩 ${left}（今日已旅行 ${used}/${limit}） · 兑换 ${r.dui_cnt ?? "?"} · 付费 ${r.pay_cnt} · 在旅行 ${traveling} · 空闲 ${idle}`;
  if (!elves.length) {
    $("travel-state").innerHTML = '<div class="empty-tip">未读到猫咪数据。</div>';
    return;
  }
  const aGrade = r.a_grade_foods || [];
  const owned = r.food_owned || {};
  let html = `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;font-size:12px">
    <span class="chip">三叶草 ${r.clover_num}</span>
    <span class="chip">粉末 ${r.fenmo_num}</span>
    <span class="chip" title="免费旅行次数 = 每日上限（猫数×3，4 猫=12） − 今日已旅行次数(main.free_cnt)。免费次数次日清零。每猫每日上限 ${perCap} 次。">免费旅行次数 剩 ${left}（今日已旅行 ${used}/${limit}）</span>
    <span class="chip" title="main.dui_cnt：兑换旅行次数（游戏面板「兑换旅行次数：」，用三叶草/积分兑换得来，不清零）">兑换旅行次数 ${r.dui_cnt ?? "?"}</span>
    <span class="chip" title="main.pay_cnt：付费次数（游戏面板付费次数，不清零）">付费次数 ${r.pay_cnt}</span>
    <span class="chip" title="travel_watered_arr：今日已浇过水的好友">今日已浇 ${r.watered_today ?? 0}${r.water_open != null ? ` / 已开通 ${r.water_open}` : ""}</span>
    <span class="chip" title="可浇 = 已开通 + 已种三叶草且未成熟 + 今日未浇过">可浇好友 ${(r.water_targets || []).length}</span>
    ${returned ? `<span class="chip" style="border-color:#e8a33d;color:#b8792a">待收获 ${returned}</span>` : ""}
  </div>`;

  if (aGrade.length) {
    html += `<div style="margin-bottom:12px;font-size:12px">
      <div style="color:#556;margin-bottom:5px">A 级食物（每只猫自动分配其一，不足自动购买）</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">`;
    for (const fid of aGrade) {
      const nm = names[fid] || `食物${fid}`;
      html += `<span class="chip" style="border-color:#12b7f5">${esc(nm)} ×${owned[fid] || 0}</span>`;
    }
    html += `</div></div>`;
  }

  html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">';
  for (const c of elves) {
    const border = c.status === "traveling" ? "#12b7f5" : (c.status === "returned" ? "#e8a33d" : "#b6c0cc");
    const fname = c.food_id ? (names[c.food_id] || `食物${c.food_id}`) : "—";
    // 每猫今日已旅行次数 / 上限（travel_limit_cnt，上限 3）——到顶的猫本轮不会再出发
    const pc = c.travel_limit_cnt != null ? Number(c.travel_limit_cnt) : null;
    const capFull = pc != null && pc >= perCap;
    const capCol = capFull ? "#c92a2a" : "#889";
    html += `<div style="border:1px solid ${border};border-radius:10px;padding:10px 12px;background:#fff">
      <div style="font-weight:600;margin-bottom:4px">猫咪 ${c.elf_id}</div>
      <div style="font-size:12px;color:#556">${travelStatusText(c)}</div>
      <div style="font-size:11px;color:#889;margin-top:4px">食物 ${esc(fname)} ×${c.food_num || 0}</div>
      ${pc != null ? `<div style="font-size:11px;color:${capCol};margin-top:2px" title="travel_limit_cnt：该猫今日已旅行次数，每猫每日上限 ${perCap} 次">今日 ${pc}/${perCap} 次${capFull ? " · 已到顶" : ""}</div>` : ""}
    </div>`;
  }
  html += "</div>";

  const tasks = r.tasks || [];
  if (tasks.length) {
    html += `<div style="margin-top:14px;font-size:12px">
      <div style="color:#556;margin-bottom:5px">每日任务（非花钱项自动做 + 领）</div>
      <div style="display:flex;flex-direction:column;gap:3px">`;
    for (const t of tasks) {
      const nm = TRAVEL_TASK_NAMES[t.index] || `任务${t.index}`;
      const col = t.claimable ? "#2f9e44" : (t.claimed ? "#9aa6b2" : "#889");
      const tail = t.claimed ? "已领" : (t.claimable ? `${t.cnt}/${t.limit} 可领` : `${t.cnt}/${t.limit}`);
      html += `<div style="display:flex;justify-content:space-between;color:${col}">
        <span>#${t.index} ${esc(nm)}</span>
        <span>${tail}</span></div>`;
    }
    html += `</div></div>`;
  }

  $("travel-state").innerHTML = html;
}

async function loadTravelState() {
  const box = $("travel-state");
  if (!box) return;
  try {
    const r = await fetch("/api/travel/state").then((x) => x.json());
    if (!r.ok) { box.innerHTML = `<div class="empty-tip">读取失败：${esc(r.message || "未知错误")}</div>`; return; }
    renderTravelState(r);
  } catch (e) {
    box.innerHTML = `<div class="empty-tip">请求异常：${esc(e.message)}</div>`;
  }
}

async function travelRun(harvestOnly) {
  const dry = $("travel-dryrun").checked;
  const foodNum = parseInt($("travel-foodnum").value, 10) || 4;
  const restore = busyBtn($("travel-run"), "运行中…");
  try {
    const r = await fetch("/api/travel/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        food_num: foodNum, harvest_only: !!harvestOnly, dry_run: dry,
        do_clover: true, do_water: true, do_tasks: true,
      }),
    }).then((x) => x.json());
    if (!r.ok) { log(`[旅行] 运行失败：${r.message || "未知"}`, "err"); return; }
    for (const it of (r.items || [])) log(`[旅行] ${it.text}`, it.ok ? "ok" : "err");
    if (r.dry_run) log("[旅行] 模拟运行，未发真实请求。", "info");
  } catch (e) {
    log(`[旅行] 请求异常：${esc(e.message)}`, "err");
  } finally {
    restore();
    await loadTravelState();
  }
}

async function travelClover() {
  const restore = busyBtn($("travel-clover"), "收三叶草中…");
  try {
    const r = await fetch("/api/travel/clover", { method: "POST" })
      .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    const busy = (r.message || "").includes("busy");
    if (r.ok) log(`[旅行] 收三叶草：成功（现 ${r.clover_num ?? "?"}）`, "ok");
    else if (busy) log("[旅行] 收三叶草：暂无可收（无成熟三叶草）。", "info");
    else log(`[旅行] 收三叶草失败：${r.message || "未知"}`, "err");
    await loadTravelState();
  } finally {
    restore();
  }
}

async function travelWater() {
  const restore = busyBtn($("travel-water"), "浇水中…");
  try {
    const r = await fetch("/api/travel/water", { method: "POST" })
      .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    if (r.opened === undefined) {
      log(`[旅行] 去浇水：${r.message || "失败"}`, "err");
    } else if (!r.targets) {
      log(`[旅行] 去浇水：${r.message || "无可浇好友"}`, "info");
    } else {
      const extra = (r.skipped ? `，跳过 ${r.skipped} 个（好友已被浇满）` : "")
        + (r.stopped ? "（连续被拒，已收手）" : "");
      log(`[旅行] 去浇水：目标 ${r.targets} 个 → 成功 ${r.ok_cnt}、失败 ${r.fail}${extra}`
        + `｜已开通 ${r.opened} 个`, r.ok ? "ok" : "err");
      for (const x of (r.results || [])) {
        if (x.skipped) log(`[旅行]   · ${x.uin}：跳过（${x.reason}）`, "info");
      }
    }
  } finally {
    restore();
    await loadTravelState();
    await travelFriendsList(true);
  }
}

// 手动收获旅行（act=23）：领取所有已返回猫的礼物
async function travelHarvest() {
  const restore = busyBtn($("travel-harvest"), "收获中…");
  try {
    const r = await fetch("/api/travel/harvest", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    if (!r.items) { log(`[旅行] 收获旅行失败：${r.message || "未知"}`, "err"); return; }
    for (const it of r.items) log(`[旅行] ${it.text}`, it.ok ? "ok" : "err");
  } finally {
    restore();
    await loadTravelState();
  }
}

// 已开通好友列表（act=12 只读）：不是所有好友都开了猫咪旅行，这里只列开通的
let travelFriendsCache = null;

function renderTravelFriends() {
  const box = $("travel-friends-box"), cnt = $("travel-friends-cnt");
  if (!box) return;
  const r = travelFriendsCache;
  if (!r) { if (cnt) cnt.textContent = ""; return; }
  if (!r.ok) {
    box.innerHTML = `<div style="font-size:12px;color:#c92a2a">拉取失败：${esc(r.message || "未知")}</div>`;
    return;
  }
  if (cnt) cnt.textContent = `已开通 ${r.count} · 可浇 ${r.waterable ?? 0} · 今日已浇 ${r.watered_today}`;
  const ST = {
    waterable: { col: "#2f9e44", text: "可浇" },
    watered: { col: "#12b7f5", text: "今日已浇" },
    no_clover: { col: "#9aa6b2", text: "未种三叶草" },
    ripe: { col: "#e8a33d", text: "已成熟" },
    self: { col: "#c8d0d8", text: "我自己" },
  };
  const fmt = (ts) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} `
      + `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };
  let html = `<div style="font-size:12px;color:#556;margin-bottom:6px">
    已开通 <b>${r.count}</b> 个 · 可浇 <b>${r.waterable ?? 0}</b> 个
    （规则：已种三叶草且<b>未成熟</b>，且今日未浇过）</div>
    <div style="display:flex;flex-wrap:wrap;gap:5px">`;
  for (const f of (r.friends || [])) {
    const st = ST[f.status] || { col: "#889", text: f.status || "" };
    const tip = f.status === "waterable"
      ? `可浇 · 三叶草成熟于 ${fmt(f.ripe_ts)}`
      : (f.reason || st.text) + (f.ripe_ts ? ` · 成熟于 ${fmt(f.ripe_ts)}` : "");
    html += `<span class="chip" style="border-color:${st.col};color:${st.col};font-size:11px"
      title="${esc(tip)}">${esc(String(f.uin))} · ${esc(st.text)}</span>`;
  }
  html += "</div>";
  box.innerHTML = html;
}

async function travelFriendsList(silent) {
  const restore = busyBtn($("travel-friends"), "拉取中…");
  try {
    const r = await fetch("/api/travel/friends").then((x) => x.json())
      .catch((e) => ({ ok: false, message: e.message }));
    travelFriendsCache = r;
    renderTravelFriends();
    if (!silent) log(`[旅行] 好友列表：已开通 ${r.count ?? 0} 个，其中可浇 ${r.waterable ?? 0} 个（已种未成熟 · 今日未浇）。`, r.ok ? "ok" : "err");
  } finally {
    restore();
  }
}

async function travelTasks() {
  const restore = busyBtn($("travel-tasks"), "领任务中…");
  try {
    const r = await fetch("/api/travel/tasks", { method: "POST" })
      .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    if (!r.ok) { log(`[旅行] 领每日任务：${r.message || "失败"}`, "err"); }
    else if (!(r.claimed || []).length) { log("[旅行] 领每日任务：暂无可领（未完成）。", "info"); }
    else for (const c of r.claimed) log(`[旅行] 领每日任务#${c.index}：${c.ok ? "成功" : (c.message || "失败")}`, c.ok ? "ok" : "err");
    await loadTravelState();
  } finally {
    restore();
  }
}

function travelStartAuto() {
  if (travelAutoTimer) return;
  const sec = parseInt($("travel-interval").value, 10) || 600;
  travelAutoTimer = setInterval(() => travelRun(false), sec * 1000);
  $("travel-auto-start").classList.add("hidden");
  $("travel-auto-stop").classList.remove("hidden");
  log(`[旅行] 自动循环已启动（每 ${sec} 秒一轮：收获+免费出发+三叶草+浇水+每日任务）。`, "ok");
  travelRun(false);
}

function travelStopAuto() {
  if (travelAutoTimer) { clearInterval(travelAutoTimer); travelAutoTimer = null; }
  const s = $("travel-auto-start"), st = $("travel-auto-stop");
  if (s) s.classList.remove("hidden");
  if (st) st.classList.add("hidden");
  log("[旅行] 自动循环已停止。", "info");
}

function initTravelOnce() {
  if (travelInited) { loadTravelState(); return; }
  travelInited = true;
  $("travel-run").onclick = () => travelRun($("travel-harvestonly").checked);
  $("travel-harvest").onclick = travelHarvest;
  $("travel-clover").onclick = travelClover;
  $("travel-water").onclick = travelWater;
  $("travel-tasks").onclick = travelTasks;
  $("travel-friends").onclick = () => travelFriendsList(false);
  $("travel-friends-reload").onclick = () => travelFriendsList(false);
  $("travel-refresh").onclick = loadTravelState;
  $("travel-auto-start").onclick = travelStartAuto;
  $("travel-auto-stop").onclick = travelStopAuto;
  $("travel-clearlog").onclick = () => { logClear(); };
  loadTravelState();
  renderTravelFriends();
}

// ---------- 挖地（card_user_dig，独立玩法页，2026-09-20） ----------
// 端点语义：act=1 读网格 / act=2 挖格子(line,col,dig_type) / act=18 一键收获 / act=7 收矿机。
// dig_type：1=锄头(挖土 type2 / 石 type3)　2=一字型炸药　3=九宫格炸药（2/3 只能扔在已挖开的空格 type0 上）
// type：0=空地 1=特殊 2=土 3=石 4=已挖出的礼物(待收) 5=岩石(不可挖)
let digInited = false;
let digPollTimer = null;
let digBusy = false;
let digAutoRunning = false;

const DIG_TYPE_LABEL = { 0: "空地", 1: "特殊", 2: "土", 3: "石", 4: "半开", 5: "岩石" };
const DIG_TOOL_NAME = { 1: "锄头", 2: "一字型炸药", 3: "九宫格炸药", 4: "超级炸药" };

const DIG_TYPE_COLOR = { 0: "#f4f7fa", 2: "#e3c08a", 3: "#d3dae2", 4: "#fff3bf", 5: "#6b7480" };

function digLegendHtml() {
  const L = [[0, "空地"], [2, "土·可挖"], [3, "石·要挖2次"], [4, "半开·再挖"], [5, "岩石·不可挖"]];
  return `<div class="dg-legend">图例：`
    + L.map(([t, n]) => `<span><i style="background:${DIG_TYPE_COLOR[t]}"></i>${n}</span>`).join("")
    + `<span><i style="background:#ffd6d6;border-color:#e05a5a"></i>超级炸药格（点它就地引爆：菱形，横/纵被石头挡住、斜角能炸石头）</span>`
    + `<span style="color:#98a4b2">虚线格 = 未探索</span></div>`;
}

function digTool() { return parseInt(($("dig-tool") || {}).value || "1", 10) || 1; }

// 当前工具下「这一格能不能点」：锄头点土/石，炸药点已挖开的空格，超级炸药点「超级炸药格」
function digCellActive(c, tool) {
  if (c.super_tnt) return tool === 4 ? "dig" : "";   // 超级炸药是格子属性，只能用 dig_type=4 点它
  if (c.pending) return "pick";                 // 不管什么工具，露出的礼物都能收
  if (tool === 1) return c.diggable ? "dig" : "";
  if (tool === 4) return "";                    // 没有炸药格的格子，超级炸药无处可落
  return c.tntable ? "dig" : "";                // 2/3 炸药：落点必须是已挖开的空格
}

function renderDigGrid(st) {
  const box = $("dig-grid");
  const cnt = $("dig-cnt");
  if (!box || !st) return;
  const tool = digTool();
  const toolName = DIG_TOOL_NAME[tool] || "锄头";
  if (cnt) cnt.textContent = `第 ${st.dig_index ?? "?"} 层 · 可挖 ${st.diggable_cnt ?? 0} · 待收 ${st.pending_cnt ?? 0}`;
  // 游戏里只显示 7×7：服务端窗口是 8 行，最顶那行是已挖穿的空行（游戏不显示）→ 取最后 7 行
  let rows = st.rows || [];
  if (rows.length > 7) rows = rows.slice(rows.length - 7);
  if (!rows.length) {
    box.innerHTML = `<div class="empty-tip">没有地层数据。</div>`;
    return;
  }
  let html = digLegendHtml()
    + `<div class="dg-hint">当前工具：<b>${toolName}</b> —— `
    + (tool === 1 ? "点<b>土/石</b>格子开挖" : "点<b>已挖开的空格</b>落炸药（范围内要有土/石，否则服务端拒）")
    + `</div><div class="dg-wrap">`;
  for (const r of rows) {
    html += `<div class="dg-row"><div class="dg-ln">${r.line}</div><div class="dg-cells">`;
    for (const c of r.cells) {
      const act = digCellActive(c, tool);
      const cls = ["dg-cell", `t${c.type}`];
      if (act === "dig") cls.push("dig");
      if (act === "pick") cls.push("pick");
      if (c.super_tnt) cls.push("super");
      if (!c.known) cls.push("fog");
      const tip = `${r.line} 行 ${c.j} 列 · ${DIG_TYPE_LABEL[c.type] || ("type" + c.type)}`
        + (c.super_tnt ? " · 超级炸药格（就地点燃：菱形，上下左右各2格·遇石头被挡；斜角1格·能炸石头）" : "")
        + (c.gift_name ? ` · ${c.gift_name}${c.gift_num ? "×" + c.gift_num : ""}` : "")
        + (act === "dig" ? `（点击用${toolName}）` : (act === "pick" ? "（点击收获）" : ""));
      const main = c.super_tnt ? "💣超级炸药"
        : (c.gift_name || (DIG_TYPE_LABEL[c.type] || ("#" + c.type)));
      const sub = c.super_tnt ? "就地引爆"
        : (c.gift_name
          ? (c.pending ? "待收" : (c.gift_num ? "×" + c.gift_num : DIG_TYPE_LABEL[c.type] || ""))
          : "");
      html += `<div class="${cls.join(" ")}" data-line="${r.line}" data-col="${c.j}" title="${esc(tip)}">`
        + `<span class="gt">${esc(main)}</span>`
        + (sub ? `<span class="tt">${esc(sub)}</span>` : "")
        + `</div>`;
    }
    html += `</div></div>`;
  }
  html += `</div>`;
  box.innerHTML = html;
  box.querySelectorAll(".dg-cell.dig").forEach((el) => {
    el.onclick = () => digClick(parseInt(el.dataset.line, 10), parseInt(el.dataset.col, 10), el);
  });
  box.querySelectorAll(".dg-cell.pick").forEach((el) => {
    el.onclick = () => digCollect();
  });
  // 地图跟着最深一行走：渲染完滚到底部（放进下一帧，等布局算完）
  requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
}

// ---------- 鉴定机（jd_macs，act=4 投石 / act=7 收获） ----------
const DIG_STONE_COLOR = ["#2f7cf6", "#2fa84f", "#e03131"];

function renderDigJd(st) {
  const box = $("dig-jd");
  if (!box || !st) return;
  const jd = st.jd || [];
  if (!jd.length) { box.innerHTML = `<div class="empty-tip">没有鉴定机数据。</div>`; return; }
  let html = "";
  for (const m of jd) {
    if ((m.lv || 0) <= 0) continue;
    html += `<div class="jd-m"><div class="jd-h">${m.machine} 号鉴定机 <span class="tt">Lv${m.lv}</span>`
      + `<span class="tt">空槽 ${m.free}</span><span class="tt">待收 ${m.ready}</span></div><div class="jd-slots">`;
    for (const s of m.slots) {
      const col = DIG_STONE_COLOR[s.jd_type] || "#98a4b2";
      const cls = s.state === "待收" ? "ready" : (s.state === "空" ? "free" : "run");
      html += `<div class="jd-s ${cls}" title="${esc(s.stone + " · " + s.state)}">`
        + `<span class="st" style="background:${col}">${esc(s.stone)}</span>`
        + `<span class="sd">${s.state === "鉴定中" ? fmtDur(s.left) : s.state}</span></div>`;
    }
    html += `</div><div class="jd-acts">`;
    for (let i = 0; i < 3; i++) {
      html += `<button class="btn xs" style="border-color:${DIG_STONE_COLOR[i]};color:${DIG_STONE_COLOR[i]}" `
        + `data-jd-machine="${m.machine}" data-jd-stone="${i}">投${["蓝", "绿", "红"][i]}</button>`;
    }
    html += `<button class="btn xs ok" data-jd-collect="${m.machine}">收获</button></div></div>`;
  }
  box.innerHTML = html || `<div class="empty-tip">还没有可用的鉴定机（需要先升级/租用）。</div>`;
  box.querySelectorAll("[data-jd-stone]").forEach((el) => {
    el.onclick = () => digJdStart(el, parseInt(el.dataset.jdMachine, 10), parseInt(el.dataset.jdStone, 10));
  });
  box.querySelectorAll("[data-jd-collect]").forEach((el) => {
    el.onclick = () => digJdCollect(parseInt(el.dataset.jdCollect, 10));
  });
}

// ---------- 道具箱（act=1 的 new_prop / new_card） ----------
function renderDigBag(st) {
  const box = $("dig-bag");
  if (!box || !st) return;
  const b = st.bag || {};
  const props = (b.props || []).filter((x) => x.num > 0);
  const cards = b.cards || [];
  const vt = b.vt || {};
  let html = `<div class="bg-line"><span class="k">背包</span><span class="v">${b.size ?? "?"} 格</span></div>`;
  html += `<div class="bg-sec">道具卡</div><div class="bg-items">`;
  html += props.length
    ? props.map((p) => `<span class="bg-i">${esc(p.name)}<b>${p.num}</b></span>`).join("")
    : `<span class="tt">无</span>`;
  html += `</div><div class="bg-sec">材料包</div><div class="bg-items">`
    + `<span class="bg-i">仓库小/中/大<b>${vt.cang?.small ?? "?"}/${vt.cang?.mid ?? "?"}/${vt.cang?.big ?? "?"}</b></span>`
    + `<span class="bg-i">升级小/中/大<b>${vt.up?.small ?? "?"}/${vt.up?.mid ?? "?"}/${vt.up?.big ?? "?"}</b></span>`
    + `</div>`;
  html += `<div class="bg-sec">挖地获得的卡 <span class="tt">${cards.length} 种</span></div><div class="bg-items">`;
  html += cards.length
    ? cards.slice(0, 60).map((c) => `<span class="bg-i" title="卡 ${c.id}">${esc(c.name)}<b>${c.num}</b></span>`).join("")
    : `<span class="tt">无</span>`;
  html += `</div>`;
  const un = st.unknown || [];
  if (un.length) {
    html += `<div class="bg-sec warn">未知道具（官方配置查不到，点它可以补名字）</div><div class="bg-items">`;
    html += un.map((k) => `<span class="bg-i unk" data-unk="${esc(k)}" title="点击补名字">${esc("#" + k)}</span>`).join("");
    html += `</div>`;
  }
  box.innerHTML = html;
  box.querySelectorAll("[data-unk]").forEach((el) => {
    el.onclick = () => digNameItem(el.dataset.unk);
  });
}

async function loadDigState(silent) {
  try {
    const r = await fetch("/api/dig/state").then((x) => x.json());
    if (!r.ok) {
      if (!silent) log(`[挖地] 读状态失败：${r.message || "未知"}`, "err");
      return null;
    }
    renderDigGrid(r);
    renderDigJd(r);
    renderDigBag(r);
    if ($("dig-res")) {
      $("dig-res").innerHTML = [
        `锄头 <b>${r.chutou ?? "?"}</b>`,
        `一字型炸药 <b>${r.line_tnt ?? "?"}</b>`,
        `九宫格炸药 <b>${r.jgg_tnt ?? "?"}</b>`,
        `矿石币 <b>${r.kuangshi_bi ?? "?"}</b>`,
        `碎金石 <b>${r.sui_jinshi ?? "?"}</b>`,
        `金币 <b>${r.cash ?? "?"}</b>`,
      ].map((s) => `<span class="chip" style="cursor:default;font-size:11.5px">${s}</span>`).join("");
    }
    const a = r.auto || {};
    // 自动跑起来后把轮询调密（3s），停了就回到 8s
    if (!!a.running !== digAutoRunning) {
      digAutoRunning = !!a.running;
      digStopPoll();
      digStartPoll();
    }
    const tag = $("dig-auto-tag");
    if (tag) {
      tag.textContent = a.running
        ? `自动挖中 ${a.digs}/${a.max_digs}（收获 ${a.collected}）`
        : (a.finished || "");
    }
    const s = $("dig-auto-start"), st = $("dig-auto-stop");
    if (s) s.classList.toggle("hidden", !!a.running);
    if (st) st.classList.toggle("hidden", !a.running);
    for (const m of (a.msgs || [])) {
      // 只在首次出现时写全局日志：用文本去重，避免轮询重复刷
      if (!window._digSeen) window._digSeen = new Set();
      const k = String(m.ts) + m.text;
      if (!window._digSeen.has(k)) { window._digSeen.add(k); log(`[挖地] ${m.text}`, m.kind || "info"); }
    }
    return r;
  } catch (e) {
    if (!silent) log(`[挖地] 请求异常：${esc(e.message)}`, "err");
    return null;
  }
}

// 手动按钮即时反馈：点击立即置「处理中」并禁用，动作完成后再恢复。
// 与定时刷新解耦——手动操作不等轮询，按下就有反应、完成就立即刷新相关界面。
function busyBtn(btn, busy, idle) {
  if (!btn) return () => {};
  const old = btn.textContent;
  btn.disabled = true;
  if (busy) btn.textContent = busy;
  return () => { btn.disabled = false; btn.textContent = idle || old; };
}

async function digClick(line, col, el) {
  if (digBusy) return;
  digBusy = true;
  if (el) el.classList.add("busy");
  try {
    const dt = parseInt($("dig-tool").value, 10) || 1;
    const r = await fetch("/api/dig/cell", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line, col, dig_type: dt }),
    }).then((x) => x.json());
    if (r.text) log(`[挖地] ${r.text}`, r.ok ? "ok" : "err");
    if (r.state) renderDigGrid(r.state);
    else await loadDigState(true);
  } catch (e) {
    log(`[挖地] 挖格子异常：${esc(e.message)}`, "err");
  } finally {
    digBusy = false;
    if (el) el.classList.remove("busy");
  }
}

async function digCollect() {
  const restore = busyBtn($("dig-collect"), "收获中…");
  try {
    const r = await fetch("/api/dig/collect", { method: "POST" })
      .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    if (r.ok) log(`[挖地] 一键收获：${r.text || "暂无可收"}`, "ok");
    else log(`[挖地] 一键收获失败：${r.message || "未知"}`, "err");
    await loadDigState(true);   // 立即刷新网格 + 道具箱（不依赖轮询）
  } finally {
    restore();
  }
}

async function digMachines() {
  const restore = busyBtn($("dig-machines"), "收鉴定中…");
  try {
    const r = await fetch("/api/dig/jd/collect", { method: "POST" })
      .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    if (r.count === 0) log(`[挖地] ${r.text || "鉴定机暂无到期产出"}`, "info");
    else {
      log(`[挖地] ${r.text || "收鉴定完成"}`, r.ok ? "ok" : "err");
      // 逐条展示本次产出：晶石鉴定**不一定得文物**，也可能得碎晶石等 → 分别标注
      const gains = (r.gains || []);
      if (gains.length) {
        const relicGain = gains.filter((g) => g.type === 76);
        const other = gains.filter((g) => g.type !== 76);
        if (relicGain.length)
          log(`[挖地] 文物（可一键上交）：` + relicGain.map((g) => `文物#${g.id}×${g.num}`).join("、"), "info");
        if (other.length)
          log(`[挖地] 其余产出（已入道具箱）：` +
            other.map((g) => `${g.name || ("#" + g.type + "_" + g.id)}×${g.num}`).join("、"), "info");
      }
    }
    renderDigRelics(r.relics || []);
    await loadDigState(true);   // 立即刷新：网格 + 道具箱（碎晶石等已入库）
  } finally {
    restore();
  }
}

async function digJdStart(btn, machine, stone) {
  const restore = busyBtn(btn, "投石中…");
  try {
    const r = await fetch("/api/dig/jd/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machine, stone }),
    }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    log(`[挖地] ${r.text || r.message || "投石失败"}`, r.ok ? "ok" : "err");
    await loadDigState(true);
  } finally {
    restore();
  }
}

async function digJdCollect(machine) {
  const r = await fetch("/api/dig/jd/collect", { method: "POST" })
    .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
  const items = (r.items || []).filter((x) => x.machine === machine);
  if (!items.length) log(`[挖地] ${r.text || "该机暂无到期产出"}`, "info");
  else log(`[挖地] ${machine} 号机收获：` + items.map((x) => x.text).join("、"), r.ok ? "ok" : "err");
  renderDigRelics(r.relics || []);
  await loadDigState(true);
}

async function digRelicSubmit(btn, id, num) {
  const restore = busyBtn(btn, "上交中…");
  try {
    const r = await fetch("/api/dig/relic/submit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: Number(id), num: Number(num) }),
    }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    const got = r.text || r.message || "无产出";
    log(`[挖地] 上交文物#${id}×${num} → ${got}`, r.ok ? "ok" : "err");
    await loadDigState(true);
    return r;
  } finally {
    restore();
  }
}

let digRelics = [];     // 本次页面会话内累计的文物（{id,num}），来自收鉴定产出

function renderDigRelics(relics) {
  const box = $("dig-relic-list");
  if (!box) return;
  // 合并新收获的文物（同 id 累加数量）
  for (const rl of (relics || [])) {
    const hit = digRelics.find((x) => x.id === rl.id);
    if (hit) hit.num += rl.num; else digRelics.push({ id: rl.id, num: rl.num });
  }
  digRelics = digRelics.filter((x) => x.num > 0);
  // 「一键上交全部」已改为 index.html 静态常驻（initDigOnce 绑定 digRelicSubmitAll），
  // 不依赖本次收获列表——刷新页面后仓库里剩的文物也能一键上交（后端自动探测 id 空间）。
  box.innerHTML = digRelics.length
    ? digRelics.map((rl) =>
        `<div class="rel-item"><span>文物 #${rl.id} ×${rl.num}</span>` +
        `<button class="btn xs ok" data-relic="${rl.id}" data-rnum="${rl.num}">上交</button></div>`).join("")
    : `<div class="rel-empty">本会话暂未收获文物；点上方「一键上交全部」可上交仓库里所有文物。</div>`;
  box.querySelectorAll("[data-relic]").forEach((el) => {
    el.onclick = () => digRelicSubmit(el, el.dataset.relic, el.dataset.rnum);
  });
}

async function digRelicSubmitAll() {
  const restore = busyBtn($("relic-all"), "上交中…");
  log("[挖地] 一键上交：自动探测仓库里所有文物并逐个上交（约 15-20s）…", "info");
  // 后端自适应探测（id 0,1,2,3 存在 → 连续 3 个不存在即停），不再盲目跑 34 套卡
  try {
    const r = await fetch("/api/dig/relic/submit_all", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ per_id_cap: 8 }),
    }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    if (!r || !r.ok) {
      log(`[挖地] 一键上交失败：${(r && r.message) || "未知"}`, "err");
      return;
    }
    for (const it of (r.items || [])) {
      log(`[挖地] 上交文物#${it.id}×${it.num}（对应套卡：${it.theme}）→ ${it.text}`, "ok");
      const hit = digRelics.find((x) => x.id === it.id);
      if (hit) hit.num = Math.max(0, hit.num - it.num);
    }
    digRelics = digRelics.filter((x) => x.num > 0);
    renderDigRelics([]);
    log(`[挖地] 一键上交完成：共上交 ${r.total || 0} 件（探测 ${r.tried || 0} 个 id）`, r.total ? "ok" : "warn");
    await loadDigState(true);
  } finally {
    restore();
  }
}

async function digJdAuto() {
  const restore = busyBtn($("dig-jd-auto"), "一轮中…");
  try {
    const r = await fetch("/api/dig/jd/auto", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    log(`[挖地] 鉴定机一轮：${r.text || r.message || ""}`, r.ok ? "ok" : "err");
    await loadDigState(true);
  } finally {
    restore();
  }
}

async function digPush() {
  const restore = busyBtn($("dig-push"), "推进中…");
  try {
    const r = await fetch("/api/dig/push", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dig_type: 2 }),
    }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    log(`[挖地] ${r.text || r.message || "推进失败"}`, r.ok ? "ok" : "err");
    await loadDigState(true);
  } finally {
    restore();
  }
}

async function digDaily(kind) {
  const url = kind === "tools" ? "/api/dig/daily/tools" : "/api/dig/daily/gift";
  const nm = kind === "tools" ? "每日道具" : "每日礼包";
  const btn = kind === "tools" ? $("dig-daily-tools") : $("dig-daily-gift");
  const restore = busyBtn(btn, "领取中…");
  try {
    const r = await fetch(url, { method: "POST" })
      .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    log(`[挖地] 领${nm}：${r.text || r.message || "无"}`, r.ok ? "ok" : "err");
    await loadDigState(true);
  } finally {
    restore();
  }
}

async function digNameItem(key) {
  const nm = prompt(`给 #${key} 补一个名字（必须是你在游戏里亲眼看到的名字，别猜）：`);
  if (!nm) return;
  const r = await fetch("/api/item/name", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, name: nm }),
  }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
  if (r.ok) { log(`[挖地] 已记住 #${key} = ${nm}`, "ok"); await loadDigState(true); }
  else log(`[挖地] 补名字失败：${r.message}`, "err");
}

async function digStartAuto() {
  const restore = busyBtn($("dig-auto-start"), "启动中…");
  const maxDigs = parseInt($("dig-max").value, 10) || 30;
  const useTnt = $("dig-tnt").checked;
  const marginEl = $("dig-margin");
  const clearMargin = marginEl ? (parseInt(marginEl.value, 10) || 0) : 1;
  try {
    const r = await fetch("/api/dig/auto/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_digs: maxDigs, use_tnt: useTnt, clear_margin: clearMargin }),
    }).then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    if (!r.ok) { log(`[挖地] 启动自动失败：${r.message || "未知"}`, "err"); restore(); return; }
    window._digSeen = new Set();
    log(`[挖地] 自动挖已启动：上限 ${maxDigs} 次，炸药${useTnt ? "开" : "关"}，`
      + `清账缓冲 ${clearMargin} 行。`, "ok");
    digAutoRunning = true;
    await loadDigState(true);
    restore();
  } catch (e) {
    log("自动挖启动异常：" + e, "err");
    restore();
  }
}

async function digStopAuto() {
  const restore = busyBtn($("dig-auto-stop"), "停止中…");
  try {
    const r = await fetch("/api/dig/auto/stop", { method: "POST" })
      .then((x) => x.json()).catch((e) => ({ ok: false, message: e.message }));
    log(`[挖地] ${r.message || "已停止"}`, "info");
    await loadDigState(true);
  } finally {
    restore();
  }
}

function digStartPoll() {
  if (digPollTimer) return;
  digPollTimer = setInterval(() => loadDigState(true), digAutoRunning ? 3000 : 8000);
}

function digStopPoll() {
  if (digPollTimer) { clearInterval(digPollTimer); digPollTimer = null; }
}

function initDigOnce() {
  if (!digInited) {
    digInited = true;
    $("dig-refresh").onclick = () => loadDigState(false);
    $("dig-collect").onclick = digCollect;
    $("dig-machines").onclick = digMachines;
    $("dig-auto-start").onclick = digStartAuto;
    $("dig-auto-stop").onclick = digStopAuto;
    if ($("dig-push")) $("dig-push").onclick = digPush;
    if ($("dig-jd-auto")) $("dig-jd-auto").onclick = digJdAuto;
    if ($("dig-daily-tools")) $("dig-daily-tools").onclick = () => digDaily("tools");
    if ($("dig-daily-gift")) $("dig-daily-gift").onclick = () => digDaily("gift");
    // 换工具要**立刻重画**网格：可点的格子集合随工具变（锄头点土/石，炸药点空格）
    if ($("dig-tool")) $("dig-tool").onchange = () => {
      fetch("/api/dig/state").then((x) => x.json()).then((r) => { if (r.ok) renderDigGrid(r); });
    };
    if ($("dig-clearlog")) $("dig-clearlog").onclick = () => logClear();
    if ($("relic-all")) $("relic-all").onclick = digRelicSubmitAll;
    renderDigRelics([]);   // 刷出空态提示（本会话暂无收获时也说明按钮用途）
  }
  loadDigState(false);
  digStartPoll();
}
// ---------- 游戏状态（总览） ----------
function fmtLeft(sec) {
  if (sec <= 0) return "已完成";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h > 0 ? `${h}小时${m}分` : (m > 0 ? `${m}分${s}秒` : `${s}秒`);
}
function chip(k, v, small = "", id = "") {
  return `<div class="stat-chip"${id ? ` id="${id}"` : ""}><span class="k">${k}</span><span class="v">${v}${small ? `<small>${small}</small>` : ""}</span></div>`;
}

// 炉位上的「取消 / 取卡」按钮。取消后素材卡会原样退回卡箱，不会消失。
async function onSlotAction(e) {
  const b = e.currentTarget, act = b.dataset.act, slot = b.dataset.slot;
  const isCancel = act === "cancel";
  if (!confirm(isCancel
      ? `取消炉位 ${slot} 的炼制？素材卡会退回卡箱。`
      : `取出炉位 ${slot} 已炼好的卡？`)) return;
  const restore = busyBtn(b, "处理中…");
  try {
    const url = isCancel ? `/api/card/cancel?slot=${slot}`
                         : `/api/refine/get?slotid=${slot}`;
    const d = await (await fetch(url, { method: isCancel ? "POST" : "GET" })).json();
    log(`${isCancel ? "取消炼卡" : "取卡"} 炉位 ${slot}：${d.ok ? "成功" : "失败 " + (d.message || d.code || "")}`,
        d.ok ? "ok" : "err");
    if (!isCancel && "pai" in d) {
      log(`翻牌（取卡附带）：${d.pai ? "成功" : "失败"} ${d.pai_message || ""}`,
          d.pai ? "ok" : "err");
    }
  } catch (err) {
    log("操作异常：" + err, "err");
  } finally {
    restore();
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
      chip("随机抽卡", `${u.randchance || 0} 次`).replace('class="stat-chip"', 'class="stat-chip" id="ov-randchip"') +
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
  const srcTids = exTargetTids();
  if (srcTids.length) body.tids = srcTids;
  const tcards = [...exState.pickCards].filter(Boolean);
  if (tcards.length) body.card_ids = tcards;
  const pay = exPaySelected();
  if (pay.length) body.pay_card_ids = pay;
  const b = $("ex-autoex-start");
  const bold = b ? b.textContent : "";
  if (b) { b.disabled = true; b.textContent = "启动中…"; }
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
    if (b) { b.disabled = false; b.textContent = bold; }
  }
}

async function exAutoStop() {
  const restore = busyBtn($("ex-autoex-stop"), "停止中…");
  try {
    await fetch("/api/exchange/auto/stop", { method: "POST" });
    exMsg("已请求停止自动换卡", "info");
  } finally {
    restore();
  }
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
    const restore = busyBtn($("ex-manual-save"), "保存中…");
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
    } finally {
      restore();
    }
  };;
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
        ? `<div class="sub"><span class="tag refinable" style="margin-right:4px">配方</span>${esc(matsWithCount(c.recipe.mats))}　耗时 ${fmtDur(c.recipe.secs)}</div>`
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
    // 套卡明细 = 该套的合成信息：按面值分层、每张卡只出现一次（不再逐卡铺「炼成 X ＝ …」，
    // 那样一张 10 面额素材卡会被重复写七八遍）。持有数/可炼/空炉 走 synthesis 接口。
    const anchor = d.cards.some((c) => c.id === Number(focusCid))
      ? Number(focusCid) : (d.cards[0] ? d.cards[0].id : 0);
    const syn = await (await fetch(`/api/card/synthesis?card_id=${anchor}`)).json();
    if (!syn.ok) throw new Error(syn.message || "合成信息加载失败");
    detail.innerHTML =
      `<div class="card-row"><div class="head">${esc(t.name)}　${stars(t.star)}
         ${kindTag(t.kind)}${attrTags(t)}
         <span class="meta" style="margin-left:auto;color:var(--muted);font-size:12px">共 ${t.n} 张卡 · 面值区间以卡为准</span></div>
       </div>` + renderSynTiers(syn);
    mountSynView(detail, syn, () => {
      const sel = detail.querySelector(".tchip.sel");
      return openTheme(tid, sel ? Number(sel.dataset.cid) : null);
    });
    if (focusCid) {
      const chip = detail.querySelector(`.tchip[data-cid="${Number(focusCid)}"]`);
      if (chip) chip.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  } catch (e) {
    detail.innerHTML = `<div class="empty-tip err">加载失败：${esc(e.message || e)}</div>`;
    log("加载套卡明细失败：" + (e.message || e), "err");
  }
}

// ===== 套卡合成信息：统一「按面值分层、每张卡只出现一次」=====
// 2026-09-19 用户拿游戏炼卡窗口做参照：那边每张卡只占一格（角标=持有数，灰=合成中）。
// 我们原来按「配方」铺 16 块，同一张材料卡在每一块里都出现一次，观感全是重复、也费空间。
// 现在只渲染一份全集清单（卡库明细 / 卡箱弹窗共用），材料通过「点卡 → 就地框出」按需看。
function renderSynTiers(d) {
  const th = d.theme || {};
  const tiers = d.tiers || [];
  const total = tiers.reduce((a, t) => a + t.cards.length, 0);
  let h = `<div class="synth-theme">📦 ${esc(th.name || "套卡")}（套卡#${th.id ?? ""}）· ${total} 张`
    + (d.free_stoves > 0 ? `<span class="tag ok">空炉 ${d.free_stoves}</span>`
                         : `<span class="tag no">炉位已满</span>`)
    + `</div>`;
  h += `<div class="synth-sub">按面值分层，每张卡只列一次（左上角数字＝你持有几张）。`
    + `点任意一张卡 → <b>它需要的下级材料就在上面直接框出来</b>：`
    + `<span class="lg lg-sel"></span>选中卡　<span class="lg lg-mat"></span>它需要的材料（同一种颜色）`
    + `。角标 <i class="mn">×2</i>＝需 2 张，<i class="mh ok">有</i>＝够、<i class="mh no">缺</i>＝不够。</div>`;
  if (!total) {
    h += `<div class="syn-empty">该套卡没有卡。</div>`;
    return h;
  }
  h += `<div class="tier-rows">`;
  for (const t of tiers) {
    h += `<div class="tier-row"><span class="tier-lab"><b>${t.price}</b>`
      + `<i>${t.secs ? fmtDur(t.secs) : "素材"}</i></span><span class="tier-cards">`
      + t.cards.map((c) => {
          const cls = c.craft ? "craft" : (!c.target ? "base" : (c.refinable ? "ok" : "no"));
          const st = c.craft ? "合成中" : (!c.target ? "基础素材卡" : (c.refinable ? "可炼" : "缺料"));
          return `<span class="tchip ${cls}" data-cid="${c.card_id}" title="${esc(c.name)} · ${st}">`
            + `<i class="q">${c.own}</i>${esc(c.name)}</span>`;
        }).join("")
      + `</span></div>`;
  }
  h += `</div><div class="syn-act"></div>`;
  return h;
}

// 挂载：绑定点卡（选中 → 高亮它的下级材料 + 单行说明）。
// rerender 供买卡/炼卡后刷新当前视图（卡库明细重载 / 弹窗重开）。
function mountSynView(root, d, rerender) {
  root.__syn = d;
  for (const chip of root.querySelectorAll(".tchip")) {
    chip.onclick = (ev) => { ev.stopPropagation(); synSelect(root, Number(chip.dataset.cid), rerender); };
  }
  synSelect(root, d.clicked_card_id, rerender);   // 默认选中你点开的那张卡
}

// 选中卡与「它需要的材料」用两种颜色区分；材料本身**统一一种颜色**（不再按配方顺序分色）
const SYN_MAT_CLS = "mat";

function synSelect(root, cid, rerender) {
  const d = root.__syn || {};
  const flat = [];
  for (const t of (d.tiers || [])) for (const c of t.cards) flat.push(c);
  const me = flat.find((c) => c.card_id === Number(cid));
  // 先清掉上一次的标注（框色 + 注入的角标/买按钮），避免叠加
  for (const c of root.querySelectorAll(".tchip")) {
    c.classList.remove("sel", "pframe", SYN_MAT_CLS);
    for (const el of c.querySelectorAll(".inj")) el.remove();
  }
  const act = root.querySelector(".syn-act");
  if (!act) return;
  if (!me) { act.innerHTML = `<span class="muted">点上方任意一张卡 → 它的下级材料会在上面直接框出</span>`; return; }
  const selEl = root.querySelector(`.tchip[data-cid="${me.card_id}"]`);
  if (selEl) selEl.classList.add("sel");

  // 基础素材卡：无配方。把「用到它的父卡」在上方框出来（虚框），说明文字留一行。
  if (!me.target) {
    const parents = flat.filter((c) => (c.mats || []).some((m) => m.card_id === me.card_id));
    for (const p of parents) {
      const el = root.querySelector(`.tchip[data-cid="${p.card_id}"]`);
      if (el) el.classList.add("pframe");
    }
    act.innerHTML = `<span class="who">${esc(me.name)}</span>`
      + `<span class="muted">基础素材卡（无配方，靠抽卡 / 商店 / 活动获得）· 持有 ${me.own}</span>`
      + (parents.length
          ? `<span class="muted">· 被 ${parents.length} 个配方用作素材（上方虚框）</span>`
          : `<span class="muted">· 当前没有被任何配方用到</span>`);
    return;
  }

  // 有配方：3 张下级材料**就在上面的分层清单里直接框出来**（同一种颜色，不再另外列一行）
  const outside = [];   // 兜底：万一有材料不属于本套卡（清单里没有那一格），仍要看得见
  me.mats.forEach((m) => {
    const el = root.querySelector(`.tchip[data-cid="${m.card_id}"]`);
    if (!el) { outside.push(m); return; }
    el.classList.add(SYN_MAT_CLS);
    if (m.need > 1) {
      const mn = document.createElement("i");
      mn.className = "inj mn";
      mn.textContent = "×" + m.need;
      el.appendChild(mn);
    }
    const mh = document.createElement("i");
    mh.className = "inj mh " + (m.owned ? "ok" : "no");
    mh.textContent = m.owned ? "有" + m.count : "缺" + m.lack;
    el.appendChild(mh);
    if (m.buyable) {
      const b = document.createElement("button");
      b.className = "inj mini-btn buy-btn syn-buy";
      b.dataset.cid = m.card_id;
      b.dataset.tid = d.theme ? d.theme.id : "";
      b.textContent = "买";
      b.title = "这张是未下架在售的 10 面额卡，可买一张补齐";
      el.appendChild(b);
    }
  });

  act.innerHTML = `<span class="muted">已选</span><span class="who">${esc(me.name)}</span>`
    + `<span class="muted">耗时 ${fmtDur(me.secs)} · 持有 ${me.own}</span>`
    + outside.map((m) => `<span class="tchip ${SYN_MAT_CLS}" style="cursor:default">`
        + `${esc(m.name)}`
        + (m.need > 1 ? `<i class="mn">×${m.need}</i>` : "")
        + `<i class="mh ${m.owned ? "ok" : "no"}">${m.owned ? "有" + m.count : "缺" + m.lack}</i>`
        + `</span>`).join("")
    + (me.refinable
        ? (d.free_stoves > 0
            ? `<button class="btn sm primary syn-refine" data-tid="${me.card_id}">炼卡</button>`
            : `<button class="btn sm primary syn-refine" data-tid="${me.card_id}" disabled title="当前炉位已满，腾出空炉后即可炼">炉满</button>`)
        : `<span class="tag no">缺料</span>`);
  for (const b of act.querySelectorAll(".syn-refine")) {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      await doRefine(Number(b.dataset.tid), b);
      if (rerender) await rerender();
    };
  }
  // 「买」按钮在材料格里，点它不能触发选中
  for (const b of root.querySelectorAll(".syn-buy")) {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      const ok = await doBuy(Number(b.dataset.cid), Number(b.dataset.tid), b);
      if (ok) { await loadCardBox(); if (rerender) await rerender(); }
    };
  }
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
  $("auto-clearlog").onclick = () => {
    $("auto-log").innerHTML = `<div class="empty-tip">日志已清空（下次「启动」自动恢复记录）。</div>`;
    autoLogCleared = true;
  };
  $("auto-theme").onchange = () => { $("auto-tid").value = ""; autoRefresh(); };
  $("auto-tid").oninput = () => { $("auto-theme").value = ""; };
  if ($("auto-queue-add")) $("auto-queue-add").onclick = autoQueueAdd;
  if ($("auto-queue-tid")) $("auto-queue-tid").onkeydown = (e) => {
    if (e.key === "Enter") autoQueueAdd();
  };
  // 偷炉好友下拉：选一个就填进输入框，输入框仍可手动改（如填不在好友列表里的 QQ）
  const pick = $("auto-steal-pick");
  if (pick) {
    pick.onchange = () => { $("auto-steal-friend").value = pick.value || ""; };
    $("auto-steal-friend").oninput = () => { pick.value = ""; };
  }
  loadAutoOptions();
  loadStealFriends();
  autoRefresh();
}

// 运行中往队列追加一套卡：长炉时（如魏书 10h）炉位不闲着，可以同时推进下一套
async function autoQueueAdd() {
  const tid = ($("auto-queue-tid").value || "").trim();
  if (!tid) { log("追加队列：请填套卡 ID", "err"); return; }
  const restore = busyBtn($("auto-queue-add"), "追加中…");
  try {
    const d = await (await fetch(`/api/auto/queue/add?tid=${encodeURIComponent(tid)}`, { method: "POST" })).json();
    if (d.ok) {
      log(`已加入队列：${d.plan.name}（队列 ${(d.queue || []).length} 套）`, "ok");
      $("auto-queue-tid").value = "";
    } else {
      log(`加入队列失败：${d.message || ""}`, "err");
    }
  } catch (e) { log("加入队列异常：" + e, "err"); }
  finally { restore(); }
  autoRefresh();
}

// 偷炉「常去好友」下拉：从好友列表（card_user_list，194 人）里选，选完填进右边的输入框。
// 按经验值倒序 —— 高等级好友更可能有炉且长期在线；自己会被后端排除。
let stealFriendsLoaded = false;
async function loadStealFriends() {
  const sel = $("auto-steal-pick");
  if (!sel) return;
  if (stealFriendsLoaded) return;
  try {
    const r = await (await fetch("/api/players", { method: "POST" })).json();
    const rows = (r.players || []).slice().sort((a, b) => (b.exp || 0) - (a.exp || 0));
    sel.innerHTML = `<option value="">不指定（自动挑）</option>` + rows.map((p) =>
      `<option value="${p.uin}">${esc(p.nick || p.uin)}（${p.uin}）</option>`).join("");
    stealFriendsLoaded = true;
    if ($("auto-steal-friend").value.trim()) sel.value = $("auto-steal-friend").value.trim();
    log(`偷炉好友列表已加载：${rows.length} 人`, "ok");
  } catch (e) {
    log("加载好友列表失败：" + e, "err");
  }
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
  const steal = $("auto-steal").checked ? 1 : 0;
  const sf = ($("auto-steal-friend").value || "").trim();
  const gd = $("auto-gift-drain").checked ? 1 : 0;
  // 变卡目标改成自动（用户 2026-09-26 定调「不要指定，缺哪个变哪个」）：
  // 传 0 = 让后端按「在炼套卡里缺的那张 40 面卡」挑，写死某个卡会变出用不上的废卡。
  const gdst = 0;
  const gup = parseInt($("auto-gift-upper").value, 10) || 45;
  const glow = parseInt($("auto-gift-lower").value, 10) || 25;
  const b = $("auto-start");
  const bold = b ? b.textContent : "";
  if (b) { b.disabled = true; b.textContent = "启动中…"; }
  try {
    const qs = `tid=${encodeURIComponent(tid)}&buy=${buy}&rounds=${rounds}&steal=${steal}`
      + (sf ? `&steal_friend=${encodeURIComponent(sf)}` : "")
      + `&gift_drain=${gd}&gift_dst=${gdst}&gift_upper=${gup}&gift_lower=${glow}`;
    const d = await (await fetch(`/api/auto/start?${qs}`, { method: "POST" })).json();
    if (d.ok) {
      log(`自动炼卡启动：${d.plan.name}（${d.plan.base_cards} 张底卡 / 集齐 1 轮需 ${d.plan.need_slots} 格）`, "ok");
      autoLogCleared = false;
      startAutoPoll();
    } else {
      log(`自动炼卡未启动：${d.message || ""}`, "err");
    }
  } catch (e) { log("自动炼卡启动异常：" + e, "err"); }
  finally {
    if (b) { b.disabled = false; b.textContent = bold; }
  }
  autoRefresh();
}

async function autoStop() {
  const restore = busyBtn($("auto-stop"), "停止中…");
  try {
    const d = await (await fetch("/api/auto/stop", { method: "POST" })).json();
    log("已请求停止自动炼卡", "warn");
  } catch (e) { log("停止异常：" + e, "err"); }
  finally { restore(); }
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
  // 卡箱礼物卡：箱里已有的同套(487) 48 面卡就地变卡，不必先从礼物箱收了再变
  const bg = (st && st.box_gift) || null;
  if (bg) {
    const last = bg.last;
    // 目标不再手填：自动挑「在炼套卡里缺的那张 40 面卡」，这里把挑中的目标显示出来
    const tgt = bg.target
      ? esc(String(bg.target))
      : (bg.dst ? `#${esc(String(bg.dst))} ${esc(bg.dst_name || "")}` : "暂无（本轮不缺套内卡）");
    head.push(`<div class="state-box" style="margin-top:10px">` +
      stat("卡箱礼物卡", bg.enabled ? "优先变卡" : "关闭", "目标自动挑") +
      stat("自动变卡目标", tgt, bg.dst_face ? `${esc(String(bg.dst_face))} 面` : "") +
      stat("上次结果", last ? esc(String((last.last) || last.message || "")) : "还没执行") +
      (last && (last.tried || last.burned)
        ? stat("变卡次数", `尝试 ${last.tried || 0} 次`
            + (last.burned ? `、失败 ${last.burned} 张（失败也烧源卡，失败后会换下一张继续变）` : ""),
            "")
        : "") +
      `</div>`);
    const dstEl = $("auto-gift-dst");
    if (dstEl) {
      dstEl.textContent = bg.target
        ? `#${esc(String(bg.dst || ""))} ${esc(bg.dst_name || "")}（${esc(String(bg.dst_face || ""))}面）`
        : "暂无（本轮不缺套内卡）";
    }
    const gft = (st && st.gift) || null;
    if (gft) {
      head[head.length - 1] += stat("收礼变卡目标",
        gft.target ? esc(String(gft.target))
          : (gft.dst_name ? `${esc(gft.dst_name)}（${esc(gft.dst_theme || "")}）` : "暂无（本轮不缺套内卡）"),
        gft.enabled ? "" : "未勾选");
    }
    // 每套「40 面卡缺口」：这套从头炼到尾要几张 40 面，手上已有几张、还差多少。
    // 只变 40 面（礼物卡只有 48 面一种形态，变卡只产出 40 面），凑不齐整套就一直变。
    const gaps = (st && st.face40) || [];
    if (gaps.length) {
      const txt = gaps.map((g) => {
        const left = g.need_total || 0;
        const cr = g.credit || {};
        const n = Object.values(cr).reduce((a, b) => a + Number(b || 0), 0);
        // credit = 变卡环节接下来会交的那几张，先验进净需求 ——
        // 所以下面这一轮**不会再拿底卡去炼 40 面卡**（否则变卡就白变了）
        const tail = n ? `；变卡即将补 ${esc(String(n))} 张 → 这轮不再炼 40 面` : "";
        return `${esc(String(g.theme))} 40面 ${esc(String(g.have))}/${esc(String(g.have + left))}`
          + (left ? `（还差 ${esc(String(left))}）` : "（已够）") + tail;
      }).join(" · ");
      head[head.length - 1] += stat("40 面缺口", txt, "礼物卡只变 40 面");
    }
  }
  body.innerHTML = head.join("");
  renderAutoLog(st);
}

// 本次运行日志单独渲染到右栏（2026-09-19）：以前贴在状态卡片下面，队列/偷炉/空位
// 一多就把日志顶到折叠线以下，看一眼要滚到底。右栏常驻后与状态同时可见。
let autoLogCleared = false;
function renderAutoLog(st) {
  const box = $("auto-log");
  if (!box || !st || autoLogCleared) return;   // 没启动过就保留初始说明；点过「清空」不再回填
  const rows = st.log || [];
  if (!rows.length) {
    box.innerHTML = `<div class="empty-tip">暂无记录。</div>`;
    return;
  }
  box.innerHTML = `<div class="auto-log" style="max-height:none;overflow:visible">` + rows.map((l) => {
    const t = new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
    const okTxt = l.ok ? "✓" : "✗";
    return `<span class="at">[${t}]</span> ${okTxt} ${esc(l.action)}：${esc(String(l.detail))}`;
  }).reverse().join("\n") + `</div>`;
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
  // 先填充 cbState（卡箱两栏），再初始化变卡表单（表单依赖 cbState 的箱卡数据）
  (async () => {
    await loadCardBox();
    await initChangeForm();
  })();
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
  const restore = busyBtn(b, "处理中…");
  try {
    if (act === "sell") {
      const price = Number(b.dataset.price || 0);
      if (!confirm(`确定卖掉「${b.closest(".cb-card").querySelector(".cbn").textContent}」？可得 ${price} 金。`)) {
        restore(); return;
      }
      const bt = box === 0 ? 1 : 2;
      const d = await (await fetch(`/api/box/sell?card_id=${id}&slot=${slot}&box_type=${bt}`, { method: "POST" })).json();
      log(`卖卡 slot ${slot}（${box === 0 ? "换卡箱" : "保险箱"}）：${d.ok ? `成功 +${price} 金` : "失败 " + (d.message || d.code)}`,
          d.ok ? "ok" : "err");
    } else {
      const toName = box === 0 ? "保险箱" : "换卡箱";
      const d = await (await fetch(`/api/box/move?card_id=${id}&slot=${slot}&from_box=${box}`, { method: "POST" })).json();
      log(`移卡 slot ${slot} → ${toName}：${d.ok ? d.message : "失败 " + (d.message || d.code)}`, d.ok ? "ok" : "err");
    }
    await loadCardBox();
  } catch (err) {
    log("卡箱操作异常：" + err, "err");
  } finally {
    restore();
  }
}

// ---------- 手动变卡（魔变）----------
// 规则（用户 2026-09-22 口述，对照游戏机制与 HAR）：
//   ① 目标卡 = 卡库里「能变得到」的卡（transferable=true）；
//   ② 材料卡 = 卡箱里**面额 ≥ 目标卡**的卡，或 100/200/600 面值道具卡（其余道具不能变卡）。
//   API：change_card(srcid=材料, dstid=目标)；材料是道具卡时 prop=材料id，否则 prop=0。
const VALID_PROP_FACES = new Set([100, 200, 600]);   // 只有这三类面值卡可作变卡材料
let cbChangeReady = false;
let _propsCache = null;   // 道具卡材料缓存（100/200/600 面值卡），避免每次选目标都重拉

function cbPropFace(p) {
  // 道具名形如「100面值卡」→ 提取面值数字；非这三类直接排除
  const m = /(\d+)\s*面值卡/.exec(p.name || "");
  return m ? Number(m[1]) : null;
}

// 卡箱（换卡箱 + 保险箱）里已拥有的卡 id 集合 —— 目标卡列表用 ✓ 标记「自己有的」
function cbOwnedSet() {
  const s = new Set();
  for (const c of ((cbState.change && cbState.change.cards) || [])) if (c.id) s.add(Number(c.id));
  for (const c of ((cbState.store && cbState.store.cards) || [])) if (c.id) s.add(Number(c.id));
  return s;
}

async function initChangeForm() {
  if (cbChangeReady) return;
  cbChangeReady = true;
  const hint = $("cb-change-hint");
  const starBtn = $("cb-dd-star-btn"), starList = $("cb-dd-star-list");
  const themeBtn = $("cb-dd-theme-btn"), themeList = $("cb-dd-theme-list");
  const tgtBtn = $("cb-dd-target-btn"), tgtList = $("cb-dd-target-list");
  const matBtn = $("cb-dd-mat-btn"), matList = $("cb-dd-mat-list");
  const goBtn = $("cb-change-go2");
  let selStar = 0, selTheme = null, selTarget = null, selMat = null;   // 星级 / 套卡 / 目标卡 / 材料卡
  await propMaterials();   // 预热道具卡缓存：否则首次选目标卡时材料列表要等请求、短暂空白

  // 可变套卡（transferable=1，套卡级属性）
  let themes = [];
  try {
    const d = await (await fetch("/api/cards/themes?transferable=1&limit=2000")).json();
    themes = d.themes || [];
  } catch (e) { themes = []; }
  // 星级档位（可变套卡按 star 分布，如 ★★★ → 3 套）
  const starSet = [...new Set(themes.map((t) => t.star))].filter((s) => s > 0).sort((a, b) => a - b);

  const allBtns = [starBtn, themeBtn, tgtBtn, matBtn], allLists = [starList, themeList, tgtList, matList];
  function closeAllDD() {
    allLists.forEach((l) => { if (l) l.classList.add("hidden"); });
    allBtns.forEach((b) => { if (b) b.classList.remove("dd-open"); });
  }
  function toggleDD(list, btn) {
    const willOpen = list && list.classList.contains("hidden");
    closeAllDD();
    if (willOpen) { list.classList.remove("hidden"); if (btn) btn.classList.add("dd-open"); }
  }
  // 点页面其它地方关闭浮动列表
  document.addEventListener("click", (e) => {
    if (!e.target || !e.target.closest || !e.target.closest(".cb-dd")) closeAllDD();
  });

  // ⓪ 星级下拉：可变套卡按星级分档（★★★ → 3 套）
  function renderStarDD() {
    if (!starList) return;
    starList.innerHTML = starSet.length
      ? starSet.map((s) =>
          `<div class="pk${selStar === s ? " sel" : ""}" data-star="${s}">` +
          `<span class="pk-n">${"★".repeat(s)}</span>` +
          `<span class="pk-f">${themes.filter((t) => t.star === s).length} 套</span></div>`).join("")
      : `<div class="pk-empty">没有可变套卡</div>`;
    starList.querySelectorAll("[data-star]").forEach((el) => {
      el.onclick = () => {
        selStar = Number(el.dataset.star);
        selTheme = null; selTarget = null; selMat = null;
        if (themeBtn) themeBtn.disabled = !selStar;   // 选了星级才可开套卡下拉
        if (tgtBtn) tgtBtn.disabled = true;
        if (matBtn) matBtn.disabled = true;
        renderStarDD(); renderThemeDD(); renderTargetDD(); renderMatDD(); updatePreview(); closeAllDD();
      };
    });
  }

  // ① 套卡下拉：所选星级下的可变套卡，按 已有↓ 名 排序
  function renderThemeDD() {
    if (!themeList) return;
    if (!selStar) { themeList.innerHTML = `<div class="pk-empty">先选星级</div>`; return; }
    const list = themes.filter((t) => t.star === selStar).slice().sort((a, b) =>
      (b.have || 0) - (a.have || 0) || (a.name || "").localeCompare(b.name || "", "zh"));
    themeList.innerHTML = list.length
      ? list.map((t) => {
          const done = t.n && t.have >= t.n;
          const bdg = t.have > 0 ? `<span class="pk-bdg">${done ? "已集齐" : "✓ " + t.have + "/" + t.n}</span>` : "";
          return `<div class="pk${selTheme && selTheme.tid === t.tid ? " sel" : ""}" data-ttid="${t.tid}" title="${esc(t.name)}">` +
            `<span class="pk-n">${esc(t.name)}</span>${bdg}<span class="pk-f">${t.n}张</span></div>`;
        }).join("")
      : `<div class="pk-empty">该星级无可变套卡</div>`;
    themeList.querySelectorAll("[data-ttid]").forEach((el) => {
      el.onclick = () => {
        selTheme = list.find((x) => x.tid === Number(el.dataset.ttid)) || null;
        selTarget = null; selMat = null;
        if (tgtBtn) tgtBtn.disabled = !selTheme;
        if (matBtn) matBtn.disabled = true;
        renderTargetDD(); renderMatDD(); updatePreview(); closeAllDD();
      };
    });
  }

  const cardCache = new Map();
  async function themeCards(tid) {
    if (cardCache.has(tid)) return cardCache.get(tid);
    let cards = [];
    try {
      const d = await (await fetch("/api/cards/theme/" + tid)).json();
      cards = (d.cards || []).slice().sort((a, b) =>
        (a.price || 0) - (b.price || 0) || (a.name || "").localeCompare(b.name || "", "zh"));
    } catch (e) { cards = []; }
    cardCache.set(tid, cards);
    return cards;
  }
  // ② 目标卡下拉：所选套卡里的卡，标「有」
  async function renderTargetDD() {
    if (!tgtList) return;
    if (!selTheme) { tgtList.innerHTML = `<div class="pk-empty">先选套卡</div>`; return; }
    const cards = await themeCards(selTheme.tid);
    if (!cards.length) { tgtList.innerHTML = `<div class="pk-empty">该套卡无可变卡</div>`; return; }
    const owned = cbOwnedSet();
    tgtList.innerHTML = cards.map((c) =>
      `<div class="pk${selTarget && selTarget.id === c.id ? " sel" : ""}" data-cid="${c.id}">` +
      `<span class="pk-n">${esc(c.name)}</span>` +
      (owned.has(Number(c.id)) ? `<span class="pk-bdg">有</span>` : "") +
      `<span class="pk-f">${c.price ?? 0}面</span></div>`).join("");
    tgtList.querySelectorAll("[data-cid]").forEach((el) => {
      el.onclick = () => {
        selTarget = cards.find((x) => x.id === Number(el.dataset.cid)) || null;
        selMat = null;
        if (matBtn) matBtn.disabled = !selTarget;
        renderMatDD(); updatePreview(); closeAllDD();
      };
    });
  }

  async function propMaterials() {
    if (_propsCache) return _propsCache;
    try {
      const d = await (await fetch("/api/props")).json();
      _propsCache = (d.props || []).filter((p) => (p.count || 0) > 0 && VALID_PROP_FACES.has(cbPropFace(p)));
    } catch (e) { _propsCache = []; }
    return _propsCache;
  }
  function boxMaterials() {
    if (!selTarget) return [];
    const tp = selTarget.price || 0;
    const tag = (arr, bx) => (arr || []).map((c) => ({ ...c, box: bx }));
    return [...tag(cbState.change?.cards, 0), ...tag(cbState.store?.cards, 1)]
      .filter((c) => (c.price || 0) >= tp)
      .sort((a, b) => (a.theme || "").localeCompare(b.theme || "", "zh") || (a.price || 0) - (b.price || 0));
  }
  // ③ 材料卡下拉：道具卡（面值≥目标）+ 卡箱卡（面额≥目标）
  function renderMatDD() {
    if (!matList) return;
    if (!selTarget) { matList.innerHTML = `<div class="pk-empty">先选目标卡</div>`; return; }
    const tp = selTarget.price || 0;
    const props = (_propsCache || []).filter((p) => cbPropFace(p) >= tp);
    const box = boxMaterials();
    let html = "";
    if (props.length) {
      html += `<div class="pk-sec">道具卡（面值 ≥ ${tp}）</div>`;
      html += props.map((p) =>
        `<div class="pk${selMat && selMat.key === "prop:" + p.id ? " sel" : ""}" data-mkey="prop:${p.id}">` +
        `<span class="pk-n">${esc(p.name)}</span><span class="pk-f">×${p.count}</span></div>`).join("");
    }
    let cur = null;
    for (const c of box) {
      const th = c.theme || "（无套卡）";
      if (th !== cur) { cur = th; html += `<div class="pk-sec">${esc(th)}</div>`; }
      const key = `box:${c.box}:${c.slot ?? ""}`;
      html += `<div class="pk${selMat && selMat.key === key ? " sel" : ""}" data-mkey="${key}" data-mid="${c.id}" data-mnm="${esc(c.name)}" data-mpr="${c.price ?? 0}">` +
        `<span class="pk-n">${esc(c.name)}</span>` +
        `<span class="pk-t">${c.box === 0 ? "换卡箱" : "保险箱"}</span>` +
        `<span class="pk-f">${c.price ?? 0}面</span></div>`;
    }
    if (!props.length && !box.length) html = `<div class="pk-empty">没有 ≥${tp} 面的材料（道具/卡箱都不够）</div>`;
    matList.innerHTML = html;
    matList.querySelectorAll("[data-mkey]").forEach((el) => {
      el.onclick = () => {
        const key = el.dataset.mkey;
        if (key.startsWith("prop:")) {
          const id = Number(key.slice(5));
          const p = props.find((x) => x.id === id);
          selMat = { kind: "prop", id, key, name: p ? p.name : "", price: p ? cbPropFace(p) : 0 };
        } else {
          selMat = {
            kind: "box", id: Number(el.dataset.mid), key,
            name: el.dataset.mnm || "", price: Number(el.dataset.mpr) || 0,
          };
        }
        updatePreview(); closeAllDD();
      };
    });
  }

  function updatePreview() {
    if (starBtn) starBtn.textContent = selStar ? `星级：${"★".repeat(selStar)} ▾` : "选择星级 ▾";
    if (themeBtn) themeBtn.textContent = selTheme ? `套卡：${selTheme.name} ▾` : "选择套卡 ▾";
    if (tgtBtn) tgtBtn.textContent = selTarget ? `目标：${selTarget.name} ▾` : "选择目标卡 ▾";
    if (matBtn) matBtn.textContent = selMat ? `材料：${selMat.name} ▾` : "选择材料卡 ▾";
    if (goBtn) goBtn.disabled = !(selTarget && selMat);
    if (hint) {
      if (!selStar) hint.textContent = "依次选：星级 → 套卡 → 目标卡 → 材料卡，再点「变卡」";
      else if (!selTheme) hint.textContent = "再选套卡（该星级下的可变套卡）";
      else if (!selTarget) hint.textContent = "再选目标卡（该套卡里要变出的卡）";
      else if (!selMat) hint.textContent = "再选一张材料卡（面额须 ≥ 目标）";
      else hint.textContent = `将用「${selMat.name}」→ 变为「${selTarget.name}」`;
    }
  }

  // 提交变卡（busyBtn 即时反馈 + 成功立即刷新卡箱）
  async function submitChange() {
    if (!selTarget) { if (hint) hint.textContent = "请先选目标卡"; return; }
    if (!selMat) { if (hint) hint.textContent = "请先选一张材料卡"; return; }
    const dst = selTarget.id;
    const srcid = selMat.id;
    const prop = selMat.kind === "prop" ? selMat.id : 0;
    const matNm = selMat.name + (selMat.price ? `（${selMat.price}面）` : "");
    const restore = goBtn ? busyBtn(goBtn, "变卡中…") : null;
    if (hint) hint.textContent = `变卡中（${matNm} → ${selTarget.name}）…`;
    log(`变卡（魔变）：${matNm} → ${selTarget.name}(#${dst}) …`, "info");
    try {
      const r = await (await fetch("/api/gift/change", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ srcid, dstid: dst, prop }),
      })).json();
      if (r.ok) {
        log(`变卡成功：${matNm} → ${selTarget.name}`, "ok");
        if (hint) hint.textContent = `成功：${matNm} → ${selTarget.name}`;
        await loadCardBox();
      } else {
        log(`变卡失败（约 50% 概率；-32002=该目标不支持或卡不在箱）：${r.message || r.code}`, "warn");
        if (hint) hint.textContent = `失败：${r.message || r.code}`;
      }
    } catch (e) {
      if (hint) hint.textContent = "异常：" + e; log("变卡异常：" + e, "err");
    } finally {
      if (restore) restore();
    }
  }

  if (goBtn) goBtn.onclick = submitChange;
  if (starBtn) starBtn.onclick = (e) => { e.stopPropagation(); toggleDD(starList, starBtn); };
  if (themeBtn) themeBtn.onclick = (e) => { e.stopPropagation(); if (!themeBtn.disabled) toggleDD(themeList, themeBtn); };
  if (tgtBtn) tgtBtn.onclick = (e) => { e.stopPropagation(); if (!tgtBtn.disabled) toggleDD(tgtList, tgtBtn); };
  if (matBtn) matBtn.onclick = (e) => { e.stopPropagation(); if (!matBtn.disabled) toggleDD(matList, matBtn); };

  renderStarDD(); renderThemeDD(); renderTargetDD(); renderMatDD(); updatePreview();
}


// ===================== 每日比赛（bisai.har 实证，2026-09-25）=====================
// 只读：competition act=1 主页 / act=6 装备 / act=20 金卡限购；pvp act=14 日榜；union act=1 工会。
// 后端 backend/match.py；未证实语义的字段一律原样透出，不做中文硬译。

let matchState = null, matchTheme = null, matchPvp = null, matchUnion = null,
    matchGold = null, matchGvg = null, matchSanguo = null,
    matchUnionExtra = null,   // 工会任务/碎片/商店（union act=15/16/30）
    matchPvpExtra = null,     // 竞技场状态（pvp act=1：surplus/对手/score）
    matchXiyou = null,        // 西游记（worldexplore act=1）
    matchXiyouTeam = null,    // 西游记 布阵/记录（act=1 同包，前端切分展示）
    matchSanguoLevels = null; // 三国战役关卡列表（sanguo act=9）
let matchSubTimer = null;

// 三国每日出战次数（实证：act=1 的 freenum 是「今日已出战次数」，0→3 封顶；
// 与后端 match.SGUO_DAILY_MAX 同源，前端只用来算「还能打几次」）
const SGUO_DAILY_MAX = 3;

// ---- 每日比赛页的子模块：每个子模块 = 一组相关卡片 + 自己的接口 ----
// 切到哪个才拉哪个，定时也只刷新当前停留的子模块（避免一次进页面打 8 个接口）。
function matchFmtTs(ts) {
  if (!ts) return "-";
  try { return new Date(Number(ts) * 1000).toLocaleString("zh-CN", { hour12: false }); }
  catch (e) { return String(ts); }
}
// 装扮槽位中文名（后端 cat_dress.slot_of 给的是 头/手/衣/其他）。
// 官方配置里只有 96 件基础装扮有名字，进阶款（如 10353）没收录 → 用部位兜底，
// 界面上不出现数字 id；真实 id 放在 title 里供对照。
const SLOT_CN = { "头": "头饰", "手": "手持", "衣": "服装", "其他": "饰品" };

function matchRow(k, v) {
  if (v === undefined || v === null || v === "") return "";
  return `<div class="rv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
}

function renderMatchState() {
  const box = $("match-state-box"), cats = $("match-cats"), cnt = $("match-state-cnt");
  const d = matchState || {};
  if (!d.ok) {
    box.className = "state-box";
    box.innerHTML = `<span style="color:var(--danger)">读取失败：${d.message || "未知"}${d.code ? "（code=" + d.code + "）" : ""}</span>`;
    cats.innerHTML = "";
    return;
  }
  if (!d.bound) {
    box.className = "state-box";
    box.innerHTML = `<span style="color:var(--warn)">该号未开通 / 暂无比赛数据：
      <br>未开通的接口会返回空壳数据。工会与 PVP 榜通常仍可读。</span>`;
  } else {
    const n = (d.cats || []).length;
    box.className = "state-box";
    // 只留比赛本身关心的字段；talent/clover/dzs/season/unionid 与比赛玩法无关，不展示
    box.innerHTML =
      matchRow("猫咪数量", n) +
      matchRow("今日剩余次数", d.performe_num) +
      matchRow("次数重置时间", d.perform_limit_ts ? matchFmtTs(d.perform_limit_ts) : "-");
  }
  cnt.textContent = d.ok ? (d.bound ? "已开通" : "空态") : "失败";
  const cs = d.cats || [];
  cats.innerHTML = cs.length
    ? `<div class="cat-row">` + cs.map((c) => {
        const eq = (c.equip || []).filter((x) => x);
        // 名字优先：进阶款官方名表没收录，退回槽位名（头/手/衣/其他），界面不留数字 id
        const names = c.equip_names || [];
        const slots = c.equip_slots || [];
        const wear = eq.map((id, i) => names[i] || (slots[i] ? SLOT_CN[slots[i]] || slots[i] : String(id)));
        const skl = (c.skills || []).map((s) => `L${s.cat_skill_level}`).join("、");
        const lv = c.level ?? "-";
        return `<div class="cat-card">
          <div class="cat-n">${c.elf_id ?? "-"} 号猫</div>
          <div class="cat-lv">${lv} 级 · 星 ${c.star ?? "-"}</div>
          <div class="cat-meta">心情 ${c.mood ?? "-"} · 运动 ${c.motion ?? "-"} · 表演 ${c.perform ?? "-"}
            <br><span class="cat-sk">技能 ${skl || "-"}</span></div>
          <div class="cat-w" title="穿戴：${wear.join("、") || "—"}\n装扮名取官方配置；进阶款未收录时显示部位\n原始 id ${eq.join("/")}">穿戴：${wear.join("、") || "—"}</div>
        </div>`;
      }).join("") + `</div>`
    : `<div class="empty-tip">暂无猫咪数据${d.bound ? "" : "（该号未开通比赛模块）"}</div>`;
}







async function loadMatchState(force) {
  // 手动刷新由 busyBtn 控制禁用，接口失败也只提示、不置死界面
  const r = await fetch("/api/match/state").then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchState = r;
  renderMatchState();
  const res = $("match-res");
  if (res) res.textContent = r.ok ? "状态已读取" : (r.message || "读取失败");
  if (!r || r.ok === false) mlogFail("比赛状态", r);   // 成功静默：60s 轮询不刷屏
}





// ===================== 出战记录 =====================
let matchLog = null;

async function loadMatchLog() {
  const r = await fetch("/api/match/race-log?limit=10").then((x) => x.json()).catch(() => ({ ok: false }));
  matchLog = r;
  renderMatchLog();
  if (!r || r.ok === false) mlogFail("出战记录", r);
}

function renderMatchLog() {
  const box = $("match-log-box"), cnt = $("match-log-cnt");
  if (!box) return;
  const items = (matchLog && matchLog.items) || [];
  if (cnt) cnt.textContent = items.length + " 条";
  if (!items.length) {
    box.innerHTML = `<div class="state-box">暂无出战记录（打一场后自动记录：猫号 / 穿装 / 得分）</div>`;
    return;
  }
  box.innerHTML = items.map((it) => {
    // 旧记录里的 name 可能是当年补不全时存下的「属性8」脏值 —— 视为没名字，走 attrName() 兜底
    const th = (it.theme || []).map((a) => {
      const nm = (a.name && !/^属性\d+$/.test(a.name)) ? a.name : attrName(a.id);
      return `${nm} ${a.value}%`;
    }).join(" · ");
    // 穿装优先用后端生成的可读文本（「头:魔法帽(演奏+266 朗诵+73) ｜ 命中主题：…」），
    // 旧记录没有该字段时退回原始编号，并注明看不出名字。
    const dressTxt = it.equip_text || ((it.equip_ids || []).join("|") || "-");
    return `<div class="state-box" style="margin-bottom:6px">` +
      matchRow("时间", it.ts) +
      matchRow("出战猫 / 场次 id",
        `#${it.cat_id} 号${it.cat_level ? `（${it.cat_level} 级）` : ""} / ${it.race_id}`) +
      matchRow("穿装", dressTxt) +
      matchRow("本场主题", th || "-") +
      matchRow("总分 / 评级分", `${it.total_value ?? "-"} / ${it.level_score ?? "-"}`) +
      matchRow("剩余次数 / 奖励", `${it.performe_num ?? "-"} / ${it.content || "-"}`) +
      `</div>`;
  }).join("");
}

// ===================== 各玩法子块（每块各自一键） =====================

// 数组长度：接口各端点字段层级不一（data.list / data.data / ...），统一兜底
// 数组长度：接口各端点字段层级不一（data.list / data.data / ...），统一兜底
function arrLen(x) {
  const o = x || {};
  const d = o.data || {};
  if (Array.isArray(d.list)) return d.list.length;
  if (Array.isArray(d.data)) return d.data.length;
  if (Array.isArray(o.list)) return o.list.length;
  return "-";
}



// ---- 以下函数自 5e57348 恢复（08f8db1 误删；猫窝日常=主界面，比赛是子玩法）----

// ⚠️ 西游记（xiyou / card_worldexplore）与三国战役（sanguo / card_worldsanguo）
// 是**两个独立玩法**——之前前端把它们合称「西游三国」挤在一个 tab 里，2026-09-27 拆开。
// 2026-09-27 二次整理：
//  · 公会战并进「工会 · 公会战」（原本两个 tab 内容高度重复：都显示出兵与今日次数）
//  · 「各玩法一键」删除 —— 领奖/刷对手/挑战/闯关已归到各玩法自己的 tab
//  · PVP 日榜 tab（2026-09-28 删）：榜单没有展示价值（点赞/挑战都已被「一键全部」自动跑掉），
//    竞技场的**状态行与动作钮**并入猫窝日常页已有的「点赞」和「竞技比赛」两块，不再单开块。
const MATCH_SUB_KEYS = ["home", "race", "union", "xiyou", "sanguo"];
const MATCH_SUB_LOAD = {
  // 猫卡片的 equip_arr 列表就在猫窝日常里，所以这里也要拉一次比赛状态（act=1 只读）；
  // 竞技场状态行（「竞技比赛」块）与可赞首位（「点赞」块）都在本页，一起拉
  home: () => { loadHomeState(); loadMatchState(); loadMatchPvp(); loadMatchPvpExtra(); },
  // 出战记录（#match-log-box）就在本面板，必须一起拉，否则永远停在「读取中…」
  race: () => { loadMatchTheme(); loadMatchState(true); loadMatchLog(); },
  // 工会 / 公会战 / 金卡限购 一个面板一起拉（公会战 act=22 依赖工会数据）
  union: () => { loadMatchUnion(); loadMatchUnionExtra(); loadMatchGold(); loadMatchGvg(); },
  // 西游记：小程序地图（8 张小地图＝套卡 1604..1611），只拉本玩法自己的接口
  xiyou: () => { loadMatchXiyou(); },
  // 三国战役：9 张小地图＝套卡 1745..1756；关卡表 act=9 一次只读，切 tab 不重复打
  sanguo: () => { loadMatchSanguo(true); if (!matchSanguoLevels) loadMatchSanguoLevels(); },
};

let homeInited = false;
let homeStateCache = null;   // 最近一次 /api/home/state，供「点赞」「竞技比赛」两块拼状态行

function matchStopPoll() {
  // 分模块定时器已合并为 matchSubTimer（按当前停留的子模块驱动）
  if (matchSubTimer) { clearInterval(matchSubTimer); matchSubTimer = null; }
}

function switchMatchSub(key) {
  if (MATCH_SUB_KEYS.indexOf(key) < 0) return;
  window.__matchSub = key;
  const bar = $("match-sub-bar");
  if (bar) bar.querySelectorAll(".tab").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-msub") === key);
  });
  MATCH_SUB_KEYS.forEach((k) => {
    const el = $("msub-" + k);
    if (el) el.classList.toggle("on", k === key);
  });
  const fn = MATCH_SUB_LOAD[key];
  if (fn) fn();
}

// 竞技场不单开块（2026-09-28 用户定调）：点赞信息进猫窝日常的「点赞」块，
// 场次/名次/对手信息进「竞技比赛」块 —— 这两块本来就有，不重复造一块。
// 点赞（点第一个可赞）与挑战都由「一键全部」自动跑掉（cathome.like("arena") / arena_auto）。

// 「点赞」块：每日比赛榜 + 竞技比赛榜的今日状态，外加竞技榜今日可赞首位
function renderHomeLike() {
  const st = $("home-like-st");
  if (!st) return;
  const d = homeStateCache || {};
  const likes = d.likes || {};
  const mark = (v) => (v === 1 ? "已点 ✓" : (v === 0 ? "未点 ✗" : "读取中…"));
  const zan = (matchPvp && matchPvp.zan_targets) || [];
  const first = zan.length
    ? `${zan[0].nick || zan[0].uin}（第 ${zan[0].rank ?? "-"} 名）`
    : "";
  st.textContent = `每日比赛榜 ${mark(likes.match)} · 竞技比赛榜 ${mark(likes.arena)}` +
    (first ? ` · 今日可赞首位 ${first}` : "");
}

// 「竞技比赛」块：次数来源/我的名次/当前对手（前两个来自猫窝状态，后两个来自竞技场接口）
function renderHomeArena() {
  const st = $("home-arena-st");
  if (!st) return;
  const d = homeStateCache || {};
  const pv = d.pvp || {};
  const rank = (matchPvp && matchPvp.ok !== false) ? matchPvp.my_rank : null;
  const x = matchPvpExtra || {};
  const es = ((x.state || {}).data || {}).enemy_list || [];
  st.textContent = `剩余 ${pv.surplus ?? "-"} / 每日免费 ${pv.free_pvp_num ?? "-"}`
    + ` · 已刷对手 ${pv.flush_num ?? "-"} · 我的名次 `
    + (rank ? `第 ${rank} 名` : `未上榜（榜内最低 ${(matchPvp && matchPvp.min_score) || "-"}）`)
    + ` · 我的战力 ${pv.my_ce ?? "-"}`
    + (es.length ? ` · 当前对手 ${es[0].name || es[0].uin}` : "")
    + (pv.surplus === 0 ? "（免费场次已用完）" : (pv.surplus > 0 ? "" : "（场次已用完）"));
}

// 竞技场状态（次数/对手）：两次语义未定案，surplus / free_pvp_num 两个字段都整份透出
async function loadMatchPvpExtra() {
  const r = await fetch("/api/match/pvp-extra").then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchPvpExtra = r;
  renderHomeArena();
  if (!r || r.ok === false) mlogFail("竞技场", r);
}

function renderMatchUnion() {
  const box = $("match-union-box"), cnt = $("match-union-cnt");
  const d = matchUnion || {};
  if (!d.ok) { box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`; cnt.textContent = "失败"; return; }
  cnt.textContent = `会员 ${d.mem_num ?? "-"}/${d.max_num ?? "-"}`;
  box.innerHTML = `<div class="state-box">` +
    matchRow("工会名", d.union_name) +
    matchRow("工会 id", d.unionid) +
    matchRow("等级", d.lv) +
    matchRow("贡献", d.exp) +
    matchRow("会员 / 上限", `${d.mem_num ?? "-"}/${d.max_num ?? "-"}`) +
    matchRow("金币 / 碎片", `${d.coin ?? "-"} / ${d.chip ?? "-"}`) +
    matchRow("活跃度", d.union_activity) +
    matchRow("会长", d.patriarch_name) +
    matchRow("成员数", Object.keys(d.members || {}).length) +
    `</div>`;
  renderUnionMembers();
}

// 成员贡献榜：members 是逗号串，第 2 段＝昵称、第 3 段＝贡献（2026-09-27 实跑证实），
// 其余段语义未证实 → 只显示这两项 + 编号，不硬译。点「赞」= union act=14。
function renderUnionMembers() {
  const box = $("match-union-mem-box"), cnt = $("match-union-mem-cnt");
  if (!box) return;
  const d = matchUnion || {};
  const raw = d.members || {};
  const rows = Object.keys(raw).map((uin) => {
    const seg = String(raw[uin] || "").split(",");
    return { uin, name: seg[2] || uin, contrib: seg[3] || "-",
             lv: seg[0] || "-", raw: raw[uin] };
  }).sort((a, b) => (Number(b.contrib) || 0) - (Number(a.contrib) || 0));
  const total = rows.reduce((s, r) => s + (Number(r.contrib) || 0), 0);
  if (cnt) cnt.textContent = `${rows.length} 人 · 总贡献 ${total}`;
  if (!rows.length) { box.innerHTML = `<div class="empty-tip">暂无成员数据</div>`; return; }
  // 行样式全用行内（项目里没有 .lst-row 这类类，靠样式表会掉格式）
  box.innerHTML = `<div>` + rows.map((r, i) =>
    `<div style="display:flex;gap:6px;align-items:center;padding:4px 8px;
        border-bottom:1px solid var(--line);${i % 2 ? "background:#f8fafc;" : ""}">
       <span style="min-width:26px;color:var(--muted)">${i + 1}</span>
       <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.name)}</span>
       <span style="color:var(--muted)">等级${esc(r.lv)}</span>
       <span style="min-width:58px;text-align:right">贡献 ${esc(r.contrib)}</span>
       <button class="btn sm" data-zan="${esc(r.uin)}">赞</button>
     </div>`).join("") + `</div>` +
    `<div class="empty-tip" style="font-size:12px">贡献＝members 第 4 段（实跑证实），等级为第 1 段（未证实，仅供参考），其余段原样保留未译。</div>`;
}

// 工会任务 / 碎片 / 商店（union act=15/16/30）
function renderMatchUnionExtra() {
  const box = $("match-union-extra-box"), cnt = $("match-union-extra-cnt");
  if (!box) return;
  const e = matchUnionExtra || {};
  if (!e || e.ok === false) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${(e && e.message) || "未加载"}</span></div>`;
    if (cnt) cnt.textContent = "失败";
    return;
  }
  const t = (e.task || {}).data || {}, ch = (e.chip || {}).data || {},
        sh = (e.shop || {}).data || {};
  const items = sh.data || sh || [];
  if (cnt) cnt.textContent = `任务次数 ${t.num ?? "-"} · 工会资金 ${ch.data ?? "-"}`;
  box.innerHTML = `<div class="state-box">` +
    // 「今日已出兵次数」改由下方公会战块统一显示（两处同一个 num，不再重复）
    matchRow("任务位 / 重置位", `${t.bits ?? "-"} / ${t.resetnum ?? "-"}`) +
    matchRow("工会资金", ch.data ?? "-") +
    matchRow("工会商店", (Array.isArray(items) && items.length)
      ? items.map((x) => `${x.consume}(${x.id})`).join(" · ") : "-") +
    `</div><div class="empty-tip" style="font-size:12px">出兵计数：只如实显示，口径待核实（不是游戏内挑战 3/3 的次数，是否每日清零未证实）；上限未知，界面不显示。</div>`;
}

// 工会玩法动作：全部走 /api/match/union-*，完成后立刻刷新对应视图（项目铁律）。
async function unionAct(btn, url, body, label, tip) {
  const restore = busyBtn(btn, "处理中…", label);
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
    if (tip) mlog(tip, r && r.ok === false ? "err" : "ok");
    await loadMatchUnion(); await loadMatchUnionExtra();
    const g = $("match-gvg-fightcnt");
    if (g) renderGvgFightCnt();
    return r;
  } finally {
    restore();
  }
}

async function unionClaim() {
  return await unionAct($("union-claim"), "/api/match/union-claim", {}, "领奖", "[工会] 工会领奖完成");
}

async function unionZan() {
  const d = (matchUnion && matchUnion.members) || {};
  const uins = Object.keys(d);
  if (!uins.length) { mlog("[工会] 成员表为空，先点「刷新工会」", "err"); return { ok: false }; }
  const r = await unionAct($("union-zan"), "/api/match/union-zan", { uins: uins.slice(0, 10) },
    "点赞", `[工会] 点赞：发给 ${Math.min(10, uins.length)} 人`);
  return r;
}

async function unionFight() {
  if (!confirm("出兵不是游戏内公会战的「挑战」（3/3 那个，接口未定位）；实测效果是工会金币 +2。确认出兵？")) {
    return { ok: false, message: "已取消" };
  }
  return await unionAct($("union-fight"), "/api/match/union-all",
    { zan: false, fight: true }, "出兵", "[工会] 出兵完成");
}

async function unionAll() {
  if (!confirm("一键工会 = 领奖 + 全员点赞 + 出兵（出兵不是游戏内挑战 3/3，实测工会金币 +2），确认？")) {
    return { ok: false, message: "已取消" };
  }
  const r = await unionAct($("union-all"), "/api/match/union-all",
    { zan: true, fight: true, zan_all: true }, "一键工会", "[工会] 一键工会完成");
  if (r && r.ok !== false) {
    const st = r.steps || [];
    mlog("[工会] " + st.map((s) => `${s.act}:${s.ok ? "ok" : "✗"}`).join(" → "), "ok");
    for (const s of st) if (s.ok === false) mlog(`[工会] ${s.act} 未成功：${s.msg}`, "err");
  }
  return r;
}

// 公会战：出兵计数展示（data/cat_equip 无关，纯展示）
// ⚠ 2026-09-28 用户实证：游戏内公会战挑战 3/3 一场没用，但这里 num 已走到 3 ——
//   num 不是游戏内挑战次数，「今日」口径也未证实（可能跨日累计）。只如实显示计数，不解读。
function renderGvgFightCnt() {
  const g = $("match-gvg-fightcnt");
  if (!g) return;
  const num = (matchUnionExtra && matchUnionExtra.task && matchUnionExtra.task.data || {}).num;
  g.textContent = num === undefined || num === null ? "" : `出兵计数 ${num}（非游戏内挑战 3/3）`;
  g.style.marginLeft = "6px";
}

function renderMatchGvgProgress(g) {
  const p = Number(g.progress || 0), e = Number(g.e_progress || 0);
  const tot = p + e || 1, pct = Math.round(p * 100 / tot);
  const qt = (g.levelgetbits || "").split(",").filter(Boolean).filter((x) => x !== "0");
  return `<div style="margin:4px 0 6px">
      <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--muted)">
        <span>我方 ${p.toLocaleString()}</span><span>敌方 ${e.toLocaleString()}</span>
      </div>
      <div style="height:8px;border-radius:4px;background:var(--bg-soft,#eee);overflow:hidden;display:flex">
        <div style="width:${pct}%;background:#4a90d9"></div>
        <div style="width:${100 - pct}%;background:#d9534f"></div>
      </div>
      <div style="font-size:12px;margin-top:2px">进度占比 我方 ${pct}% · 敌方 ${100 - pct}%</div>
    </div>` +
    matchRow("关卡领取位", qt.length ? qt.join(" · ") : "-") +
    (qt.length ? `<div class="empty-tip" style="font-size:12px">关卡领取位：0 表示还没领，非零表示此关卡已领过奖（未逐级验证，只记录）。</div>` : "");
}

function renderMatchGvg() {
  const box = $("match-gvg-box"), cnt = $("match-gvg-cnt");
  const d = matchGvg || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  const g = d.data || {};
  cnt.textContent = `我方 Lv${g.level ?? "-"} · 敌方 Lv${g.e_levelid ?? "-"}`;
  box.innerHTML = `<div class="state-box">` +
    renderMatchGvgProgress(g) +
    matchRow("我方等级", g.level) +
    matchRow("我方进度", g.progress) +
    matchRow("我方关卡", g.inlv) +
    matchRow("敌方等级", g.e_levelid) +
    matchRow("敌方名称", g.e_name) +
    matchRow("敌方工会", g.enemy_uinon) +
    matchRow("敌方进度", g.e_progress) +
    matchRow("关卡领取位（原始）", g.levelgetbits) +
    matchRow("排名位（语义未证实）", `${g.level_rank_bit ?? "-"} / ${g.level_rank_bit2 ?? "-"}`) +
    matchRow("历史等级（语义未证实）", g.last_lv) +
    matchRow("赛季礼包", g.season_gift) +
    matchRow("已赠送记录",
      g.gift_record ? String(g.gift_record).split(",").filter(Boolean).length + " 条" : "-") +
    matchRow("更新时间", matchFmtTs(g.ts)) +
    `</div>`;
}

function renderMatchSanguo() {
  const box = $("match-sanguo-box"), cnt = $("match-sanguo-cnt");
  const d = matchSanguo || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  const t = d;
  // 关卡记录按 HAR 原样统计，段位含义未证实 → 不硬译
  const logs = t.logData || [];
  const passed = logs.filter((x) => x && x.passData && x.passData.isget).length;
  cnt.textContent = `关卡 ${t.levelId ?? "-"} · 记录 ${logs.length}`;
  box.innerHTML = `<div class="state-box">` +
    matchRow("当前关卡", t.levelId) +
    matchRow("关卡进度号（语义未证实）", t.passId) +
    matchRow("今日已出战", t.freenum === undefined || t.freenum === null ? "-" :
      `${t.freenum} / ${SGUO_DAILY_MAX} 次`) +
    matchRow("今日可再出战", t.freenum === undefined || t.freenum === null ? "-" :
      `${Math.max(0, SGUO_DAILY_MAX - Number(t.freenum || 0))} 次`) +
    matchRow("已购次数", t.buynum) +
    matchRow("天赋分 / 天赋率", `${t.talent_score ?? "-"} / ${t.talent_rate ?? "-"}`) +
    matchRow("出战阵型（技能等级）", (t.formation || []).map((f, i) => `槽${i + 1}·Lv${f.lv}`).join("、") || "-") +
    matchRow("关卡记录", logs.length ? `${logs.length} 条，已通 ${passed} 关` : "-") +
    matchRow("随行精灵", (t.travel_elf_arr || []).length + " 只") +
    matchRow("道具", (t.img_items || []).length ? `${(t.img_items || []).length} 种` : "-") +
    matchRow("关卡卡箱 / 保险箱", `${(t.ex_box_data || []).filter((x) => x && x.card_id).length} / ${t.ex_box_num ?? "-"} 格 · ${t.safe_box_num ?? "-"} 格`) +
    matchRow("每日任务",
      (t.task_detail || []).length ? `${(t.task_detail || []).length} 项` : "（未拉）") +
    matchRow("商店", (t.shoplist || []).length ? `${(t.shoplist || []).length} 项` : "-") +
    `</div>`;
  // 「一键出战」按钮文案跟真实剩余次数走（服务端 act=1 的 freenum = 今日已出战次数）
  const left = Math.max(0, SGUO_DAILY_MAX - Number(t.freenum || 0));
  const rb = document.querySelector("[data-act='mm-raid-sg']");
  if (rb && rb.dataset.idle !== "出战中…") {
    rb.textContent = left ? `一键出战（今日剩 ${left} 次）` : "一键出战（今日已出满）";
  }
}

function renderMatchGold() {
  const box = $("match-gold-box"), cnt = $("match-gold-cnt");
  const d = matchGold || {};
  if (!d.ok) { box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`; cnt.textContent = "失败"; return; }
  const g = d.data || {};
  cnt.textContent = "金卡限购";
  let bought = 0;
  for (let i = 1; i <= 6; i++) { if (Number(g["gold_buy_id" + i]) > 0) bought++; }
  box.innerHTML = `<div class="state-box">` +
    matchRow("今日已购分组", bought ? `${bought} 组有购买记录` : "无") +
    matchRow("购买记录分组",
      [1, 2, 3, 4, 5, 6].map((i) => g["gold_buy_id" + i]).join(" / ")) +
    `</div><div class="empty-tip" style="font-size:12px">分组含义未证实，仅透出接口原值。</div>`;
}

async function loadMatchPvp() {
  // 榜单不展示，只用来取「今日可赞首位」和「我的名次」；名次与可赞对象都在全量 1000 人上算，
  // 跟取回多少条无关，故只取前 20 条（top 越小请求越轻）
  const r = await fetch("/api/match/pvp?top=20").then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchPvp = r;
  renderHomeLike();
  renderHomeArena();
  if (!r || r.ok === false) mlogFail("竞技场", r);
}

async function loadMatchUnion() {
  const r = await fetch("/api/match/union").then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchUnion = r;
  renderMatchUnion();
  renderGvgFightCnt();
  if (!r || r.ok === false) mlogFail("工会", r);
}

async function loadMatchUnionExtra() {
  const r = await fetch("/api/match/union-extra").then((x) => x.json()).catch(() => ({ ok: false }));
  matchUnionExtra = r;
  renderMatchUnionExtra();
  renderGvgFightCnt();
  if (r && r.ok === false) mlogFail("工会任务/商店", r);
}

async function loadMatchGvg() {
  const r = await fetch("/api/match/gvg").then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchGvg = r;
  renderMatchGvg();
  if (!r || r.ok === false) mlogFail("公会战", r);
}

// 三国一键出战：连打到今日次数用尽（后端内部每轮重读真实阵容，次数由服务端回读）
// sgPickChapter 是界面上选的大关（0 = 跟服务端当前关）：选了就在那一大关里打缺口最小的层。
async function sanguoRaid() {
  const r = await fetch("/api/match/sanguo-raid", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chapter: sgPickChapter || 0 }),
  }).then((x) => x.json()).catch(() => null);
  if (!r || r.ok === false) {
    mlog("[三国] 一键出战失败：" + ((r && r.message) || "网络异常"), "err");
    return;
  }
  if (r.skipped) { mlog("[三国] " + (r.message || "今日已出满，无需再出"), "warn"); return; }
  const rs = (r.rounds || []).map((x) => `第${x.no}场 ${x.ok ? (x.text || "成功") : (x.msg || "失败")}`);
  mlog(`[三国] 一键出战：${r.chapter ? `第 ${r.chapter} 大关 · ` : ""}关卡 L${r.levelid} 出战 ${r.played} / ${(r.rounds || []).length} 场` +
    `（今日 ${r.used_before}→${r.used_after} 次）` + (rs.length ? "｜" + rs.join("；") : ""),
    r.played ? "ok" : "");
  await loadMatchSanguo(true); await loadMatchSanguoLevels();
}

async function loadMatchSanguo(withTask = false) {
  const url = "/api/match/sanguo" + (withTask ? "?want_task=1" : "");
  const r = await fetch(url).then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchSanguo = r;
  renderMatchSanguo();
  loadMatchSanguoBox();   // 顺带刷新关卡卡箱（一次读取，不额外请求状态接口）
  if (!r || r.ok === false) mlogFail("三国战役", r);
}

// ---------- 三国「关卡卡箱 + 保险箱」：层卡 / 缺层 / 同层多余可卖 ----------
// 两个箱子（关卡卡箱 ex_box 15 格＝箱子编号 0，保险箱 safe_box 12 格＝编号 1），
// 都能用 act=5 {cards:'{箱子}_{卡位}_{卡id}'} 卖掉换三国币。
// 关卡卡箱是自动攒层卡的地方、缺层就出不了关；保险箱只展示 + 手动卖 ——
// 「保险箱 ↔ 关卡卡箱」怎么搬没有实证，自动动它可能把还缺的层卡卖掉。
let matchSgBox = null;

// ---------- 大关可选（2026-09-29 用户要求：三国 / 西游都能选打第几大关）----------
// 0 = 不指定、跟服务端给的当前关走；选了就往那一大关里缺口最小的层打。
// ⚠ 选中的值必须存在变量里：面板每 60s 轮询重渲染一次，只把选中态写在 DOM 上会被刷掉。
let sgPickChapter = 0;
let xyPickChapter = 0;

/** 把后端的 chapters 填进大关下拉；刷新时保留用户已选的那一项。
 *
 * ⚠ option 的 value 按玩法取不同的量，别混：
 *   - 三国 `mode="chapter"`：后端要的是**大关号**（1..9），由后端在该大关里挑该打的小关
 *     （第 1 大关的缺口 → 102），关号（c.levelid）只能当文案显示；
 *   - 西游 `mode="levelid"`：出发本来就按大关号，`c.levelid` 与 `c.chapter` 同值。
 */
function fillChapterSelect(id, chapters, picked, mode) {
  const el = $(id);
  if (!el) return;
  const keep = String(picked || el.value || "0");
  const valOf = (c) => (mode === "chapter" ? c.chapter : (c.levelid || c.chapter));
  const head = mode === "chapter"
    ? `<option value="0">跟当前关（自动）</option>`
    : `<option value="0">跟当前大关（自动）</option>`;
  el.innerHTML = head + (chapters || []).map((c) => {
    const dis = c.unlocked === false ? " disabled" : "";
    const cur = c.current ? "（当前）" : "";
    const prog = `${c.have}/${c.need}` + (c.done ? " 已齐" : ` 缺 ${c.gap}`);
    const to = mode === "chapter" && c.levelid ? ` → 打 ${c.levelid}` : "";
    const nm = c.name ? ` ${c.name}` : "";
    return `<option value="${valOf(c)}"${dis}>第${c.chapter}大关${nm}（${prog}）${to}${cur}</option>`;
  }).join("");
  if ([...el.options].some((o) => o.value === keep)) el.value = keep;
}

async function loadMatchSanguoBox() {
  const r = await fetch("/api/match/sanguo-box")
    .then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchSgBox = r;
  renderMatchSanguoBox();
}

// 一个箱子的层卡明细 + 同层多余可卖
function sgBoxPanel(label, plan, bno) {
  const rows = (plan.slots || []).map((s) => {
    const keep = s.keep || {};
    // 同一层里不同的卡都要收集；只有同名卡拿到 2+ 张，多出来的才是多余
    const extra = s.count > 1 ? `（同名卡拿到 ${s.count} 张，多 ${s.count - 1} 张可卖）` : "";
    // s.g = 游戏界面的层号（1=boss 层只有 1 张，5=要攒 8 张），别用 s.layer（卡库编号，反的）
    return matchRow(`第 ${s.g ?? s.layer} 层`, `${keep.name || "?"}${extra}`);
  }).join("");
  const spare = plan.spare || [];
  return `<div style="border-top:1px solid var(--line);margin-top:6px;padding-top:6px">` +
    `<div style="font-weight:600;margin-bottom:4px">${label}` +
    `<span style="opacity:.7;font-weight:400">　${plan.filled}/${plan.capacity} 格</span></div>` +
    (rows || matchRow("层卡", "（空）")) +
    (spare.length
      ? matchRow("重复多余的（可卖）", spare.map((s) => `${s.name || "卡" + s.card_id}`).join("、")) +
        `<div style="margin:6px 0"><button class="btn sm ok" data-act="mm-sgsell-all" data-box="${bno}">` +
        `卖掉${label}重复的 ${spare.length} 张（换三国币）</button></div>`
      : matchRow("重复多余的（可卖）", "无 —— 同一层的不同卡都要留着收集")) +
    `</div>`;
}

function renderMatchSanguoBox() {
  const box = $("match-sgbox-box"), cnt = $("match-sgbox-cnt");
  const d = matchSgBox || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  const all = d.box || {};
  const ex = all.ex || {}, safe = all.safe || {};
  // 大关下拉（一键出战打哪一大关）：chapters 来自后端 plan_sg_layers，
  // 每项带套名 / 收集进度 / 该大关缺口最小层的关号。
  fillChapterSelect("match-sg-chapter", (d.layers || {}).chapters, sgPickChapter, "chapter");
  cnt.textContent = `关卡卡箱 ${ex.filled || 0}/${ex.capacity || 0} · 保险箱 ${safe.filled || 0}/${safe.capacity || 0}`;

  // ⛔ 不再打印「某套缺的第 N 层卡保险箱压着：…」这类诊断：它把**已经收在保险箱里的卡**
  // 说成「缺」（实测显示「缺的第 3/4 层，保险箱压着：岁在甲子、黄天当立…」—— 人家早有了，
  // 只是躺在保险箱）。真实缺口由下面的层进度表按卡名列全，这里不重复、不误导。
  const diag = "";

  box.innerHTML = `<div class="state-box">` +
    matchRow("下一个该打的关 / 掉落", `${d.levelid ?? "-"} / ${d.dropitem || "-"}`) +
    diag +
    sgLayerPanel(d.layers || {}) +
    sgBoxPanel("关卡卡箱", ex, 0) +
    sgBoxPanel("保险箱", safe, 1) +
    `<div style="opacity:.7;margin:4px 0;font-size:11.5px">` +
    `关卡越靠后掉的卡越少：游戏第 1 层只有 1 张（张角关那档的 boss 层），第 5 层要攒 8 张。` +
    `同一层里不同的卡都要收集，只有同名重复的才卖掉换三国币。` +
    `集齐一套的全部 5 层才能上交、进入下一大关。` +
    `保险箱是另一个箱子、也能卖卡换三国币，但两箱之间怎么搬没有实证，所以只显示、不自动动它。` +
    `</div>` +
    `</div>`;
}

// 层进度 + 该打哪一关。
// ⚠ 层号用游戏口径 lv.g（1＝boss 层只有 1 张、5＝要攒 8 张），**不是** lv.layer
//   （那是卡库位置值编号，跟游戏里显示的正好相反 —— 2026-09-28 用户纠正）。
function sgLayerPanel(L) {
  const rec = L.recommend || null;
  const head = `<div style="border-top:1px solid var(--line);margin-top:6px;padding-top:6px;font-weight:600">` +
    `层进度（关卡越靠后掉卡越少：第 1 层 1 张、第 2 层 3 张、第 5 层 8 张）</div>`;
  const recHtml = rec
    ? `<div style="color:var(--danger);margin:3px 0">` +
      `建议打<b>小关 ${rec.levelid}</b>（游戏第 ${rec.g} 层）：` +
      `${rec.why}，比第 5 层那种 8 张一套的快得多。</div>`
    : `<div style="color:var(--ok,var(--muted,#888));margin:3px 0">每一层都齐了，可以继续上交了。</div>`;
  // 「还差什么」只列**再打一张就能补齐**的卡（差 1 张的），其余整层表里有。
  // 全列的话两套加一起 20 张，反而盖住真正该盯的目标（2026-09-28 实测 20 个卡名刷屏）。
  const near = [];
  (L.levels || []).forEach((lv) => (lv.by_theme || []).forEach((r) => {
    if (r.need - r.have !== 1) return;
    (r.missing || []).forEach((c) => near.push(c.name || "卡" + c.card_id));
  }));
  const lackHtml = near.length
    ? `<div style="margin:3px 0">再打一张就能凑齐的：<b>${[...new Set(near)].join("、")}</b></div>`
    : "";
  const rows = (L.levels || []).map((lv) => {
    const per = (lv.by_theme || []).map((r) => {
      if (r.done) return `${r.theme} 套 ${r.have}/${r.need}（已齐）`;
      // 直接点出还差哪几张的名字，别只给「差 1 张」让人自己猜
      const lack = (r.missing || []).map((c) => c.name || ("卡" + c.card_id)).join("、");
      return `${r.theme} 套 ${r.have}/${r.need}（还缺 ${lack}）`;
    }).join("；");
    // 推荐关号不一定等于 10N（当前关 204 属于第 4 层），别写成 104
    const mark = (rec && rec.layer === lv.layer)
      ? `<br><b style="color:var(--danger)">现在就打小关 ${rec.levelid}</b>` : "";
    // 按箱合计，别按套重复列（两套各 1 张不该写成「关卡卡箱 1 + 关卡卡箱 1」）
    const nEx = (lv.by_theme || []).reduce((s, r) => s + r.have_ex, 0);
    const nSf = (lv.by_theme || []).reduce((s, r) => s + r.have_safe, 0);
    const where = [];
    if (nEx) where.push(`关卡卡箱 ${nEx}`);
    if (nSf) where.push(`保险箱 ${nSf}`);
    const none = where.length
      ? where.join(" + ") + "（两套分着算）"
      : "一个都没有，得打这一档的小关才出";
    return matchRow(`游戏第 ${lv.g} 层 · 小关 ${lv.levelid}`,
      `${per}${mark}<br><span style="opacity:.7;font-size:11.5px">现在：${none}</span>`);
  }).join("");
  return head + recHtml + lackHtml + rows;
}

async function sanguoSellSpare(bno) {
  const boxName = bno === 1 ? "保险箱" : "关卡卡箱";
  const all = (matchSgBox || {}).box || {};
  const plan = bno === 1 ? (all.safe || {}) : (all.ex || {});
  const list = plan.spare || [];
  if (!list.length) { log(`${boxName}没有多余层卡可卖`); return; }
  if (!confirm(`卖掉${boxName}重复多余的 ${list.length} 张层卡（同名卡留一张，换三国币）？\n` +
    list.map((s) => `  第 ${s.g ?? s.layer} 层 · ${s.name || "卡" + s.card_id}`).join("\n") +
    `\n注意：同一层里**不同的卡**是要收集的，只有同名重复的才会出现在这里。`)) return;
  const done = [];
  for (const s of list) {
    const r = await fetch("/api/match/sanguo-sell", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ box: bno, index: s.index, card_id: s.card_id }),
    }).then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
    if (!r || r.ok === false) {
      log(`卖掉「${s.name || s.card_id}」失败：${(r && r.message) || "服务端拒绝"}（已停手，后面的没动）`);
      break;
    }
    done.push(`${s.name || s.card_id} → ${r.content || "三国币"}`);
    log(`${boxName}卖卡：${s.name || s.card_id}（第 ${s.g ?? s.layer} 层）→ ${r.content || "三国币"}`);
  }
  if (done.length) log(`本轮卖掉 ${done.length} 张：${done.join("、")}`);
  await loadMatchSanguoBox();
}

// ---------- 西游记探索（card_worldexplore）· 与三国战役完全独立的玩法 ----------
// 接口口径见 backend/xiyou.py：act=5 出发 / act=6 收收获点 / act=9 卖重复卡 /
// act=16 领任务奖。所有对外层号都用「游戏界口径」（第 1 层＝boss 只有 1 张）。
async function loadMatchXiyou(withTask = false) {
  const url = "/api/match/xiyou" + (withTask ? "?want_task=1" : "");
  const r = await fetch(url).then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchXiyou = r;
  renderMatchXiyou();
  renderMatchXiyouBox();
  renderMatchXiyouGap();
  renderMatchXiyouTeam();
  if (!r || r.ok === false) mlogFail("西游记探索", r);
}

function xyHms(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h} 小时 ${m} 分` : `${m} 分`;
}

function renderMatchXiyou() {
  const box = $("match-xiyou-box"), cnt = $("match-xiyou-cnt");
  const d = matchXiyou || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  const p = d.raw || {}, run = p.run || {}, g = p.gap || {};
  // 大关下拉（出发去哪一大关）：chapters 来自后端 plan_xiyou，已开启的大关都在里面。
  fillChapterSelect("match-xy-chapter", p.chapters, xyPickChapter, "levelid");
  cnt.textContent = `第 ${p.level ?? "-"} 关 · ${p.theme_name || "-"} · ${run.running ? "探索中" : "待出发"}`;
  box.innerHTML = `<div class="state-box">` +
    matchRow("当前大关", `第 ${p.level ?? "-"} 关（${p.theme_name || "-"}），上限第 ${p.maxlevel ?? "-"} 关`) +
    matchRow("探索状态",
      run.running ? `进行中，还剩 ${xyHms(run.remain)}（出发后 24 小时内有效）` : "还没出发（或已收完）") +
    matchRow("可收收获点", run.total ? `${run.got} / ${run.total} 已收${run.pending && run.pending.length ? `，还剩 ${run.pending.length} 个` : "，已全收"}` : "-") +
    matchRow("还差什么", g.need ? `${g.name || ""} ${g.have}/${g.need}` : "-") +
    (g.missing && g.missing.length
      ? matchRow("缺的卡", g.missing.map((m) => `${m.name}（第 ${m.game_layer} 层）`).join("、"))
      : matchRow("缺的卡", g.need ? "一个都不缺，可以上交了" : "-")) +
    `</div>` +
    (run.pending && run.pending.length
      ? `<div class="lib-toolbar" style="gap:6px;flex-wrap:wrap;padding:6px 8px">` +
        run.pending.slice(0, 12).map((i) =>
          `<button class="btn sm" data-act="mm-xy-get" data-idx="${i}" data-idle="收集中…">收第 ${i + 1} 个</button>`).join("") +
        (run.pending.length > 12 ? `<span class="hint">…共 ${run.pending.length} 个</span>` : "") +
        `</div>` : "");
}

function renderMatchXiyouBox() {
  const box = $("match-xy-box"), cnt = $("match-xy-box-cnt");
  const d = matchXiyou || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  const p = d.raw || {}, bs = p.boxes || {};
  const label = { new: "探索收获箱", safe: "保险箱", ex: "关卡卡箱" };
  let free = 0, tot = 0;
  const rows = Object.keys(bs).map((k) => {
    const v = bs[k] || {};
    free += Number(v.free || 0); tot += Number(v.filled || 0);
    return matchRow(label[k] || k,
      `${v.filled ?? 0} 张${v.capacity ? ` / ${v.capacity}` : ""}${v.free ? `（空 ${v.free} 格）` : ""}`);
  }).join("");
  const dupes = p.dupes || [];
  const drows = dupes.map((x) =>
    `<div class="rv"><span class="k">${x.name} × ${x.count}</span>` +
    `<span class="v">${x.boxes.join("+")} 各一份` +
    (x.boxes[0] === x.boxes[1] ? "" : `，可卖第 2 份`) + `</span></div>`).join("");
  cnt.textContent = `共 ${tot} 张 / 空 ${free} 格 · 重复 ${dupes.length} 组`;
  box.innerHTML = `<div class="state-box">${rows || "-"}</div>` +
    (dupes.length
      ? `<div class="rv" style="border-top:1px solid var(--line)"><span class="k" style="font-weight:600">重复卡（卖掉腾空间，找不回来）</span><span class="v">${dupes.length} 组</span></div>` +
        drows +
        `<div class="lib-toolbar" style="gap:6px;flex-wrap:wrap;padding:6px 8px">` +
        dupes.slice(0, 10).map((x, i) => {
          const s = (x.sell || [])[0];
          if (!s) return "";
          return `<button class="btn sm danger" data-act="mm-xy-sell" data-idx="${s.index}" ` +
            `data-cid="${s.card_id}" data-idle="卖卡中…">卖 ${x.name}（第 ${i + 1} 份）</button>`;
        }).join("") + `</div>`
      : `<div class="rv" style="border-top:1px solid var(--line)"><span class="k">重复卡</span><span class="v">没有重复的</span></div>`);
}

function renderMatchXiyouGap() {
  const box = $("match-xy-gap"), cnt = $("match-xy-gap-cnt");
  const d = matchXiyou || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  const p = d.raw || {}, g = p.gap || {}, tasks = p.tasks || [];
  const rows = (g.layers || []).map((L) =>
    `<div class="rv"><span class="k">第 ${L.game_layer} 层 缺</span>` +
    `<span class="v">${L.missing.join("、")}</span></div>`).join("");
  const trows = tasks.map((t) =>
    `<div class="rv"><span class="k">任务 ${t.id}（完成 ${t.cnt}）</span>` +
    `<span class="v">${t.done ? (t.claimable ? "可领奖" : "已领") : "未完成"}</span></div>`).join("");
  cnt.textContent = `${tasks.filter((t) => t.claimable).length} 个任务可领`;
  box.innerHTML = `<div class="state-box">` +
    matchRow("当前大关套", `${g.name || "-"}：${g.have ?? 0}/${g.need ?? 0}`) +
    `</div>` +
    (rows || `<div class="rv"><span class="k">缺的卡</span><span class="v">按层看：没有缺的</span></div>`) +
    `<div class="rv" style="border-top:1px solid var(--line)"><span class="k" style="font-weight:600">每日任务</span><span class="v">${tasks.length} 个</span></div>` +
    (trows || `<div class="rv"><span class="k">任务</span><span class="v">（未拉）</span></div>`) +
    (tasks.filter((t) => t.claimable).length
      ? `<div class="lib-toolbar" style="gap:6px;flex-wrap:wrap;padding:6px 8px">` +
        tasks.filter((t) => t.claimable).map((t) =>
          `<button class="btn sm ok" data-act="mm-xy-task" data-id="${t.id}" data-idle="领奖中…">领任务奖 ${t.id}</button>`).join("") +
        `</div>` : "");
}

function renderMatchXiyouTeam() {
  const box = $("match-xiyou-team"), cnt = $("match-xiyou-team-cnt");
  const d = matchXiyou || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  // 布阵的猫与「最近探索」都在 plan 里（formation / logs），不再读顶层老字段
  const raw = d.raw || {};
  const fl = raw.formation || [];
  const logs = (raw.logs || []).slice(0, 4);
  cnt.textContent = `布阵 ${fl.length} 只 · 记录 ${logs.length}`;
  const rows = fl.map((f) =>
    `<div class="rv"><span class="k">猫 ${f.catid} · 战力 ${f.ce}</span><span class="v">装 ${f.eqlist || "-"}</span></div>`).join("");
  const lrows = logs.map((x) => {
    const got = (x.stepData || []).filter((s) => s && s.isget).length;
    return `<div class="rv"><span class="k">第 ${x.levelid ?? "-"} 关 · ${x.startts || "-"}</span>` +
      `<span class="v">收获 ${got}/${(x.stepData || []).length}</span></div>`;
  }).join("");
  box.innerHTML = `<div class="state-box"><div class="rv" style="font-weight:600">布阵</div>${rows || "-"}
    <div class="rv" style="border-top:1px solid var(--line);font-weight:600">最近探索</div>${lrows || "-"}</div>`;
}

// 三国战役关卡表（sanguo act=9）：只读，字段语义未证实 → 只报条数与原始关卡号
async function loadMatchSanguoLevels() {
  const r = await fetch("/api/match/sanguo-extra?levelid=0")
    .then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchSanguoLevels = r;
  renderMatchSanguoLevels();
}

function renderMatchSanguoLevels() {
  const box = $("match-sanguo-levels"), cnt = $("match-sanguo-levels-cnt");
  const d = matchSanguoLevels || {};
  if (!d.ok) {
    box.innerHTML = `<div class="state-box"><span style="color:var(--danger)">${d.message || "读取失败"}</span></div>`;
    cnt.textContent = "失败";
    return;
  }
  // act=9 的关卡表是 {levelid: {...}} 字典（不是数组），这里统一成数组再展示
  const pack = d.levels || {};
  const raw = pack.data || {};
  const lv = Array.isArray(raw) ? raw
    : Object.keys(raw).map((k) => Object.assign({}, raw[k] || {}, { levelid: (raw[k] || {}).levelid ?? k }));
  cnt.textContent = `${lv.length} 关`;
  const head = lv.slice(0, 12).map((x) =>
    `<div class="rv"><span class="k">关卡 ${x.levelid ?? "-"}</span><span class="v">boss ${x.bossid ?? "-"} · 掉 ${x.dropitem || "-"}</span></div>`).join("");
  // 闯关按钮从「各玩法一键」搬到这里：合法关卡 id 从 101 起（1~9 会被判「错误的关卡ID」）
  const runs = lv.filter((x) => Number(x.levelid) >= 101).slice(0, 3);
  box.innerHTML = `<div class="state-box">${head || "-"}` +
    `<div class="rv"><span class="k">关卡总数</span><span class="v">${lv.length}</span></div>` +
    (lv.length > 12 ? `<div class="empty-tip" style="font-size:12px">只列前 12 条，其余关卡的字段原样返回、未解读。</div>` : "") +
    `</div>` +
    (runs.length ? `<div class="lib-toolbar" style="gap:6px;flex-wrap:wrap;padding:6px 8px">` +
      runs.map((x) => `<button class="btn sm danger" data-act="mm-run-sg" data-levelid="${x.levelid}" data-idle="闯关 L${x.levelid}">闯关 L${x.levelid}（消耗）</button>`).join("") +
      `<span class="hint" style="font-size:12px">闯关会消耗次数（先喂猫到 80 再上阵）。</span></div>` : "");
}

async function loadMatchGold() {
  const r = await fetch("/api/match/gold").then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchGold = r;
  renderMatchGold();
  if (!r || r.ok === false) mlogFail("金卡限购", r);
}

// 切换每日比赛页的子模块：只显示目标子模块，并立刻拉取它的数据（手动操作即时可见）

function arrLen(x) {
  const o = x || {};
  const d = o.data || {};
  if (Array.isArray(d.list)) return d.list.length;
  if (Array.isArray(d.data)) return d.data.length;
  if (Array.isArray(o.list)) return o.list.length;
  return "-";
}

// 子块一键领奖：only 填空即全量（原「一键领奖（全玩法）」也走这里）

async function claimGroup(only) {
  const r = await fetch("/api/match/claim-all", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ only: only || "" }),
  }).then((x) => x.json()).catch(() => null);
  const items = (r && r.items) || [];
  if (!items.length) { mlog("[领奖] 没有可领的项目", "warn"); return false; }
  let ok = 0;
  for (const it of items) {
    const d = (it.r && it.r.data) || {};
    const bad = (it.r && it.r.ok === false) || (d.code !== undefined && d.code !== 0);
    if (bad) {
      mlog("[领奖] " + it.k + " → 无奖励或失败 " + (d.desc || d.code || (it.r && it.r.message) || ""), "");
    } else {
      ok += 1;
      mlog("[领奖] " + it.k + " → " + (d.content || "成功"), "ok");
    }
  }
  mlog("[领奖] 成功 " + ok + " / " + items.length, ok ? "ok" : "");
  return ok > 0;
}

async function claimAll() {
  await claimGroup("");
  await refreshAfterClaim("");
}

// 领奖后刷新对应玩法的视图（「各玩法一键」tab 已删，不能再去刷新它）
async function refreshAfterClaim(only) {
  const m = {
    comp: async () => { await loadMatchState(true); await loadMatchLog(); },
    pvp: async () => { await loadMatchPvp(); await loadMatchPvpExtra(); },
    union: async () => { await loadMatchUnion(); await loadMatchUnionExtra(); await loadMatchGvg(); },
    explore: async () => { await loadMatchXiyou(true); },
    sanguo: async () => { await loadMatchSanguo(true); await loadMatchSanguoLevels(); },
  };
  if (only && m[only]) { await m[only](); return; }
  // 全量领奖：刷新当前停留的子模块（其余切过去时会自己拉）
  const fn = MATCH_SUB_LOAD[window.__matchSub || "home"];
  if (fn) fn();
}

async function pvpFight() {
  // 竞技场状态块就在猫窝日常页，走的就是 /api/match/pvp-extra
  const st = (matchPvpExtra && matchPvpExtra.state && matchPvpExtra.state.data) || {};
  const e0 = (st.enemy_list || [])[0];
  if (!e0) { mlog("[竞技场] 没有可挑战的对手", "warn"); return; }
  if (!confirm("挑战「" + (e0.name || e0.uin) + "」会消耗 1 次竞技场次数，确认？")) return;
  const r = await fetch("/api/match/pvp-fight", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ euin: e0.uin }),
  }).then((x) => x.json()).catch(() => null);
  const d = (r && r.data) || {};
  const bad = (r && r.ok === false) || (d.code !== undefined && d.code !== 0);
  mlog("[竞技场] 挑战 " + (e0.name || e0.uin) + " → " +
    (bad ? (d.desc || d.code || r.message || "失败") : (d.content || "胜利 " + (d.winnum ?? ""))), bad ? "" : "ok");
  await loadMatchPvp(); await loadMatchPvpExtra();
}

async function unionFight() {
  // 出兵按钮在工会 tab 的「公会战」块里（data-act="mm-fight-union"），
  // busyBtn 由 data-act 委托统一处理，这里只管发请求 + 刷视图。
  if (!confirm("公会战出兵会消耗出兵次数，确认？")) return { ok: false, message: "已取消" };
  const r = await fetch("/api/match/union-fight", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }).then((x) => x.json()).catch(() => null);
  const d = (r && r.data) || {};
  const bad = (r && r.ok === false) || (d.code !== undefined && d.code !== 0);
  mlog("[工会] 出兵 → " + (bad ? (d.desc || d.code || r.message || "失败") : (d.content || "成功")), bad ? "" : "ok");
  await loadMatchUnion();
  return r;
}

async function sanguoRun(levelid) {
  if (!levelid) { mlog("[三国] 没选关卡", "warn"); return; }
  if (!confirm("三国闯关 L" + levelid + " 会消耗闯关次数，确认？")) return;
  const r = await fetch("/api/match/sanguo-auto", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ levelid: levelid }),
  }).then((x) => x.json()).catch(() => null);
  if (!r || r.ok === false) { mlog("[三国] 闯关失败：" + ((r && r.message) || "网络异常"), "err"); return; }
  const d = (r.result && r.result.data) || (r.result || {});
  const bad = (d.code !== undefined && d.code !== 0);
  mlog("[三国] 闯关 L" + levelid + "（" + r.eq_count + " 件装 / " + (r.cats || []).join(",") + "）→ " +
    (bad ? (d.desc || d.code) : (d.content || "成功")), bad ? "" : "ok");
  await loadMatchSanguo(true); await loadMatchSanguoLevels();
}

// data-act 委托（document 级，只绑一次）：新增子块/按钮无需再改 bind 列表

function bindMatchModeActs() {
  if (window.__modeActsBound) return;
  window.__modeActsBound = true;
  document.addEventListener("click", (ev) => {
    const b = ev.target && ev.target.closest ? ev.target.closest("[data-act^='mm-']") : null;
    if (!b) return;
    const act = b.dataset.act;
    const restore = busyBtn(b, "处理中…", b.dataset.idle || b.textContent);
    (async () => {
      try {
        if (act === "mm-claim") {
          await claimGroup(b.dataset.only || "");
          await refreshAfterClaim(b.dataset.only || "");
          await loadMatchLog();
        } else if (act === "mm-flush") {
          const r = await fetch("/api/match/pvp-flush", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
          }).then((x) => x.json()).catch(() => null);
          const d = (r && r.data) || {};
          mlog("[竞技场] 刷新对手 → " + ((d.code !== undefined && d.code !== 0)
            ? (d.desc || d.code || (r && r.message) || "失败") : "已刷新"), "");
          await loadMatchPvpExtra(); await loadMatchPvp();
        } else if (act === "mm-fight-pvp") {
          await pvpFight();
        } else if (act === "mm-arena-zan") {
          // 点榜里第一个可赞对象（每天 1 次，已点过后端自动跳过）
          const r = await fetch("/api/home/like", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target: "arena" }),
          }).then((x) => x.json()).catch(() => null);
          const dd = (r && r.data) || r || {};
          mlog("[竞技场] 点赞 → " + (dd.ok === false
            ? (dd.message || dd.code || "失败")
            : (dd.content || dd.message || "已点（或今天已赞过）")),
            dd.ok === false ? "" : "ok");
          await loadMatchPvp(); await loadMatchPvpExtra(); await loadHomeState();
        } else if (act === "mm-fight-union") {
          await unionFight(); await loadMatchUnionExtra(); await loadMatchGvg();
        } else if (act === "mm-run-sg") {
          await sanguoRun(parseInt(b.dataset.levelid, 10));
        } else if (act === "mm-raid-sg") {
          await sanguoRaid();
        } else if (act === "mm-sgbox") {
          await loadMatchSanguoBox();
        } else if (act === "mm-sgsell-all") {
          await sanguoSellSpare(parseInt(b.dataset.box || "0", 10));
        } else if (act === "mm-xy-get") {
          await xyPost("/api/match/xiyou-get", { index: parseInt(b.dataset.idx, 10) },
            "收第 " + (parseInt(b.dataset.idx, 10) + 1) + " 个收获点");
        } else if (act === "mm-xy-sell") {
          await xyPost("/api/match/xiyou-sell",
            { index: parseInt(b.dataset.idx, 10), card_id: parseInt(b.dataset.cid, 10) },
            "卖出 " + (b.dataset.cid || "卡"));
        } else if (act === "mm-xy-task") {
          await xyPost("/api/match/xiyou-task", { id: parseInt(b.dataset.id, 10) },
            "任务奖 " + (b.dataset.id ?? ""));
        }
      } finally {
        restore();
      }
    })();
  });
}

// ---------- 西游记探索的写操作（后端 backend/xiyou.py，参数全有抓包实证）----------
async function xyPost(url, body, what) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  }).then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  const msg = r && r.message ? r.message
    : (r && r.content ? String(r.content) : (r && r.ok ? "完成" : "失败"));
  mlog("[西游记] " + what + " → " + msg, r && r.ok === false ? "" : "ok");
  await loadMatchXiyou(true);
  return r;
}

async function xySellAllDupes() {
  const p = ((matchXiyou || {}).raw) || {};
  const dupes = p.dupes || [];
  if (!dupes.length) { mlog("[西游记] 没有重复的卡，不用腾空间", "ok"); return; }
  if (!confirm(`卖掉这 ${dupes.length} 组重复卡的第 2 份（共 ${dupes.reduce((a, x) => a + (x.sell || []).length, 0)} 张）？卖掉就找不回来了。`)) return;
  for (const x of dupes) {
    for (const s of (x.sell || [])) {
      await xyPost("/api/match/xiyou-sell",
        { index: s.index, card_id: s.card_id }, "卖 " + x.name);
    }
  }
}

// 全量一键领奖：与子块「一键领奖」同一端点，only 传空 = 全跑

function homeRewardText(content) {
  if (!content) return "";
  return String(content).split(",").map((seg) => {
    const p = seg.split("_");
    if (p.length === 3) {
      const base = p[1] === "0" ? p[0] : `${p[0]}_${p[1]}`;
      return (HOME_ITEM_NAME[base] || HOME_ITEM_NAME[p[0]] || seg) + (p[0] === "48" ? `（${p[1]}面额）` : "") + `×${p[2]}`;
    }
    return seg;
  }).join("、");
}

async function loadHomeState() {
  const st = $("home-like-st");
  if (!st) return;
  let d;
  try {
    d = await fetch("/api/home/state").then((x) => x.json());
  } catch (e) { st.textContent = "读取失败：" + e; return; }
  if (!d || d.ok === false) {
    st.textContent = "读取失败：" + (d && d.message || "?");
    homeStateCache = null;
    renderHomeLike(); renderHomeArena();
    return;
  }
  homeStateCache = d;
  renderHomeLike();
  $("home-gacha-st").textContent =
    `免费抽 ${d.gacha.free_used ? "已用" : "可用"} · 积分 ${d.gacha.score ?? "-"}`;
  $("home-cat-st").textContent =
    (d.cats || []).map((c) => `${c.elf_id}:${c.mood ?? "?"}`).join(" · ") +
    `（目标 80）心情不足会自动喂`;
  // 「竞技比赛」块的状态行由 renderHomeArena 拼（还要叠加竞技场接口的名次与对手）
  renderHomeArena();
  // 金币福利：兑红宝石后计数落在 gold_buy_id2（具体字段含义见后端 cathome.py 注释）
  const gi = (d.gold && d.gold.items) || [];
  const ruby = gi.find((g) => g.id === 2) || {};
  const bought = ruby.bought ?? 0;
  $("home-gold-st").textContent = gi.length
    ? `红宝石 已兑 ${bought}/7 次（` +
      (bought >= 7 ? "今日上限，跳过" : "一键时会自动兑") + "）· 其余 " +
      gi.filter((g) => g.id !== 2).map((g) => (g.bought ? `#${g.id}:${g.bought}次` : "未兑")).join(" ")
    : "读取失败";
  // 衣柜锻造：挑「还没锻过（forge=0）且要吃材料最少（chip 最小）」的一件，
  // 与后端 pick_forge_target 同一判据 —— 用户要求「找不花红宝石的锻造」
  const parts = (d.forge && d.forge.parts) || {};
  const pool = [];
  for (const [k, arr] of Object.entries(parts)) for (const it of arr) pool.push({ k, ...it });
  const fresh = pool.filter((x) => (x.forge ?? 0) === 0);
  const best = (fresh.length ? fresh : pool)
    .sort((a, b) => ((a.chip ?? 0) - (b.chip ?? 0)) || ((a.id ?? 0) - (b.id ?? 0)))[0];
  $("home-forge-st").textContent = pool.length
    ? `共 ${pool.length} 件（未锻 ${fresh.length}）` +
      (best ? ` · 建议锻：${best.k}部 ${best.id}（要材料 ${best.chip ?? 0}）` : "")
    : "读取失败";
}

async function homePost(url, body) {
  const r = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) }).then((x) => x.json()).catch(() => null);
  return r;
}

// 「一键全部」：按固定顺序自动跑完每日玩法（用户要的主入口，不要分开的手动按钮）。
// 顺序由后端 run_all 保证：点赞 → 扭蛋 → 换糖（不够时）→ 喂猫到 80 → 竞技自动。

async function homeRunAll(btn) {
  const done = busyBtn(btn, "运行中…", "一键全部（自动跑完）");
  const note = $("home-all-st");
  if (note) note.textContent = "正在跑，请稍候…";
  try {
    const r = await homePost("/api/home/all");
    for (const stp of (r && r.steps) || []) {
      const m = stp.msg || "";
      if (stp.act === "like") mlog(`[点赞] ${m}`, stp.ok ? "ok" : "err");
      else if (stp.act === "gacha") mlog(`[扭蛋机] ${m}`, stp.ok ? "ok" : "warn");
      else if (stp.act === "exchange") mlog(`[扭蛋机] ${m}`, "ok");
      else if (stp.act === "feed") mlog(`[玩耍] ${m}（竞技前自动）`, stp.ok ? "" : "warn");
      else if (stp.act === "gold") mlog(`[金币福利] ${m || "兑红宝石"}`,
                                     stp.ok ? (stp.skipped ? "warn" : "ok") : "err");
      else if (String(stp.act).startsWith("arena:")) {
        if (stp.act === "arena:fight" && stp.ok) {
          mlog(`[竞技] ${stp.msg} → ${stp.win === 1 ? "胜利" : "失败"}${stp.score != null ? ` +${stp.score}分` : ""}`,
              stp.win === 1 ? "ok" : "");
        } else if (stp.act === "arena:claim" && stp.ok) mlog(`[竞技] ${m}`, "ok");
        else if (stp.ok === false) mlog(`[竞技] ${m}`, stp.act === "arena:claim" ? "warn" : "err");
      } else if (stp.act === "skip-group") mlog(`[跳过] ${m}`, "warn");
    }
    for (const c of (r && r.claims) || []) mlog(`[竞技] 领奖 id${c.id} → ${homeRewardText(c.content)}`, "ok");
    if (!r || r.ok === false) {
      mlog(`[一键全部] ${(r && r.message) || "失败"}`, "err");
      if (note) note.textContent = "有步骤失败，看日志";
    } else {
      mlog(`[一键全部] 完成：${r.fights} 战 ${r.wins} 胜，剩余挑战 ${r.surplus_left}`, "ok");
      if (note) note.textContent = `完成 · ${r.fights} 战 ${r.wins} 胜`;
    }
  } finally {
    await loadHomeState();
    done();
    if (note && !/失败/.test(note.textContent || "")) note.textContent = "";
  }
}

function initHomeOnce() {
  if (homeInited) return;
  homeInited = true;
  const b = $("home-all");
  if (b) b.onclick = () => homeRunAll(b);
}

// 切换每日比赛页的子模块：只显示目标子模块，并立刻拉取它的数据（手动操作即时可见）

function initMatchOnce() {
  if (window.__matchInited) return;
  window.__matchInited = true;
  const bind = (id, fn) => {
    const b = $(id);
    if (b) b.onclick = (ev) => { fn(b, ev); };
  };
  // 右栏日志：进页面就把上次留下的记录填回去（与总览页全局日志互不干扰）
  mlogHistory();
  bind("mlog-clear", () => mlogClear());
  // 手动按钮：点下立即禁用 + 「处理中」，完成立即刷新（项目铁律）
  bind("match-refresh", (b) => busyBtn(b, "读取中…", "刷新状态")(loadMatchState(true)));
  bind("match-equip", async (b) => {
    const restore = busyBtn(b, "读取中…", "装备详情");
    const r = await fetch("/api/match/state?want_equip=1").then((x) => x.json()).catch(() => null);
    matchState = r || matchState;
    renderMatchState();
    mlog("[每日比赛] 装备详情：" + (r && r.equip ? "已读取 " + Object.keys(r.equip).length + " 组属性" : (r && r.message) || "空"),
      r && r.equip ? "ok" : "warn");
    restore();
  });
  // 一键比赛：打满今日剩余场次 + 顺带领奖（会消耗 performe_num，函数内已有 confirm）
  bind("match-runall", (b) => busyBtn(b, "比赛进行中…", "一键比赛（打满今日场次）")(runAllMatch()));
  bind("match-race", (b) => busyBtn(b, "出战中…", "打一场（当前装）")(raceOnce()));
  bind("match-dress", (b) => busyBtn(b, "计算中…", "最优穿搭建议")(equipBest()));
  bind("match-dressrace", (b) => busyBtn(b, "出战中…", "按最优装打一场")(equipThenRace()));
  bind("claim-all", (b) => busyBtn(b, "领奖中…", "一键领奖（各玩法奖励，零消耗）")(claimOnly()));
  // 各子玩法的刷新按钮
  // 竞技场状态块在猫窝日常页，走「一键全部」/数据委托刷新，这里不再单独放刷新钮
  // 工会 / 公会战已合并成一个 tab：一次刷新把工会信息、任务、金卡、公会战都带上
  bind("match-union", async (b) => {
    const restore = busyBtn(b, "读取中…", "刷新工会 / 公会战");
    await loadMatchUnion(); await loadMatchUnionExtra();
    await loadMatchGold(); await loadMatchGvg();
    restore();
  });
  bind("match-union-extra", (b) => busyBtn(b, "读取中…", "刷新工会任务/商店")(loadMatchUnionExtra()));
  bind("match-gold", (b) => busyBtn(b, "读取中…", "金卡限购")(loadMatchGold()));
  // 工会玩法动作（结果走全局日志，操作完立刻刷新工会/公会战视图）
  bind("union-all", (b) => unionAll());
  bind("union-claim", (b) => unionClaim());
  bind("union-zan", (b) => unionZan());
  // 「出兵」按钮随公会战块一起移进工会 tab（data-act="mm-fight-union"），这里不再单绑一个
  document.addEventListener("click", async (ev) => {
    const btn = ev.target && ev.target.closest ? ev.target.closest("[data-zan]") : null;
    if (!btn) return;
    ev.stopPropagation();
    const r = await unionAct(btn, "/api/match/union-zan",
      { uins: [btn.getAttribute("data-zan")] }, "赞", "[工会] 已给该成员点赞");
    if (r && r.ok === false) mlog(`[工会] 点赞失败：${r.message || "服务端未接受"}`, "err");
  });
  bind("match-sanguo", async (b) => {
    const restore = busyBtn(b, "读取中…", "刷新三国战役");
    await loadMatchSanguo(true);
    mlog("[每日比赛] 三国战役任务已刷新", "ok");
    restore();
  });
  bind("match-xiyou", async (b) => {
    const restore = busyBtn(b, "读取中…", "刷新");
    await loadMatchXiyou(true);
    mlog("[每日比赛] 西游记探索已刷新", "ok");
    restore();
  });
  bind("match-xy-run", async (b) => {
    const restore = busyBtn(b, "探索跑完中…", "一键探索");
    const r = await fetch("/api/match/xiyou-run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter: xyPickChapter || 0 }),
    }).then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
    const steps = (r && r.steps) || [];
    mlog("[西游记] 一键探索：出发 / 收满 / 领奖 → " +
      (steps.length ? steps.map((s) => `${s.step}${s.ok ? " 成功" : " 失败：" + (s.note || "")}`).join("；") : "没有可做的步骤"),
      r && r.ok === false ? "" : "ok");
    await loadMatchXiyou(true);
    restore();
  });
  bind("match-xy-start", async (b) => {
    const ch = xyPickChapter || 0;
    const chName = ch ? (($("match-xy-chapter") || {}).selectedOptions || [{}])[0].textContent || "" : "";
    if (!confirm(`出发一次高级探索${ch ? `（去第 ${ch} 大关${chName ? " " + chName.replace(/^第\d+大关/, "") : ""}）` : ""}？` +
                 "会消耗一次探索机会，探索最长 24 小时。")) return;
    const restore = busyBtn(b, "出发中…", "出发探索");
    await xyPost("/api/match/xiyou-start", { levelid: ch }, "出发探索");
    restore();
  });
  bind("match-xy-get", async (b) => {
    const restore = busyBtn(b, "收集中…", "收满收获");
    const p = ((matchXiyou || {}).raw || {}).run || {};
    const list = (p.pending || []).slice();
    for (const i of list) await xyPost("/api/match/xiyou-get", { index: i }, "收第 " + (i + 1) + " 个收获点");
    if (!list.length) mlog("[西游记] 当前没有可收的收获点", "ok");
    restore();
  });
  bind("match-xy-task", async (b) => {
    const restore = busyBtn(b, "领奖中…", "领任务奖");
    await xyPost("/api/match/xiyou-task", {}, "领任务奖");
    restore();
  });
  bind("match-xy-sell", async (b) => {
    const restore = busyBtn(b, "腾空间中…", "卖重复卡腾空间");
    await xySellAllDupes();
    restore();
  });
  // 大关下拉：选中值存进变量（面板会轮询重渲染，只写 DOM 会被刷回默认），并说清这次打哪一关。
  const sgSel = $("match-sg-chapter");
  if (sgSel) sgSel.onchange = () => {
    sgPickChapter = parseInt(sgSel.value, 10) || 0;
    const t = sgSel.selectedOptions[0] ? sgSel.selectedOptions[0].textContent : "";
    mlog("[三国] 一键出战的大关：" + (sgPickChapter ? t : "跟随服务端当前关"), "ok");
  };
  const xySel = $("match-xy-chapter");
  if (xySel) xySel.onchange = () => {
    xyPickChapter = parseInt(xySel.value, 10) || 0;
    const t = xySel.selectedOptions[0] ? xySel.selectedOptions[0].textContent : "";
    mlog("[西游记] 出发的大关：" + (xyPickChapter ? t : "跟随当前大关"), "ok");
  };
  bindMatchModeActs();
  // 子模块 tab：全局委托（按钮是页面里的静态元素）
  const subBar = $("match-sub-bar");
  if (subBar) subBar.onclick = (e) => {
    const t = e.target && e.target.closest ? e.target.closest(".tab") : null;
    if (!t) return;
    switchMatchSub(t.getAttribute("data-msub"));
    mlog("[每日比赛] 子模块：" + (t.textContent || "").trim() + " 已加载", "ok");
  };
  // 默认子模块 = 猫窝日常（主界面）；比赛只是子玩法之一
  switchMatchSub(window.__matchSub || "home");
  // 60s 兜底轮询只刷新当前停留的子模块（与手动操作解耦）
  if (!matchSubTimer) matchSubTimer = setInterval(() => {
    const pane = $("pane-match");
    if (!pane || pane.classList.contains("hidden")) return;
    const fn = MATCH_SUB_LOAD[window.__matchSub || "home"];
    if (fn) fn();
  }, 60000);
}

// ---------- 一键喂食 / 一键出战（2026-09-26 真机实打实证）----------
// 心情阈值：游戏里心情不够做不了别的（真机：喂一次 cat_mood +1）。
// 参数：competition act=11 {candy_type:1, elf_id} → 返回 cat_mood；act=4 {catId,id,ids} → 成绩。
async function feedOne(elfId) {
  const r = await fetch("/api/match/feed", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ elf_id: elfId, candy_type: 1 }),
  }).then((x) => x.json()).catch(() => null);
  return r;
}

async function equipBest() {
  // 最优穿搭建议（只读）：今日主题（act=2 实时，每场随机）+ 穷举头/手/衣/各的最优组合
  const r = await fetch("/api/match/theme").then((x) => x.json()).catch(() => null);
  if (!r || r.ok === false) {
    mlog("[每日比赛] 主题读取失败 " + ((r && r.message) || "网络异常"), "warn");
    return null;
  }
  const th = (r.theme || []).map((a) => `${a.name || ("属性" + a.id)} ${a.value}%`).join(" ");
  mlog("[每日比赛] 今日主题：" + (th || "未知") + "（剩 " + (r.performe_num ?? "?") + " 次）", "ok");
  const sug = r.suggest || {};
  if (!sug.equip_ids || !sug.equip_ids.length) {
    mlog("[每日比赛] 算不出最优穿装（衣橱数据为空）", "warn");
    return r;
  }
  // 穿装用中文名 + 属性加成 + 命中情况（后端 cat_dress 从官方配置实证，不是猜的）
  mlog("[每日比赛] 最优穿装：" + (sug.text || sug.equip_ids.join("|"))
      + "（主题加权覆盖 " + sug.cover + "）", "ok");
  for (const c of r.cats || []) {
    if (c.cover == null) continue;
    const gap = (sug.cover - c.cover).toFixed(1);
    mlog("[每日比赛] 猫#" + c.elf_id + "（Lv" + c.level + "）现在穿：" + (c.equip_text || "-")
        + " ｜ 覆盖 " + c.cover + "，比最优低 " + gap
        + (gap > 1 ? " → 建议按上面的组合上台" : ""), "");
  }
  return r;
}

async function equipThenRace() {
  // 每日比赛没有独立的穿装接口：前端搭配好，直接把 4 件装备 id 随 act=4 一起上台
  // （用户 2026-09-26 指出 + 实打验证：ids 可以不是猫当前穿着的）
  const r = await equipBest();          // 读今日主题（act=2）+ 算最优组合
  if (!r) return;
  const ids = (r.suggest && r.suggest.equip_ids) || [];
  if (!ids.length) {
    mlog("[每日比赛] 没算出最优组合，已中止出战（不浪费次数）", "warn");
    return;
  }
  // 出战猫：优先「心情达标 + 满 4 件装备」里等级最高的（用户习惯用等级最高的猫）；
  // 心情不达标的也放进来，后端出战前会先把心情补到 80
  const cats = (r.cats || []).filter((c) => (c.equip || []).filter((x) => x > 0).length === 4);
  if (!cats.length) { mlog("[每日比赛] 没有满 4 件装备的猫，无法出战", "warn"); return; }
  cats.sort((a, b) => ((b.mood ?? 0) >= 80) - ((a.mood ?? 0) >= 80)
                     || (b.level || 0) - (a.level || 0));
  const cat = cats[0];
  const rr = await fetch("/api/match/race", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cat_id: cat.elf_id, equip: ids }),
  }).then((x) => x.json()).catch(() => null);
  const d = (rr && rr.data) ? rr.data : rr || {};
  if (d.code !== 0) {
    mlog("[每日比赛] 出战失败 code=" + d.code + " " + (d.desc || d.message || ""), "warn");
  } else {
    mlog("[每日比赛] " + cat.elf_id + " 号猫带最优装 [" + (d.equip_text || (ids.join("|")))
        + "] 出战：总分 " + d.total_value + " · 评级分 " + d.level_score
        + " · 剩余次数 " + d.performe_num + " · 奖励 " + (d.content || "-"), "ok");
  }
  await loadMatchState(true);
}

async function raceOnce() {
  // 心情会随时间下降：挑猫前必须读最新的比赛状态（act=1 只读）。
  // 心情不足 80 也照样能打 —— /api/match/race 会在后端先补到 80 再出战。
  await loadMatchState(true);
  const st = matchState || {};
  const cats = (st.cats) || [];
  // 挑「心情够 + 有满 4 件装备」的第一只，参数必须取真实值（铁律：坐标/槽位不用固定值）
  const cat = cats.find((c) => c.mood >= 80 && (c.equip || []).filter((x) => x > 0).length === 4)
           || cats.find((c) => (c.equip || []).filter((x) => x > 0).length === 4);
  if (!cat) { mlog("[每日比赛] 没有可出战的猫（需要满 4 件装备）", "warn"); return; }
  const r = await fetch("/api/match/race", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cat_id: cat.elf_id, equip: cat.equip, cat_level: cat.level }),
  }).then((x) => x.json()).catch(() => null);
  const d = r && r.data ? r.data : r || {};
  if (d.code !== 0) {
    mlog("[每日比赛] 出战失败 code=" + d.code + " " + (d.desc || d.message || ""), "warn");
  } else {
    mlog("[每日比赛] 出战 " + cat.elf_id + " 号（" + cat.level + " 级）穿 ["
        + (d.equip_text || (cat.equip || []).join("|")) + "]：总分 " + d.total_value
        + " · 评级分 " + d.level_score + " · 剩余次数 " + d.performe_num
        + " · 奖励 " + (d.content || "-"), "ok");
  }
  await loadMatchState(true);
}

// ---------------- 一键比赛（2026-09-26）----------------
// 后端 run_match_all() 负责「连续打到次数用完」，前端这里补上领奖与状态刷新。
// 会消耗 performe_num，调用前必须 confirm（项目铁律：写操作让用户确认）。

// 属性名（1=唱歌 … 12=灵活）：后端 /api/match/theme 回带 attrs 全表（来自官方配置的实证），
// 拿不到就退回「属性N」，绝不本地瞎编名字。
function attrName(k) {
  const m = matchTheme || {};
  const t = (m.attrs || {})[String(k)];
  return t || ("属性" + k);
}

function raceLine(r) {
  if (!r) return "-";
  if (r.err) return r.err;
  return `#${r.cat_id} · ${r.equip_text || r.equip?.join?.("|") || ""} · 总分 ${r.total_value ?? "-"}`
    + ` · 评级 ${r.level_score ?? "-"} · 覆盖 ${r.cover ?? "-"} · 余 ${r.left ?? "-"}`;
}

async function runAllMatch() {
  const before = (matchState && matchState.performe_num) || 0;
  if (!confirm(`一键比赛将连续打完今日剩余场次（当前剩余 ${before} 次）。\n`
    + "上台奖励由系统自动发放，本操作不再去领其他玩法的奖；会真实消耗比赛次数，确认继续？")) return;
  mlog("[一键比赛] 开始：先把心情喂到 80，再读主题与最优穿装、逐场出战…", "ok");
  const r = await fetch("/api/match/run-all", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }).then((x) => x.json()).catch(() => null);
  if (!r || r.ok === false) {
    mlog("[一键比赛] 失败：" + ((r && r.message) || "网络异常"), "err");
    await loadMatchState(true);
    return;
  }
  const races = r.races || [];
  if (!races.length) {
    mlog("[一键比赛] 一场没打成：" + (r.message || "无出战记录"), "warn");
  } else {
    for (const x of races) mlog("[一键比赛] 第 " + (races.indexOf(x) + 1) + " 场 " + raceLine(x), "ok");
  }
  mlog(`[一键比赛] 完成：打了 ${r.ran || races.length} 场，剩余次数 ${r.left_before ?? "-"} → ${r.left_after ?? "-"}`, "ok");
  if (r.feed && r.feed.ok) {
    mlog(`[一键比赛] 出战前已把心情喂到 80（用掉 ${r.feed.candies ?? 0} 颗糖）`, "ok");
  }
  // 注意：不打这里领奖。一键比赛只管出战，奖励上台后系统自动发；
  // 跨玩法的一键领奖在「猫窝日常」页，由用户自己决定要不要领。
  await loadMatchState(true);
  await loadMatchTheme();
}

// 只领奖：跑现成的 /api/match/claim-all（零消耗，各玩法独立，失败不影响其他项）
async function claimOnly() {
  const r = await fetch("/api/match/claim-all", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }).then((x) => x.json()).catch(() => null);
  const items = (r && r.items) || [];
  if (!items.length) { mlog("[一键领奖] 没有可领的奖励项", "warn"); return false; }
  let ok = 0;
  for (const it of items) {
    if (it.ok) { ok += 1; mlog("[领奖] " + it.k + " → " + (it.content || "成功"), "ok"); }
    else mlog("[领奖] " + it.k + " 失败：" + (it.err || it.desc || "失败"), "warn");
  }
  mlog("[一键领奖] 成功 " + ok + " / " + items.length, ok ? "ok" : "");
  return ok > 0;
}

// ---------------- 今日主题与最优穿装（比赛页新卡） ----------------

function renderMatchTheme() {
  const box = $("match-theme-box"), cnt = $("match-theme-cnt");
  if (!box) return;
  const d = matchTheme || {};
  if (!d.ok) {
    box.className = "state-box";
    box.innerHTML = `<span style="color:var(--danger)">${(d.message || "读取失败")
      + (d.code ? "（code=" + d.code + "）" : "")}</span>`;
    if (cnt) cnt.textContent = "失败";
    return;
  }
  const th = d.theme || [];
  box.className = "state-box";
  const themeTxt = th.length
    ? th.map((a) => `${a.name || ("属性" + a.id)} ${a.value}%`).join(" · ")
    : "未读取到主题";
  const sug = d.suggest || {};
  const ids = sug.equip_ids || [];
  // 组合为各属性提供的加成量（属性名已由后端查表得到）
  const det = Object.entries(sug.theme_cover || {})
    .map(([k, v]) => `${attrName(k)}:${v}`).join("  ");
  const cats = d.cats || [];
  const gap = cats.filter((c) => c.cover != null && sug.cover != null)
    .map((c) => `#${c.elf_id}(Lv${c.level || "-"}) ${c.cover}`).join("　");
  // 每只猫现在穿的什么（中文名 + 加成 + 是否命中主题）
  const worn = cats.filter((c) => c.equip_text)
    .map((c) => `#${c.elf_id}：${c.equip_text}`).join("<br>");
  box.innerHTML =
    matchRow("今日主题", themeTxt) +
    matchRow("最优穿装", sug.text || (ids.length ? ids.join(" | ") : "-")) +
    matchRow("加权覆盖", sug.cover != null ? sug.cover : "-") +
    matchRow("组合属性覆盖", det || "-") +
    matchRow("各猫当前覆盖", gap || "-") +
    matchRow("各猫当前穿装", worn || "-") +
    matchRow("今日剩余次数", d.performe_num != null ? d.performe_num : "-");
  if (cnt) cnt.textContent = th.length ? `${th.length} 项主题` : "空";
}

async function loadMatchTheme() {
  const r = await fetch("/api/match/theme")
    .then((x) => x.json()).catch(() => ({ ok: false, message: "网络错误" }));
  matchTheme = r;
  renderMatchTheme();
  renderMatchLog();   // 属性名表到了，出战记录里的旧主题（裸编号）一并刷成中文
  if (!r || r.ok === false) mlogFail("今日主题", r);
}


// ---------- 合成路径弹窗（点卡箱任意卡 -> 显示所属套卡的合成路径 + 一键炼卡）----------
function closeSynth() {
  $("synth-modal").classList.add("hidden");
  $("synth-body").innerHTML = "";
}

function matsWithCount(names) {
  const out = [];
  const idx = new Map();
  for (const nm of names) {
    if (idx.has(nm)) { idx.get(nm).n++; }
    else { const e = { nm, n: 1 }; out.push(e); idx.set(nm, e); }
  }
  return out.map((e) => (e.n > 1 ? `${e.nm}×${e.n}` : e.nm)).join(" + ");
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
    $("synth-body").innerHTML = renderSynTiers(d);
    mountSynView($("synth-body"), d, () => openSynth(cardId));
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

// ---------- 随机抽卡 / 翻牌（并入总览页；结果只写全局操作日志） ----------
// 2026-09-18 用户要求：不必单独占一个子页，功能放总览、结果放全局操作日志。
// 因此不再维护本页自己的结果列表（原 #lot-body + localStorage mc_lot_log）——
// 一切产出都走 log()，与其它操作同一条时间线，刷新页面也还在。
let lotInitDone = false;
let lotRunning = false;

function initLotteryOnce() {
  if (lotInitDone) return;
  lotInitDone = true;
  $("lot-draw-all").onclick = lotDrawAll;
  $("lot-pai-once").onclick = lotPaiOnce;
  $("daily-run").onclick = dailyRun;
}

// 一键日常：只跑后端 daily.ACTIONS 里「已实证」的动作，结果逐条进全局日志
async function dailyRun() {
  const btn = $("daily-run"), last = $("daily-last");
  btn.disabled = true;
  const old = btn.textContent; btn.textContent = "领取中…";
  try {
    const d = await (await fetch("/api/daily/run", { method: "POST" })).json();
    if (!d.items) { log("一键日常失败：" + (d.message || "未知原因"), "err"); return; }
    let got = false;
    for (const it of d.items) {
      const lvl = it.ok ? "ok" : (it.quota_done ? "warn" : "err");
      log(`一键日常 · ${it.name}：${it.text}`, lvl);
      if (it.ok && it.code === 0 && it.sent_list && it.sent_list.length) {
        for (const x of it.sent_list) {
          log(`  · 48面卡 → ${x.uin}${x.nick ? "（" + x.nick + "）" : ""}${x.exp ? " [exp " + x.exp + "]" : ""}`, "info");
        }
      }
      if (it.ok && it.code === 0) got = true;
    }
    if (last) last.textContent = d.items.map(i => `${i.name}：${i.text}`).join("；");
    if (got) loadState();   // 金币变了，重读一次
  } catch (e) {
    log("一键日常异常：" + e, "err");
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

// 就地刷新总览「随机抽卡」那个 chip（不重绘整块 state-box，避免打断正在看的炉位）
function lotSetChance(n) {
  const el = $("ov-randchip");
  if (el) el.innerHTML = `<span class="k">随机抽卡</span><span class="v">${n} 次</span>`;
}

async function lotDrawAll() {
  if (lotRunning) return;
  lotRunning = true;
  const btn = $("lot-draw-all");
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = "抽卡中…";
  try {
    // 点击前强制刷新，避免前端 stale 导致「明明有机会却抽了 0 次」的误解
    let chance = null;
    try {
      const s = await (await fetch("/api/state")).json();
      if (s.ok && s.user) { chance = s.user.randchance || 0; lotSetChance(chance); }
    } catch {}
    if (chance !== null && chance <= 0) {
      log("一键抽卡：当前没有随机抽卡机会（0 次），等待自动恢复后再抽", "err");
      return;
    }
    const d = await (await fetch("/api/random/draw", { method: "POST" })).json();
    const summary = d.ok
      ? `一键抽卡：抽 ${d.draws} 次，得卡 ${d.cards} 张，自动卖出 10 面额 ${d.sold} 张（+${d.gold} 金），剩余 ${d.remain} 次`
      : `一键抽卡失败：${d.message || d.code || "未知原因"}`;
    log(summary, d.ok ? "ok" : "err");
    if (d.ok) { lotSetChance(d.remain || 0); loadState(); }  // 卡进换卡箱/金币变了，重读一次
  } catch (e) {
    log("一键抽卡异常：" + e, "err");
  } finally {
    lotRunning = false;
    btn.disabled = false; btn.textContent = old;
  }
}

async function lotPaiOnce() {
  const btn = $("lot-pai-once");
  btn.disabled = true; btn.textContent = "翻牌中…";
  try {
    const d = await (await fetch("/api/pai/open", { method: "POST" })).json();
    const items = (d.items || []).map((it) =>
      it.text || it.name || it.cardname || `道具${it.id || ""}`).join("、") || "（无奖励数据）";
    log(`翻牌一次：${d.message || (d.ok ? "成功" : "失败")} → ${items}`, d.ok ? "ok" : "err");
    if (d.ok) loadState();
  } catch (e) {
    log("翻牌异常：" + e, "err");
  } finally {
    btn.disabled = false; btn.textContent = "手动翻一次牌";
  }
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
      // 支持 URL hash 直接打开某个子页（默认 #overview）。
      // #lottery 已并入总览（2026-09-18），老书签落到 overview，别变成空白页。
      let hp = (location.hash || "").replace("#", "");
      if (hp === "lottery") hp = "overview";
      if (hp && ["overview", "lib", "cardbox", "auto", "exchange", "spirite"].includes(hp)) {
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
  // 「单次历练 / 手动收」按钮已随玩法格动态生成（spRenderAll），不再操作固定 id。
  // 玩法切换入口只有**上方状态行**（点一行即切），这里同步设置面板标题里的玩法名。
  const tt = $("sp-set-title");
  if (tt) tt.textContent = "设置 · " + ((SP_MODE_LABELS[mode] || SP_MODE_LABELS.map)[0] || mode);
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
  // 2026-09-19：控制按钮已移入各玩法格内（spRenderAll 动态生成 + 绑定），
  // 不再有固定 id 的 sp-refresh/sp-harvest/sp-start/sp-once/sp-stop 元素。
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
  // 子玩法切换（大地图 / 荒岛 / 穿越丛林）= 点上方三行状态面板的某一行，
  // 绑定在 spRenderAll 里做（行是每次重渲染的，必须重建时绑）。这里不再有 tab 按钮。
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
  // 大地图免费探索次数（灵宠）：free_explore_cnt = 剩余免费次数（每次出发 -1，归零改扣 permanent）；
  // today_fri_explore_cnt = 今日已用（随 explore 变）。总数 = 剩余 + 已用。
  const mFree = Number(d.free_explore_cnt ?? 0);
  const mUsed = Number(d.today_fri_explore_cnt ?? 0);
  const mTotal = mFree + mUsed;
  const mCnt = `免费 ${mUsed}/${mTotal} 次 · 剩 ${mFree}`;
  const mapTxt = (mts && mput.length)
    ? "进行中 " + Math.floor((now - mts) / 60) + " 分 · " + names(mput) + " @ " + mStage + " ｜ " + mCnt
    : "无进行中 · 当前 " + mStage + " ｜ " + mCnt;

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

  const pw = d.power ?? "-";
  const rows = [
    ["map", "大地图", mapTxt, ""],
    ["island", "荒岛", islTxt, spLatestTxt(r.island)],
    ["across", "穿越丛林", acTxt, spLatestTxt(r.across)],
  ];
  box.innerHTML = rows.map(([mode, nm, txt, extra]) => {
    const au = (spAutoModes && spAutoModes[mode]) || {};
    const hv = Number(au.harvests || 0);
    const running = !!au.running;
    const on = spMode === mode;
    // 该玩法专属操作按钮 —— **只在当前选中格内显示**（其余格纯状态，不抢空间）
    let acts = "";
    if (on) {
      const b = ['<span class="sp-pw">⚡ 体力 ' + esc(String(pw)) + "</span>",
        '<button class="btn sm ghost" data-act="refresh">刷新</button>'];
      if (mode === "across") {
        if (acLeft > 0) b.push('<button class="btn sm primary" data-act="once">单次历练</button>');
      } else {
        const can = mode === "island" ? (its && iput.length) : (mts && mput.length);
        if (can) b.push('<button class="btn sm ok" data-act="harvest">手动收</button>');
      }
      b.push('<button class="btn sm ok" data-act="start"' + (running ? " disabled" : "") + ">开始自动</button>");
      b.push('<button class="btn sm ghost" data-act="stop"' + (running ? "" : " disabled") + ">停止</button>");
      acts = '<div class="sp-acts">' + b.join("") + "</div>";
    }
    return '<div class="sp-all-row' + (on ? " on" : "") + '" data-mode="' + mode + '">'
      + '<span class="n">' + esc(nm) + '</span><span class="v">' + esc(txt) + "</span>"
      + (extra ? '<span class="x">' + esc(extra) + "</span>" : "")
      + (running ? '<span class="x" style="color:#1e7e34;font-weight:600">● 自动中'
          + (hv ? " · 已收 " + hv : "") + "</span>" : "")
      + acts
      + "</div>";
  }).join("");
  // 整行点击 = 切玩法；格内按钮点击 = 执行该玩法专属操作（阻止冒泡，避免重复切格）
  for (const el of box.querySelectorAll(".sp-all-row")) {
    el.addEventListener("click", () => spSetMode(el.dataset.mode));
  }
  for (const el of box.querySelectorAll("[data-act]")) {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const mode = el.closest(".sp-all-row").dataset.mode;
      const a = el.dataset.act;
      if (a === "refresh") spRefresh(el);
      else if (a === "harvest") spHarvest(mode, el);
      else if (a === "once") spOnce(mode, el);
      else if (a === "start") spStart(mode, el);
      else if (a === "stop") spStop(mode, el);
    });
  }
}

function spRenderState(d) {
  spLastData = d;
  // 2026-09-19 精简：状态只留一套（下方三行面板，三套玩法同屏）。
  // 工具栏的那几块「免费次数 / 当前探险 / 累计收获」chip 已删，别再往里写（元素不存在会炸）。
  const pw = $("sp-power");
  if (pw) pw.textContent = d.power ?? "-";
  spRenderWallet(d);
  const ex = d.explore || {};
  const isl = d.island || {};
  // 「收」按钮是否可用：只有当前玩法**确有进行中的探险**才亮。
  // 穿越丛林是即时历练（没有进行中状态），永远不亮。
  const active = spMode === "island"
    ? !!(isl.explore_ts && (isl.spirites || []).length)
    : spMode === "map" && !!(ex.explore_ts && (ex.put_spirites || []).length);
  // 「收」按钮可用性由 spRenderAll 内按 on 格动态生成（有进行中才显示该按钮）
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
async function spHarvest(mode, el) {
  mode = mode || spMode;
  const restore = el ? busyBtn(el, "收中…") : null;
  const isIsland = (mode === "island");
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
    spTick();
  } catch (e) { spSay("收获异常：" + e, "err"); }
  finally { if (restore) restore(); }
}

// 穿越丛林「单次历练」：act=11，**即时到账**（没有收获步骤）。
// 参数由后端按 (1 级地图, 2 级地图) 换算成 id=1级-1、sub_id=2级-1（0-based，对应官方 open_c/open_s）。
async function spOnce(mode, el) {
  mode = mode || spMode;
  if (mode !== "across") { spSay("单次历练只在「穿越丛林」玩法下可用", "warn"); return; }
  const seq = Number($("sp-pass").value || 0);
  const found = spAcrossBySeq(seq);
  if (!found) { spSay("请选择要历练的关卡", "warn"); return; }
  const restore = el ? busyBtn(el, "历练中…") : null;
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
  finally { if (restore) restore(); }
}

async function spRefresh(el) {
  const restore = el ? busyBtn(el, "刷新中…") : null;
  try {
    const d = await (await fetch("/api/cat/travel")).json();
    if (!d.ok) { spSay("查询失败：" + (d.message || d.code), "err"); return; }
    if (d.recent) spRecent = d.recent;
    spRenderState(d);
    if (!spMapIds.length || !Object.keys(spSpirits).length) await spLoadTable();
    if (!spPicked[spMode]) spApplyDefault();
    spRenderSpirites(d.spirites);
    spSay("状态：免费 " + d.free_explore_cnt + " 次 / 灵力 " + d.power
      + " / 灵宠 " + (d.spirites || []).length + " 只", "info");
  } catch (e) { spSay("查询异常：" + e, "err"); }
  finally { if (restore) restore(); }
}

async function spStart(mode, el) {
  mode = mode || spMode;
  spSetMode(mode);   // 同步切下拉到该玩法，下方按 spMode 读下拉构造 body
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
  const restore = el ? busyBtn(el, "启动中…") : null;
  try {
    const d = await (await fetch("/api/spirite/auto/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).json();
    spSay(d.message || (d.ok ? "已启动" : "启动失败"), d.ok ? "ok" : "err");
    if (!d.ok) return;
    log(`开始灵宠自动探险：${where}` + (body.dry_run ? "（模拟）" : ""), "ok");
    // 按钮态由 spPoll → spRenderAll 按 spAutoModes[mode].running 统一刷新
    if (spTimer) clearInterval(spTimer);
    spTimer = setInterval(spPoll, 3000);
    spPoll();
  } catch (e) { spSay("启动异常：" + e, "err"); }
  finally { if (restore) restore(); }
}

async function spStop(mode, el) {
  mode = mode || spMode;
  const restore = el ? busyBtn(el, "停止中…") : null;
  try {
    const d = await (await fetch("/api/spirite/auto/stop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    })).json();
    spSay(d.message || "已停止", d.ok ? "warn" : "info");
    if (d.ok) log("停止灵宠自动探险（" + (SP_MODE_LABELS[spMode] ? SP_MODE_LABELS[spMode][0] : spMode) + "）", "warn");
  } catch (e) { spSay("停止异常：" + e, "err"); }
  finally { if (restore) restore(); }
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
    // 「累计收获」工具栏 chip 已删，数值并进三行面板的「● 自动中」徽标（见 spRenderAll）。
    const cur = modes[spMode] || {};
    if (cur.state && (cur.state.pass_id !== undefined || cur.state.explore)) spRenderState(cur.state);
    const running = !!cur.running;
    // 按钮态（开始/停止 disabled）由下方 spRenderAll 按 spAutoModes[mode].running 统一刷新
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
