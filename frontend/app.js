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
  const inOverview = pane === "overview";
  $("refresh-btn").classList.toggle("hidden", !inOverview);
  $("auto-refresh").classList.toggle("hidden", !inOverview);
  if (pane === "lib") initLibOnce();
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
      return;
    }
  } catch {}
  showLogin();
  loadQr();
})();
