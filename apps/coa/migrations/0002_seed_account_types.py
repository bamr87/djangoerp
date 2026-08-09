# Seeds the five AccountType codes report_generators.py hard-codes (AS/LI/EQ/RE/EX).
# Every report generator branches on these two-letter codes to decide normal balance
# and report section, so a fresh database needs them to exist before any account can
# be created against a working type.

from django.db import migrations

ACCOUNT_TYPES = [
    ("AS", "Asset"),
    ("LI", "Liability"),
    ("EQ", "Equity"),
    ("RE", "Revenue"),
    ("EX", "Expense"),
]


def seed_account_types(apps, schema_editor):
    AccountType = apps.get_model("coa", "AccountType")
    for code, name in ACCOUNT_TYPES:
        AccountType.objects.get_or_create(code=code, defaults={"name": name})


def remove_account_types(apps, schema_editor):
    AccountType = apps.get_model("coa", "AccountType")
    AccountType.objects.filter(code__in=[code for code, _ in ACCOUNT_TYPES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("coa", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_account_types, remove_account_types),
    ]
