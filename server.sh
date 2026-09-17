#!/bin/bash
# 魔法卡片助手 - 服务管理脚本（独立后台进程，不占用任务卡片）
# 用法: ./server.sh start | stop | status
cd "$(dirname "$0")"
# 项目无内置 venv；使用托管隔离环境（httpx/uvicorn/fastapi 已装于此）
PYBIN=/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3
LOG=/tmp/mc.log

case "$1" in
  start)
    # double-fork 守护进程化，脱离当前终端会话
    $PYBIN - <<'PY'
import os, sys
pid = os.fork()
if pid > 0: sys.exit(0)
os.setsid()
pid2 = os.fork()
if pid2 > 0: sys.exit(0)
os.chdir("/Users/stalitz/www/魔法卡片")
fd = os.open("/tmp/mc.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1); os.dup2(fd, 2)
os.execv("/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3",
         ["/Users/stalitz/.workbuddy/binaries/python/envs/mc/bin/python3", "-m", "uvicorn",
          "backend.main:app", "--host", "127.0.0.1",
          "--port", "8000", "--log-level", "warning"])
PY
    # 自校验：脚本原先无论成败都打印 "started"，bind 失败时用户/助手都看不出来。
    for _ in $(seq 1 10); do
      lsof -iTCP:8000 -sTCP:LISTEN -n -P >/dev/null 2>&1 && break
      sleep 0.5
    done
    if lsof -iTCP:8000 -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "started -> http://localhost:8000 (log: $LOG)"
    else
      echo "启动失败：:8000 无监听。最后日志："
      tail -5 "$LOG"
      exit 1
    fi
    ;;
  stop)
    # ⚠️ 只杀「真正占用 :8000 的那个进程」，绝不用 pkill -f 宽匹配——
    # 2026-09-15 用 pkill -f "uvicorn backend.main" 把用户其他端口的同名进程也误杀了。
    for pid in $(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null); do
      kill "$pid" 2>/dev/null
    done
    # 必须等端口真正释放再返回：旧进程刚被杀时 :8000 仍处于占用态，
    # 若紧接着 start 会 bind 失败（errno 48）退出，而旧实例已死 → 服务静默停摆。
    # 2026-09-14 就是踩了这个（stop;sleep 1;start 后无人校验，服务空了一下午）。
    for _ in $(seq 1 20); do
      lsof -iTCP:8000 -sTCP:LISTEN -n -P >/dev/null 2>&1 || break
      sleep 0.3
    done
    if lsof -iTCP:8000 -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "stopped（警告：:8000 仍被占用，可能非本服务占用，请手动排查）"
    else
      echo "stopped"
    fi
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
