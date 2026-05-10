from django.contrib import admin

from Leaves.models import EmployeeLeaveBalance, LeaveApplication, LeaveType


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "office", "is_active", "requires_approval", "allow_half_day")
    list_filter = ("is_active", "is_paid", "requires_approval")
    search_fields = ("name", "code", "office__name", "office__organization__name")


@admin.register(EmployeeLeaveBalance)
class EmployeeLeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "allocated_days", "consumed_days")
    list_filter = ("leave_type",)
    search_fields = ("employee__name", "employee__emp_code")


@admin.register(LeaveApplication)
class LeaveApplicationAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date", "total_days", "status", "applied_at")
    list_filter = ("status", "leave_type")
    search_fields = ("employee__name", "reason")
    raw_id_fields = ("employee", "leave_type", "applied_by", "reviewed_by")
