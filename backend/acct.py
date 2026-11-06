"""按账号隔离的落盘状态（日计数 / 每日限次 / 实时缓存 / 运行历史）。

同一端口可同时跑多个 QQ 号（`main.SESSIONS[token]`），任何**落到磁盘的状态**都不能
共用一个文件：A 账号把 50 次送礼额度用完，B 账号点一键日常就会被本地计数直接拦下，
一次请求都不发（2026-11-06 用户实测就是这个现象）。

分桶键优先取 **uin**：送礼额度、摇水果/公会战次数、比赛主题都是**游戏账号级**的，
同一个 QQ 号用两个 token 登录本来就该共享同一份额度；取不到 uin 再退回 token。
（注意 `default` 与真 token 可能是同一个 QQ 号，按 uin 分桶刚好把它们合到一起，
不会出现同号双份额度。）

纯静态数据（卡库、道具表、配方表）不要用这里，它们对所有账号都一样。
"""
from pathlib import Path


def cur_session():
    """当前请求上下文的登录会话；延迟 import main，避开 main ↔ 业务模块的循环依赖。"""
    try:
        from .main import _cur_session
        return _cur_session()
    except Exception:  # noqa: BLE001
        return None


def acct_key(session=None) -> str:
    """账号键：uin 优先（游戏账号级状态），退回 token，最后 "default"。"""
    s = session if session is not None else cur_session()
    if s is not None:
        for attr in ("uin", "token"):
            try:
                v = getattr(s, attr, None)
            except Exception:  # noqa: BLE001
                v = None
            if v:
                return str(v)
    return "default"


def acct_path(path, session=None) -> Path:
    """`data/x.json` → `data/x.<uin>.json`；同一账号始终落在同一个文件。"""
    p = Path(path)
    return p.with_name(f"{p.stem}.{acct_key(session)}{p.suffix}")
