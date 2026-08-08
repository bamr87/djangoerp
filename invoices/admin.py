from django.contrib import admin

from .models import Invoice, InvoiceLineItem, Payment


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 1


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'customer', 'vendor', 'due_date', 'status', 'total')
    list_filter = ('status',)
    search_fields = ('invoice_number', 'customer', 'vendor')
    readonly_fields = ('created_at', 'updated_at', 'created_by')
    inlines = [InvoiceLineItemInline]
    date_hierarchy = 'due_date'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('payment_reference', 'invoice', 'amount', 'payment_method', 'payment_date')
    list_filter = ('payment_method',)
    search_fields = ('payment_reference',)
    readonly_fields = ('created_at', 'created_by')
    date_hierarchy = 'payment_date'
