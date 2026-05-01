"""
Department CRUD API: departments are scoped to an office.

GET  /api/employees/departments/?office_id=
POST /api/employees/departments/  { office_id, name }
GET  /api/employees/departments/<id>/
PATCH /api/employees/departments/<id>/
DELETE /api/employees/departments/<id>/
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from Attenova.api_utils import parse_json_request
from Organization.access import get_offices_queryset, user_can_access_office
from Organization.models import Department, Office

from Users.auth_utils import require_auth


def _department_payload(dept: Department) -> dict:
    return {
        "id": dept.id,
        "office_id": dept.office_id,
        "organization_id": dept.office.organization_id,
        "name": dept.name,
        "is_active": dept.is_active,
        "created_at": dept.created_at.isoformat() if dept.created_at else None,
        "updated_at": dept.updated_at.isoformat() if dept.updated_at else None,
    }


def departments_queryset_for_user(user):
    offices = get_offices_queryset(user)
    return Department.objects.filter(office__in=offices).select_related(
        "office",
        "office__organization",
    )


@method_decorator(csrf_exempt, name="dispatch")
class DepartmentView(View):
    @method_decorator(require_auth)
    def get(self, request, pk=None):
        if pk is None:
            return self._list(request)
        return self._detail(request, pk)

    @method_decorator(require_auth)
    def post(self, request):
        return self._create(request)

    @method_decorator(require_auth)
    def patch(self, request, pk):
        return self._update(request, pk)

    @method_decorator(require_auth)
    def delete(self, request, pk):
        return self._delete(request, pk)

    def _list(self, request):
        from Employee.utils import safe_int

        user = request.user
        office_id = safe_int(request.GET.get("office_id"))
        if office_id is None:
            return JsonResponse({"error": "office_id query parameter is required"}, status=400)

        office = Office.objects.filter(pk=office_id).first()
        if not office:
            return JsonResponse({"error": "Office not found"}, status=404)
        if not user_can_access_office(user, office):
            return JsonResponse({"error": "Not found"}, status=404)

        qs = (
            Department.objects.filter(office_id=office_id)
            .select_related("office", "office__organization")
            .order_by("name")
        )
        active_only = (request.GET.get("include_inactive") or "").lower() not in ("true", "1", "yes")
        if active_only:
            qs = qs.filter(is_active=True)
        return JsonResponse({"departments": [_department_payload(d) for d in qs]}, status=200)

    def _detail(self, request, pk):
        user = request.user
        dept = departments_queryset_for_user(user).filter(pk=pk).first()
        if not dept:
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse({"department": _department_payload(dept)}, status=200)

    def _create(self, request):
        from Employee.utils import safe_int, user_can_create_employees

        user = request.user
        if not user_can_create_employees(user):
            return JsonResponse({"error": "Not authorized to create departments"}, status=403)

        body, err = parse_json_request(request)
        if err:
            return err

        office_id = safe_int(body.get("office_id"))
        name = (body.get("name") or "").strip()
        if not office_id:
            return JsonResponse({"error": "office_id is required"}, status=400)
        if not name:
            return JsonResponse({"error": "name is required"}, status=400)

        office = Office.objects.filter(pk=office_id).first()
        if not office:
            return JsonResponse({"error": "Office not found"}, status=404)
        if not user_can_access_office(user, office):
            return JsonResponse({"error": "Not authorized for this office"}, status=403)

        try:
            dept = Department.objects.create(office_id=office_id, name=name)
        except (ValidationError, IntegrityError) as e:
            return JsonResponse({"error": str(e)}, status=400)
        dept = Department.objects.select_related("office", "office__organization").get(pk=dept.pk)
        return JsonResponse({"department": _department_payload(dept)}, status=201)

    def _update(self, request, pk):
        user = request.user
        from Employee.utils import user_can_create_employees

        if not user_can_create_employees(user):
            return JsonResponse({"error": "Not authorized"}, status=403)

        dept = departments_queryset_for_user(user).filter(pk=pk).first()
        if not dept:
            return JsonResponse({"error": "Not found"}, status=404)

        body, err = parse_json_request(request)
        if err:
            return err

        if "name" in body:
            n = (body.get("name") or "").strip()
            if n:
                dept.name = n
        if "is_active" in body:
            dept.is_active = bool(body["is_active"])
        try:
            dept.save()
        except (ValidationError, IntegrityError) as e:
            return JsonResponse({"error": str(e)}, status=400)

        dept.refresh_from_db()
        dept = Department.objects.select_related("office", "office__organization").get(pk=dept.pk)
        return JsonResponse({"department": _department_payload(dept)}, status=200)

    def _delete(self, request, pk):
        user = request.user
        from Employee.utils import user_can_create_employees

        if not user_can_create_employees(user):
            return JsonResponse({"error": "Not authorized"}, status=403)

        dept = departments_queryset_for_user(user).filter(pk=pk).first()
        if not dept:
            return JsonResponse({"error": "Not found"}, status=404)
        if dept.employees.exists():
            return JsonResponse(
                {"error": "Cannot delete department while employees are assigned to it"},
                status=409,
            )
        dept.delete()
        return JsonResponse({"message": "Deleted"}, status=200)


def resolve_optional_department(department_id, office_id):
    """
    If department_id is missing, returns (None, None).
    Otherwise returns (Department, None) or (None, JsonResponse error).
    """
    from Employee.utils import safe_int

    if department_id is None or department_id == "":
        return None, None
    did = safe_int(department_id)
    if did is None:
        return None, None
    dept = Department.objects.filter(pk=did, office_id=office_id, is_active=True).first()
    if not dept:
        return None, JsonResponse(
            {"error": "Department not found or does not belong to this office"},
            status=400,
        )
    return dept, None
