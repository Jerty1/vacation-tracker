from django.contrib import admin

from .models import Employee, StatusChange, Vacation


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "position")
    search_fields = ("full_name", "position")
    readonly_fields = ("created_at",)


class ReadOnlyAdmin(admin.ModelAdmin):
    """All vacation changes go through the transactional API service."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StatusChangeInline(admin.TabularInline):
    model = StatusChange
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "changed_by", "changed_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Vacation)
class VacationAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "employee",
        "start_date",
        "end_date",
        "days",
        "status",
    )
    list_filter = ("status", "start_date")
    search_fields = ("employee__full_name",)
    list_select_related = ("employee",)
    inlines = (StatusChangeInline,)


@admin.register(StatusChange)
class StatusChangeAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "vacation_id",
        "from_status",
        "to_status",
        "changed_by",
        "changed_at",
    )
    list_filter = ("to_status",)
    search_fields = ("changed_by",)
