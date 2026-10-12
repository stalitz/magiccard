"""买卡/换卡/炼卡写动作的审计日志。

问题背景（2026-10-06）：应用层之前**完全不记买卡日志**（mc.log 只有一个 mc.api
logger，全是二维码轮询），导致「箱里凭空多出/少了一张卡」事后无法审计，排查时
一路靠猜。本模块给三处写卡入口（card_buy / zcg._buy_ten_face / engine._buy_for）
统一落盘，格式：时间 | caller | cid | tid | code | ok | 备注。

落盘到 /tmp/mc_buy.log（与 mc.log 同目录、独立文件，避免被二维码轮询刷屏）。
"""
import logging
import os

_BUY_LOG_PATH = os.path.join("/tmp", "mc_buy.log")

_logger = logging.getLogger("mc.buy")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _fh = logging.FileHandler(_BUY_LOG_PATH, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    _logger.addHandler(_fh)
    # 同时打到 stderr，便于实时观察（不影响 mc.log 的 mc.api 输出）
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    _logger.addHandler(_sh)


def log_buy(caller: str, cid: int, tid: int, code=None, ok: bool = False,
            note: str = "") -> None:
    """记录一次买卡尝试。caller 标明来源（card_buy / zcg._buy_ten_face / engine）。"""
    _logger.info(f"BUY | caller={caller} cid={cid} tid={tid} code={code} ok={ok} {note}")
