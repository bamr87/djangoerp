from django.contrib import admin

from .models import Product, ProductCategory, UnitOfMeasure


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']
    search_fields = ['code', 'name']


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'inventory_account', 'revenue_account', 'cogs_account']
    search_fields = ['name']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'category', 'procurement_type', 'standard_cost', 'is_active']
    list_filter = ['product_type', 'procurement_type', 'is_active', 'category']
    search_fields = ['sku', 'name']
