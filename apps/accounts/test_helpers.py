from django.contrib.auth.models import User

from .models import UserRole


def create_user(username, password='pass', role='viewer'):
    """Create a plain auth User plus its UserRole, for tests across apps."""
    user = User.objects.create_user(username=username, password=password)
    UserRole.objects.create(user=user, role=role)
    return user
