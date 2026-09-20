"""实跑一轮猫咪旅行自动循环（真实消耗免费次数，发起旅行）。

用法：python -m backend.probe_travel_live_run
等价于 POST /api/travel/run（dry_run=false）。
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
    print("=== 实跑一轮（真实出发）===")
    out = await te.run_once(s, dry_run=False)
    for it in out:
        print(f"  [{it.get('act')}] elf={it.get('elf_id')} ok={it.get('ok')} -> {it.get('text')}")


if __name__ == "__main__":
    asyncio.run(main())
