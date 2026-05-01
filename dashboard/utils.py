"""
Dashboard helpers: limits, query parsing, attendance KPIs, breakdowns, punches, attention rows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from django.http import QueryDict

from Attendance.models import Attendance, AttendancePunch, AttendanceStatus
from Employee.utils import safe_int
from Reports.utils import parse_date


# --- Limits (homepage + attention API) ---
MIN_TREND_DAYS = 7
DEFAULT_TREND_DAYS = 14
MAX_TREND_DAYS = 60

DEFAULT_PUNCH_PREVIEW_LIMIT = 24
MIN_PUNCH_PREVIEW_LIMIT = 1
MAX_PUNCH_PREVIEW_LIMIT = 50

ATTENTION_HOMEPAGE_PREVIEW_LIMIT = 25

DEFAULT_ATTENTION_PAGE_SIZE = 20
MAX_ATTENTION_PAGE_SIZE = 100


class BreakdownAxis(StrEnum):
    OFFICE_ID = "office_id"
    SHIFT_ID = "shift_id"
    DEPARTMENT_ID = "department_id"


class AttentionIssueKind(StrEnum):
    LATE = "late"
    ABSENT_MARKED = "absent_marked"
    MISSING_RECORD = "missing_record"


BREAKDOWN_BUCKET_FALLBACK_LABEL: dict[BreakdownAxis, str] = {
    BreakdownAxis.OFFICE_ID: "Unknown office",
    BreakdownAxis.SHIFT_ID: "No shift assigned",
    BreakdownAxis.DEPARTMENT_ID: "Unassigned",
}


def _clamp_punch_limit(raw_limit: int | None) -> int:
    if raw_limit is None:
        return DEFAULT_PUNCH_PREVIEW_LIMIT
    return max(MIN_PUNCH_PREVIEW_LIMIT, min(raw_limit, MAX_PUNCH_PREVIEW_LIMIT))


def _clamp_trend_days(raw: int | None) -> int:
    if raw is None or raw < MIN_TREND_DAYS:
        return DEFAULT_TREND_DAYS
    return min(raw, MAX_TREND_DAYS)


@dataclass(frozen=True, slots=True)
class DashboardSummaryRequest:
    as_of: date
    trend_days: int
    punch_preview_limit: int

    @classmethod
    def parse(cls, query_get: QueryDict, *, today: date) -> DashboardSummaryRequest:
        as_of = parse_date(query_get.get("date")) or today
        trend_days = _clamp_trend_days(safe_int(query_get.get("trend_days")))
        punch_lim = _clamp_punch_limit(safe_int(query_get.get("punch_limit")))
        return cls(as_of=as_of, trend_days=trend_days, punch_preview_limit=punch_lim)


StatusMap = Mapping[Tuple[int, date], str]


def attendance_status_counts_for_date(
    emp_ids: Sequence[int],
    d: date,
    attendance_rows: StatusMap,
) -> Dict[str, int]:
    roster = set(emp_ids)
    weekly_off = 0
    present = 0
    late = 0
    absent_marked = 0
    missing_record = 0

    for eid in roster:
        st = attendance_rows.get((eid, d))
        if st is None:
            missing_record += 1
            continue
        if st == AttendanceStatus.WO:
            weekly_off += 1
        elif st == AttendanceStatus.P:
            present += 1
        elif st == AttendanceStatus.L:
            late += 1
        elif st == AttendanceStatus.A:
            absent_marked += 1
        else:
            absent_marked += 1

    denominator = len(roster) - weekly_off
    attended = present + late
    rate = round(100 * attended / denominator, 1) if denominator > 0 else 0
    absent_total = absent_marked + missing_record

    return {
        "present": present,
        "late": late,
        "absent_marked": absent_marked,
        "missing_record": missing_record,
        "absent_total": absent_total,
        "weekly_off": weekly_off,
        "denominator_expected_at_work": denominator,
        "attendance_rate": rate,
    }


def fetch_attendance_status_map(
    emp_ids: Iterable[int],
    start: date,
    end: date,
) -> StatusMap:
    ids = list(emp_ids)
    if not ids:
        return {}
    rows = Attendance.objects.filter(
        employee_id__in=ids,
        date__gte=start,
        date__lte=end,
    ).values_list("employee_id", "date", "status")
    return {(int(eid), d): str(status) for eid, d, status in rows}


def build_daily_trend_rates(
    emp_ids_sorted: Sequence[int],
    attendance_map: StatusMap,
    dates: Iterable[date],
) -> Tuple[List[str], List[float]]:
    date_strs: List[str] = []
    rates: List[float] = []
    ids = tuple(emp_ids_sorted)
    for d in dates:
        counts = attendance_status_counts_for_date(ids, d, attendance_map)
        date_strs.append(d.isoformat())
        rates.append(float(counts["attendance_rate"]))
    return date_strs, rates


def summary_block_as_dict(
    emp_ids_sorted: Sequence[int],
    d: date,
    attendance_map: StatusMap,
) -> Dict[str, Any]:
    c = attendance_status_counts_for_date(tuple(emp_ids_sorted), d, attendance_map)
    return {
        "present": c["present"],
        "late": c["late"],
        "absent_marked": c["absent_marked"],
        "missing_record": c["missing_record"],
        "absent_total": c["absent_total"],
        "weekly_off": c["weekly_off"],
        "attendance_rate": c["attendance_rate"],
        "denominator_expected_at_work": c["denominator_expected_at_work"],
    }


def prior_day_rate_comparison(
    emp_ids_sorted: Sequence[int],
    target: date,
    attendance_map: StatusMap,
) -> Dict[str, Any]:
    prior = target - timedelta(days=1)
    ids = tuple(emp_ids_sorted)
    curr = attendance_status_counts_for_date(ids, target, attendance_map)
    prev = attendance_status_counts_for_date(ids, prior, attendance_map)
    delta = round(curr["attendance_rate"] - prev["attendance_rate"], 1)
    return {
        "prior_date": prior.isoformat(),
        "prior_attendance_rate": prev["attendance_rate"],
        "delta_vs_prior_day_pct_points": delta,
    }


_ISSUE_SORT_ORDER: dict[AttentionIssueKind, int] = {
    AttentionIssueKind.LATE: 0,
    AttentionIssueKind.ABSENT_MARKED: 1,
    AttentionIssueKind.MISSING_RECORD: 2,
}


def build_attention_issue_rows(
    roster_rows: Sequence[Mapping[str, Any]],
    target: date,
    attendance_map: StatusMap,
    att_detail_by_emp: Mapping[int, Mapping[str, Any]],
    office_labels: Mapping[int, str],
    *,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    rows_out: List[Dict[str, Any]] = []

    for row in roster_rows:
        eid = row["id"]
        st = attendance_map.get((eid, target))
        if st in (AttendanceStatus.P, AttendanceStatus.WO):
            continue

        oid = row.get("office_id")
        office_name = office_labels.get(oid) if oid else None

        if st == AttendanceStatus.L:
            det = att_detail_by_emp.get(eid) or {}
            lm = int(det.get("late_minutes") or 0)
            rows_out.append(
                {
                    "employee_id": eid,
                    "name": row.get("name") or "",
                    "emp_code": row.get("emp_code") or "",
                    "office_name": office_name,
                    "kind": AttentionIssueKind.LATE.value,
                    "late_minutes": lm,
                }
            )
        elif st == AttendanceStatus.A:
            rows_out.append(
                {
                    "employee_id": eid,
                    "name": row.get("name") or "",
                    "emp_code": row.get("emp_code") or "",
                    "office_name": office_name,
                    "kind": AttentionIssueKind.ABSENT_MARKED.value,
                    "late_minutes": 0,
                }
            )
        elif st is None:
            rows_out.append(
                {
                    "employee_id": eid,
                    "name": row.get("name") or "",
                    "emp_code": row.get("emp_code") or "",
                    "office_name": office_name,
                    "kind": AttentionIssueKind.MISSING_RECORD.value,
                    "late_minutes": 0,
                }
            )
        else:
            continue

    def sort_key(x: Mapping[str, Any]) -> Tuple[int, int, str]:
        kind = AttentionIssueKind(str(x["kind"]))
        pri = _ISSUE_SORT_ORDER.get(kind, 99)
        late_pri = -(x.get("late_minutes") or 0) if kind == AttentionIssueKind.LATE else 0
        return (pri, late_pri, (x.get("name") or "").lower())

    rows_out.sort(key=sort_key)
    if limit is not None:
        return rows_out[:limit]
    return rows_out


def attention_preview_for_dashboard(
    roster_rows: Sequence[Mapping[str, Any]],
    target: date,
    attendance_map: StatusMap,
    att_detail_by_emp: Mapping[int, Mapping[str, Any]],
    office_labels: Mapping[int, str],
    *,
    preview_limit: int,
) -> List[Dict[str, Any]]:
    return build_attention_issue_rows(
        roster_rows,
        target,
        attendance_map,
        att_detail_by_emp,
        office_labels,
        limit=preview_limit,
    )


def group_roster_by_axis(
    employee_rows: Iterable[Dict[str, Any]],
    axis: BreakdownAxis,
) -> Dict[Any, Tuple[int, Tuple[int, ...]]]:
    field = axis.value
    groups: Dict[Any, List[int]] = defaultdict(list)
    for row in employee_rows:
        eid = row["id"]
        k = row.get(field)
        groups[k].append(eid)

    result: Dict[Any, Tuple[int, Tuple[int, ...]]] = {}
    for k, ids in groups.items():
        result[k] = (len(ids), tuple(ids))
    return result


def build_breakdown_rows(
    grouped: Mapping[Any, Tuple[int, Tuple[int, ...]]],
    att_date: date,
    attendance_map: StatusMap,
    *,
    axis: BreakdownAxis,
    labels_by_id: Mapping[Any, str],
) -> List[Dict[str, Any]]:
    null_label = BREAKDOWN_BUCKET_FALLBACK_LABEL[axis]
    out: List[Dict[str, Any]] = []
    for k in sorted(grouped.keys(), key=lambda x: (x is None, str(x))):
        count, ids = grouped[k]
        blk = attendance_status_counts_for_date(ids, att_date, attendance_map)
        label = null_label if k is None else labels_by_id.get(k, null_label)

        out.append(
            {
                "id": k,
                "label": label,
                "assigned": count,
                "present": blk["present"],
                "late": blk["late"],
                "absent_total": blk["absent_total"],
                "weekly_off": blk["weekly_off"],
                "coverage_rate": round(100 * (blk["present"] + blk["late"]) / count, 1) if count else 0,
                "attendance_rate": blk["attendance_rate"],
            }
        )
    return out


def recent_check_events_for_roster(
    emp_ids_sorted: Sequence[int],
    lim: int,
) -> List[Dict[str, Any]]:
    if not emp_ids_sorted:
        return []
    qs = (
        AttendancePunch.objects.filter(attendance__employee_id__in=emp_ids_sorted)
        .select_related("attendance__employee")
        .order_by("-punch_time")[:lim]
    )
    out: List[Dict[str, Any]] = []
    for p in qs:
        emp = p.attendance.employee
        out.append(
            {
                "employee_name": emp.name,
                "emp_code": emp.emp_code,
                "designation": emp.designation or "",
                "punch_time": p.punch_time.isoformat(),
                "direction": (p.direction or "").lower(),
            }
        )
    return out
