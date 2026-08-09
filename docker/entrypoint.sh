#!/bin/sh
# Container entrypoint.
#
# Startup ordering is handled by compose healthchecks (`depends_on:
# condition: service_healthy`), so there is no wait-for-db loop here — by the
# time this runs, Postgres is accepting connections.
#
# Set RUN_MIGRATIONS=0 for containers that must not touch the schema: the
# Celery worker (it would race the web container) and one-shot test runs
# (Django builds its own test database).
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "==> applying migrations"
    python manage.py migrate --noinput

    # Same gate as migrations: only the container that owns the schema seeds
    # the admin account. Needs DJANGO_SUPERUSER_USERNAME/_PASSWORD; no-ops
    # otherwise, and never overwrites an existing account's password.
    if [ "${BOOTSTRAP_ADMIN:-1}" = "1" ]; then
        python /usr/local/bin/bootstrap-admin.py
    fi
fi

exec "$@"
