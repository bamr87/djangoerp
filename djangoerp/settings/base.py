import os
from datetime import timedelta
from pathlib import Path

import environ

env = environ.Env()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

environ.Env.read_env(os.path.join(BASE_DIR, '.env'), overwrite=False)

# A development and test fallback only: settings/prod.py reads SECRET_KEY with no default and refuses this value.
SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-me')

DEBUG = False

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'django_filters',
    'corsheaders',
    'drf_yasg',
    # Local — financial core
    'core',
    'company',
    'accounts',
    'coa',
    'journal',
    'invoices',
    'reports',
    # Local — supply chain, manufacturing, planning
    'partners',
    'products',
    'inventory',
    'purchasing',
    'sales',
    'manufacturing',
    'mrp',
]

MIDDLEWARE = [
    # First, so its X-Request-Id / X-App-Version reach every response, redirects and static files included.
    'core.middleware.RequestIdMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Served by /health/ and /version. release-please rewrites it in each release PR (release-please-config.json
# extra-files), so the running version cannot drift from the release tag.
APP_VERSION = '0.2.0'  # x-release-please-version

# Build identity for /version (UPS-BE-11). The prod image build sets both (Dockerfile build args); when they are
# unset, /version reports null rather than a guess.
GIT_SHA = env.str('GIT_SHA', default='') or None
BUILT_AT = env.str('BUILT_AT', default='') or None

# The fleet "Improve this page" widget (templates/includes/fleet-feedback.html) files issues against this repo and
# branch; company.context_processors.fleet hands both, with APP_VERSION, to every template.
FLEET_REPO = 'bamr87/djangoerp'
FLEET_BRANCH = env('FLEET_BRANCH', default='main')

ROOT_URLCONF = 'djangoerp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'company.context_processors.fleet',
            ],
        },
    },
]

WSGI_APPLICATION = 'djangoerp.wsgi.application'

DATABASES = {
    'default': env.db('DATABASE_URL', default='sqlite:///db.sqlite3')
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
# public/ holds the fleet feedback kit's scripts: the hub's `tools/fanout.sh --kit feedback` vendors them there and
# upgrades them in place, so they stay put. The 'js' prefix serves them where the kit's Django include asks for
# them, static/js/fleet-feedback.js and static/js/fleet-feedback-capture.js.
STATICFILES_DIRS = [('js', BASE_DIR / 'public')]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    # DRF's handler plus a 409 for a delete a PROTECT/RESTRICT foreign key refuses (else a 500).
    'EXCEPTION_HANDLER': 'core.exceptions.exception_handler',
}

# JWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=env.int('JWT_ACCESS_TOKEN_LIFETIME_MINUTES', default=60)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=env.int('JWT_REFRESH_TOKEN_LIFETIME_DAYS', default=7)),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# Swagger
SWAGGER_SETTINGS = {
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
            'description': 'JWT auth. Use: Bearer <access-token>',
        }
    },
    # /swagger/#/<tag> opens that tag's section; the landing page links each /api/<area>/ prefix there
    # (company.views.API_AREAS).
    'DEEP_LINKING': True,
}

# Celery
CELERY_BROKER_URL = env('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('REDIS_URL', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = env.bool('CELERY_TASK_ALWAYS_EAGER', default=False)
CELERY_TASK_EAGER_PROPAGATES = env.bool('CELERY_TASK_EAGER_PROPAGATES', default=False)

# CORS
CORS_ALLOWED_ORIGINS = env.list(
    'CORS_ALLOWED_ORIGINS',
    default=['http://localhost:3000', 'http://127.0.0.1:3000'],
)
