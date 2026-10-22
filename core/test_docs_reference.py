"""
The generated route reference must match the URLconf (UPS-DOC-05).

tools/gen_api_routes.py renders docs/reference/api-routes.md from Django's URL
resolver. This test re-renders it and fails when the committed file is stale;
the fix is always `python3 tools/gen_api_routes.py --write`, never a hand edit.
Runs under both `python manage.py test` and `python -m pytest`.
"""
import importlib.util
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parent.parent


def _generator():
    spec = importlib.util.spec_from_file_location('gen_api_routes', ROOT / 'tools' / 'gen_api_routes.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GeneratedRouteReferenceTests(SimpleTestCase):
    def test_reference_is_current(self):
        self.assertFalse(
            _generator().stale(ROOT),
            'docs/reference/api-routes.md is stale — run `python3 tools/gen_api_routes.py --write` and commit it',
        )

    def test_every_app_router_is_listed(self):
        text = _generator().render()
        for needle in ('`/api/auth/login/`', '`/api/invoices/invoices/{pk}/post/`', '`/api/mrp/runs/{pk}/convert/`',
                       '`/health/`'):
            self.assertIn(needle, text)
