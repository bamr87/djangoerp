from django.contrib.auth.models import User
from django.db import models

from apps.coa.models import Account


class JournalEntry(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('voided', 'Voided'),
    ]

    entry_number = models.CharField(max_length=20, unique=True)
    date = models.DateField()
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    # Nullable so system-generated entries (scheduled imports, Celery tasks) have
    # an author field they can leave empty, and SET_NULL so deleting a user cannot take
    # their journal entries with it. Matches Invoice/SavedReport.created_by.
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    # Set by journal.services.post_entry for system-generated postings — one business
    # event, one entry. Unique (NULLs exempt) so a retried flow cannot double-post the
    # same event; manual API entries leave it null.
    source_reference = models.CharField(max_length=64, unique=True, null=True, blank=True)

    def __str__(self):
        return self.entry_number


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, related_name='lines', on_delete=models.CASCADE)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    # Wider than the 12,2 used elsewhere: a large filer's or consolidated ledger's balance
    # sheet can run to hundreds of billions (12,2 caps out below $10bn).
    debit = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.entry.entry_number}: {self.account.code}"
