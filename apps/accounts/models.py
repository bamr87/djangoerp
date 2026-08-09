from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from apps.core.models import TimestampMixin


class UserRole(TimestampMixin):
    """
    Custom user roles as specified in the design document:
    Admin, Accountant, Viewer, Auditor
    """
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('accountant', 'Accountant'),
        ('viewer', 'Viewer'),
        ('auditor', 'Auditor'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='role')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='viewer')

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


class AuditLog(models.Model):
    """
    Audit trail for security monitoring
    """
    ACTION_CHOICES = (
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('view', 'View'),
        ('login', 'Login'),
        ('logout', 'Logout'),
    )

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    timestamp = models.DateTimeField(default=timezone.now)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=50, blank=True, null=True)
    object_repr = models.CharField(max_length=200, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True)
    details = models.JSONField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['action']),
            models.Index(fields=['timestamp']),
            models.Index(fields=['model_name']),
        ]

    def __str__(self):
        return f"{self.get_action_display()} by {self.user} at {self.timestamp}"
