#!/usr/bin/env bash
set -euo pipefail

export SQLITE_PATH="${SQLITE_PATH:-/app/var/db.sqlite3}"
export USER_DATA_ROOT="${USER_DATA_ROOT:-/app/var/user_data}"
export STATIC_ROOT="${STATIC_ROOT:-/app/var/staticfiles}"
export TEAM_DATA_ROOT="${TEAM_DATA_ROOT:-/app/var/team_data}"
export GLOBAL_DATA_ROOT="${GLOBAL_DATA_ROOT:-/app/var/global_data}"

mkdir -p "$(dirname "$SQLITE_PATH")" "$USER_DATA_ROOT" "$TEAM_DATA_ROOT" "$GLOBAL_DATA_ROOT" "$STATIC_ROOT"

normalize_var_permissions() {
  local var_root runtime_uid runtime_gid
  var_root="$(dirname "$SQLITE_PATH")"
  runtime_uid="$(id -u)"
  runtime_gid="$(id -g)"

  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n chown -R "${runtime_uid}:${runtime_gid}" "$var_root" || true
    sudo -n find "$var_root" -type d -exec chmod u+rwx {} + || true
    sudo -n find "$var_root" -type f -name '*.db' -exec chmod u+rw {} + || true
    return
  fi

  find "$var_root" -type d -exec chmod u+rwx {} + || true
  find "$var_root" -type f -name '*.db' -exec chmod u+rw {} + || true
}

normalize_var_permissions

if [ "${RUN_MIGRATE:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi
if [ "${RUN_USER_HOME_MIGRATION:-${RUN_MIGRATE:-1}}" = "1" ]; then
  migration_cmd=(python manage.py migrate_user_home_data)
  if [ "${RUN_USER_HOME_MIGRATION_DRY_RUN:-0}" = "1" ]; then
    migration_cmd+=(--dry-run)
  fi
  if [ "${RUN_USER_HOME_MIGRATION_SKIP_PRUNE:-0}" = "1" ]; then
    migration_cmd+=(--skip-prune)
  fi
  if [ "${RUN_USER_HOME_MIGRATION_FINALIZE:-0}" = "1" ]; then
    migration_cmd+=(--finalize)
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n "${migration_cmd[@]}"
  else
    "${migration_cmd[@]}"
  fi
fi
if [ "${RUN_COLLECTSTATIC:-1}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

normalize_var_permissions

exec "$@"
