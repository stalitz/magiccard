// 离线验证：用真实 synthesis 接口数据 + app.js 里真实的分层渲染函数，
// 在 jsdom 里跑一遍，检查「每张卡是否只出现一次」，而不是靠读代码推断。
// 用法: NODE_PATH=<workspace>/node_modules node tools/syn_render_check.js <card_id>
const fs = require("fs");
const vm = require("vm");
const { JSDOM } = require("jsdom");

const CARD_ID = process.argv[2] || "26131";
const src = fs.readFileSync(__dirname + "/../frontend/app.js", "utf8");

// 抠出渲染块（真实代码，不做任何复制改写）
const start = src.indexOf("// ===== 套卡合成信息");
const end = src.indexOf("// ---------- 自动炼卡页 ----------");
if (start < 0 || end < 0) throw new Error("找不到分层渲染代码块，app.js 结构可能变了");
const block = src.slice(start, end);

// 抠出它依赖的两个工具函数
const escM = src.match(/const esc = \(s\) =>[\s\S]*?\)\);\n/);
const fmtM = src.match(/function fmtDur\(sec\) \{[\s\S]*?\n\}/);
if (!escM || !fmtM) throw new Error("找不到 esc / fmtDur");

const dom = new JSDOM('<!doctype html><body><div id="root"></div>');
const sandbox = {
  document: dom.window.document,
  window: dom.window,
  console,
  doRefine: async () => {},
  doBuy: async () => false,
  loadCardBox: async () => {},
};

(async () => {
  const d = await (await fetch(`http://127.0.0.1:8000/api/card/synthesis?card_id=${CARD_ID}`)).json();
  if (!d.ok) throw new Error("接口失败: " + d.message);
  vm.createContext(sandbox);
  vm.runInContext(escM[0] + "\n" + fmtM[0] + "\n" + block, sandbox);

  const root = dom.window.document.getElementById("root");
  root.innerHTML = sandbox.renderSynTiers(d);
  sandbox.mountSynView(root, d, null);

  const names = [];
  for (const t of d.tiers) for (const c of t.cards) names.push(c.name);
  // 只统计分层清单区域：动作行里出现一次「已选 X」是正常的，不该算重复
  const gridHtml = root.querySelector(".tier-rows").innerHTML;
  const html = root.innerHTML;

  // 1) 每张卡的「卡片格」数量（tchip 里含 data-cid，正文里不该再出现卡名）
  const chipCount = (html.match(/class="tchip/g) || []).length;
  console.log(`套卡 ${d.theme.name} · 卡片格 ${chipCount} 个 / 应有 ${d.total} 张（每张一次）`);

  const dup = [];
  for (const t of d.tiers) {
    for (const c of t.cards) {
      // 卡名在**分层清单**里出现几次（含 title 属性一次 = 2 次以内算正常）
      const n = gridHtml.split(c.name).length - 1;
      if (n > 2) dup.push(`${c.name}×${n}`);
    }
  }
  console.log("重复出现（>2 次）的卡:", dup.length ? dup.join(", ") : "无 ✓");

  // 2) 材料必须是**在上方清单里就地换色框出**（统一一种颜色），不是另列一行
  console.log("动作行:", root.querySelector(".syn-act").textContent.trim());
  // 图例色块必须是**空 span**（文字写在色块外面）；文字塞进 12px 色块里会挤成竖排
  const lgBad = [...root.querySelectorAll(".synth-sub .lg")].filter((e) => e.textContent.trim());
  console.log("图例色块里混入文字（应为 0）:", lgBad.length);
  console.log("说明行:", root.querySelector(".synth-sub").textContent.trim());
  const framed = [...root.querySelectorAll(".tchip.mat")]
    .map((e) => `${e.className.replace(/tchip\s*/, "")}·${e.textContent.trim()}`);
  console.log("被框出的材料格:", framed.length ? framed.join(" / ") : "（无）");
  console.log("选中格:", (root.querySelector(".tchip.sel") || {}).textContent);
  console.log("动作行里的兜底材料格（应为 0）:",
    root.querySelectorAll(".syn-act .tchip").length);

  // 3) 下钻：点一个框出来的材料格，看是否切到那张卡（逐层看路径，零重复）
  const m = root.querySelector(".tchip.mat[data-cid]");
  if (!m) {
    console.log("\n（该卡没有材料可下钻，跳过）");
  } else {
    console.log(`\n点材料「${m.textContent.trim()}」→ 动作行变为:`);
    m.onclick({ stopPropagation() {} });
    console.log("  ", root.querySelector(".syn-act").textContent.trim());
  }

  console.log("\n===== 渲染结构（层级行）=====");
  for (const tr of root.querySelectorAll(".tier-row")) {
    console.log("[", tr.querySelector(".tier-lab").textContent.padEnd(14), "]",
      [...tr.querySelectorAll(".tchip")].map((e) => e.textContent.trim()).join("  "));
  }
})();
