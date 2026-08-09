from rest_framework import serializers

from .models import StockLevel, StockMove, Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ['id', 'code', 'name', 'is_active', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['created_by', 'created_at', 'updated_at']


class StockMoveSerializer(serializers.ModelSerializer):
    product_display = serializers.StringRelatedField(source='product', read_only=True)
    warehouse_display = serializers.StringRelatedField(source='warehouse', read_only=True)

    class Meta:
        model = StockMove
        fields = [
            'id', 'reference', 'product', 'product_display', 'warehouse', 'warehouse_display',
            'move_type', 'quantity', 'unit_cost', 'total_value', 'status', 'move_date',
            'source_reference', 'created_by', 'created_at', 'updated_at',
        ]
        # reference comes from the SM sequence; status/valuation are owned by the
        # complete/cancel services — the API creates drafts only.
        read_only_fields = ['reference', 'status', 'total_value', 'created_by', 'created_at', 'updated_at']

    def validate(self, data):
        quantity = data.get('quantity', getattr(self.instance, 'quantity', None))
        if quantity is not None and quantity <= 0:
            raise serializers.ValidationError('Stock move quantity must be positive.')
        unit_cost = data.get('unit_cost')
        if unit_cost is not None and unit_cost < 0:
            raise serializers.ValidationError('unit_cost cannot be negative.')
        product = data.get('product', getattr(self.instance, 'product', None))
        if product is not None and product.product_type != 'stocked':
            raise serializers.ValidationError(f'{product.sku} is not a stocked product.')
        if self.instance and self.instance.status != 'draft':
            raise serializers.ValidationError('Completed or cancelled moves are append-only history.')
        return data


class StockLevelSerializer(serializers.ModelSerializer):
    product_display = serializers.StringRelatedField(source='product', read_only=True)
    warehouse_display = serializers.StringRelatedField(source='warehouse', read_only=True)

    class Meta:
        model = StockLevel
        fields = [
            'id', 'product', 'product_display', 'warehouse', 'warehouse_display',
            'quantity_on_hand', 'average_cost',
        ]
