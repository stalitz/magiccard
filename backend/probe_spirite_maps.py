#!/usr/bin/env python3
"""猫咪探险（card_user_spirite）探查：act=1 完整字段 + 地图编号实验。

用法：
  python -m backend.probe_spirite_maps            # 只 dump act=1 完整字段
  python -m backend.probe_spirite_maps --probe    # 追加地图编号探测实验

零消耗原理（--probe）：服务端先校验地图、再校验精灵，用不存在的精灵 ids=999
- 有效地图 → -2（精灵不存在）
- 无效地图 → -5
不会真的派出精灵。free=0 时另有对比实验。

必须串行请求：并发探测会出现假象（此前踩坑）。
"""
import asyncio
import json
import sys

from backend.mc_api import LoginSession
from backend.spirite_auto import plain_uin

MAX_SEC = 15


def show(d: dict) -> None:
    print("act=1 字段数=%d" % len(d))
    for k in sorted(d):
        v = d[k]
        sv = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        sv = str(sv)
        print("  %-24s %s" % (k, sv if len(sv) <= 230 else sv[:230] + " …(共%d)" % len(sv)))
    pc = d.get("pieces") or []
    if pc:
        print("  [pieces] %d 项; id %s..%s; num>0 的有 %d 项"
              % (len(pc), pc[0].get("id"), pc[-1].get("id"),
                 sum(1 for x in pc if int(x.get("num") or 0) > 0)))


async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    op = plain_uin(s)
    print("uin=%s opuin=%s\n" % (s.uin, op))

    r1 = await s.post("card_user_spirite", act=1, opuin=op)
    d1 = r1.get("data") or {}
    print("=== act=1 (code=%s) ===" % r1.get("code"))
    show(d1)

    if "--probe" not in sys.argv:
        return

    sp_id = 0
    for x in (d1.get("spirites") or []):
        sp_id = int(x.get("id") or 0)
        if sp_id:
            break

    print("\n=== A. 不存在精灵(ids=999) 探各地图 ===")
    for sec in range(1, MAX_SEC + 1):
        r = await s.post("card_user_spirite", act=3, opuin=op,
                         section_id=sec, pass_id=1, ids=999,
                         explore_pass_id=sec * 100 + 1)
        print("  section %-3s -> code=%-7s %s" % (sec, r.get("code"), str(r.get("message"))[:40]))

    print("\n=== B. 真实精灵(id=%s) 探关键地图（free=0 应被拒）===" % sp_id)
    for sec in (1, 6, 7, 9, int(d1.get("section_id") or 0)):
        if not sec:
            continue
        r = await s.post("card_user_spirite", act=3, opuin=op,
                         section_id=sec, pass_id=1, ids=sp_id,
                         explore_pass_id=sec * 100 + 1)
        print("  section %-3s -> code=%-7s %s" % (sec, r.get("code"), str(r.get("message"))[:40]))

    r2 = await s.post("card_user_spirite", act=1, opuin=op)
    d2 = r2.get("data") or {}
    print("\n复查：free=%s explore_ts=%s explore_pass_id=%s"
          % (d2.get("free_explore_cnt"),
             (d2.get("explore") or {}).get("explore_ts"),
             (d2.get("explore") or {}).get("explore_pass_id")))


if __name__ == "__main__":
    asyncio.run(main())
