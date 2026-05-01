import pymysql

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from Attenova.api_utils import parse_json_request
from Organization.access import user_can_access_office
from Users.auth_utils import require_auth

from Biometric.models import BiometricDevice
from Biometric.utils import (
    device_payload,
    format_time_for_essl,
    get_devices_queryset,
    get_essl_conn_params,
)

_ALLOWED_DEVICE_TYPE = {x[0] for x in BiometricDevice.DeviceType.choices}
_ALLOWED_DEVICE_DIRECTION = {x[0] for x in BiometricDevice.DeviceDirection.choices}


@method_decorator(csrf_exempt, name="dispatch")
class BiometricDeviceView(View):
    """
    GET    /api/biometric/devices/       → list (auth). Filter: ?office_id= (Org Admin only)
    POST   /api/biometric/devices/       → create (auth)
    GET    /api/biometric/devices/<id>/  → detail (auth)
    PATCH  /api/biometric/devices/<id>/ → update (auth)
    DELETE /api/biometric/devices/<id>/ → delete (auth)
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
        devices = get_devices_queryset(user).order_by("office", "device_id")
        office_id = request.GET.get("office_id")
        if office_id:
            try:
                office_id = int(office_id)
                devices = devices.filter(office_id=office_id)
            except (TypeError, ValueError):
                pass
        return JsonResponse(
            {"devices": [device_payload(d) for d in devices]},
            status=200,
        )

    def _detail(self, request, pk):
        device = BiometricDevice.objects.filter(pk=pk).select_related("office").first()
        if not device:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(request.user, device.office):
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse(device_payload(device), status=200)

    def _create(self, request):
        user = request.user
        body, err = parse_json_request(request)
        if err:
            return err

        office_id = body.get("office_id")
        device_id = (body.get("device_id") or "").strip()
        name = (body.get("name") or "").strip()
        is_active = bool(body.get("is_active", True))
        serial_number = (body.get("serial_number") or "").strip()[:128]
        ip_address = (body.get("ip_address") or "").strip()[:45]
        device_location = (body.get("device_location") or "").strip()[:255]
        device_type = (body.get("device_type") or "").strip().lower()
        if device_type and device_type not in _ALLOWED_DEVICE_TYPE:
            return JsonResponse(
                {"error": "device_type must be one of: face, finger, both, rfid"},
                status=400,
            )
        device_direction = (body.get("device_direction") or "").strip().lower()
        if device_direction and device_direction not in _ALLOWED_DEVICE_DIRECTION:
            return JsonResponse(
                {"error": "device_direction must be one of: in, out, alternate_in_out"},
                status=400,
            )

        if not office_id:
            return JsonResponse({"error": "office_id is required"}, status=400)
        if not device_id:
            return JsonResponse({"error": "device_id is required"}, status=400)

        try:
            office_id = int(office_id)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid office_id"}, status=400)

        from Organization.models import Office

        office = Office.objects.filter(pk=office_id).prefetch_related("managers").first()
        if not office:
            return JsonResponse({"error": "Office not found"}, status=404)
        if not user_can_access_office(user, office):
            return JsonResponse({"error": "Not authorized for this office"}, status=403)

        if BiometricDevice.objects.filter(office_id=office_id, device_id=device_id).exists():
            return JsonResponse(
                {"error": "A device with this device_id already exists for this office"},
                status=409,
            )

        try:
            device = BiometricDevice.objects.create(
                office_id=office_id,
                device_id=device_id,
                name=name,
                serial_number=serial_number,
                ip_address=ip_address,
                device_location=device_location,
                device_type=device_type,
                device_direction=device_direction,
                is_active=is_active,
            )
        except ValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)

        return JsonResponse(device_payload(device), status=201)

    def _update(self, request, pk):
        user = request.user
        device = BiometricDevice.objects.filter(pk=pk).select_related("office").first()
        if not device:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(user, device.office):
            return JsonResponse({"error": "Not found"}, status=404)

        body, err = parse_json_request(request)
        if err:
            return err

        if "name" in body:
            device.name = (body.get("name") or "").strip()
        if "is_active" in body:
            device.is_active = bool(body["is_active"])
        if "device_id" in body:
            new_device_id = (body.get("device_id") or "").strip()
            if not new_device_id:
                return JsonResponse({"error": "device_id cannot be empty"}, status=400)
            if (
                BiometricDevice.objects.filter(office_id=device.office_id, device_id=new_device_id)
                .exclude(pk=device.pk)
                .exists()
            ):
                return JsonResponse(
                    {"error": "A device with this device_id already exists for this office"},
                    status=409,
                )
            device.device_id = new_device_id
        if "serial_number" in body:
            device.serial_number = (body.get("serial_number") or "").strip()[:128]
        if "ip_address" in body:
            device.ip_address = (body.get("ip_address") or "").strip()[:45]
        if "device_location" in body:
            device.device_location = (body.get("device_location") or "").strip()[:255]
        if "device_type" in body:
            device_type = (body.get("device_type") or "").strip().lower()
            if device_type and device_type not in _ALLOWED_DEVICE_TYPE:
                return JsonResponse(
                    {"error": "device_type must be one of: face, finger, both, rfid"},
                    status=400,
                )
            device.device_type = device_type
        if "device_direction" in body:
            device_direction = (body.get("device_direction") or "").strip().lower()
            if device_direction and device_direction not in _ALLOWED_DEVICE_DIRECTION:
                return JsonResponse(
                    {"error": "device_direction must be one of: in, out, alternate_in_out"},
                    status=400,
                )
            device.device_direction = device_direction

        try:
            device.full_clean()
            device.save()
        except ValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)

        return JsonResponse(device_payload(device), status=200)

    def _delete(self, request, pk):
        device = BiometricDevice.objects.filter(pk=pk).select_related("office").first()
        if not device:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(request.user, device.office):
            return JsonResponse({"error": "Not found"}, status=404)
        device.delete()
        return JsonResponse({"message": "Deleted"}, status=200)


@require_auth
def essl_device_logs(request):
    """
    GET /api/biometric/essl-logs/
    Returns attendance logs from ESSL DB (grouped by employee/date): employee, device,
    log_date, direction, check_in_time, check_out_time, hours_worked.
    """
    if "essl_db" not in settings.DATABASES:
        return JsonResponse({"error": "essl_db not configured"}, status=500)

    sql = """
        SELECT 
            E.EmployeeCode,
            E.EmployeeName,
            D.DeviceId,
            CAST(D.LogDate AS DATE) AS LogDate,
            GROUP_CONCAT(DISTINCT D.Direction ORDER BY D.Direction) AS Direction,
            MIN(CASE WHEN D.Direction = 'IN' THEN TIME(D.LogDate) END) AS CheckInTime,
            MAX(CASE WHEN D.Direction = 'OUT' THEN TIME(D.LogDate) END) AS CheckOutTime,
            ROUND(TIMESTAMPDIFF(SECOND,
                MIN(CASE WHEN D.Direction = 'IN' THEN D.LogDate END),
                MAX(CASE WHEN D.Direction = 'OUT' THEN D.LogDate END)
            ) / 3600.0, 2) AS HoursWorked
        FROM DeviceLogs_2_2026 D
        JOIN Employees E 
            ON E.EmployeeCodeInDevice = D.UserId
        WHERE D.DeviceId IN (3, 9)
        GROUP BY E.EmployeeCode, E.EmployeeName, D.DeviceId, CAST(D.LogDate AS DATE)
        ORDER BY LogDate DESC, E.EmployeeName ASC
        LIMIT 500
    """
    try:
        with pymysql.connect(**get_essl_conn_params()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

    data = [
        {
            "employee_code": row[0],
            "employee_name": row[1],
            "device_id": row[2],
            "log_date": row[3].strftime("%Y-%m-%d") if row[3] else None,
            "direction": row[4],
            "check_in_time": format_time_for_essl(row[5]),
            "check_out_time": format_time_for_essl(row[6]),
            "hours_worked": float(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]
    return JsonResponse({"logs": data}, status=200)
