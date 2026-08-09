from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ['number', 'supplier', 'warehouse', 'status', 'order_date', 'expected_date']
    list_filter = ['status', 'warehouse']
    search_fields = ['number', 'supplier__code', 'supplier__name', 'source_reference']
    inlines = [PurchaseOrderLineInline]
