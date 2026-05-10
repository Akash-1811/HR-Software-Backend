from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from Attenova.api_utils import parse_json_request
from Organization.access import user_can_access_office
from Shifts.models import Shift
from Shifts.utils import (
    apply_shift_patch,
    get_accessible_shifts_queryset,
    parse_min_working_hours,
    parse_shift_time,
    parse_weekoff_days,
    serialize_shift,
)
from Users.auth_utils import require_auth


@method_decorator(csrf_exempt, name="dispatch")
class ShiftView(View):
    """
    GET    /api/shifts/       → list (auth). Filter: ?office_id=
    POST   /api/shifts/       → create (auth)
    GET    /api/shifts/<id>/  → detail (auth)
    PATCH  /api/shifts/<id>/  → update (auth)
    DELETE /api/shifts/<id>/  → delete (auth)
    """

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
        user = request.user
        shifts = get_accessible_shifts_queryset(user).order_by("office", "name")
        office_id = request.GET.get("office_id")
        if office_id:
            try:
                oid = int(office_id)
                shifts = shifts.filter(office_id=oid)
            except (TypeError, ValueError):
                pass
        return JsonResponse({"shifts": [serialize_shift(s) for s in shifts]}, status=200)

    def _detail(self, request, pk):
        shift = Shift.objects.filter(pk=pk).select_related("office").first()
        if not shift:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(request.user, shift.office):
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse(serialize_shift(shift), status=200)

    def _create(self, request):
        user = request.user
        body, err = parse_json_request(request)
        if err:
            return err

        office_id = body.get("office_id")
        name = (body.get("name") or "").strip()
        start_time = parse_shift_time(body.get("start_time"))
        end_time = parse_shift_time(body.get("end_time"))

        if not office_id:
            return JsonResponse({"error": "office_id is required"}, status=400)
        try:
            office_id = int(office_id)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid office_id"}, status=400)
        if not name:
            return JsonResponse({"error": "name is required"}, status=400)
        if start_time is None:
            return JsonResponse({"error": "start_time is required (HH:MM)"}, status=400)
        if end_time is None:
            return JsonResponse({"error": "end_time is required (HH:MM)"}, status=400)

        from Organization.models import Office

        office = Office.objects.filter(pk=office_id).first()
        if not office:
            return JsonResponse({"error": "Office not found"}, status=404)
        if not user_can_access_office(user, office):
            return JsonResponse({"error": "Not found"}, status=404)

        grace_minutes = max(0, int(body.get("grace_minutes") or 0))
        weekoff_days = parse_weekoff_days(body.get("weekoff_days"))
        min_working_hours = parse_min_working_hours(body.get("min_working_hours"))
        lunch_break_minutes = max(0, int(body.get("lunch_break_minutes") or 0))
        tea_break_minutes = max(0, int(body.get("tea_break_minutes") or 0))
        lunch_break_paid = bool(body.get("lunch_break_paid", True))
        tea_breaks_paid = bool(body.get("tea_breaks_paid", True))
        is_night_shift = bool(body.get("is_night_shift", False))
        is_active = bool(body.get("is_active", True))
        is_default = bool(body.get("is_default", False))

        with transaction.atomic():
            if is_default:
                Shift.objects.filter(office_id=office_id).update(is_default=False)
            try:
                shift = Shift(
                    office=office,
                    name=name,
                    start_time=start_time,
                    end_time=end_time,
                    grace_minutes=grace_minutes,
                    weekoff_days=weekoff_days,
                    min_working_hours=min_working_hours,
                    lunch_break_minutes=lunch_break_minutes,
                    tea_break_minutes=tea_break_minutes,
                    lunch_break_paid=lunch_break_paid,
                    tea_breaks_paid=tea_breaks_paid,
                    is_night_shift=is_night_shift,
                    is_active=is_active,
                    is_default=is_default,
                    created_by=user,
                    updated_by=user,
                )
                shift.full_clean()
                shift.save()
            except ValidationError as e:
                return JsonResponse({"error": str(e)}, status=400)
        return JsonResponse(serialize_shift(shift), status=201)

    def _update(self, request, pk):
        user = request.user
        shift = Shift.objects.filter(pk=pk).select_related("office").first()
        if not shift:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(user, shift.office):
            return JsonResponse({"error": "Not found"}, status=404)

        body, err = parse_json_request(request)
        if err:
            return err

        update_fields, patch_error = apply_shift_patch(shift, body)
        if patch_error:
            return patch_error

        shift.updated_by = user
        update_fields.extend(["updated_at", "updated_by"])
        with transaction.atomic():
            if shift.is_default:
                Shift.objects.filter(office_id=shift.office_id).exclude(pk=shift.pk).update(is_default=False)
            try:
                shift.full_clean()
                shift.save(update_fields=update_fields)
            except ValidationError as e:
                return JsonResponse({"error": str(e)}, status=400)
        return JsonResponse(serialize_shift(shift), status=200)

    def _delete(self, request, pk):
        user = request.user
        shift = Shift.objects.filter(pk=pk).select_related("office").first()
        if not shift:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(user, shift.office):
            return JsonResponse({"error": "Not found"}, status=404)
        shift.delete()
        return JsonResponse({"message": "Deleted"}, status=200)
