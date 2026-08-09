#!/bin/sh
# Development web server.
#
# DEBUGPY_ENABLE=1 runs Django under the debug adapter with --noreload:
# Django's autoreloader forks a child that serves the requests, while debugpy
# would stay attached to the parent, so breakpoints would never be hit. The
# trade is autoreload; restart the container (or the "docker: restart web"
# task) to pick up changes while debugging.
#
# DEBUGPY_WAIT=1 blocks startup until VS Code attaches — use it to debug
# anything that runs during boot (AppConfig.ready, settings, migrations).
set -e

PORT="${DJANGO_PORT:-8000}"
DBG_PORT="${DEBUGPY_PORT:-5678}"

if [ "${DEBUGPY_ENABLE:-0}" = "1" ]; then
    if [ "${DEBUGPY_WAIT:-0}" = "1" ]; then
        echo "==> debugpy: waiting for client on 0.0.0.0:${DBG_PORT}"
        exec python -Xfrozen_modules=off -m debugpy --listen "0.0.0.0:${DBG_PORT}" --wait-for-client \
            manage.py runserver "0.0.0.0:${PORT}" --noreload
    fi
    echo "==> debugpy: listening on 0.0.0.0:${DBG_PORT} (autoreload off)"
    exec python -Xfrozen_modules=off -m debugpy --listen "0.0.0.0:${DBG_PORT}" \
        manage.py runserver "0.0.0.0:${PORT}" --noreload
fi

echo "==> runserver with autoreload (no debugger)"
exec python manage.py runserver "0.0.0.0:${PORT}"
