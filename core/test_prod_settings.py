"""
Production settings refuse a guessable SECRET_KEY and pass Django's deployment checklist.

djangoerp.settings picks dev or prod once, at import, so each case runs in a subprocess with
DJANGO_SETTINGS_MODULE=djangoerp.settings.prod. Every variable the outcome depends on is set explicitly, because
settings also read the repo's .env for any variable the environment leaves unset.
"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parent.parent

REFUSED = "ImproperlyConfigured: SECRET_KEY is empty or a 'django-insecure' placeholder"

# Loads settings with read_env stubbed out, so a developer's .env cannot supply a key the case leaves unset.
SETUP_WITHOUT_DOTENV = 'import environ; environ.Env.read_env = lambda *a, **k: None; import django; django.setup()'


def run_prod(args, **env):
    """Run `python <args>` in the repo root under prod settings; SECRET_KEY is unset unless `env` sets it."""
    environment = {key: value for key, value in os.environ.items() if key != 'SECRET_KEY'}
    environment |= {
        'DJANGO_SETTINGS_MODULE': 'djangoerp.settings.prod',
        'DATABASE_URL': 'sqlite:///:memory:',
        'ALLOWED_HOSTS': 'erp.example.com',
        'SECURE_SSL_REDIRECT': 'True',
        **env,
    }
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120
    )


class ProdSettingsTests(SimpleTestCase):
    def test_an_unset_secret_key_is_refused(self):
        result = run_prod(['-c', SETUP_WITHOUT_DOTENV])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImproperlyConfigured: Set the SECRET_KEY environment variable', result.stderr)

    def test_an_empty_or_placeholder_secret_key_is_refused(self):
        for key in ('', '   ', 'django-insecure-change-me', 'django-insecure-x'):
            with self.subTest(key=key):
                result = run_prod(['manage.py', 'check'], SECRET_KEY=key)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(REFUSED, result.stderr)

    def test_a_strong_key_passes_check_deploy_with_no_warnings(self):
        command = ['manage.py', 'check', '--deploy', '--fail-level', 'WARNING']
        result = run_prod(command, SECRET_KEY=secrets.token_urlsafe(50))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('System check identified no issues', result.stdout)
