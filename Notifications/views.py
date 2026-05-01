from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from Attenova.api_utils import pagination_params

from Users.auth_utils import require_auth
from Notifications.models import Notification, DISPLAY_TYPE_MAP


def _notification_payload(n: Notification) -> dict:
    payload = {
        "id": n.id,
        "notification_type": n.notification_type,
        "display_type": DISPLAY_TYPE_MAP.get(n.notification_type, "info"),
        "title": n.title,
        "message": n.message,
        "is_read": n.is_read,
        "content_type": n.content_type_id,
        "object_id": n.object_id,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }
    payload["created_by_id"] = n.created_by_id
    payload["created_by_name"] = (n.created_by.name or n.created_by.email) if getattr(n, "created_by", None) else None
    return payload


@method_decorator([csrf_exempt, require_auth], name="dispatch")
class NotificationView(View):
    """GET /api/notifications/ — list notifications for the current user."""

    def get(self, request):
        qs = Notification.objects.filter(recipient=request.user).select_related("created_by")

        is_read = request.GET.get("is_read")
        if is_read is not None:
            qs = qs.filter(is_read=is_read.lower() in ("true", "1", "yes"))

        ntype = request.GET.get("notification_type")
        if ntype:
            qs = qs.filter(notification_type=ntype)

        page, page_size, start = pagination_params(request.GET)

        total = qs.count()
        notifications = list(qs[start : start + page_size])

        return JsonResponse(
            {
                "notifications": [_notification_payload(n) for n in notifications],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )


@csrf_exempt
@require_auth
@require_http_methods(["GET"])
def unread_count(request):
    """GET /api/notifications/unread-count/"""
    count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({"unread_count": count})


@csrf_exempt
@require_auth
@require_http_methods(["PATCH"])
def mark_read(request, pk):
    """PATCH /api/notifications/<id>/read/"""
    try:
        notif = Notification.objects.get(pk=pk, recipient=request.user)
    except Notification.DoesNotExist:
        return JsonResponse({"error": "Notification not found"}, status=404)

    notif.is_read = True
    notif.save(update_fields=["is_read"])
    return JsonResponse({"notification": _notification_payload(notif)})


@csrf_exempt
@require_auth
@require_http_methods(["PATCH"])
def mark_all_read(request):
    """PATCH /api/notifications/read-all/"""
    updated = Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return JsonResponse({"updated": updated})
