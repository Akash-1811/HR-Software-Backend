from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from Attenova.api_utils import parse_json_request
from Organization.access import get_offices_queryset, user_can_access_office
from Shifts.models import Shift
from Users.auth_utils import require_auth


def _shift_payload(shift):
    """Build API payload for a Shift."""
    return {
        "id": shift.id,
        "office_id": shift.office_id,
        "name": shift.name,
        "start_time": shift.start_time.strftime("%H:%M") if shift.start_time else None,
        "end_time": shift.end_time.strftime("%H:%M") if shift.end_time else None,
        "grace_minutes": shift.grace_minutes,
        "is_night_shift": shift.is_night_shift,
        "is_active": shift.is_active,
        "is_default": shift.is_default,
        "created_at": shift.created_at.isoformat() if shift.created_at else None,
    }


def _get_shifts_queryset(user):
    """Shifts the user can access (via offices they can access)."""
    offices = get_offices_queryset(user)
    return Shift.objects.filter(office__in=offices).select_related("office")


def _parse_time(value):
    """Parse 'HH:MM' or 'HH:MM:SS' string to time. Returns None on failure."""
    if value is None:
        return None
    if hasattr(value, "hour"):  # already a time
        return value
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).time()
        except (ValueError, TypeError):
            continue
    return None


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
        shifts = _get_shifts_queryset(user).order_by("office", "name")
        office_id = request.GET.get("office_id")
        if office_id:
            try:
                office_id = int(office_id)
                shifts = shifts.filter(office_id=office_id)
            except (TypeError, ValueError):
                pass
        return JsonResponse({"shifts": [_shift_payload(s) for s in shifts]}, status=200)

    def _detail(self, request, pk):
        shift = Shift.objects.filter(pk=pk).select_related("office").first()
        if not shift:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(request.user, shift.office):
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse(_shift_payload(shift), status=200)

    def _create(self, request):
        user = request.user
        body, err = parse_json_request(request)
        if err:
            return err

        office_id = body.get("office_id")
        name = (body.get("name") or "").strip()
        start_time = _parse_time(body.get("start_time"))
        end_time = _parse_time(body.get("end_time"))
        grace_minutes = max(0, int(body.get("grace_minutes") or 0))
        is_night_shift = bool(body.get("is_night_shift", False))
        is_active = bool(body.get("is_active", True))
        is_default = bool(body.get("is_default", False))

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
        return JsonResponse(_shift_payload(shift), status=201)

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

        update_fields = []
        if "name" in body:
            v = (body.get("name") or "").strip()
            if v:
                shift.name = v
                update_fields.append("name")
        if "start_time" in body:
            t = _parse_time(body.get("start_time"))
            if t is not None:
                shift.start_time = t
                update_fields.append("start_time")
        if "end_time" in body:
            t = _parse_time(body.get("end_time"))
            if t is not None:
                shift.end_time = t
                update_fields.append("end_time")
        if "grace_minutes" in body:
            shift.grace_minutes = max(0, int(body.get("grace_minutes") or 0))
            update_fields.append("grace_minutes")
        if "is_night_shift" in body:
            shift.is_night_shift = bool(body["is_night_shift"])
            update_fields.append("is_night_shift")
        if "is_active" in body:
            shift.is_active = bool(body["is_active"])
            update_fields.append("is_active")
        if "is_default" in body:
            shift.is_default = bool(body["is_default"])
            update_fields.append("is_default")

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
        return JsonResponse(_shift_payload(shift), status=200)

    def _delete(self, request, pk):
        user = request.user
        shift = Shift.objects.filter(pk=pk).select_related("office").first()
        if not shift:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(user, shift.office):
            return JsonResponse({"error": "Not found"}, status=404)
        shift.delete()
        return JsonResponse({"message": "Deleted"}, status=200)
