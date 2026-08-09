from rest_framework import serializers

from .models import SalesOrder, SalesOrderLine


class SalesOrderLineSerializer(serializers.ModelSerializer):
    product_display = serializers.StringRelatedField(source='product', read_only=True)
    open_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = [
            'id', 'product', 'product_display', 'description', 'quantity', 'unit_price',
            'shipped_quantity', 'open_quantity', 'line_total',
        ]
        read_only_fields = ['shipped_quantity']

    def validate(self, data):
        if data.get('quantity') is not None and data['quantity'] <= 0:
            raise serializers.ValidationError('Line quantity must be positive.')
        if data.get('unit_price') is not None and data['unit_price'] < 0:
            raise serializers.ValidationError('unit_price cannot be negative.')
        return data


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = SalesOrderLineSerializer(many=True)
    customer_display = serializers.StringRelatedField(source='customer', read_only=True)
    warehouse_display = serializers.StringRelatedField(source='warehouse', read_only=True)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            'id', 'number', 'customer', 'customer_display', 'warehouse', 'warehouse_display',
            'status', 'order_date', 'requested_date', 'notes', 'total_amount', 'lines',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['number', 'status', 'created_by', 'created_at', 'updated_at']

    def validate(self, data):
        customer = data.get('customer', getattr(self.instance, 'customer', None))
        if customer and not customer.is_customer:
            raise serializers.ValidationError('Sales orders require a partner flagged is_customer.')
        if self.instance and self.instance.status != 'draft':
            raise serializers.ValidationError('Only draft sales orders can be edited.')
        return data

    def create(self, validated_data):
        from apps.core.models import DocumentSequence

        lines_data = validated_data.pop('lines')
        validated_data['created_by'] = self.context['request'].user
        validated_data['number'] = DocumentSequence.next_number('SO')
        order = SalesOrder.objects.create(**validated_data)
        for line_data in lines_data:
            SalesOrderLine.objects.create(order=order, **line_data)
        return order

    def update(self, instance, validated_data):
        lines_data = validated_data.pop('lines', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if lines_data is not None:
            instance.lines.all().delete()
            for line_data in lines_data:
                SalesOrderLine.objects.create(order=instance, **line_data)
        return instance
