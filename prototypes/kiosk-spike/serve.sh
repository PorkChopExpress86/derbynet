#!/bin/bash
# SPIKE #71: runs the Django skeleton plus the prototypes on PORT (default
# 8771), with the test endpoints on, against the SQLite database in var/.
#   prototypes/kiosk-spike/serve.sh [port]
# Then load the fixture with tools/fixture.sh http://127.0.0.1:PORT
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$HERE/../../server"
PORT="${1:-8771}"
mkdir -p "$HERE/var/data" "$HERE/var/config"
export DERBYNET_DATA_DIR="$HERE/var/data" DERBYNET_CONFIG_DIR="$HERE/var/config"
export DERBYNET_ENABLE_TEST_ENDPOINTS=1
export DJANGO_SETTINGS_MODULE=spike_settings
export PYTHONPATH="$HERE:$SERVER"
cd "$SERVER"
uv run python manage.py migrate -v0
uv run python manage.py create_test_event >/dev/null 2>&1 || true
exec uv run python manage.py runserver "0.0.0.0:$PORT" --noreload --insecure
