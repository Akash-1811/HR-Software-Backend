"""
Wide-matrix attendance CSV for email exports (legacy vendor / biometric style).

Each column after ``Type`` and ``Total`` is one calendar day (``MMDD`` labels).
Rows group employees with paired In/Out punch times, punch-type codes from
processed Attendance, working hours, and a zero stub for overtime.

Constants live in :mod:`Reports.constants`; date/CSV/biometric helpers in
:mod:`Reports.utils`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Mapping

from django.db.models import Q

from Employee.utils import apply_list_filters, get_employees_queryset
from Organization.models import Office

from Reports.constants import MAX_MATRIX_DATE_RANGE_DAYS, MATRIX_PRESENTISH_STATUSES
from Reports.utils import (
    MatrixCellFormat,
    day_header_label_month_day,
    encode_rows_as_utf8_sig_csv,
    inclusive_calendar_days,
    load_attendance_index_by_employee_and_date,
    load_biometric_punches_grouped_by_employee_and_date,
    matrix_attachment_and_workflow_labels,
    pair_biometric_in_out_for_day,
    resolve_office_for_email_context,
)


def validate_matrix_date_range(start_date: date, end_date: date) -> None:
    """
    Ensure ``start_date <= end_date`` and span is within ``MAX_MATRIX_DATE_RANGE_DAYS``.

    Raises:
        ValueError: If ordering is invalid or range is too long.
    """
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")
    span = (end_date - start_date).days + 1
    if span > MAX_MATRIX_DATE_RANGE_DAYS:
        raise ValueError(
            f"Date range must be at most {MAX_MATRIX_DATE_RANGE_DAYS} days (requested {span})"
        )


def build_matrix_csv_bundle_for_user(
    *,
    user,
    start_date: date,
    end_date: date,
    office_id: int | None = None,
    search: str = "",
    extra_query_params: dict | None = None,
) -> tuple[bytes, str, Office, str]:
    """
    Build the matrix report for the requesting user’s data scope and return mail assets.

    Args:
        user: Authenticated Django user (role drives employee scope).
        start_date / end_date: Inclusive report window.
        office_id: Optional office filter (Org Admin / manager semantics via ``apply_list_filters``).
        search: Optional substring match on employee name or code.
        extra_query_params: Same shape as Reports list filters (e.g. ``{"office_id": "2"}``).

    Returns:
        ``(csv_bytes, attachment_filename, context_office, attachment_label_for_subject)``.

    Raises:
        ValueError: Empty scope, filter mismatch, or invalid date range.
    """
    validate_matrix_date_range(start_date, end_date)
    query_params = dict(extra_query_params or {})
    if office_id is not None:
        query_params["office_id"] = str(office_id)

    employee_qs = apply_list_filters(
        get_employees_queryset(user).filter(is_active=True),
        user,
        query_params,
    )

    emp_codes = set(employee_qs.values_list("emp_code", flat=True).distinct())
    if not emp_codes:
        raise ValueError("No employees in scope for this report")

    query = (search or "").strip()
    if query:
        emp_codes = set(
            employee_qs.filter(
                Q(name__icontains=query) | Q(emp_code__icontains=query)
            ).values_list("emp_code", flat=True)
        )
        if not emp_codes:
            raise ValueError("No employees match the search filter")

    employee_by_code = {
        row["emp_code"]: row
        for row in employee_qs.filter(emp_code__in=emp_codes).values(
            "id", "emp_code", "name"
        )
    }

    office = resolve_office_for_email_context(user, office_id)
    attachment_label, workflow_title = matrix_attachment_and_workflow_labels(
        office, user
    )

    punches_by_day = load_biometric_punches_grouped_by_employee_and_date(
        emp_codes, start_date, end_date
    )
    attendance_index = load_attendance_index_by_employee_and_date(
        employee_by_code, start_date, end_date
    )

    builder = VendorMatrixCsvBuilder(
        calendar_days=inclusive_calendar_days(start_date, end_date),
        employee_by_code=employee_by_code,
        punches_by_employee_and_date=punches_by_day,
        attendance_by_employee_and_date=attendance_index,
        workflow_title=workflow_title,
    )
    matrix_rows = builder.build_rows()

    csv_bytes = encode_rows_as_utf8_sig_csv(matrix_rows)
    safe_label = attachment_label.replace(" ", "_")
    filename = (
        f"Daily_Attendance_{safe_label}_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"
    )
    return csv_bytes, filename, office, attachment_label


class VendorMatrixCsvBuilder:
    """
    Builds the vendor-style matrix table from punches + Attendance.

    Pairing is computed once per ``(employee code, day)`` and reused for all In/Out
    row slots (predictable performance for long ranges).
    """

    def __init__(
        self,
        *,
        calendar_days: list[date],
        employee_by_code: Mapping[str, dict],
        punches_by_employee_and_date: Mapping[tuple[str, date], list],
        attendance_by_employee_and_date: Mapping[tuple[str, date], dict[str, Any]],
        workflow_title: str,
    ) -> None:
        self._days = calendar_days
        self._employees = dict(employee_by_code)
        self._punches = punches_by_employee_and_date
        self._attendance = dict(attendance_by_employee_and_date)
        self._workflow = (workflow_title or "Attenova").strip()
        self._fmt = MatrixCellFormat()
        self._pair_cache = self._cache_in_out_pairs_per_employee_day()
        self._pair_slot_count = self._max_pair_count_across_grid()

    def build_rows(self) -> list[list[Any]]:
        """Return rows as lists of cell values, ready for CSV encoding."""
        day_headers = [day_header_label_month_day(d) for d in self._days]
        n = len(self._days)

        table: list[list[Any]] = [
            ["Type", "Total", *day_headers],
            [f"Workflow: {self._workflow}", "", *[""] * n],
        ]

        for emp_code, employee in sorted(
            self._employees.items(),
            key=lambda kv: (kv[1].get("name") or "").lower(),
        ):
            name = employee.get("name") or ""
            table.append([f"Employee: {name}", *[""] * (n + 1)])
            table.extend(self._paired_in_out_rows(emp_code))
            table.append(self._punch_type_row(emp_code))
            table.append(self._working_hours_row(emp_code))
            table.append(self._overtime_stub_row(n))

        return table

    def _cache_in_out_pairs_per_employee_day(
        self,
    ) -> dict[tuple[str, date], list[tuple[Any, Any]]]:
        cache: dict[tuple[str, date], list[tuple[Any, Any]]] = {}
        for emp_code in self._employees:
            for day in self._days:
                raw_list = list(self._punches.get((emp_code, day), []))
                cache[(emp_code, day)] = pair_biometric_in_out_for_day(raw_list)
        return cache

    def _max_pair_count_across_grid(self) -> int:
        peak = 1
        for emp_code in self._employees:
            for day in self._days:
                peak = max(peak, len(self._pair_cache[(emp_code, day)]))
        return peak

    def _paired_in_out_rows(self, emp_code: str) -> list[list[Any]]:
        rows_out: list[list[Any]] = []

        for slot in range(self._pair_slot_count):
            in_row: list[Any] = [f"{slot + 1}In", ""]
            out_row: list[Any] = [f"{slot + 1}Out", ""]

            for day in self._days:
                pairs = self._pair_cache[(emp_code, day)]
                if slot < len(pairs):
                    t_in, t_out = pairs[slot]
                    in_row.append(self._fmt.clock_hh_mm(t_in) if t_in else "")
                    out_row.append(
                        self._fmt.clock_hh_mm(t_out) if t_out else "M"
                    )
                else:
                    in_row.append("")
                    out_row.append("")

            rows_out.append(in_row)
            rows_out.append(out_row)

        return rows_out

    @staticmethod
    def _status_code_for_day(
        emp_code: str,
        day: date,
        punches: Mapping[tuple[str, date], list],
        attendance: Mapping[tuple[str, date], dict[str, Any]],
    ) -> str:
        """Punch-type column: Attendance status wins; else P if raw punches exist, else A."""
        record = attendance.get((emp_code, day))
        if record and record.get("status"):
            return str(record["status"])
        if punches.get((emp_code, day)):
            return "P"
        return "A"

    def _punch_type_row(self, emp_code: str) -> list[Any]:
        cells = [
            self._status_code_for_day(emp_code, d, self._punches, self._attendance)
            for d in self._days
        ]
        presentish_days = sum(1 for c in cells if c in MATRIX_PRESENTISH_STATUSES)
        total = str(presentish_days) if presentish_days else "0"
        return ["3PunchType", total, *cells]

    def _working_hours_row(self, emp_code: str) -> list[Any]:
        decimals: list[Any] = []
        cells: list[str] = []
        for day in self._days:
            rec = self._attendance.get((emp_code, day))
            dec = rec.get("working_hours") if rec else None
            decimals.append(dec)
            cells.append(self._fmt.decimal_working_hours_as_hh_mm(dec))
        total = self._fmt.sum_decimal_hours_as_total_hh_mm(decimals)
        return ["4WorkingHours", total, *cells]

    @staticmethod
    def _overtime_stub_row(num_days: int) -> list[Any]:
        """Placeholder overtime row until product defines OT rules."""
        zeros = ["0:00"] * num_days
        return ["5OverTimeHrs", "0:00", *zeros]


# Backward-compatible names for existing imports and call sites.
def build_matrix_csv_rows(
    *,
    workflow_label: str,
    start_date: date,
    end_date: date,
    emp_map: dict[str, dict],
    groups: defaultdict,
    attendance_by_key: dict[tuple[str, date], dict],
) -> list[list[Any]]:
    """
    Build matrix rows (wrapper kept for callers/tests that pass loose dict inputs).

    Prefer ``VendorMatrixCsvBuilder`` or ``build_matrix_csv_bundle_for_user`` for new code.
    """
    builder = VendorMatrixCsvBuilder(
        calendar_days=inclusive_calendar_days(start_date, end_date),
        employee_by_code=emp_map,
        punches_by_employee_and_date=groups,
        attendance_by_employee_and_date=attendance_by_key,
        workflow_title=workflow_label,
    )
    return builder.build_rows()


def matrix_rows_to_csv_bytes(rows: list[list[Any]]) -> bytes:
    """Alias of :func:`encode_rows_as_utf8_sig_csv` for older call sites."""
    return encode_rows_as_utf8_sig_csv(rows)


def load_attendance_by_emp_date(emp_map: dict[str, dict], start: date, end: date):
    """Deprecated name; use :func:`load_attendance_index_by_employee_and_date`."""
    return load_attendance_index_by_employee_and_date(emp_map, start, end)


def load_biometric_groups(emp_codes: set[str], start: date, end: date):
    """Deprecated name; use :func:`load_biometric_punches_grouped_by_employee_and_date`."""
    return load_biometric_punches_grouped_by_employee_and_date(emp_codes, start, end)


def resolve_office_for_email_user(user, office_id: int | None):
    """Deprecated name; use :func:`resolve_office_for_email_context`."""
    return resolve_office_for_email_context(user, office_id)


def iter_dates_inclusive(start: date, end: date) -> list[date]:
    """Deprecated name; use :func:`inclusive_calendar_days`."""
    return inclusive_calendar_days(start, end)


def mmdd(d: date) -> str:
    """Deprecated name; use :func:`day_header_label_month_day`."""
    return day_header_label_month_day(d)
