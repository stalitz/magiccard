"""逐步诊断自动挖地：打印每轮 规划动作 → 真实服务端响应，定位「走不动」根因。

用法： python -m tools.dig_diag [步数] [max_digs]
"""
import asyncio
import sys

from backend.mc_api import LoginSession
import backend.dig_auto as dg
import backend.dig_plan as dplan


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录"); return
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    st = await dg.get_state(s)
    if st is None:
        print("读状态失败"); return
    print(f"初始 dig_index={st['dig_index']} chutou={st['chutou']} "
          f"line_tnt={st['line_tnt']} jgg_tnt={st['jgg_tnt']}")
    print("--- 网格(行: 每格 type/known/tntable/diggable/super) ---")
    for r in st["rows"]:
        cells = " ".join(
            f"j{c['j']}:t{c['type']}{'K' if c['known'] else '?'}"
            f"{'T' if c['tntable'] else ''}{'D' if c['diggable'] else ''}"
            f"{'S' if c['super_tnt'] else ''}"
            for c in r["cells"])
        print(f"  L{r['line']}: {cells}")
    print("------------------------------------------")
    tools = {"chutou": st.get("chutou"), "line_tnt": st.get("line_tnt"),
             "jgg_tnt": st.get("jgg_tnt")}
    last_idx = st["dig_index"]
    for i in range(steps):
        st = await dg.get_state(s)
        if st is None:
            print(f"[步{i}] 读状态失败"); break
        res = dplan.plan(st["rows"], tools, use_tnt=True, clear_margin=1)
        act = res.get("action")
        if not act:
            print(f"[步{i}] 规划: 停止 → {res.get('reason')}")
            break
        dt, line, col = act["dig_type"], act["line"], act["col"]
        r = await dg.dig_cell(s, line, col, dt)
        new_idx = (r.get("state") or {}).get("dig_index")
        delta = (new_idx - last_idx) if new_idx is not None else None
        last_idx = new_idx
        print(f"[步{i}] {dplan.TOOL_NAME[dt]}@(L{line},j{col}) "
              f"→ code={r.get('code')} ok={r.get('ok')} "
              f"dig_index={new_idx}(Δ{delta}) | {res.get('reason')}")
        if not r.get("ok"):
            print(f"       失败详情: {r.get('text')}")
            # 连续失败保护
        tools = {"chutou": (r.get("state") or st).get("chutou", tools["chutou"]),
                 "line_tnt": (r.get("state") or st).get("line_tnt", tools["line_tnt"]),
                 "jgg_tnt": (r.get("state") or st).get("jgg_tnt", tools["jgg_tnt"])}
    print("--- 结束 ---")


if __name__ == "__main__":
    asyncio.run(main())
