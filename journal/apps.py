from django.apps import AppConfig


class JournalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'journal'

    def ready(self):
        # pre_delete guards: posted or voided journal entries cannot be deleted through the ORM either.
        from . import signals  # noqa: F401
