"""驱动真实自动挖（走服务端 API）：向下走 N 层后自动停止，打印进度。
只轮询 /api/dig/state（自带 auto 字段），停止用 /api/dig/auto/stop。
用法： python -m tools.dig_auto_drive [层数]
"""
import json
import time
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=30).read())


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def main():
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    st0 = get("/api/dig/state")
    start = st0.get("dig_index")
    print(f"起始 dig_index = {start}，目标向下 {want} 层（→ {start+want}）")
    for i in range(45):
        time.sleep(11)
        st = get("/api/dig/state")
        idx = st.get("dig_index")
        a = st.get("auto") or {}
        running = a.get("running")
        delta = idx - start
        tail = (a.get("msgs") or [])[-1].get("text", "") if a.get("msgs") else ""
        print(f"  [轮{i}] dig_index={idx} (Δ{delta}) 已挖{a.get('digs')} "
              f"收获{a.get('collected')} running={running} | {tail}")
        if delta >= want:
            print(f"✅ 已向下推进 {delta} 层，达到目标，停止")
            post("/api/dig/auto/stop")
            break
        if not running:
            print(f"⚠️ 自动挖已停止：{a.get('finished')}")
            break
    else:
        print("⏱ 轮询超时，手动停止")
        post("/api/dig/auto/stop")


if __name__ == "__main__":
    main()
