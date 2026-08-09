from django.conf import settings
from django.contrib import admin
from django.test import TestCase
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
