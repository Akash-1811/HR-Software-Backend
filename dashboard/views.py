from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any, Dict

from django.db.models import Q
from django.http import JsonResponse, QueryDict
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from Attendance.models import Attendance, RegularizationStatus
from Attendance.views import _get_regularization_queryset
from Attenova.api_utils import pagination_params
from Employee.utils import apply_list_filters, get_employees_queryset
from Organization.models import Department, Office
from Reports.utils import parse_date
from Shifts.models import Shift
from Users.auth_utils import require_auth

from dashboard import utils

_ATTENTION_KIND_FILTER_ALLOWED = frozenset({"late", "absent_marked", "missing_record"})


def build_dashboard_home_payload(*, query_get: QueryDict, user: Any, today) -> Dict[str, Any]:
    dash_req = utils.DashboardSummaryRequest.parse(query_get, today=today)

    employees_qs = apply_list_filters(
        get_employees_queryset(user).filter(is_active=True),
        user,
        query_get,
    )
    roster_values = list(
        employees_qs.values(
            "id",
            "name",
            "emp_code",
            "office_id",
            "shift_id",
            "department_id",
        )
    )
    emp_ids = [r["id"] for r in roster_values]
    active_roster = len(emp_ids)
    emp_ids_sorted = tuple(sorted(emp_ids))

    trend_start = dash_req.as_of - timedelta(days=dash_req.trend_days - 1)
    dates_ordered: list = []
    d = trend_start
    while d <= dash_req.as_of:
        dates_ordered.append(d)
        d += timedelta(days=1)

    attendance_map = utils.fetch_attendance_status_map(emp_ids_sorted, trend_start, dash_req.as_of)
    summary = utils.summary_block_as_dict(emp_ids_sorted, dash_req.as_of, attendance_map)

    pending_rq = _get_regularization_queryset(user)
    pending_regularizations = pending_rq.filter(status=RegularizationStatus.PENDING).count()

    trend_dates_payload, trend_rates = utils.build_daily_trend_rates(emp_ids_sorted, attendance_map, dates_ordered)
    recent = utils.recent_check_events_for_roster(emp_ids_sorted, dash_req.punch_preview_limit)
    cmp_prior = utils.prior_day_rate_comparison(emp_ids_sorted, dash_req.as_of, attendance_map)

    office_ids = {r["office_id"] for r in roster_values if r["office_id"]}
    shift_ids_nonnull = {r["shift_id"] for r in roster_values if r["shift_id"]}
    dept_ids_nonnull = {r["department_id"] for r in roster_values if r["department_id"]}

    office_labels = {o["id"]: o["name"] for o in Office.objects.filter(pk__in=office_ids).values("id", "name")}
    shift_labels = {s["id"]: s["name"] for s in Shift.objects.filter(pk__in=shift_ids_nonnull).values("id", "name")}
    dept_labels = {x["id"]: x["name"] for x in Department.objects.filter(pk__in=dept_ids_nonnull).values("id", "name")}

    office_groups = utils.group_roster_by_axis(roster_values, utils.BreakdownAxis.OFFICE_ID)
    shift_groups = utils.group_roster_by_axis(roster_values, utils.BreakdownAxis.SHIFT_ID)
    dept_groups = utils.group_roster_by_axis(roster_values, utils.BreakdownAxis.DEPARTMENT_ID)

    by_office_rows = utils.build_breakdown_rows(
        office_groups,
        dash_req.as_of,
        attendance_map,
        axis=utils.BreakdownAxis.OFFICE_ID,
        labels_by_id=office_labels,
    )
    by_shift_rows = utils.build_breakdown_rows(
        shift_groups,
        dash_req.as_of,
        attendance_map,
        axis=utils.BreakdownAxis.SHIFT_ID,
        labels_by_id=shift_labels,
    )
    by_department_rows = utils.build_breakdown_rows(
        dept_groups,
        dash_req.as_of,
        attendance_map,
        axis=utils.BreakdownAxis.DEPARTMENT_ID,
        labels_by_id=dept_labels,
    )

    att_detail_by_emp = {
        a["employee_id"]: a
        for a in Attendance.objects.filter(
            employee_id__in=emp_ids_sorted,
            date=dash_req.as_of,
        ).values("employee_id", "status", "late_minutes")
    }
    attention_today_preview = utils.attention_preview_for_dashboard(
        roster_values,
        dash_req.as_of,
        attendance_map,
        att_detail_by_emp,
        office_labels,
        preview_limit=utils.ATTENTION_HOMEPAGE_PREVIEW_LIMIT,
    )

    return {
        "as_of_date": dash_req.as_of.isoformat(),
        "pending_regularizations": pending_regularizations,
        "active_roster": active_roster,
        **summary,
        "prior_comparison": cmp_prior,
        "trend": {
            "days": dash_req.trend_days,
            "dates": trend_dates_payload,
            "rates": trend_rates,
        },
        "recent_punches": recent,
        "by_office": by_office_rows,
        "by_shift": by_shift_rows,
        "by_department": by_department_rows,
        "attention_today": attention_today_preview,
    }


def build_attention_report_payload(*, query_get: QueryDict, user: Any, today) -> Dict[str, Any]:
    target = parse_date(query_get.get("date")) or today

    employees_qs = apply_list_filters(
        get_employees_queryset(user).filter(is_active=True),
        user,
        query_get,
    )
    search = (query_get.get("search") or "").strip()
    if search:
        employees_qs = employees_qs.filter(Q(name__icontains=search) | Q(emp_code__icontains=search))

    roster_values = list(
        employees_qs.values(
            "id",
            "name",
            "emp_code",
            "office_id",
        )
    )
    emp_ids_sorted = tuple(sorted(r["id"] for r in roster_values))
    active_roster = len(roster_values)

    if active_roster == 0:
        return {
            "as_of_date": target.isoformat(),
            "active_roster": 0,
            "exception_counts": {
                "late": 0,
                "absent_marked": 0,
                "missing_record": 0,
                "total": 0,
            },
            "rows": [],
            "total": 0,
            "page": 1,
            "page_size": utils.DEFAULT_ATTENTION_PAGE_SIZE,
        }

    attendance_map = utils.fetch_attendance_status_map(emp_ids_sorted, target, target)
    att_detail_by_emp = {
        a["employee_id"]: a
        for a in Attendance.objects.filter(
            employee_id__in=emp_ids_sorted,
            date=target,
        ).values("employee_id", "status", "late_minutes")
    }

    office_ids = {r["office_id"] for r in roster_values if r.get("office_id")}
    office_labels = {o["id"]: o["name"] for o in Office.objects.filter(pk__in=office_ids).values("id", "name")}

    rows_all = utils.build_attention_issue_rows(
        roster_values,
        target,
        attendance_map,
        att_detail_by_emp,
        office_labels,
        limit=None,
    )
    kind_counts = Counter(str(r["kind"]) for r in rows_all)
    kind_filter = (query_get.get("kind") or "").strip()
    if kind_filter in _ATTENTION_KIND_FILTER_ALLOWED:
        rows_full = [r for r in rows_all if str(r["kind"]) == kind_filter]
    else:
        rows_full = rows_all
    total_issues_all_kinds = len(rows_all)
    total = len(rows_full)

    page, page_size, offset = pagination_params(
        query_get,
        default_page_size=utils.DEFAULT_ATTENTION_PAGE_SIZE,
        max_page_size=utils.MAX_ATTENTION_PAGE_SIZE,
    )
    page_rows = rows_full[offset : offset + page_size]

    return {
        "as_of_date": target.isoformat(),
        "active_roster": active_roster,
        "exception_counts": {
            "late": kind_counts.get("late", 0),
            "absent_marked": kind_counts.get("absent_marked", 0),
            "missing_record": kind_counts.get("missing_record", 0),
            "total": total_issues_all_kinds,
        },
        "rows": page_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@method_decorator(require_auth, name="dispatch")
class DashboardHomeView(View):
    """GET ``/api/dashboard/summary/`` — homepage KPIs and charts."""

    def get(self, request):
        payload = build_dashboard_home_payload(
            query_get=request.GET,
            user=request.user,
            today=timezone.now().date(),
        )
        return JsonResponse(payload)


@method_decorator(require_auth, name="dispatch")
class AttentionReportView(View):
    """GET ``/api/dashboard/attention/`` — full needs-attention list (Reports UI)."""

    def get(self, request):
        payload = build_attention_report_payload(
            query_get=request.GET,
            user=request.user,
            today=timezone.now().date(),
        )
        return JsonResponse(payload)
