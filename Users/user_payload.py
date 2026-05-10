"""Serialize User for auth/session/profile API responses."""


def user_payload(user):
    last_login = None
    raw_login = getattr(user, "last_login", None)
    if raw_login is not None:
        try:
            last_login = raw_login.isoformat()
        except Exception:
            last_login = None
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name or "",
        "role": user.role,
        "organization_id": user.organization_id,
        "is_superadmin": getattr(user, "is_superuser", False),
        "phone_number": getattr(user, "phone_number", "") or "",
        "designation": getattr(user, "designation", "") or "",
        "emp_code": getattr(user, "emp_code", "") or "",
        "last_login": last_login,
        "active_sessions_supported": False,
    }
