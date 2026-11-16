import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user

from .models import Company


class HealthEndpointTests(TestCase):
    def test_health_returns_200_and_json_contract(self):
        response = self.client.get(reverse('company:health'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(sorted(payload), ['app', 'debug', 'status', 'version'])
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['app'], 'djangoerp')
        self.assertEqual(payload['version'], settings.APP_VERSION)


class HomeViewTests(TestCase):
    def test_home_url_reverses_to_root(self):
        self.assertEqual(reverse('company:home'), '/')

    def test_home_renders_without_a_company(self):
        response = self.client.get(reverse('company:home'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'company/home.html')
        self.assertContains(response, 'No company on file')

    def test_home_shows_company_name(self):
        Company.objects.create(name='Northwind Trading')
        response = self.client.get(reverse('company:home'))
        self.assertContains(response, 'Northwind Trading')

    def test_home_ignores_inactive_companies(self):
        Company.objects.create(name='Dissolved Co', is_active=False)
        response = self.client.get(reverse('company:home'))
        self.assertContains(response, 'No company on file')


class FeedbackWidgetTests(TestCase):
    """The fleet feedback kit (public/*.js, templates/includes/) is mounted by company/base.html."""

    def home_html(self):
        return self.client.get(reverse('company:home')).content.decode()

    def test_widget_files_reports_against_this_repo(self):
        html = self.home_html()
        self.assertIn('<fleet-feedback', html)
        self.assertIn('repo="bamr87/djangoerp"', html)
        self.assertIn('branch="main"', html)
        self.assertIn('app-version="%s"' % settings.APP_VERSION, html)
        self.assertIn('<script src="/static/js/fleet-feedback.js" defer></script>', html)
        self.assertIn('issues/new?template=page_feedback.yml', html)  # the no-JS link
        self.assertGreater(html.index('<fleet-feedback'), html.index('</main>'))

    def test_capture_buffer_is_the_first_script(self):
        html = self.home_html()
        scripts = re.findall(r'<script\b[^>]*>', html)
        self.assertEqual(scripts[0], '<script src="/static/js/fleet-feedback-capture.js">')
        self.assertLess(html.index(scripts[0]), html.index('</head>'))

    @override_settings(DEBUG=True)
    def test_env_is_development_for_a_local_request_under_debug(self):
        self.assertIn('env="development"', self.home_html())

    def test_env_is_production_without_debug(self):
        self.assertIn('env="production"', self.home_html())

    def test_kit_scripts_resolve_from_public(self):
        for name in ('fleet-feedback.js', 'fleet-feedback-capture.js'):
            found = finders.find('js/' + name)
            self.assertIsNotNone(found, name)
            self.assertEqual(Path(found), Path(settings.BASE_DIR) / 'public' / name)


class VersionEndpointTests(TestCase):
    """GET /version is the build identity (hub specs/BACKEND.md UPS-BE-11)."""

    def test_version_returns_200_and_json_contract(self):
        response = self.client.get('/version')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(sorted(payload), ['built_at', 'commit', 'name', 'version'])
        self.assertEqual(payload['name'], 'djangoerp')
        self.assertEqual(payload['version'], settings.APP_VERSION)

    def test_version_url_has_no_trailing_slash(self):
        self.assertEqual(reverse('company:version'), '/version')

    @override_settings(GIT_SHA=None, BUILT_AT=None)
    def test_unknown_build_reports_null(self):
        payload = self.client.get('/version').json()
        self.assertIsNone(payload['commit'])
        self.assertIsNone(payload['built_at'])

    @override_settings(GIT_SHA='4e4a8f1f', BUILT_AT='2026-11-15T00:00:00Z')
    def test_build_identity_comes_from_settings(self):
        payload = self.client.get('/version').json()
        self.assertEqual(payload['commit'], '4e4a8f1f')
        self.assertEqual(payload['built_at'], '2026-11-15T00:00:00Z')

    def test_app_version_is_the_release_please_version(self):
        manifest = json.loads((Path(settings.BASE_DIR) / '.release-please-manifest.json').read_text())
        self.assertEqual(settings.APP_VERSION, manifest['.'])
        config = json.loads((Path(settings.BASE_DIR) / 'release-please-config.json').read_text())
        self.assertIn('djangoerp/settings/base.py', config['packages']['.'].get('extra-files', []))


class SkipLinkTests(TestCase):
    """The first focusable element skips to <main id="main-content"> (hub specs/FRONTEND.md UPS-FE-11)."""

    def test_skip_link_is_the_first_focusable_element(self):
        html = self.client.get(reverse('company:home')).content.decode()
        body = html[html.index('<body'):]
        first = re.search(r'<(?:a|button|input|select|textarea)\b[^>]*>', body).group(0)
        self.assertEqual(first, '<a class="skip-link" href="#main-content">')

    def test_main_is_the_skip_target(self):
        html = self.client.get(reverse('company:home')).content.decode()
        self.assertEqual(html.count('id="main-content"'), 1)
        self.assertIn('<main id="main-content"', html)


class CompanyModelTests(TestCase):
    def test_str_returns_name(self):
        self.assertEqual(str(Company(name='Northwind Trading')), 'Northwind Trading')

    def test_timestamps_come_from_the_mixin(self):
        company = Company.objects.create(name='Timestamped Co')
        self.assertIsNotNone(company.created_at)
        self.assertIsNotNone(company.updated_at)

    def test_default_currency_is_usd(self):
        self.assertEqual(Company.objects.create(name='Default Co').currency_code, 'USD')


class CompanyAPITests(APITestCase):
    def setUp(self):
        self.user = create_user('company-tester', role='accountant')
        self.client.force_authenticate(user=self.user)

    def test_create_company_sets_created_by(self):
        response = self.client.post(reverse('company-list'), {
            'name': 'Northwind Trading', 'currency_code': 'eur',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['created_by'], self.user.id)
        self.assertEqual(response.data['currency_code'], 'EUR')

    def test_currency_code_must_be_three_letters(self):
        response = self.client.post(reverse('company-list'), {
            'name': 'Bad Currency Co', 'currency_code': 'US1',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse('company-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AdminRegistrationTests(TestCase):
    def test_company_registered_in_admin(self):
        self.assertTrue(admin.site.is_registered(Company))
