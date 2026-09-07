const $ = (id) => document.getElementById(id);

let pollTimer = null;
let stateTimer = null;
let loggedIn = false;

// 卡库页状态
const libState = {
  mode: "price",          // price | theme
  price: "", kw: "", star: 0, ownOnly: false,
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

// ---------- 操作日志 ----------
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
  switchPane("overview");
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
    log("二维码已生成，等待扫码", "info");
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
  } catch { return; }
  const s = res.status;
  if (s === "scanned") {
    setQrStatus("已扫描，请在手机上点击确认", "warn");
    log("手机已扫描，待确认", "warn");
  } else if (s === "ok") {
    log(`登录成功！账号 ${res.uin}`, "ok");
    showDash(res.uin);
  } else if (s === "expired") {
    clearInterval(pollTimer); pollTimer = null;
    setQrStatus("二维码已失效，请点击刷新", "warn");
    log("二维码已失效，请点「刷新二维码」", "warn");
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

$("qr-refresh").onclick = loadQr;
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
  $("pane-exchange").classList.toggle("hidden", pane !== "exchange");
  const inOverview = pane === "overview";
  $("refresh-btn").classList.toggle("hidden", !inOverview);
  $("auto-refresh").classList.toggle("hidden", !inOverview);
  if (pane === "lib") initLibOnce();
  if (pane === "exchange") initExchangeOnce();
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
  themeTid: 0,
  themeName: "",
  price: "",
  apiPeers: [],        // 服务器按主题搜到的卡友
  manualPeers: [],     // 手动补充（持久化在服务端）
  manualText: "",
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
}

function exMsg(text, cls = "warn") {
  const body = $("ex-body");
  const m = document.createElement("div");
  m.className = `ex-msg ${cls}`;
  m.textContent = text;
  body.insertBefore(m, body.firstChild);
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
    box.innerHTML = d.themes.map((t) => {
      const tags = [t.flash ? '<span class="tag flash">闪</span>' : "",
                    t.offline ? '<span class="tag off">已下架</span>' : ""].join("");
      return `<span class="chip" data-tid="${t.tid}" data-name="${esc(t.name)}">${esc(t.name)} ${tags}</span>`;
    }).join("");
    box.querySelectorAll(".chip[data-tid]").forEach((c) => {
      c.onclick = () => {
        box.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
        c.classList.add("active");
        selectTargetTheme(Number(c.dataset.tid), c.dataset.name);
      };
    });
  } catch (e) {
    box.innerHTML = `<span style="color:var(--err)">搜索失败：${esc(e.message || e)}</span>`;
  }
}

function selectTargetTheme(tid, name) {
  exState.themeTid = tid;
  exState.themeName = name;
  exState.apiPeers = [];
  clearResults();
  $("ex-target").textContent = `目标：${name}（#${tid}）`;
  $("ex-cnt").textContent = "";
  $("ex-theme-cands").classList.add("hidden");
  hideAutoPeers();
  exMsg(`目标套卡已设为「${name}」。点「找该套卡友」拉取玩家列表。`, "ok");
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
  if (!exState.themeTid) { exMsg("请先搜并选定想收的套卡", "err"); return; }
  const btn = $("ex-peers-btn");
  btn.disabled = true; btn.textContent = "拉取中…";
  try {
    const d = await (await fetch(`/api/exchange/peers?tid=${exState.themeTid}`)).json();
    if (!d.ok) throw new Error(d.message || "拉取失败");
    exState.apiPeers = d.api_peers || [];
    exState.manualPeers = d.manual_peers || [];
    renderManualPeers();
    renderAutoPeers(exState.apiPeers);
    const auto = exState.apiPeers.length;
    const man = exState.manualPeers.length;
    $("ex-cnt").textContent = `自动卡友 ${auto} 人 + 手动 ${man} 人`;
    exMsg(`找到 ${auto} 个正在玩「${exState.themeName}」的卡友${man ? `，加手动名单共 ${auto + man} 人` : ""}。点「扫描卡友换卡箱」开始扫描。`, "ok");
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
  if (!exState.themeTid) { exMsg("请先搜并选定想收的套卡", "err"); return; }
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
  exMsg(manualOnly
    ? `开始扫描 ${manual.length} 个手动卡友的换卡箱，约需 ${Math.max(3, Math.round(manual.length / 6))} 秒…`
    : "开始扫描：自动拉取该套在玩玩家并逐个读取换卡箱。服务器限流约 15 人/10 秒，扫满 80 人约需 1~2 分钟，请稍候…", "info");
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
        theme_id: exState.themeTid,
        price: Number(exState.price) || 0,
        peers: manualOnly ? manual : [],
        use_api: !manualOnly,
        manual_peers: manual,
        concurrency: 5,
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

  let html = "";
  const priceTxt = d.price ? `面值 ${d.price} 的` : "全部面值的";
  $("ex-cnt").textContent =
    `「${d.target_name}」${priceTxt}卡：扫 ${d.scanned}/${d.peers_total} 人，${d.hit_users} 人有货` +
    (d.errors_n ? `，${d.errors_n} 人读取失败` : "") + `（${d.elapsed}s）`;

  if (d.errors_n) {
    html += `<div class="ex-msg warn">${d.errors_n} 个卡友主页读取失败（可能对方未开卡主页/网络抖动）。<br>${d.errors.slice(0, 8).map((e) => `QQ ${e.uin}：${esc(e.error)}`).join("<br>")}</div>`;
  }
  if (!d.rows || !d.rows.length) {
    html += `<div class="empty-tip" style="padding:30px 16px">${d.errors_n ? "" : "扫描完成，但"}没人在换卡箱放出「${d.target_name}」的${priceTxt}卡。<br>换个时间再扫（换卡箱里的卡会被换走/上锁），或补充手动卡友。${d.my_exch && d.my_exch[0] ? "" : ""}</div>`;
    list.innerHTML = html;
    return;
  }

  const myExch = (d.my_exch || []).filter((x) => x);
  const myExchTxt = myExch.length
    ? `我的换卡设置：想收 ${myExch.join(",")}`
    : "";
  if (myExchTxt) html += `<div class="ex-msg ok">${esc(myExchTxt)}（别人来我家时按此给我套卡）</div>`;
  html += `<div class="ex-msg info" style="background:#eef4fa;color:#2b6cb0">换卡规则（实测）：对方换卡箱里未上锁的卡才能换；<b>我付出的卡必须是“对方想收的套”里的同面值卡</b>（下方「需付出」列出）。对方若未设想收套（灰显）则无法交换。目标套若是已下架套，同理也可选。</div>`;

  list.innerHTML = html;

  d.rows.forEach((row) => {
    const peerEl = document.createElement("div");
    peerEl.className = "ex-peer";
    // 该卡友所有命中（含未开放交换的）
    const hitHtml = row.hits.map((h) => {
      const wantOk = h.want_theme_id ? true : false;
      const lockedNow = h.unlock > Math.floor(Date.now() / 1000);
      const srcOpts = h.srcs.length
        ? `<select data-friend="${row.uin}" data-dst="${h.id}_${h.slot}" data-src="">
             <option value="">选择付出的卡…</option>
             ${h.srcs.map((s) => `<option value="${s.id}_${s.slot}_1">${esc(s.name)}（面值 ${s.price}，${esc(s.theme_name)}）</option>`).join("")}
           </select>
           <button class="btn sm primary ex-swap" data-friend="${row.uin}"
                   data-dst-id="${h.id}" data-dst-slot="${h.slot}">交换</button>`
        : "";
      const note = [];
      if (!wantOk) note.push('<span class="tag off" style="background:#f0f2f5;color:#7a8699">对方未设想收套 · 不可换</span>');
      if (lockedNow) note.push('<span class="tag off">对方上锁至 ' + new Date(h.unlock * 1000).toLocaleTimeString("zh-CN", { hour12: false }) + '</span>');
      return `<div class="ex-hit">
        <span class="cnm">${esc(h.name)} <span class="v-badge" style="background:#eef4fa;color:#66738a">面值 ${h.price}</span></span>
        <span class="pay" style="font-size:12px">${h.want_theme_id ? `需付出：${esc(h.want_theme_name)}·${h.price}面值` : ""}${srcOpts ? "" : (wantOk ? '<span class="ex-nopay">（你没有该套卡，无法换）</span>' : "")}</span>
        ${note.join(" ")}
        ${srcOpts}
      </div>`;
    }).join("");

    peerEl.innerHTML = `<div class="ph">
        <span class="nick">${esc(row.nick)}</span>
        <span class="qq">QQ ${row.uin}</span>
        <span class="want">他想要的套：${row.hits[0].want_theme_id ? esc(row.hits[0].want_theme_name) : "未设置"}</span>
        <span style="margin-left:auto;color:var(--muted);font-size:12px">${row.hits.length} 张可换目标</span>
      </div>
      <div class="hits">${hitHtml}</div>`;
    list.appendChild(peerEl);
  });

  // 交换事件
  list.querySelectorAll(".ex-swap").forEach((btn) => {
    btn.onclick = () => {
      const hit = btn.closest(".ex-hit");
      const sel = hit.querySelector("select");
      if (!sel || !sel.value) { exMsg("请先选择要付出的卡", "err"); return; }
      const [srcId, srcSlot] = sel.value.split("_");
      if (!confirm(`确认用这张卡交换？\n付出：${sel.options[sel.selectedIndex].text}\n换入：${hit.querySelector(".cnm").textContent.trim()}\n\n交换会真实消耗付出卡。`)) return;
      doSwap(btn.dataset.friend, Number(btn.dataset.dstId), Number(btn.dataset.dstSlot),
             Number(srcId), Number(srcSlot));
    };
  });
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
        c.flash ? '<span class="tag flash">闪</span>' : "",
        c.offline ? '<span class="tag off">已下架</span>' : "",
      ].join("");
      const rec = c.recipe
        ? `<div class="sub"><span class="tag refinable" style="margin-right:4px">配方</span>${esc(c.recipe.mats.join(" + "))}　耗时 ${fmtDur(c.recipe.secs)}</div>`
        : (c.own ? "" : '<div class="sub">基础素材卡（无合成配方，只能抽卡/获得）</div>');
      return `<div class="it" data-tid="${c.theme_id}" data-cid="${c.id}">
        <div class="r1"><span class="nm">${esc(c.name)}</span>
          ${tags}
          <span class="own-badge">${c.own ? `持 ${c.own}` : "未持有"}</span>
          <span class="meta">${esc(c.theme_name)} ${stars(c.star)}</span>
          <span class="v-badge">面值 ${c.price}</span></div>
        ${rec}</div>`;
    }).join("");
    $("lib-list").querySelectorAll(".it").forEach((el) => {
      el.onclick = () => openTheme(el.dataset.tid, el.dataset.cid);
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
    params.set("limit", 900);
    const d = await (await fetch(`/api/cards/themes?${params}`)).json();
    $("lst-title").textContent = "浏览套卡";
    $("lst-cnt").textContent = `${d.shown} / ${d.total} 套`;
    $("lst-foot").textContent = "按新上架排序；在右侧看每张卡的面值与合成路径。";
    if (!d.themes.length) {
      $("lib-list").innerHTML = '<div class="empty-tip">没有匹配的套卡，换个关键词/星级。</div>';
      return;
    }
    $("lib-list").innerHTML = d.themes.map((t) => {
      const tags = [t.flash ? '<span class="tag flash">闪卡</span>' : "",
                    t.offline ? '<span class="tag off">已下架</span>' : ""].join(" ");
      return `<div class="it" data-tid="${t.tid}">
        <div class="r1"><span class="nm">${esc(t.name)}</span> ${stars(t.star)}
          ${tags}<span class="meta">${t.n} 张 · 面值 ${t.pmin}~${t.pmax}</span></div>
        <div class="sub">主题 #${t.tid}</div></div>`;
    }).join("");
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
    $("dt-sub").textContent = `主题 #${t.tid} · ${t.n} 张` + (t.flash ? " · 闪卡" : "") + (t.offline ? " · 已下架" : "");
    detail.innerHTML =
      `<div class="card-row"><div class="head">${esc(t.name)}　${stars(t.star)}
         ${t.flash ? '<span class="tag flash">闪卡</span>' : ""}
         ${t.offline ? '<span class="tag off">已下架</span>' : ""}
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
            <span style="margin-left:auto"><button class="mini-btn ${libState.openChains.has(c.id) ? "chain-on" : ""}" data-act="chain">${libState.openChains.has(c.id) ? "收起路径" : "合成路径"}</button></span>
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

// ---------- 启动 ----------
(async () => {
  log("前端已加载", "info");
  try {
    const me = await (await fetch("/api/me")).json();
    if (me.logged_in) {
      log(`检测到已有登录态（${me.uin}）`, "ok");
      showDash(me.uin);
      // 支持 URL hash 直接打开某个子页（默认 #overview）
      const hp = (location.hash || "").replace("#", "");
      if (hp && ["overview", "lib", "exchange"].includes(hp)) {
        setTimeout(() => switchPane(hp), 50);
      }
      return;
    }
  } catch {}
  showLogin();
  loadQr();
})();
