#!/bin/bash
# 魔法卡片助手 - 服务管理脚本（独立后台进程，不占用任务卡片）
# 用法: ./server.sh {start|stop|status} [端口=8000] [session文件=/tmp/mc_session.json]
# 多开：用不同端口 + 不同 session 文件起多个实例，浏览器各开窗口访问不同端口，各登各的号
#   例：./server.sh start 8001 /tmp/mc_a.json
#       ./server.sh start 8002 /tmp/mc_b.json
cd "$(dirname "$0")"
PYBIN=/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3

PORT="${2:-8000}"
SESS="${3:-/tmp/mc_session.json}"
LOG="/tmp/mc_${PORT}.log"

case "$1" in
  start)
    # 前置环境变量仅对本次 python 进程生效：PORT/MC_SESSION_FILE/MC_LOG 由 mc_api 与启动器读取
    MC_SESSION_FILE="$SESS" MC_LOG="$LOG" PORT="$PORT" $PYBIN - <<'PY'
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
      echo "started -> http://localhost:$PORT (session: $SESS, log: $LOG)"
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
    echo "用法: $0 {start|stop|status} [端口=8000] [session文件=/tmp/mc_session.json]"
    ;;
esac
