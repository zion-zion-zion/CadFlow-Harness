#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM="$(uname -s)"
ARCHITECTURE="$(uname -m)"
case "$PLATFORM:$ARCHITECTURE" in
  Linux:x86_64)
    PROJECT_PYTHON="3.12"
    ;;
  Darwin:arm64)
    PROJECT_PYTHON="3.13"
    ;;
  *)
    printf 'error: unsupported platform %s/%s; expected Linux/x86_64 or macOS/arm64\n' \
      "$PLATFORM" "$ARCHITECTURE" >&2
    exit 1
    ;;
esac
BACKEND_HOST="${TEXT_TO_CAD_HOST:-0.0.0.0}"
BACKEND_PORT="${TEXT_TO_CAD_PORT:-8765}"
FRONTEND_HOST="${TEXT_TO_CAD_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${TEXT_TO_CAD_FRONTEND_PORT:-5678}"
API_TARGET_HOST="$BACKEND_HOST"
if [[ "$API_TARGET_HOST" == "0.0.0.0" || "$API_TARGET_HOST" == "::" ]]; then
  API_TARGET_HOST="127.0.0.1"
fi

if ! command -v uv >/dev/null 2>&1; then
  printf 'error: uv is required but was not found in PATH\n' >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  printf 'error: Node.js is required but was not found in PATH\n' >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  printf 'error: npm is required but was not found in PATH\n' >&2
  exit 1
fi
port_in_use() {
  local host="$1" port="$2"
  if [[ "$host" == "0.0.0.0" || "$host" == "::" ]]; then
    host="127.0.0.1"
  fi
  uv run --no-project --python "$PROJECT_PYTHON" python - "$host" "$port" <<'PY'
import socket
import sys

try:
    with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=0.2):
        pass
except OSError:
    raise SystemExit(1)
PY
}

if port_in_use "$BACKEND_HOST" "$BACKEND_PORT"; then
  printf 'error: backend port %s is already in use\n' "$BACKEND_PORT" >&2
  exit 1
fi
if port_in_use "$FRONTEND_HOST" "$FRONTEND_PORT"; then
  printf 'error: frontend port %s is already in use\n' "$FRONTEND_PORT" >&2
  exit 1
fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cadflow.XXXXXX")"
backend_pid=""
frontend_pid=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$frontend_pid" ]] && kill -0 "$frontend_pid" 2>/dev/null; then
    kill "$frontend_pid" 2>/dev/null || true
  fi
  if [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" 2>/dev/null; then
    kill "$backend_pid" 2>/dev/null || true
  fi
  wait "$frontend_pid" "$backend_pid" 2>/dev/null || true
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT INT TERM

cd "$SCRIPT_DIR"

printf 'Using %s/%s with Python %s\n' "$PLATFORM" "$ARCHITECTURE" "$PROJECT_PYTHON"
printf 'Starting backend at http://%s:%s\n' "$BACKEND_HOST" "$BACKEND_PORT"
TEXT_TO_CAD_HOST="$BACKEND_HOST" \
TEXT_TO_CAD_PORT="$BACKEND_PORT" \
  uv run --locked --python "$PROJECT_PYTHON" python -m backend \
  >"$LOG_DIR/backend.log" 2>&1 &
backend_pid=$!

printf 'Starting frontend at http://%s:%s\n' "$FRONTEND_HOST" "$FRONTEND_PORT"
TEXT_TO_CAD_API_TARGET="http://${API_TARGET_HOST}:${BACKEND_PORT}" \
  npm --prefix viewer run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" \
  >"$LOG_DIR/frontend.log" 2>&1 &
frontend_pid=$!

sleep 1
if ! kill -0 "$backend_pid" 2>/dev/null; then
  cat "$LOG_DIR/backend.log" >&2
  exit 1
fi
if ! kill -0 "$frontend_pid" 2>/dev/null; then
  cat "$LOG_DIR/frontend.log" >&2
  exit 1
fi

printf 'Both services are running. Press Ctrl-C to stop them.\n'
printf 'Logs: %s/backend.log and %s/frontend.log\n' "$LOG_DIR" "$LOG_DIR"
wait "$backend_pid" "$frontend_pid"
