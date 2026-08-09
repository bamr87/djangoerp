from django.contrib import admin

from .models import SalesOrder, SalesOrderLine


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 0


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ['number', 'customer', 'warehouse', 'status', 'order_date', 'requested_date']
    list_filter = ['status', 'warehouse']
    search_fields = ['number', 'customer__code', 'customer__name']
    inlines = [SalesOrderLineInline]
