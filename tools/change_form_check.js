// 离线验证：用真实接口数据 + app.js 里真实的 initChangeForm，在 jsdom 里跑一遍，
// 检查变卡选择器在「列表 + 搜索 + 面值分组」下的可选择性（不是靠读代码推断）。
// 用法: NODE_PATH=<workspace>/node_modules node tools/change_form_check.js
const fs = require("fs");
const vm = require("vm");
const { JSDOM } = require("jsdom");

const src = fs.readFileSync(__dirname + "/../frontend/app.js", "utf8");
const html = fs.readFileSync(__dirname + "/../frontend/index.html", "utf8");

const dollar = src.match(/const \$ = \(id\) => document\.getElementById\(id\);\n/);
const escM = src.match(/const esc = \(s\) =>[\s\S]*?\)\);\n/);
const start = src.indexOf("const VALID_PROP_FACES");
const end = src.indexOf("// ---------- 合成路径弹窗");
if (!dollar || !escM || start < 0 || end < 0) throw new Error("找不到变卡代码块，app.js 结构可能变了");
const block = src.slice(start, end);

const dom = new JSDOM(html);
const $ = (id) => dom.window.document.getElementById(id);
const face = (el) => { const m = /(\d+)\s*面/.exec(el.querySelector(".pk-f").textContent); return m ? Number(m[1]) : null; };

(async () => {
  const dState = await (await fetch("http://127.0.0.1:8000/api/state")).json();
  const sandbox = {
    document: dom.window.document, window: dom.window, console,
    fetch: (u, o) => fetch("http://127.0.0.1:8000" + u, o),
    log: () => {},
    cbState: { change: dState.changebox || null, store: dState.storebox || null },
  };
  vm.createContext(sandbox);
  vm.runInContext(dollar[0] + escM[0] + block, sandbox);

  await sandbox.initChangeForm();

  const tList = $("cb-target-list"), mList = $("cb-mat-list");
  const rows = [...tList.querySelectorAll(".pk[data-tid]")];
  const secs = [...tList.querySelectorAll(".pk-sec")].map((e) => e.textContent.trim());
  console.log("=== 目标卡列表 ===");
  console.log("行数:", rows.length, "| 面值分组:", secs.join(", ") || "（无）");
  console.log("前 6 行:", rows.slice(0, 6).map((r) => r.textContent.trim().replace(/\s+/g, " ")).join(" | "));

  // 逐行核对：每行都应有「面值 N」按钮且 N 与所属分组一致（升序）
  const priceSeq = rows.map(face);
  const asc = priceSeq.every((v, i) => i === 0 || v >= priceSeq[i - 1]);
  console.log("目标面值升序:", asc ? "✓" : `✗ ${priceSeq.join(",")}`);
  console.log("每行有面值:", priceSeq.every((v) => v != null) ? "✓" : "✗");

  // 搜索过滤
  $("cb-target-search").value = "套";
  $("cb-target-search").oninput();
  const filtered = $("cb-target-list").querySelectorAll(".pk[data-tid]").length;
  console.log("搜索『套』后行数:", filtered, filtered < rows.length ? "✓ 过滤生效" : "✗ 没过滤");
  $("cb-target-search").value = "";
  $("cb-target-search").oninput();

  // 逐个目标卡点选，断言：材料面值全部 ≥ 目标；道具卡段只出现 100/200/600 且 ≥ 目标
  console.log("\n=== 点选目标卡 → 材料列表（抽样 8 个不同面值）===");
  const seen = new Set();
  let bad = 0;
  for (const row of [...$("cb-target-list").querySelectorAll(".pk[data-tid]")]) {
    const tp = face(row);
    if (seen.has(tp)) continue;
    seen.add(tp);
    row.onclick();
    await new Promise((r) => setTimeout(r, 250));   // 等 props 首次 fetch 完成（之后走缓存）
    const tSel = $("cb-target-sel").textContent;
    const mats = [...mList.querySelectorAll(".pk[data-mk]")];
    const matFaces = mats.map(face).filter((v) => v != null);
    const below = matFaces.filter((v) => v < tp);
    const propFaces = [...mList.querySelectorAll('.pk[data-mk="prop"] .pk-n')]
      .map((e) => { const m = /(\d+)/.exec(e.textContent); return m ? Number(m[1]) : null; });
    const badProp = propFaces.filter((v) => ![100, 200, 600].includes(v));
    if (below.length || badProp.length) bad++;
    console.log(`目标 ${tp}面 → ${tSel} | 材料 ${mats.length} 项（道具 ${propFaces.length} / 箱 ${mats.length - propFaces.length}）` +
      (below.length ? ` ✗ 有 ${below.length} 项面值 < 目标` : "") +
      (badProp.length ? ` ✗ 非法道具面值 ${badProp.join(",")}` : ""));
    if (mats.length) {
      const first = mats[0];
      first.onclick();
      await new Promise((r) => setTimeout(r, 60));
      console.log(`   点首项「${first.textContent.trim().replace(/\s+/g, " ")}」→ 预览: ${$("cb-change-preview").textContent}`);
    } else {
      console.log("   （该目标无可用材料）");
    }
  }
  console.log("\n违规（材料面值 < 目标 或 非法道具面值）的抽样数:", bad, bad === 0 ? "✓" : "✗");
})();
