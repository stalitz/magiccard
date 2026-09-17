"""灵宠探险（card_user_spirite）自动循环（2026-09-14 新增）。

协议与真机实测结论（全部经真实调用验证）：
- act=1 查询：**opuin 必须传「去掉前导零」的 uin**（登录态是 0004821992，
  原样传会静默返回空数据 spirites=[]/section_id=0，曾据此误判「账号未参与」）；
  参数 uin 则无此要求。
- act=3 出发：**pass_id（大地图）+ section_id（该图内的小关）+ ids（精灵）** 三者缺一即 -2；
  explore_pass_id = pass_id * 100 + section_id（实测 北京颐和园 pass=6,section=8 → 608，
  服务端会按 section_id/pass_id 重算，故该字段传不传都行）；每次出发扣 1 次 free_explore_cnt
  ——⚠️ **free=0 时不会报错，会改扣 `permanent`（永久次数）**，所以探测绝不能用真实精灵。
- act=4 收获：结算返回 send:[{id,num,step,type}]，成功后 put_spirites=[] explore_ts=0；
  **探索不足约 5 分钟时返回 -1 系统繁忙**（实测 t=0/60s/120s 均失败，6.9 分钟成功）。
- 参数语义（2026-09-14 用户实证校正，此前注释写反了）：
  `pass_id` = **大地图**（1=文明古国 … 6=世界古遗迹 … 11=悬崖之巅，未开放）；
  `section_id` = **该大地图内的小关**。依据：用户在大地图 6 第 9 小关时，
  act=1 返回 section_id=9 / pass_id=6，两项各自吻合。
- 名字（大地图名/小关名）服务端不下发，来自客户端卡库，见 `spirite_maps.py`。
- 单次 act=3 只能派 1 只精灵（ids 用 '|' 分隔只取首值，重复参数只取末值）；
  客户端「一次派两只」的真实参数仍未找到（疑与 `hy_*` 槽位有关）。

自动策略：轮询（默认 30s）。有探险在跑 → 满 min_wait 就试着收；收到 → 若还有
免费次数就派下一只。全程只用 act=1/3/4，不触碰其它活动。
"""
import asyncio
import logging
import time

from . import prop_dict as pd
from . import spirite_maps as spm
from .constants import EP_SPIRITE
from .spirite_maps import MAP_IDS

log = logging.getLogger(__name__)

#: 大地图（pass_id）候选编号，取自 `spirite_maps.MAP_IDS`（1..11）。
#: 用户确认「最后 1 关是第 11 关 悬崖之巅」，故上界 11；权威定义在 spirite_maps.py。
#: 这里只用于前端下拉取值，**不做范围硬拦**（真值交服务端判定，组合无效返 -5）。
SPIRITE_MAPS = MAP_IDS
#: 收获最短探索时长（秒）。实测 5 分钟可收，更早返回 -1。
SPIRITE_MIN_WAIT = 300
#: 荒岛（c_island）探险时长（秒）。游戏内文案「探险所需时间：3 分」，早收返回 -2。
ISLAND_MIN_WAIT = 180
#: 穿越丛林（c_across）每天历练次数上限。游戏内文案「历练次数 9/10」
#: ＋「VIP玩家每天额外多5次」。act=1 只回已用次数（torrid_zone_explore_cnt），
#: 不给上限，故上限由这里给默认值，可由 opts 覆盖。
ACROSS_DAILY_MAX = 10
#: 荒岛（c_island）每天探险次数上限。游戏内普通 5 次/天（VIP 更多），
#: act=1 只回已用次数（today_hy_explore_cnt），上限由这里给默认值。
ISLAND_DAILY_MAX = 5


def plain_uin(session) -> str:
    """精灵接口的 opuin：必须去掉登录态 uin 的前导零。"""
    return str(int(str(session.uin)))


def fmt_reward(send) -> str:
    """把 act=4 / act=15 的 `send[]` 压成可读文本。

    名字查官方统一物品表（`prop_dict` → `item_dict`），**不再自带硬编码表**
    —— 旧表只有 `{56:碎片, 57:灵力果}`，会把 58 藏宝图/72 银锭/73 百变丹
    全显示成「道具N」，57 也不分档位。
    """
    return pd.summarize_all(send) or "无奖励"


class SpiriteAuto:
    """灵宠探险自动循环。同一时刻只允许一个循环。"""

    def __init__(self, session):
        self.s = session
        self.task: asyncio.Task | None = None
        self.running = False
        self._stop = False
        self.finished = False
        self.message = ""
        self.logs: list[dict] = []
        self.opts: dict = {}
        self.started_at = 0.0
        self.harvests = 0
        self.dispatches = 0
        self.rewards: list[dict] = []
        self.last_state: dict = {}
        self._rot = 0

    # ---------- 日志 ----------
    def _log(self, action: str, ok: bool = True, detail: str = "") -> None:
        self.logs.append({"ts": int(time.time()), "action": action,
                          "ok": ok, "detail": detail})
        del self.logs[:-200]
        log.info("[spirite-auto] %s %s %s", action, ok, detail)

    # ---------- 接口封装 ----------
    async def _post(self, act: int, **kw):
        kw.setdefault("opuin", plain_uin(self.s))
        return await self.s.post(EP_SPIRITE, act=act, **kw)

    async def _query(self) -> tuple[int, dict]:
        r = await self._post(1)
        return r.get("code"), (r.get("data") or {})

    def _pick_spirites(self, data: dict, n: int | None = None) -> list:
        """选若干只灵宠出发（n 默认 2；荒岛按该关 elf_num 需要 3~8 只）。
        指定了 spirite_ids 就只在其内轮换，否则全体轮换。
        轮换按 id 降序：优先用最后面（高 id）的灵宠，逐只往前（用户规则）。
        返回 id 列表，调用方需以逗号拼接成 ids='9,10' 再发（真机抓包确认的格式）。"""
        sps = list(data.get("spirites") or [])
        want = self.opts.get("spirite_ids") or []
        if want:
            picked = [x for x in sps if x.get("id") in want]
            sps = picked or sps
        if not sps:
            return []
        sps.sort(key=lambda x: -(x.get("id") or 0))
        need = int(n if n is not None else self.opts.get("spirite_num", 2) or 2)
        need = max(1, min(len(sps), need))
        self._rot += 1
        start = (self._rot - 1) % len(sps)
        return [int(sps[(start + i) % len(sps)].get("id") or 0) for i in range(need)]

    # ---------- 主循环 ----------
    def _snapshot(self, d: dict) -> None:
        """把 act=1 的关键状态存进 last_state（三套玩法的字段一次全存）。
        同时保留 explore/island/across 三层嵌套对象，前端 spRenderState 直接用它渲染，
        这样自动跑的过程中三行状态面板不会退化成「无进行中」。"""
        ex = d.get("explore") or {}
        # ⚠️ act=1 原始响应**不含** island/across 子对象（那是 /api/cat/travel
        # 额外 enrich 出来的）。这里按同样的口径补建，否则自动跑的过程中
        # 轮询渲染的 last_state.island/across = {} → 界面「进行中」会闪成「无」，
        # 每 15 秒又被 /api/cat/travel 覆盖回「有」，出现一闪一闪的假象。
        hy_id = ex.get("hy_explore_pass_id")
        island = {
            "seq": hy_id or 0,
            "place": spm.island_place_text(hy_id),
            "stage": spm.island_place(hy_id),
            "spirites": ex.get("hy_put_spirites") or [],
            "explore_ts": int(ex.get("hy_explore_ts") or 0),
            "today_cnt": d.get("today_hy_explore_cnt"),
        }
        across = {
            "used": d.get("torrid_zone_explore_cnt"),
            "ts": d.get("fri_explore_ts"),
        }
        self.last_state = {
            "mode": self.opts.get("mode") or "map",
            "free": int(d.get("free_explore_cnt") or 0),
            "section_id": d.get("section_id"),
            "pass_id": d.get("pass_id"),
            "power": d.get("power"),
            "spirites": d.get("spirites") or [],
            "explore": ex,
            "island": island,
            "across": across,
            "put_spirites": ex.get("put_spirites") or [],
            "explore_ts": int(ex.get("explore_ts") or 0),
            "explore_pass_id": ex.get("explore_pass_id"),
            # 荒岛（官方 c_island，act=14 出发 / act=15 收获）
            "island_seq": int(ex.get("hy_explore_pass_id") or 0),
            "island_ts": int(ex.get("hy_explore_ts") or 0),
            "island_spirites": ex.get("hy_put_spirites") or [],
            "today_hy": d.get("today_hy_explore_cnt"),
            # 穿越丛林（官方 c_across，act=11）。**计数器叫 torrid_zone**（实测 +1）
            "torrid_used": d.get("torrid_zone_explore_cnt"),
        }

    async def _loop(self) -> None:
        """按 opts['mode'] 分发到三套玩法各自的循环。"""
        try:
            mode = self.opts.get("mode") or "map"
            if mode == "island":
                await self._loop_island()
            elif mode == "across":
                await self._loop_across()
            else:
                await self._loop_map()
        except asyncio.CancelledError:
            self._log("已手动停止", True)
            raise
        except Exception as e:  # noqa: BLE001
            self._log("循环异常", False, str(e))
        finally:
            self.running = False
            self.finished = True
            self.message = f"完成：收获 {self.harvests} 次 / 出发 {self.dispatches} 次"

    async def _loop_map(self) -> None:
        """大地图：act=3 出发 / act=4 收获（探索 ≥5 分钟）。"""
        c = self.opts
        while not self._stop:
            code, d = await self._query()
            if code != 0:
                self._log("查询失败", False, f"code={code}")
                await asyncio.sleep(c["interval"])
                continue
            self._snapshot(d)
            ex = d.get("explore") or {}
            free = int(d.get("free_explore_cnt") or 0)
            ts = int(ex.get("explore_ts") or 0)

            if ts:
                elapsed = time.time() - ts
                if elapsed < c["min_wait"]:
                    self._log("探险进行中", True,
                              f"{elapsed / 60:.1f}/{c['min_wait'] / 60:.0f} 分钟")
                    await asyncio.sleep(c["interval"])
                    continue
                r = await self._post(4)
                if r.get("code") != 0:
                    self._hf += 1
                    if self._hf >= 12:
                        self._log("收获持续失败，停止空转", False,
                                  f"code={r.get('code')} 已尝试 {self._hf} 次")
                        break
                    self._log("收获未就绪", False,
                              f"code={r.get('code')} 已跑 {elapsed / 60:.1f} 分（第{self._hf}次）")
                    await asyncio.sleep(c["interval"])
                    continue
                self._hf = 0
                send = (r.get("data") or {}).get("send") or []
                self.harvests += 1
                self.rewards.append({"ts": int(time.time()), "send": send,
                                     "text": fmt_reward(send)})
                self._log("收获成功", True, fmt_reward(send))
                ts = 0

            # 当前无探险在跑
            if not c["auto_dispatch"]:
                self._log("已收获，未开自动派出，结束", True)
                break
            if free <= 0:
                self._log("免费次数已用尽，结束", True)
                break
            if c["max_rounds"] and self.dispatches >= c["max_rounds"]:
                self._log("已达派出轮数上限，结束", True)
                break

            sids = self._pick_spirites(d)
            if not sids:
                self._log("没有可用精灵，结束", False)
                break
            ids_str = ",".join(str(x) for x in sids)
            tag = f"地图 {c['section_id']} / 精灵 {ids_str} / 小关 {c['pass_id']}"
            if c["dry_run"]:
                self._log("模拟出发（未真发）", True, tag)
                break

            r = await self._post(3, section_id=c["section_id"],
                                 ids=ids_str, pass_id=c["pass_id"])
            if r.get("code") != 0:
                self._log("出发失败", False, f"code={r.get('code')} {tag}")
                break
            self.dispatches += 1
            self._log("出发", True, tag)
            await asyncio.sleep(c["interval"])

    async def _loop_island(self) -> None:
        """荒岛（官方 c_island）：act=14（`type=1&id=<连续编号>&ids=<逗号串>`）出发，
        act=15（无参数）收获。探险 3 分钟，早收返回 -2。

        ⚠️ 大地图的 act=4 **不能**用来收荒岛（实测 -5）；关卡号只认连续编号
        （1..48），`hy_section_id=65535` 是无效值。"""
        c = self.opts
        seq = int(c["island_seq"])
        place = spm.island_place_text(seq) or f"编号 {seq}"
        need = int(c.get("island_need") or 0)
        while not self._stop:
            code, d = await self._query()
            if code != 0:
                self._log("查询失败", False, f"code={code}")
                await asyncio.sleep(c["interval"])
                continue
            self._snapshot(d)
            ex = d.get("explore") or {}
            ts = int(ex.get("hy_explore_ts") or 0)
            cur = int(ex.get("hy_explore_pass_id") or 0)
            if ts and cur and cur != seq:
                self._log("荒岛进行中的关卡与目标不符，结束", False,
                          f"进行中 {cur}（{spm.island_place_text(cur)}），目标 {seq}")
                break

            if ts:
                elapsed = time.time() - ts
                if elapsed < c["min_wait"]:
                    self._log("荒岛探险中", True,
                              f"{place} {elapsed / 60:.1f}/{c['min_wait'] / 60:.0f} 分钟")
                    await asyncio.sleep(c["interval"])
                    continue
                r = await self._post(15)
                if r.get("code") != 0:
                    self._hf += 1
                    if self._hf >= 12:
                        self._log("荒岛收获持续失败，停止空转", False,
                                  f"code={r.get('code')} 已尝试 {self._hf} 次")
                        break
                    self._log("荒岛收获未就绪", False,
                              f"code={r.get('code')} 已跑 {elapsed / 60:.1f} 分（第{self._hf}次）")
                    await asyncio.sleep(c["interval"])
                    continue
                self._hf = 0
                res = r.get("data") or {}
                send = res.get("send") or []
                txt = pd.summarize_reward(res.get("content") or "") or fmt_reward(send)
                self.harvests += 1
                self.rewards.append({"ts": int(time.time()), "send": send,
                                     "content": res.get("content"), "text": txt})
                self._log("荒岛收获成功", True, txt)
                ts = 0

            if not c["auto_dispatch"]:
                self._log("已收获，未开自动派出，结束", True)
                break
            used = int(d.get("today_hy_explore_cnt") or 0)
            if c["daily_max"] and used >= c["daily_max"]:
                self._log("今日荒岛探险次数已用尽，结束", True,
                          f"{used}/{c['daily_max']}")
                break
            if c["max_rounds"] and self.dispatches >= c["max_rounds"]:
                self._log("已达派出轮数上限，结束", True)
                break

            sids = self._pick_spirites(d, n=need or None)
            if not sids:
                self._log("没有可用灵宠，结束", False)
                break
            ids_str = ",".join(str(x) for x in sids)
            tag = f"{place} / 灵宠 {ids_str}"
            if c["dry_run"]:
                self._log("模拟出发（未真发）", True, tag)
                break
            r = await self._post(14, type=1, id=seq, ids=ids_str)
            if r.get("code") != 0:
                self._log("荒岛出发失败", False, f"code={r.get('code')} {tag}")
                break
            self.dispatches += 1
            self._log("荒岛出发", True, tag)
            await asyncio.sleep(c["interval"])

    async def _loop_across(self) -> None:
        """穿越丛林（官方 c_across）：act=11 `sub_id=<关序-1>&id=<章号-1>&cnt=1`。

        **即时到账**（返回 `content` 就是奖励，没有收获步骤）。每成功一次
        `torrid_zone_explore_cnt` +1；被拒时该计数不变 —— 据此判定是否真执行。"""
        c = self.opts
        p = spm.across_place_by_ci(c["across_chapter"], c["across_index"]) or {}
        label = f"{p.get('chapter_name', '')}{p.get('index', '')}关 {p.get('name', '')}".strip()
        while not self._stop:
            code, d = await self._query()
            if code != 0:
                self._log("查询失败", False, f"code={code}")
                await asyncio.sleep(c["interval"])
                continue
            self._snapshot(d)
            used = int(d.get("torrid_zone_explore_cnt") or 0)
            if c["daily_max"] and used >= c["daily_max"]:
                self._log("今日历练次数已用尽，结束", True, f"{used}/{c['daily_max']}")
                break
            if c["max_rounds"] and self.dispatches >= c["max_rounds"]:
                self._log("已达历练轮数上限，结束", True)
                break
            if c["dry_run"]:
                self._log("模拟历练（未真发）", True, label)
                break

            r = await self._post(11, sub_id=c["across_sub_id"], id=c["across_id"], cnt=1)
            res = r.get("data") or {}
            content = res.get("content")
            if r.get("code") != 0 or not content:
                self._log("历练失败", False,
                          f"code={r.get('code')} {label}（该关可能未解锁）")
                break
            txt = pd.summarize_reward(content) or content
            self.dispatches += 1
            self.rewards.append({"ts": int(time.time()), "content": content, "text": txt})
            self._log("历练成功", True, f"{label} → {txt}")
            await asyncio.sleep(c["interval"])

    # ---------- 对外控制 ----------
    def start(self, **opts) -> dict:
        if self.running:
            return {"ok": False, "message": "自动探险已在运行"}
        mode = str(opts.get("mode") or "map")
        if mode not in ("map", "island", "across"):
            return {"ok": False, "message": f"未知玩法 {mode}（可选 map / island / across）"}
        ch = int(opts.get("across_chapter") or 0)
        idx = int(opts.get("across_index") or 0)
        sub_id, aid = spm.across_param(ch, idx) if (ch and idx) else (0, 0)
        self.opts = {
            "mode": mode,
            # 大地图（act=3/4）
            "section_id": int(opts.get("section_id") or 1),
            "pass_id": int(opts.get("pass_id") or 1),
            # 荒岛（act=14/15）：id 用**连续编号**，需按该关 elf_num 派够只数
            "island_seq": int(opts.get("island_seq") or 0),
            "island_need": int(opts.get("island_need") or 0),
            # 穿越丛林（act=11）：sub_id = 关序-1、id = 章号-1（实测的 0-based）
            "across_chapter": ch,
            "across_index": idx,
            "across_sub_id": sub_id,
            "across_id": aid,
            "daily_max": max(0, int(opts.get("daily_max") or 0)),
            "spirite_ids": [int(x) for x in (opts.get("spirite_ids") or [])],
            "spirite_num": max(1, min(8, int(opts.get("spirite_num") or 2))),
            "auto_dispatch": bool(opts.get("auto_dispatch", True)),
            "max_rounds": max(0, int(opts.get("max_rounds") or 0)),
            "interval": min(600, max(10, int(opts.get("interval") or 30))),
            "min_wait": min(3600, max(60, int(
                opts.get("min_wait") or (ISLAND_MIN_WAIT if mode == "island" else SPIRITE_MIN_WAIT)))),
            "dry_run": bool(opts.get("dry_run", False)),
        }
        if mode == "island":
            if self.opts["island_seq"] < 1:
                return {"ok": False, "message": "荒岛关卡编号（连续 1..48）必须 ≥ 1"}
            p = spm.island_place(self.opts["island_seq"])
            if not p:
                return {"ok": False,
                        "message": f"荒岛没有编号 {self.opts['island_seq']} 的关卡（官方配置 1..48）"}
            self.opts["island_need"] = self.opts["island_need"] or int(p.get("elf_num") or 0)
            self.opts["island_label"] = spm.island_place_text(self.opts["island_seq"])
            # 荒岛每天次数上限（普通 5 次，VIP 更多）；上限只用于「还剩几次」展示与到点自动停，
            # 不强制——服务端若已拒（用尽）会返回错误码，循环自然会停。
            self.opts["daily_max"] = self.opts["daily_max"] or ISLAND_DAILY_MAX
        elif mode == "across":
            if not (ch and idx):
                return {"ok": False, "message": "穿越丛林需要同时给 across_chapter（章号）和 across_index（关内序号）"}
            p = spm.across_place_by_ci(ch, idx)
            if not p:
                return {"ok": False, "message": f"穿越丛林没有 第{ch}章 第{idx}关（官方配置 11 章 62 关）"}
            self.opts["daily_max"] = self.opts["daily_max"] or ACROSS_DAILY_MAX
            self.opts["across_label"] = f"{p.get('chapter_name')} 第{idx}关"
        else:
            if self.opts["section_id"] < 1 or self.opts["pass_id"] < 1:
                return {"ok": False,
                        "message": f"地图/小关编号必须 ≥ 1（当前 section={self.opts['section_id']} "
                                   f"pass={self.opts['pass_id']}）。组合是否可出发由服务端判定，"
                                   f"返回 -5 即该组合无效。"}
            self.opts["daily_max"] = self.opts["daily_max"] or 0
        self.running = True
        self._stop = False
        self.finished = False
        self._hf = 0
        self.logs = []
        self.harvests = 0
        self.dispatches = 0
        self.rewards = []
        self._rot = 0
        self.started_at = time.time()
        self.task = asyncio.create_task(self._loop())
        return {"ok": True, "message": "自动探险已启动", "opts": self.opts}

    def stop(self) -> dict:
        if not self.running:
            return {"ok": False, "message": "当前没有在跑"}
        self._stop = True
        if self.task:
            self.task.cancel()
        return {"ok": True, "message": "已请求停止"}

    def status(self) -> dict:
        return {
            "ok": True,
            "running": self.running,
            "finished": self.finished,
            "message": self.message,
            "opts": self.opts,
            "started_at": int(self.started_at) if self.started_at else 0,
            "harvests": self.harvests,
            "dispatches": self.dispatches,
            "rewards": self.rewards[-20:],
            "state": self.last_state,
            "logs": self.logs[-80:],
        }


#: 三套玩法各自一个独立引擎，互不干扰（各自一个 asyncio 任务、各自的 running 标志）。
MODES = ("map", "island", "across")
_ENGINES: dict[str, "SpiriteAuto"] = {}


def get_engine(session, mode: str = "map") -> SpiriteAuto:
    """取（或重建）某玩法各自的独立引擎。三套玩法互不干扰。"""
    mode = str(mode) if str(mode) in MODES else "map"
    eng = _ENGINES.get(mode)
    if eng is None or eng.s is not session:
        eng = SpiriteAuto(session)
        _ENGINES[mode] = eng
    return eng
