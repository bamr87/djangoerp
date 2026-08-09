from django.conf import settings
from django.db import models

from core.models import TimestampMixin


class MRPRun(TimestampMixin):
    """
    One regenerative planning run for one warehouse, executed asynchronously by
    mrp.engine.run_mrp (the same Celery pattern as reports.SavedReport). The run
    nets demand against supply level-by-level and writes PlannedOrder proposals;
    converting them into real purchase/work orders is a separate, deliberate
    action.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    number = models.CharField(max_length=32, unique=True)
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='mrp_runs')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    # Planning anchor date: release dates earlier than this are clamped to it and
    # the planned order flagged expedited.
    as_of_date = models.DateField()
    log = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.number


class PlannedOrder(TimestampMixin):
    """
    A supply proposal: buy or make `quantity` of `product`, releasing on
    `release_date` to arrive by `due_date`. Pegged to the demand that caused it
    (demand_reference, and parent for exploded component requirements), following
    frePPLe's proposed -> converted promotion ladder.
    """

    ORDER_TYPES = [
        ('buy', 'Buy'),
        ('make', 'Make'),
    ]
    STATUS_CHOICES = [
        ('planned', 'Planned'),
        ('converted', 'Converted'),
        ('cancelled', 'Cancelled'),
    ]

    run = models.ForeignKey(MRPRun, on_delete=models.CASCADE, related_name='planned_orders')
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='planned_orders')
    order_type = models.CharField(max_length=8, choices=ORDER_TYPES)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    due_date = models.DateField()
    release_date = models.DateField()
    # True when the lead-time offset landed before the run's as_of_date: the order
    # cannot arrive on time even if released immediately.
    expedited = models.BooleanField(default=False)
    # Pegging: the demand element this order covers ("SO-00001 line 3 (WIDGET)",
    # "safety stock", ...) and, for exploded component demand, the parent make order.
    demand_reference = models.CharField(max_length=128, blank=True, default='')
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='component_requirements'
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='planned')
    converted_reference = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ['due_date', 'id']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['order_type']),
        ]

    def __str__(self):
        return f"{self.get_order_type_display()} {self.quantity} x {self.product.sku} by {self.due_date}"
