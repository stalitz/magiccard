#!/bin/bash
# 魔法卡片助手 - 服务管理脚本（独立后台进程，不占用任务卡片）
# 用法: ./server.sh {start|stop|status} [端口=8000] [session文件(可选,旧版兼容)]
# 账号：/tmp/mc_sessions/*.json，一个文件一个账号（文件名=token），同端口多账号靠 X-MC-Token 头路由
# 多开：用不同端口 + 不同 MC_SESSIONS_DIR 起多个实例
#   例：./server.sh start 8001 /tmp/mc_a.json
#       ./server.sh start 8002 /tmp/mc_b.json
cd "$(dirname "$0")"
PYBIN=/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3

PORT="${2:-8000}"
# 第 3 个参数是「旧版单文件会话」，已废弃：现在一个目录装所有账号（文件名=token）。
# 仅多实例兼容场景才需要显式传它，不传就走多账号目录。
SESS="${3:-}"
ACCOUNTS="${MC_SESSIONS_DIR:-/tmp/mc_sessions}"
LOG="/tmp/mc_${PORT}.log"

case "$1" in
  start)
    # 前置环境变量仅对本次 python 进程生效：PORT/MC_SESSION_FILE/MC_LOG 由 mc_api 与启动器读取
    ENVARGS=(MC_SESSIONS_DIR="$ACCOUNTS" MC_LOG="$LOG" PORT="$PORT")
    [ -n "$SESS" ] && ENVARGS+=(MC_SESSION_FILE="$SESS")
    # ★ 剥掉宿主代理环境变量（2026-11-05 真机事故）：从带 HTTP_PROXY/HTTPS_PROXY 的
    #   shell 启动时，后台常驻进程会继承那个临时端口；端口随会话失效后，任何
    #   httpx 默认 trust_env=True 的调用都变成 `All connection attempts failed`
    #   （「刷新卡库」就这么恒失败）。腾讯服务与 localhost 都直连可达，不需要代理。
    env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
        -u http_proxy -u https_proxy -u all_proxy \
        "${ENVARGS[@]}" $PYBIN - <<'PY'
import os, sys
port = int(os.environ.get("PORT", "8000"))
sess = os.environ.get("MC_SESSION_FILE", "/tmp/mc_session.json")
log = os.environ.get("MC_LOG", "/tmp/mc.log")
PYBIN = "/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3"
pid = os.fork()
if pid > 0: sys.exit(0)
os.setsid()
pid2 = os.fork()
if pid2 > 0: sys.exit(0)
os.chdir("/Users/stalitz/www/魔法卡片")
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1); os.dup2(fd, 2)
os.execv(PYBIN, [PYBIN, "-m", "uvicorn",
         "backend.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"])
PY
    # 自校验：脚本原先无论成败都打印 "started"，bind 失败时用户/助手都看不出来。
    for _ in $(seq 1 10); do
      lsof -iTCP:$PORT -sTCP:LISTEN -n -P >/dev/null 2>&1 && break
      sleep 0.5
    done
    if lsof -iTCP:$PORT -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "started -> http://localhost:$PORT (账号目录: $ACCOUNTS, log: $LOG)"
    else
      echo "启动失败：:$PORT 无监听。最后日志："
      tail -5 "$LOG"
      exit 1
    fi
    ;;
  stop)
    # ⚠️ 只杀「真正占用该端口的那个进程」，绝不用 pkill -f 宽匹配——
    # 2026-09-15 用 pkill -f "uvicorn backend.main" 把用户其他端口的同名进程也误杀了。
    for pid in $(lsof -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null); do
      kill "$pid" 2>/dev/null
    done
    # 必须等端口真正释放再返回：旧进程刚被杀时 :PORT 仍处于占用态，
    # 若紧接着 start 会 bind 失败（errno 48）退出，而旧实例已死 → 服务静默停摆。
    for _ in $(seq 1 20); do
      lsof -iTCP:$PORT -sTCP:LISTEN -n -P >/dev/null 2>&1 || break
      sleep 0.3
    done
    if lsof -iTCP:$PORT -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "stopped（警告：:$PORT 仍被占用，可能非本服务占用，请手动排查）"
    else
      echo "stopped（:$PORT）"
    fi
    ;;
  status)
    if lsof -iTCP:$PORT -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "RUNNING on :$PORT"
      curl -s http://localhost:$PORT/api/me; echo
    else
      echo "NOT running on :$PORT"
    fi
    ;;
  *)
    echo "用法: $0 {start|stop|status} [端口=8000] [session文件(可选,旧版兼容)]"
    ;;
esac
