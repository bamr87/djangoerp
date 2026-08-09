#!/bin/sh
# Celery worker.
#
# --pool=solo when debugging: the default prefork pool forks the task into a
# child process the debugger is not attached to. Reports
# (apps/reports/report_generators.py) and the MRP engine (apps/mrp/engine.py) are the
# tasks worth breakpointing.
#
# Note dev settings default CELERY_TASK_ALWAYS_EAGER to True, which runs tasks
# in the web process instead of dispatching them here. The `celery` compose
# profile sets it False on both services so this worker actually receives work.
set -e

DBG_PORT="${DEBUGPY_PORT:-5678}"
LOGLEVEL="${CELERY_LOGLEVEL:-info}"

if [ "${DEBUGPY_ENABLE:-0}" = "1" ]; then
    echo "==> debugpy: listening on 0.0.0.0:${DBG_PORT} (celery, solo pool)"
    exec python -Xfrozen_modules=off -m debugpy --listen "0.0.0.0:${DBG_PORT}" \
        -m celery -A djangoerp worker --loglevel="${LOGLEVEL}" --pool=solo
fi

exec celery -A djangoerp worker --loglevel="${LOGLEVEL}"
