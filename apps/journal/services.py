from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.core.models import DocumentSequence

from .models import JournalEntry, JournalLine

TWO_PLACES = Decimal('0.01')


def post_entry(*, date, description, lines, created_by=None, source_reference=None, status='posted'):
    """
    The single programmatic write path into the ledger for system-generated
    postings (goods receipts, shipments, production, invoice/payment posting).

    ``lines`` is an iterable of dicts: {'account': Account, 'debit': Decimal,
    'credit': Decimal, 'reference': str}. Enforces the same invariants as
    JournalEntrySerializer.validate — at least one line, debits equal credits —
    plus non-negative amounts and a non-zero total.

    Posting is idempotent per ``source_reference``: the column is unique, and a
    second call with the same reference returns the existing entry instead of
    double-posting (safe under Celery retries).
    """
    with transaction.atomic():
        if source_reference:
            existing = JournalEntry.objects.filter(source_reference=source_reference).first()
            if existing:
                return existing

        cleaned = []
        for line in lines:
            debit = Decimal(line.get('debit') or 0).quantize(TWO_PLACES)
            credit = Decimal(line.get('credit') or 0).quantize(TWO_PLACES)
            if debit < 0 or credit < 0:
                raise serializers.ValidationError('Journal line amounts cannot be negative')
            if debit == 0 and credit == 0:
                continue
            cleaned.append({
                'account': line['account'],
                'debit': debit,
                'credit': credit,
                'reference': line.get('reference') or None,
            })

        if not cleaned:
            raise serializers.ValidationError('Journal entry must have at least one non-zero line')

        total_debit = sum(line['debit'] for line in cleaned)
        total_credit = sum(line['credit'] for line in cleaned)
        if total_debit != total_credit:
            raise serializers.ValidationError(
                f'Journal entry must balance: debits {total_debit} != credits {total_credit}'
            )

        entry = JournalEntry.objects.create(
            entry_number=DocumentSequence.next_number('JE'),
            date=date,
            description=description,
            status=status,
            created_by=created_by,
            source_reference=source_reference or None,
        )
        JournalLine.objects.bulk_create(
            JournalLine(entry=entry, **line) for line in cleaned
        )
        return entry
