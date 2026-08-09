# syntax=docker/dockerfile:1

# DjangoERP container image.
#
#   base -> dev   : runserver/celery under debugpy, source bind-mounted (compose)
#   base -> prod  : gunicorn + whitenoise, source baked in
#
# Python 3.12 matches the requirements.txt floor and the shared hub CI matrix;
# Django 6.1 dropped 3.11.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=djangoerp.settings.dev

WORKDIR /app

# psycopg2-binary ships manylinux wheels, so no libpq/build-essential needed.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY docker/ /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh

COPY . .


# ---------------------------------------------------------------------------
# dev — adds the debug adapter. Not in requirements.txt on purpose: that file
# is the pinned production dependency set.
# ---------------------------------------------------------------------------
FROM base AS dev

RUN python -m pip install debugpy==1.8.21

EXPOSE 8000 5678
ENTRYPOINT ["entrypoint.sh"]
CMD ["dev-server.sh"]


# ---------------------------------------------------------------------------
# prod — gunicorn behind whitenoise, static collected at build time.
# ---------------------------------------------------------------------------
FROM base AS prod

ENV DJANGO_SETTINGS_MODULE=djangoerp.settings.prod

RUN SECRET_KEY=build-time-only \
    DATABASE_URL=sqlite:///build.sqlite3 \
    python manage.py collectstatic --noinput \
    && rm -f build.sqlite3

EXPOSE 8000
ENTRYPOINT ["entrypoint.sh"]
CMD ["gunicorn", "djangoerp.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
