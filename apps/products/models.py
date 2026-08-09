from django.conf import settings
from django.db import models

from apps.core.models import TimestampMixin


class UnitOfMeasure(TimestampMixin):
    """
    Base unit a product is stocked and planned in (EA, KG, M, HR...). Quantities
    on documents are always in the product's base unit; UoM conversion rings are
    deliberately out of scope for the core (see roadmap).
    """

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=50)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return self.code


class ProductCategory(TimestampMixin):
    """
    Groups products and carries their GL posting defaults (the "item group
    defaults" pattern from ERPNext): which accounts inventory value, revenue and
    cost of goods sold post to for products in this category.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    inventory_account = models.ForeignKey(
        'coa.Account', on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_categories'
    )
    revenue_account = models.ForeignKey(
        'coa.Account', on_delete=models.PROTECT, null=True, blank=True, related_name='revenue_categories'
    )
    cogs_account = models.ForeignKey(
        'coa.Account', on_delete=models.PROTECT, null=True, blank=True, related_name='cogs_categories'
    )

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'product categories'

    def __str__(self):
        return self.name


class Product(TimestampMixin):
    """
    The item master. procurement_type drives MRP: 'buy' shortages become planned
    purchase orders, 'make' shortages become planned work orders exploded through
    the product's active bill of materials.
    """

    PRODUCT_TYPES = [
        ('stocked', 'Stocked product'),
        ('service', 'Service'),
    ]
    PROCUREMENT_TYPES = [
        ('buy', 'Buy'),
        ('make', 'Make'),
    ]

    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        ProductCategory, on_delete=models.PROTECT, null=True, blank=True, related_name='products'
    )
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name='products')
    product_type = models.CharField(max_length=16, choices=PRODUCT_TYPES, default='stocked')
    procurement_type = models.CharField(max_length=8, choices=PROCUREMENT_TYPES, default='buy')
    # Planning/reference cost and default sell price. Inventory is valued at moving
    # average (see inventory.services), so standard_cost seeds planned purchase
    # prices, not the stock ledger.
    standard_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    sales_price = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    lead_time_days = models.PositiveIntegerField(default=0)
    safety_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    # Lot sizing for MRP: shortfalls are floored to min_order_qty and rounded up
    # to a multiple of order_multiple. Both 0 means plain lot-for-lot.
    min_order_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    order_multiple = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    default_supplier = models.ForeignKey(
        'partners.BusinessPartner', on_delete=models.PROTECT, null=True, blank=True,
        related_name='supplied_products',
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['sku']
        indexes = [
            models.Index(fields=['procurement_type']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"
