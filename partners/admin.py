from django.contrib import admin

from .models import BusinessPartner


@admin.register(BusinessPartner)
class BusinessPartnerAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'is_customer', 'is_supplier', 'is_active']
    list_filter = ['is_customer', 'is_supplier', 'is_active']
    search_fields = ['code', 'name', 'email', 'tax_id']
