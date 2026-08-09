from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TimestampMixin


class PurchaseOrder(TimestampMixin):
    """
    Procure-to-pay document. Lifecycle: draft -> confirmed -> (partially_received)
    -> received; cancellable until goods arrive. Transitions live in
    purchasing.services — views and serializers never assign status directly.
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('partially_received', 'Partially received'),
        ('received', 'Received'),
        ('cancelled', 'Cancelled'),
    ]
    # Statuses whose open line quantities count as scheduled receipts for MRP.
    OPEN_STATUSES = ('confirmed', 'partially_received')

    number = models.CharField(max_length=32, unique=True)
    supplier = models.ForeignKey('partners.BusinessPartner', on_delete=models.PROTECT, related_name='purchase_orders')
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='purchase_orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    order_date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    # Planning trail: the MRP run (if any) this order was converted from.
    source_reference = models.CharField(max_length=64, blank=True, default='', db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-order_date', '-id']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['order_date']),
        ]

    def __str__(self):
        return self.number

    @property
    def total_amount(self):
        return sum((line.line_total for line in self.lines.all()), Decimal('0.00'))


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='purchase_lines')
    description = models.CharField(max_length=255, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.order.number}: {self.product.sku} x {self.quantity}"

    @property
    def open_quantity(self):
        return self.quantity - self.received_quantity

    @property
    def line_total(self):
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))
