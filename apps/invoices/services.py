from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from apps.core.audit import log_event
from apps.journal.services import post_entry

VALUE_PLACES = Decimal('0.01')


def post_invoice(invoice, user=None, posting_date=None):
    """
    Post a customer invoice to the ledger: Dr the partner's receivable account
    for the total, Cr revenue per line (product category revenue account, else
    the invoice's fallback revenue_account). Once posted the invoice is history —
    corrections are credit notes, not edits.
    """
    if invoice.journal_entry_id:
        raise serializers.ValidationError(f'{invoice.invoice_number} is already posted')
    if invoice.status not in ('draft', 'sent'):
        raise serializers.ValidationError(f'Only draft or sent invoices can post (invoice is {invoice.status})')
    if not invoice.partner_id:
        raise serializers.ValidationError('Link a business partner before posting — it carries the AR account')
    if not invoice.partner.receivable_account_id:
        raise serializers.ValidationError(
            f'Partner {invoice.partner.code} has no receivable_account configured'
        )

    line_items = list(invoice.line_items.select_related('product__category'))
    if not line_items:
        raise serializers.ValidationError('Cannot post an invoice with no line items')

    revenue_by_account = {}
    for item in line_items:
        account = None
        if item.product_id and item.product.category_id:
            account = item.product.category.revenue_account
        account = account or invoice.revenue_account
        if account is None:
            raise serializers.ValidationError(
                f'Line "{item.description}" has no revenue account: set the product category '
                f'revenue_account or the invoice revenue_account'
            )
        amount = Decimal(item.total).quantize(VALUE_PLACES)
        revenue_by_account[account] = revenue_by_account.get(account, Decimal('0.00')) + amount

    total = sum(revenue_by_account.values(), Decimal('0.00'))
    if total != Decimal(invoice.total).quantize(VALUE_PLACES):
        raise serializers.ValidationError(
            f'Invoice total {invoice.total} does not match the sum of its lines {total}'
        )

    with transaction.atomic():
        entry = post_entry(
            date=posting_date or timezone.now().date(),
            description=f'Invoice {invoice.invoice_number} ({invoice.customer})',
            lines=[
                {'account': invoice.partner.receivable_account, 'debit': total,
                 'reference': invoice.invoice_number},
            ] + [
                {'account': account, 'credit': amount, 'reference': invoice.invoice_number}
                for account, amount in revenue_by_account.items()
            ],
            created_by=user,
            source_reference=f'invoice:{invoice.invoice_number}',
        )
        invoice.journal_entry = entry
        if invoice.status == 'draft':
            invoice.status = 'sent'
        invoice.save(update_fields=['journal_entry', 'status', 'updated_at'])
        log_event(user, 'invoice.post', invoice, {'journal_entry': entry.entry_number})
        return invoice


def post_payment(payment, user=None):
    """
    Post a customer payment: Dr the deposit (cash/bank) account, Cr the
    partner's receivable. Flips the invoice to paid when posted payments cover
    its total.
    """
    invoice = payment.invoice
    if payment.journal_entry_id:
        raise serializers.ValidationError(f'{payment.payment_reference} is already posted')
    if not invoice.journal_entry_id:
        raise serializers.ValidationError('Post the invoice before posting payments against it')
    if not payment.deposit_account_id:
        raise serializers.ValidationError('Set deposit_account (the cash/bank account) before posting')
    amount = Decimal(payment.amount).quantize(VALUE_PLACES)
    if amount <= 0:
        raise serializers.ValidationError('Payment amount must be positive')

    with transaction.atomic():
        entry = post_entry(
            date=payment.payment_date,
            description=f'Payment {payment.payment_reference} for {invoice.invoice_number}',
            lines=[
                {'account': payment.deposit_account, 'debit': amount, 'reference': payment.payment_reference},
                {'account': invoice.partner.receivable_account, 'credit': amount,
                 'reference': payment.payment_reference},
            ],
            created_by=user,
            source_reference=f'payment:{payment.payment_reference}',
        )
        payment.journal_entry = entry
        payment.save(update_fields=['journal_entry'])

        posted_total = invoice.payments.filter(journal_entry__isnull=False).aggregate(
            total=Sum('amount'))['total'] or Decimal('0')
        if posted_total >= invoice.total:
            invoice.status = 'paid'
            invoice.save(update_fields=['status', 'updated_at'])
        log_event(user, 'payment.post', payment, {'journal_entry': entry.entry_number})
        return payment
