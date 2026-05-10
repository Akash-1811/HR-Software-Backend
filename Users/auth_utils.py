"""JWT utilities for token-based auth."""

import jwt
from functools import wraps
from django.conf import settings
from django.utils import timezone

from Users.models import User

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # 7 days


def create_token(user):
    payload = {
        "user_id": user.id,
        "email": user.email,
        "exp": timezone.now() + timezone.timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_user_from_request(request):
    """Extract user from Authorization: Bearer <token> header. Returns User or None."""
    auth = request.META.get("HTTP_AUTHORIZATION") or ""
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp"]},
        )
        return User.objects.get(pk=payload["user_id"], is_active=True)
    except (jwt.InvalidTokenError, User.DoesNotExist, KeyError):
        return None


def require_auth(view_func):
    """Decorator: set request.user from Bearer token; return 401 if invalid."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        request.user = get_user_from_request(request)
        if request.user is None:
            from django.http import JsonResponse

            return JsonResponse({"error": "Invalid or expired token"}, status=401)
        return view_func(request, *args, **kwargs)

    return wrapper
