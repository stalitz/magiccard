"""猫咪旅行自动循环（2026-09-20 HAR 抓包实证 + 静态配置对齐）。

实证的动作（全部来自用户真实抓包，code=0）：
- act=5   出发旅行：elf_id, food_id, food_num, amulet_vt, lucky_stone
           → 响应 free_cnt-1，travel_ts/end_ts 推进约 +3600s
- act=23  领取某只猫的旅行礼物（召回/收获）：elf_id
           → 响应 travel_gift；main 里该猫 travel_ts/end_ts 归零
- act=4   收取成熟三叶草（被动）：无参 → clover_num
- act=10  商店购买：buy_type(=1 食物), id(=food_id), buy_num
           → HAR 实证 id=4（夹心饼干，A 级食物）购买成功
- act=12  **拉取「已开通猫咪旅行」的好友列表 + 各自三叶草状态（只读）**：uins(全量好友 | 分隔), uin(自己)
           → uin_tag_arr 只含**开通该玩法**的好友（服务端从全量里筛，实测 194 好友 → 17 开通）。
           每条 `{"uin","tag","data"}`，其中 `data` 是三叶草状态串：
             - `data=None`            → 该好友**没种三叶草** → 不能浇
             - `data="<成熟ts>_0"`     → 已种三叶草，ts=成熟时间；`ts>now` = **未成熟**（可浇）
           ⚠️ 这是**只读查询**，不浇水。返回的 uin 在请求列表里位置分散（1,3,16,52,…,193）
           → 确认是筛选而非截断。
- act=21  给指定好友浇水（单个，**唯一的浇水动作**）：opuin
           → 响应 `{"code":0,"content":"92_9_117","desc":"sucess"}`；HAR 实证浇水后自己的
           `travel_watered_arr` 新增 `{uin, ts=浇水时刻}` → 该字段就是「我今日浇过谁」。
           ⚠️ 规则（用户 2026-09-20 明确 + HAR 佐证）：
             ① 只有「已种三叶草且**未成熟**」的好友能浇；
             ② 每个好友每天只能浇一次（由 travel_watered_arr 去重）；
             ③ 每个好友每天**被浇次数有上限**（main?opuin=<uin> 的 can_water_times，
                实测好友 327904123 浇水前 =6，自己 =1）；浇满后服务端会拒。
- main?opuin=<uin>  **看好友主页（只读）**：card_travel_elf_main + opuin → 返回**该好友**的状态
           （他的 free_cnt/clover_num/can_water_times/watered_times 等）。
           注：main 不带 opuin = 自己；带 opuin = 好友。两处都是只读。
- act=32  拉取每日任务列表：无参 → task_detail[{cnt,index,type}]
- act=33  领取每日任务奖励：id(=task index 1..8)
- act=3   出发前配置（食物/装备列表）：无参 → travel_travel_food_arr[{food_id,num,ts}]

端点（HAR 坐实）：
- card_travel_elf_main = 状态/配置（POST 无参 → 完整 JSON，只读）
- card_travel_elf      = 动作（POST act=...）

生命周期（HAR 实证）：
- travel_end_ts>0 且 now<end_ts  → 旅行中，等
- travel_end_ts>0 且 now>=end_ts → 已返回，需收获(act=23)
- travel_end_ts==0               → 空闲，可出发（今日免费次数还有剩则 act=5）

A 级食物（quality=3，实证来源 travel_petShop type=91 食物商店名字 ↔ tool_food 等级）：
  food_id 4=夹心饼干, 5=饭团, 6=汉堡, 8=回忆便当  （正好 4 种，对应 4 只猫各一种）
  等级解析：food 商店 itemid ↔ travel_tool.name ↔ tool_food[position=travel_tool.id].food_quality

铁律：只发已实证动作；dry_run 只算不发，便于先验证逻辑再放手。
"""
import asyncio
import json
import os
import re
import time
import urllib.request

from .constants import EP_TRAVEL_ELF, EP_TRAVEL_ELF_MAIN, EP_USER_LIST

# 每只猫默认投喂数量（faithful to HAR：elf1 food_num=4）
DEFAULT_FOOD_NUM = 4
# 同时旅行上限：用户确认 4 只猫可同时旅行 → 上限=4（此前误判的 3 是连续出发限流，非并发上限）
MAX_CONCURRENT = 4

# 好友 uins 缓存（来自用户 HAR 真实好友列表，用于 act=12 批量浇水）
_FRIEND_UINS_PATH = os.path.join(os.path.dirname(__file__), "travel_friend_uins.json")

# 静态配置（travel_Info XML）URL，公开免登录
_TRAVEL_INFO_URL = "http://appimg2.qq.com/card/mk/travel_Info_v_461.xml"

# 回退用：A 级食物（quality=3）food_id（实证）
_FALLBACK_A_GRADE = [4, 5, 6, 8]

# 每日任务完成阈值（来自 travel_day_task XML task_list.task_limit，实证）
# index: limit  —— cnt>=limit 才可领取
DAILY_TASK_LIMIT = {1: 1, 2: 3, 3: 1, 4: 3, 5: 1, 6: 3, 7: 3, 8: 1}

# ⚠️ **免费旅行次数口径（2026-09-20 定案，三层证据，别再用 8）**：
#   1) `free_cnt` / `day_limit_cnt` = **今日已用的免费旅行次数**（不是剩余！）
#      HAR 逐帧：自己 main 14:02:20 free_cnt=0 → 出发 elf1 后 =1 → 出发 elf2 后 =2
#      （出发前是 0 还能连发两只 → 若是剩余，0 就该发不出去）。
#   2) 每只猫另有 `travel_elf_arr[].travel_limit_cnt` = **该猫今日已旅行次数**，**每猫上限 3**。
#   3) 上限 = **猫数 × 3**：HAR #45 好友 327904123 的 main 里四只猫 travel_limit_cnt 全 = 3，
#      同一帧 free_cnt = day_limit_cnt = **12** = 4 × 3 → 12 就是满额。
#   → free_limit = 猫数 × 3（4 猫 = 12）；剩余 = Σ max(0, 3 - 该猫已旅行次数)。
#   用户界面 17:00 显示"还剩 5 次"，与 4 猫已用 2/2/2/1 → 剩 1+1+1+2 = 5 完全吻合。
#   ⚠️ 静态配置 `travel_house.travel_cnt_limit = 8`（1..120 级恒 8）**不是**这个上限，语义另有所指，
#      早期误当成"每日免费 8 次"是错的（用户实测 12）。
PER_CAT_TRAVEL_LIMIT = 3
_FALLBACK_CAT_COUNT = 4

# 模块级缓存
_cached_a_grade = None
_cached_food_quality = None
_cached_food_names = None


# ───────────────────────── 节流 + 限流重试 ─────────────────────────
# 实证（2026-09-20）：连续快速请求会被服务端限流，返回 code=-3 "system busy"。
# 故所有 card_travel_elf 动作统一走 _act：请求间最小间隔 + busy 退避重试。
# 2026-09-29 依用户实测抓包下调：游戏客户端自身连发间隔常态 0.1~0.5s（bisai2 117 请求
# min gap=0.00s、大量 0.15~0.5s 均成功），1.2s 过保守致首屏 3 请求串行 ~4s。
# 取 0.4s（常态区间内）；仍触发限流时由 _act 的 busy 退避重试兜底。
_MIN_INTERVAL = 0.4
_last_req_ts = 0.0


async def _throttle():
    global _last_req_ts
    now = time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_req_ts)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_req_ts = time.monotonic()


async def _act(session, retries: int = 4, **params) -> dict:
    """发 card_travel_elf 动作：节流 + 对 system busy 退避重试。"""
    last = None
    for i in range(retries):
        await _throttle()
        r = await session.post(EP_TRAVEL_ELF, **params)
        if r.get("code") == 0:
            return r
        last = r
        msg = (r.get("message") or r.get("desc") or "").lower()
        if r.get("code") == -3 or "busy" in msg:
            await asyncio.sleep(1.5 * (i + 1))
            continue
        return r  # 非限流错误，不重试
    return last


def _resp_code(r: dict) -> int:
    """从 parse_response 结果里取业务 code。

    ⚠️ 不能写 `int(d.get("code", r.get("code", -1)) or -1)` —— `0 or -1` 会短路成 -1，
    把 `code=0 sucess` 的**成功**响应误判为失败（2026-09-20 实际踩到：浇水成功却报失败）。
    """
    d = r.get("data") if isinstance(r.get("data"), dict) else None
    for src in (d, r):
        if isinstance(src, dict) and src.get("code") is not None:
            try:
                return int(src["code"])
            except (TypeError, ValueError):
                continue
    return -1


# ───────────────────────── 读取类 ─────────────────────────
async def get_state(session) -> dict | None:
    """读自己的状态；失败返回 None。parse_response 把 JSON 主体放在 r["data"]。"""
    await _throttle()
    r = await session.post(EP_TRAVEL_ELF_MAIN)
    if r.get("code") != 0:
        return None
    return r.get("data") or {}


async def get_friend_state(session, uin) -> dict | None:
    """看好友主页（main + opuin，只读）：返回**该好友**的状态。

    实证（HAR #45）：`card_travel_elf_main` 带 `opuin=327904123` 返回的是**他的**数据
    （他的 clover_num=1776340、free_cnt=12、can_water_times=6、watered_times=3），
    而不带 opuin 时返回自己的。用于浇水前判断好友还剩几次可被浇（`can_water_times`）。
    """
    await _throttle()
    r = await session.post(EP_TRAVEL_ELF_MAIN, opuin=str(uin))
    if r.get("code") != 0:
        return None
    return r.get("data") or {}


async def get_food_config(session) -> dict:
    """拉食物持有清单（act=3）。返回 {food_id: num}。失败返回 {}。"""
    r = await _act(session, act=3)
    d = r.get("data") or r
    arr = d.get("travel_travel_food_arr") or []
    return {int(x["food_id"]): int(x.get("num") or 0) for x in arr if "food_id" in x}


async def get_daily_tasks(session) -> dict:
    """拉每日任务（act=32）。

    返回 {"tasks": task_detail, "bits": task_bits}。
    ⚠️ 实证：`task_bits` 是**已领取掩码**（bit i 置位 = 第 i 个任务已领），
    而非"已完成"。故判定可领 = `cnt>=limit 且 bit 未置位`；重复领返回 system busy。
    """
    r = await _act(session, act=32)
    d = r.get("data") or r
    return {"tasks": d.get("task_detail") or [], "bits": int(d.get("task_bits") or 0)}


# ───────────────────────── 静态配置解析 ─────────────────────────
def _fetch_travel_info():
    """同步拉取 travel_Info XML（公开免登录），返回根元素或 None。"""
    try:
        with urllib.request.urlopen(_TRAVEL_INFO_URL, timeout=15) as resp:
            data = resp.read()
        import xml.etree.ElementTree as ET
        return ET.fromstring(data)
    except Exception:
        return None


def resolve_food_quality() -> dict:
    """返回 {food_id: quality(str)}，覆盖食物商店 8 种食物。

    映射链：travel_petShop type=91(itemid→name) ↔ travel_tool(name→id,tool_type=2)
            ↔ tool_food[position=id].food_quality。失败回退到硬编码 A 级集合反推。
    """
    global _cached_food_quality
    if _cached_food_quality is not None:
        return _cached_food_quality
    root = _fetch_travel_info()
    quality: dict = {}
    if root is not None:
        try:
            # 1) 食物商店：itemid → name
            shop = {}
            for el in root.iter("travel_petShop"):
                if el.attrib.get("type") == "91":
                    for it in el.iter("item"):
                        shop[int(it.attrib["itemid"])] = it.attrib.get("name", "")
            # 2) travel_tool：name → id（仅食物 tool_type=2）
            name2id = {}
            for el in root.iter("travel_tool"):
                if el.attrib.get("tool_type") == "2":
                    name2id[el.attrib.get("name", "")] = int(el.attrib["id"])
            # 3) tool_food：position(=id) → quality
            foods = list(root.iter("tool_food"))
            pos2q = {}
            for i, f in enumerate(foods, start=1):
                pos2q[i] = f.attrib.get("food_quality")
            # 4) 组装
            for itemid, name in shop.items():
                tid = name2id.get(name)
                if tid and tid in pos2q:
                    quality[itemid] = pos2q[tid]
        except Exception:
            quality = {}
    if not quality:
        # 回退：仅标记 A 级集合
        quality = {fid: "3" for fid in _FALLBACK_A_GRADE}
    _cached_food_quality = quality
    return quality


def resolve_food_names() -> dict:
    """返回 {food_id: name}（来自 travel_petShop type=91 食物商店）。失败回退硬编码。"""
    global _cached_food_names
    if _cached_food_names is not None:
        return _cached_food_names
    root = _fetch_travel_info()
    names = {}
    if root is not None:
        try:
            for el in root.iter("travel_petShop"):
                if el.attrib.get("type") == "91":
                    for it in el.iter("item"):
                        names[int(it.attrib["itemid"])] = it.attrib.get("name", "")
        except Exception:
            names = {}
    if not names:
        names = {1: "曲奇饼", 2: "寿司", 3: "牛排", 4: "夹心饼干",
                 5: "饭团", 6: "汉堡", 7: "回忆棒棒糖", 8: "回忆便当"}
    _cached_food_names = names
    return names


def resolve_a_grade_foods() -> list:
    """返回 A 级（quality==3）食物 food_id 列表（应为 4 种）。"""
    global _cached_a_grade
    if _cached_a_grade is not None:
        return _cached_a_grade
    q = resolve_food_quality()
    a = sorted(fid for fid, qual in q.items() if qual == "3")
    if not a:
        a = list(_FALLBACK_A_GRADE)
    _cached_a_grade = a
    return a


def compute_free_counts(state: dict) -> dict:
    """把主状态折算成「免费旅行次数」三件套（**唯一判定入口**，UI 与出发循环共用）。

    模型（三层证据见文件头注释）：每猫每日 3 次 → 总额 = 猫数 × 3 = 12。

    返回 `{"free_used", "free_limit", "free_left", "cats", "per_cat"}`：
    - `free_used`  = main.free_cnt（== Σ per_cat，两者实证一致）
    - `free_limit` = 猫数 × 3
    - `free_left`  = Σ max(0, 3 - 该猫已旅行次数)（比 free_limit-free_used 更精确：
      某只猫单独到顶时也能正确反映）
    """
    state = state or {}
    elves = state.get("travel_elf_arr") or []
    per_cat = {}
    used_sum = 0
    left_sum = 0
    for c in elves:
        eid = c.get("elf_id")
        n = int(c.get("travel_limit_cnt") or 0)
        per_cat[eid] = n
        used_sum += n
        left_sum += max(0, PER_CAT_TRAVEL_LIMIT - n)
    cats = len(elves) or _FALLBACK_CAT_COUNT
    free_limit = cats * PER_CAT_TRAVEL_LIMIT
    free_used = int(state.get("free_cnt") or 0)
    if free_used <= 0 and used_sum > 0:
        free_used = used_sum
    if free_used > free_limit:          # 服务端已用超过按猫数算的上限 → 以实际为准
        free_limit = free_used
    free_left = left_sum if elves else max(0, free_limit - free_used)
    if free_used + free_left > free_limit:   # 自愈：两者矛盾时以已用为准
        free_left = max(0, free_limit - free_used)
    return {"free_used": free_used, "free_limit": free_limit, "free_left": free_left,
            "cats": cats, "per_cat": per_cat, "per_cat_limit": PER_CAT_TRAVEL_LIMIT}


def resolve_free_daily_limit(cats: int | None = None) -> int:
    """每日免费旅行次数上限 = 猫数 × 每猫 3 次。

    ⚠️ 曾经的"读 `travel_house.travel_cnt_limit`(=8)"是**错的**（用户实测界面为 12）。
    该配置项语义另有所指；真上限来自服务端行为（好友满额帧 free_cnt=12、四猫各 3）。
    """
    return (int(cats) if cats else _FALLBACK_CAT_COUNT) * PER_CAT_TRAVEL_LIMIT


def parse_clover_data(data) -> tuple | None:
    """解析 act=12 的 `data`（好友三叶草状态串）。

    `"1789927248_0"` → `(成熟时间ts, flag)`；`None`/空 → None（该好友没种三叶草，不能浇）。
    """
    if not data:
        return None
    try:
        head, _, tail = str(data).partition("_")
        return int(head), (int(tail) if tail else 0)
    except (TypeError, ValueError):
        return None


def _load_cached_uins() -> list:
    """HAR 提取的好友缓存（回退用）。"""
    try:
        return json.load(open(_FRIEND_UINS_PATH))
    except Exception:
        return []


def _self_uin(session) -> str:
    """自己的 uin（规范化成纯数字）。

    ⚠️ `LoginSession.uin` 是补零格式 `'0004821992'`，而 act=12 的 `uin` 参数与
    `uin_tag_arr` 里的 uin 都是**不补零**的 `'4821992'` → 必须归一化，否则
    「自己」会被当成好友混进浇水目标（实测踩到过）。
    """
    u = getattr(session, "uin", None)
    try:
        return str(int(u))
    except (TypeError, ValueError):
        return str(u or "")


async def load_friend_uins(session=None) -> list:
    """全量好友 uin 列表。

    优先 `card_user_list`（动态、最新，顺序=游戏界面顺序，本账号 194 人），
    失败时回退 HAR 缓存文件。全量好友只作 **act=12 的输入**，真正参与猫咪旅行
    互动的是 act=12 返回的「已开通」子集（见 fetch_open_friends）。
    """
    if session is not None:
        try:
            r = await session.post(EP_USER_LIST)
            lst = re.findall(r'<user\s+uin="(\d+)"', r.get("raw", ""))
            if lst:
                return lst
        except Exception:
            pass
    return _load_cached_uins()


async def fetch_open_friends(session) -> dict:
    """拉取「已开通猫咪旅行玩法」的好友列表（act=12，只读）。

    返回 {"ok", "total", "friends": [{"uin", "tag", "data"}]}。
    `data` 形如 "1789927248_0"，是服务端给的状态串（语义未完全确认，原样保留）。
    """
    friends = await load_friend_uins(session)
    if not friends:
        return {"ok": False, "message": "无好友列表", "total": 0, "friends": []}
    uin = _self_uin(session)
    r = await _act(session, act=12, uins="|".join(str(u) for u in friends), uin=uin)
    d = r.get("data") or {}
    arr = d.get("uin_tag_arr") or []
    rows = []
    for x in arr:
        u = str(x.get("uin") or "")
        if not u:
            continue
        rows.append({"uin": u, "tag": x.get("tag"), "data": x.get("data"),
                     "is_self": bool(uin) and u == uin})
    code = _resp_code(r)
    return {"ok": code == 0 or bool(rows), "code": code,
            "total": len(friends), "friends": rows,
            # ⚠️ 服务端把**自己**也放进 uin_tag_arr（实测 17 里有 1 个是自己的 uin），
            # 浇水目标必须排除自己 → friend_uins 只含真·好友。
            "friend_uins": [r_["uin"] for r_ in rows if not r_["is_self"]]}


# ───────────────────────── 动作类 ─────────────────────────
async def depart(session, elf_id, food_id, food_num=DEFAULT_FOOD_NUM,
                 amulet_vt=0, lucky_stone=0) -> dict:
    """出发旅行（act=5）。"""
    return await _act(session, act=5, elf_id=elf_id, food_id=food_id,
                      food_num=food_num, amulet_vt=amulet_vt, lucky_stone=lucky_stone)


async def harvest(session, elf_id) -> dict:
    """领取某猫旅行礼物 / 召回（act=23）。"""
    return await _act(session, act=23, elf_id=elf_id)


async def buy_food(session, food_id, num) -> dict:
    """商店购买食物（act=10, buy_type=1）。

    ⚠️ 实证（2026-09-20 实跑）：act=10 的 `id` 是 **0-based** —— 传 id=6 实际买到
    itemid 7（回忆棒棒糖），返回 content="91_7_1"，扣三叶草 360（=回忆棒棒糖定价）。
    故 `id = food_id - 1`。食物用**三叶草**购买（免费货币，非付费）。
    """
    return await _act(session, act=10, buy_type=1, id=int(food_id) - 1, buy_num=num)


async def harvest_clover(session) -> dict:
    """收取三叶草（act=4）。"""
    return await _act(session, act=4)


async def water_one(session, uin) -> dict:
    """给单个好友浇水（act=21, opuin）—— **唯一的浇水动作**（HAR 实证成功）。

    ⚠️ 这是**写**动作；只发一次、不做重试（重试会重复消耗好友的可被浇次数）。
    HAR 实证：`act=21 opuin=327904123` → `{"code":0,"content":"92_9_117","desc":"sucess"}`，
    且自己 main 的 `travel_watered_arr` 立刻新增该好友（ts=浇水时刻）。
    """
    await _throttle()
    return await session.post(EP_TRAVEL_ELF, act=21, opuin=uin)


async def harvest_returned(session, elf_id=None) -> list[dict]:
    """手动收获旅行：领取已返回猫的旅行礼物（act=23）。

    elf_id 为空 = 收获**所有已返回**的猫；指定则只收那只。
    ⚠️ 只对 `travel_end_ts>0 && now>=end`（已返回）的猫发动作；旅行中的猫会被跳过
    （对未返回的猫发 act=23 无意义）。
    """
    out: list[dict] = []
    st = await get_state(session)
    if st is None:
        return [{"ok": False, "act": "harvest", "text": "读取状态失败，未收获"}]
    now = int(time.time())
    for c in st.get("travel_elf_arr") or []:
        eid = c.get("elf_id")
        if elf_id is not None and int(eid) != int(elf_id):
            continue
        end = int(c.get("travel_end_ts") or 0)
        if end <= 0:
            out.append({"ok": True, "act": "harvest", "elf_id": eid, "code": 0,
                        "text": f"猫{eid} 空闲中（未出发），无需收获"})
            continue
        if now < end:
            out.append({"ok": True, "act": "harvest", "elf_id": eid, "code": 0,
                        "text": f"猫{eid} 还在旅行中，剩 {end - now}s，暂不收获"})
            continue
        r = await harvest(session, eid)
        code = _resp_code(r)
        out.append({"ok": code == 0, "act": "harvest", "elf_id": eid, "code": code,
                    "gift": (r.get("data") or {}).get("travel_gift") if isinstance(r.get("data"), dict) else None,
                    "text": f"猫{eid} 收获：{'成功' if code == 0 else (r.get('message') or code)}"})
    if elf_id is not None and not out:
        out.append({"ok": False, "act": "harvest", "text": f"未找到猫{elf_id}"})
    if elf_id is None and not any(i.get("act") == "harvest" and "收获" in i.get("text", "") for i in out):
        out.append({"ok": True, "act": "harvest", "text": "没有已返回待收获的猫"})
    return out


async def collect_water_targets(session, st: dict | None = None) -> dict:
    """算出**可浇水**的好友名单（严格按规则，不靠猜）。

    规则（用户明确 + HAR 实证）：
      ① 只取 act=12 返回的**已开通猫咪旅行**的好友（自动排除自己——服务端会把自己也放进来）；
      ② `data` 非空（= 该好友**种了三叶草**）且 `ts > now`（**未成熟**）才可浇；
      ③ 自己 `travel_watered_arr` 里出现过的 = 今日已浇 → 跳过（**每个好友每天只浇一次**）。

    `st`：调用方若已拉过自己的 main 状态（travel_watered_arr 在里面）可直接传入，
    避免重复发一次 main 请求（state 路由 2026-09-29 优化：5 请求 → 4）。

    返回 {"ok","opened","watered_today","targets":[uin...],"all":[{uin,status,reason,...}]}。
    """
    if st is None:
        st = await get_state(session) or {}
    done = {str(x.get("uin")) for x in (st.get("travel_watered_arr") or [])}
    op = await fetch_open_friends(session)
    now = int(time.time())
    rows, targets = [], []
    for f in op.get("friends") or []:
        uin = str(f.get("uin"))
        clover = parse_clover_data(f.get("data"))
        if f.get("is_self"):
            reason, status = "自己", "self"
        elif uin in done:
            reason, status = "今日已浇过", "watered"
        elif clover is None:
            reason, status = "未种三叶草（不能浇）", "no_clover"
        elif clover[0] <= now:
            reason, status = "三叶草已成熟（不能浇）", "ripe"
        else:
            reason, status = "", "waterable"
            targets.append(uin)
        rows.append({"uin": uin, "is_self": bool(f.get("is_self")), "tag": f.get("tag"),
                     "data": f.get("data"), "status": status, "reason": reason,
                     "ripe_ts": clover[0] if clover else None,
                     "watered": uin in done})
    return {"ok": bool(op.get("ok")), "opened": len(rows), "watered_today": len(done),
            "open_uins": [r["uin"] for r in rows if not r["is_self"]],
            "targets": targets, "all": rows}


async def water_targets(session, uins, check_quota: bool = True,
                        max_fail: int = 3) -> dict:
    """给一批**已筛过的**好友逐个浇水（act=21，唯一的浇水动作）。

    check_quota: 浇水前先读好友主页（main?opuin）看他的 `can_water_times`
                 （= 他今天还能被浇几次）；为 0 则跳过（**每人每天被浇有上限**）。
    连续 max_fail 次失败即收手（多为当日浇水次数已尽/冷却），避免空刷。
    """
    uins = [str(u) for u in (uins or [])]
    results: list[dict] = []
    ok_cnt, fail, stopped = 0, 0, False
    for u in uins:
        if check_quota:
            fs = await get_friend_state(session, u)
            cwt = None if fs is None else fs.get("can_water_times")
            if cwt is not None and int(cwt) <= 0:
                results.append({"uin": u, "code": None, "skipped": True,
                                "reason": "好友今日已被浇满（can_water_times=0）"})
                continue
        else:
            cwt = None
        r = await water_one(session, u)
        code = _resp_code(r)
        item = {"uin": u, "code": code, "can_water_times": cwt,
                "reward": (r.get("data") or {}).get("content") if isinstance(r.get("data"), dict) else None,
                "message": (r.get("data") or {}).get("desc") if isinstance(r.get("data"), dict)
                           else r.get("message") or ""}
        results.append(item)
        if code == 0:
            ok_cnt += 1
            fail = 0
        else:
            fail += 1
            if ok_cnt == 0 and fail >= max_fail:
                stopped = True
                break
    return {"ok": ok_cnt > 0, "tried": sum(1 for x in results if not x.get("skipped")),
            "ok_cnt": ok_cnt, "skipped": sum(1 for x in results if x.get("skipped")),
            "fail": sum(1 for x in results if not x.get("skipped") and x.get("code") != 0),
            "stopped": stopped, "results": results}


async def claim_daily_task(session, task_id) -> dict:
    """领取每日任务奖励（act=33, id=task index）。"""
    return await _act(session, act=33, id=task_id)


# ───────────────────────── 编排 ─────────────────────────
async def run_once(session, food_num: int = DEFAULT_FOOD_NUM,
                   harvest_only: bool = False, dry_run: bool = False,
                   do_clover: bool = True, do_water: bool = True,
                   do_tasks: bool = True, a_food_ids: list | None = None,
                   water_uins: list | None = None) -> list[dict]:
    """跑一轮自动旅行。

    顺序：收获已返回的猫 → 用今日剩余免费次数出发空闲猫(各喂一种 A 级食物,不够则购买)
          → 收三叶草 → 给「已种未成熟三叶草且今日未浇」的好友浇水 → 领取可领的每日任务。

    ⚠️ 免费次数口径：main.free_cnt = **今日已用**次数，上限 = **猫数 × 3**（4 猫 = 12，
       每猫每日 3 次）；剩余 = Σ max(0, 3 − 该猫已旅行次数)。本轮出发到用尽为止。
    dry_run 只算不发（仍读状态/食物配置）。
    """
    out: list[dict] = []
    st = await get_state(session)
    if st is None:
        return [{"ok": False, "act": "read", "text": "读取猫咪旅行状态失败"}]

    now = int(time.time())
    elves = st.get("travel_elf_arr") or []
    # ⚠️ free_cnt = 今日**已用**免费次数；上限 = 猫数 × 3（4 猫 = 12）；另受每猫 3 次约束。
    fc = compute_free_counts(st)
    free_limit, free_used = fc["free_limit"], fc["free_used"]
    free_left = fc["free_left"]
    per_cat_limit = fc["per_cat_limit"]

    if a_food_ids is None:
        a_food_ids = resolve_a_grade_foods()

    # 1) 收获所有已返回的猫
    for c in elves:
        eid = c.get("elf_id")
        end = int(c.get("travel_end_ts") or 0)
        if end > 0 and now >= end:
            if dry_run:
                out.append({"ok": True, "dry": True, "act": "harvest", "elf_id": eid,
                            "text": f"猫{eid} 已返回，拟收获(act=23)"})
                continue
            r = await harvest(session, eid)
            code = _resp_code(r)
            out.append({"ok": code == 0, "act": "harvest", "elf_id": eid, "code": code,
                        "text": f"猫{eid} 收获：{'成功' if code == 0 else (r.get('message') or code)}"})

    # 2) 出发空闲猫（各喂一种不同的 A 级食物；道具不够先买；用尽今日免费次数为止）
    if not harvest_only:
        # 重读状态让刚收获的猫进入可出发集合
        st2 = st if dry_run else (await get_state(session) or st)
        fc2 = compute_free_counts(st2)
        free_limit, free_used, free_left = fc2["free_limit"], fc2["free_used"], fc2["free_left"]
        per_cat = fc2["per_cat"]
        owned = await get_food_config(session)  # act=3 只读，dry_run 也拉以便算购买量
        idle = [c for c in (st2 or st).get("travel_elf_arr") or []
                if int(c.get("travel_end_ts") or 0) == 0]
        used_foods = set()
        for c in idle:
            if free_left <= 0:
                out.append({"ok": True, "act": "depart",
                            "text": f"今日免费次数已用完（已用 {free_used}/{free_limit}，剩 0），停止出发"})
                break
            eid = c.get("elf_id")
            # 每猫每日上限 3 次：这只猫自己到顶就跳过（服务端会返 system busy，不必硬撞）
            if int(per_cat.get(eid) or 0) >= per_cat_limit:
                out.append({"ok": True, "act": "depart", "elf_id": eid,
                            "text": f"猫{eid} 今日已旅行 {per_cat.get(eid)}/{per_cat_limit} 次（该猫到顶），跳过"})
                continue
            # 选一种尚未使用的 A 级食物（保证 4 猫各不同）
            food = None
            for f in a_food_ids:
                if f not in used_foods:
                    food = f
                    break
            if food is None:
                food = a_food_ids[0]  # 理论上不会（A 级数>=猫数）
            used_foods.add(food)
            have = int(owned.get(food) or 0)
            need_buy = 0 if dry_run else max(0, food_num - have)
            if dry_run:
                out.append({"ok": True, "dry": True, "act": "depart", "elf_id": eid,
                            "text": f"猫{eid} 拟出发：A级食物 food_id={food}（持有{have}，需{food_num}）"
                                    f"｜今日免费 {(free_used + 1)}/{free_limit}"})
                free_used += 1
                free_left -= 1
                per_cat[eid] = int(per_cat.get(eid) or 0) + 1
                continue
            # 道具不够 → 商店购买
            if need_buy > 0:
                rb = await buy_food(session, food, need_buy)
                cb = _resp_code(rb)
                out.append({"ok": cb == 0, "act": "buy", "elf_id": eid, "food_id": food,
                            "code": cb,
                            "text": f"猫{eid} 食物{food}不足，购买{need_buy}个：{rb.get('message') or cb}"})
                if cb != 0:
                    # 购买失败则不出发这只猫（避免带不够食物硬发）
                    continue
            # 出发（限流由 _act 统一节流 + 退避重试）
            r = await depart(session, eid, food, food_num)
            code = _resp_code(r)
            ok = code == 0
            if ok:
                # 出发响应里的 free_cnt 同样是"已用次数"
                free_used = int((r.get("data") or {}).get("free_cnt") or (free_used + 1))
                free_left = max(0, free_limit - free_used)
                per_cat[eid] = int(per_cat.get(eid) or 0) + 1
                out.append({"ok": True, "act": "depart", "elf_id": eid, "code": code,
                            "text": f"猫{eid} 出发(A级食物{food})：成功"
                                    f"（今日免费 {free_used}/{free_limit}，剩 {free_left}）"})
            else:
                out.append({"ok": False, "act": "depart", "elf_id": eid, "code": code,
                            "text": f"猫{eid} 出发失败：{r.get('message') or code}"})

    # 3) 收三叶草（act=4）；无可收时服务端返 system busy，按"暂无可收"软处理
    if do_clover:
        if dry_run:
            out.append({"ok": True, "dry": True, "act": "clover", "text": "拟收取三叶草(act=4)"})
        else:
            rc = await harvest_clover(session)
            c0 = rc.get("code") == 0
            busy = "busy" in (rc.get("message") or "").lower()
            out.append({"ok": c0, "act": "clover", "code": rc.get("code"),
                        "text": (f"收三叶草：成功（现 {rc.get('clover_num')}）" if c0
                                 else ("收三叶草：暂无可收（无成熟三叶草）" if busy
                                       else f"收三叶草失败：{rc.get('message') or rc.get('code')}"))})

    # 4) 去好友家浇水：act=12 拉「已开通好友 + 三叶草状态」→ 筛「种了且未成熟」→ 逐个 act=21
    #    规则：只浇未成熟三叶草；每个好友每天只浇一次；好友可被浇次数满了就跳过。
    if do_water:
        col = await collect_water_targets(session)
        targets = [str(u) for u in water_uins] if water_uins is not None else list(col.get("targets") or [])
        if dry_run:
            out.append({"ok": True, "dry": True, "act": "water",
                        "text": f"拟给 {len(targets)} 个「已种三叶草且未成熟、今日未浇」的好友浇水"
                                f"（已开通 {col.get('opened')} 个，今日已浇 {col.get('watered_today')} 个）"})
        elif not targets:
            out.append({"ok": True, "act": "water",
                        "text": f"浇水：无可浇好友（已开通 {col.get('opened')} 个，"
                                f"今日已浇 {col.get('watered_today')} 个，其余未种三叶草/已成熟）"})
        else:
            rw = await water_targets(session, targets)
            tail = "（连续被拒，提前收手）" if rw.get("stopped") else ""
            skiptail = f"，跳过 {rw.get('skipped')} 个（好友已被浇满）" if rw.get("skipped") else ""
            out.append({"ok": (rw.get("ok_cnt") or 0) > 0, "act": "water",
                        "code": 0 if rw.get("ok_cnt") else -1,
                        "text": f"浇水：目标 {len(targets)} 个 → 成功 {rw.get('ok_cnt')}、"
                                f"失败 {rw.get('fail')}{skiptail}{tail}"})
            for x in rw.get("results") or []:
                if x.get("skipped"):
                    out.append({"ok": True, "act": "water", "uin": x["uin"],
                                "text": f"  · 好友{x['uin']}：跳过（{x.get('reason')}）"})
                    continue
                cwt = x.get("can_water_times")
                quota = f"（他可被浇余 {cwt} 次）" if cwt is not None else ""
                out.append({"ok": x.get("code") == 0, "act": "water", "uin": x["uin"],
                            "code": x.get("code"),
                            "text": f"  · 好友{x['uin']}："
                                    f"{'浇水成功' if x.get('code') == 0 else '浇水失败'}{quota}"})

    # 5) 领取可领的每日任务（cnt>=limit 且 task_bits 未置位=未领过；付费项达不到阈值自然不领）
    if do_tasks:
        if dry_run:
            out.append({"ok": True, "dry": True, "act": "tasks", "text": "拟领取可领的每日任务(act=33)"})
        else:
            info = await get_daily_tasks(session)
            bits = int(info.get("bits") or 0)
            pending = []
            for t in info.get("tasks") or []:
                idx = int(t.get("index") or 0)
                cnt = int(t.get("cnt") or 0)
                lim = DAILY_TASK_LIMIT.get(idx, 0)
                claimed = bool(bits & (1 << idx))   # 已领过 → 跳过，避免 system busy
                if lim and cnt >= lim and not claimed:
                    pending.append(idx)
            if not pending:
                out.append({"ok": True, "act": "task", "text": "每日任务：当前无新完成可领（已领/未达标）"})
            for idx in pending:
                rt = await claim_daily_task(session, idx)
                out.append({"ok": rt.get("code") == 0, "act": "task", "task_id": idx,
                            "code": rt.get("code"),
                            "text": f"领每日任务#{idx}：{'成功' if rt.get('code')==0 else (rt.get('message') or rt.get('code'))}"})

    if not out:
        out.append({"ok": True, "act": "none",
                    "text": f"无需操作（今日免费 已用{free_used}/{free_limit} 剩{free_left}，"
                            f"{len(elves)} 只猫均旅行中/已处理）"})
    return out
