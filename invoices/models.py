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
    due_date = models.DateField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft")
    total = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.invoice_number


class InvoiceLineItem(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="line_items", on_delete=models.CASCADE)
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
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.payment_reference
