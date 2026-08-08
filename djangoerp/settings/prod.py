from .base import *  # noqa: F401,F403,F405

DEBUG = False

# Enforce these in production via environment
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
