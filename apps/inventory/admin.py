from django.contrib import admin

from .models import StockLevel, StockMove, Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'is_active']
    search_fields = ['code', 'name']


@admin.register(StockMove)
class StockMoveAdmin(admin.ModelAdmin):
    list_display = ['reference', 'product', 'warehouse', 'move_type', 'quantity', 'status', 'move_date']
    list_filter = ['move_type', 'status', 'warehouse']
    search_fields = ['reference', 'source_reference', 'product__sku']


@admin.register(StockLevel)
class StockLevelAdmin(admin.ModelAdmin):
    list_display = ['product', 'warehouse', 'quantity_on_hand', 'average_cost']
    list_filter = ['warehouse']
    search_fields = ['product__sku']
