// 离线验证：用真实 /api/dig/state 数据 + app.js 里真实的 renderDigGrid，
// 断言「游戏只显示 7×7」——服务端 8 行窗口的最顶那行（已挖穿的空行）不渲染。
// 用法: NODE_PATH=<workspace>/node_modules node tools/dig_grid_check.js
const fs = require("fs");
const vm = require("vm");
const { JSDOM } = require("jsdom");

const src = fs.readFileSync(__dirname + "/../frontend/app.js", "utf8");
const dollar = src.match(/const \$ = \(id\) => document\.getElementById\(id\);\n/);
const escM = src.match(/const esc = \(s\) =>[\s\S]*?\)\);\n/);
const start = src.indexOf("const DIG_TYPE_LABEL");
const end = src.indexOf("// ---------- 鉴定机");
if (!dollar || !escM || start < 0 || end < 0) throw new Error("找不到挖地渲染块，app.js 结构可能变了");
const block = src.slice(start, end);

const dom = new JSDOM('<!doctype html><body><div id="dig-grid"></div><div id="dig-cnt"></div><select id="dig-tool"><option value="1" selected>1</option></select>');

(async () => {
  const st = await (await fetch("http://127.0.0.1:8000/api/dig/state")).json();
  const raf = [];
  const sandbox = {
    document: dom.window.document, window: dom.window, console,
    requestAnimationFrame: (f) => raf.push(f),
    digClick: () => {}, digCollect: () => {},
  };
  vm.createContext(sandbox);
  vm.runInContext(dollar[0] + escM[0] + block, sandbox);

  const srcLines = (st.rows || []).map((r) => r.line);
  console.log("服务端窗口行数:", srcLines.length, "→", srcLines.join(", "));
  sandbox.renderDigGrid(st);

  const box = dom.window.document.getElementById("dig-grid");
  const rendered = [...box.querySelectorAll(".dg-row .dg-ln")].map((e) => Number(e.textContent));
  const cellsPerRow = [...box.querySelectorAll(".dg-row")].map((r) => r.querySelectorAll(".dg-cell").length);
  console.log("渲染行数:", rendered.length, "→", rendered.join(", "));
  console.log("每行列数:", cellsPerRow.join(", "));
  console.log("曾位于窗口顶的已挖穿空行被隐藏:", rendered[0] === srcLines[1] ? "✓" : "✗");
  console.log("列数全为 7:", cellsPerRow.every((n) => n === 7) ? "✓" : "✗");
  console.log("总格数:", box.querySelectorAll(".dg-cell").length, "（应为 7×7=49）");
  console.log("计数栏:", dom.window.document.getElementById("dig-cnt").textContent);
})();
