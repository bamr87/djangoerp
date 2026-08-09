#!/bin/sh
# Web liveness.
#
# With DEBUGPY_WAIT=1 the server does not start until a debugger attaches, so
# HTTP would never answer and `docker compose up --wait` would stall forever.
# In that mode "ready" means the debug adapter is accepting connections.
#
# Otherwise probe company's /health/ — an unauthenticated JsonResponse that
# touches no models, which is exactly what a liveness check wants.
set -e

if [ "${DEBUGPY_ENABLE:-0}" = "1" ] && [ "${DEBUGPY_WAIT:-0}" = "1" ]; then
    exec python -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(s.connect_ex(('127.0.0.1', ${DEBUGPY_PORT:-5678})))"
fi

exec python -c "
import json, sys, urllib.request
with urllib.request.urlopen('http://127.0.0.1:${DJANGO_PORT:-8000}/health/', timeout=5) as r:
    sys.exit(0 if json.load(r).get('status') == 'ok' else 1)
"
