from django.contrib.auth.models import User
from django.db import models

from core.models import TimestampMixin


class ReportTemplate(TimestampMixin):
    """
    Templates for financial reports
    """
    # NOTE: EDGAR-derived report types (edgar_statement, edgar_reconciliation) are not
    # available yet — they depend on the edgar app, which is a later consolidation
    # iteration (see amrs-project's edgar/README.md for the design being ported).
    REPORT_TYPES = (
        ('balance_sheet', 'Balance Sheet'),
        ('income_statement', 'Income Statement'),
        ('cash_flow', 'Cash Flow Statement'),
        ('general_ledger', 'General Ledger'),
        ('trial_balance', 'Trial Balance'),
        ('custom', 'Custom Report'),
    )

    name = models.CharField(max_length=100)
    report_type = models.CharField(max_length=32, choices=REPORT_TYPES)
    description = models.TextField(blank=True)
    configuration = models.JSONField(default=dict)
    is_system = models.BooleanField(default=False, help_text="System templates cannot be modified by users")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='report_templates')

    def __str__(self):
        return f"{self.name} ({self.get_report_type_display()})"


class SavedReport(models.Model):
    """
    Saved report instances generated from templates
    """
    STATUS_CHOICES = (
        ('generating', 'Generating'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    )

    template = models.ForeignKey(ReportTemplate, on_delete=models.CASCADE, related_name='saved_reports')
    name = models.CharField(max_length=100)
    parameters = models.JSONField(default=dict)
    result_data = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='generating')
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='saved_reports')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.created_at.strftime('%Y-%m-%d')})"
