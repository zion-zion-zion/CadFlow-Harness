#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SITE_DIR="$REPO_ROOT/site"

usage() {
  cat <<'EOF'
Usage: ./scripts/docs.sh <build|serve|clean>

Commands:
  build  Build English and Chinese sites in strict mode.
  serve  Build both sites, then serve the combined site locally.
  clean  Remove the generated site directory.
EOF
}

clean_site() {
  if [[ -d "$SITE_DIR" ]]; then
    find "$SITE_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  fi
}

build_sites() {
  cd "$REPO_ROOT"
  mkdir -p "$SITE_DIR"
  uv run --locked zensical build --clean --strict --config-file "$REPO_ROOT/zensical.toml"
  uv run --locked zensical build --clean --strict --config-file "$REPO_ROOT/zensical.zh.toml"
  mkdir -p "$SITE_DIR/assets"
  cp "$REPO_ROOT/docs/root/index.html" "$SITE_DIR/index.html"
  cp "$REPO_ROOT/docs/assets/cadflow-harness-logo.png" "$SITE_DIR/assets/cadflow-harness-logo.png"
}

command_name="${1:-}"
case "$command_name" in
  build)
    clean_site
    build_sites
    ;;
  serve)
    clean_site
    build_sites
    cd "$SITE_DIR"
    exec python -m http.server "${DOCS_PORT:-8000}" --bind "${DOCS_HOST:-127.0.0.1}"
    ;;
  clean)
    clean_site
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
