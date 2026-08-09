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
