"""Transactional leave workflows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from Employee.models import Employee
from Leaves.access import leave_type_belongs_to_employee_office, user_can_review_application
from Leaves.models import (
    EmployeeLeaveBalance,
    HalfDayPeriod,
    LeaveApplication,
    LeaveApplicationStatus,
    LeaveType,
)
from Leaves.utils import (
    available_leave_balance,
    compute_total_days,
    has_overlapping_application,
)

TODAY = date.today


def _validation_error_response(exc: ValidationError) -> JsonResponse:
    msg = "; ".join(str(x) for x in exc.messages) if hasattr(exc, "messages") else str(exc)
    return JsonResponse({"error": msg}, status=400)


def apply_leave(
    *, user, employee: Employee, leave_type: LeaveType, payload: dict
) -> tuple[LeaveApplication | None, JsonResponse | None]:
    """
    Create a PENDING application (or APPROVED immediately when approval not required).
    Returns (instance, None) or (None, error_response).
    """
    from Attenova.api_utils import parse_iso_date

    start = parse_iso_date(payload.get("start_date"))
    end = parse_iso_date(payload.get("end_date"))
    if not start or not end:
        return None, JsonResponse({"error": "start_date and end_date are required (YYYY-MM-DD)"}, status=400)

    is_half_day = bool(payload.get("is_half_day"))
    raw_period = (payload.get("half_day_period") or "").strip().upper()
    half_day_period = ""
    if is_half_day:
        if raw_period == "FIRST_HALF":
            half_day_period = HalfDayPeriod.FIRST_HALF
        elif raw_period == "SECOND_HALF":
            half_day_period = HalfDayPeriod.SECOND_HALF
        else:
            return None, JsonResponse({"error": "half_day_period must be FIRST_HALF or SECOND_HALF"}, status=400)

    if start < TODAY():
        return None, JsonResponse({"error": "Cannot apply leave for past dates."}, status=400)

    total_days, err = compute_total_days(start, end, is_half_day=is_half_day, leave_type=leave_type)
    if err:
        return None, JsonResponse({"error": err}, status=400)

    if not leave_type.is_active:
        return None, JsonResponse({"error": "This leave type is inactive."}, status=400)

    if not employee.is_active:
        return None, JsonResponse({"error": "Employee is inactive."}, status=400)

    if not employee.office_id:
        return None, JsonResponse({"error": "Employee has no office assigned."}, status=400)

    if not leave_type_belongs_to_employee_office(leave_type, employee):
        return None, JsonResponse({"error": "Leave type does not belong to the employee's office."}, status=400)

    if has_overlapping_application(employee.pk, start, end):
        return None, JsonResponse({"error": "Another pending or approved leave overlaps these dates."}, status=400)

    reason = (payload.get("reason") or "").strip()

    try:
        with transaction.atomic():
            Employee.objects.select_for_update().filter(pk=employee.pk).first()

            bal = (
                EmployeeLeaveBalance.objects.select_for_update()
                .filter(employee=employee, leave_type=leave_type)
                .first()
            )
            available, _ = available_leave_balance(employee, leave_type, balance_row=bal)
            if not leave_type.allow_negative_balance and available < total_days:
                return None, JsonResponse(
                    {"error": "Insufficient leave balance (including other pending requests)."},
                    status=400,
                )

            status = LeaveApplicationStatus.PENDING
            if not leave_type.requires_approval:
                status = LeaveApplicationStatus.APPROVED

            app = LeaveApplication(
                employee=employee,
                leave_type=leave_type,
                start_date=start,
                end_date=end,
                is_half_day=is_half_day,
                half_day_period=half_day_period,
                total_days=total_days,
                reason=reason,
                status=status,
                applied_by=user,
            )
            app.full_clean()
            app.save()

            if status == LeaveApplicationStatus.APPROVED:
                if bal is None:
                    bal = EmployeeLeaveBalance(
                        employee=employee,
                        leave_type=leave_type,
                        allocated_days=Decimal("0"),
                        consumed_days=Decimal("0"),
                    )
                    bal.save()
                    bal = EmployeeLeaveBalance.objects.select_for_update().get(pk=bal.pk)
                bal.consumed_days += total_days
                bal.full_clean()
                bal.save(update_fields=["consumed_days", "updated_at"])
                app.reviewed_at = timezone.now()
                app.reviewed_by = user
                app.save(update_fields=["reviewed_at", "reviewed_by"])

            return app, None
    except ValidationError as e:
        return None, _validation_error_response(e)


def approve_leave(*, user, application_id: int) -> tuple[LeaveApplication | None, JsonResponse | None]:
    try:
        with transaction.atomic():
            app = (
                LeaveApplication.objects.select_related("employee", "leave_type")
                .select_for_update(of=("self",))
                .filter(pk=application_id)
                .first()
            )
            if not app:
                return None, JsonResponse({"error": "Not found"}, status=404)
            if not user_can_review_application(user, app):
                return None, JsonResponse({"error": "Not found"}, status=404)
            if app.status != LeaveApplicationStatus.PENDING:
                return None, JsonResponse({"error": "Only pending applications can be approved."}, status=400)

            Employee.objects.select_for_update().filter(pk=app.employee_id).first()
            lt = app.leave_type
            bal = EmployeeLeaveBalance.objects.select_for_update().filter(employee=app.employee, leave_type=lt).first()
            available, bal = available_leave_balance(
                app.employee,
                lt,
                balance_row=bal,
                exclude_application_id=app.pk,
            )
            if not lt.allow_negative_balance and available < app.total_days:
                return None, JsonResponse({"error": "Insufficient balance to approve this leave."}, status=400)

            if bal is None:
                bal = EmployeeLeaveBalance(
                    employee=app.employee,
                    leave_type=lt,
                    allocated_days=Decimal("0"),
                    consumed_days=Decimal("0"),
                )
                bal.save()

            bal = EmployeeLeaveBalance.objects.select_for_update().get(pk=bal.pk)
            bal.consumed_days += app.total_days
            bal.full_clean()
            bal.save(update_fields=["consumed_days", "updated_at"])

            app.status = LeaveApplicationStatus.APPROVED
            app.reviewed_at = timezone.now()
            app.reviewed_by = user
            app.reviewer_note = ""
            app.save(update_fields=["status", "reviewed_at", "reviewed_by", "reviewer_note"])

            return app, None
    except ValidationError as e:
        return None, _validation_error_response(e)


def reject_leave(*, user, application_id: int, note: str) -> tuple[LeaveApplication | None, JsonResponse | None]:
    try:
        with transaction.atomic():
            app = (
                LeaveApplication.objects.select_related("employee")
                .select_for_update(of=("self",))
                .filter(pk=application_id)
                .first()
            )
            if not app:
                return None, JsonResponse({"error": "Not found"}, status=404)
            if not user_can_review_application(user, app):
                return None, JsonResponse({"error": "Not found"}, status=404)
            if app.status != LeaveApplicationStatus.PENDING:
                return None, JsonResponse({"error": "Only pending applications can be rejected."}, status=400)

            Employee.objects.select_for_update().filter(pk=app.employee_id).first()

            app.status = LeaveApplicationStatus.REJECTED
            app.reviewed_at = timezone.now()
            app.reviewed_by = user
            app.reviewer_note = note.strip()
            app.save(update_fields=["status", "reviewed_at", "reviewed_by", "reviewer_note"])

            return app, None
    except ValidationError as e:
        return None, _validation_error_response(e)
