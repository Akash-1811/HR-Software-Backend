from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class LeaveApplicationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class HalfDayPeriod(models.TextChoices):
    FIRST_HALF = "FIRST_HALF", "First half"
    SECOND_HALF = "SECOND_HALF", "Second half"


class LeaveType(models.Model):
    """
    Office-scoped leave catalog (Casual, Sick, etc.).
    `total_allowed_days` is the default annual entitlement template; per-employee balances are stored separately.
    """

    office = models.ForeignKey(
        "Organization.Office",
        on_delete=models.CASCADE,
        related_name="leave_types",
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32)
    description = models.TextField(blank=True)
    is_paid = models.BooleanField(default=True)
    total_allowed_days = models.DecimalField(max_digits=5, decimal_places=2)
    is_active = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=True)
    allow_half_day = models.BooleanField(default=False)
    allow_negative_balance = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "leave_type"
        ordering = ["office", "name"]
        constraints = [
            models.UniqueConstraint(fields=["office", "code"], name="leave_type_office_code_unique"),
        ]
        indexes = [
            models.Index(fields=["office", "is_active"]),
        ]

    def clean(self):
        super().clean()
        if self.total_allowed_days < Decimal("0"):
            raise ValidationError({"total_allowed_days": "Must be non-negative."})

    def __str__(self) -> str:
        return f"{self.name} (office {self.office_id})"


class EmployeeLeaveBalance(models.Model):
    """Per employee + leave type balance. `consumed_days` increases only when applications are approved."""

    employee = models.ForeignKey(
        "Employee.Employee",
        on_delete=models.CASCADE,
        related_name="leave_balances",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.CASCADE,
        related_name="employee_balances",
    )
    allocated_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0"))
    consumed_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "employee_leave_balance"
        constraints = [
            models.UniqueConstraint(fields=["employee", "leave_type"], name="employee_leave_balance_unique"),
        ]
        indexes = [
            models.Index(fields=["employee"]),
        ]

    def clean(self):
        super().clean()
        if self.allocated_days < Decimal("0") or self.consumed_days < Decimal("0"):
            raise ValidationError("Balances cannot be negative.")
        lt = getattr(self, "leave_type", None)
        allow_neg = bool(lt and lt.allow_negative_balance)
        if self.consumed_days > self.allocated_days and not allow_neg:
            raise ValidationError("Consumed cannot exceed allocated for this leave type.")
        emp = getattr(self, "employee", None)
        if (
            emp
            and lt
            and getattr(emp, "office_id", None)
            and getattr(lt, "office_id", None)
            and emp.office_id != lt.office_id
        ):
            raise ValidationError("Leave type must belong to the employee's office.")

    def __str__(self) -> str:
        return f"{self.employee_id}:{self.leave_type_id}"


class LeaveApplication(models.Model):
    employee = models.ForeignKey(
        "Employee.Employee",
        on_delete=models.CASCADE,
        related_name="leave_applications",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="leave_applications",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_half_day = models.BooleanField(default=False)
    half_day_period = models.CharField(
        max_length=16,
        choices=HalfDayPeriod.choices,
        blank=True,
        default="",
    )
    total_days = models.DecimalField(max_digits=6, decimal_places=2)
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=LeaveApplicationStatus.choices,
        default=LeaveApplicationStatus.PENDING,
        db_index=True,
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewer_note = models.TextField(blank=True)

    class Meta:
        db_table = "leave_application"
        ordering = ["-applied_at", "-id"]
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["employee", "start_date", "end_date"]),
        ]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})
        if self.is_half_day:
            if self.start_date != self.end_date:
                raise ValidationError({"is_half_day": "Half-day leave must be a single calendar day."})
            if self.half_day_period not in (HalfDayPeriod.FIRST_HALF, HalfDayPeriod.SECOND_HALF):
                raise ValidationError({"half_day_period": "Half-day period is required."})

    def __str__(self) -> str:
        return f"{self.employee_id} {self.start_date}–{self.end_date} ({self.status})"
