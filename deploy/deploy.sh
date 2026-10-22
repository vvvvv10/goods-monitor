#!/usr/bin/env bash
# 本地执行：同步项目到服务器，并用 Docker Compose 构建启动。
#
# 用法:
#   ./deploy/deploy.sh user@1.2.3.4
#   DEPLOY_SSH_OPTS='-p 2222 -i ~/.ssh/id_ed25519' ./deploy/deploy.sh root@example.com
#   REMOTE_DIR=/srv/goods-monitor ./deploy/deploy.sh user@host
set -euo pipefail

SERVER="${1:-${DEPLOY_SERVER:-}}"
REMOTE_DIR="${REMOTE_DIR:-/opt/goods-monitor}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_CMD="ssh ${DEPLOY_SSH_OPTS:-}"

if [ -z "$SERVER" ]; then
  echo "用法: $0 user@host"
  echo "可选环境变量: REMOTE_DIR=/opt/goods-monitor DEPLOY_SSH_OPTS='-p 2222 -i ~/.ssh/id_ed25519'"
  exit 1
fi

if [ ! -f "$ROOT/config.yaml" ]; then
  echo "缺少 $ROOT/config.yaml"
  echo "请先复制 config.example.yaml 并填好钉钉 webhook："
  echo "  cp config.example.yaml config.yaml"
  exit 1
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

TMP_CFG="$(mktemp -t goods-monitor-config.XXXXXX.yaml)"
trap 'rm -f "$TMP_CFG"' EXIT

"$PY" - "$ROOT/config.yaml" "$TMP_CFG" <<'PY'
import sys
import yaml

src, dst = sys.argv[1], sys.argv[2]
with open(src, encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}
cfg.setdefault("browser", {})
cfg["browser"]["channel"] = "chromium"   # 容器内使用 Playwright 自带 Chromium（新无头模式）
cfg["browser"]["headless"] = True
with open(dst, "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
PY

echo "==> 准备远程目录 $SERVER:$REMOTE_DIR"
$SSH_CMD "$SERVER" "mkdir -p '$REMOTE_DIR/data'"

echo "==> 同步代码"
rsync -az \
  --exclude '.git/' --exclude '.venv/' --exclude 'data/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store' \
  --exclude 'config.yaml' \
  -e "$SSH_CMD" "$ROOT/" "$SERVER:$REMOTE_DIR/"

echo "==> 上传服务器配置（channel 已切换为容器内 Chromium）"
scp -q ${DEPLOY_SSH_OPTS:-} "$TMP_CFG" "$SERVER:$REMOTE_DIR/config.yaml"

# 国内服务器直连 Docker Hub 经常 403，默认走 DaoCloud/国内镜像；海外可覆盖。
BASE_IMAGE="${BASE_IMAGE:-docker.m.daocloud.io/library/python:3.11-slim-bookworm}"
PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://registry.npmmirror.com/-/binary/playwright}"
MEM_LIMIT="${MEM_LIMIT:-768m}"

echo "==> 构建并启动容器（首次约 5-15 分钟）"
$SSH_CMD "$SERVER" "set -e
  cd '$REMOTE_DIR'
  command -v docker >/dev/null || { echo 'Docker 未安装，请先安装 Docker + compose 插件'; exit 1; }
  docker compose version >/dev/null || { echo '缺少 docker compose 插件'; exit 1; }
  mkdir -p data
  if [ \"\$(id -u)\" = 0 ]; then
    chown -R 1000:1000 data
    chown 1000:1000 config.yaml
  fi
  chmod 600 config.yaml
  BASE_IMAGE='$BASE_IMAGE' PLAYWRIGHT_DOWNLOAD_HOST='$PLAYWRIGHT_DOWNLOAD_HOST' MEM_LIMIT='$MEM_LIMIT' docker compose up -d --build
  docker compose ps"

echo
echo "完成。常用命令："
echo "  查看日志: ssh $SERVER \"cd $REMOTE_DIR && docker compose logs -f\""
echo "  SSH 隧道: ssh -N -L 8765:127.0.0.1:8765 $SERVER"
echo "  打开面板: http://127.0.0.1:8765"
