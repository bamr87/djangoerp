from accounts.models import AuditLog


def log_event(user, event, instance, details=None):
    """
    Record a document-flow event (confirm, receive, ship, complete, post, convert)
    in the audit trail.

    AuditLog's action choices predate document workflows, so events are stored as
    'update' actions with the precise event name in details['event']. Service
    functions are the writers: every state transition that moves stock or money
    passes through a service, so logging here gives the trail real coverage
    without middleware.
    """
    payload = {'event': event}
    if details:
        payload.update(details)
    AuditLog.objects.create(
        user=user if getattr(user, 'pk', None) else None,
        action='update',
        model_name=instance._meta.label,
        object_id=str(instance.pk),
        object_repr=str(instance)[:200],
        details=payload,
    )
