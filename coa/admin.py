from django.contrib import admin

from .models import Account, AccountType


@admin.register(AccountType)
class AccountTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'description')
    search_fields = ('code', 'name')


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'account_type', 'is_active', 'parent_account')
    list_filter = ('account_type', 'is_active')
    search_fields = ('code', 'name', 'description')
    autocomplete_fields = ('parent_account', 'account_type')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Basic Information', {
            'fields': ('code', 'name', 'account_type', 'is_active')
        }),
        ('Hierarchy', {
            'fields': ('parent_account',)
        }),
        ('Additional Information', {
            'fields': ('description', 'created_at', 'updated_at')
        }),
    )
