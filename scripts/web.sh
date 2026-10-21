#!/usr/bin/env bash
# deal-monitor Web 面板管理
# 用法: ./scripts/web.sh start|stop|restart|status|log
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/data/web.pid"
LOGFILE="$ROOT/data/web.out"
PY="$ROOT/.venv/bin/python"

mkdir -p "$ROOT/data"

is_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

port() {
  "$PY" -c "import yaml; c=yaml.safe_load(open('$ROOT/config.yaml')) or {}; print((c.get('web') or {}).get('port', 8765))" 2>/dev/null || echo 8765
}

start() {
  if is_running; then
    echo "Web 面板已在运行 (pid $(cat "$PIDFILE"))  http://127.0.0.1:$(port)"
    exit 0
  fi
  nohup "$PY" "$ROOT/run.py" web >>"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"
  sleep 1.2
  if is_running; then
    echo "已启动 (pid $(cat "$PIDFILE"))"
    echo "面板地址: http://127.0.0.1:$(port)"
    echo "日志: $LOGFILE"
  else
    echo "启动失败，最近日志："
    tail -n 20 "$LOGFILE" 2>/dev/null || true
    rm -f "$PIDFILE"
    exit 1
  fi
}

stop() {
  if ! is_running; then
    echo "未在运行"
    rm -f "$PIDFILE"
    exit 0
  fi
  pid="$(cat "$PIDFILE")"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.3
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "进程未退出，强制结束"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
  echo "已停止"
}

status() {
  if is_running; then
    echo "运行中 (pid $(cat "$PIDFILE"))  http://127.0.0.1:$(port)"
  else
    echo "未运行"
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 0.5; start ;;
  status) status ;;
  log) tail -n 100 -f "$LOGFILE" ;;
  *) echo "用法: $0 start|stop|restart|status|log"; exit 1 ;;
esac
