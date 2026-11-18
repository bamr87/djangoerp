from django.apps import AppConfig


class InvoicesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'invoices'

    def ready(self):
        # pre_delete guards: posted invoices and payments cannot be deleted through the ORM either.
        from . import signals  # noqa: F401
