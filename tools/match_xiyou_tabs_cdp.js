/**
 * 真机（headless Chrome + CDP）验收：西游记 / 三国战役两个独立 tab 的布局与渲染。
 *
 * 背景：上一轮「DOM 塌陷」就是 jsdom 查不出来的（div 配平 0 错误，但 flex 全塌）。
 * 所以这里走真实 CSS 布局 —— 量 tab 条高度/是否被裁、量各 msub 面板的宽度，
 * 并截图留证。
 *
 * 用法（复用本机已有的 headless Chrome，只观测不动它）：
 *   node tools/match_xiyou_tabs_cdp.js
 *
 * 默认连 http://127.0.0.1:9222（本机 headless Chrome）。要自己起一个就换 CDP_PORT。
 *
 * 依赖：Node 22 自带 WebSocket（不装 ws 包）。
 */
const http = require("http");
const fs = require("fs");

const CDP = process.env.CDP_PORT ? `http://127.0.0.1:${process.env.CDP_PORT}` : "http://127.0.0.1:9222";
const URL_PAGE = "http://127.0.0.1:8000/";
const OUT = "/tmp/mc_tabs";

function get(path) {
  return new Promise((resolve, reject) => {
    http.get(CDP + path, (res) => {
      let b = "";
      res.on("data", (c) => (b += c));
      res.on("end", () => resolve(JSON.parse(b)));
    }).on("error", reject);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const list = await get("/json/list");
  const page = list.find((t) => t.type === "page");
  if (!page) throw new Error("没有找到 page target，Chrome 起好了吗？");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let nextId = 1;
  const pend = new Map();
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
  });
  await new Promise((r) => ws.addEventListener("open", r));
  const send = (method, params) => new Promise((resolve) => {
    const id = nextId++;
    pend.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  const evaluate = async (expr) => {
    const r = await send("Runtime.evaluate", {
      expression: expr, awaitPromise: true, returnByValue: true,
    });
    if (r.result && r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails));
    return r.result && r.result.result ? r.result.result.value : null;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: URL_PAGE });
  await sleep(2500);

  // 注入 mock：不登录也能看到两个玩法的真实字段（结构与 /api 实际返回一致）
  await evaluate(`(() => {
    const XIYOU = { ok:true, code:0,
      currLevelData:[{formationid:0,levelid:2,origin_time:86400,startts:1790323040,
        stepData:[{getts:200005,gifts:"121_23200_1",index:0,isget:1}],
        targetbit:0,time:86400,type:1}],
      logData:[{formationid:0,levelid:2,origin_time:86400,startts:1790162822,
        stepData:[{getts:200008,gifts:"92_36_4",index:0,isget:1}],targetbit:0,time:0,type:1}],
      formationList:[{catid:1,ce:9041,eqlist:"10493,20162,30162,40353",index:1}],
      freepowerbit:[{bit_1:0,id_1:1},{bit_2:0,id_2:2}],
      luckydata:[{id:1,value:66},{id:2,value:168}], shoplist:[{cnt:0,id:1}],
      ex_box_num:6,new_box_num:15,safe_box_num:17,maxLevelId:2,
      nlb:1825,sjb:45840,txd:170,guide:1,newbiebit:2,suitcard:[],
      task_bits:0, task_detail:[{type:1,cnt:0},{type:2,cnt:0}] };
    const SANGUO = { ok:true, code:0, levelId:204, passId:0, freenum:0, buynum:0,
      talent_score:650, talent_rate:12.0, levelData:[], formation:[{id:0,lv:10}],
      logData:[{bossid:102,levelid:102,dropitem:"92_38_6",percent_hp:62,reduce_hp:5192,
        startts:1790354523,stepData:[],passData:{getts:0,isget:0,sitem:""}}],
      travel_elf_arr:[],img_items:[],ex_box_num:6,safe_box_num:0,shoplist:[],
      task_bits:0, task_detail:[{type:1,cnt:0}] };
    const SANGUO_EXTRA = { ok:true, levels:{ok:true,code:0,data:{
      101:{levelid:101,bossid:101,dropitem:"92_38_6"},
      102:{levelid:102,bossid:102,dropitem:"92_38_5"}}}, lineup:null };
    window.fetch = (u) => {
      const url = String(u);
      const payload = url.includes("/api/match/sanguo-extra") ? SANGUO_EXTRA
        : url.includes("/api/match/explore") ? XIYOU
        : url.includes("/api/match/sanguo") ? SANGUO
        : url.includes("/api/match/union") ? { ok:true, code:0, union_name:"搞起耍", mem_num:29,
            members:{"1006409757":"4,1790402280,紫旋の梦蝶,6213,96,1_81_1,202680,180,1790402552,3,35"} }
        : url.includes("/api/match/gvg") ? { ok:true, code:0, data:{level:8,inlv:8,levelgetbits:",10100,20100",
            e_name:"夜半钟声到客船",e_progress:595420,enemy_uinon:21718,progress:478555,
            level_rank_bit:252,level_rank_bit2:0,season_gift:0,ts:1787673667,gift_record:"1260277398"} }
        : url.includes("/api/match/pvp") ? { ok:true, code:0, list:[], total:0, my_score:0, my_ce:0, zan_bit:0 }
        : url.includes("/api/match/state") ? { ok:true, code:0, cats:[{elf_id:3,level:20,mood:85,equip:[]}],
            performe_num:2, races:[] }
        : { ok:true, code:0, cats:[{elf_id:3,level:20,mood:85,equip:[]}], list:[], data:{}, message:"ok" };
      return Promise.resolve({ ok:true, status:200, json:()=>Promise.resolve(payload) });
    };
    "injected";
  })()`);

  const measure = async (key, file) => {
    await evaluate(`(async () => {
      switchPane('match');
      switchMatchSub(${JSON.stringify(key)});
      await loadMatchState(true);
      await new Promise(r => setTimeout(r, 260));
      return document.getElementById('msub-${key}').textContent.slice(0, 60);
    })()`);
    await sleep(400);
    const m = await evaluate(`(() => {
      const bar = document.getElementById('match-sub-bar');
      const barR = bar.getBoundingClientRect();
      const tabs = [...bar.querySelectorAll('.tab')];
      const p = document.getElementById('msub-${key}').getBoundingClientRect();
      const box = document.getElementById('match-${key === 'xiyou' ? 'xiyou' : key === 'sanguo' ? 'sanguo' : key}-box');
      return {
        barW: Math.round(barR.width), barH: Math.round(barR.height),
        barScrollW: bar.scrollWidth, barClientW: bar.clientWidth,
        tabCount: tabs.length,
        lastTabRight: Math.round(tabs[tabs.length - 1].getBoundingClientRect().right),
        paneW: Math.round(p.width), paneVisible: p.width > 300 && p.height > 80,
        cnt: (document.getElementById('match-${key === 'xiyou' ? 'xiyou-cnt' : key === 'sanguo' ? 'sanguo-cnt' : key + '-cnt'}') || {}).textContent || "",
        boxText: box ? box.textContent.slice(0, 120) : "(no box)",
      };
    })()`);
    console.log(`\n[${key}] ` + JSON.stringify(m, null, 1));
    const shot = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(`${OUT}/${file}.png`, Buffer.from(shot.result.data, "base64"));
  };

  await measure("gvg", "gvg");
  await measure("xiyou", "xiyou");
  await measure("sanguo", "sanguo");
  console.log("\n截图已存到 " + OUT);
  ws.close();
  process.exit(0);
})().catch((e) => { console.error("ERR", e); process.exit(1); });
