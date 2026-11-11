"""回归：灵宠引擎 last_state 必须与 /api/cat/travel 的前端渲染契约对齐。

背景（2026-11-11）：spPoll 每 3s 拿 engine.last_state 调 spRenderState，而前端
spRenderAll 读的是 free_explore_cnt / today_fri_explore_cnt / fri_explore_ts。
以前 last_state 只存 free / fri_used / fri_ts 三个别名 → 次数被刷成 0，
15s 后 spTick 用真机数据又刷回 5，界面「一会 0 一会剩 5」。
"""
import sys

from backend import spirite_auto as sa

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    if not cond:
        print(f"FAIL {name}: {extra}", file=sys.stderr)


def snap(d, mode="map"):
    eng = object.__new__(sa.SpiriteAuto)
    eng.opts = {"mode": mode}
    eng._snapshot(d)
    return eng.last_state


BASE = {
    "free_explore_cnt": 5,
    "today_fri_explore_cnt": 1,
    "fri_explore_ts": 1700000000,
    "section_id": 3,
    "pass_id": 6,
    "power": 120,
    "torrid_zone_explore_cnt": 4,
    "today_hy_explore_cnt": 2,
    "explore": {"hy_explore_pass_id": 16, "hy_put_spirites": [1, 3],
                "hy_explore_ts": 1700000000, "explore_ts": 0},
}

st = snap(BASE)
ck("补齐 free_explore_cnt", st.get("free_explore_cnt") == 5, st.get("free_explore_cnt"))
ck("补齐 today_fri_explore_cnt", st.get("today_fri_explore_cnt") == 1,
   st.get("today_fri_explore_cnt"))
ck("补齐 fri_explore_ts", st.get("fri_explore_ts") == 1700000000,
   st.get("fri_explore_ts"))
ck("旧别名 free 保留", st.get("free") == 5, st.get("free"))
ck("旧别名 fri_used 保留", st.get("fri_used") == 1, st.get("fri_used"))
ck("island 计数器与上限", st["island"]["today_cnt"] == 2
   and st["island"]["daily_max"] == sa.ISLAND_DAILY_MAX, st["island"])
ck("across 计数器与上限", st["across"]["used"] == 4
   and st["across"]["daily_max"] == sa.ACROSS_DAILY_MAX, st["across"])
ck("island 嵌套仍补建（不闪成无进行中）",
   st["island"]["explore_ts"] == 1700000000 and st["island"]["spirites"] == [1, 3],
   st["island"])

# 真实的 0 不能被 `or 0` 之外的逻辑改掉，也不能被包装成 None
z = snap({**BASE, "free_explore_cnt": 0, "today_fri_explore_cnt": 0})
ck("真实 0 原样透出", z["free_explore_cnt"] == 0 and z["today_fri_explore_cnt"] == 0,
   (z["free_explore_cnt"], z["today_fri_explore_cnt"]))

# 空壳响应：不能凭空造出 0 让前端把「剩 5」覆盖掉（交给 spMergeState 回退旧值）
e = snap({"explore": {}})
ck("空壳响应不伪造 0", e.get("free_explore_cnt") is None
   and e.get("today_fri_explore_cnt") is None,
   (e.get("free_explore_cnt"), e.get("today_fri_explore_cnt")))

print(f"{len(PASS)} PASS / {len(FAIL)} FAIL", file=sys.stderr)
sys.exit(1 if FAIL else 0)
