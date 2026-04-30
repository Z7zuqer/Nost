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
UPSTREAM_REPO="https://github.com/iterativv/NostalgiaForInfinity.git"
UPSTREAM_RAW="https://raw.githubusercontent.com/iterativv/NostalgiaForInfinity/main"

LOG_DIR="$REPO_DIR/user_data/logs"
LOG_FILE="$LOG_DIR/nfi-update.log"
LOCK_FILE="/tmp/nfi-host-update.lock"
SHA_CACHE="$REPO_DIR/user_data/.upstream_nfi_head_sha"

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

# Cheap-first check: ask upstream for HEAD SHA via git ls-remote (1 small HTTP).
# If it matches our cached SHA, no point downloading any files.
upstream_sha=$(git ls-remote "$UPSTREAM_REPO" HEAD 2>/dev/null | awk '{print $1}' | head -1)
if [ -z "$upstream_sha" ]; then
  log "ERROR: failed to query upstream HEAD via git ls-remote — aborting (cache untouched)"
  exit 0
fi

cached_sha=""
[ -f "$SHA_CACHE" ] && cached_sha=$(cat "$SHA_CACHE" 2>/dev/null || echo "")

if [ "$upstream_sha" = "$cached_sha" ]; then
  log "No new upstream commits (HEAD ${upstream_sha:0:7})"
  exit 0
fi

log "Upstream advanced ${cached_sha:0:7}..${upstream_sha:0:7} — checking files"

CHANGED=0
CHANGED_FILES=""
DOWNLOAD_FAILED=0

check_file() {
  local remote_path=$1
  local local_path=$2
  local tmp
  tmp=$(mktemp)
  if ! curl -fsSL --max-time 30 -o "$tmp" "$UPSTREAM_RAW/$remote_path"; then
    log "ERROR: download failed for $remote_path"
    DOWNLOAD_FAILED=1
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

check_file "${STRATEGY}.py"                       "${STRATEGY}.py"
check_file "configs/blacklist-${EXCHANGE}.json"   "configs/blacklist-${EXCHANGE}.json"
check_file "configs/${PAIRLIST_FILE}"             "configs/${PAIRLIST_FILE}"

# Only advance the cache if everything downloaded cleanly. Otherwise force retry next hour.
if [ "$DOWNLOAD_FAILED" -eq 0 ]; then
  echo "$upstream_sha" > "$SHA_CACHE"
fi

if [ "$CHANGED" -eq 0 ]; then
  log "Upstream had new commits but the 3 watched files are unchanged."
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
