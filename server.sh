#!/bin/bash
# 魔法卡片助手 - 服务管理脚本（独立后台进程，不占用任务卡片）
# 用法: ./server.sh start | stop | status
cd "$(dirname "$0")"
VENV=backend/.venv
LOG=/tmp/mc.log

case "$1" in
  start)
    # double-fork 守护进程化，脱离当前终端会话
    $VENV/bin/python - <<'PY'
import os, sys
pid = os.fork()
if pid > 0: sys.exit(0)
os.setsid()
pid2 = os.fork()
if pid2 > 0: sys.exit(0)
os.chdir(os.path.dirname(os.path.abspath(".")) or ".")
os.chdir("/Users/stalitz/www/魔法卡片")
fd = os.open("/tmp/mc.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1); os.dup2(fd, 2)
os.execv("backend/.venv/bin/uvicorn",
         ["uvicorn", "backend.main:app", "--host", "127.0.0.1",
          "--port", "8000", "--log-level", "warning"])
PY
    sleep 2
    echo "started -> http://localhost:8000 (log: $LOG)"
    ;;
  stop)
    pkill -f "uvicorn backend.main" && echo "stopped" || echo "not running"
    ;;
  status)
    if lsof -iTCP:8000 -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "RUNNING on :8000"
      curl -s http://localhost:8000/api/me; echo
    else
      echo "NOT running"
    fi
    ;;
  *)
    echo "用法: $0 {start|stop|status}"
    ;;
esac
