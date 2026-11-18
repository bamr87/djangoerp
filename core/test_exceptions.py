from django.db.models import ProtectedError, RestrictedError
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.exceptions import NotFound

from coa.models import Account, AccountType

from .exceptions import exception_handler


class ExceptionHandlerTests(SimpleTestCase):
    """core.exceptions.exception_handler: a refused delete is a 409, everything else is DRF's."""

    def test_protected_and_restricted_deletes_are_conflicts(self):
        blockers = [Account(pk=1), Account(pk=2), AccountType(pk=1)]
        for exc in (ProtectedError('protected', blockers), RestrictedError('restricted', blockers)):
            with self.subTest(type(exc).__name__):
                response = exception_handler(exc, {})
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(
                    response.data['detail'], 'Cannot delete: still referenced by 1 account type, 2 accounts.',
                )

    def test_other_exceptions_fall_through_to_drf(self):
        self.assertEqual(exception_handler(NotFound(), {}).status_code, status.HTTP_404_NOT_FOUND)
        self.assertIsNone(exception_handler(ValueError('not an API error'), {}))
