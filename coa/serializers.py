from rest_framework import serializers

from .models import Account, AccountType


class AccountTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountType
        fields = ['id', 'code', 'name', 'description']


class AccountSerializer(serializers.ModelSerializer):
    account_type_display = serializers.StringRelatedField(source='account_type', read_only=True)
    parent_account_display = serializers.StringRelatedField(source='parent_account', read_only=True)

    class Meta:
        model = Account
        fields = [
            'id', 'code', 'name', 'account_type', 'account_type_display',
            'is_active', 'parent_account', 'parent_account_display',
            'description', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def validate(self, data):
        # Prevent circular references in parent-child relationship
        if 'parent_account' in data and data['parent_account']:
            parent = data['parent_account']
            if self.instance and self.instance.id == parent.id:
                raise serializers.ValidationError("An account cannot be its own parent.")

            if self.instance:
                current_parent = parent
                while current_parent:
                    if current_parent.id == self.instance.id:
                        raise serializers.ValidationError("Cannot create circular hierarchy.")
                    current_parent = current_parent.parent_account

        return data


class AccountTreeSerializer(serializers.ModelSerializer):
    """Serializer for displaying account hierarchy"""
    children = serializers.SerializerMethodField()
    account_type_display = serializers.StringRelatedField(source='account_type')

    class Meta:
        model = Account
        fields = ['id', 'code', 'name', 'account_type', 'account_type_display', 'is_active', 'children']

    def get_children(self, obj):
        children = Account.objects.filter(parent_account=obj)
        return AccountTreeSerializer(children, many=True).data
