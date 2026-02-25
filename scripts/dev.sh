#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# Load .env for local defaults, but keep any already-exported shell values.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
source .venv/bin/activate
python3 manage.py collectstatic --noinput
python3 manage.py makemigrations
python3 manage.py migrate
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
uvicorn alshival.asgi:application --reload --host "${HOST}" --port "${PORT}"
