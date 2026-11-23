import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user

from . import views
from .models import Company


def contrast_ratio(a, b):
    """WCAG 2 contrast ratio of two #rrggbb colours."""
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    lighter, darker = sorted((luminance(a), luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class HealthEndpointTests(TestCase):
    def test_health_returns_200_and_json_contract(self):
        response = self.client.get(reverse('company:health'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(sorted(payload), ['app', 'status', 'version'])  # never `debug`: it leaked configuration
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


class AssistPanelTests(TestCase):
    """The fleet assistant kit (public/fleet-assist.js, templates/includes/fleet-assist.html) is a developer tool:
    company/base.html mounts it, and company.context_processors.fleet turns it on under DEBUG only."""

    # Every page this repo renders from its own templates: the landing page, the workspace shell and the console.
    PAGES = ('company:home', 'workspace:shell', 'simulation:console')

    def home_html(self):
        return self.client.get(reverse('company:home')).content.decode()

    @override_settings(DEBUG=True)
    def test_debug_mounts_the_panel_once_for_this_repo(self):
        html = self.home_html()
        self.assertEqual(html.count('<fleet-assist'), 1)
        tag = re.search(r'<fleet-assist\b[^>]*>', html).group(0)
        for attribute in (
            'repo="bamr87/djangoerp"',
            'branch="main"',
            'route="company:home"',
            'framework="django"',
            'app-version="%s"' % settings.APP_VERSION,
            'env="development"',
        ):
            self.assertIn(attribute, tag)
        self.assertNotIn('broker=', tag)  # the kit's default: the Harness Console on this machine
        self.assertLess(html.index('</footer>'), html.index('<fleet-assist'))
        self.assertLess(html.index('</fleet-assist>'), html.index('</body>'))

    @override_settings(DEBUG=True)
    def test_panel_script_resolves_from_public(self):
        sources = re.findall(r'<script src="([^"]*fleet-assist[^"]*)" defer></script>', self.home_html())
        self.assertEqual(sources, ['/static/js/fleet-assist.js'])
        found = finders.find(sources[0].removeprefix('/static/'))
        self.assertIsNotNone(found, sources[0])
        self.assertEqual(Path(found), Path(settings.BASE_DIR) / 'public' / 'fleet-assist.js')

    @override_settings(DEBUG=False)
    def test_no_page_carries_the_panel_without_debug(self):
        for name in self.PAGES:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertNotIn('fleet-assist', response.content.decode(), name)

    def test_context_follows_debug(self):
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                context = self.client.get(reverse('company:home')).context
                self.assertIs(context['FLEET_ASSIST'], debug)


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


class LandingPageTests(TestCase):
    """What a visitor sees on / : setup steps, meta, landmarks, the API map and readable colours."""

    def home_html(self):
        return self.client.get(reverse('company:home')).content.decode()

    def test_anonymous_empty_state_shows_setup_steps_and_no_admin_add_link(self):
        html = self.home_html()
        self.assertIn('<h1>Get started</h1>', html)
        self.assertIn('python manage.py createsuperuser', html)
        self.assertIn('python manage.py demo_erp', html)
        self.assertNotIn(reverse('admin:company_company_add'), html)

    def test_staff_empty_state_links_the_admin_add_form(self):
        self.client.force_login(User.objects.create_user('staffer', password='pass', is_staff=True))
        self.assertIn('href="%s"' % reverse('admin:company_company_add'), self.home_html())

    def test_non_staff_user_gets_no_admin_add_link(self):
        self.client.force_login(create_user('clerk'))
        self.assertNotIn(reverse('admin:company_company_add'), self.home_html())

    def test_title_and_h1_name_the_page(self):
        html = self.home_html()
        self.assertIn('<title>Get started · DjangoERP</title>', html)
        self.assertEqual(re.findall(r'<h1\b[^>]*>(.*?)</h1>', html), ['Get started'])
        Company.objects.create(name='Northwind Trading')
        html = self.home_html()
        self.assertIn('<title>Northwind Trading · DjangoERP</title>', html)
        self.assertEqual(re.findall(r'<h1\b[^>]*>(.*?)</h1>', html), ['Northwind Trading'])

    def test_head_carries_description_theme_icon_canonical_and_open_graph(self):
        html = self.home_html()
        head = html[:html.index('</head>')]
        for needle in (
            '<meta name="description" content="',
            '<meta name="theme-color" content="#f7f5f0" media="(prefers-color-scheme: light)">',
            '<meta name="theme-color" content="#1b1815" media="(prefers-color-scheme: dark)">',
            '<link rel="icon" href="/static/company/favicon.svg" type="image/svg+xml">',
            '<link rel="canonical" href="http://testserver/">',
            '<meta property="og:type" content="website">',
            '<meta property="og:title" content="Get started">',
            '<meta property="og:description" content="',
            '<meta property="og:url" content="http://testserver/">',
        ):
            self.assertIn(needle, head)
        self.assertIsNotNone(finders.find('company/favicon.svg'))

    def test_primary_nav_is_labelled_and_health_lives_in_the_footer(self):
        html = self.home_html()
        nav = re.search(r'<nav aria-label="Primary">(.*?)</nav>', html, re.S).group(1)
        self.assertNotIn(reverse('company:health'), nav)
        self.assertIn('href="%s"' % reverse('company:health'), html[html.index('<footer>'):])

    def test_api_table_has_a_caption_column_headers_and_a_link_per_prefix(self):
        table = re.search(r'<table>(.*?)</table>', self.home_html(), re.S).group(1)
        self.assertIn('<caption>', table)
        self.assertIn('<thead>', table)
        self.assertEqual(table.count('<th scope="col">'), 2)
        for label, prefix in views.API_AREAS:
            self.assertIn('<a href="/swagger/#/%s"><code>/api/%s/</code></a>' % (prefix, prefix), table)

    def test_every_listed_prefix_is_a_swagger_tag(self):
        # The links are Swagger UI deep links (SWAGGER_SETTINGS DEEP_LINKING): #/<tag> opens that section.
        self.assertTrue(settings.SWAGGER_SETTINGS.get('DEEP_LINKING'))
        schema = self.client.get(reverse('schema-json', kwargs={'format': '.json'})).json()
        tags = {tag for ops in schema['paths'].values() for op in ops.values() if isinstance(op, dict)
                for tag in op.get('tags', [])}
        self.assertEqual({prefix for _, prefix in views.API_AREAS}, tags)

    def test_badge_text_meets_aa_contrast_in_both_schemes(self):
        css = re.search(r'<style>(.*?)</style>', self.home_html(), re.S).group(1)
        self.assertRegex(css, r'\.badge\s*\{[^}]*background:\s*var\(--gold\);[^}]*color:\s*var\(--bg\);')
        light, dark = css.split('@media (prefers-color-scheme: dark)')[:2]
        for scheme in (light, dark):
            colours = dict(re.findall(r'--(bg|gold):\s*(#[0-9a-fA-F]{6})', scheme))
            self.assertGreaterEqual(contrast_ratio(colours['bg'], colours['gold']), 4.5, colours)


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
