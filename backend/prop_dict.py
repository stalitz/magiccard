"""道具字典：道具编号 → 可读名字。

数据文件 `data/prop_dict.json`，来自两处公开静态配置（免登录）：
  - `mk/propCard_v_26.xml`  → 道具卡（升炉卡 / 开箱卡 / 面值卡 / 租炉卡 …）
  - `mk/soul_elf_v_78.xml` 的 `card_piecelist` → 卡片碎片（488 种）

用途：把奖励编号翻译成可读文本。**奖励条目统一是 `大类id_类内编号id_数量`**
（灵宠历练 act=11 的 `content`、关卡 `send_gifts`、翻牌 `content`、活动礼包
`package`、猫咪旅行 `giftInfo` 全是同一格式），名字一律查官方静态配置
`backend/item_dict.py`。

关于 `send[]` 的字段（实测 + 官方 `pass_section/send_gifts_3` 对照）：
  - `type` 是**物品大类**（57=灵力果、58=藏宝图、59=金钥匙、72=银锭、73=百变丹…）
  - `id`   是该类下的**类内编号**（57_1/2/3 = 初级/中级/高级灵力果；
           type=56 卡片碎片时对应 `card_piecelist` 的碎片 id）
  - `num`  是数量
官方关卡配置 `send_gifts_3` 也是同一套 `s_type` / `s_id` / `s_num` / `s_ratio`（概率‰）。

⚠️ 曾经这里手写了一张 `REWARD_ITEM_IDS`（56碎片/57灵力果/58高级灵力果/59金钥匙/
72银锭/73百变丹），2026-09-16 **已删除**：官方配置能完整覆盖，且硬编码本身就有错
—— `58` 官方是**藏宝图**不是「高级灵力果」，`57` 还分三个档位。铁律：常量必须有实证来源。
"""
import json
from pathlib import Path

from . import item_dict as _item_dict

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data" / "prop_dict.json"

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_DATA.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _cache = {"props": {}, "pieces": {}, "spirite_types": {}}
    return _cache


def prop_name(pid) -> str:
    """道具卡 id → 名（升炉卡 / 开箱卡 …）。"""
    return (_load().get("props") or {}).get(str(pid), {}).get("name", "")


def piece_name(pid) -> str:
    """碎片 id → 名（如 201 → 「xxx碎片1」）。"""
    return (_load().get("pieces") or {}).get(str(pid), "")


def type_name(t) -> str:
    """物品大类 → 名（优先官方统一表 `item_dict`，回退旧 `spirite_types`）。"""
    nm = _item_dict.type_label(t)
    if not nm or nm == "（未知大类）":
        nm = (_load().get("spirite_types") or {}).get(str(t), "")
    return nm


def describe(item: dict) -> str:
    """把收获 `send[]` 的一条转成可读文本，如「中级灵力果×2」「藏宝图×1」。

    与 `describe_all` 同规则（都走 `reward_name`）。
    """
    t = item.get("type") or 0
    sid = item.get("id") or 0
    num = item.get("num") or 0
    return f"{reward_name(t, sid)}×{num}"


# =====================================================================
# 奖励串解析：`大类id_类内编号id_数量`
# =====================================================================
# 灵宠历练（act=11）的 `content`、关卡 `send_gifts`、翻牌 `content`、活动礼包
# `package`、猫咪旅行 `giftInfo` **全是同一个三段式格式**（实证链见 item_dict 模块注释）。
# 名字一律查官方静态配置 `backend/item_dict.py`，**不再维护手写常量表**：
#   - `57_1/2/3` → 初级/中级/高级灵力果（旧表笼统显示「灵力果」）
#   - `58_0`     → **藏宝图**（旧表 `REWARD_ITEM_IDS` 错写成「高级灵力果」）
#   - `56_<碎片编号>` → `card_piecelist` 的碎片全名（488 种）


def reward_name(t, i=0) -> str:
    """奖励条目 (大类, 类内编号) → 名（查不到返回 `未知道具#T_I`）。

    来源（全部是官方静态配置，优先级从高到低）：
      1. `type=56` 且编号非 0 → `card_piecelist` 的碎片全名
      2. `item_dict` 的 (大类, 编号) 表 —— 主来源
      3. `item_dict` 的大类标签
      4. 编号为 0 时回退道具卡目录 `prop_name`
    查不到**绝不猜名**（项目铁律：常量须有实证来源）。
    """
    try:
        t, i = int(t), int(i or 0)
    except (TypeError, ValueError):
        return f"未知道具#{t}_{i}"
    if t == 56 and i:
        nm = piece_name(i)
        if nm:
            return nm
    nm = _item_dict.item_name(t, i) or _item_dict.type_label(t)
    if not nm or nm == "（未知大类）":
        nm = prop_name(t) if i == 0 else ""
    return nm or f"未知道具#{t}_{i}"


def describe_reward(content: str) -> list:
    """解析奖励串（形如 `"73_0_5,72_0_105,57_2_2"`）→ 可读条目。

    每条是 `大类id_类内编号_数量`，逗号分隔。返回
    `[{type, id, num, name, text}, ...]`，同名条目**合并**
    （`73_0_5,73_0_5` → 「百变丹×10」，与实测 `baibiandan +10` 一致）。
    """
    agg: dict = {}
    for part in str(content or "").split(","):
        f = part.strip().split("_")
        if len(f) < 3:
            continue
        try:
            t, i, n = int(f[0]), int(f[1]), int(f[2])
        except ValueError:
            continue
        agg[(t, i)] = agg.get((t, i), 0) + n
    out = []
    for (t, i), n in agg.items():
        nm = reward_name(t, i)
        out.append({"type": t, "id": i, "num": n, "name": nm, "text": f"{nm}×{n}"})
    return out


def summarize_reward(content: str) -> str:
    """奖励串 → 一行可读文本，如「百变丹×10、银锭×105」。"""
    return "、".join(i["text"] for i in describe_reward(content))


def summarize_all(send) -> str:
    """收获 `send[]` → 一行可读文本（与 `summarize_reward` 同规则）。"""
    return "、".join(i["text"] for i in describe_all(send))


def describe_all(send) -> list:
    """收获 `send[]` → [{type, id, num, name, text}, ...]（规则同 `reward_name`）。"""
    out = []
    for it in send or []:
        if not isinstance(it, dict):
            continue
        t = it.get("type") or 0
        sid = it.get("id") or 0
        num = it.get("num") or 0
        nm = reward_name(t, sid)
        out.append({"type": t, "id": sid, "num": num, "name": nm,
                    "text": f"{nm}×{num}"})
    return out


# =====================================================================
# 翻牌（取卡后的「翻牌」抽奖）奖励翻译
# =====================================================================
# 翻牌 `card_pai_open` 的 `content` 形如 `"48_1415_1"` / `"92_32_1"` / `"7_0_1"`，
# 三段 = `大类_类内编号_数量`，**和灵宠关卡 register_gift、活动礼包 package 是
# 同一个格式**（见 item_dict 模块注释里的实证链）。
#
#   - 第 1 段是**玩法/品类 id**（不是道具 id）：4=小魔瓶类、7=面值卡类、
#     44=金币、46=五彩石、47=金粉、48=通用/活动道具、92=猫咪旅行道具 …
#   - 第 2 段是**该类内部的物品编号**：如 `48_1415` = 气球、`92_32` = 红宝石、
#     `92_9` = 三叶草。
#
# ⚠️ 走过的两次弯路（都要记住）：
#   1. 一开始把第 2 段当成「卡 id」查卡库 → `48_1415_1` 被译成卡「比利亚」，
#      而它其实是**气球**（1415 只是在卡库里恰好也有这张卡，跨字典重名）。
#   2. 也曾把第 1 段当道具卡 id 查 propCard 目录 → 目录里 id=48 重名 9 样
#      （五彩石/加速卡/减时卡…），根本分不清。
#   正解只有一个：**先定大类，再查类内编号**。名字表见 `backend/item_dict.py`。


def describe_pai(content: str) -> list:
    """解析翻牌 content（形如 `"48_1415_1,7_0_1"`）成可读条目。

    走 `item_dict`（大类_编号 → 名字）；查不到的显示「未知道具#大类_编号」，
    绝不猜名（项目铁律：常量须有实证来源）。
    """
    return _item_dict.decode(content)


def summarize_pai(content: str) -> str:
    """翻牌 content → 一行可读文本，如「气球×1、100面值卡×1」。"""
    return "、".join(i["text"] for i in describe_pai(content))
