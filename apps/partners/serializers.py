from rest_framework import serializers

from .models import BusinessPartner


class BusinessPartnerSerializer(serializers.ModelSerializer):
    receivable_account_display = serializers.StringRelatedField(source='receivable_account', read_only=True)
    payable_account_display = serializers.StringRelatedField(source='payable_account', read_only=True)

    class Meta:
        model = BusinessPartner
        fields = [
            'id', 'code', 'name', 'is_customer', 'is_supplier', 'email', 'phone',
            'address', 'tax_id', 'receivable_account', 'receivable_account_display',
            'payable_account', 'payable_account_display', 'is_active',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def validate(self, data):
        is_customer = data.get('is_customer', getattr(self.instance, 'is_customer', False))
        is_supplier = data.get('is_supplier', getattr(self.instance, 'is_supplier', False))
        if not is_customer and not is_supplier:
            raise serializers.ValidationError('A partner must be a customer, a supplier, or both.')
        return data
