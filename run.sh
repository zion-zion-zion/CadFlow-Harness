#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_HOST="${TEXT_TO_CAD_HOST:-0.0.0.0}"
BACKEND_PORT="${TEXT_TO_CAD_PORT:-8000}"
FRONTEND_HOST="${TEXT_TO_CAD_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${TEXT_TO_CAD_FRONTEND_PORT:-5173}"
API_TARGET_HOST="$BACKEND_HOST"
if [[ "$API_TARGET_HOST" == "0.0.0.0" || "$API_TARGET_HOST" == "::" ]]; then
  API_TARGET_HOST="127.0.0.1"
fi

if ! command -v uv >/dev/null 2>&1; then
  printf 'error: uv is required but was not found in PATH\n' >&2
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
  (exec 9<>"/dev/tcp/${host}/${port}") >/dev/null 2>&1
}

if port_in_use "$BACKEND_HOST" "$BACKEND_PORT"; then
  printf 'error: backend port %s is already in use\n' "$BACKEND_PORT" >&2
  exit 1
fi
if port_in_use "$FRONTEND_HOST" "$FRONTEND_PORT"; then
  printf 'error: frontend port %s is already in use\n' "$FRONTEND_PORT" >&2
  exit 1
fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/simplecadapi.XXXXXX")"
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

printf 'Starting backend at http://%s:%s\n' "$BACKEND_HOST" "$BACKEND_PORT"
TEXT_TO_CAD_HOST="$BACKEND_HOST" \
TEXT_TO_CAD_PORT="$BACKEND_PORT" \
  uv run python -m backend >"$LOG_DIR/backend.log" 2>&1 &
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
