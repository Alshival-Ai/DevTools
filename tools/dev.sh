#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Ensure host bind-mounted entrypoint stays runnable in dev containers.
chmod +x "$ROOT_DIR/docker/entrypoint.sh"

export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
export LOCAL_USERNAME="$(id -un)"
export LOCAL_HOME="${HOME}"

PRUNE=false
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--prune" ]; then
    PRUNE=true
    continue
  fi
  ARGS+=("$arg")
done

remove_path() {
  local target="$1"
  [ -e "$target" ] || return 0
  if rm -rf "$target" 2>/dev/null; then
    return 0
  fi
  sudo -n rm -rf "$target"
}

if [ "$PRUNE" = "true" ]; then
  docker compose \
    -f docker-compose.yml \
    -f docker-compose.dev.yml \
    down --volumes --remove-orphans || true
  docker system prune -a --volumes -f
  remove_path "$ROOT_DIR/var"
  remove_path "$ROOT_DIR/db.sqlite3"
fi

mkdir -p "$ROOT_DIR/var"
if [ ! -w "$ROOT_DIR/var" ]; then
  sudo -n chown -R "$LOCAL_UID:$LOCAL_GID" "$ROOT_DIR/var"
fi

exec docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up "${ARGS[@]}"
