#!/usr/bin/env python3
"""从 SWF 解压数据里 dump 'card_travel_elf' / 'elf_id' / LoadVars 等上下文。

ActionScript 拼请求时端点名与参数名相邻，dump 前后字节能定位真实 POST 参数。
"""
import zlib, re

path = "/tmp/TravelElfModule_v_295.swf"
data = open(path, "rb").read()
dec = zlib.decompress(data[8:])
print("decompressed", len(dec))

# 把不可打印折叠，方便看上下文
def ctx(needle: bytes, before=60, after=200):
    print(f"\n##### context for {needle!r} #####")
    start = 0
    n = 0
    while True:
        i = dec.find(needle, start)
        if i < 0:
            break
        n += 1
        seg = dec[max(0, i-before): i+after]
        # 折叠不可打印为 .
        vis = bytes(b if 32 <= b < 127 else 46 for b in seg)
        print(f"--- occur {n} @ {i} ---")
        print(vis.decode("ascii", "replace"))
        start = i + 1
        if n >= 12:
            print("...(truncated)")
            break

for nd in [b"card_travel_elf", b"travel_elf", b"elf_id", b"LoadVars",
          b"sendAndLoad", b"URLRequest", b"addRequest", b"navigateToURL",
          b"random_visit", b"travel_dui", b"free_cnt", b"toolid", b"food_id"]:
    ctx(nd)
