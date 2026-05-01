from django.contrib import admin

from Organization.models import Organization, Office, Department


class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "city", "state", "country", "phone_number", "email", "pincode", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "address", "city", "state", "country", "phone_number", "email", "pincode")
    list_editable = ("is_active",)
    list_per_page = 10
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    fields = ("name", "address", "city", "state", "country", "phone_number", "email", "pincode", "is_active")


admin.site.register(Organization, OrganizationAdmin)


class OfficeAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "full_address", "num_biometric_devices", "managers_list", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "location", "full_address", "num_biometric_devices")
    list_editable = ("is_active",)
    list_per_page = 10
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    filter_horizontal = ("managers",)
    fields = ("name", "location", "full_address", "num_biometric_devices", "managers", "is_active")

    def managers_list(self, obj):
        return ", ".join(m.name or m.email for m in obj.managers.all()) or "-"

    managers_list.short_description = "Managers"


admin.site.register(Office, OfficeAdmin)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "office", "is_active", "created_at")
    list_filter = ("is_active", "office__organization")
    search_fields = ("name", "office__name")
    ordering = ("office", "name")
