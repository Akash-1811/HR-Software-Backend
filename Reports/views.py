"""
Report views for Attenova.

Attendance report is date-ranged; visibility is office-scoped:
- Org Admin: sees all offices in their org; may filter by office_id.
- Office Admin, Supervisor, Office Manager: see only their respective office(s).
"""

from collections import defaultdict

from django.db.models import Q, Count
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from Attenova.api_utils import pagination_params, parse_json_request
from Biometric.models import DummyEsslBiometricAttendanceData
from Employee.utils import apply_list_filters, get_employees_queryset
from Reports.utils import (
    build_hierarchical_rows,
    compute_attendance_report_stats,
    empty_attendance_response,
    fetch_regularized_clock_strings_for_pairs,
    fetch_working_hours_for_pairs,
    parse_date,
)
from Attendance.utils import regularization_payload, regularizations_visible_to_user
from Users.auth_utils import require_auth


@method_decorator(require_auth, name="dispatch")
class AttendanceReport(View):
    """
    GET /api/reports/attendance/

    Hierarchical attendance report: one parent row per employee-day with first_in, last_out,
    and nested punches. Default: today if start_date/end_date omitted.
    Office scope: Org Admin sees org offices (optional office_id); others see only their office.

    Query params:
        start_date, end_date  - YYYY-MM-DD (optional; default: today)
        search                - filter by employee name or emp_code
        office_id             - filter by office (Org Admin only; others ignored)
        status                - "in" or "out" to filter punch direction
        page, page_size       - pagination (default page_size=20, max 100)
        sort                  - "date_desc" (default) or "date_asc"

    Response:
        { "stats": {...}, "rows": [{ employee_code, employee_name, device_id, log_date,
          first_in, last_out, hours_worked, is_regularized, punches: [{ status, check_in, check_out }] }], ... }
    """

    def get(self, request):
        today = timezone.now().date()
        start_date = parse_date(request.GET.get("start_date")) or today
        end_date = parse_date(request.GET.get("end_date")) or today

        if start_date > end_date:
            return JsonResponse(
                {"error": "start_date must be <= end_date"},
                status=400,
            )

        # Office-scoped: Org Admin → org (optional office_id); others → only their office
        employees_qs = apply_list_filters(
            get_employees_queryset(request.user).filter(is_active=True),
            request.user,
            request.GET,
        )
        emp_codes = set(employees_qs.values_list("emp_code", flat=True).distinct())
        if not emp_codes:
            return empty_attendance_response(start_date, end_date)

        search = (request.GET.get("search") or "").strip()
        if search:
            search_qs = employees_qs.filter(Q(name__icontains=search) | Q(emp_code__icontains=search))
            emp_codes = set(search_qs.values_list("emp_code", flat=True))
            if not emp_codes:
                return empty_attendance_response(start_date, end_date)

        status_filter = (request.GET.get("status") or "").strip().lower()
        direction_filter = status_filter if status_filter in ("in", "out") else None

        page, page_size, offset = pagination_params(request.GET)
        sort = (request.GET.get("sort") or "date_desc").strip().lower()
        sort_desc = sort != "date_asc"

        emp_rows = list(employees_qs.filter(emp_code__in=emp_codes).values("id", "emp_code", "name"))
        emp_map = {e["emp_code"]: e for e in emp_rows}
        scoped_employee_ids = [e["id"] for e in emp_rows]

        qs = DummyEsslBiometricAttendanceData.objects.filter(
            UserId__in=emp_codes,
            LogDate__isnull=False,
            LogDate__date__gte=start_date,
            LogDate__date__lte=end_date,
        )
        if direction_filter == "in":
            qs = qs.filter(Q(Direction__iexact="in") | Q(Direction="1"))
        elif direction_filter == "out":
            qs = qs.filter(Q(Direction__iexact="out") | Q(Direction="0"))

        raw = list(qs.order_by("-LogDate", "UserId").values("UserId", "DeviceId", "LogDate", "Direction"))

        # Group by (emp_code, date) - single pass
        groups = defaultdict(list)
        for r in raw:
            log_dt = r.get("LogDate")
            if log_dt and hasattr(log_dt, "date"):
                groups[(r["UserId"], log_dt.date())].append(r)

        sorted_keys = sorted(
            groups.keys(),
            key=lambda k: (
                -k[1].toordinal(),
                (emp_map.get(k[0]) or {}).get("name", ""),
            ),
        )
        if not sort_desc:
            sorted_keys = list(reversed(sorted_keys))

        total = len(sorted_keys)
        page_keys = sorted_keys[offset : offset + page_size]
        date_emp_pairs = set(page_keys)
        attendance_working_hours = fetch_working_hours_for_pairs(date_emp_pairs, employee_ids=scoped_employee_ids)
        regularized_clocks = fetch_regularized_clock_strings_for_pairs(date_emp_pairs, employee_ids=scoped_employee_ids)

        page_groups = {k: groups[k] for k in page_keys}
        rows = build_hierarchical_rows(
            emp_map,
            page_groups,
            attendance_working_hours,
            page_keys,
            regularized_clocks=regularized_clocks,
        )

        stats = compute_attendance_report_stats(employees_qs, start_date, end_date)

        return JsonResponse(
            {
                "stats": stats,
                "rows": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )


@method_decorator(require_auth, name="dispatch")
class RegularizationReport(View):
    """
    GET /api/reports/regularization/

    Audit-style list of attendance regularizations in the user's scope.
    Rows are filtered by the attendance calendar date (the day being corrected),
    with the same active-employee + office filters as the attendance report.

    Query params:
        start_date, end_date — YYYY-MM-DD (default: today)
        search — employee name, code, or reason substring
        office_id — Org Admin only (same as attendance report)
        status — PENDING | APPROVED | REJECTED (optional)
        page, page_size
    """

    def get(self, request):
        today = timezone.now().date()
        start_date = parse_date(request.GET.get("start_date")) or today
        end_date = parse_date(request.GET.get("end_date")) or today

        if start_date > end_date:
            return JsonResponse(
                {"error": "start_date must be <= end_date"},
                status=400,
            )

        employees_qs = apply_list_filters(
            get_employees_queryset(request.user).filter(is_active=True),
            request.user,
            request.GET,
        )
        emp_ids = list(employees_qs.values_list("id", flat=True))
        if not emp_ids:
            return JsonResponse(
                {
                    "summary": {
                        "total": 0,
                        "pending": 0,
                        "approved": 0,
                        "rejected": 0,
                    },
                    "rows": [],
                    "total": 0,
                    "page": 1,
                    "page_size": 20,
                }
            )

        qs = regularizations_visible_to_user(request.user).filter(
            employee_id__in=emp_ids,
            date__gte=start_date,
            date__lte=end_date,
        )

        search = (request.GET.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(employee__name__icontains=search)
                | Q(employee__emp_code__icontains=search)
                | Q(reason__icontains=search)
            )

        status_filter = (request.GET.get("status") or "").strip().upper()
        if status_filter in {"PENDING", "APPROVED", "REJECTED"}:
            qs = qs.filter(status=status_filter)

        qs = qs.order_by("-date", "-created_at")

        summary_row = qs.aggregate(
            total=Count("pk"),
            pending=Count("pk", filter=Q(status="PENDING")),
            approved=Count("pk", filter=Q(status="APPROVED")),
            rejected=Count("pk", filter=Q(status="REJECTED")),
        )

        page, page_size, start_offset = pagination_params(request.GET)
        total = summary_row["total"]
        regs = list(qs[start_offset : start_offset + page_size])

        return JsonResponse(
            {
                "summary": {
                    "total": total,
                    "pending": summary_row["pending"],
                    "approved": summary_row["approved"],
                    "rejected": summary_row["rejected"],
                },
                "rows": [regularization_payload(r) for r in regs],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )


@method_decorator([csrf_exempt, require_auth], name="dispatch")
class SendAttendanceEmailReport(View):
    """
    POST /api/reports/attendance/send-email/

    Send attendance matrix report (CSV attachment) for a date range.
    Body:
        { "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD" } (preferred)
        or legacy { "date": "YYYY-MM-DD" } → single-day range.
    Optional: "office_id" (int), "search" (str).
    """

    def post(self, request):
        body, err = parse_json_request(request)
        if err:
            return err

        start_str = (body.get("start_date") or "").strip()
        end_str = (body.get("end_date") or "").strip()
        legacy = (body.get("date") or "").strip()

        if start_str and end_str:
            start_date = parse_date(start_str)
            end_date = parse_date(end_str)
            if not start_date or not end_date:
                return JsonResponse(
                    {"error": "Invalid start_date or end_date (use YYYY-MM-DD)"},
                    status=400,
                )
        elif legacy:
            d = parse_date(legacy)
            if not d:
                return JsonResponse(
                    {"error": "Invalid date format (expected YYYY-MM-DD)"},
                    status=400,
                )
            start_date = end_date = d
        else:
            return JsonResponse(
                {
                    "error": "Provide start_date and end_date (YYYY-MM-DD), or legacy date",
                },
                status=400,
            )

        if start_date > end_date:
            return JsonResponse(
                {"error": "start_date must be <= end_date"},
                status=400,
            )

        office_id = body.get("office_id")
        if office_id is not None:
            try:
                office_id = int(office_id)
            except (TypeError, ValueError):
                office_id = None

        search = (body.get("search") or "").strip()

        from Reports.email_report import run_send_manual_ui_report

        try:
            result = run_send_manual_ui_report(
                request.user,
                start_date=start_date,
                end_date=end_date,
                office_id=office_id,
                search=search,
            )
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)

        return JsonResponse(
            {
                "success": True,
                "message": result["message"],
                "sent": result["sent"],
            }
        )
