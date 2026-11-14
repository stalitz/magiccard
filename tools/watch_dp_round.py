#!/usr/bin/env python3
"""DP 真机长期哨兵（只读归档 + 集齐一套的复盘窗口 + 自动续炼 + 红线停手）。

用户 2026-11-15 定调：一直炼、集齐一套时保留复盘窗口；发现问题要停并改好
（改前必须先跑仿真），改完重启自动炼卡，然后继续无限炼并继续监控。

行为：
1. 每 60s 只读一次 /api/auto/status，把 log 去重追加到全量时间线
   （服务端内存只留最近 30 条，多套连续炼会把第一套动作冲掉）。
2. 每次归档都做「红线」静态检查（硬铁律）：卖出动作超出白名单 /
   单轮套数 > 2 / 错误数暴增。命中 → 立刻暂停引擎 + 写 ALERT.flag，
   保持停止，等自动化或人工处理，绝不自动重启。
3. 集齐第一套（rounds 或 collected >= 1）→ 暂停引擎 + 存档完整 status +
   若无红线则按原参数自动重启「无限炼」，继续归档监控；只处理一次
   （ROUND1.flag 幂等），之后每套只归档不再暂停。
4. 引擎意外 stopped（连续 2 次读到）→ 自愈重启，避免整夜空转。

不修改任何生产代码，不操作其它账号；只写 .workbuddy/memory/ 下的文件。
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEM = ROOT / ".workbuddy" / "memory"
TOKEN = "59b8d9e2dc0748129d49f8545eabcaff"
BASE = "http://127.0.0.1:8000"
H = {"X-MC-Token": TOKEN}

ARCH = MEM / "2026-11-15-dp-log.jsonl"       # 全量动作时间线（去重追加）
SNAP = MEM / "2026-11-15-dp-run2.json"       # 最近一次完整 status 快照
FLAG = MEM / "2026-11-15-dp-STOPPED.flag"    # 兼容旧监控：集齐一套已暂停
ROUND1 = MEM / "2026-11-15-dp-ROUND1.flag"   # 第一套复盘窗口已处理（幂等）
ALERT = MEM / "2026-11-15-dp-ALERT.flag"     # 红线命中，保持停止

# 礼物箱已按用户要求收到 35/50，重启后不再收礼变卡（避免把腾出的箱位填满）
START = ("/api/auto/start?tid=1652&buy=1&rounds=0&sets=0&steal=1&dp=1"
         "&gift_drain=0")

SELL_OK = ("卖杂卡", "卖10面底卡", "挪保险箱")


def call(path: str, method: str = "GET", timeout: int = 30) -> dict:
    req = urllib.request.Request(BASE + path, headers=H, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def check_line(item: dict) -> list[str]:
    """硬铁律静态红线；宁可少报也不误停。"""
    bad = []
    act = str(item.get("action") or "")
    det = str(item.get("detail") or "")
    if "DP腾箱·" in det and not any(k in det for k in SELL_OK):
        bad.append("腾箱动作超出白名单: " + det[:100])
    if "卖" in act and not any(k in det for k in SELL_OK[:2]):
        bad.append("可疑卖出动作: " + det[:100])
    return bad


def check_status(d: dict) -> list[str]:
    bad = []
    sets = d.get("sets_now") or {}
    if isinstance(sets, dict) and any((v or 0) > 2 for v in sets.values()):
        bad.append(f"单轮套数超上限 2: sets_now={sets}")
    st = d.get("stats") or {}
    err = int(st.get("errors") or 0)
    ref = int(st.get("refined") or 0)
    if err >= 25 and err > 4 * max(1, ref):
        bad.append(f"错误数暴增: errors={err} refined={ref}")
    return bad


def archive(d: dict) -> tuple[int, list[str]]:
    seen: set[str] = set()
    if ARCH.exists():
        for line in ARCH.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line).get("k", ""))
            except Exception:                       # noqa: BLE001
                pass
    added, bad = 0, []
    with ARCH.open("a", encoding="utf-8") as fh:
        for item in d.get("log") or []:
            key = f'{item.get("ts")}|{item.get("action")}|{item.get("detail")}'
            bad += check_line(item)
            if key in seen:
                continue
            seen.add(key)
            fh.write(json.dumps({"k": key, **item}, ensure_ascii=False) + "\n")
            added += 1
    SNAP.write_text(json.dumps({"at": time.time(), "status": d},
                               ensure_ascii=False), encoding="utf-8")
    return added, bad


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps({"at": time.time(), **payload},
                               ensure_ascii=False), encoding="utf-8")


def once(stop_streak: list[int]) -> str:
    if ALERT.exists():
        return "alert"
    try:
        d = call("/api/auto/status")
    except Exception as exc:                       # noqa: BLE001
        stop_streak[0] = 0                        # 读失败≠引擎停（重启服务期间会读失败）
        print(f"read status failed: {exc}", flush=True)
        return "read_fail"

    added, line_bad = archive(d)
    bad = line_bad + check_status(d)
    st = d.get("stats") or {}
    print(f"archived=+{added} rounds={st.get('rounds')} refined={st.get('refined')} "
          f"bought={st.get('bought')} errors={st.get('errors')} "
          f"running={d.get('running')} round1={ROUND1.exists()}", flush=True)

    if bad:
        try:
            r = call("/api/auto/stop", "POST")
        except Exception as exc:                   # noqa: BLE001
            r = {"error": str(exc)}
        write(ALERT, {"reasons": bad, "stats": st, "stop": r})
        print(f"REDLINE -> STOP: {bad}", flush=True)
        return "alert"

    if not d.get("running"):
        stop_streak[0] += 1
    else:
        stop_streak[0] = 0

    done = int(st.get("rounds") or 0) >= 1 or int(st.get("collected") or 0) >= 1
    if not ROUND1.exists():
        if not done:
            # 集齐前意外停了 → 自愈（连续 2 次确认，避开服务重启窗口）
            if stop_streak[0] >= 2:
                try:
                    r = call(START, "POST", timeout=90)
                    print(f"SELF-HEAL restart: ok={r.get('ok')}", flush=True)
                    stop_streak[0] = 0
                except Exception as exc:           # noqa: BLE001
                    print(f"self-heal failed: {exc}", flush=True)
            return "running"
        # 集齐第一套：暂停 → 存档 → 无红线则自动续炼
        try:
            r = call("/api/auto/stop", "POST")
        except Exception as exc:                   # noqa: BLE001
            r = {"error": str(exc)}
        write(FLAG, {"stats": st, "stop": r})
        time.sleep(3)
        try:
            resumed = call(START, "POST", timeout=90)
        except Exception as exc:                   # noqa: BLE001
            resumed = {"error": str(exc)}
        write(ROUND1, {"stats": st, "stop": r, "redlines": [],
                       "resumed": {"ok": resumed.get("ok"),
                                   "message": resumed.get("message")}})
        stop_streak[0] = 0
        print(f"ROUND1 done -> resumed: ok={resumed.get('ok')} "
              f"msg={resumed.get('message')}", flush=True)
        return "resumed"

    # 第一套之后：纯归档；引擎意外停了同样自愈
    if stop_streak[0] >= 2:
        try:
            r = call(START, "POST", timeout=90)
            print(f"SELF-HEAL restart: ok={r.get('ok')}", flush=True)
            stop_streak[0] = 0
        except Exception as exc:                   # noqa: BLE001
            print(f"self-heal failed: {exc}", flush=True)
    return "monitor"


if __name__ == "__main__":
    streak = [0]
    if len(sys.argv) >= 3 and sys.argv[1] == "--loop":
        gap, limit = int(sys.argv[2]), int(sys.argv[3])
        for i in range(limit):
            if once(streak) == "alert":
                break
            if i + 1 < limit:
                time.sleep(gap)
    else:
        once(streak)
