#!/usr/bin/env python3
"""解压 TravelElfModule SWF，抽字符串定位 card_travel_elf 的动作参数。

手法（项目铁律③）：客户端 SWF 里硬编码了端点名与动作参数。
zlib.decompress(swf[8:]) 后 grep 打印字符串，过滤动作相关关键词。
只做离线只读反查，不向任何端点发真实动作。
"""
import urllib.request, zlib, re, sys

URL = "https://appimg2.qq.com/card/swf/modules/TravelElfModule_v_295.swf"


def decompress(path: str) -> bytes:
    data = open(path, "rb").read()
    # FWS: 未压缩头(8B) + zlib；CWS: 已 zlib；ZWS: LZMA
    sig = data[:3]
    if sig == b"FWS":
        return data[8:]
    if sig == b"CWS":
        return zlib.decompress(data[8:])
    if sig == b"ZWS":
        # LZMA: 4B 压缩长度 + 5B lzma props + stream
        import lzma
        props = data[8:13]
        length = int.from_bytes(data[4:8], "little")
        return lzma.decompress(data[13:], lzma.FORMAT_RAW, filters=[{
            "id": lzma.FILTER_LZMA1, "dict_size": 1 << 22,
            "lc": props[0] & 7, "lp": (props[0] >> 3) & 7, "pb": props[0] >> 6,
        }])
    return data


def strings(b: bytes):
    # 抽取可打印 ASCII（含常见符号）连续段
    out = []
    cur = []
    for ch in b:
        if 32 <= ch < 127 or ch in (9, 10, 13):
            cur.append(chr(ch))
        else:
            if len(cur) >= 4:
                out.append("".join(cur))
            cur = []
    if len(cur) >= 4:
        out.append("".join(cur))
    return out


def main():
    import tempfile, os
    path = "/tmp/TravelElfModule_v_295.swf"
    print("download", URL)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    open(path, "wb").write(raw)
    print("size", len(raw))
    dec = decompress(path)
    print("decompressed", len(dec))
    allstr = strings(dec)
    print("strings", len(allstr))
    # 关键词
    kws = ["travel_elf", "travelElf", "travel", "elf", "depart", "recall", "explore",
           "back", "reward", "claim", "start", "go", "pet", "tool", "cat", "trip",
           "card_travel", "zhongkong", "act", "cmd", "op", "petid", "toolid",
           "catid", "type", "levelid", "scene", "map"]
    hits = {}
    for s in allstr:
        sl = s.lower()
        if any(k in sl for k in kws):
            # 只保留像参数/端点/动作的较短串
            if len(s) <= 60:
                hits.setdefault(s, 0)
                hits[s] += 1
    # 输出去重计数，按出现排序
    for s, c in sorted(hits.items(), key=lambda x: -x[1]):
        print(f"{c:5d}  {s}")


if __name__ == "__main__":
    main()
