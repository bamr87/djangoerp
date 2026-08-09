from django.conf import settings
from django.db import models

from core.models import TimestampMixin


class Invoice(TimestampMixin):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent"),
        ("paid", "Paid"),
        ("cancelled", "Cancelled"),
        ("overdue", "Overdue"),
    ]
    invoice_number = models.CharField(max_length=32, unique=True)
    customer = models.CharField(max_length=255)
    vendor = models.CharField(max_length=255, blank=True, null=True)
    # Structured links added by the ERP consolidation; the legacy customer/vendor
    # name fields stay for API compatibility. partner is required to post the
    # invoice to the ledger (it carries the receivable control account).
    partner = models.ForeignKey(
        "partners.BusinessPartner", on_delete=models.PROTECT, null=True, blank=True, related_name="invoices"
    )
    sales_order = models.ForeignKey(
        "sales.SalesOrder", on_delete=models.PROTECT, null=True, blank=True, related_name="invoices"
    )
    due_date = models.DateField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft")
    total = models.DecimalField(max_digits=12, decimal_places=2)
    # Fallback revenue account for lines whose product has no category revenue
    # account (manual invoices without products must set it before posting).
    revenue_account = models.ForeignKey(
        "coa.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="revenue_invoices"
    )
    # Set when the invoice posts; a posted invoice is ledger history and can only
    # be corrected by a credit note, never edited.
    journal_entry = models.ForeignKey(
        "journal.JournalEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="invoices"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.invoice_number


class InvoiceLineItem(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="line_items", on_delete=models.CASCADE)
    # Optional product link so posting can resolve the line's revenue account from
    # the product category; free-text lines fall back to Invoice.revenue_account.
    product = models.ForeignKey(
        "products.Product", on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.description} ({self.invoice.invoice_number})"


class Payment(models.Model):
    PAYMENT_METHODS = [
        ("bank_transfer", "Bank Transfer"),
        ("credit_card", "Credit Card"),
        ("cash", "Cash"),
        ("check", "Check"),
        ("other", "Other"),
    ]
    payment_reference = models.CharField(max_length=64, unique=True)
    invoice = models.ForeignKey(Invoice, related_name="payments", on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=32, choices=PAYMENT_METHODS)
    payment_date = models.DateField()
    # The cash/bank account this payment lands in ("deposit to"); required to post.
    deposit_account = models.ForeignKey(
        "coa.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="deposit_payments"
    )
    # Set when the payment posts (Dr deposit account / Cr partner receivable).
    journal_entry = models.ForeignKey(
        "journal.JournalEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="payments"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.payment_reference
