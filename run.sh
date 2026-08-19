#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PI_SIDECAR_DIR="$SCRIPT_DIR/pi-sidecar"
PI_ENABLED="${TEXT_TO_CAD_ENABLE_PI:-}"
if [[ -z "$PI_ENABLED" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    PI_ENABLED="0"
  else
    PI_ENABLED="1"
  fi
fi
case "$PI_ENABLED" in
  1|true|TRUE|yes|YES|on|ON) PI_ENABLED="1" ;;
  0|false|FALSE|no|NO|off|OFF) PI_ENABLED="0" ;;
  *)
    printf 'error: TEXT_TO_CAD_ENABLE_PI must be a boolean value\n' >&2
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
if ! node -e '
const [major, minor] = process.versions.node.split(".").map(Number);
process.exit(major > 22 || (major === 22 && minor >= 19) ? 0 : 1);
'; then
  printf 'error: Node.js 22.19 or newer is required for Pi (found %s)\n' "$(node --version)" >&2
  exit 1
fi
if [[ "$PI_ENABLED" == "1" && "$(uname -s)" == "Linux" && ! -x /usr/bin/bwrap ]]; then
  printf 'error: /usr/bin/bwrap is required for the Pi shell sandbox\n' >&2
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

if [[ "$PI_ENABLED" == "1" ]]; then
  sidecar_install_marker="$PI_SIDECAR_DIR/node_modules/.package-lock.json"
  if [[ ! -f "$sidecar_install_marker" \
    || "$PI_SIDECAR_DIR/package.json" -nt "$sidecar_install_marker" \
    || "$PI_SIDECAR_DIR/package-lock.json" -nt "$sidecar_install_marker" ]]; then
    printf 'Installing locked Pi sidecar dependencies\n'
    npm --prefix "$PI_SIDECAR_DIR" ci
  fi
  printf 'Building Pi sidecar\n'
  npm --prefix "$PI_SIDECAR_DIR" run build
else
  printf 'Pi sidecar disabled\n'
fi

printf 'Starting backend at http://%s:%s\n' "$BACKEND_HOST" "$BACKEND_PORT"
TEXT_TO_CAD_ENABLE_PI="$PI_ENABLED" \
TEXT_TO_CAD_HOST="$BACKEND_HOST" \
TEXT_TO_CAD_PORT="$BACKEND_PORT" \
  uv run --no-sync python -m backend >"$LOG_DIR/backend.log" 2>&1 &
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
