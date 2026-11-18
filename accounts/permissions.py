from rest_framework import permissions


class IsAdmin(permissions.BasePermission):
    """
    Permission class that checks if user has admin role
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        try:
            return request.user.role.role == 'admin'
        except AttributeError:
            return False


class IsAdminOrSelf(permissions.BasePermission):
    """
    Permission class that allows users to edit their own profile,
    but only admins can edit other users' profiles
    """
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True

        if obj == request.user:
            return True

        try:
            return request.user.role.role == 'admin'
        except AttributeError:
            return False


class IsAccountant(permissions.BasePermission):
    """
    Permission class that checks if user has accountant role
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        try:
            return request.user.role.role in ('accountant', 'admin')
        except AttributeError:
            return False


class IsAuditor(permissions.BasePermission):
    """
    Permission class that checks if user has auditor role
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        try:
            return request.user.role.role in ('auditor', 'admin')
        except AttributeError:
            return False


def _role(user):
    """The user's role name, or None for an anonymous user or one with no `UserRole` row."""
    if not user.is_authenticated:
        return None
    try:
        return user.role.role
    except AttributeError:
        return None


class IsAccountantOrAuditorReadOnly(permissions.BasePermission):
    """
    Ledger data: safe methods need the accountant or auditor role (admin implies both),
    every write needs the accountant role. Viewers and role-less users are denied.
    """
    def has_permission(self, request, view):
        role = _role(request.user)
        if request.method in permissions.SAFE_METHODS:
            return role in ('accountant', 'auditor', 'admin')
        return role in ('accountant', 'admin')


class IsAccountantOrReadOnly(permissions.BasePermission):
    """
    Safe methods are open to any authenticated user with a role (viewers included),
    every write needs the accountant role. Role-less users are denied.
    """
    def has_permission(self, request, view):
        role = _role(request.user)
        if request.method in permissions.SAFE_METHODS:
            return role is not None
        return role in ('accountant', 'admin')
