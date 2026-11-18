from django.db.models import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import StockMove


@receiver(pre_delete, sender=StockMove, dispatch_uid='inventory.refuse_deleting_done_moves')
def refuse_deleting_done_moves(sender, instance, **kwargs):
    """
    Defence in depth behind the API's ProtectedHistoryDestroyMixin: a done move
    is part of the stock ledger that StockLevel caches and the GL posted, so no
    ORM, admin or cascading delete may remove it either. Connected in
    InventoryConfig.ready().
    """
    if instance.status == 'done':
        raise ProtectedError(f'Stock move {instance} is done: the stock ledger cannot be edited', [instance])
