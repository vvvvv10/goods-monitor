#!/usr/bin/env bash
# deal-monitor 后台运行管理
# 用法: ./scripts/daemon.sh start|stop|restart|status|log
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/data/monitor.pid"
LOGFILE="$ROOT/data/monitor.out"
PY="$ROOT/.venv/bin/python"

mkdir -p "$ROOT/data"

is_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

start() {
  if is_running; then
    echo "监控已在运行 (pid $(cat "$PIDFILE"))"
    exit 0
  fi
  nohup "$PY" "$ROOT/run.py" run >>"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"
  echo "已启动 (pid $(cat "$PIDFILE"))"
  echo "日志: $LOGFILE   （tail -f 查看）"
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
    sleep 0.5
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
    echo "运行中 (pid $(cat "$PIDFILE"))"
    tail -n 5 "$LOGFILE" 2>/dev/null || true
  else
    echo "未运行"
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  log) tail -n 100 -f "$LOGFILE" ;;
  *) echo "用法: $0 start|stop|restart|status|log"; exit 1 ;;
esac
