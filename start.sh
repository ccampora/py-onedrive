#!/usr/bin/env bash
# start.sh — cleanly (re)start the OneDrive FUSE mount
#
# Usage:
#   ./start.sh              # start / restart with defaults
#   ./start.sh --debug      # pass extra flags to mount.py
#   ./start.sh --help

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
MOUNT_SCRIPT="$SCRIPT_DIR/mount.py"
MOUNTPOINT="$HOME/Onedrive"
LOG_FILE="/tmp/onedrive-mount.log"
PYTHON="$VENV/bin/python"
EXTRA_ARGS=("$@")
# ─────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

info()    { echo -e "${GREEN}[onedrive]${NC} $*"; }
warn()    { echo -e "${YELLOW}[onedrive]${NC} $*"; }
error()   { echo -e "${RED}[onedrive]${NC} $*" >&2; }

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [[ ! -f "$PYTHON" ]]; then
    error "Virtual environment not found at $VENV"
    error "Run: python -m venv $VENV && $VENV/bin/pip install -r $SCRIPT_DIR/requirements.txt"
    exit 1
fi

if [[ ! -f "$MOUNT_SCRIPT" ]]; then
    error "mount.py not found at $MOUNT_SCRIPT"
    exit 1
fi

# ── Step 1: kill any running mount.py process ─────────────────────────────────
PIDS=$(pgrep -f "python.*mount\.py" 2>/dev/null || true)
if [[ -n "$PIDS" ]]; then
    warn "Stopping existing mount process (PID $PIDS)..."
    kill "$PIDS" 2>/dev/null || true
    # Give it a moment to unmount cleanly via auto_unmount
    sleep 2
    # If it's still alive (e.g. stuck in D-state), force kill
    if kill -0 $PIDS 2>/dev/null; then
        warn "Process did not exit cleanly — sending SIGKILL"
        kill -9 $PIDS 2>/dev/null || true
        sleep 1
    fi
    # Always force a lazy unmount after killing — the transport endpoint
    # stays broken until explicitly detached even after SIGKILL.
    fusermount3 -uz "$MOUNTPOINT" 2>/dev/null || true
    sleep 1
else
    info "No existing mount process found"
fi

# ── Step 2: unmount any stale FUSE mount ──────────────────────────────────────
# Use lazy unmount unconditionally — it's a no-op when nothing is mounted
# and the only reliable option when the transport endpoint is broken.
fusermount3 -uz "$MOUNTPOINT" 2>/dev/null || true

# Verify the mountpoint is now accessible as a plain directory
if [[ -e "$MOUNTPOINT" ]] && ! ls "$MOUNTPOINT" &>/dev/null; then
    error "Mountpoint $MOUNTPOINT is still inaccessible after unmount."
    error "Try: sudo umount -l $MOUNTPOINT"
    exit 1
fi

# ── Step 3: ensure mountpoint directory exists ────────────────────────────────
if [[ ! -d "$MOUNTPOINT" ]]; then
    info "Creating mountpoint at $MOUNTPOINT"
    mkdir -p "$MOUNTPOINT"
fi

# ── Step 4: start mount.py ────────────────────────────────────────────────────
info "Starting OneDrive FUSE mount → $MOUNTPOINT"
info "Log: $LOG_FILE"
info "Stop with: fusermount3 -u $MOUNTPOINT  (or kill \$(pgrep -f mount.py))"

nohup "$PYTHON" "$MOUNT_SCRIPT" "${EXTRA_ARGS[@]}" \
    >> "$LOG_FILE" 2>&1 &

MOUNT_PID=$!
echo $MOUNT_PID > /tmp/onedrive-mount.pid

# ── Step 5: verify it mounted successfully ────────────────────────────────────
MAX_WAIT=20
for i in $(seq 1 $MAX_WAIT); do
    sleep 1
    if ! kill -0 $MOUNT_PID 2>/dev/null; then
        error "Process exited prematurely. Last log lines:"
        tail -20 "$LOG_FILE" >&2
        exit 1
    fi
    if mountpoint -q "$MOUNTPOINT" 2>/dev/null; then
        info "Mounted successfully (PID $MOUNT_PID)"
        echo ""
        tail -5 "$LOG_FILE"
        exit 0
    fi
done

error "Mount did not become active within ${MAX_WAIT}s. Last log lines:"
tail -20 "$LOG_FILE" >&2
exit 1
