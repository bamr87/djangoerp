#!/bin/sh
# Celery worker liveness.
#
# While debugging, "ready" means the debug adapter is listening: the solo pool
# is single-threaded, so `celery inspect ping` times out whenever the worker is
# parked on a breakpoint and the container would flap unhealthy mid-session.
set -e

if [ "${DEBUGPY_ENABLE:-0}" = "1" ]; then
    exec python -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(s.connect_ex(('127.0.0.1', ${DEBUGPY_PORT:-5678})))"
fi

exec celery -A djangoerp inspect ping -d "celery@$(hostname)" >/dev/null
