from django.contrib import admin

from .models import BillOfMaterials, BOMLine, WorkOrder


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 0


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(admin.ModelAdmin):
    list_display = ['product', 'reference', 'quantity', 'is_active']
    list_filter = ['is_active']
    search_fields = ['product__sku', 'reference']
    inlines = [BOMLineInline]


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ['number', 'product', 'warehouse', 'quantity', 'status', 'due_date']
    list_filter = ['status', 'warehouse']
    search_fields = ['number', 'product__sku', 'source_reference']
