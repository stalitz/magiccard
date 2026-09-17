"""自动换卡引擎（2026-09-10）。

功能：指定一套卡 + 该套内的目标卡（可指定单张，或按面值取整批），
从「正在玩这套卡的卡友」的换卡箱里自动换入，直到每张换够指定数量，
或卡友列表查完。

服务器约束（exchange.py 模块头已实测，本节全部依赖这些事实）：
- 换卡是**等价面值交换**：src 面值必须 == dst 面值 → 同名目卡的总数守恒，
  「一换一」不会让某个面值的卡变多。
- 「想收主题」限制作用于**商店能买到的套卡**，判据是**套卡级别**的（用户 2026-09-10 两次纠正）：
  该套卡内**存在 10 面额基础卡** → 这套能在商店买到 → 换出这套的任意卡时，
  其主题必须在对方 changebox 的 exch（想收主题，最多 4 个槽位）里。
  整套都没有 10 面额基础卡的（下架/绝版套，如名家 1792：最低面值 75）→ 商店买不到，
  **不受 exch 限制，只要面值相同就能换**。
  注意：限制看的是「卡所属套卡能不能买」，不是「这张卡自身是不是 10 面额」。
- dst 必须未锁（unlock 时间戳已过）且仍在对方换卡箱（否则 -33059）。
- 读他人主页约 15 次/10s 窗口后开始 -3001，故串行 + 0.7s 间隔 + 退避。

安全策略（避免「拆东墙补西墙」的净损失）：
- 默认只付出「多余的」卡：持有数 − 目标数 > 0 才可用。
  例：目标「每种 1 张」而你某张只有 1 张 → 不算多余，不会被付出。
- 由此外保证：换卡只改善品种结构，不会把你已有的品种换没了。
- dry_run=True 时只报告「会换什么」，不发换卡请求（测试/预览用）。
"""
import asyncio
import time

from .card_query import indexes, buyable_themes
from .constants import EP_MAINPAGE
from .exchange import parse_my_boxes, theme_peers, fetch_peer_box, do_exchange


class AutoExchange:
    def __init__(self, session):
        self.s = session
        self.task: asyncio.Task | None = None
        self._stop = False
        self.running = False
        self.logs: list[dict] = []
        # 运行参数
        self.opts: dict = {}
        self.need: dict[int, int] = {}      # card_id -> 还缺几张
        self.owned: dict[int, int] = {}     # card_id -> 当前持有
        self.peers_total = 0
        self.scanned = 0
        self.exchanged = 0
        self.failed = 0
        # 诊断计数：换不到时区分「对方没这张卡」和「我有卡付不起」
        self.seen_target = 0      # 在卡友箱里看到过缺的卡的次数
        self.miss_no_pay = 0      # 看到了但挑不出可付出的卡
        self.miss_locked = 0      # 看到了但卡被锁
        self.started_at = 0.0
        self.finished = False
        self.message = ""
        self.mine: list[dict] = []

    # ---------- 日志 ----------
    def _log(self, action: str, ok: bool, detail: str = "") -> None:
        self.logs.append({
            "ts": int(time.time()), "action": action,
            "ok": ok, "detail": detail,
        })
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]

    # ---------- 我方卡箱 ----------
    async def _refresh_mine(self) -> None:
        """重读自己主页，统计持有并按箱/槽位记录可付出的卡。"""
        mp = await self.s.post(EP_MAINPAGE, code="", opuin=self.s.uin)
        if mp.get("code") != 0:
            return
        boxes = parse_my_boxes(mp.get("raw", ""))
        mine: list[dict] = []
        owned: dict[int, int] = {}
        for box_name, box_type in (("storebox", 1), ("changebox", 0)):
            for c in boxes.get(box_name, []):
                cid = int(c.get("id") or 0)
                if cid <= 0:
                    continue
                owned[cid] = owned.get(cid, 0) + 1
                if c.get("locked"):
                    continue  # 换卡箱里被锁的卡不能付出
                mine.append({
                    "slot": int(c.get("slot") or 0),
                    "id": cid,
                    "price": int(c.get("price") or 0),
                    "theme_id": int(c.get("theme_id") or 0),
                    "name": c.get("name") or f"卡{cid}",
                    "box_type": box_type,   # 1=保险箱 0=换卡箱（do_exchange 的 src_type）
                })
        self.mine = mine
        self.owned = owned

    # ---------- 目标计算 ----------
    def _calc_targets(self) -> None:
        idx = indexes()
        # 源套（玩家池来源）：tids 多选，未给则回落到单 tid。
        src_tids = [int(t) for t in (self.opts.get("tids") or []) if str(t).strip()]
        if not src_tids:
            src_tids = [int(self.opts.get("tid") or 0)]
        self.src_tids = [t for t in src_tids if t > 0]
        # 主套：优先 tid，否则取源套第一套（未指定卡时按主套全部卡当目标）。
        tid = int(self.opts.get("tid") or 0) or (self.src_tids[0] if self.src_tids else 0)
        want = int(self.opts.get("want") or 1)
        price = int(self.opts.get("price") or 0)
        card_ids = [int(x) for x in (self.opts.get("card_ids") or []) if str(x).isdigit()]
        # 指定卡（可多张、可跨套）优先；未指定时回落到「主套全部卡」。
        # 与手动换卡一致：指定卡不再要求所属套在源套集合里 ——
        # 例：源套选纸寿千年等、指定卡选名家 263，就能从纸寿千年玩家箱里换到名家卡。
        th = next((t for t in idx["themes"] if t["tid"] == tid), None)
        ids = card_ids or (th["cards"] if th else [])
        targets: list[int] = []
        for cid in ids:
            ci = idx["card_by_id"].get(cid)
            if ci is None:
                continue
            if price and int(ci[3]) != price:
                continue
            targets.append(cid)
        self.targets = targets
        self.need = {cid: max(0, want - self.owned.get(cid, 0)) for cid in targets}
        self.need = {k: v for k, v in self.need.items() if v > 0}

    # ---------- 选一张可付出的卡 ----------
    def _pick_pay(self, price: int, exch: list[int]) -> dict | None:
        """挑一张可付出的同面值卡。两种模式：

        1) 自动模式（未指定 pay_card_ids）：挑「多余的」卡，持有数 − 保留数 > 0。
           - 保留数：目标卡（在 targets 里）按 want 计；**非目标卡按 1 计**——
             即默认替你保留每种至少 1 张，绝不会把某张卡的最后一张付出去
             （否则「换入 1 张新卡 + 失去 1 张旧卡」品种数净零，等于白换）。
        2) 指定付出卡模式（opts["pay_card_ids"] 非空）：**只从这些卡里付，且保留数按 0 计**，
           即可以把它们全换完。用于「拿仅有的 3 张不可合成的卡去换 3 张可合成的卡」这类
           明确要清仓的场景。付出卡用尽后不会回退到别的卡。

        exch 限制（套卡级，用户 2026-09-10 纠正）：只有当付出卡所属套卡**含 10 面额基础卡**
        （即这套在商店能买到）时，才要求「该主题在对方想收列表 exch 里」；
        整套无 10 面额基础卡的下架/绝版套，同面值即可换。
        另外「未设置想收套」（exch 为空）= 不限制，对方的任何同面额卡都能收，
        此时不做主题校验 —— 与 exchange.py（手动换卡）保持同一条规则。
        """
        want = int(self.opts.get("want") or 1)
        exch_set = {int(t) for t in exch if t}
        buyable = buyable_themes()
        pay_ids = {int(x) for x in (self.opts.get("pay_card_ids") or []) if str(x).strip()}
        for c in self.mine:
            if c["price"] != price:
                continue
            if exch_set and c["theme_id"] in buyable and c["theme_id"] not in exch_set:
                continue
            if pay_ids:
                if c["id"] not in pay_ids:
                    continue          # 指定模式：只付出你勾选的卡
                keep = 0              # 指定的卡可全部换完
            else:
                keep = want if c["id"] in self.targets else 1
            if self.owned.get(c["id"], 0) - keep <= 0:
                continue  # 没有多余，付出去就变缺了
            return c
        return None

    # ---------- 主循环 ----------
    async def _try_peer(self, box: dict) -> int:
        idx = indexes()
        got = 0
        dry = bool(self.opts.get("dry_run"))
        for card in box.get("cards", []):
            cid = int(card.get("id") or 0)
            if self.need.get(cid, 0) <= 0:
                continue
            self.seen_target += 1
            if card.get("locked"):
                self.miss_locked += 1
                continue
            ci = idx["card_by_id"].get(cid)
            if ci is None:
                continue
            price = int(ci[3])
            exch = [t for t in (box.get("exch") or []) if t]
            pay = self._pick_pay(price, exch)
            if not pay:
                self.miss_no_pay += 1
                continue
            name = str(ci[2]).strip()
            desc = f"{name}({price}) ← 付 {pay['name']}"
            if dry:
                self._log("试换", True, f"[模拟] {box.get('nick')}：{desc}")
                self.need[cid] -= 1
                got += 1
                continue
            r = await do_exchange(
                self.s, str(box.get("uin")), int(card["slot"]), cid,
                pay["slot"], pay["id"], pay["box_type"],
            )
            if r.get("ok"):
                self.need[cid] -= 1
                self.exchanged += 1
                got += 1
                self._log("换卡", True, f"{box.get('nick')}：{desc}")
                await self._refresh_mine()   # 换完立即重读，避免本地计数漂移
            else:
                self.failed += 1
                self._log("换卡", False,
                          f"{box.get('nick')}：{desc} → {r.get('message') or r.get('code')}")
        return got

    async def _run(self) -> None:
        self.started_at = time.time()
        try:
            await self._refresh_mine()
            self._calc_targets()
            total_need = sum(self.need.values())
            if not self.targets:
                self.message = "没有匹配的目标卡（检查套卡 ID / 面值 / 卡 ID）"
                self.finished = True
                self.running = False
                return
            if total_need == 0:
                self.message = "目标卡都已凑够，无需换卡"
                self.finished = True
                self.running = False
                return
            self._log("开始", True,
                      f"目标 {len(self.targets)} 种 / 共需 {total_need} 张"
                      + ("（模拟运行，不会真换）" if self.opts.get("dry_run") else ""))

            peers = list(self.opts.get("peers") or [])
            if not peers:
                # 多选源套：把每个源套「在玩」的玩家池并起来、去重。
                src = self.src_tids or [int(self.opts.get("tid") or 0)]
                src = [t for t in src if t > 0]
                for t in src:
                    for u in await theme_peers(self.s, t):
                        if u not in peers:
                            peers.append(u)
            peers = [p for p in peers if p and str(p) != str(self.s.uin)]
            self.peers_total = len(peers)
            max_peers = int(self.opts.get("max_peers") or 0)

            for i, u in enumerate(peers):
                if self._stop:
                    break
                if not any(v > 0 for v in self.need.values()):
                    break
                if max_peers and self.scanned >= max_peers:
                    break
                box = None
                for attempt in range(3):
                    box = await fetch_peer_box(self.s, u)
                    if box and box.get("ok"):
                        break
                    if (box or {}).get("code") == -3001:
                        await asyncio.sleep(6 * (attempt + 1))
                        continue
                    break
                self.scanned += 1
                if not box or not box.get("ok"):
                    continue
                await self._try_peer(box)
                await asyncio.sleep(0.7)

            left = sum(self.need.values())
            diag = (f"｜看到缺的卡 {self.seen_target} 次，其中被锁 {self.miss_locked} 次、"
                    f"无卡可付 {self.miss_no_pay} 次")
            if left == 0:
                self.message = f"已换够，共换入 {self.exchanged} 张" + (
                    f"（查了 {self.scanned}/{self.peers_total} 位卡友）")
            else:
                self.message = (f"卡友已查完，仍缺 {left} 张"
                                f"（查了 {self.scanned}/{self.peers_total} 位，换入 {self.exchanged} 张）"
                                + diag)
                if self.seen_target == 0:
                    self.message += "；卡友换卡箱里根本没出现这些卡，建议扩大卡友或改目标"
                elif self.miss_no_pay and self.exchanged == 0:
                    self.message += ("；看到了卡但付不出——需要「多余的」同面值卡"
                                     + ("（该套卡商店能买到，还要求其主题在对方想收清单里）"
                                        if int(self.opts.get("tid") or 0) in buyable_themes()
                                        else "")
                                     + "，同面值卡没有多余时不会动你的卡")
            self._log("结束", True, self.message)
        except Exception as e:  # noqa: BLE001
            self.message = f"运行异常：{e}"
            self._log("异常", False, self.message)
        finally:
            self.finished = True
            self.running = False

    # ---------- 对外 ----------
    def start(self, **opts) -> dict:
        if self.running:
            return {"ok": False, "message": "自动换卡正在运行中"}
        self._stop = False
        self.logs = []
        self.need = {}
        self.owned = {}
        self.mine = []
        self.scanned = 0
        self.exchanged = 0
        self.failed = 0
        self.seen_target = 0
        self.miss_no_pay = 0
        self.miss_locked = 0
        self.finished = False
        self.message = ""
        self.opts = opts
        self.running = True
        self.task = asyncio.create_task(self._run())
        return {"ok": True, "message": "已启动"}

    def stop(self) -> dict:
        self._stop = True
        self._log("中止", True, "收到停止请求，将在当前卡友处理完后结束")
        return {"ok": True, "message": "已请求停止"}

    def status(self) -> dict:
        idx = indexes()
        need_rows = []
        for cid, n in self.need.items():
            ci = idx["card_by_id"].get(cid)
            need_rows.append({
                "id": cid,
                "name": str(ci[2]).strip() if ci else f"卡{cid}",
                "price": int(ci[3]) if ci else 0,
                "own": self.owned.get(cid, 0),
                "need": n,
            })
        need_rows.sort(key=lambda r: (-r["price"], r["id"]))
        return {
            "running": self.running,
            "finished": self.finished,
            "message": self.message,
            "tid": int(self.opts.get("tid") or 0),
            "tids": getattr(self, "src_tids", []),
            "card_ids": [int(x) for x in (self.opts.get("card_ids") or [])
                         if str(x).isdigit()],
            "want": int(self.opts.get("want") or 1),
            "price": int(self.opts.get("price") or 0),
            "dry_run": bool(self.opts.get("dry_run")),
            "pay_card_ids": [int(x) for x in (self.opts.get("pay_card_ids") or [])
                             if str(x).strip().isdigit()],
            "peers_total": self.peers_total,
            "scanned": self.scanned,
            "exchanged": self.exchanged,
            "failed": self.failed,
            "seen_target": self.seen_target,
            "miss_no_pay": self.miss_no_pay,
            "miss_locked": self.miss_locked,
            "need_total": sum(self.need.values()),
            "need": need_rows,
            "elapsed": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "logs": self.logs[-60:],
        }


_auto_ex: AutoExchange | None = None


def get_engine(session) -> AutoExchange:
    global _auto_ex
    if _auto_ex is None or _auto_ex.s is not session:
        _auto_ex = AutoExchange(session)
    return _auto_ex
