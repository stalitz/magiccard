const $ = (id) => document.getElementById(id);

let pollTimer = null;
let stateTimer = null;
let loggedIn = false;

// ---------- 操作日志（右侧控制台 #log）----------
function log(msg, cls = "info") {
  const box = $("log");
  if (!box) return;
  const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const line = document.createElement("div");
  line.innerHTML = `<span class="t">[${ts}] </span><span class="${cls}">${msg}</span>`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

// ---------- 视图切换 ----------
function showLogin() {
  $("login-view").classList.remove("hidden");
  $("dash-view").classList.add("hidden");
  $("top-user").textContent = "";
}
function showDash(uin) {
  loggedIn = true;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  $("login-view").classList.add("hidden");
  $("dash-view").classList.remove("hidden");
  $("dash-uin").textContent = uin || "(未知)";
  $("top-user").textContent = uin ? `QQ ${uin}` : "";
  log(`已进入主面板，当前账号 ${uin || ""}`, "ok");
  loadState();
  if (stateTimer) clearInterval(stateTimer);
  if ($("auto-refresh").checked) stateTimer = setInterval(loadState, 60000);
}

// ---------- 二维码登录 ----------
async function loadQr() {
  try {
    log("正在获取二维码…", "info");
    const r = await fetch("/api/login/qr");
    const d = await r.json();
    $("qr-img").src = d.qr;
    setQrStatus("请使用手机 QQ 扫码", "");
    log("二维码已生成，等待扫码（切到手机后本页轮询会节流，记得切回来）", "info");
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

function startPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOnce, 2500);
}

async function pollOnce() {
  if (loggedIn) return;
  let res;
  try {
    res = await (await fetch("/api/login/poll")).json();
  } catch (e) {
    return; // 网络抖动，下一轮再试
  }
  const s = res.status;
  if (s === "scanned") {
    setQrStatus("已扫描，请在手机上点击确认", "warn");
    log("手机已扫描，待确认", "warn");
  } else if (s === "ok") {
    log(`登录成功！账号 ${res.uin}`, "ok");
    showDash(res.uin);
  } else if (s === "expired") {
    clearInterval(pollTimer);
    pollTimer = null;
    setQrStatus("二维码已失效，请点击刷新", "warn");
    log("二维码已失效，请点「刷新二维码」", "warn");
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !loggedIn && !pollTimer) startPoll();
});

// ---------- 快速登录（本机 QQ）----------
async function checkQuick() {
  const info = $("quick-info");
  const btn = $("quick-launch");
  try {
    const r = await fetch("/api/login/quick");
    const d = await r.json();
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

// ---------- Tab 切换 ----------
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

$("qr-refresh").onclick = loadQr;
$("logout").onclick = async () => {
  try { await fetch("/api/logout"); } catch {}
  location.reload();
};

// ---------- 游戏状态 ----------
function fmtLeft(sec) {
  if (sec <= 0) return "已完成";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h > 0 ? `${h}小时${m}分` : (m > 0 ? `${m}分${s}秒` : `${s}秒`);
}

function chip(k, v, small = "") {
  return `<div class="stat-chip"><span class="k">${k}</span><span class="v">${v}${small ? `<small>${small}</small>` : ""}</span></div>`;
}

async function loadState() {
  const box = $("state-box");
  try {
    const d = await (await fetch("/api/state")).json();
    if (!d.ok) {
      box.innerHTML = `<div class="err" style="grid-column:1/-1">读取失败：code=${d.code ?? "?"} ${d.message || ""}</div>`;
      log(`读取游戏状态失败：code=${d.code ?? "?"} ${d.message || ""}`, "err");
      return;
    }
    const u = d.user || {};
    const sb = d.stovebox || {}, cb = d.changebox || {}, st = d.storebox || {};

    const slots = (sb.cards || []).map((c) => {
      const cls = c.id === 0 ? "empty" : (c.flag === 2 ? "done" : "busy");
      const txt = c.id === 0 ? "空位" : (c.flag === 2 ? "可取卡" : `炼制中 ${fmtLeft(c.left)}`);
      return `<span class="slot ${cls}"><span class="sname">炉位 ${c.slot}</span>${txt}</span>`;
    }).join("") || '<span class="slot empty">无炼卡位数据</span>';

    // 炼炉状态摘要
    const stoves = (d.stoves || []).map((s) => {
      const left = s.endtime - d.now;
      const state = left <= 0 ? "空闲" : `炼制中 ${fmtLeft(left)}`;
      return { id: s.id, state };
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
      chip("抽卡机会", `${u.randchance || 0} 次`) +
      chip("炼卡位", `${sb.cur || 0} / ${sb.max || 0}`, doneSlots ? `${doneSlots} 位可取` : "") +
      chip("换卡箱", `${cb.filled ?? 0} / ${cb.max || 0}`) +
      chip("保险箱", `${st.filled ?? 0} / ${st.max || 0}`) +
      chip("炼炉", busyN ? `${busyN} 个炼制中` : "全部空闲") +
      `<div class="stat-chip" style="grid-column:1/-1">
         <span class="k">炼卡位明细</span>
         <div class="slot-list">${slots}</div>
       </div>` +
      `<div class="stat-chip" style="grid-column:1/-1">
         <span class="k">${stoveTxt}</span>
       </div>`;
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

// ---------- 探测真实接口 ----------
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
    log("探测完成：card.show.qq.com 活着，返回标准 XML；appimg 302 / qzs 404 已死。", "ok");
  } catch (e) {
    log("探测请求失败：" + e, "err");
  } finally {
    btn.disabled = false; btn.textContent = "探测接口（调试）";
  }
};

// ---------- 启动 ----------
(async () => {
  log("前端已加载", "info");
  try {
    const me = await (await fetch("/api/me")).json();
    if (me.logged_in) {
      log(`检测到已有登录态（${me.uin}）`, "ok");
      showDash(me.uin);
      return;
    }
  } catch {}
  showLogin();
  loadQr();
})();
