from rest_framework import serializers

from .models import MRPRun, PlannedOrder


class PlannedOrderSerializer(serializers.ModelSerializer):
    product_display = serializers.StringRelatedField(source='product', read_only=True)
    run_display = serializers.StringRelatedField(source='run', read_only=True)

    class Meta:
        model = PlannedOrder
        fields = [
            'id', 'run', 'run_display', 'product', 'product_display', 'order_type',
            'quantity', 'due_date', 'release_date', 'expedited', 'demand_reference',
            'parent', 'status', 'converted_reference', 'created_at', 'updated_at',
        ]


class MRPRunSerializer(serializers.ModelSerializer):
    warehouse_display = serializers.StringRelatedField(source='warehouse', read_only=True)
    created_by_display = serializers.StringRelatedField(source='created_by', read_only=True)
    planned_order_count = serializers.IntegerField(source='planned_orders.count', read_only=True)

    class Meta:
        model = MRPRun
        fields = [
            'id', 'number', 'warehouse', 'warehouse_display', 'status', 'as_of_date',
            'log', 'error_message', 'completed_at', 'planned_order_count',
            'created_by', 'created_by_display', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'number', 'status', 'log', 'error_message', 'completed_at',
            'created_by', 'created_at', 'updated_at',
        ]
