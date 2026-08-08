from .base import *  # noqa: F401,F403,F405

DEBUG = True

# SQLite by default for local development; set DATABASE_URL to override.
DATABASES = {
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')  # noqa: F405
}

# Run Celery tasks synchronously in development so report generation doesn't
# require a running worker/broker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
