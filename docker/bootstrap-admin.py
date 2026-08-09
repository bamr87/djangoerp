"""
Idempotent admin bootstrap for the local Docker stack.

Creates the superuser named by DJANGO_SUPERUSER_* and — critically — the
matching accounts.UserRole row. `manage.py createsuperuser` alone is not
enough here: every DRF permission class in accounts/permissions.py reads
`request.user.role.role` inside a try/except AttributeError, so a superuser
with no UserRole can open /admin/ but is denied by IsAdmin, IsAccountant and
IsAuditor on every API endpoint.

Never resets an existing account's password — edit .env and recreate the user
by hand if you need to rotate it. The role IS kept in sync with .env, because
that is the field that decides whether the API works at all.
"""
import os
import sys

import django

# This script lives in /usr/local/bin, so Python puts *that* directory on
# sys.path — not the project root. Without this the settings import fails with
# ModuleNotFoundError: No module named 'djangoerp'.
sys.path.insert(0, os.environ.get('APP_DIR', '/app'))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djangoerp.settings')
django.setup()

from django.contrib.auth.models import User  # noqa: E402

from accounts.models import UserRole  # noqa: E402

username = os.environ.get('DJANGO_SUPERUSER_USERNAME', '').strip()
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()
role = os.environ.get('DJANGO_SUPERUSER_ROLE', 'admin').strip() or 'admin'

if not username or not password:
    raise SystemExit(0)

valid_roles = {choice for choice, _ in UserRole.ROLE_CHOICES}
if role not in valid_roles:
    raise SystemExit(f"==> DJANGO_SUPERUSER_ROLE={role!r} is not one of {sorted(valid_roles)}")

user, created = User.objects.get_or_create(
    username=username,
    defaults={'email': email, 'is_staff': True, 'is_superuser': True},
)

if created:
    user.set_password(password)
    user.save(update_fields=['password'])
    print(f"==> created superuser {username!r}")
else:
    print(f"==> superuser {username!r} already exists (password left as is)")

user_role, role_created = UserRole.objects.get_or_create(user=user, defaults={'role': role})
if role_created:
    print(f"==> granted role {role!r} to {username!r}")
elif user_role.role != role:
    previous = user_role.role
    user_role.role = role
    user_role.save(update_fields=['role'])
    print(f"==> role for {username!r}: {previous!r} -> {role!r} (synced from .env)")
