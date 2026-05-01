from django.contrib import admin

from Biometric.models import BiometricDevice


@admin.register(BiometricDevice)
class BiometricDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id",
        "name",
        "serial_number",
        "ip_address",
        "device_location",
        "device_direction",
        "device_type",
        "office",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "office", "device_type", "device_direction")
    search_fields = (
        "device_id",
        "name",
        "serial_number",
        "ip_address",
        "device_location",
        "office__name",
    )
