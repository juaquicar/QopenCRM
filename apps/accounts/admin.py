from django.contrib import admin

from .models import AccessLog, Role, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["email", "name", "role", "is_active", "is_superuser"]
    search_fields = ["email", "name"]
    exclude = ["password", "groups", "user_permissions", "last_login"]


admin.site.register(Role)


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "user", "action", "object_type", "object_repr", "channel", "ip"]
    list_filter = ["action", "channel"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
