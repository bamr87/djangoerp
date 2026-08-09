#!/bin/sh
# Run a one-shot command under debugpy and block until VS Code attaches.
#
#   debug-command.sh manage.py test
#   debug-command.sh manage.py demo_erp
#   debug-command.sh -m pytest
#
# Driven by the "docker: debug ..." tasks in .vscode/tasks.json; attach with
# the "Docker: Attach to one-shot command" launch configuration.
set -e

DBG_PORT="${DEBUGPY_PORT:-5678}"

echo "==> debugpy: waiting for client on 0.0.0.0:${DBG_PORT}"
echo "==> attach with: Docker: Attach to one-shot command"
exec python -Xfrozen_modules=off -m debugpy --listen "0.0.0.0:${DBG_PORT}" --wait-for-client "$@"
