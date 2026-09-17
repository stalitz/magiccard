#!/usr/bin/env python3
"""宽范围枚举 card_act_send_free 的 act/id，寻找仍在运行的活动。"""
import asyncio, json, re, sys
from pathlib import Path
from backend.mc_api import LoginSession

TARGETS = []
# 重点扫 80~120、140~200
for act in range(1, 301):
    if act <= 20 or 80 <= act <= 130 or 140 <= act <= 210 or 280 <= act <= 300:
        ids = list(range(0, 21))
    else:
        ids = [0, 1]
    for i in ids:
        TARGETS.append((act, i))

HINTS = ["猫", "旅行", "探险", "80", "11/14", "0/600", "4/80", "进度", "已完成", "积分", "day", "第.*天", "17"]

def hints_in(text: str) -> list[str]:
    out = []
    for h in HINTS:
        if re.search(h, text, re.I):
            out.append(h)
    return out

async def main():
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    matches = []
    errors = []
    for idx, (act, i) in enumerate(TARGETS):
        try:
            if i == 0:
                r = await s.post("card_act_send_free", act=act)
            else:
                r = await s.post("card_act_send_free", act=act, id=i)
            code = r.get("code")
            msg = r.get("message", "")
            raw = r.get("raw", "")
            hs = hints_in(raw)
            if code == 0 or hs:
                rec = {"act": act, "id": i, "code": code, "msg": msg, "hints": hs, "raw": raw[:1200]}
                matches.append(rec)
                print(f"[HIT] act={act} id={i} code={code} hints={hs}")
            elif code not in (0, -1110, -1100, -1001, -1005):
                # 记录异常但非 end 的响应
                errors.append({"act": act, "id": i, "code": code, "msg": msg})
            if idx % 100 == 0:
                print(f"... {idx}/{len(TARGETS)}")
        except Exception as e:
            errors.append({"act": act, "id": i, "error": str(e)})

    out_path = Path("/tmp/probe_act_wide.json")
    out = {"matches": matches, "errors": errors[:100]}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"done, matches={len(matches)}, errors={len(errors)} -> {out_path}")


async def probe_chunk(start_act: int, end_act: int, ids: list[int], label: str):
    s = LoginSession()
    if not s.restore():
        print("未登录")
        return
    matches = []
    errors = []
    out_path = Path(f"/tmp/probe_act_{start_act}_{end_act}.json")
    total = (end_act - start_act + 1) * len(ids)
    for idx, act in enumerate(range(start_act, end_act + 1)):
        for i in ids:
            try:
                kw = {"act": act}
                if i != 0:
                    kw["id"] = i
                r = await s.post("card_act_send_free", **kw)
                code = r.get("code")
                msg = r.get("message", "")
                raw = r.get("raw", "")
                hs = hints_in(raw)
                if code == 0 or hs:
                    rec = {"act": act, "id": i, "code": code, "msg": msg, "hints": hs, "raw": raw[:1200]}
                    matches.append(rec)
                    print(f"[{label} HIT] act={act} id={i} code={code} hints={hs} msg={msg[:60]}")
                elif code not in (0, -1110, -1100, -1001, -1005):
                    errors.append({"act": act, "id": i, "code": code, "msg": msg})
            except Exception as e:
                errors.append({"act": act, "id": i, "error": str(e)})
        if idx % 5 == 0:
            out_path.write_text(json.dumps({"matches": matches, "errors": errors[:100]}, ensure_ascii=False, indent=2))
            print(f"[{label}] {idx}/{end_act-start_act+1}")
    out_path.write_text(json.dumps({"matches": matches, "errors": errors[:100]}, ensure_ascii=False, indent=2))
    print(f"[{label}] done -> {out_path}")


def run_chunk(start_act: int, end_act: int, ids: list[int], label: str):
    asyncio.run(probe_chunk(start_act, end_act, ids, label))

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4:
        run_chunk(int(sys.argv[1]), int(sys.argv[2]), [int(x) for x in sys.argv[3].split(",")], sys.argv[4])
    else:
        asyncio.run(main())
