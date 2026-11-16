from django.db.models import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import JournalEntry


@receiver(pre_delete, sender=JournalEntry, dispatch_uid='journal.refuse_deleting_posted_entries')
def refuse_deleting_posted_entries(sender, instance, **kwargs):
    """
    Defence in depth behind the API's ProtectedHistoryDestroyMixin: a posted or
    voided entry is ledger history, so no ORM, admin or cascading delete may
    remove it either. Only drafts can go. Connected in JournalConfig.ready().
    """
    if instance.status != 'draft':
        raise ProtectedError(
            f'Journal entry {instance} is {instance.status}: posted history cannot be deleted', [instance],
        )
