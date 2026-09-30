"""猫咪装扮名表：装备 id → 名字/槽位，属性 id → 属性名。

## 为什么单开一个模块（2026-09-27）

每日比赛的出战日志原先只打一串装备 id（`10493|20141|30383|40582`），
用户看不懂「到底穿了什么」、也不知道搭配对不对。名字必须来自**实证**，
不能猜（项目铁律：常量必须有实证来源），所以这里把官方静态配置解析成表。

## 权威源（免登录直取，官方）

    http://appimg2.qq.com/card/mk/catHome_Info_v_114.xml
      <catHome_Equiplist>
        <catHome_Equip id="10022" name="棒球帽" type="1" series="1"
                       entry1="2,1,50" entry2=""
                       desc="一顶普通的棒球帽，在“舞蹈”比赛中，装扮它可获得更高的分数">
    共 96 条，字段含义：

      · `id`        装备 id，与 act=6（`cat_home_dress_*_attr`）与猫的 `equip_arr`
                    同一编号空间，首位数字 = 槽位（1头 / 2手 / 3衣 / 4其他）
      · `name`      装备名（「棒球帽」「头巾」「魔法帽」…）
      · `type`      1=头饰 2=手持 3=服装 4=其他（与首位数字一致，双保险）
      · `entry1/2`  "属性id,等级,加成值" —— 该装扮在哪些才艺上加成分更高
      · `desc`      说明文案，引号里的才艺名**就是 entry 里属性 id 的中文名**
                    （例：entry1="2,1,50" + desc「“舞蹈”比赛」⇒ 2 = 舞蹈）

## 属性名是怎么定出来的（不是猜）

对每条装备做「entry 的属性 id ↔ desc 里的才艺名」配对投票：
只有 1 个 entry 时是**唯一配对**（强证据，权重 3）；有两项时按共现投票。
96 条全量投票结果（票数远高于次选，例如 1:唱歌×27 vs 舞蹈×2）：

    1=唱歌 2=舞蹈 3=朗诵 4=演奏 5=跑动 6=翻滚
    7=跳跃 8=游泳 9=强健 10=艳丽 11=优雅 12=灵活

`attr_votes` 会一并写进 data/cat_equip.json，随时可复查。

## 用法

    import cat_dress as cd
    cd.attr_name(5)          # → "跑动"
    cd.equip_name(10493)     # → "魔法帽"（等）
    cd.slot_of(10493)        # → "头"
    cd.describe([10493, 20141, 30122, 40141], dress)   # 可读穿装文本

`build()` 可联网重建 `data/cat_equip.json`（游戏更新版本号后重跑即可）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data" / "cat_equip.json"

#: 官方配置地址（版本号随游戏更新；用 item_dict 的同一份，避免两处漂移）。
CAT_HOME_URL = "http://appimg2.qq.com/card/mk/catHome_Info_v_114.xml"

#: 全量服装表（2026-09-29 用户抓包实证 URL；仅 http→https 301，需 https 直连）。
#: 285 条 cat_cloth，含 name/cloth_type/cloth_param，是 catHome_Info_v_114（96 条）的超集——
#: 玩家衣柜 127 件全部有名字（此前 48 件未收录由此补齐）。版本号 54188 随游戏更新，
#: 更新后在游戏里抓一次 cat_cloth_v_N.xml 换 URL 重跑 build() 即可。
CAT_CLOTH_URL = "https://appimg2.qq.com/card/mk/cat_cloth_v_54188.xml"

#: 槽位首位数字 → 名字。同样有官方源：`<catHome_Equip type>`（1=头 2=手 3=衣 4=其他）
#: 与装备 id 首位一致，这里只做 id 解析的兜底。
SLOT_BY_PREFIX = {1: "头", 2: "手", 3: "衣", 4: "其他"}
SLOT_BY_TYPE = {1: "头", 2: "手", 3: "衣", 4: "其他"}

#: 官方表缺失时的兜底（会打「?」标记，不会静默当成真相）
_FALLBACK_ATTRS = {
    1: "唱歌", 2: "舞蹈", 3: "朗诵", 4: "演奏", 5: "跑动", 6: "翻滚",
    7: "跳跃", 8: "游泳", 9: "强健", 10: "艳丽", 11: "优雅", 12: "灵活",
}

_cache: dict | None = None


# ---------------------------------------------------------------- 表构建
def _attrs(s: str) -> dict:
    return dict(re.findall(r"([\w]+)=\"([^\"]*)\"", s or ""))


def parse_cat_home(text: str) -> list:
    """`<catHome_Equiplist>` 全量解析 → [{id, name, type, desc, bonus:[(attr_id, val)], words:[...]}]。"""
    m = re.search(r"<catHome_Equiplist>(.*?)</catHome_Equiplist>", text, re.S)
    if not m:
        return []
    out = []
    for mm in re.finditer(r"<catHome_Equip\b([^>]*)/?>", m.group(1)):
        a = _attrs(mm.group(1))
        eid = a.get("id")
        if not eid:
            continue
        bonus = []
        for k in ("entry1", "entry2"):
            v = (a.get(k) or "").split(",")
            if len(v) >= 3 and v[0].strip().isdigit():
                bonus.append((int(v[0]), int(v[2])))
        out.append({
            "id": int(eid),
            "name": a.get("name") or "",
            "type": int(a["type"]) if (a.get("type") or "").isdigit() else 0,
            "desc": a.get("desc") or "",
            "bonus": bonus,
            "words": re.findall(r"“([^”]+)”", a.get("desc") or ""),
        })
    return out


def parse_cat_cloth(text: str) -> list:
    """`<cat_clothlist>` 全量解析（cat_cloth_v_54188.xml，285 条）。

    cloth_param 格式 "属性id,?,值,?,?|属性id,?,值,?,?"（与 catHome_Equip.entry 同
    属性 id 空间、量级不同——铁律：数值只认 act=6 衣橱 eq_attr，这里只取名字/槽位）。
    """
    out = []
    for mm in re.finditer(r"<cat_cloth\s+([^>]*?)/>", text, re.S):
        a = _attrs(mm.group(1))
        eid = a.get("id")
        if not eid:
            continue
        bonus = []
        for seg in (a.get("cloth_param") or "").split("|"):
            v = seg.split(",")
            if len(v) >= 3 and v[0].strip().isdigit():
                bonus.append((int(v[0]), int(v[2])))
        out.append({
            "id": int(eid),
            "name": a.get("name") or "",
            "type": int(a["cloth_type"]) if (a.get("cloth_type") or "").isdigit() else 0,
            "desc": a.get("cloth_intro") or "",
            "bonus": bonus,
            "words": re.findall(r"“([^”]+)”", a.get("cloth_intro") or ""),
            "rarity": int(a["cloth_rarity"]) if (a.get("cloth_rarity") or "").isdigit() else 0,
        })
    return out


def _fetch(url: str) -> str:
    """拉一个官方配置（必须 https：http 会 301 跳回 https）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def _vote_attrs(ents: list) -> dict:
    """entry 属性 id ↔ desc 才艺名 配对投票 → {attr_id: 属性名}。"""
    import collections
    vote: dict = collections.defaultdict(collections.Counter)
    for e in ents:
        ids = [i for i, _v in e["bonus"]]
        words = e["words"]
        if not ids or not words:
            continue
        if len(ids) == 1 and len(words) == 1:
            vote[ids[0]][words[0]] += 3            # 唯一配对 = 强证据
        elif len(ids) == len(words):
            for (i, _v), w in zip(e["bonus"], words):
                vote[i][w] += 1
        else:
            for i in ids:
                for w in words:
                    vote[i][w] += 0.5
    out, detail = {}, {}
    for i, c in vote.items():
        name, n = c.most_common(1)[0]
        top2 = c.most_common(2)
        # 次选票数接近就说明配对不可靠，宁可不写
        if len(top2) > 1 and top2[1][1] >= n and top2[1][1] > 0:
            continue
        out[i] = name
        detail[str(i)] = {k: v for k, v in c.most_common()}
    return {"names": {str(k): v for k, v in sorted(out.items())}, "votes": detail}


def build(url: str = "", cloth_url: str = "", verbose: bool = True) -> dict:
    """联网拉官方配置 → 生成属性名 + 装备名表 → 写 `data/cat_equip.json`。

    双源：catHome_Info_v_114.xml（96 条，属性名投票仍靠它的 desc/entry 配对）
    + cat_cloth_v_54188.xml（285 条全量服装，覆盖衣柜 127 件全部名字）。
    同 id 冲突时 cat_cloth 优先（更新更全）。
    """
    ents = []
    if cloth_url or CAT_CLOTH_URL:
        text = _fetch(cloth_url or CAT_CLOTH_URL)
        ents = parse_cat_cloth(text)
        if not ents:
            raise RuntimeError("cat_cloth 没解析到条目（源可能改版）")
    if url or CAT_HOME_URL:
        try:
            text = _fetch(url or CAT_HOME_URL)
            ents_old = parse_cat_home(text)
            if ents_old:
                ents = ents + ents_old
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"catHome_Info 拉取失败（继续用 cat_cloth）：{e}")
    if not ents:
        raise RuntimeError("两个配置源都没拉到数据")
    v = _vote_attrs(ents)
    equips = {}
    for e in ents:
        slot = 0
        if e["type"] in SLOT_BY_TYPE:
            slot = e["type"]
        else:
            p = str(e["id"])[:1]
            slot = int(p) if p.isdigit() and int(p) in SLOT_BY_PREFIX else 0
        equips[str(e["id"])] = {
            "name": e["name"],
            "slot": SLOT_BY_TYPE.get(slot, ""),
            "desc": e["desc"],
            "bonus": [{"id": i, "v": vv} for i, vv in e["bonus"]],
        }
    attrs = {int(k): vv for k, vv in v["names"].items()} or dict(_FALLBACK_ATTRS)
    data = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": " + ".join(filter(None, [cloth_url or CAT_CLOTH_URL, url or CAT_HOME_URL])),
        "count": len(equips),
        "attrs": {str(k): vv for k, vv in sorted(attrs.items())},
        "equips": equips,
        "attr_votes": v["votes"],
    }
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    _DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if verbose:
        print(f"写入 {_DATA}：{len(equips)} 件装扮、{len(attrs)} 个属性名")
    global _cache
    _cache = data
    return data


def load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_DATA.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _cache = {"attrs": {str(k): v for k, v in _FALLBACK_ATTRS.items()},
                      "equips": {}, "source": "", "count": 0}
    return _cache


# ---------------------------------------------------------------- 查询
def attr_name(aid) -> str:
    """属性 id → 中文名（1..12）；查不到返回「属性N」。"""
    try:
        a = int(aid)
    except (TypeError, ValueError):
        return ""
    return load().get("attrs", {}).get(str(a)) or f"属性{a}"


def equip_name(eid) -> str:
    """装备 id → 名字；查不到返回空串。"""
    try:
        return load().get("equips", {}).get(str(int(eid)), {}).get("name", "") or ""
    except (TypeError, ValueError):
        return ""


def slot_of(eid) -> str:
    """装备 id → 槽位（头/手/衣/其他）。"""
    e = load().get("equips", {}).get(str(eid) if isinstance(eid, int) else str(eid), {})
    if e.get("slot"):
        return e["slot"]
    s = str(eid)
    return SLOT_BY_PREFIX.get(int(s[0]), "") if s and s[0].isdigit() else ""


def attr_names() -> dict:
    """{属性id: 名字} 全表。"""
    return {int(k): v for k, v in (load().get("attrs") or {}).items()}


def equip_bonus(eid) -> list:
    """装备 id → [{id: 属性id, v: 加成值}]。"""
    e = load().get("equips", {}).get(str(eid), {})
    return e.get("bonus") or []


_DRESS_KEYS = ("cat_home_dress_head_attr", "cat_home_dress_hand_attr",
               "cat_home_dress_clothing_attr", "cat_home_dress_other_attr")


def equip_attrs(eid, dress: dict = None) -> dict:
    """装备 id → {属性id: 加成值}。

    **加成值只认 act=6 的 `eq_attr`（玩家身上真实的数值）**；官方表
    `catHome_Equip.entry` 里的第三个字段量级不同（实测 10022：entry=50 vs
    eq_attr.value=82），拿它求和会算错，所以只用它取名字、不取数值。
    """
    eid = int(eid)
    for key in _DRESS_KEYS:
        for it in ((dress or {}).get(key) or []):
            if int(it.get("id")) == eid:
                return {int(a.get("id")): int(a.get("value") or 0)
                        for a in (it.get("eq_attr") or [])}
    return {}


def describe(equip_ids, dress: dict = None) -> str:
    """一串装备 id → 一行可读穿装，如「头:魔法帽(演奏+266 朗诵+73)」。

    官方表未收录的 id 只显示 `#10493` 并在末尾标「未收录」，**绝不编名字**
    （官方 catHome_Info_v_114 只有 96 条，玩家背包里约 1/3 的 id 不在其中）。
    """
    parts, unknown = [], []
    for eid in equip_ids or []:
        if not eid:
            continue                      # 槽位空位（猫可能不满 4 件）跳过
        eid = int(eid)
        nm = equip_name(eid)
        if nm:
            label = f"{slot_of(eid)}:{nm}" if slot_of(eid) else nm
        else:
            label = f"#{eid}"
            unknown.append(eid)
        attrs = equip_attrs(eid, dress)
        if attrs:
            label += "(" + " ".join(f"{attr_name(k)}+{v}" for k, v in sorted(attrs.items())) + ")"
        parts.append(label)
    out = " ".join(parts) if parts else "-"
    if unknown:
        out += f"（#{','.join(str(x) for x in unknown)} 官方名表未收录，只显示编号）"
    return out


def summary(equip_ids, dress: dict = None, theme: dict | None = None) -> str:
    """可读穿装 + 属性合计（+ 命中/未命中主题），日志一行显示用。"""
    prov: dict = {}
    for eid in equip_ids or []:
        if not eid:
            continue
        for k, v in equip_attrs(int(eid), dress).items():
            prov[k] = prov.get(k, 0) + v
    txt = describe(equip_ids, dress)
    if not any(equip_ids or []):
        return "没穿装（空槽位）"           # 猫可能不满 4 件，别再补一句「没补上主题」
    if prov:
        txt += " ｜ 合计 " + " ".join(f"{attr_name(k)}+{v}" for k, v in sorted(prov.items()))
    if theme:
        hit = [(attr_name(k), v, theme.get(k, 0)) for k, v in sorted(prov.items()) if theme.get(k)]
        if hit:
            txt += " ｜ 命中主题：" + "、".join(f"{n}(+{v}，主题{t}%)" for n, v, t in hit)
        miss = [attr_name(k) for k in theme if prov.get(k, 0) == 0]
        if miss:
            txt += " ｜ 没补上：" + "、".join(miss)
    return txt


if __name__ == "__main__":  # python -m backend.cat_dress
    build()
