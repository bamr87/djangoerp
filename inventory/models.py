from django.conf import settings
from django.db import models

from core.models import TimestampMixin


class Warehouse(TimestampMixin):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class StockMove(TimestampMixin):
    """
    The stock ledger — the quantity twin of journal.JournalLine. Append-only:
    like journal entries, only status='done' moves count, corrections are
    opposing moves (adjustment_in/out), and a completed move is never edited.
    On-hand quantity is by definition the sum of done moves; StockLevel is just
    the cache of that sum (mirrors ERPNext's Stock Ledger Entry / Odoo's
    stock.move + stock.quant split).
    """

    INBOUND_TYPES = ('receipt', 'production_receipt', 'adjustment_in')
    OUTBOUND_TYPES = ('shipment', 'production_issue', 'adjustment_out')
    MOVE_TYPES = [
        ('receipt', 'Purchase receipt'),
        ('production_receipt', 'Production receipt'),
        ('adjustment_in', 'Adjustment in'),
        ('shipment', 'Customer shipment'),
        ('production_issue', 'Production issue'),
        ('adjustment_out', 'Adjustment out'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ]

    reference = models.CharField(max_length=32, unique=True)
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, related_name='stock_moves')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='stock_moves')
    move_type = models.CharField(max_length=20, choices=MOVE_TYPES)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    # Valuation snapshot, set when the move completes: inbound moves carry the cost
    # they arrive at, outbound moves the moving-average cost they leave at.
    # total_value is the 2dp amount the GL posting for this move used.
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    total_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    move_date = models.DateField()
    # Document that caused the move (GRN/shipment/work-order number) — loose string
    # coupling so inventory has no model dependency on the ordering apps.
    source_reference = models.CharField(max_length=64, blank=True, default='', db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-move_date', '-id']
        indexes = [
            models.Index(fields=['product', 'warehouse', 'status']),
            models.Index(fields=['move_type']),
            models.Index(fields=['move_date']),
        ]

    def __str__(self):
        return f"{self.reference} ({self.get_move_type_display()})"

    @property
    def is_inbound(self):
        return self.move_type in self.INBOUND_TYPES

    @property
    def signed_quantity(self):
        return self.quantity if self.is_inbound else -self.quantity


class StockLevel(models.Model):
    """
    Cached on-hand quantity and moving-average cost per product/warehouse,
    maintained under row locks by inventory.services.complete_move and
    reconstructible from the move ledger via rebuild_stock_levels().
    """

    product = models.ForeignKey('products.Product', on_delete=models.CASCADE, related_name='stock_levels')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stock_levels')
    quantity_on_hand = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    average_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['product', 'warehouse'], name='unique_stock_level'),
        ]

    def __str__(self):
        return f"{self.product.sku} @ {self.warehouse.code}: {self.quantity_on_hand}"
