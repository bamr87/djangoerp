from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403,F405

DEBUG = False

# No default: base.py's fallback is a public 'django-insecure-' key, and SECRET_KEY signs sessions, password-reset
# links and every JWT (SimpleJWT's SIGNING_KEY defaults to it). An unset variable already raises
# ImproperlyConfigured (django-environ); an empty one or a 'django-insecure' placeholder raises here, so production
# never starts on a guessable key. No length rule: the Dockerfile's prod stage runs collectstatic with a throwaway
# SECRET_KEY=build-time-only.
SECRET_KEY = env('SECRET_KEY')  # noqa: F405
if not SECRET_KEY.strip() or SECRET_KEY.lstrip().startswith('django-insecure'):
    raise ImproperlyConfigured(
        "SECRET_KEY is empty or a 'django-insecure' placeholder; production needs a strong private key. "
        'Generate one with: python3 -c "import secrets; print(secrets.token_urlsafe(50))"'
    )

# Enforce these in production via environment
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
