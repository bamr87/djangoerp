from collections import Counter

from django.db.models import ProtectedError, RestrictedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback


def exception_handler(exc, context):
    """
    DRF's exception handler, plus a 409 for a delete the database refuses.

    ``Model.delete()`` raises ProtectedError (or RestrictedError) when a PROTECT
    (or RESTRICT) foreign key still points at the row: a partner's control
    account, a child account, a product's category, a document's journal entry.
    Unhandled, that is a 500. It is a conflict with the current state, so answer
    409 and say what still references the row. Wired in as
    ``REST_FRAMEWORK['EXCEPTION_HANDLER']``.
    """
    if isinstance(exc, (ProtectedError, RestrictedError)):
        blockers = exc.protected_objects if isinstance(exc, ProtectedError) else exc.restricted_objects
        counts = Counter(type(obj) for obj in blockers)
        referenced_by = ', '.join(sorted(
            f'{count} {model._meta.verbose_name if count == 1 else model._meta.verbose_name_plural}'
            for model, count in counts.items()
        ))
        set_rollback()
        return Response(
            {'detail': f'Cannot delete: still referenced by {referenced_by}.'},
            status=status.HTTP_409_CONFLICT,
        )
    return drf_exception_handler(exc, context)
