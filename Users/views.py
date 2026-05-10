from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.contrib.auth import authenticate

from Attenova.api_utils import parse_json_request
from Users.profile_service import get_profile_bundle, patch_profile_bundle
from Users.auth_utils import create_token, get_user_from_request
from Users.user_payload import user_payload as _user_payload


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(View):
    """POST /api/auth/login/  Body: { email, password }"""

    @method_decorator(require_http_methods(["POST"]))
    def post(self, request):
        body, err = parse_json_request(request)
        if err:
            return err

        email = (body.get("email") or "").strip()
        password = body.get("password")

        if not email or not password:
            return JsonResponse({"error": "email and password required"}, status=400)

        user = authenticate(request, username=email, password=password)
        if user is None:
            return JsonResponse({"error": "Invalid email or password"}, status=401)
        if not user.is_active:
            return JsonResponse({"error": "Account is inactive"}, status=401)

        token = create_token(user)
        return JsonResponse(
            {"token": token, "user": _user_payload(user)},
            status=200,
        )


@method_decorator(csrf_exempt, name="dispatch")
class MeView(View):
    """GET / PATCH /api/auth/me/  Requires Authorization: Bearer <token>"""

    def get(self, request):
        user = get_user_from_request(request)
        if user is None:
            return JsonResponse({"error": "Invalid or expired token"}, status=401)
        return JsonResponse({"user": _user_payload(user)}, status=200)

    @method_decorator(require_http_methods(["PATCH"]))
    def patch(self, request):
        user = get_user_from_request(request)
        if user is None:
            return JsonResponse({"error": "Invalid or expired token"}, status=401)
        body, err = parse_json_request(request)
        if err:
            return err
        if "name" in body:
            user.name = str(body.get("name") or "").strip()[:255]
        if "phone_number" in body:
            user.phone_number = str(body.get("phone_number") or "").strip()[:20]
        if "designation" in body:
            user.designation = str(body.get("designation") or "").strip()[:255]
        user.save()
        return JsonResponse({"user": _user_payload(user)}, status=200)


@method_decorator(csrf_exempt, name="dispatch")
class ChangePasswordView(View):
    """POST /api/auth/me/password/  Body: { old_password, new_password }"""

    @method_decorator(require_http_methods(["POST"]))
    def post(self, request):
        user = get_user_from_request(request)
        if user is None:
            return JsonResponse({"error": "Invalid or expired token"}, status=401)
        body, err = parse_json_request(request)
        if err:
            return err
        old_password = body.get("old_password")
        new_password = body.get("new_password")
        if old_password is None or new_password is None:
            return JsonResponse({"error": "old_password and new_password required"}, status=400)
        if len(str(new_password)) < 8:
            return JsonResponse({"error": "New password must be at least 8 characters"}, status=400)
        if not user.check_password(old_password):
            return JsonResponse({"error": "Current password is incorrect"}, status=400)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        return JsonResponse({"ok": True}, status=200)


@method_decorator(csrf_exempt, name="dispatch")
class ProfileView(View):
    """GET / PATCH /api/auth/me/profile/ — bundled user + linked employee + extended profile."""

    def get(self, request):
        user = get_user_from_request(request)
        if user is None:
            return JsonResponse({"error": "Invalid or expired token"}, status=401)
        return JsonResponse(get_profile_bundle(user), status=200)

    @method_decorator(require_http_methods(["PATCH"]))
    def patch(self, request):
        user = get_user_from_request(request)
        if user is None:
            return JsonResponse({"error": "Invalid or expired token"}, status=401)
        body, err = parse_json_request(request)
        if err:
            return err
        result = patch_profile_bundle(user, body)
        if isinstance(result, JsonResponse):
            return result
        return JsonResponse(result, status=200)
