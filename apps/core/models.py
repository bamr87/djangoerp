from django.db import models, transaction


class TimestampMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DocumentSequence(TimestampMixin):
    """
    Atomic per-prefix document numbering (JE-00001, PO-00001, SO-00001, ...).

    Every document app claims its numbers through next_number() instead of
    hand-rolling its own counter (the "naming series" pattern from ERPNext).
    Rows are created lazily the first time a prefix is used, and the increment
    happens under select_for_update inside a transaction so concurrent posters
    cannot claim the same number.
    """

    prefix = models.CharField(max_length=10, unique=True)
    next_value = models.PositiveIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=5)

    def __str__(self):
        return f"{self.prefix} (next: {self.next_value})"

    @classmethod
    def next_number(cls, prefix):
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(prefix=prefix)
            number = seq.next_value
            seq.next_value = number + 1
            seq.save(update_fields=['next_value', 'updated_at'])
            return f"{prefix}-{number:0{seq.padding}d}"
