from django.db import transaction
from rest_framework import serializers

from .models import JournalEntry, JournalLine
from .services import next_entry_number, validate_lines


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
        # entry_number comes from the JE sequence in create(), the series system postings
        # (post_entry) draw from too; a client-chosen number could take one the sequence
        # has yet to reach and make that later posting fail on the unique column.
        read_only_fields = ['entry_number', 'created_by', 'source_reference']

    def get_total_debit(self, obj):
        return sum(line.debit for line in obj.lines.all())

    def get_total_credit(self, obj):
        return sum(line.credit for line in obj.lines.all())

    def validate(self, data):
        """
        Enforce double entry through journal.services.validate_lines — the same
        rules post_entry applies — and keep posted history immutable: a posted
        entry can only be voided, never edited; corrections are reversing entries.
        """
        if self.instance and self.instance.status == 'voided':
            raise serializers.ValidationError("Voided entries are immutable")
        if self.instance and self.instance.status == 'posted':
            if data.get('status') != 'voided':
                raise serializers.ValidationError(
                    "Posted entries are ledger history: void them or post a reversing entry, don't edit"
                )
            for field in ('date', 'description'):
                if field in data and data[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(f"Cannot change {field} while voiding a posted entry")
            if 'lines' in data:
                raise serializers.ValidationError("Cannot change lines while voiding a posted entry")
            return data

        lines = data.get('lines')
        if lines is None and self.instance is not None:
            # A partial update that leaves the lines alone (e.g. PATCH {"status": "posted"} on a
            # draft) is still a write into the ledger: the lines it keeps must pass too.
            lines = list(self.instance.lines.values('debit', 'credit'))
        if not lines:
            raise serializers.ValidationError("Journal entry must have at least one line")
        validate_lines(lines, skip_zero_lines=False)
        return data

    def create(self, validated_data):
        lines_data = validated_data.pop('lines')

        validated_data['created_by'] = self.context['request'].user

        with transaction.atomic():
            journal_entry = JournalEntry.objects.create(entry_number=next_entry_number(), **validated_data)

            for line_data in lines_data:
                JournalLine.objects.create(entry=journal_entry, **line_data)

        return journal_entry

    def update(self, instance, validated_data):
        lines_data = validated_data.pop('lines', None)

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if lines_data is not None:
                instance.lines.all().delete()

                for line_data in lines_data:
                    JournalLine.objects.create(entry=instance, **line_data)

        return instance
