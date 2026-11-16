from .base import *  # noqa: F401,F403,F405

DEBUG = True

# Requests from these addresses get Django's `debug` template variable, which the feedback widget reports as
# env="development". Under docker compose a browser request arrives from the bridge gateway instead, so the widget
# reports "production" there.
INTERNAL_IPS = ['127.0.0.1', '::1']

# SQLite by default for local development; set DATABASE_URL to override.
DATABASES = {
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')  # noqa: F405
}

# Run Celery tasks synchronously in development so report generation doesn't
# require a running worker/broker. Overridable so the Docker stack's `celery`
# profile can dispatch to a real worker (docker-compose.yml) without switching
# to prod settings; the default is unchanged.
CELERY_TASK_ALWAYS_EAGER = env.bool('CELERY_TASK_ALWAYS_EAGER', default=True)  # noqa: F405
CELERY_TASK_EAGER_PROPAGATES = env.bool('CELERY_TASK_EAGER_PROPAGATES', default=True)  # noqa: F405
