from datetime import date
from decimal import Decimal

from django.db import transaction
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from Attenova.api_utils import parse_int_optional, pagination_params, parse_json_request
from Employee.models import Employee
from Employee.utils import user_can_access_employee
from Organization.access import is_superadmin
from Organization.models import Office
from Users.auth_utils import require_auth
from Users.models import UserRole

from Leaves.access import (
    get_accessible_leave_types_queryset,
    get_leave_applications_queryset,
    get_subject_employee,
    resolve_leave_context_office_id,
    user_can_assign_leave_balances,
    user_can_manage_leave_types,
    user_can_review_leave_applications,
    user_can_submit_leave_application,
)
from Leaves.models import EmployeeLeaveBalance, LeaveApplicationStatus, LeaveType
from Leaves.services import apply_leave, approve_leave, reject_leave
from Leaves.utils import (
    aggregate_employee_balance_totals,
    consumed_leave_days_in_calendar_year,
    parse_decimal_days_optional,
    serialize_application,
    serialize_balance,
    serialize_leave_type,
)

_LEAVE_APPLICATION_STATUS_VALUES = frozenset(s.value for s in LeaveApplicationStatus)


def _resolve_leave_type_office_for_write(user, body: dict):
    """
    Returns (Office instance, None) or (None, JsonResponse error).
    OFFICE_ADMIN is pinned to user.office_id (optional office_id must match).
    """
    requested_id = parse_int_optional(body.get("office_id"))

    if is_superadmin(user):
        if requested_id is None:
            return None, JsonResponse({"error": "office_id is required"}, status=400)
        office = Office.objects.filter(pk=requested_id, is_active=True).select_related("organization").first()
        if not office or not office.organization.is_active:
            return None, JsonResponse({"error": "Office not found"}, status=404)
        return office, None

    if user.role == UserRole.ORG_ADMIN:
        if not getattr(user, "organization_id", None):
            return None, JsonResponse({"error": "Forbidden"}, status=403)
        if requested_id is None:
            return None, JsonResponse({"error": "office_id is required"}, status=400)
        office = Office.objects.filter(
            pk=requested_id,
            organization_id=user.organization_id,
            is_active=True,
        ).first()
        if not office:
            return None, JsonResponse({"error": "Office not found"}, status=404)
        return office, None

    if user.role == UserRole.OFFICE_ADMIN:
        oid = getattr(user, "office_id", None)
        if not oid:
            return None, JsonResponse({"error": "Forbidden"}, status=403)
        office = Office.objects.filter(pk=oid, organization_id=user.organization_id, is_active=True).first()
        if not office:
            return None, JsonResponse({"error": "Forbidden"}, status=403)
        if requested_id is not None and requested_id != oid:
            return None, JsonResponse({"error": "Cannot manage leave types for another office"}, status=403)
        return office, None

    return None, JsonResponse({"error": "Forbidden"}, status=403)


@method_decorator(csrf_exempt, name="dispatch")
class LeaveTypeView(View):
    """
    GET    /api/leaves/types/       → list (?office_id= superadmin/org-admin narrow, ?is_active=)
    POST   /api/leaves/types/       → create (office_id required except pinned OFFICE_ADMIN)
    GET    /api/leaves/types/<id>/ → detail
    PATCH  /api/leaves/types/<id>/ → update (deactivate via is_active)
    """

    @method_decorator(require_auth)
    def get(self, request, pk=None):
        user = request.user
        if pk is None:
            qs = get_accessible_leave_types_queryset(user).order_by("office__name", "name")
            office_filter = parse_int_optional(request.GET.get("office_id"))
            if office_filter is not None:
                qs = qs.filter(office_id=office_filter)
            active_raw = (request.GET.get("is_active") or "").strip().lower()
            if active_raw in {"true", "1", "yes"}:
                qs = qs.filter(is_active=True)
            elif active_raw in {"false", "0", "no"}:
                qs = qs.filter(is_active=False)
            return JsonResponse({"leave_types": [serialize_leave_type(lt) for lt in qs]}, status=200)

        lt = LeaveType.objects.filter(pk=pk).select_related("office", "office__organization").first()
        if not lt:
            return JsonResponse({"error": "Not found"}, status=404)
        if lt not in get_accessible_leave_types_queryset(user):
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse(serialize_leave_type(lt), status=200)

    @method_decorator(require_auth)
    def post(self, request):
        user = request.user
        if not user_can_manage_leave_types(user):
            return JsonResponse({"error": "Forbidden"}, status=403)
        body, err = parse_json_request(request)
        if err:
            return err

        office, e = _resolve_leave_type_office_for_write(user, body)
        if e:
            return e

        name = (body.get("name") or "").strip()
        code = (body.get("code") or "").strip().upper()
        description = (body.get("description") or "").strip()
        if not name or not code:
            return JsonResponse({"error": "name and code are required"}, status=400)

        total = parse_decimal_days_optional(body.get("total_allowed_days"))
        if total is None or total < Decimal("0"):
            return JsonResponse({"error": "total_allowed_days must be a non-negative number"}, status=400)

        lt = LeaveType(
            office=office,
            name=name,
            code=code,
            description=description,
            is_paid=bool(body.get("is_paid", True)),
            total_allowed_days=total,
            is_active=bool(body.get("is_active", True)),
            requires_approval=bool(body.get("requires_approval", True)),
            allow_half_day=bool(body.get("allow_half_day", False)),
            allow_negative_balance=bool(body.get("allow_negative_balance", False)),
            created_by=user,
            updated_by=user,
        )
        lt.full_clean()
        try:
            lt.save()
        except Exception:
            return JsonResponse({"error": "Could not create leave type (duplicate code?)"}, status=400)
        return JsonResponse(serialize_leave_type(lt), status=201)

    @method_decorator(require_auth)
    def patch(self, request, pk):
        user = request.user
        if not user_can_manage_leave_types(user):
            return JsonResponse({"error": "Forbidden"}, status=403)
        lt = LeaveType.objects.filter(pk=pk).select_related("office", "office__organization").first()
        if not lt or lt not in get_accessible_leave_types_queryset(user):
            return JsonResponse({"error": "Not found"}, status=404)

        body, err = parse_json_request(request)
        if err:
            return err

        if "name" in body:
            lt.name = (body.get("name") or "").strip() or lt.name
        if "code" in body:
            lt.code = ((body.get("code") or "").strip().upper()) or lt.code
        if "description" in body:
            lt.description = (body.get("description") or "").strip()
        if "is_paid" in body:
            lt.is_paid = bool(body.get("is_paid"))
        if "total_allowed_days" in body:
            total = parse_decimal_days_optional(body.get("total_allowed_days"))
            if total is None or total < Decimal("0"):
                return JsonResponse({"error": "total_allowed_days invalid"}, status=400)
            lt.total_allowed_days = total
        if "is_active" in body:
            lt.is_active = bool(body.get("is_active"))
        if "requires_approval" in body:
            lt.requires_approval = bool(body.get("requires_approval"))
        if "allow_half_day" in body:
            lt.allow_half_day = bool(body.get("allow_half_day"))
        if "allow_negative_balance" in body:
            lt.allow_negative_balance = bool(body.get("allow_negative_balance"))

        lt.updated_by = user
        lt.full_clean()
        try:
            lt.save()
        except Exception:
            return JsonResponse({"error": "Could not update leave type"}, status=400)
        return JsonResponse(serialize_leave_type(lt), status=200)


@method_decorator(csrf_exempt, name="dispatch")
class LeaveBalanceView(View):
    """
    GET  /api/leaves/balances/?employee_id= — self when omitted; scoped by access.
    POST /api/leaves/balances/ — set allocated_days (upsert row); managers/org admins.
    """

    @method_decorator(require_auth)
    def get(self, request):
        user = request.user
        raw_eid = request.GET.get("employee_id")
        requested_id = None
        if raw_eid is not None:
            s = raw_eid.strip()
            if s:
                requested_id = parse_int_optional(s)
                if requested_id is None:
                    return JsonResponse({"error": "Invalid employee_id"}, status=400)

        emp = get_subject_employee(user, requested_id)
        if not emp:
            return JsonResponse({"error": "Not found"}, status=404)

        qs = EmployeeLeaveBalance.objects.filter(employee=emp).select_related("leave_type").order_by("leave_type__name")
        return JsonResponse({"balances": [serialize_balance(b) for b in qs]}, status=200)

    @method_decorator(require_auth)
    def post(self, request):
        user = request.user
        if not user_can_assign_leave_balances(user):
            return JsonResponse({"error": "Forbidden"}, status=403)
        body, err = parse_json_request(request)
        if err:
            return err

        emp_id = parse_int_optional(body.get("employee_id"))
        lt_id = parse_int_optional(body.get("leave_type_id"))
        if emp_id is None or lt_id is None:
            return JsonResponse({"error": "employee_id and leave_type_id are required"}, status=400)

        allocated = parse_decimal_days_optional(body.get("allocated_days"))
        if allocated is None or allocated < Decimal("0"):
            return JsonResponse({"error": "allocated_days must be a non-negative number"}, status=400)

        emp = Employee.objects.filter(pk=emp_id).select_related("organization", "office").first()
        if not emp or not user_can_access_employee(user, emp):
            return JsonResponse({"error": "Not found"}, status=404)

        lt = LeaveType.objects.filter(pk=lt_id).first()
        if not lt or lt.office_id != emp.office_id:
            return JsonResponse({"error": "Leave type not found for this employee"}, status=404)

        try:
            with transaction.atomic():
                Employee.objects.select_for_update().filter(pk=emp.pk).first()
                bal, _created = EmployeeLeaveBalance.objects.select_for_update().get_or_create(
                    employee=emp,
                    leave_type=lt,
                    defaults={"allocated_days": allocated, "consumed_days": Decimal("0")},
                )
                if not _created:
                    bal.allocated_days = allocated
                    bal.full_clean()
                    bal.save(update_fields=["allocated_days", "updated_at"])
                else:
                    bal.full_clean()
                    bal.save()
                bal = EmployeeLeaveBalance.objects.select_related("leave_type").get(pk=bal.pk)
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return JsonResponse({"balance": serialize_balance(bal)}, status=200)


@method_decorator(csrf_exempt, name="dispatch")
class LeaveApplicationView(View):
    """
    GET  /api/leaves/applications/ — list (filters: status, employee_id, office_id org-admin, pending_only)
    POST /api/leaves/applications/ — apply
    GET  /api/leaves/applications/<id>/ — detail
    """

    @method_decorator(require_auth)
    def get(self, request, pk=None):
        user = request.user
        if pk is not None:
            qs = get_leave_applications_queryset(user)
            app = qs.filter(pk=pk).first()
            if not app:
                return JsonResponse({"error": "Not found"}, status=404)
            return JsonResponse(serialize_application(app), status=200)

        qs = get_leave_applications_queryset(user)

        emp_id = parse_int_optional(request.GET.get("employee_id"))
        if emp_id is not None:
            qs = qs.filter(employee_id=emp_id)

        if (request.GET.get("pending_only") or "").strip().lower() in {"1", "true", "yes"}:
            qs = qs.filter(status=LeaveApplicationStatus.PENDING)
        else:
            st = (request.GET.get("status") or "").strip().upper()
            if st in _LEAVE_APPLICATION_STATUS_VALUES:
                qs = qs.filter(status=st)

        office_id = parse_int_optional(request.GET.get("office_id"))
        if office_id is not None and user.role == UserRole.ORG_ADMIN:
            qs = qs.filter(employee__office_id=office_id)

        page, page_size, start = pagination_params(request.GET)
        total = qs.count()
        rows = list(qs[start : start + page_size])
        return JsonResponse(
            {
                "applications": [serialize_application(a) for a in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            },
            status=200,
        )

    @method_decorator(require_auth)
    def post(self, request):
        user = request.user
        body, err = parse_json_request(request)
        if err:
            return err

        emp_id = parse_int_optional(body.get("employee_id"))
        emp = get_subject_employee(user, emp_id)
        if not emp:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_submit_leave_application(user, emp):
            return JsonResponse({"error": "Forbidden"}, status=403)

        lt_id = parse_int_optional(body.get("leave_type_id"))
        if lt_id is None:
            return JsonResponse({"error": "leave_type_id is required"}, status=400)

        lt = LeaveType.objects.filter(pk=lt_id).first()
        if not lt:
            return JsonResponse({"error": "Leave type not found"}, status=404)
        if lt not in get_accessible_leave_types_queryset(user):
            return JsonResponse({"error": "Not found"}, status=404)

        app, resp = apply_leave(user=user, employee=emp, leave_type=lt, payload=body)
        if resp:
            return resp
        return JsonResponse(serialize_application(app), status=201)


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def leave_application_approve(request, pk):
    app, resp = approve_leave(user=request.user, application_id=pk)
    if resp:
        return resp
    return JsonResponse(serialize_application(app), status=200)


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def leave_application_reject(request, pk):
    body, err = parse_json_request(request)
    if err:
        return err
    note = (body.get("reviewer_note") or body.get("note") or "").strip()
    if not note:
        return JsonResponse({"error": "reviewer_note is required"}, status=400)

    app, resp = reject_leave(user=request.user, application_id=pk, note=note)
    if resp:
        return resp
    return JsonResponse(serialize_application(app), status=200)


@csrf_exempt
@require_auth
@require_http_methods(["GET"])
def leave_dashboard_summary(request):
    """GET /api/leaves/summary/ — counts, personal leave metrics, and upcoming rows for the dashboard."""
    user = request.user
    qs = get_leave_applications_queryset(user)
    pending_review = 0
    if user_can_review_leave_applications(user):
        pending_review = qs.filter(status=LeaveApplicationStatus.PENDING).count()

    today = date.today()
    linked = get_subject_employee(user, None)

    my_pending = 0
    if linked:
        my_pending = qs.filter(employee_id=linked.pk, status=LeaveApplicationStatus.PENDING).count()

    context_office_id = resolve_leave_context_office_id(user, linked)

    available_sum = Decimal("0")
    allocated_sum = Decimal("0")
    consumed_this_year = Decimal("0")
    upcoming_payload = []

    if linked:
        consumed_this_year = consumed_leave_days_in_calendar_year(linked.pk, today.year)
        allocated_sum, available_sum = aggregate_employee_balance_totals(linked.pk)

        upcoming_qs = (
            get_leave_applications_queryset(user)
            .filter(
                employee_id=linked.pk,
                end_date__gte=today,
                status__in=(LeaveApplicationStatus.PENDING, LeaveApplicationStatus.APPROVED),
            )
            .order_by("start_date", "id")[:12]
        )
        upcoming_payload = [serialize_application(a) for a in upcoming_qs]

    return JsonResponse(
        {
            "pending_review_count": pending_review,
            "my_pending_count": my_pending,
            "can_manage_types": user_can_manage_leave_types(user),
            "can_assign_balances": user_can_assign_leave_balances(user),
            "can_review": user_can_review_leave_applications(user),
            "context_office_id": context_office_id,
            "leave_metrics_year": today.year,
            "available_leave_days_sum": float(available_sum),
            "allocated_leave_days_sum": float(allocated_sum),
            "consumed_leave_days_this_year": float(consumed_this_year),
            "has_linked_employee": linked is not None,
            "upcoming_leave_applications": upcoming_payload,
        },
        status=200,
    )
