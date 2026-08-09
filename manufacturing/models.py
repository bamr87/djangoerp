from django.conf import settings
from django.db import models

from core.models import TimestampMixin


class BillOfMaterials(TimestampMixin):
    """
    Recipe for one product: the component quantities that produce `quantity`
    units of `product`. One active BOM per product (enforced by a partial unique
    constraint); superseded revisions stay inactive for history. Routings and
    work centers are deliberately out of scope for the core (capacity-infinite
    MRP I — see roadmap).
    """

    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='boms')
    reference = models.CharField(max_length=64, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['product__sku', '-id']
        verbose_name_plural = 'bills of materials'
        constraints = [
            models.UniqueConstraint(
                fields=['product'], condition=models.Q(is_active=True), name='one_active_bom_per_product'
            ),
        ]

    def __str__(self):
        return f"BOM {self.product.sku} x {self.quantity}" + (f" ({self.reference})" if self.reference else "")


class BOMLine(models.Model):
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.CASCADE, related_name='lines')
    component = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='used_in_bom_lines')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['bom', 'component'], name='unique_component_per_bom'),
        ]

    def __str__(self):
        return f"{self.component.sku} x {self.quantity}"


class WorkOrder(TimestampMixin):
    """
    Make document. Lifecycle: draft -> confirmed -> in_progress -> completed;
    cancellable until work starts. Completion consumes components and receives
    the finished good in one transaction (manufacturing.services) — there is no
    separate WIP ledger in the core, the whole order completes at once.
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('in_progress', 'In progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    # Statuses whose quantities count as scheduled receipts for MRP.
    OPEN_STATUSES = ('confirmed', 'in_progress')

    number = models.CharField(max_length=32, unique=True)
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='work_orders')
    bom = models.ForeignKey(BillOfMaterials, on_delete=models.PROTECT, related_name='work_orders')
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='work_orders')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    due_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Planning trail: the MRP run (if any) this order was converted from.
    source_reference = models.CharField(max_length=64, blank=True, default='', db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-id']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['due_date']),
        ]

    def __str__(self):
        return self.number
