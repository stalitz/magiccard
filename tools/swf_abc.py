#!/usr/bin/env python3
"""SWF → ABC 字符串定位器（**只做「找名字串」这件事**，不解析字节码）。

背景：本项目的道具/玩法配置有一部分**不在任何静态 XML 里**（如大类 70 = 挖地道具），
名字只存在于客户端模块 SWF 的内嵌字符串表（AS3 ABC 常量池）。这个工具用来把它们捞出来。

用法：
    # 直接给 URL 或本地文件 + 关键字
    python tools/swf_abc.py http://appimg2.qq.com/card/swf/modules/WaDiModule_v_32.swf 钉子
    python tools/swf_abc.py /tmp/WaDi.swf 铜爵 --ctx 12

原理（**关键坑**）：ABC 常量池是「u30 长度 + 字节」连续排列的，但 int/uint/double 三个
定长池的长度**不能只按计数直算** —— 本 SWF 实测 `int_count=322` 之后就全程失步。
所以这里**不解析池头**，改成：枚举偏移 P，按「长度前缀」贪心解码，**能一路连到目标串
的那个对齐就是对的**；再从该对齐点向前/向后打印邻居（邻居位置 = 类内编号顺序，
实测大类 70 的 11 件道具就是一条连续的名字串，用已核实的锚点可以定出未知道具名）。

⚠️ 不实现字节码反汇编（找 act 号那种需求需要真正的 ABC 解析器，本工具不做）。
"""
from __future__ import annotations

import struct
import sys
import urllib.request
import zlib


# ---------------------------------------------------------------- SWF 容器
def load_swf(path_or_url: str) -> bytes:
    """读本地文件或 http(s) URL，返回**解压后**的 SWF 主体。"""
    if path_or_url.startswith(("http://", "https://")):
        req = urllib.request.Request(path_or_url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=40).read()
    else:
        raw = open(path_or_url, "rb").read()
    sig = raw[:3]
    if sig == b"CWS":
        return raw[:8] + zlib.decompress(raw[8:])
    if sig == b"ZWS":
        raise SystemExit("ZWS(LZMA) 暂不支持")
    return raw


def _tags(body: bytes):
    """遍历 SWF 标签，yield (tag_code, payload)。"""
    p = 8
    nbits = body[p] >> 3
    rect_bits = 5 + nbits * 4
    p += (rect_bits + 7) // 8
    p += 4  # frameRate(2) + frameCount(2)
    while p + 2 <= len(body):
        len_tag = struct.unpack_from("<H", body, p)[0]
        p += 2
        code = len_tag >> 6
        length = len_tag & 0x3F
        if length == 0x3F:
            length = struct.unpack_from("<I", body, p)[0]
            p += 4
        yield code, body[p:p + length]
        p += length


def doabc_payloads(body: bytes):
    """所有 DoABC(82) 标签 → [(name, abc_data)]（跳过 4 字节 flags 与名字串）。"""
    out = []
    for code, payload in _tags(body):
        if code == 82 and len(payload) > 4:
            flags = struct.unpack_from("<I", payload, 0)[0]
            j = payload.find(b"\x00", 4)
            name = payload[4:j].decode("utf-8", "ignore") or f"flagged_{flags}"
            out.append((name, payload[j + 1:]))
    return out


# ---------------------------------------------------------------- 字符串
def read_u30(b: bytes, p: int) -> tuple[int, int]:
    v = 0
    for i in range(5):
        x = b[p]
        p += 1
        v |= (x & 0x7F) << (7 * i)
        if not x & 0x80:
            break
    return v, p


def _walk(b: bytes, p: int, limit: int, max_len: int = 200000):
    """从 p 按「长度前缀」贪心解码，yield (条目序号, 条目起点, 内容)。"""
    for i in range(limit):
        if p >= len(b):
            return
        L, q = read_u30(b, p)
        if L > max_len or q + L > len(b):
            return
        yield i, p, b[q:q + L]
        p = q + L


def prev_entry_start(b: bytes, cur_start: int, max_len: int = 4000) -> int | None:
    """反推「上一条」的起点。

    `cur_start` = 当前条目的**长度前缀字节**位置。上一条的内容 [q, q+v) 必须正好
    结束在 cur_start，所以判据是 `q + v == cur_start`（v = 长度，q = 内容起点）。
    """
    for L in range(0, max_len):
        p = cur_start - 1 - L
        if p < 0:
            return None
        v, q = read_u30(b, p)
        if q + v == cur_start:
            return p
    return None


def find_run(b: bytes, needle: bytes):
    """找含 `needle` 的**对齐串表**，并反向回溯到表头。

    返回 (表头偏移, needle 的条目序号, 条目列表)；找不到返回 None。

    needle 可能在文件里出现多次（一次在提示文案里、一次在真正的名字表里），
    所以**每个出现位置都试**，取「回溯后条目数最多」的那个 = 真正的表。
    """
    best = None
    pos = -1
    while True:
        pos = b.find(needle, pos + 1)
        if pos < 0:
            break
        # 该条目起点 = 往前 1..5 字节里那个「长度前缀正好等于 len(needle) 且恰好接上」的位置
        head = None
        for back in range(1, 6):
            p = pos - back
            if p < 0:
                continue
            v, q = read_u30(b, p)
            if q == pos and v == len(needle):
                head = p
                break
        if head is None:
            continue
        # 反向回溯到表头
        while True:
            pv = prev_entry_start(b, head)
            if pv is None:
                break
            head = pv
        entries = [s for _i, _s0, s in _walk(b, head, limit=200000)]
        if best is None or len(entries) > len(best[2]):
            idx = next((i for i, _s0, s in _walk(b, head, limit=200000)
                        if s == needle), None)
            best = (head, idx, entries)
    return best


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    src, kw = sys.argv[1], sys.argv[2]
    ctx = 12
    if "--ctx" in sys.argv:
        ctx = int(sys.argv[sys.argv.index("--ctx") + 1])

    body = load_swf(src)
    payloads = doabc_payloads(body)
    print(f"SWF {len(body)}B · ABC 单元 {[n for n, _ in payloads]}")
    needle = kw.encode()
    for name, abc in payloads:
        r = find_run(abc, needle)
        if not r:
            print(f"[{name}] 没找到对齐串表包含 {kw!r}")
            continue
        P, idx, entries = r
        print(f"[{name}] 串表头 @{P}，{kw!r} 在第 {idx} 条，共解出 {len(entries)} 条")
        for k in range(max(0, idx - ctx), min(len(entries), idx + ctx + 1)):
            s = entries[k].decode("utf-8", "ignore").replace("\n", "\\n")
            print(f"  {k:>6} {'>>' if k == idx else '  '} {s}")


if __name__ == "__main__":
    main()
