"""
Helper functions for Reports app.
"""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from Attenova.api_utils import parse_iso_date
from Attendance.models import Attendance, AttendanceStatus
from Attendance.utils import attendance_clock_hhmmss_for_report

from Reports.constants import BIOMETRIC_DIRECTION_IN, BIOMETRIC_DIRECTION_OUT


def parse_date(value):
    """Parse YYYY-MM-DD string to date, or None if invalid."""
    return parse_iso_date(value)


def empty_attendance_response(start_date, end_date):
    """JsonResponse for attendance report when no data in scope."""
    return JsonResponse({
        "stats": {
            "total_employees": 0,
            "avg_attendance_pct": 0,
            "late_today": 0,
            "on_leave": 0,
        },
        "rows": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
    })


def build_hierarchical_rows(
    emp_map,
    groups,
    attendance_working_hours,
    ordered_keys,
    *,
    regularized_clocks=None,
):
    """
    Build parent rows: one per (employee_code, date) with first_in, last_out, hours_worked, punches.
    groups: dict of (emp_code, date) -> list of raw punch dicts (UserId, DeviceId, LogDate, Direction).
    ordered_keys: list of (emp_code, date) in display order.
    regularized_clocks: optional (emp_code, date) -> {first_in?, last_out?} clock strings from Attendance when regularized.
    """
    DIR_IN = BIOMETRIC_DIRECTION_IN
    DIR_OUT = BIOMETRIC_DIRECTION_OUT
    rows = []
    for emp_code, att_date in ordered_keys:
        punches_raw = groups[(emp_code, att_date)]
        emp = emp_map.get(emp_code)
        if not emp:
            continue

        in_times = []
        out_times = []
        punch_list = []
        # Sort punches by time for chronological display
        punches_sorted = sorted(punches_raw, key=lambda p: p.get("LogDate"))

        for r in punches_sorted:
            log_dt = r.get("LogDate")
            direction = (r.get("Direction") or "").strip().lower()
            time_str = log_dt.strftime("%H:%M:%S") if log_dt and hasattr(log_dt, "strftime") else ""
            is_in = direction in DIR_IN

            if is_in:
                in_times.append(log_dt)
                punch_list.append({"status": "in", "check_in": time_str, "check_out": None})
            elif direction in DIR_OUT:
                out_times.append(log_dt)
                punch_list.append({"status": "out", "check_in": None, "check_out": time_str})

        first_in = min(in_times).strftime("%H:%M:%S") if in_times else None
        last_out = max(out_times).strftime("%H:%M:%S") if out_times else None

        ov = (
            regularized_clocks.get((emp_code, att_date))
            if regularized_clocks
            else None
        )
        if ov:
            if ov.get("first_in"):
                first_in = ov["first_in"]
            if ov.get("last_out"):
                last_out = ov["last_out"]

        log_date_str = att_date.strftime("%b %d, %Y") if hasattr(att_date, "strftime") else str(att_date)
        device_id = punches_raw[0].get("DeviceId") or "" if punches_raw else ""
        hours_worked = attendance_working_hours.get((emp_code, att_date))
        emp_id = emp.get("id")
        is_regularized = bool(regularized_clocks and (emp_code, att_date) in regularized_clocks)
        rows.append({
            "employee_code": emp["emp_code"],
            "employee_name": emp["name"],
            "employee_id": emp_id,
            "date": att_date.strftime("%Y-%m-%d"),
            "device_id": device_id,
            "log_date": log_date_str,
            "first_in": first_in,
            "last_out": last_out,
            "hours_worked": str(hours_worked) if hours_worked is not None else None,
            "is_regularized": is_regularized,
            "punches": punch_list,
        })
    return rows


def fetch_working_hours_for_pairs(date_emp_pairs):
    """Fetch (emp_code, date) -> working_hours for the given (UserId, date) pairs."""
    if not date_emp_pairs:
        return {}
    q = Q()
    for emp_code, d in date_emp_pairs:
        q |= Q(employee__emp_code=emp_code, date=d)
    atts = Attendance.objects.filter(q).values(
        "employee__emp_code", "date", "working_hours"
    )
    return {
        (a["employee__emp_code"], a["date"]): float(a["working_hours"])
        for a in atts
        if a["working_hours"] is not None
    }


def fetch_regularized_clock_strings_for_pairs(date_emp_pairs):
    """
    For attendance rows flagged is_regularized, return clock strings from processed
    Attendance (effective first_in / last_out) so reports match Regularize + payroll.
    """
    if not date_emp_pairs:
        return {}
    q = Q()
    for emp_code, d in date_emp_pairs:
        q |= Q(employee__emp_code=emp_code, date=d, is_regularized=True)
    rows = Attendance.objects.filter(q).values(
        "employee__emp_code",
        "date",
        "first_in",
        "last_out",
    )
    result = {}
    for a in rows:
        pair = (a["employee__emp_code"], a["date"])
        fi = None
        lo = None
        if a["first_in"]:
            fi = attendance_clock_hhmmss_for_report(a["first_in"])
        if a["last_out"]:
            lo = attendance_clock_hhmmss_for_report(a["last_out"])
        result[pair] = {"first_in": fi, "last_out": lo}
    return result


def fetch_attendance_report_rows_for_office(office_id, report_date):
    """
    Fetch parent-only attendance report rows for an office on a given date.
    Used by the daily email cronjob. Returns list of dicts with keys:
    employee_code, employee_name, device_id, log_date, first_in, last_out, hours_worked, punches (count as "X punch(es)").
    """
    from collections import defaultdict

    from Biometric.models import DummyEsslBiometricAttendanceData
    from Employee.models import Employee

    employees_qs = Employee.objects.filter(
        office_id=office_id,
        is_active=True,
    ).values("id", "emp_code", "name")
    emp_map = {e["emp_code"]: e for e in employees_qs}
    emp_codes = set(emp_map.keys())
    if not emp_codes:
        return []

    raw = list(
        DummyEsslBiometricAttendanceData.objects.filter(
            UserId__in=emp_codes,
            LogDate__isnull=False,
            LogDate__date=report_date,
        )
        .order_by("-LogDate", "UserId")
        .values("UserId", "DeviceId", "LogDate", "Direction")
    )

    groups = defaultdict(list)
    for r in raw:
        log_dt = r.get("LogDate")
        if log_dt and hasattr(log_dt, "date") and log_dt.date() == report_date:
            groups[(r["UserId"], report_date)].append(r)

    if not groups:
        return []

    ordered_keys = sorted(
        groups.keys(),
        key=lambda k: (emp_map.get(k[0]) or {}).get("name", ""),
    )
    date_emp_pairs = set(ordered_keys)
    attendance_working_hours = fetch_working_hours_for_pairs(date_emp_pairs)
    regularized_clocks = fetch_regularized_clock_strings_for_pairs(date_emp_pairs)
    full_rows = build_hierarchical_rows(
        emp_map,
        dict(groups),
        attendance_working_hours,
        ordered_keys,
        regularized_clocks=regularized_clocks,
    )
    # Return parent-only: punch count instead of punch list
    return [
        {
            "employee_code": r["employee_code"],
            "employee_name": r["employee_name"],
            "device_id": r["device_id"],
            "log_date": r["log_date"],
            "first_in": r["first_in"],
            "last_out": r["last_out"],
            "hours_worked": r["hours_worked"],
            "is_regularized": r["is_regularized"],
            "punches": f"{len(r['punches'])} punch(es)" if r["punches"] else "0 punch(es)",
        }
        for r in full_rows
    ]


def fetch_attendance_report_rows_for_organization(organization_id, report_date):
    """
    Org-wide attendance rows for a single calendar day — matches GET /reports/attendance/ scope for Org Admin.
    (fetch_attendance_report_rows_for_office only includes one office.)
    """
    from collections import defaultdict

    from Biometric.models import DummyEsslBiometricAttendanceData
    from Employee.models import Employee

    employees_qs = Employee.objects.filter(
        organization_id=organization_id,
        is_active=True,
    ).values("id", "emp_code", "name")
    emp_map = {e["emp_code"]: e for e in employees_qs}
    emp_codes = set(emp_map.keys())
    if not emp_codes:
        return []

    raw = list(
        DummyEsslBiometricAttendanceData.objects.filter(
            UserId__in=emp_codes,
            LogDate__isnull=False,
            LogDate__date=report_date,
        )
        .order_by("-LogDate", "UserId")
        .values("UserId", "DeviceId", "LogDate", "Direction")
    )

    groups = defaultdict(list)
    for r in raw:
        log_dt = r.get("LogDate")
        if log_dt and hasattr(log_dt, "date") and log_dt.date() == report_date:
            groups[(r["UserId"], report_date)].append(r)

    if not groups:
        return []

    ordered_keys = sorted(
        groups.keys(),
        key=lambda k: (emp_map.get(k[0]) or {}).get("name", ""),
    )
    date_emp_pairs = set(ordered_keys)
    attendance_working_hours = fetch_working_hours_for_pairs(date_emp_pairs)
    regularized_clocks = fetch_regularized_clock_strings_for_pairs(date_emp_pairs)
    full_rows = build_hierarchical_rows(
        emp_map,
        dict(groups),
        attendance_working_hours,
        ordered_keys,
        regularized_clocks=regularized_clocks,
    )
    return [
        {
            "employee_code": r["employee_code"],
            "employee_name": r["employee_name"],
            "device_id": r["device_id"],
            "log_date": r["log_date"],
            "first_in": r["first_in"],
            "last_out": r["last_out"],
            "hours_worked": r["hours_worked"],
            "is_regularized": r["is_regularized"],
            "punches": f"{len(r['punches'])} punch(es)" if r["punches"] else "0 punch(es)",
        }
        for r in full_rows
    ]


def get_recipients_for_office(office):
    """
    Get active users who should receive the office's daily attendance report:
    Office Admin, Supervisor, and Office Managers for this office.
    Returns list of User objects with email.
    """
    from Users.models import User, UserRole

    recipients = []
    seen = set()
    # Office Admin and Supervisor (user.office_id = office.id)
    for u in User.objects.filter(
        office_id=office.id,
        role__in=[UserRole.OFFICE_ADMIN, UserRole.SUPERVISOR],
        is_active=True,
    ).select_related("office"):
        if u.email and u.email not in seen:
            recipients.append(u)
            seen.add(u.email)
    # Office Managers (office.managers M2M)
    for u in office.managers.filter(is_active=True):
        if u.email and u.email not in seen:
            recipients.append(u)
            seen.add(u.email)
    return recipients


def compute_attendance_report_stats(employees_qs, start_date, end_date):
    """Compute stats dict for attendance report: total_employees, avg_attendance_pct, late_today, on_leave."""
    total_employees = employees_qs.count()
    if total_employees == 0:
        return {
            "total_employees": 0,
            "avg_attendance_pct": 0,
            "late_today": 0,
            "on_leave": 0,
        }
    emp_ids = list(employees_qs.values_list("id", flat=True))
    present_in_range = Attendance.objects.filter(
        employee_id__in=emp_ids,
        date__gte=start_date,
        date__lte=end_date,
        status__in=[AttendanceStatus.P, AttendanceStatus.L],
    ).values("employee_id").distinct().count()
    avg_pct = (
        round(100 * present_in_range / total_employees, 1)
        if total_employees else 0
    )
    today = timezone.now().date()
    late_today = Attendance.objects.filter(
        employee_id__in=emp_ids,
        date=today,
        status=AttendanceStatus.L,
    ).count()
    return {
        "total_employees": total_employees,
        "avg_attendance_pct": avg_pct,
        "late_today": late_today,
        "on_leave": 0,
    }


# --- Matrix CSV export (vendor-style wide table) --------------------------------------


def inclusive_calendar_days(start: date, end: date) -> list[date]:
    """Return every calendar date from ``start`` through ``end`` inclusive, in order."""
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def day_header_label_month_day(d: date) -> str:
    """Vendor-style column header ``MMDD`` (zero-padded)."""
    return f"{d.month:02d}{d.day:02d}"


def encode_rows_as_utf8_sig_csv(rows: list[list[Any]]) -> bytes:
    """Serialize matrix rows to UTF-8 with BOM for Excel-friendly CSV."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


@dataclass(frozen=True, slots=True)
class MatrixCellFormat:
    """How times and decimal working hours are shown inside matrix cells."""

    @staticmethod
    def clock_hh_mm(dt: Any) -> str:
        """Format a datetime as ``HH:MM``; empty if invalid."""
        if not dt or not hasattr(dt, "strftime"):
            return ""
        return dt.strftime("%H:%M")

    @staticmethod
    def decimal_working_hours_as_hh_mm(hours: Any) -> str:
        """Convert stored decimal hours (e.g. ``7.82``) to ``H:MM`` for one day."""
        if hours is None:
            return ""
        try:
            total_minutes = int(round(float(hours) * 60))
        except (TypeError, ValueError):
            return ""
        h, m = divmod(total_minutes, 60)
        return f"{h}:{m:02d}"

    @staticmethod
    def sum_decimal_hours_as_total_hh_mm(parts: list[Any]) -> str:
        """Sum decimal hour values into one ``H:MM`` total cell."""
        total = 0.0
        for part in parts:
            if part is None:
                continue
            try:
                total += float(part)
            except (TypeError, ValueError):
                pass
        total_minutes = int(round(total * 60))
        h, m = divmod(total_minutes, 60)
        return f"{h}:{m:02d}"


def pair_biometric_in_out_for_day(raw_rows: list[dict]) -> list[tuple[Any, Any]]:
    """
    Sort one day's raw punches by time and pair each IN with the next OUT.

    Returns:
        ``(in_datetime_or_None, out_datetime_or_None)`` per pair.
        Missing OUT after IN yields ``(..., None)`` (Out row shows ``M`` in the matrix).
    """
    if not raw_rows:
        return []
    sentinel = datetime(1970, 1, 1)
    ordered = sorted(raw_rows, key=lambda row: row.get("LogDate") or sentinel)
    pairs: list[tuple[Any, Any]] = []
    pending_in: Any = None

    for row in ordered:
        log_dt = row.get("LogDate")
        direction = (row.get("Direction") or "").strip().lower()

        if direction in BIOMETRIC_DIRECTION_IN:
            if pending_in is not None:
                pairs.append((pending_in, None))
            pending_in = log_dt
        elif direction in BIOMETRIC_DIRECTION_OUT:
            if pending_in is not None:
                pairs.append((pending_in, log_dt))
                pending_in = None
            else:
                pairs.append((None, log_dt))

    if pending_in is not None:
        pairs.append((pending_in, None))

    return pairs


def load_attendance_index_by_employee_and_date(
    employee_by_code: Mapping[str, dict],
    start: date,
    end: date,
) -> dict[tuple[str, date], dict[str, Any]]:
    """Map ``(emp_code, date)`` → ``{status, working_hours}`` from processed ``Attendance``."""
    ids = [entry["id"] for entry in employee_by_code.values()]
    if not ids:
        return {}

    index: dict[tuple[str, date], dict[str, Any]] = {}
    queryset = Attendance.objects.filter(
        employee_id__in=ids,
        date__gte=start,
        date__lte=end,
    ).values("employee__emp_code", "date", "status", "working_hours")

    for row in queryset:
        index[(row["employee__emp_code"], row["date"])] = {
            "status": row["status"],
            "working_hours": row["working_hours"],
        }
    return index


def load_biometric_punches_grouped_by_employee_and_date(
    employee_codes: set[str],
    start: date,
    end: date,
) -> dict[tuple[str, date], list[dict]]:
    """Group raw ESSL rows by ``(UserId emp_code, local calendar date)``."""
    from Biometric.models import DummyEsslBiometricAttendanceData

    if not employee_codes:
        return defaultdict(list)

    raw = (
        DummyEsslBiometricAttendanceData.objects.filter(
            UserId__in=employee_codes,
            LogDate__isnull=False,
            LogDate__date__gte=start,
            LogDate__date__lte=end,
        )
        .order_by("LogDate", "UserId")
        .values("UserId", "DeviceId", "LogDate", "Direction")
    )

    groups: dict[tuple[str, date], list[dict]] = defaultdict(list)
    for row in raw:
        log_dt = row.get("LogDate")
        if log_dt and hasattr(log_dt, "date"):
            groups[(row["UserId"], log_dt.date())].append(row)
    return groups


def resolve_office_for_email_context(user, office_id_from_request: int | None):
    """
    Resolve the ``Office`` used for email HTML context and permission checks,
    consistent with the manual send-email flow.
    """
    from Organization.models import Office
    from Users.models import UserRole

    office = None

    if office_id_from_request is not None:
        office = Office.objects.filter(
            pk=office_id_from_request, is_active=True
        ).first()
        if not office or (
            user.organization_id and office.organization_id != user.organization_id
        ):
            raise ValueError("Office not found or not in your organization")
    elif getattr(user, "office_id", None):
        office = Office.objects.filter(pk=user.office_id, is_active=True).first()
    elif user.role == UserRole.OFFICE_MANAGER:
        office = user.managed_offices.filter(is_active=True).first()
    elif user.role == UserRole.ORG_ADMIN or user.is_superuser:
        if user.organization_id:
            office = (
                Office.objects.filter(
                    organization_id=user.organization_id,
                    is_active=True,
                )
                .select_related("organization")
                .first()
            )
        else:
            office = Office.objects.filter(is_active=True).first()

    if not office:
        raise ValueError("No office found for your account")
    return office


def matrix_attachment_and_workflow_labels(office, user) -> tuple[str, str]:
    """Attachment/subject label and CSV workflow line text."""
    same_org = (
        user.organization_id
        and office.organization_id == user.organization_id
    )
    if same_org and office.organization_id and office.organization:
        attachment = office.organization.name
    else:
        attachment = office.name

    if office.organization_id and office.organization:
        workflow = f"{office.organization.name} — {office.name}"
    else:
        workflow = attachment

    return attachment, workflow
