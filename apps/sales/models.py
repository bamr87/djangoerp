from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TimestampMixin


class SalesOrder(TimestampMixin):
    """
    Order-to-cash document and the demand source for MRP. Lifecycle:
    draft -> confirmed -> (partially_shipped) -> shipped -> invoiced;
    cancellable until goods leave. Transitions live in sales.services.
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('partially_shipped', 'Partially shipped'),
        ('shipped', 'Shipped'),
        ('invoiced', 'Invoiced'),
        ('cancelled', 'Cancelled'),
    ]
    # Statuses whose open line quantities count as demand for MRP.
    OPEN_STATUSES = ('confirmed', 'partially_shipped')

    number = models.CharField(max_length=32, unique=True)
    customer = models.ForeignKey('partners.BusinessPartner', on_delete=models.PROTECT, related_name='sales_orders')
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='sales_orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    order_date = models.DateField()
    # When the customer wants the goods — the due date MRP plans backwards from.
    requested_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
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


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='sales_lines')
    description = models.CharField(max_length=255, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    shipped_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.order.number}: {self.product.sku} x {self.quantity}"

    @property
    def open_quantity(self):
        return self.quantity - self.shipped_quantity

    @property
    def line_total(self):
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))
