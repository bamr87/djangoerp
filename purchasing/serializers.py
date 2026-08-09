from rest_framework import serializers

from .models import PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    product_display = serializers.StringRelatedField(source='product', read_only=True)
    open_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            'id', 'product', 'product_display', 'description', 'quantity', 'unit_price',
            'received_quantity', 'open_quantity', 'line_total',
        ]
        read_only_fields = ['received_quantity']

    def validate(self, data):
        if data.get('quantity') is not None and data['quantity'] <= 0:
            raise serializers.ValidationError('Line quantity must be positive.')
        if data.get('unit_price') is not None and data['unit_price'] < 0:
            raise serializers.ValidationError('unit_price cannot be negative.')
        return data


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True)
    supplier_display = serializers.StringRelatedField(source='supplier', read_only=True)
    warehouse_display = serializers.StringRelatedField(source='warehouse', read_only=True)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'number', 'supplier', 'supplier_display', 'warehouse', 'warehouse_display',
            'status', 'order_date', 'expected_date', 'notes', 'source_reference',
            'total_amount', 'lines', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['number', 'status', 'source_reference', 'created_by', 'created_at', 'updated_at']

    def validate(self, data):
        supplier = data.get('supplier', getattr(self.instance, 'supplier', None))
        if supplier and not supplier.is_supplier:
            raise serializers.ValidationError('Purchase orders require a partner flagged is_supplier.')
        if self.instance and self.instance.status != 'draft':
            raise serializers.ValidationError('Only draft purchase orders can be edited.')
        return data

    def create(self, validated_data):
        from core.models import DocumentSequence

        lines_data = validated_data.pop('lines')
        validated_data['created_by'] = self.context['request'].user
        validated_data['number'] = DocumentSequence.next_number('PO')
        order = PurchaseOrder.objects.create(**validated_data)
        for line_data in lines_data:
            PurchaseOrderLine.objects.create(order=order, **line_data)
        return order

    def update(self, instance, validated_data):
        lines_data = validated_data.pop('lines', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if lines_data is not None:
            instance.lines.all().delete()
            for line_data in lines_data:
                PurchaseOrderLine.objects.create(order=instance, **line_data)
        return instance
