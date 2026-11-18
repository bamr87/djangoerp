from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from core.audit import log_event
from core.models import DocumentSequence

from .models import JournalEntry, JournalLine

TWO_PLACES = Decimal('0.01')


def validate_lines(lines, *, skip_zero_lines):
    """
    The double-entry rules every write path into JournalEntry enforces: the API
    (JournalEntrySerializer.validate) and system postings (post_entry) both call
    this, and JournalLine's check constraints back it in the database.

    ``lines`` is an iterable of dicts with ``debit`` and ``credit`` (a missing
    side is zero). Amounts are quantized to the cent; each line must carry one
    non-negative side, never both; the entry needs a non-zero debit and a
    non-zero credit, and the totals must be equal. An all-zero line is dropped
    when ``skip_zero_lines`` is true (post_entry: a computed posting can produce
    one) and rejected when it is false (the API: a client sent it).

    Returns the kept lines as new dicts with the quantized amounts; raises DRF's
    ValidationError naming the first rule broken.
    """
    cleaned = []
    for line in lines:
        debit = Decimal(line.get('debit') or 0).quantize(TWO_PLACES)
        credit = Decimal(line.get('credit') or 0).quantize(TWO_PLACES)
        if debit < 0 or credit < 0:
            raise serializers.ValidationError('Journal line amounts cannot be negative')
        if debit and credit:
            raise serializers.ValidationError('A journal line carries a debit or a credit, not both')
        if not debit and not credit:
            if skip_zero_lines:
                continue
            raise serializers.ValidationError('A journal line needs a non-zero debit or credit')
        cleaned.append({**line, 'debit': debit, 'credit': credit})

    if not cleaned:
        raise serializers.ValidationError('Journal entry must have at least one non-zero line')

    total_debit = sum(line['debit'] for line in cleaned)
    total_credit = sum(line['credit'] for line in cleaned)
    if not total_debit or not total_credit:
        raise serializers.ValidationError('Journal entry needs at least one debit line and one credit line')
    if total_debit != total_credit:
        raise serializers.ValidationError(
            f'Journal entry must balance: debits {total_debit} != credits {total_credit}'
        )
    return cleaned


def next_entry_number():
    """
    Claim the next free number from the ``JE`` sequence, the one series manual
    (API) and system entries share.

    Numbers already held by an entry numbered outside the sequence (a client-set
    number from before the API made ``entry_number`` read-only, or one typed in
    the admin) are skipped, so the sequence walks past them instead of failing
    the next posting on the unique ``entry_number``.
    """
    while True:
        number = DocumentSequence.next_number('JE')
        if not JournalEntry.objects.filter(entry_number=number).exists():
            return number


def post_entry(*, date, description, lines, created_by=None, source_reference=None, status='posted'):
    """
    The single programmatic write path into the ledger for system-generated
    postings (goods receipts, shipments, production, invoice/payment posting).

    ``lines`` is an iterable of dicts: {'account': Account, 'debit': Decimal,
    'credit': Decimal, 'reference': str}. Enforces the same invariants as
    JournalEntrySerializer.validate through validate_lines; all-zero lines are
    dropped rather than rejected.

    Posting is idempotent per ``source_reference``: the column is unique, and a
    second call with the same reference returns the existing entry instead of
    double-posting (safe under Celery retries).
    """
    with transaction.atomic():
        if source_reference:
            existing = JournalEntry.objects.filter(source_reference=source_reference).first()
            if existing:
                return existing

        cleaned = validate_lines(lines, skip_zero_lines=True)

        entry = JournalEntry.objects.create(
            entry_number=next_entry_number(),
            date=date,
            description=description,
            status=status,
            created_by=created_by,
            source_reference=source_reference or None,
        )
        JournalLine.objects.bulk_create(
            JournalLine(
                entry=entry,
                account=line['account'],
                debit=line['debit'],
                credit=line['credit'],
                reference=line.get('reference') or None,
            )
            for line in cleaned
        )
        return entry


def reverse_entry(entry, *, user=None, date=None, description=None):
    """
    Correct a posted manual entry by posting its mirror image: the same accounts
    with debits and credits swapped, through post_entry, linked to the original
    by ``reverses`` and audit-logged as ``journal.reverse``. The original stays
    posted, so each period keeps the history it reported (constitution § 2).

    Idempotent: the reversal's ``source_reference`` is ``reversal:<pk>`` and
    ``reverses`` is one-to-one, so a repeat call returns the existing reversal.
    Returns ``(reversal, created)``.

    Only posted manual entries (no ``source_reference``) qualify. Drafts and
    voided entries are not ledger history, a reversal is not reversed again, and
    an entry a business document posted (goods receipt, shipment, invoice, ...)
    is corrected through that document so the two cannot drift apart.
    """
    with transaction.atomic():
        entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
        existing = JournalEntry.objects.filter(reverses=entry).first()
        if existing:
            return existing, False
        if entry.status != 'posted':
            raise serializers.ValidationError(
                f'Only a posted entry can be reversed ({entry.entry_number} is {entry.status})'
            )
        if entry.reverses_id:
            raise serializers.ValidationError(
                f'{entry.entry_number} is itself a reversal; post a new entry to restore what it reversed'
            )
        if entry.source_reference:
            raise serializers.ValidationError(
                f'{entry.entry_number} was posted by {entry.source_reference}; correct it through that document'
            )

        lines = [
            {'account': line.account, 'debit': line.credit, 'credit': line.debit, 'reference': line.reference}
            for line in entry.lines.select_related('account')
        ]
        reversal = post_entry(
            date=date or timezone.now().date(),
            description=description or f'Reversal of {entry.entry_number}',
            lines=lines,
            created_by=user,
            source_reference=f'reversal:{entry.pk}',
        )
        reversal.reverses = entry
        reversal.save(update_fields=['reverses'])
        log_event(user, 'journal.reverse', entry, {'reversal': reversal.entry_number})
        return reversal, True
