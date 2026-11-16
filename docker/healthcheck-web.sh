#!/bin/sh
# Web readiness.
#
# With DEBUGPY_WAIT=1 the server does not start until a debugger attaches, so
# HTTP would never answer and `docker compose up --wait` would stall forever.
# In that mode "ready" means the debug adapter is accepting connections.
#
# Otherwise probe /readyz (hub UPS-BE-10): it answers 200 only while the
# database answers SELECT 1 (and Redis a ping, once the celery profile turns
# eager mode off), so a dead Postgres turns the container unhealthy instead of
# hiding behind a live process. A 503 prints the failing checks, which
# `docker inspect` keeps in the container's health log.
set -e

if [ "${DEBUGPY_ENABLE:-0}" = "1" ] && [ "${DEBUGPY_WAIT:-0}" = "1" ]; then
    exec python -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(s.connect_ex(('127.0.0.1', ${DEBUGPY_PORT:-5678})))"
fi

exec python -c "
import json, sys, urllib.error, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:${DJANGO_PORT:-8000}/readyz', timeout=5) as r:
        body = r.read()
except urllib.error.HTTPError as e:
    body = e.read()
sys.exit(0 if json.loads(body).get('status') == 'ok' else 'not ready: ' + body.decode())
"
