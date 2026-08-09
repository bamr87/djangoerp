from django.contrib import admin

from .models import Company

admin.site.site_header = 'DjangoERP Administration'
admin.site.site_title = 'DjangoERP'
admin.site.index_title = 'Ledgers and master data'


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ['name', 'currency_code', 'email', 'founded_on', 'is_active']
    list_filter = ['is_active', 'currency_code']
    search_fields = ['name', 'legal_name', 'email', 'tax_id']
