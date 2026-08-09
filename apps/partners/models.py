from django.conf import settings
from django.db import models

from apps.core.models import TimestampMixin


class BusinessPartner(TimestampMixin):
    """
    A customer and/or supplier. Single-partner pattern (as in Odoo/Tryton): one
    row can play both roles, flagged by is_customer / is_supplier, so a company
    you both buy from and sell to is one record with one balance history.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    is_customer = models.BooleanField(default=False)
    is_supplier = models.BooleanField(default=False)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    tax_id = models.CharField(max_length=64, blank=True)
    # GL control accounts for this partner. Posting a document against the partner
    # (invoice, payment, goods receipt accrual) requires the matching account to be
    # set — services raise a clear configuration error instead of guessing.
    receivable_account = models.ForeignKey(
        'coa.Account', on_delete=models.PROTECT, null=True, blank=True, related_name='receivable_partners'
    )
    payable_account = models.ForeignKey(
        'coa.Account', on_delete=models.PROTECT, null=True, blank=True, related_name='payable_partners'
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['code']
        indexes = [
            models.Index(fields=['is_customer']),
            models.Index(fields=['is_supplier']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"
