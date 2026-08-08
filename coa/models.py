from django.db import models

from core.models import TimestampMixin


class AccountType(models.Model):
    """
    Account types such as Asset, Liability, Equity, Revenue, Expense
    """
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=2, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Account(TimestampMixin):
    """
    Chart of Accounts model with hierarchical structure
    """
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    account_type = models.ForeignKey(AccountType, on_delete=models.PROTECT, related_name='accounts')
    is_active = models.BooleanField(default=True)
    parent_account = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT, related_name='child_accounts'
    )
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['code']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['account_type']),
            models.Index(fields=['parent_account']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def full_name(self):
        """Returns the full hierarchical name of the account"""
        if self.parent_account:
            return f"{self.parent_account.full_name} > {self.name}"
        return self.name

    @property
    def level(self):
        """Returns the level of the account in the hierarchy"""
        if self.parent_account is None:
            return 1
        return self.parent_account.level + 1
