"""Serialization and pure validation helpers for Leave APIs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db.models import Sum

from Employee.models import Employee
from Leaves.models import (
    EmployeeLeaveBalance,
    HalfDayPeriod,
    LeaveApplication,
    LeaveApplicationStatus,
    LeaveType,
)


def parse_decimal_days_optional(value) -> Decimal | None:
    """Parse a non-negative decimal from JSON/query; invalid → None."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def consumed_leave_days_in_calendar_year(employee_id: int, year: int | None = None) -> Decimal:
    """
    Sum approved leave days attributed to a calendar year.
    Multi-day ranges that span years count proportionally (total_days * overlap / span).
    """
    today = date.today()
    y = year if year is not None else today.year
    ys, ye = date(y, 1, 1), date(y, 12, 31)
    total = Decimal("0")
    qs = LeaveApplication.objects.filter(
        employee_id=employee_id,
        status=LeaveApplicationStatus.APPROVED,
    ).only("start_date", "end_date", "total_days")
    for app in qs.iterator():
        s, e = app.start_date, app.end_date
        if e < ys or s > ye:
            continue
        olap_start = max(s, ys)
        olap_end = min(e, ye)
        if olap_start > olap_end:
            continue
        span = (e - s).days + 1
        overlap = (olap_end - olap_start).days + 1
        if span <= 0:
            continue
        total += app.total_days * Decimal(overlap) / Decimal(span)
    return total


def inclusive_calendar_days(start: date, end: date) -> Decimal:
    return Decimal((end - start).days + 1)


def compute_total_days(
    start: date,
    end: date,
    *,
    is_half_day: bool,
    leave_type: LeaveType,
) -> tuple[Decimal | None, str | None]:
    if is_half_day:
        if not leave_type.allow_half_day:
            return None, "This leave type does not allow half-day requests."
        if start != end:
            return None, "Half-day leave must use the same start and end date."
        return Decimal("0.5"), None
    if start > end:
        return None, "Invalid date range."
    return inclusive_calendar_days(start, end), None


def pending_days_total(
    employee_id: int,
    leave_type_id: int,
    *,
    exclude_application_id: int | None = None,
) -> Decimal:
    qs = LeaveApplication.objects.filter(
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        status=LeaveApplicationStatus.PENDING,
    )
    if exclude_application_id is not None:
        qs = qs.exclude(pk=exclude_application_id)
    agg = qs.aggregate(s=Sum("total_days"))
    v = agg["s"]
    return v if v is not None else Decimal("0")


def aggregate_employee_balance_totals(employee_id: int) -> tuple[Decimal, Decimal]:
    """
    Sum allocated_days and computed available across all balance rows for an employee.
    Matches per-row logic in serialize_balance (available = allocated - consumed - pending).
    """
    allocated_sum = Decimal("0")
    available_sum = Decimal("0")
    qs = EmployeeLeaveBalance.objects.filter(employee_id=employee_id).select_related("leave_type")
    for b in qs:
        allocated_sum += b.allocated_days
        pend = pending_days_total(employee_id, b.leave_type_id)
        available_sum += b.allocated_days - b.consumed_days - pend
    return allocated_sum, available_sum


def serialize_leave_type(lt: LeaveType) -> dict:
    office = lt.office
    org_id = office.organization_id if office else None
    return {
        "id": lt.id,
        "office_id": lt.office_id,
        "office_name": office.name if office else "",
        "organization_id": org_id,
        "name": lt.name,
        "code": lt.code,
        "description": lt.description or "",
        "is_paid": lt.is_paid,
        "total_allowed_days": float(lt.total_allowed_days),
        "is_active": lt.is_active,
        "requires_approval": lt.requires_approval,
        "allow_half_day": lt.allow_half_day,
        "allow_negative_balance": lt.allow_negative_balance,
        "created_at": lt.created_at.isoformat() if lt.created_at else None,
        "updated_at": lt.updated_at.isoformat() if lt.updated_at else None,
    }


def serialize_balance(b: EmployeeLeaveBalance) -> dict:
    lt = b.leave_type
    pending = pending_days_total(b.employee_id, lt.id)
    available = b.allocated_days - b.consumed_days - pending
    return {
        "id": b.id,
        "employee_id": b.employee_id,
        "leave_type_id": lt.id,
        "leave_type_name": lt.name,
        "leave_type_code": lt.code,
        "allocated_days": float(b.allocated_days),
        "consumed_days": float(b.consumed_days),
        "available_days": float(available),
        "pending_days": float(pending),
        "allow_negative_balance": lt.allow_negative_balance,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


def serialize_application(app: LeaveApplication) -> dict:
    emp = app.employee
    lt = app.leave_type
    applied_by = app.applied_by
    reviewed_by = app.reviewed_by
    return {
        "id": app.id,
        "employee_id": emp.id,
        "employee_name": emp.name,
        "emp_code": emp.emp_code,
        "office_id": emp.office_id,
        "office_name": emp.office.name if emp.office_id else "",
        "leave_type_id": lt.id,
        "leave_type_name": lt.name,
        "leave_type_code": lt.code,
        "start_date": app.start_date.isoformat(),
        "end_date": app.end_date.isoformat(),
        "is_half_day": app.is_half_day,
        "half_day_period": app.half_day_period or None,
        "total_days": float(app.total_days),
        "reason": app.reason or "",
        "status": app.status,
        "applied_at": app.applied_at.isoformat() if app.applied_at else None,
        "applied_by_id": applied_by.id if applied_by else None,
        "applied_by_name": (applied_by.name or applied_by.email) if applied_by else None,
        "reviewed_at": app.reviewed_at.isoformat() if app.reviewed_at else None,
        "reviewed_by_id": reviewed_by.id if reviewed_by else None,
        "reviewed_by_name": (reviewed_by.name or reviewed_by.email) if reviewed_by else None,
        "reviewer_note": app.reviewer_note or "",
        "requires_approval": lt.requires_approval,
    }


def has_overlapping_application(
    employee_id: int,
    start: date,
    end: date,
    *,
    exclude_application_id: int | None = None,
) -> bool:
    qs = LeaveApplication.objects.filter(
        employee_id=employee_id,
        status__in=(LeaveApplicationStatus.PENDING, LeaveApplicationStatus.APPROVED),
    ).filter(
        start_date__lte=end,
        end_date__gte=start,
    )
    if exclude_application_id is not None:
        qs = qs.exclude(pk=exclude_application_id)
    return qs.exists()


def available_leave_balance(
    employee: Employee,
    leave_type: LeaveType,
    *,
    balance_row: EmployeeLeaveBalance | None = None,
    exclude_application_id: int | None = None,
) -> tuple[Decimal, EmployeeLeaveBalance | None]:
    """
    Returns (available_days, balance_row or None).
    available = allocated - consumed - pending (same type, optional exclude for approval math).
    """
    if balance_row is None:
        balance_row = EmployeeLeaveBalance.objects.filter(
            employee=employee,
            leave_type=leave_type,
        ).first()
    allocated = balance_row.allocated_days if balance_row else Decimal("0")
    consumed = balance_row.consumed_days if balance_row else Decimal("0")
    pending = pending_days_total(
        employee.pk,
        leave_type.pk,
        exclude_application_id=exclude_application_id,
    )
    return allocated - consumed - pending, balance_row
