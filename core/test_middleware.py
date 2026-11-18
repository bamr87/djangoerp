"""
core.middleware.RequestIdMiddleware (hub specs/BACKEND.md UPS-BE-04): every response carries X-Request-Id and
X-App-Version, whatever produced it.
"""

from django.conf import settings
from django.test import TestCase, override_settings


class RequestIdMiddlewareTests(TestCase):
    def assertTraced(self, response, request_id=None):
        if request_id is None:
            self.assertRegex(response['X-Request-Id'], r'^[0-9a-f]{32}$')  # a fresh uuid4().hex
        else:
            self.assertEqual(response['X-Request-Id'], request_id)
        self.assertEqual(response['X-App-Version'], settings.APP_VERSION)

    def test_html_json_and_api_error_responses_carry_both_headers(self):
        for path, status in (('/', 200), ('/healthz', 200), ('/api/company/companies/', 401)):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, status)
                self.assertTraced(response)

    def test_a_well_formed_inbound_id_is_echoed(self):
        for inbound in ('0f8fad5b-d9cb-469f-a165-70867728950e', 'b2c6f3a1e4d5', 'Zm9vYmFy+/=', 'x' * 200):
            with self.subTest(inbound=inbound[:40]):
                response = self.client.get('/api/company/companies/', headers={'X-Request-Id': inbound})
                self.assertEqual(response.status_code, 401)
                self.assertTraced(response, inbound)

    def test_a_malformed_inbound_id_is_replaced_not_echoed(self):
        for inbound in ('', 'has spaces', '<script>alert(1)</script>', 'abc\r\nSet-Cookie: session=x', 'x' * 201):
            with self.subTest(inbound=inbound[:40]):
                response = self.client.get('/healthz', headers={'X-Request-Id': inbound})
                self.assertEqual(response.status_code, 200)
                self.assertTraced(response)

    def test_every_request_gets_its_own_id_and_the_view_sees_it(self):
        first, second = self.client.get('/healthz'), self.client.get('/healthz')
        self.assertNotEqual(first['X-Request-Id'], second['X-Request-Id'])
        self.assertEqual(first.wsgi_request.request_id, first['X-Request-Id'])

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_https_redirects_carry_them_too(self):
        # SecurityMiddleware answers this before any view runs; the middleware comes first, so it still stamps it.
        response = self.client.get('/healthz', headers={'X-Request-Id': 'lb-7f3a'})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], 'https://testserver/healthz')
        self.assertTraced(response, 'lb-7f3a')
