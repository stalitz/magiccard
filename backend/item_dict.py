"""统一物品字典：`(大类 type, 类内编号 id) → 名字`。

## 背景（2026-09-16 定案）

魔法卡片所有奖励串（翻牌 `card_pai_open` 的 `content`、灵宠关卡 `register_gift`、
活动礼包 `package`、猫咪旅行 `giftInfo`）用的是**同一个三段式格式**：

    大类id _ 类内编号id _ 数量      例：`92_32_1` = 猫咪旅行类第32号 ×1

`大类id` 就是**玩法/品类**的编号（不是道具 id），第二段才是该类内部的物品编号。
因此「翻牌为什么会出现气球」的答案是：气球属于大类 48 的活动道具，编号 1415。

## 官方静态配置源（均免登录直取，权威）

| 大类 | 名字表来源 |
|---|---|
| 2/3/4/…/73（单条目类） | `swf/resource/resource_v_*.xml` 的 `<prop type="T" id="I" name="X">` |
| **48**（通用/活动道具） | `mk/activity_info_v_*.xml` 的 `<ac_items><ac_item id name>` |
| **92**（猫咪旅行道具） | `mk/catHome_Info_v_*.xml` 的 `<catHome_Itemlist>`；`mk/travel_Info_v_*.xml` 的 `<travel_petShop type="92">` |
| 任意类（带名字的交叉验证源） | `mk/act_addon_v_*.xml` 的 `send_gifts[{name,num,send_id,type}]`；`travel_Info` 的 `<gift_info other="T_I" value="名字">` |

### 实证链（不是推测）

- `act_addon`：`{"name":"三叶草","num":40,"send_id":9,"type":92}` ⇒ `92_9` = 三叶草
- `act_addon`：`{"name":"能量包","num":200,"send_id":1599,"type":48}` ⇒ `48_1599` = 能量包，
  与 `ac_items` 的 `id=1599 → 能量包` **独立吻合**
- `travel_Info`：`<gift_info other="92_62" value="锻造石"/>`、`other="48_1599" value="能量包"`
- `soul_elf_v_78.xml` 的 `pass_main register_gift`（同一格式的整串奖励）逐条可解：
  `58_0_100`=藏宝图×100、`59_0_100`=金钥匙×100、`57_3_100`=高级灵力果×100、
  `92_32_200`=红宝石×200、`92_59_30`=纤维×30、`92_9_10000`=三叶草×10000 …
- `travel_Info` 的 `<giftInfo item1="92_38_0" item2="92_32_5" item3="92_35_1">` 与 `catHome_Item`
  的 `38=经验药水（小） / 32=红宝石 / 35=小糖果` 逐条对上。

⚠️ 教训：`48_1415_1` 曾被误译成卡「比利亚」（因为 1415 恰好在卡库里是那张卡），
真相是 `48` 类第 1415 号的**气球**。**编号跨字典重名是常态，必须先定大类。**

## 用法

    import item_dict as it
    it.build()                       # 联网重建 data/item_dict.json（可选）
    it.item_name(92, 32)             # → "红宝石"
    it.name_of("92_32_1")            # → "红宝石"
    it.decode("92_32_1,48_1415_1")   # → [条目...]
    it.type_label(48)                # → "通用/活动道具"
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data" / "item_dict.json"
#: 人工核实过的名字（优先级最高）。格式：{"78_0": "名字"}。
#: 官方静态配置里查不到的大类（如 78）只能靠**实证**补：游戏内实际显示的名字，
#: 或 server 响应里带的名字。**禁止凭猜写进来**（项目铁律）。
_EXTRA = _ROOT / "data" / "item_names_extra.json"

#: 官方静态配置的默认地址（版本号会随游戏更新变化；`build(session=...)` 会
#: 通过 `card_run_base` 的 version_list 自动取最新版，无需手改这里）。
DEFAULT_URLS = {
    "resource": "http://appimg2.qq.com/card/swf/resource/resource_v_3442.xml",
    "propCard": "http://appimg2.qq.com/card/mk/propCard_v_26.xml",
    "activityInfo": "http://appimg2.qq.com/card/mk/activity_info_v_1275.xml",
    "catHomeInfo": "http://appimg2.qq.com/card/mk/catHome_Info_v_114.xml",
    "travelInfo": "http://appimg2.qq.com/card/mk/travel_Info_v_461.xml",
    "actAddon": "http://appimg2.qq.com/card/mk/act_addon_v_1665.xml",
    "soulElfCfg": "http://appimg2.qq.com/card/mk/soul_elf_v_78.xml",
}

#: `card_run_base.version_list` 里的键 → 本地源名
_VERSION_KEYS = {
    "resource": "resource",
    "propCardUrl": "propCard",
    "activityInfo": "activityInfo",
    "catHomeInfo": "catHomeInfo",
    "travelInfo": "travelInfo",
    "actAddonUrl": "actAddon",
    "soulElfCfgUrl": "soulElfCfg",
}

#: 大类标签。48/57/92/93/97 由上面「实证链」确定，其余单条目类用其物品名，
#: 再兜底用 `travel_petShop` 的商店名（88=猫咪商店、91=食物商店）。
_TYPE_LABELS = {
    "48": "通用/活动道具",
    "57": "灵力果",          # resource type=57 id=1..5 = 初级/中级/高级/1000点/3000点灵力果
    "92": "猫咪旅行道具",
    "93": "猫咪小屋经验",     # act_addon: {"name":"猫咪小屋经验","send_id":1,"type":93}
    "97": "猫咪装备",         # act_addon: 97_40783 = 「A级比赛服装迎春鼓 主属：跑步」
}

#: `travel_petShop` 的 type → 商店名（解析时填，作为大类标签的兜底）
_SHOP_LABELS: dict[int, str] = {}

#: 名字冲突时优先级（大者胜）。
#: 整表（resource / ac_items / catHome_Item / travel 商店）> 交叉引用（gift_info）
#: > propCard > act_addon —— 因为 `act_addon.send_gifts[].name` 是**展示串**，
#: 常把数量拼进名字（实测「金粉2000」「现金100」「十周年经验」），不如整表干净。
_PRIORITY = {
    "resource_prop": 5,
    "ac_items": 5,
    "cathome_item": 5,
    "travel_shop": 5,
    "gift_info": 3,
    "propcard": 2,
    "act_addon": 1,
}

_cache: dict | None = None


def _warn_missing() -> None:
    print(f"[item_dict] 未找到 {_DATA}，编号将显示为「未知道具#大类_编号」。"
          f"请联网重建：python -m backend.item_dict")


# ---------------------------------------------------------------------
# 各配置源的解析器（纯正则，避免 5MB XML 反复建树）
# ---------------------------------------------------------------------
def _attrs(s: str) -> dict:
    return dict(re.findall(r'(\w+)="([^"]*)"', s))


def parse_resource_prop(text: str):
    """`resource_v_*.xml` 的 `<prop type id name>`（单条目类，如 46=五彩石）。"""
    for m in re.finditer(r"<prop\b([^>]*)/?>", text):
        a = _attrs(m.group(1))
        if a.get("type") and a.get("id") is not None and a.get("name"):
            yield int(a["type"]), int(a["id"]), a["name"], "resource_prop"


def parse_propcard(text: str):
    """`propCard_v_*.xml`：`<items id="大类" content="类内编号" name="名字">`。

    ⚠️ 只有**带 `content`** 的条目才是「大类 = id、编号 = content」（如 id=48 content=4
    → 五彩石，与 resource 的 `type=48 id=4` 吻合）。不带 content 的 id 里混着
    23~30 之类的**套卡主题**，不能当成 (id, 0)。
    """
    for m in re.finditer(r"<items\b([^>]*)/?>", text):
        a = _attrs(m.group(1))
        if a.get("content") and a.get("name") and a.get("id"):
            yield int(a["id"]), int(a["content"]), a["name"], "propcard"


def parse_ac_items(text: str):
    """`activity_info` 的 `<ac_item id name>` —— 就是**大类 48** 的名字表。"""
    m = re.search(r"<ac_items>(.*?)</ac_items>", text, re.S)
    if not m:
        return
    for it in re.finditer(r"<ac_item\b([^>]*)/?>", m.group(1)):
        a = _attrs(it.group(1))
        if a.get("id") and a.get("name"):
            yield 48, int(a["id"]), a["name"], "ac_items"


def parse_cathome_items(text: str):
    """`catHome_Info` 的 `<catHome_Item id name>` —— **大类 92** 的名字表。"""
    m = re.search(r"<catHome_Itemlist>(.*?)</catHome_Itemlist>", text, re.S)
    if not m:
        return
    for it in re.finditer(r"<catHome_Item\b([^>]*)/?>", m.group(1)):
        a = _attrs(it.group(1))
        if a.get("id") and a.get("name"):
            yield 92, int(a["id"]), a["name"], "cathome_item"


def parse_travel_shops(text: str):
    """`travel_Info` 的 `<travel_petShop type="T"><item itemid name>`。

    商店自己的名字（如 `type="92" name="道具商店"`）回传为**大类标签**的兜底来源。
    """
    for m in re.finditer(r"<travel_petShop\b([^>]*)>(.*?)</travel_petShop>", text, re.S):
        a0 = _attrs(m.group(1))
        t = a0.get("type")
        if not t:
            continue
        if a0.get("name"):
            _SHOP_LABELS[int(t)] = a0["name"]
        for it in re.finditer(r"<item\b([^>]*)/?>", m.group(2)):
            a = _attrs(it.group(1))
            if a.get("itemid") and a.get("name"):
                yield int(t), int(a["itemid"]), a["name"], "travel_shop"


def parse_gift_info_pairs(text: str):
    """`<gift_info other="大类_编号" value="名字">` —— 显式带名字的引用。"""
    for m in re.finditer(r"<gift_info\b([^>]*)/?>", text):
        a = _attrs(m.group(1))
        other, value = a.get("other") or "", a.get("value") or ""
        mm = re.fullmatch(r"(\d+)_(\d+)", other.strip())
        if mm and value and not value.isdigit():
            yield int(mm.group(1)), int(mm.group(2)), value, "gift_info"


def parse_act_addon(text: str):
    """`act_addon` 的 `send_gifts`: `{"name":X,"num":N,"send_id":I,"type":T}`。

    `type=0` 是「无物品」占位（实测存在 `{"name":"0","send_id":0,"type":0}`），跳过。
    """
    pat = re.compile(
        r'\{\s*"name"\s*:\s*"([^"]*)"\s*,\s*"num"\s*:\s*\d+\s*,'
        r'\s*"send_id"\s*:\s*(\d+)\s*,\s*"type"\s*:\s*(\d+)\s*\}'
    )
    for name, sid, t in pat.findall(text):
        if name and int(t) != 0:
            yield int(t), int(sid), name, "act_addon"


#: 源名 → 解析器
PARSERS = {
    "resource": parse_resource_prop,
    "propCard": parse_propcard,
    "activityInfo": parse_ac_items,
    "catHomeInfo": parse_cathome_items,
    "travelInfo": parse_travel_shops,
    "actAddon": parse_act_addon,
}

#: 解析 `gift_info` 拿的是 travelInfo 的正文，单独再扫一遍
EXTRA_PARSERS = {"travelInfo": [parse_gift_info_pairs]}


def _fetch(url: str) -> str:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def parse_version_list(raw: str) -> dict:
    """`card_run_base` 的 version_list 正文 → {源名: 当前地址}（用来刷新版本号）。

    取法：用登录态调一次 `card_run_base`，把它的 `raw` 传进来即可，例如
    `print(item_dict.parse_version_list((await s.post("card_run_base"))["raw"]))`
    """
    out = {}
    for m in re.finditer(r'"name"\s*:\s*"([^"]+)"\s*,\s*"url"\s*:\s*"([^"]+)"', raw or ""):
        src = _VERSION_KEYS.get(m.group(1))
        if src:
            out[src] = m.group(2)
    return out


def build(urls: dict | None = None, verbose: bool = True) -> dict:
    """联网拉全部公开配置 → 合并 → 写 `data/item_dict.json`。"""
    urls = {**DEFAULT_URLS, **(urls or {})}
    best: dict[tuple, tuple] = {}   # (t,i) -> (prio, name, src)
    conflicts = []
    counts = {}
    texts: dict[str, str] = {}
    for src, url in urls.items():
        try:
            texts[src] = _fetch(url)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  ! {src} 拉取失败：{type(e).__name__} {e}")
    for src, fn in PARSERS.items():
        if src not in texts:
            continue
        n = 0
        for t, i, name, s in fn(texts[src]):
            n += 1
            prio = _PRIORITY.get(s, 0)
            cur = best.get((t, i))
            if cur is None:
                best[(t, i)] = (prio, name, s)
            elif cur[1] != name:
                conflicts.append((t, i, cur[2], cur[1], s, name))
                if prio > cur[0]:
                    best[(t, i)] = (prio, name, s)
        counts[src] = counts.get(src, 0) + n
    for src, fns in EXTRA_PARSERS.items():
        if src not in texts:
            continue
        for fn in fns:
            n = 0
            for t, i, name, s in fn(texts[src]):
                n += 1
                prio = _PRIORITY.get(s, 0)
                cur = best.get((t, i))
                if cur is None:
                    best[(t, i)] = (prio, name, s)
                elif cur[1] != name:
                    conflicts.append((t, i, cur[2], cur[1], s, name))
                    if prio > cur[0]:
                        best[(t, i)] = (prio, name, s)
            counts[src] = counts.get(src, 0) + n

    items = {f"{t}_{i}": v[1] for (t, i), v in best.items()}
    per_type: dict[str, int] = {}
    for (t, _i) in best:
        per_type[str(t)] = per_type.get(str(t), 0) + 1
    types = {}
    for t, n in per_type.items():
        label = _TYPE_LABELS.get(t)
        if label is None:
            label = _SHOP_LABELS.get(int(t)) if t.isdigit() else None
        if label is None:
            label = items.get(f"{t}_0") or "（未知大类）"
        types[t] = {"name": label, "n": n}

    data = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "urls": urls,
        "counts": counts,
        "types": types,
        "items": items,
        "conflicts": conflicts,
    }
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    _DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if verbose:
        print(f"共 {len(items)} 条 (大类, 编号)→名字，写入 {_DATA}")
        print("各大类条目数:", dict(sorted(per_type.items(), key=lambda kv: int(kv[0]))))
        if conflicts:
            print(f"⚠️ {len(conflicts)} 处跨源名字冲突（已按优先级取值）：")
            for c in conflicts[:15]:
                print(f"   {c[0]}_{c[1]}: {c[2]}「{c[3]}」vs {c[4]}「{c[5]}」")
    global _cache
    _cache = data
    return data


# ---------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------
def load_extra() -> dict:
    """读 `data/item_names_extra.json`（人工核实过的名字）→ {"大类_编号": "名字"}。"""
    try:
        d = json.loads(_EXTRA.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  (文件可选)
        return {}
    return {str(k): str(v) for k, v in (d.get("items") or {}).items() if v}


def set_extra(key: str, name: str) -> dict:
    """把一条**已核实**的名字写进覆盖表并热生效。

    key = "大类_编号"（如 "78_0"）。名字来源只能是实证：游戏内界面显示的名字、
    或 server 响应里带的名字 —— 禁止凭猜（项目铁律）。
    """
    key, name = str(key).strip(), str(name).strip()
    if not re.fullmatch(r"\d{1,3}_\d{1,7}", key):
        raise ValueError("key 必须是「大类_编号」，如 78_0")
    if not name:
        raise ValueError("名字不能为空")
    try:
        d = json.loads(_EXTRA.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {"_note": "人工核实过的道具名（优先级最高）", "items": {}}
    d.setdefault("items", {})[key] = name
    _EXTRA.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    global _cache
    _cache = None          # 丢掉缓存 → 下次查询重新合并覆盖层
    return {"key": key, "name": name, "items": d["items"]}


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_DATA.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _warn_missing()
            _cache = {"items": {}, "types": {}}
        # 人工核实层：覆盖官方表（官方表查不到的大类只能靠它补）
        extra = load_extra()
        if extra:
            items = dict(_cache.get("items") or {})
            for k, v in extra.items():
                t = k.split("_")[0]
                items[k] = v
                types = _cache.setdefault("types", {})
                if t not in types:
                    types[t] = {"name": "（人工核实）", "n": 0}
                types[t]["n"] = types[t].get("n", 0) + 1
            _cache["items"] = items
            _cache["extra_count"] = len(extra)
    return _cache


def item_name(t, i) -> str:
    """(大类, 类内编号) → 名字；查不到返回空串。"""
    try:
        return (_load().get("items") or {}).get(f"{int(t)}_{int(i)}", "")
    except (TypeError, ValueError):
        return ""


def type_label(t) -> str:
    """大类编号 → 品类名（如 48 → 通用/活动道具）。"""
    try:
        return ((_load().get("types") or {}).get(str(int(t))) or {}).get("name", "")
    except (TypeError, ValueError):
        return ""


def count() -> int:
    return len(_load().get("items") or {})


def decode(content: str) -> list:
    """解析奖励串 `"大类_编号_数量,..."` → [{type,id,num,name,text}, ...]。

    同名条目会合并。查不到编号时 name 为空串、text 为「未知道具#T_I」——
    **绝不猜名**（项目铁律：常量必须有实证来源）。
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
        key = (t, i)
        agg[key] = agg.get(key, 0) + n
    out = []
    for (t, i), n in agg.items():
        nm = item_name(t, i)
        out.append({
            "type": t, "id": i, "num": n, "name": nm,
            "text": f"{nm}×{n}" if nm else f"未知道具#{t}_{i}×{n}",
        })
    return out


def summarize(content: str) -> str:
    """奖励串 → 一行可读文本，如「红宝石×1、气球×1」。"""
    items = decode(content)
    return "、".join(x["text"] for x in items) if items else ""


if __name__ == "__main__":  # python -m backend.item_dict
    build()
