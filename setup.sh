#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MIN_GLIBC_MAJOR=2
MIN_GLIBC_MINOR=31
MIN_MACOS_MAJOR=26
PROJECT_NODE_VERSION=22

usage() {
  cat <<'EOF'
Usage: ./setup.sh [--check]

Install the platform-specific Python environment and Viewer dependencies.

Options:
  --check  Validate the platform and required tools without installing anything.
  -h, --help
           Show this help message.
EOF
}

CHECK_ONLY=false
case "${1:-}" in
  "")
    ;;
  --check)
    CHECK_ONLY=true
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    printf 'error: unknown argument: %s\n' "$1" >&2
    usage >&2
    exit 2
    ;;
esac

if (( $# > 1 )); then
  printf 'error: expected at most one argument\n' >&2
  usage >&2
  exit 2
fi

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

check_platform_version() {
  local version major minor

  if [[ "$PLATFORM" == "Linux" ]]; then
    version="$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
    version="${version##* }"
    if [[ ! "$version" =~ ^([0-9]+)\.([0-9]+) ]]; then
      printf 'error: unable to determine glibc version; glibc %s.%s or newer is required\n' \
        "$MIN_GLIBC_MAJOR" "$MIN_GLIBC_MINOR" >&2
      exit 1
    fi
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    if (( major < MIN_GLIBC_MAJOR || (major == MIN_GLIBC_MAJOR && minor < MIN_GLIBC_MINOR) )); then
      printf 'error: glibc %s is unsupported; version %s.%s or newer is required\n' \
        "$version" "$MIN_GLIBC_MAJOR" "$MIN_GLIBC_MINOR" >&2
      exit 1
    fi
    return
  fi

  version="$(sw_vers -productVersion 2>/dev/null || true)"
  major="${version%%.*}"
  if [[ ! "$major" =~ ^[0-9]+$ ]] || (( major < MIN_MACOS_MAJOR )); then
    printf 'error: macOS %s is unsupported; macOS %s or newer is required\n' \
      "${version:-unknown}" "$MIN_MACOS_MAJOR" >&2
    exit 1
  fi
}

download_to() {
  local url="$1" destination="$2"

  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error "$url" --output "$destination"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --output-document="$destination" "$url"
  else
    printf 'error: curl or wget is required to install missing tools\n' >&2
    exit 1
  fi
}

install_uv() {
  local installer
  installer="$(mktemp "${TMPDIR:-/tmp}/cadflow-uv-install.XXXXXX")"
  trap 'rm -f "$installer"' RETURN

  printf 'Installing uv...\n'
  download_to "https://astral.sh/uv/install.sh" "$installer"
  sh "$installer"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

  trap - RETURN
  rm -f "$installer"
}

node_version_is_compatible() {
  local version major minor

  command -v node >/dev/null 2>&1 || return 1
  version="$(node -p 'process.versions.node' 2>/dev/null || true)"
  IFS=. read -r major minor _ <<<"$version"
  [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || return 1

  (( (major == 20 && minor >= 19) || (major == 22 && minor >= 12) || major > 22 ))
}

install_node() {
  local installer volta_command volta_home
  installer="$(mktemp "${TMPDIR:-/tmp}/cadflow-volta-install.XXXXXX")"
  trap 'rm -f "$installer"' RETURN

  if command -v volta >/dev/null 2>&1; then
    volta_command="$(command -v volta)"
  else
    volta_home="${VOLTA_HOME:-$HOME/.volta}"
    printf 'Installing Volta...\n'
    download_to "https://get.volta.sh" "$installer"
    bash "$installer"
    export VOLTA_HOME="$volta_home"
    export PATH="$VOLTA_HOME/bin:$PATH"
    volta_command="$VOLTA_HOME/bin/volta"
  fi

  printf 'Installing Node.js %s...\n' "$PROJECT_NODE_VERSION"
  "$volta_command" install "node@$PROJECT_NODE_VERSION"

  trap - RETURN
  rm -f "$installer"
}

check_platform_version
printf 'Detected %s/%s; using Python %s\n' \
  "$PLATFORM" "$ARCHITECTURE" "$PROJECT_PYTHON"

if [[ "$CHECK_ONLY" == true ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    printf 'error: uv is not installed\n' >&2
    exit 1
  fi
  if ! node_version_is_compatible; then
    printf 'error: Node.js ^20.19 or >=22.12 is required\n' >&2
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    printf 'error: npm is not installed\n' >&2
    exit 1
  fi
  printf 'Environment check passed: %s, Node.js %s, npm %s\n' \
    "$(uv --version)" "$(node --version)" "$(npm --version)"
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  install_uv
fi
if ! node_version_is_compatible || ! command -v npm >/dev/null 2>&1; then
  install_node
fi
if ! command -v npm >/dev/null 2>&1; then
  printf 'error: npm was not installed with Node.js\n' >&2
  exit 1
fi

cd "$SCRIPT_DIR"

printf 'Syncing Python %s dependencies...\n' "$PROJECT_PYTHON"
uv sync --locked --group dev --python "$PROJECT_PYTHON"

printf 'Installing Viewer dependencies...\n'
npm --prefix "$SCRIPT_DIR/viewer" ci

if [[ ! -e "$SCRIPT_DIR/.env" ]]; then
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  printf 'Created .env from .env.example; add your model provider credentials before running the app.\n'
else
  printf 'Keeping existing .env unchanged.\n'
fi

printf 'Setup complete. Run ./run.sh to start CadFlowAgent.\n'
