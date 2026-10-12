"""dry-run 验证 travel_elf_auto.run_once 逻辑（只读状态 + 算参数，不发黄实动作）。

用法：python -m backend.probe_travel_dryrun
"""
import asyncio
from .mc_api import LoginSession
from . import travel_elf_auto as te


async def main():
    s = LoginSession()
    s.restore()
    if not s.logged_in:
        print("未登录，先扫码")
        return
    print("=== 真实状态（只读）===")
    st = await te.get_state(s)
    if st is None:
        print("读状态失败"); return
    import time
    now = int(time.time())
    print(f"free_cnt={st.get('free_cnt')} pay_cnt={st.get('pay_cnt')} "
          f"clover={st.get('clover_num')} fenmo={st.get('fenmo_num')}")
    for c in st.get("travel_elf_arr") or []:
        end = int(c.get("travel_end_ts") or 0)
        tag = "旅行中" if end > 0 and now < end else ("已返回" if end > 0 else "空闲")
        print(f"  猫{c.get('elf_id')}: {tag} end_ts={end} food={c.get('food_id')} num={c.get('food_num')}")

    print("\n=== dry-run 一轮（不发请求）===")
    plan = [step async for step in te.run_once(s, dry_run=True)]
    for it in plan:
        print(f"  [{it.get('act')}] elf={it.get('elf_id')} -> {it.get('text')}")


if __name__ == "__main__":
    asyncio.run(main())
