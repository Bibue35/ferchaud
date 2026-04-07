#!/usr/bin/env bash
# ============================================================
# QuantBot — Combined Startup Script
# Runs the trading bot + web server together with one command
# Usage:  ./start.sh          (starts both)
#         ./start.sh --web    (web server only, no bot)
#         ./start.sh --bot    (bot only, no web server)
# ============================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Load .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PIDS=()

cleanup() {
  echo ""
  echo -e "${YELLOW}Shutting down QuantBot...${NC}"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  echo -e "${GREEN}All processes stopped. Goodbye!${NC}"
  exit 0
}
trap cleanup SIGINT SIGTERM

RUN_BOT=true
RUN_WEB=true

for arg in "$@"; do
  case $arg in
    --web) RUN_BOT=false ;;
    --bot) RUN_WEB=false ;;
  esac
done

echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       QuantBot v2 — Starting Up      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

if $RUN_BOT; then
  echo -e "${GREEN}▶ Starting trading bot (main.py)...${NC}"
  python3 main.py >> logs/bot.log 2>&1 &
  PIDS+=($!)
  echo -e "  Bot PID: ${PIDS[-1]} — logs/bot.log"
fi

if $RUN_WEB; then
  echo -e "${GREEN}▶ Starting web server on port 3000...${NC}"
  python3 -m uvicorn web.server:app --host 0.0.0.0 --port 3000 --reload >> logs/web.log 2>&1 &
  PIDS+=($!)
  echo -e "  Web PID: ${PIDS[-1]} — logs/web.log"
fi

echo ""
echo -e "${GREEN}✅ QuantBot is running!${NC}"
if $RUN_WEB; then
  echo -e "   ${CYAN}Open your browser: http://localhost:3000${NC}"
fi
echo -e "   Press ${YELLOW}Ctrl+C${NC} to stop everything"
echo ""

# Wait for any process to exit
wait -n "${PIDS[@]}" 2>/dev/null || wait "${PIDS[0]}"
