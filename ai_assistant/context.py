"""Server-built tenant-safe snapshot for the model — single module, explicit."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from Attendance.models import Attendance
from Employee.models import Employee
from Leaves.access import (
    get_leave_applications_queryset,
    get_subject_employee,
    user_can_assign_leave_balances,
    user_can_manage_leave_types,
    user_can_review_leave_applications,
)
from Leaves.models import EmployeeLeaveBalance, LeaveApplicationStatus
from Leaves.utils import aggregate_employee_balance_totals, serialize_balance
from Organization.models import Organization


def _attendance_snapshot_lines(*, employee: Employee | None, days: int = 10) -> list[str]:
    if employee is None:
        return ["- Recent attendance (authorized): no linked employee profile; omit personal attendance."]
    today = timezone.now().date()
    start = today - timedelta(days=max(days, 1) + 7)
    qs = (
        Attendance.objects.filter(employee_id=employee.pk, date__gte=start, date__lte=today)
        .order_by("-date")
        .values_list("date", "status", "late_minutes")[: days + 7]
    )
    rows = list(qs)
    if not rows:
        return ["- Recent attendance (authorized): no rows in the lookback window."]
    counts: dict[str, int] = {}
    late_days = 0
    for _d, st, late_m in rows[:days]:
        counts[st] = counts.get(st, 0) + 1
        if late_m and late_m > 0:
            late_days += 1
    parts = [f"{k}: {v}" for k, v in sorted(counts.items())]
    return [
        f"- Recent attendance (last ~{days} calendar days in window, authorized self only): "
        + ", ".join(parts)
        + (f"; days with late flag: {late_days}" if late_days else "")
    ]


def build_compact_context(*, user, client_route: str | None) -> str:
    """
    Human-readable block injected into the system prompt.
    Only includes data the user is allowed to see in-app for their tenant.
    """
    lines: list[str] = []

    org_name = ""
    if getattr(user, "organization_id", None):
        org_name = (
            Organization.objects.filter(pk=user.organization_id).values_list("name", flat=True).first() or ""
        )
    lines.append(f"- Authenticated user role: {getattr(user, 'role', '') or 'unknown'}")
    lines.append(f"- Organization: {org_name or 'none linked'}")
    lines.append(
        "- Capability flags (leave-related): "
        f"manage_leave_types={user_can_manage_leave_types(user)}; "
        f"assign_leave_balances={user_can_assign_leave_balances(user)}; "
        f"review_leave_applications={user_can_review_leave_applications(user)}"
    )

    route = (client_route or "").strip()[:300]
    if route:
        lines.append(f"- Current app route (client-reported; use for navigation hints only): {route}")
    else:
        lines.append("- Current app route (client-reported): unknown")

    linked = get_subject_employee(user, None)
    if linked:
        shift_name = ""
        if linked.shift_id:
            shift_name = linked.shift.name if linked.shift else ""
        lines.append(
            "- Linked employee profile (authorized): "
            f"office={linked.office.name if linked.office_id else 'n/a'}; "
            f"assigned_shift={shift_name or 'none'}; "
            f"designation_label={linked.get_designation_display() if linked.designation else 'n/a'}"
        )
        alloc, avail = aggregate_employee_balance_totals(linked.pk)
        lines.append(
            f"- Leave balances (authorized totals): allocated_sum≈{float(alloc):.2f} days; "
            f"available_sum≈{float(avail):.2f} days (pending requests deducted per policy)."
        )
        bals = (
            EmployeeLeaveBalance.objects.filter(employee_id=linked.pk)
            .select_related("leave_type")
            .order_by("leave_type__name")[:12]
        )
        if bals:
            detail = []
            for b in bals:
                s = serialize_balance(b)
                detail.append(f"{s['leave_type_name']}: available≈{s['available_days']:.2f}")
            lines.append("- Leave by type (max 12 rows): " + "; ".join(detail))
        apps_qs = get_leave_applications_queryset(user)
        my_pending = apps_qs.filter(
            employee_id=linked.pk,
            status=LeaveApplicationStatus.PENDING,
        ).count()
        lines.append(f"- My pending leave requests (authorized): {my_pending}")
    else:
        lines.append("- Linked employee profile (authorized): none")

    lines.extend(_attendance_snapshot_lines(employee=linked, days=10))

    if user_can_review_leave_applications(user):
        qs = get_leave_applications_queryset(user)
        pending_review = qs.filter(status=LeaveApplicationStatus.PENDING).count()
        lines.append(f"- Pending leave approvals in my queue (authorized scope): {pending_review}")

    lines.append(
        "- Navigation hints: `/dashboard` home; `/employees` roster; `/shifts`; `/leaves` "
        "(dashboard, history, approvals, types); `/reports`; `/notifications`; `/profile`; `/biometric-devices`."
    )

    return "\n".join(lines)
