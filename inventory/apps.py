from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'

    def ready(self):
        # pre_delete guards: done stock moves cannot be deleted through the ORM either.
        from . import signals  # noqa: F401
