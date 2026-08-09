from django.conf import settings
from django.db import models

from apps.core.models import TimestampMixin


class Company(TimestampMixin):
    """
    The legal entity whose books this installation keeps.

    Master data, deliberately kept out of `core` so the shared platform stays
    infrastructure-only (TimestampMixin, DocumentSequence, audit). Multiple rows
    are allowed — the portal shows the first active one — so a future
    multi-company pass can add a per-document FK without a data migration.
    """

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    currency_code = models.CharField(
        max_length=3,
        default='USD',
        help_text='ISO 4217 code the books are kept in.',
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    tax_id = models.CharField(max_length=64, blank=True)
    founded_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'companies'
        indexes = [
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.name
