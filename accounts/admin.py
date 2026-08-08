from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import AuditLog, UserRole


class UserRoleInline(admin.StackedInline):
    model = UserRole
    can_delete = False
    verbose_name = 'User Role'
    verbose_name_plural = 'User Roles'


class UserAdmin(BaseUserAdmin):
    inlines = (UserRoleInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'get_role')

    def get_role(self, obj):
        try:
            return obj.role.get_role_display()
        except UserRole.DoesNotExist:
            return '-'
    get_role.short_description = 'Role'


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'timestamp', 'action', 'model_name', 'object_repr', 'ip_address')
    list_filter = ('action', 'model_name', 'user', 'timestamp')
    search_fields = ('user__username', 'object_repr', 'details')
    readonly_fields = ('user', 'timestamp', 'action', 'model_name', 'object_id', 'object_repr', 'ip_address',
                       'user_agent', 'details')
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and obj is None
