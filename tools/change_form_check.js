// 离线验证：用真实接口数据 + app.js 里真实的 initChangeForm，在 jsdom 里跑一遍三级下钻：
//   ① 星 → ② 套卡（标记已有） → ③ 目标卡（按面额升序） → 材料卡（卡箱按套卡·面额 + 道具卡段）
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
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const face = (el) => { const m = /(\d+)\s*面/.exec(el.querySelector(".pk-f").textContent); return m ? Number(m[1]) : null; };
const txt = (el) => el.textContent.trim().replace(/\s+/g, " ");

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
  await wait(250);

  // ① 星
  const stars = [...$("cb-star").querySelectorAll(".pk[data-star]")];
  console.log("=== ① 星 ===");
  console.log("可选项:", stars.map(txt).join(" | "));
  const star3 = stars.find((s) => s.dataset.star === "3") || stars[0];
  const starVal = Number(star3.dataset.star);

  // ② 套卡
  star3.onclick();
  await wait(120);
  const ths = [...$("cb-theme-list").querySelectorAll(".pk[data-ttid]")];
  console.log(`\n=== ② 套卡（选了 ${starVal}★）===`);
  console.log("套卡数:", ths.length);
  const withBadge = ths.filter((t) => t.querySelector(".pk-bdg"));
  console.log("标了「已有/已集齐」的套卡:", withBadge.length,
    withBadge.slice(0, 8).map(txt).join(" | "));

  // ③ 目标卡 + 材料（抽 3 套）
  console.log("\n=== ③ 目标卡 + 材料 ===");
  let bad = 0, checked = 0;
  for (const th of ths.slice(0, 3)) {
    th.onclick();
    await wait(200);   // 拉套卡卡表
    const cards = [...$("cb-target-list").querySelectorAll(".pk[data-cid]")];
    const prices = cards.map(face);
    const asc = prices.every((v, i) => i === 0 || v >= prices[i - 1]);
    console.log(`\n套卡「${txt(th).split(" ")[0]}」→ ${cards.length} 张卡，面额升序: ${asc ? "✓" : "✗ " + prices.join(",")}`);
    console.log("  前 5 张:", cards.slice(0, 5).map(txt).join(" | "));

    const picks = [cards[0], cards[Math.floor(cards.length / 2)], cards[cards.length - 1]].filter(Boolean);
    for (const card of picks) {
      const tp = face(card);
      card.onclick();
      await wait(250);   // 首次要拉 props
      const mList = $("cb-mat-list");
      const secs = [...mList.querySelectorAll(".pk-sec")].map((e) => e.textContent.trim());
      const mats = [...mList.querySelectorAll(".pk[data-mkey]")];
      const matFaces = mats.map((m) => { const mm = /(\d+)\s*面/.exec(m.querySelector(".pk-f").textContent); return mm ? Number(mm[1]) : null; }).filter((v) => v != null);
      // 道具卡的面值在**名称**里（如「100面值卡」）；.pk-f 是数量 ×N，别拿来当面值
      const propFaces = [...mList.querySelectorAll('[data-mkey^="prop:"] .pk-n')]
        .map((e) => { const mm = /(\d+)/.exec(e.textContent); return mm ? Number(mm[1]) : null; });
      const below = matFaces.filter((v) => v < tp);
      const badProp = propFaces.filter((v) => ![100, 200, 600].includes(v));
      checked++; if (below.length || badProp.length) bad++;
      console.log(`  目标 ${tp}面「${txt(card).split(" ")[0]}」→ 材料 ${mats.length} 项；段落: ${secs.slice(0, 4).join(" / ")}${secs.length > 4 ? " …" : ""}`);
      if (below.length || badProp.length) console.log(`    ✗ 违规: 面值<目标 ${below.length}，非法道具面值 ${badProp.join(",")}`);
      if (mats.length) { mats[0].onclick(); await wait(30); console.log(`    点首项「${txt(mats[0])}」→ ${$("cb-change-preview").textContent}`); }
      else console.log("    （无材料）");
    }
  }
  console.log(`\n违规抽样 ${bad}/${checked}`, bad === 0 ? "✓" : "✗");
})();
