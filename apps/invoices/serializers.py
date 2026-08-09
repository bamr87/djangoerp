from rest_framework import serializers

from .models import Invoice, InvoiceLineItem, Payment


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = ['id', 'product', 'description', 'quantity', 'unit_price', 'total']


class InvoiceSerializer(serializers.ModelSerializer):
    line_items = InvoiceLineItemSerializer(many=True)
    partner_display = serializers.StringRelatedField(source='partner', read_only=True)
    sales_order_display = serializers.StringRelatedField(source='sales_order', read_only=True)
    journal_entry_display = serializers.StringRelatedField(source='journal_entry', read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_number', 'customer', 'vendor', 'partner', 'partner_display',
            'sales_order', 'sales_order_display', 'due_date', 'status', 'total',
            'revenue_account', 'journal_entry', 'journal_entry_display',
            'created_at', 'updated_at', 'created_by', 'line_items'
        ]
        read_only_fields = ['journal_entry', 'created_at', 'updated_at', 'created_by']

    def validate(self, data):
        if self.instance and self.instance.journal_entry_id:
            raise serializers.ValidationError(
                'Posted invoices are ledger history — issue a credit note instead of editing.'
            )
        partner = data.get('partner', getattr(self.instance, 'partner', None))
        if partner and not partner.is_customer:
            raise serializers.ValidationError('Invoice partner must be flagged is_customer.')
        return data

    def create(self, validated_data):
        line_items_data = validated_data.pop('line_items')
        invoice = Invoice.objects.create(**validated_data)
        for item_data in line_items_data:
            InvoiceLineItem.objects.create(invoice=invoice, **item_data)
        return invoice

    def update(self, instance, validated_data):
        line_items_data = validated_data.pop('line_items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if line_items_data is not None:
            instance.line_items.all().delete()
            for item_data in line_items_data:
                InvoiceLineItem.objects.create(invoice=instance, **item_data)
        return instance


class PaymentSerializer(serializers.ModelSerializer):
    journal_entry_display = serializers.StringRelatedField(source='journal_entry', read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id', 'payment_reference', 'invoice', 'amount', 'payment_method',
            'payment_date', 'deposit_account', 'journal_entry', 'journal_entry_display',
            'created_by', 'created_at'
        ]
        read_only_fields = ['journal_entry', 'created_by', 'created_at']

    def validate(self, data):
        if self.instance and self.instance.journal_entry_id:
            raise serializers.ValidationError('Posted payments are ledger history and cannot be edited.')
        amount = data.get('amount', getattr(self.instance, 'amount', None))
        if amount is not None and amount <= 0:
            raise serializers.ValidationError('Payment amount must be positive.')
        return data
