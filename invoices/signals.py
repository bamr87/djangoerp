from django.db.models import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import Invoice, Payment


@receiver(pre_delete, sender=Invoice, dispatch_uid='invoices.refuse_deleting_posted_invoices')
@receiver(pre_delete, sender=Payment, dispatch_uid='invoices.refuse_deleting_posted_payments')
def refuse_deleting_posted_documents(sender, instance, **kwargs):
    """
    Defence in depth behind the API's ProtectedHistoryDestroyMixin: an invoice
    or payment that has posted to the ledger is history, so no ORM, admin or
    cascading delete may remove it either (its journal entry would be left
    without the document that explains it). Connected in InvoicesConfig.ready().
    """
    if instance.journal_entry_id:
        raise ProtectedError(
            f'{sender._meta.verbose_name.capitalize()} {instance} is posted: posted history cannot be deleted',
            [instance],
        )
