#!/usr/bin/env bash
set -euo pipefail

export SQLITE_PATH="${SQLITE_PATH:-/app/var/db.sqlite3}"
export USER_DATA_ROOT="${USER_DATA_ROOT:-/app/var/user_data}"
export STATIC_ROOT="${STATIC_ROOT:-/app/var/staticfiles}"

mkdir -p "$(dirname "$SQLITE_PATH")" "$USER_DATA_ROOT" "$STATIC_ROOT"

if [ "${RUN_MIGRATE:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi
if [ "${RUN_COLLECTSTATIC:-1}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
