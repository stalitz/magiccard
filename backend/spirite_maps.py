"""灵宠探险：大关（大地图）与小关名表 —— 数据源 = **官方客户端配置**。

数据文件 `data/spirite_pass.json`，源头是
`http://appimg2.qq.com/card/mk/soul_elf_v_78.xml`（公开静态 XML，免登录直取），
由 `card_run_base` 返回的 `soulElfCfgUrl` 字段指向。

2026-09-15 起整个模块改为官方数据驱动。此前靠「用户口述 + 卡库推断」得出的三样东西
全部得到官方印证，可以直接删掉猜测逻辑：

  | 曾经的推断 | 官方字段 | 结果 |
  |---|---|---|
  | 11 个大关的名字与 theme_id | `pass_mainlist`      | 逐项一致 |
  | 小关名 = 该大关套卡里的卡名 | `pass_sectionlist[].name` | 一致 |
  | 小关顺序「不可信」（只能按卡 id 升序） | `pass_sectionlist[].section_id` | **已解决**，官方顺序即游戏内顺序 |

官方还给了此前完全没有的维度（见 `section_info`）：
`max_spirite_num`（该关最多派几只灵宠，取值 1/2/4）· `need_gold_key` / `need_map`
（解锁消耗，170→1050 递增）· `consume_blood_min~max`（灵宠血量消耗）·
`pass_att` / `pass_no_match_att`（属性匹配 / 不匹配）。

另附官方灵宠名表（`spirits()`，`spirit_maplist`，16 只：金木水火土风光电 × 雄雌）。
"""
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data" / "spirite_pass.json"

_cache: dict | None = None
_table_cache: dict | None = None


def _load() -> dict:
    """整个官方配置（懒加载 + 缓存）。文件不在时返回空壳，不抛异常。"""
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_DATA.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _cache = {"mains": [], "sections": []}
    return _cache


def _mains() -> dict:
    """{pass_id: {name, theme_id, desc}}（大关）。"""
    return {int(m["pass_id"]): m for m in (_load().get("mains") or [])}


def _sections() -> dict:
    """{pass_id: [小关 dict, ...]}，按官方 section_id 升序。"""
    out: dict = {}
    for s in _load().get("sections") or []:
        out.setdefault(int(s["pass_id"]), []).append(s)
    for v in out.values():
        v.sort(key=lambda x: int(x["section_id"]))
    return out


def spirits() -> list:
    """官方灵宠名表（16 只：金木水火土风光电 × 雄雌）。"""
    return list(_load().get("spirits") or [])


def spirit_name(sid) -> str:
    """灵宠 id → 官方名（如 9 → 「土灵宠 （雄）」）；查不到返回空串。"""
    for sp in spirits():
        if int(sp["id"]) == int(sid):
            return str(sp["name"]).strip()
    return ""


#: 大关编号 → 套卡 theme_id（官方 pass_mainlist）
MAP_THEMES = {k: v["theme_id"] for k, v in _mains().items()}

#: 大关下拉候选编号（官方给几章就是几章，不再拍脑袋放宽）
MAP_IDS = tuple(sorted(_mains())) or tuple(range(1, 12))


def stage_table() -> dict:
    """大关 → 小关表（含官方附加维度）。

    返回形如::

        { "6": {"theme_id": 773, "name": "世界古遗迹", "desc": "...",
                "stages": [{"index": 1, "card_id": 12298, "name": "博尔巴任",
                            "max_spirite_num": 2, "need_gold_key": 170,
                            "need_map": 170, "blood_min": 180, "blood_max": 300,
                            "att": 5, "no_match_att": 3}, ...]}}

    `index` 即 `section_id`，顺序 = 官方顺序 = 游戏内顺序。
    """
    global _table_cache
    if _table_cache is not None:
        return _table_cache
    mains, secs = _mains(), _sections()
    out: dict = {}
    for pid, m in mains.items():
        out[str(pid)] = {
            "theme_id": m.get("theme_id"),
            "name": str(m.get("name") or "").strip(),
            "desc": str(m.get("desc") or "").strip(),
            "stages": [
                {
                    # 官方 XML 里部分小关名带前导空格（如「 秦岭兵马俑」），统一 strip
                    "index": int(s["section_id"]),
                    "card_id": int(s.get("card_id") or 0),
                    "name": str(s.get("name") or "").strip(),
                    "max_spirite_num": int(s.get("max_spirite_num") or 2),
                    "need_gold_key": int(s.get("need_gold_key") or 0),
                    "need_map": int(s.get("need_map") or 0),
                    "blood_min": int(s.get("blood_min") or 0),
                    "blood_max": int(s.get("blood_max") or 0),
                    "att": int(s.get("att") or 0),
                    "no_match_att": int(s.get("no_match_att") or 0),
                }
                for s in secs.get(pid, [])
            ],
        }
    _table_cache = out
    return out


def _find(pass_id, section_id):
    tab = stage_table().get(str(pass_id)) or {}
    for st in tab.get("stages", []):
        if st["index"] == int(section_id):
            return st
    return None


def stage_name(pass_id, section_id) -> str:
    """小关名（查不到返回空串）。"""
    st = _find(pass_id, section_id)
    return st["name"] if st else ""


def map_name(pass_id) -> str:
    """大关名（查不到返回空串）。"""
    return (stage_table().get(str(pass_id)) or {}).get("name", "")


def section_info(pass_id, section_id) -> dict:
    """该小关的官方完整信息（含 max_spirite_num 等）；查不到返回 {}。"""
    return _find(pass_id, section_id) or {}


def max_spirite_num(pass_id, section_id) -> int:
    """该关官方允许同时派出的灵宠数（1/2/4），查不到按 2。"""
    return int((_find(pass_id, section_id) or {}).get("max_spirite_num") or 2)


# ---------------------------------------------------------------------------
# 灵宠玩法的另外两套（挂同一个「灵宠探险」入口下，与大地图并列）
#
# 官方 soul_elf 里除 `pass_mainlist`/`pass_sectionlist`（大地图）外，还有两个独立容器：
#
#   | 玩法 | 官方标签 | 规模 | 需求字段 | 状态来源（act=1） |
#   |---|---|---|---|---|
#   | 大地图 | `pass_mainlist`   | 11 章 122 关 | `max_spirite_num` 1/2/4 | `explore.*`（pass_id/section_id） |
#   | 荒岛   | `c_island`        |  8 章  48 关 | `elf_num` 3~8 + **`elf_hp`**（战斗） | `hy_*` |
#   | 穿越丛林 | `c_across`      | 11 章  62 关 | `elf_num` 1~6 + `elf_lv`（等级门槛） | 未确认 |
#
# 荒岛 ↔ `hy_*` 的证据（2026-09-15 实测，两条独立吻合）：
#   ① `hy_put_spirites` = [1,3,5,7,9]（5 只）↔ 荒岛是唯一存在 `elf_num=5` 的玩法；
#   ② `hy_explore_pass_id = 16` ↔ 荒岛连续编号第 16 关 =「戴尔得荒岛4」，其 `elf_num` 正好 5。
#   且 `zj_bits=255`（8 位全开）↔ 荒岛 8 章；首章名「荒野孤岛」→ hy = 荒野。
# 荒岛的关卡号走 `hy_explore_pass_id`（**连续编号 1..48**），不是 pass*100+section
# （`hy_section_id=65535` 是无效值，别拿它算）。
#
# 注意：两套玩法的关卡号都是「连续编号」（seq），跨章不重置。
# ---------------------------------------------------------------------------

_MODES = {"islands": "c_island", "across": "c_across"}


def _chapters(mode: str) -> list:
    return list((_load().get(mode) or {}).get("chapters") or [])


def _place(mode: str, seq) -> dict:
    """连续编号（seq）→ 该关卡 dict（跨章查找）；查不到返回 {}。"""
    try:
        seq = int(seq)
    except (TypeError, ValueError):
        return {}
    for ch in _chapters(mode):
        for s in ch.get("sections") or []:
            if int(s.get("seq") or 0) == seq:
                return {**s, "chapter_id": ch.get("chapter_id"),
                        "chapter_name": ch.get("name")}
    return {}


def _mode_table(mode: str) -> dict:
    """{chapter_id: {name, open_c, tips, sections:[...]}}。"""
    out: dict = {}
    for ch in _chapters(mode):
        out[str(ch.get("chapter_id"))] = {
            "chapter_id": int(ch.get("chapter_id") or 0),
            "name": str(ch.get("name") or "").strip(),
            "open_c": int(ch.get("open_c") or 0),
            "tips": str(ch.get("tips") or "").strip(),
            "sections": [
                {**{k: v for k, v in s.items() if k != "award"},
                 "name": str(s.get("name") or "").strip(),
                 "award": str(s.get("award") or "").strip()}
                for s in (ch.get("sections") or [])
            ],
        }
    return out


def island_table() -> dict:
    """荒岛（c_island）章节表。"""
    return _mode_table("islands")


def across_table() -> dict:
    """穿越丛林（c_across）章节表。"""
    return _mode_table("across")


def island_place(seq) -> dict:
    """荒岛连续编号 → 关卡（含 chapter_name / elf_num / elf_hp）。"""
    return _place("islands", seq)


def across_place(seq) -> dict:
    """穿越丛林连续编号 → 关卡。"""
    return _place("across", seq)


def island_place_text(seq) -> str:
    """荒岛编号 → 可读文本，如 16 → 戴尔得荒岛 · 第4关 戴尔得荒岛4。"""
    p = island_place(seq)
    if not p:
        return ""
    return f"{p.get('chapter_name')} · 第{p.get('index')}关 {p.get('name')}"


def across_place_text(seq) -> str:
    p = across_place(seq)
    if not p:
        return ""
    return f"{p.get('chapter_name')} · 第{p.get('index')}关 {p.get('name')}"


# ---------------------------------------------------------------------------
# 接口参数映射（2026-09-15 真机抓包 + 9 组真调实验确定，勿凭直觉改）
# ---------------------------------------------------------------------------
#
# 【穿越丛林 · 历练】card_user_spirite `act=11`，参数 `sub_id` / `id` / `cnt`
#
#   **参数语义（用户 2026-09-15 确认）**：`id` = 1 级地图编号，`sub_id` = 2 级地图编号。
#   ⚠️ 别与每关自带的 `open_c` / `open_s` 混淆：后两者是**解锁条件**
#      （需大地图第 open_c 章第 open_s 关已通过），与 act=11 的参数无关。
#      全表据此判定「能去的最新关」，见 backend/spirite_progress.py。
#   1 级地图 = 官方 c_across 的 chapter（11 个：热带雨林 … 翠峰林）；
#   2 级地图 = chapter 下的 section（章内关卡，如「洪泛森林4」）。
#   两者都是 **0-based**（实测），即
#
#       id     = 1 级地图序号 - 1   （洪泛森林是第 6 个 1 级地图 → id = 5）
#       sub_id = 2 级地图序号 - 1   （洪泛森林4 是章内第 4 关   → sub_id = 3）
#
#   用户抓包 `sub_id=3&act=11&cnt=1&id=5`，截图里点的是「洪泛森林4」→ (3,5) ✅
#
#   9 组真调全部自洽（torrid_zone_explore_cnt 只在成功时 +1）：
#     (3,5)=洪泛森林4 ✅   —— 截图里该关正是「探索」按钮
#     (2,5)=洪泛森林3 ✅   —— 该章已通的关可重复历练
#     (4,5)=洪泛森林5 ❌-2 —— 截图中明确显示「未解锁」
#     (5,5)=洪泛森林6 ❌-2 —— 同上
#     (4,6)=针叶林5   ❌-1 —— 第 7 个 1 级地图整片未开启（错误码与上一类不同）
#     (5,6)=针叶林6   ❌-1
#     (6,4)/(6,5)=2 级地图序号 7 ⚪空返回(code=0 无 content，不计次)
#     (4,4)=平原湿地森林5 ✅
#
#   套卡碎片是**概率掉落**（用户确认）：奖励栏里的《XX》碎片不是每次必给，
#   返回 `content` 里稳定出现的是通用道具（银锭/百变丹/金钥匙等）。
#
#   奖励走返回的 `content` 字段（`大类id_类内编号id_数量`，逗号分隔），
#   道具名见 `prop_dict.reward_name`（查官方统一表 backend/item_dict.py，
#   如 59=金钥匙 / 72=银锭 / 73=百变丹 / 58=藏宝图）。
#   **历练是即时的**（不像大地图/荒岛要等时间），返回即到账，无需收获。
def across_param(chapter_id, index) -> tuple:
    """(1 级地图序号, 2 级地图序号) → act=11 的 `(sub_id, id)`。

    两者都 **0-based**（实测）：`sub_id = 2 级地图序号 - 1`、`id = 1 级地图序号 - 1`。
    """
    return int(index) - 1, int(chapter_id) - 1


def across_place_by_ci(chapter_id, index) -> dict:
    """(章号, 关内序号) → 关卡 dict；查不到返回 {}。"""
    for ch in _chapters("across"):
        if int(ch.get("chapter_id") or 0) != int(chapter_id):
            continue
        for s in ch.get("sections") or []:
            if int(s.get("index") or 0) == int(index):
                return {**s, "chapter_id": ch.get("chapter_id"),
                        "chapter_name": ch.get("name")}
    return {}


def across_place_seq(chapter_id, index):
    """(章号, 关内序号) → 该关的连续编号 seq（查不到返回 0）。"""
    return int((across_place_by_ci(chapter_id, index) or {}).get("seq") or 0)


# 【荒岛 · 探险】card_user_spirite `act=14`（出发）/ `act=15`（收获）
#
#   用户抓包：`type=1&act=14&id=16&ids=1,3,5,7,9`
#     `id` = 2 级地图的**全局连续编号 seq（1..48）**，跨 1 级地图不重置，
#     不是「1 级地图 + 章内序号」两段式（这点与穿越 act=11 不同）——
#     id=16 恰好是「戴尔得荒岛4」（第 3 个 1 级地图的第 4 关，seq 13..18 中的 16），
#     与截图逐项吻合。`type=1` 为探险类型，固定值。
#     `ids` = 逗号串灵宠 id（本关需 5 只，实测 elf_num=5）。
#   收获：`act=15`（**无参数**）。实测调用后 `explore.hy_explore_ts` 归零、
#     `hy_put_spirites` 清空，并真实入包（碎片 + 百变丹 +5）。
#     注意 `act=4` 是**大地图专用**收获，对荒岛返回 -5。
#   时长：3 分钟（游戏内文案），到点后才能收 —— 早收返回 -2。
#   解锁：每关自带 `open_c`/`open_s` = **需大地图第 open_c 章第 open_s 关已通过**
#     （与穿越同理，不是本关坐标）；「能去的最新关」由 spirite_progress 逐关推导。


def island_place_by_ci(chapter_id, index) -> dict:
    """(章号, 关内序号) → 荒岛关卡 dict；查不到返回 {}。"""
    for ch in _chapters("islands"):
        if int(ch.get("chapter_id") or 0) != int(chapter_id):
            continue
        for s in ch.get("sections") or []:
            if int(s.get("index") or 0) == int(index):
                return {**s, "chapter_id": ch.get("chapter_id"),
                        "chapter_name": ch.get("name")}
    return {}


def island_seq(chapter_id, index) -> int:
    """(章号, 关内序号) → 荒岛 act=14 用的 `id`（连续编号 1..48）。"""
    return int((island_place_by_ci(chapter_id, index) or {}).get("seq") or 0)


def island_elf_num(chapter_id, index) -> int:
    """该荒岛关需要几只灵宠（官方 `elf_num`，3~8）。"""
    return int((island_place_by_ci(chapter_id, index) or {}).get("elf_num") or 0)
