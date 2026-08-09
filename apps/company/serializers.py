from rest_framework import serializers

from .models import Company


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            'id', 'name', 'legal_name', 'currency_code', 'email', 'phone',
            'address', 'tax_id', 'founded_on', 'is_active',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def validate_currency_code(self, value):
        if not value.isalpha() or len(value) != 3:
            raise serializers.ValidationError('Currency code must be three letters (ISO 4217), e.g. USD.')
        return value.upper()
