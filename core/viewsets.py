from rest_framework.exceptions import ValidationError

PROTECTED_HISTORY = 'Posted history cannot be deleted: void, reverse or cancel it instead.'


class ProtectedHistoryDestroyMixin:
    """
    Refuse DELETE once a document has become history.

    Posted history is immutable (constitution § 2): a posted journal entry, a
    posted invoice or payment, a done stock move or a confirmed order is
    corrected by voiding, reversing, an opposing move or cancelling, never by
    deleting it. Put this mixin before ``ModelViewSet`` in a viewset's bases;
    ``perform_destroy`` asks ``check_deletable`` first, which by default allows
    only ``status == 'draft'``. A viewset with another rule overrides
    ``check_deletable`` and raises through ``refuse_delete``.
    """

    def check_deletable(self, instance):
        if instance.status != 'draft':
            self.refuse_delete(instance, f'it is {instance.status}')

    def refuse_delete(self, instance, why):
        raise ValidationError(f'{instance} cannot be deleted because {why}. {PROTECTED_HISTORY}')

    def perform_destroy(self, instance):
        self.check_deletable(instance)
        super().perform_destroy(instance)
