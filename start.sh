#!/usr/bin/env sh
# ChainCoxswain 一键启动（macOS / Linux）
cd "$(dirname "$0")" || exit 1

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PORT="${CHAINCOXSWAIN_PORT:-8765}"
echo "[ChainCoxswain] 启动后端 http://127.0.0.1:$PORT"
echo "前端：直接用浏览器打开 index.html（file:// 离线可演示）"
python backend/server.py
