#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Ensure host bind-mounted entrypoint stays runnable in containers.
chmod +x "$ROOT_DIR/docker/entrypoint.sh"

find_docker() {
  local cand resolved
  for cand in "${DOCKER_BIN:-}" docker /usr/bin/docker /usr/local/bin/docker /snap/bin/docker; do
    [ -n "$cand" ] || continue

    # If a command name is provided (e.g. "docker"), resolve it from PATH.
    if [[ "$cand" != */* ]]; then
      resolved="$(type -P "$cand" 2>/dev/null || true)"
      [ -n "$resolved" ] || continue
    else
      resolved="$cand"
    fi

    [ -f "$resolved" ] || continue
    if [ -x "$resolved" ]; then
      echo "$resolved"
      return 0
    fi
  done
  return 1
}

DOCKER="$(find_docker)" || {
  echo "Error: docker CLI not found (or resolved to a directory)." >&2
  echo "If docker is installed, set DOCKER_BIN to its full path (e.g. /usr/bin/docker)." >&2
  exit 1
}

export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
export CERTBOT_DOMAIN="${CERTBOT_DOMAIN:-dev.alshival.dev}"
export CERTBOT_EMAIL="${CERTBOT_EMAIL:-dev@alshival.dev}"
export CERTBOT_CERT_NAME="${CERTBOT_CERT_NAME:-$CERTBOT_DOMAIN}"

PRUNE=false
BUILD=true
DETACH=true
WAIT=true
DOWN=false
RESTART=false
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --down)
      DOWN=true
      ;;
    --restart)
      RESTART=true
      ;;
    --prune)
      PRUNE=true
      ;;
    --build)
      BUILD=true
      ;;
    --no-build)
      BUILD=false
      ;;
    --attach)
      DETACH=false
      WAIT=false
      ;;
    -d|--detach|--detached)
      DETACH=true
      ;;
    --no-wait)
      WAIT=false
      ;;
    *)
      ARGS+=("$arg")
      ;;
  esac
done

compose_https_files=(
  -f docker-compose.yml
  -f docker-compose-https.yml
)

run_compose_https() {
  "$DOCKER" compose "${compose_https_files[@]}" "$@"
}

if [ "$DOWN" = "true" ] || [ "$RESTART" = "true" ]; then
  run_compose_https down --remove-orphans || true
  if [ "$DOWN" = "true" ] && [ "$PRUNE" != "true" ]; then
    exit 0
  fi
fi

remove_path() {
  local target="$1"
  [ -e "$target" ] || return 0
  if rm -rf "$target" 2>/dev/null; then
    return 0
  fi
  sudo -n rm -rf "$target"
}

if [ "$PRUNE" = "true" ]; then
  run_compose_https down --volumes --remove-orphans || true
  "$DOCKER" system prune -a --volumes -f
  remove_path "$ROOT_DIR/var"
  remove_path "$ROOT_DIR/db.sqlite3"
fi

mkdir -p "$ROOT_DIR/var"
if [ ! -w "$ROOT_DIR/var" ]; then
  sudo -n chown -R "$LOCAL_UID:$LOCAL_GID" "$ROOT_DIR/var"
fi

CERTBOT_DIR="$ROOT_DIR/docker/certbot"

cert_path_exists() {
  local p="$1"
  [ -f "$p" ] || [ -L "$p" ]
}

detect_existing_cert_name() {
  local live_dir candidate picked
  live_dir="$CERTBOT_DIR/conf/live"
  [ -d "$live_dir" ] || return 1

  # Certbot may create suffixes like domain-0001 when reissuing.
  shopt -s nullglob
  for candidate in "$live_dir/${CERTBOT_DOMAIN}"*; do
    [ -d "$candidate" ] || continue
    cert_path_exists "$candidate/fullchain.pem" || continue
    picked="$candidate"
  done
  shopt -u nullglob

  [ -n "${picked:-}" ] || return 1
  basename "$picked"
}

if detected_cert_name="$(detect_existing_cert_name)"; then
  export CERTBOT_CERT_NAME="$detected_cert_name"
fi
CERT_PATH="$CERTBOT_DIR/conf/live/$CERTBOT_CERT_NAME/fullchain.pem"

if [ -d "$CERTBOT_DIR" ]; then
  if [ ! -w "$CERTBOT_DIR" ]; then
    sudo -n chown -R "$LOCAL_UID:$LOCAL_GID" "$CERTBOT_DIR" || true
  fi
fi

if ! cert_path_exists "$CERT_PATH"; then
  mkdir -p "$CERTBOT_DIR/www" "$CERTBOT_DIR/conf"
  if [ ! -w "$CERTBOT_DIR/conf" ]; then
    sudo -n chown -R "$LOCAL_UID:$LOCAL_GID" "$CERTBOT_DIR" || true
  fi
  echo "No TLS cert found for $CERTBOT_DOMAIN. Fetching via certbot..." >&2

  "$DOCKER" compose \
    -f docker-compose.yml \
    -f docker-compose-http.yml \
    up -d web nginx-http

  "$DOCKER" run --rm \
    -v "$CERTBOT_DIR/conf:/etc/letsencrypt" \
    -v "$CERTBOT_DIR/www:/var/www/certbot" \
    certbot/certbot:latest certonly \
    --webroot -w /var/www/certbot \
    -d "$CERTBOT_DOMAIN" \
    --email "$CERTBOT_EMAIL" \
    --agree-tos \
    --no-eff-email \
    --keep-until-expiring \
    --non-interactive

  "$DOCKER" compose \
    -f docker-compose.yml \
    -f docker-compose-http.yml \
    down

  if detected_cert_name="$(detect_existing_cert_name)"; then
    export CERTBOT_CERT_NAME="$detected_cert_name"
    CERT_PATH="$CERTBOT_DIR/conf/live/$CERTBOT_CERT_NAME/fullchain.pem"
  fi
fi

if ! cert_path_exists "$CERT_PATH"; then
  echo "Error: TLS cert not found at $CERT_PATH (CERTBOT_CERT_NAME=$CERTBOT_CERT_NAME)." >&2
  exit 1
fi

compose_up_cmd=(
  "$DOCKER" compose "${compose_https_files[@]}"
  up
  --remove-orphans
)

if [ "$BUILD" = "true" ]; then
  compose_up_cmd+=(--build)
fi
if [ "$DETACH" = "true" ]; then
  compose_up_cmd+=(-d)
fi

if [ "$WAIT" = "true" ] && [ "$DETACH" = "true" ]; then
  if "$DOCKER" compose up --help 2>/dev/null | grep -q -- "--wait"; then
    compose_up_cmd+=(--wait)
  fi
fi

compose_up_cmd+=("${ARGS[@]}")
exec "${compose_up_cmd[@]}"
