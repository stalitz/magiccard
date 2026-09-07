const $ = (id) => document.getElementById(id);

let pollTimer = null;
let loggedIn = false;

// ---------- 操作日志 ----------
function log(msg, cls = "info", where = "login") {
  const box = $(where === "dash" ? "log-dash" : "log");
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
}
function showDash(uin) {
  loggedIn = true;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  $("login-view").classList.add("hidden");
  $("dash-view").classList.remove("hidden");
  $("dash-uin").textContent = uin || "(未知)";
  log(`已进入主面板，当前账号 ${uin || ""}`, "ok", "dash");
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
  // waiting / error：静默继续轮询
}

// 切回前台时保证轮询在跑（修复后台标签被节流的问题）
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !loggedIn && !pollTimer) startPoll();
});

// ---------- 快速登录（本机 QQ） ----------
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

// ---------- 探测真实接口 ----------
$("probe-btn").onclick = async () => {
  const btn = $("probe-btn");
  btn.disabled = true;
  btn.textContent = "探测中…";
  log("开始探测魔法卡片真实接口…", "info", "dash");
  try {
    const r = await fetch("/api/probe");
    const d = await r.json();
    if (d.hosts_found && d.hosts_found.length)
      log("入口页出现的主机：" + d.hosts_found.join("、"), "info", "dash");
    else if (d.page_error)
      log("拉取入口页失败：" + d.page_error, "err", "dash");
    (d.tests || []).forEach((t) => {
      if (t.error) log(`${t.base} → 错误：${t.error}`, "err", "dash");
      else log(`${t.base} → HTTP ${t.status}　${t.head.replace(/\s+/g, " ").slice(0, 80)}`, t.status === 200 ? "ok" : "warn", "dash");
    });
    log("探测完成，详见上方结果", "ok", "dash");
  } catch (e) {
    log("探测请求失败：" + e, "err", "dash");
  } finally {
    btn.disabled = false;
    btn.textContent = "探测真实接口";
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
