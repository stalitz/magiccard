#!/usr/bin/env python3
"""彻底抽 SWF 字符串，专找 card_travel_elf 动作请求参数名。

之前只过滤 UI 类名 + 返回字段名。这次放宽：把所有像 CGI 参数 / 动作动词的
字符串全量 dump 出来（不限长度、不分大小写），逐个看真实命名。
"""
import zlib, re, sys

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
        props = data[8:13]
        length = int.from_bytes(data[4:8], "little")
        return lzma.decompress(data[13:], lzma.FORMAT_RAW, filters=[{
            "id": lzma.FILTER_LZMA1, "dict_size": 1 << 22,
            "lc": props[0] & 7, "lp": (props[0] >> 3) & 7, "pb": props[0] >> 6,
        }])
    return data


def strings(b: bytes):
    out, cur = [], []
    for ch in b:
        if 32 <= ch < 127 or ch in (9, 10, 13):
            cur.append(chr(ch))
        else:
            if len(cur) >= 3:
                out.append("".join(cur))
            cur = []
    if len(cur) >= 3:
        out.append("".join(cur))
    return out


# 疑似 CGI 参数 / 动作动词（小写匹配）
TOKENS = ["card_travel", "cmd", "op", "act", "elf_id", "food_id", "tool_id",
          "travel_id", "pet_id", "cat_id", "type", "level_id", "scene", "map_id",
          "xurlloader", "urlvariables", "addvariable", "send", "recall", "reward",
          "claim", "explore", "depart", "starttravel", "goback", "takeelf",
          "random_visit", "amulet", "lucky_stone", "clover", "fenmo", "zhongkong",
          "free_cnt", "pay_cnt", "travel_end", "travel_ts", "travel_limit"]


def main():
    dec = decompress(PATH)
    print("decompressed", len(dec))
    allstr = strings(dec)
    print("total strings", len(allstr))
    seen = {}
    for s in allstr:
        sl = s.lower()
        if any(t in sl for t in TOKENS):
            seen.setdefault(s, 0)
            seen[s] += 1
    # 按长度升序打印短的（更像参数名）在前
    for s in sorted(seen, key=lambda x: len(x)):
        print(f"{seen[s]:5d}  {s!r}")
    print("=== unique matched:", len(seen))


if __name__ == "__main__":
    main()
