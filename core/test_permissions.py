"""The role x area x verb matrix for the viewsets that were open to any authenticated user."""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user

READ_ROLES = {
    # ledger data: accountant + auditor (admin implies both); viewers and role-less users are denied
    'journalline-list': {'admin', 'accountant', 'auditor'},
    'invoice-list': {'admin', 'accountant', 'auditor'},
    'payment-list': {'admin', 'accountant', 'auditor'},
    # reference data: any user with a role
    'company-list': {'admin', 'accountant', 'auditor', 'viewer'},
    'savedreport-list': {'admin', 'accountant', 'auditor', 'viewer'},
}
# Writable routes: every write needs the accountant role (admin implies it).
WRITE_ROUTES = ['invoice-list', 'payment-list', 'company-list', 'savedreport-list']
WRITE_ROLES = {'admin', 'accountant'}
ALL_ROLES = ['admin', 'accountant', 'auditor', 'viewer', None]  # None: an account with no UserRole row


class ViewsetPermissionMatrixTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {role: create_user(f'user_{role}', role=role) for role in ALL_ROLES if role}
        cls.users[None] = User.objects.create_user('user_none', password='pass')

    def test_read_matrix(self):
        for name, allowed in READ_ROLES.items():
            for role in ALL_ROLES:
                with self.subTest(route=name, role=role):
                    self.client.force_authenticate(user=self.users[role])
                    response = self.client.get(reverse(name))
                    expected = status.HTTP_200_OK if role in allowed else status.HTTP_403_FORBIDDEN
                    self.assertEqual(response.status_code, expected)

    def test_write_matrix(self):
        # An empty body is never valid, so an allowed role gets 400 and a denied one 403.
        for name in WRITE_ROUTES:
            for role in ALL_ROLES:
                with self.subTest(route=name, role=role):
                    self.client.force_authenticate(user=self.users[role])
                    response = self.client.post(reverse(name), {}, format='json')
                    expected = status.HTTP_400_BAD_REQUEST if role in WRITE_ROLES else status.HTTP_403_FORBIDDEN
                    self.assertEqual(response.status_code, expected)

    def test_anonymous_is_unauthorised(self):
        for name in READ_ROLES:
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, status.HTTP_401_UNAUTHORIZED)
