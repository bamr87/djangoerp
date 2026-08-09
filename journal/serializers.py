from rest_framework import serializers

from .models import JournalEntry, JournalLine


class JournalLineSerializer(serializers.ModelSerializer):
    account_display = serializers.StringRelatedField(source='account', read_only=True)

    class Meta:
        model = JournalLine
        fields = ['id', 'account', 'account_display', 'debit', 'credit', 'reference']


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True)
    created_by_display = serializers.StringRelatedField(source='created_by', read_only=True)
    total_debit = serializers.SerializerMethodField()
    total_credit = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = [
            'id', 'entry_number', 'date', 'description', 'status', 'source_reference',
            'created_by', 'created_by_display', 'lines', 'total_debit', 'total_credit'
        ]
        read_only_fields = ['created_by', 'source_reference']

    def get_total_debit(self, obj):
        return sum(line.debit for line in obj.lines.all())

    def get_total_credit(self, obj):
        return sum(line.credit for line in obj.lines.all())

    def validate(self, data):
        """
        Check that the total debits equal total credits (balanced entry), and
        that posted history stays immutable: a posted entry can only be voided,
        never edited — corrections are reversing entries.
        """
        if self.instance and self.instance.status == 'voided':
            raise serializers.ValidationError("Voided entries are immutable")
        if self.instance and self.instance.status == 'posted':
            if data.get('status') != 'voided':
                raise serializers.ValidationError(
                    "Posted entries are ledger history: void them or post a reversing entry, don't edit"
                )
            for field in ('entry_number', 'date', 'description'):
                if field in data and data[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(f"Cannot change {field} while voiding a posted entry")
            if 'lines' in data:
                raise serializers.ValidationError("Cannot change lines while voiding a posted entry")
            return data

        lines = data.get('lines', [])
        if not lines:
            raise serializers.ValidationError("Journal entry must have at least one line")

        total_debit = sum(line['debit'] for line in lines)
        total_credit = sum(line['credit'] for line in lines)

        if total_debit != total_credit:
            raise serializers.ValidationError("Journal entry must balance: total debits must equal total credits")

        return data

    def create(self, validated_data):
        lines_data = validated_data.pop('lines')

        validated_data['created_by'] = self.context['request'].user

        journal_entry = JournalEntry.objects.create(**validated_data)

        for line_data in lines_data:
            JournalLine.objects.create(entry=journal_entry, **line_data)

        return journal_entry

    def update(self, instance, validated_data):
        lines_data = validated_data.pop('lines', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if lines_data is not None:
            instance.lines.all().delete()

            for line_data in lines_data:
                JournalLine.objects.create(entry=instance, **line_data)

        return instance
