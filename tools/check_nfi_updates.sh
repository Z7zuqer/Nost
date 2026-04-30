#!/bin/bash
# Pull upstream NFI strategy/blacklist/pairlist from raw.githubusercontent.com
# (NOT git pull — keeps our MyNFI.py / docker-compose.yml / .env untouched).
# Restarts freqtrade only if a file actually changed AND no trade is open.
# Designed to run from cron on the host.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STRATEGY="${STRATEGY:-NostalgiaForInfinityX7}"
EXCHANGE="${EXCHANGE:-binance}"
PAIRLIST_FILE="${NFI_PAIRLIST_FILE:-pairlist-static-binance-futures-usdt.json}"
UPSTREAM="https://raw.githubusercontent.com/iterativv/NostalgiaForInfinity/main"

LOG_DIR="$REPO_DIR/user_data/logs"
LOG_FILE="$LOG_DIR/nfi-update.log"
LOCK_FILE="/tmp/nfi-host-update.lock"

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date -Iseconds)] Skipping: another update run in progress" >> "$LOG_FILE"
  exit 0
fi

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE"; }

cd "$REPO_DIR"

# Load .env for API + Telegram credentials (no-op if absent)
if [ -f .env ]; then
  set -a; source .env; set +a
fi

CHANGED=0
CHANGED_FILES=""

check_file() {
  local remote_path=$1
  local local_path=$2
  local tmp
  tmp=$(mktemp)
  if ! curl -fsSL --max-time 30 -o "$tmp" "$UPSTREAM/$remote_path"; then
    log "ERROR: download failed for $remote_path"
    rm -f "$tmp"
    return
  fi
  if [ ! -f "$local_path" ] || ! cmp -s "$tmp" "$local_path"; then
    log "CHANGED: $local_path"
    cp "$tmp" "$local_path"
    CHANGED=1
    CHANGED_FILES="$CHANGED_FILES $(basename "$local_path")"
  fi
  rm -f "$tmp"
}

log "Checking upstream NFI for updates..."

check_file "${STRATEGY}.py"                       "${STRATEGY}.py"
check_file "configs/blacklist-${EXCHANGE}.json"   "configs/blacklist-${EXCHANGE}.json"
check_file "configs/${PAIRLIST_FILE}"             "configs/${PAIRLIST_FILE}"

if [ "$CHANGED" -eq 0 ]; then
  log "No updates."
  exit 0
fi

# Skip restart if any trade is currently open — wait for next cron tick.
API_PORT="${FREQTRADE__API_SERVER__LISTEN_PORT:-8080}"
API_USER="${FREQTRADE__API_SERVER__USERNAME:-}"
API_PASS="${FREQTRADE__API_SERVER__PASSWORD:-}"
if [ -n "$API_USER" ] && [ -n "$API_PASS" ]; then
  open_count=$(curl -s --max-time 10 -u "$API_USER:$API_PASS" \
    "http://localhost:$API_PORT/api/v1/count" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('current', 0))" 2>/dev/null || echo "?")
  if [ "$open_count" != "0" ] && [ "$open_count" != "?" ]; then
    log "SKIP RESTART: $open_count open trade(s) — will retry next cron tick"
    exit 0
  fi
fi

log "Restarting freqtrade (changed:$CHANGED_FILES)"
sudo -n /usr/bin/docker compose -f "$REPO_DIR/docker-compose.yml" up -d --force-recreate >/dev/null 2>&1 || {
  log "ERROR: docker compose restart failed"
  exit 1
}

# Optional: telegram notification using freqtrade's bot credentials.
if [ -n "${FREQTRADE__TELEGRAM__TOKEN:-}" ] && [ -n "${FREQTRADE__TELEGRAM__CHAT_ID:-}" ]; then
  msg="NFI auto-update applied —${CHANGED_FILES}. Bot restarted."
  curl -sS --max-time 10 -X POST \
    --data-urlencode "text=$msg" \
    --data "chat_id=${FREQTRADE__TELEGRAM__CHAT_ID}" \
    "https://api.telegram.org/bot${FREQTRADE__TELEGRAM__TOKEN}/sendMessage" >/dev/null || true
fi

log "Done."
