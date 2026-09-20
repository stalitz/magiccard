#!/usr/bin/env python3
"""在 SWF 字节流里定位 card_travel_elf / sendCgi / cgi 等锚点，
打印其前后相邻的字符串窗口，看动作请求参数是否就近硬编码。"""
import zlib, re

PATH = "/tmp/TravelElfModule_v_295.swf"


def decompress(path: str) -> bytes:
    data = open(path, "rb").read()
    sig = data[:3]
    if sig == b"FWS":
        return data[8:]
    if sig == b"CWS":
        return zlib.decompress(data[8:])
    if sig == b"ZWS":
        import lzma
        props = data[8:13]; length = int.from_bytes(data[4:8], "little")
        return lzma.decompress(data[13:], lzma.FORMAT_RAW, filters=[{
            "id": lzma.FILTER_LZMA1, "dict_size": 1 << 22,
            "lc": props[0] & 7, "lp": (props[0] >> 3) & 7, "pb": props[0] >> 6}])
    return data


def strings_with_pos(b: bytes):
    out = []
    cur = []; start = 0
    for i, ch in enumerate(b):
        if 32 <= ch < 127 or ch in (9, 10, 13):
            if not cur:
                start = i
            cur.append(chr(ch))
        else:
            if len(cur) >= 3:
                out.append((start, "".join(cur)))
            cur = []
    if len(cur) >= 3:
        out.append((start, "".join(cur)))
    return out


ANCHORS = [b"card_travel_elf", b"card_travel_elf_main", b"sendCgi", b"XURLLoader",
           b"URLVariables", b"card_zhongkong", b"random_visit", b"travel_end_ts"]


def main():
    dec = decompress(PATH)
    strs = strings_with_pos(dec)
    for a in ANCHORS:
        idx = 0
        while True:
            p = dec.find(a, idx)
            if p < 0:
                break
            idx = p + 1
            # 找最近的字符串（位置 >= p-200 且 <= p+200）
            near = [(pos, s) for pos, s in strs if p - 400 <= pos <= p + 400]
            print(f"\n##### anchor {a!r} @ byte {p}  (nearby strings: {len(near)})")
            for pos, s in near[:40]:
                rel = pos - p
                print(f"   [{rel:+5d}] {s!r}")


if __name__ == "__main__":
    main()
