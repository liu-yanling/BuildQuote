#!/usr/bin/env bash
# 本机一键：启动 BuildQuote + Serveo 临时公网隧道（演示用，URL 不固定）
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8010}"
if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  nohup .venv/bin/python -m app.serve > data/dev-server.log 2>&1 &
  sleep 12
fi
nohup ssh -p 443 -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:127.0.0.1:"$PORT" serveo.net \
  > data/tunnel.log 2>&1 &
sleep 12
grep -o 'https://[a-z0-9]*\.serveousercontent\.com' data/tunnel.log | head -1 || {
  echo "隧道启动失败，查看 data/tunnel.log"; exit 1; }
