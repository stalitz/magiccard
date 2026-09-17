"""灵宠探险：三套玩法「能去的最新关」= **纯规则推导**，不落盘、不存任何本地进度值。

## 判定依据（全部来自官方静态配置 soul_elf_v_78.xml）

每关自带解锁条件：

  - `open_c` / `open_s` —— 前置：**大地图第 open_c 章第 open_s 关**需已通过
  - 灵宠条件：穿越 `elf_num` 只等级 ≥ `elf_lv`；荒岛 `elf_num` 只（荒岛无 elf_lv 字段）
  - `seq` —— 官方连续编号（穿越 1..62、荒岛 1..48），跨章不重置

## 为什么不需要「本地存一个进度」的字段（2026-09-16 结论）

`card_user_spirite act=1` 只下发**大地图**的当前关（`pass_id` / `section_id`，
另有 `vt52` = 它的全局累计序号）。穿越 / 荒岛在 act=1 里**没有**进度字段 ——
但它们的解锁条件本身指向大地图坐标，而灵宠等级就在同一份 act=1 的 `spirites[].lv`。
所以三套玩法的「能去的最新关」都能算出来：

    能去的最新关 = 解锁集合里 seq 最大的那关
    解锁 = open 条件满足 且 灵宠条件满足

## 实测校验（2026-09-16；大地图进度 6-9，10 只灵宠 lv 16/15/16/15/16/14/16/15/16/14）

| 关卡 | open_c/open_s | 判定 | 用户截图 |
|---|---|---|---|
| 穿越 洪泛森林4 | 6 / 8 | ✅ 8 ≤ 9 | 可探索 |
| 穿越 洪泛森林5 | 6 / 10 | ❌ 10 > 9 | 未解锁 |
| 穿越 针叶林1（第 7 章） | 7 / 2 | ❌ 整章未开启 | 未开启 |
| 荒岛 戴尔得荒岛4（seq 16） | 6 / 8 | ✅ | 能去 |
| 荒岛 戴尔得荒岛5（seq 17） | 6 / 10 | ❌ | 未解锁 |

推导结果与截图逐项吻合，也与用户抓包（穿越 `sub_id=3&id=5`、荒岛 `id=16`）一致。
即官方客户端（`checkIsSectionOpened` / `checkCanEnterAcross`）本来就是拿这份配置
在本地算，服务端不存"进度值"。
"""

from __future__ import annotations

from . import spirite_maps as spm

#: 玩法显示名（用于提示文案）
_LABELS = {"across": "穿越丛林", "island": "荒岛"}


def _ordered(mode: str) -> list:
    """按官方连续编号升序的完整关卡列表（跨章展开）。"""
    tbl = spm.across_table() if mode == "across" else spm.island_table()
    out = []
    for cid, ch in tbl.items():
        for s in (ch.get("sections") or []):
            out.append({**s, "chapter_id": int(cid),
                        "chapter_name": ch.get("name") or ""})
    out.sort(key=lambda x: int(x.get("seq") or 0))
    return out


def _why_blocked(sec: dict, cur_pass: int, cur_sec: int, spirites: list) -> str:
    """该关不满足的原因（人话）；满足则返回空串。"""
    oc = int(sec.get("open_c") or 0)
    os_ = int(sec.get("open_s") or 0)
    if (oc, os_) > (cur_pass, cur_sec):
        return f"需先过大地图第{oc}章第{os_}关（当前 {cur_pass}-{cur_sec}）"
    need = int(sec.get("elf_num") or 0)
    lv = int(sec.get("elf_lv") or 0)
    if need > 0:
        pool = ([s for s in spirites if int(s.get("lv") or 0) >= lv] if lv else spirites)
        if len(pool) < need:
            if lv:
                return f"需 {need} 只等级≥{lv} 的灵宠（现有 {len(pool)} 只）"
            return f"需 {need} 只灵宠（现有 {len(pool)} 只）"
    return ""


def _derive_mode(mode: str, cur_pass: int, cur_sec: int, spirites: list) -> dict:
    rows = _ordered(mode)
    unlocked, blocked = [], None
    for s in rows:
        why = _why_blocked(s, cur_pass, cur_sec, spirites)
        if why:
            if blocked is None:          # 只报第一个卡点，够用且不会刷屏
                blocked = {
                    "name": s.get("name"), "seq": s.get("seq"),
                    "chapter": s.get("chapter_id"), "index": s.get("index"),
                    "open_c": s.get("open_c"), "open_s": s.get("open_s"),
                    "elf_num": s.get("elf_num"), "elf_lv": s.get("elf_lv"),
                    "reason": why,
                }
            continue
        unlocked.append(s)
    # latest = 解锁集合里**最深**的关。刻意只用官方的 open / 灵宠条件判定，
    # 不额外假设「必须顺序通关」：同章内 open_s 与 elf_lv 都是递增的，两种口径结果一致；
    # 只有「某关被等级卡住、而后面的章等级要求更低」时才会跳跃，此时 max(解锁) 更贴近
    # 官方判定（官方若不允许跳关，派发接口会返回 -2，用户当场就能发现并回报）。
    latest = unlocked[-1] if unlocked else None
    return {
        "chapter": (latest or {}).get("chapter_id"),
        "index": (latest or {}).get("index"),
        "seq": (latest or {}).get("seq"),
        "name": (latest or {}).get("name"),
        "unlocked": [int(s.get("seq") or 0) for s in unlocked],
        "blocked": blocked,
        "src": "规则推导（open_c/open_s + 灵宠等级）",
    }


def derive(data: dict) -> dict:
    """由 act=1 的状态推导三套玩法「能去的最新关」。

    `data` = `card_user_spirite act=1` 的 data 字段（含 pass_id / section_id / spirites）。
    """
    cur_pass = int(data.get("pass_id") or 0)
    cur_sec = int(data.get("section_id") or 0)
    spirites = list(data.get("spirites") or [])
    out = {
        # 大地图：act=1 直接下发当前关，本身就是权威值，不需要推导。
        "map": {"pass_id": cur_pass, "section_id": cur_sec,
                "seq": data.get("vt52"), "src": "服务端 act=1"},
    }
    for mode in ("across", "island"):
        out[mode] = _derive_mode(mode, cur_pass, cur_sec, spirites)
    return out


def all_recent() -> dict:
    """兼容旧调用：没有状态时给一份空壳（调用方应改用 derive）。"""
    return {"map": {}, "across": {}, "island": {}}
