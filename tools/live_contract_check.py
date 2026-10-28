"""真机口径核对（只读，10 秒）：真机状态 vs 引擎硬编码假设。

为什么需要它：改引擎/调度后，最贵的验证不是「跑完一轮」（4★ 78h），
而是「引擎假设的口径与真机是否一致」。口径错了，仿真跑得再久也是错的；
口径对了，长周期交给仿真（同一套代码）跑就行。

用法：
    python tools/live_contract_check.py            # 读正在跑的服务（:8000）
    python tools/live_contract_check.py --port 8000

只读：只调 /api/state，不发任何写请求。
"""
import argparse
import json
import sys
import urllib.request

DEF_PORT = 8000
# 引擎侧硬编码/默认值（改这些地方时同步改这里，否则核对结果会失真）
ENGINE_ASSUMPTIONS = {
    "machines": ("auto_refine.py:1231 DPTickController(machines=3)", 3),
    "stove_slots": ("真机 <stovebox cur> 应为 3 炉×2 卡位+1 偷炉 = 7", 7),
    "sim_store_cur": ("Engine.run_sim(store_cur=200)", 200),
    "sim_change_cur": ("Engine.run_sim(change_cur=18)", 18),
}


def fetch(port: int) -> dict:
    url = f"http://127.0.0.1:{port}/api/state"
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEF_PORT)
    a = ap.parse_args()
    try:
        d = fetch(a.port)
    except Exception as exc:  # noqa: BLE001
        print(f"读 /api/state 失败：{exc}（服务没起或未登录？）")
        return 2
    if not d.get("ok"):
        print(f"接口返回失败：{d.get('message')}")
        return 2

    user = d.get("user") or {}
    now = d.get("now") or 0
    print(f"账号 {user.get('nick')}({user.get('uin')}) 金币={user.get('money')} "
          f"服务器时间={now}")

    def box(key):
        b = d.get(key) or {}
        cards = b.get("cards") or []
        used = sum(1 for c in cards if (c.get("id") or 0) > 0)
        return b.get("cur", 0), b.get("max", 0), used

    ccur, cmax, cused = box("changebox")
    scur, smax, sused = box("storebox")
    print(f"\n换卡箱 cur={ccur} max={cmax} 已用={cused} 空={ccur - cused}")
    print(f"保险箱 cur={scur} max={smax} 已用={sused} 空={scur - sused}")

    # 炉位（★ 2026-10-28 纠正）：stovebox.cards 是**卡位**不是炉。
    #   真机：<stovebox cur="7"> = 3 主炉×2 卡位 + 1 偷炉位，炉号在 card["stove"]。
    #   一个炉可排多张（前一张没取走就投第二张），所以「在炼张数」≠「炉数」，
    #   旧代码用 len(busy)+len(free) 当主炉数 → 恒报「主炉≈5」的假不一致。
    cards = (d.get("stovebox") or {}).get("cards") or []
    slots_cur = (d.get("stovebox") or {}).get("cur", 0)

    def is_steal(c):
        return c.get("slottype") == "steal"

    main_slots = [c for c in cards if not is_steal(c)]
    steal_slots = [c for c in cards if is_steal(c)]
    # 炉集合：有卡位（无论空/在炼）的 stove 号即该炉存在
    stoves = sorted({int(c.get("stove") or 0) for c in main_slots})
    per_stove = {s: [c for c in main_slots if int(c.get("stove") or 0) == s]
                 for s in stoves}
    main_busy = [c for c in main_slots if (c.get("id") or 0) > 0]
    steal_busy = [c for c in steal_slots if (c.get("id") or 0) > 0]

    print(f"\n炉位：主炉 {len(stoves)} 座 {stoves}，卡位 "
          f"{len(main_slots)}/{slots_cur}（偷炉位 {len(steal_slots)}）")
    for s in stoves:
        cs = per_stove[s]
        busy = [c for c in cs if (c.get("id") or 0) > 0]
        queue = max(0, len(busy) - 1)          # 同炉多张 = 排队，零产能增益
        print(f"   炉{s}: 卡位 {len(cs)} 在炼 {len(busy)}"
              + (f"（排队 {queue} 张，不增产）" if queue else ""))
    print(f"      偷炉：{'在炼' if steal_busy else '空'}")
    for c in main_busy:
        left = int(c.get("left") or 0)
        when = "已完成可收" if (c.get("flag") == 2 or left <= 0) else f"{left//60} 分钟后"
        print(f"   炉{c.get('stove')} slot{c.get('slot')} "
              f"{c.get('name')}({c.get('id')}) 面值{c.get('price')} {when}")

    # ---- 对照引擎假设 ----
    print("\n=== 与引擎假设对照 ===")
    real_machines = len(stoves)
    ok = True

    def cmp(name, real, assume_key, loose=False):
        """loose=True：真机 <= 假设属于「仿真偏乐观」，记警告但不判失败。"""
        nonlocal ok
        where, assumed = ENGINE_ASSUMPTIONS[assume_key]
        if real == assumed:
            print(f"  {name}: 真机={real} 引擎={assumed} [{where}] OK")
        elif loose and real < assumed:
            print(f"  {name}: 真机={real} 引擎={assumed} [{where}] "
                  f"⚠ 仿真偏乐观（生产会更早撞上限，长周期数字要打折扣）")
        else:
            ok = False
            print(f"  {name}: 真机={real} 引擎={assumed} [{where}] ⚠ 不一致")

    cmp("主炉数", real_machines, "machines")
    cmp("炉卡位数 cur", slots_cur, "stove_slots")
    cmp("换卡箱格数 cur", ccur, "sim_change_cur", loose=True)
    cmp("保险箱格数 cur", scur, "sim_store_cur", loose=True)
    print(f"  偷炉位: 真机={'有' if (steal_slots or steal_busy) else '无'} "
          f"引擎=1（steal_enabled）")
    if steal_busy:
        print("  ★ 偷炉里有卡：偷炉只该跑 40 面，且产物可能被好友收走。")

    if not ok:
        print("\n★ 口径不一致的后果：仿真数字不能直接当生产预期；"
              "若主炉数偏小，真机 DP 会闲置炉位。")
    else:
        print("\n口径一致，可以把长周期交给仿真跑。")
    print("\n提示：这是只读核对。写操作（买/卖/挪/变/偷）属于下一层「触点验证」，"
          "每条都有真实后果，需单独确认。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
