from datetime import datetime

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from Users.auth_utils import require_auth
from Users.models import UserRole
from Employee.models import Employee
from Attenova.api_utils import pagination_params, parse_json_request, parse_iso_date
from Attendance.models import (
    Attendance,
    AttendanceRegularization,
    AttendanceStatus,
    RegularizationStatus,
)
from Attendance.utils import (
    apply_regularization,
    can_regularize_employee,
    can_review_regularization,
    get_approvers_for_employee,
    is_auto_approved,
    regularization_payload,
    regularizations_visible_to_user,
)
from Notifications.models import NotificationType
from Notifications.utils import create_bulk_notifications, create_notification


# ── helpers ─────────────────────────────────────────────────────────


def _parse_datetime(value):
    """Parse an ISO-8601 string to a timezone-aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except (ValueError, TypeError):
        return None


def _get_regularization_queryset(user):
    return regularizations_visible_to_user(user)


# ── Views ───────────────────────────────────────────────────────────


@method_decorator([csrf_exempt, require_auth], name="dispatch")
class RegularizationView(View):
    """
    POST /api/attendance/regularizations/      — create
    GET  /api/attendance/regularizations/       — list (filters: employee_id, date=YYYY-MM-DD, status,
                                                     date_from/date_to, office_id for Org Admin)
    GET  /api/attendance/regularizations/<pk>/  — detail
    """

    def post(self, request):
        body, err = parse_json_request(request)
        if err:
            return err

        employee_id = body.get("employee_id")
        date_str = body.get("date")
        new_status = body.get("new_status")
        reason = body.get("reason", "").strip()

        if not all([employee_id, date_str, new_status, reason]):
            return JsonResponse(
                {"error": "employee_id, date, new_status and reason are required"},
                status=400,
            )

        if new_status not in AttendanceStatus.values:
            return JsonResponse(
                {"error": f"new_status must be one of {list(AttendanceStatus.values)}"},
                status=400,
            )

        try:
            employee = Employee.objects.select_related("office").get(pk=employee_id)
        except Employee.DoesNotExist:
            return JsonResponse({"error": "Employee not found"}, status=404)

        if not can_regularize_employee(request.user, employee):
            return JsonResponse({"error": "Not authorized for this employee"}, status=403)

        att_date = parse_iso_date(date_str)
        if att_date is None:
            return JsonResponse({"error": "Invalid date format (expected YYYY-MM-DD)"}, status=400)

        today = timezone.now().date()
        if att_date > today:
            return JsonResponse({"error": "Cannot regularize a future date"}, status=400)

        try:
            attendance = Attendance.objects.get(employee=employee, date=att_date)
        except Attendance.DoesNotExist:
            return JsonResponse(
                {"error": f"No attendance record found for {employee.name} on {att_date}"},
                status=404,
            )

        new_first_in = _parse_datetime(body.get("new_first_in"))
        new_last_out = _parse_datetime(body.get("new_last_out"))

        auto = is_auto_approved(request.user)
        reg_status = RegularizationStatus.APPROVED if auto else RegularizationStatus.PENDING

        with transaction.atomic():
            attendance = Attendance.objects.select_for_update().get(pk=attendance.pk)
            if AttendanceRegularization.objects.filter(
                attendance=attendance,
                status=RegularizationStatus.PENDING,
            ).exists():
                return JsonResponse(
                    {"error": "A pending regularization already exists for this attendance record"},
                    status=409,
                )

            reg = AttendanceRegularization.objects.create(
                attendance=attendance,
                employee=employee,
                date=att_date,
                previous_first_in=attendance.first_in,
                previous_last_out=attendance.last_out,
                previous_status=attendance.status,
                new_status=new_status,
                new_first_in=new_first_in,
                new_last_out=new_last_out,
                reason=reason,
                status=reg_status,
                requested_by=request.user,
                reviewed_by=request.user if auto else None,
                reviewed_at=timezone.now() if auto else None,
            )

            if auto:
                apply_regularization(reg)

        if not auto:
            approvers = get_approvers_for_employee(employee)
            create_bulk_notifications(
                recipients=approvers,
                notification_type=NotificationType.REGULARIZATION_REQUEST,
                title=f"Regularization request for {employee.name}",
                message=(
                    f"{request.user.name or request.user.email} requested attendance "
                    f"regularization for {employee.name} on {att_date} – {reason}"
                ),
                related_object=reg,
                created_by=request.user,
            )

        reg.refresh_from_db()
        return JsonResponse(
            {"regularization": regularization_payload(reg)},
            status=201,
        )

    def get(self, request, pk=None):
        qs = _get_regularization_queryset(request.user)

        if pk is not None:
            try:
                reg = qs.get(pk=pk)
            except AttendanceRegularization.DoesNotExist:
                return JsonResponse({"error": "Regularization not found"}, status=404)
            return JsonResponse({"regularization": regularization_payload(reg)})

        # list with optional filters
        emp_id = request.GET.get("employee_id")
        if emp_id:
            try:
                qs = qs.filter(employee_id=int(emp_id))
            except (TypeError, ValueError):
                return JsonResponse({"error": "Invalid employee_id"}, status=400)

        # Only Org Admin can filter by office_id; Manager/Office Admin/Supervisor see only their office(s)
        office_id = request.GET.get("office_id")
        if office_id and request.user.role == UserRole.ORG_ADMIN:
            try:
                qs = qs.filter(employee__office_id=int(office_id))
            except (ValueError, TypeError):
                pass

        status_filter = request.GET.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        date_from = request.GET.get("date_from")
        if date_from:
            parsed = parse_iso_date(date_from)
            if parsed is None:
                return JsonResponse({"error": "Invalid date_from"}, status=400)
            qs = qs.filter(date__gte=parsed)

        date_to = request.GET.get("date_to")
        if date_to:
            parsed = parse_iso_date(date_to)
            if parsed is None:
                return JsonResponse({"error": "Invalid date_to"}, status=400)
            qs = qs.filter(date__lte=parsed)

        date_exact = (request.GET.get("date") or "").strip()
        if date_exact:
            d_exact = parse_iso_date(date_exact)
            if d_exact is None:
                return JsonResponse({"error": "Invalid date"}, status=400)
            qs = qs.filter(date=d_exact)

        page, page_size, start = pagination_params(request.GET)

        total = qs.count()
        regs = list(qs[start : start + page_size])

        return JsonResponse(
            {
                "regularizations": [regularization_payload(r) for r in regs],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def approve_regularization(request, pk):
    """POST /api/attendance/regularizations/<pk>/approve/"""
    body, err = parse_json_request(request)
    if err:
        return err

    qs = _get_regularization_queryset(request.user)
    with transaction.atomic():
        try:
            reg = qs.select_for_update().get(pk=pk)
        except AttendanceRegularization.DoesNotExist:
            return JsonResponse({"error": "Regularization not found"}, status=404)

        if reg.status != RegularizationStatus.PENDING:
            return JsonResponse(
                {"error": f"Cannot approve – current status is {reg.status}"},
                status=400,
            )

        if not can_review_regularization(request.user, reg):
            return JsonResponse({"error": "Not authorized to approve"}, status=403)

        reg.status = RegularizationStatus.APPROVED
        reg.reviewed_by = request.user
        reg.reviewed_at = timezone.now()
        reg.review_remarks = body.get("remarks", "")
        reg.save()
        apply_regularization(reg)

    create_notification(
        recipient=reg.requested_by,
        notification_type=NotificationType.REGULARIZATION_APPROVED,
        title=f"Regularization approved for {reg.employee.name}",
        message=(
            f"Your regularization request for {reg.employee.name} on "
            f"{reg.date} has been approved by {request.user.name or request.user.email}."
        ),
        related_object=reg,
        created_by=request.user,
    )

    reg.refresh_from_db()
    return JsonResponse({"regularization": regularization_payload(reg)})


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def reject_regularization(request, pk):
    """POST /api/attendance/regularizations/<pk>/reject/"""
    body, err = parse_json_request(request)
    if err:
        return err

    remarks = body.get("remarks", "").strip()
    if not remarks:
        return JsonResponse(
            {"error": "remarks is required when rejecting"},
            status=400,
        )

    qs = _get_regularization_queryset(request.user)
    with transaction.atomic():
        try:
            reg = qs.select_for_update().get(pk=pk)
        except AttendanceRegularization.DoesNotExist:
            return JsonResponse({"error": "Regularization not found"}, status=404)

        if reg.status != RegularizationStatus.PENDING:
            return JsonResponse(
                {"error": f"Cannot reject – current status is {reg.status}"},
                status=400,
            )

        if not can_review_regularization(request.user, reg):
            return JsonResponse({"error": "Not authorized to reject"}, status=403)

        reg.status = RegularizationStatus.REJECTED
        reg.reviewed_by = request.user
        reg.reviewed_at = timezone.now()
        reg.review_remarks = remarks
        reg.save()

    create_notification(
        recipient=reg.requested_by,
        notification_type=NotificationType.REGULARIZATION_REJECTED,
        title=f"Regularization rejected for {reg.employee.name}",
        message=(
            f"Your regularization request for {reg.employee.name} on "
            f"{reg.date} has been rejected by {request.user.name or request.user.email}. "
            f"Reason: {remarks}"
        ),
        related_object=reg,
        created_by=request.user,
    )

    reg.refresh_from_db()
    return JsonResponse({"regularization": regularization_payload(reg)})
