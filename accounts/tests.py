from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import UserRole
from .test_helpers import create_user


class UserRoleModelTests(TestCase):
    def test_role_defaults_to_viewer(self):
        plain_user = User.objects.create_user(username='plain', password='testpass123')
        role = UserRole.objects.create(user=plain_user)
        self.assertEqual(role.role, 'viewer')

    def test_role_choices(self):
        user = create_user('admin1', role='admin')
        self.assertEqual(user.role.role, 'admin')


class LoginTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', password='testpass123', role='accountant')

    def test_login_success(self):
        response = self.client.post(reverse('token_obtain_pair'), {
            'username': 'tester', 'password': 'testpass123',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['username'], 'tester')
        self.assertEqual(response.data['user']['role'], 'accountant')

    def test_login_bad_password(self):
        response = self.client.post(reverse('token_obtain_pair'), {
            'username': 'tester', 'password': 'wrong',
        })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class UserViewSetTests(APITestCase):
    def setUp(self):
        self.admin = create_user('admin', role='admin')
        self.viewer = create_user('viewer', role='viewer')

    def test_list_users_authenticated(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(reverse('user-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_user_admin_only(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(reverse('user-list'), {
            'username': 'newuser', 'password': 'pass123',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_update_other_user_viewer_denied(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.patch(
            reverse('user-detail', args=[self.admin.id]), {'first_name': 'Hacked'},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_self_allowed(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.patch(
            reverse('user-detail', args=[self.viewer.id]), {'first_name': 'Me'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
