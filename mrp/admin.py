from django.contrib import admin

from .models import MRPRun, PlannedOrder


@admin.register(MRPRun)
class MRPRunAdmin(admin.ModelAdmin):
    list_display = ['number', 'warehouse', 'status', 'as_of_date', 'completed_at']
    list_filter = ['status', 'warehouse']
    search_fields = ['number']


@admin.register(PlannedOrder)
class PlannedOrderAdmin(admin.ModelAdmin):
    list_display = ['run', 'product', 'order_type', 'quantity', 'due_date', 'release_date', 'status']
    list_filter = ['order_type', 'status', 'expedited']
    search_fields = ['product__sku', 'demand_reference', 'converted_reference']
