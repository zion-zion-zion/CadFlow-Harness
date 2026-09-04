#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

case "$(uname -s):$(uname -m)" in
  Linux:x86_64) PROJECT_PYTHON=3.12 ;;
  Darwin:arm64) PROJECT_PYTHON=3.13 ;;
  *) printf 'error: unsupported platform; expected Linux/x86_64 or macOS/arm64\n' >&2; exit 1 ;;
esac

if [[ ! -f .env ]]; then
  printf 'error: repository .env is required and must contain OPENAI_API_KEY and OPENAI_MODEL_ID\n' >&2
  exit 1
fi

exec uv run --locked --python "$PROJECT_PYTHON" python -m benchmark.runner "$@"
