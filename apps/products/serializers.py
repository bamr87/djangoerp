from rest_framework import serializers

from .models import Product, ProductCategory, UnitOfMeasure


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = ['id', 'code', 'name', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class ProductCategorySerializer(serializers.ModelSerializer):
    inventory_account_display = serializers.StringRelatedField(source='inventory_account', read_only=True)
    revenue_account_display = serializers.StringRelatedField(source='revenue_account', read_only=True)
    cogs_account_display = serializers.StringRelatedField(source='cogs_account', read_only=True)

    class Meta:
        model = ProductCategory
        fields = [
            'id', 'name', 'description', 'inventory_account', 'inventory_account_display',
            'revenue_account', 'revenue_account_display', 'cogs_account', 'cogs_account_display',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class ProductSerializer(serializers.ModelSerializer):
    category_display = serializers.StringRelatedField(source='category', read_only=True)
    uom_display = serializers.StringRelatedField(source='uom', read_only=True)
    default_supplier_display = serializers.StringRelatedField(source='default_supplier', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'sku', 'name', 'description', 'category', 'category_display',
            'uom', 'uom_display', 'product_type', 'procurement_type',
            'standard_cost', 'sales_price', 'lead_time_days', 'safety_stock',
            'min_order_qty', 'order_multiple',
            'default_supplier', 'default_supplier_display', 'is_active',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def validate(self, data):
        default_supplier = data.get('default_supplier', getattr(self.instance, 'default_supplier', None))
        if default_supplier and not default_supplier.is_supplier:
            raise serializers.ValidationError('default_supplier must be a partner flagged is_supplier.')
        for field in ('standard_cost', 'sales_price', 'safety_stock', 'min_order_qty', 'order_multiple'):
            value = data.get(field)
            if value is not None and value < 0:
                raise serializers.ValidationError(f'{field} cannot be negative.')
        return data
