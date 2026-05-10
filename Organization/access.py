"""
Office / org access control and scoped querysets.

Single source of truth for superuser checks, who may access an office,
and which offices appear in list APIs. Import from here — not from views.
"""

from Organization.models import Office
from Users.models import UserRole


def is_superadmin(user) -> bool:
    return bool(getattr(user, "is_superuser", False))


def user_can_access_office(user, office) -> bool:
    """
    True if the user may view/edit this office.

    Requires organization alignment for all non-superusers (including office
    managers) so M2M manager membership cannot cross tenant boundaries.
    """
    if is_superadmin(user):
        return True
    if not office or not getattr(user, "organization_id", None):
        return False
    if user.organization_id != office.organization_id:
        return False
    if user.role == UserRole.ORG_ADMIN:
        return True
    if user.role in (UserRole.OFFICE_ADMIN, UserRole.SUPERVISOR):
        return getattr(user, "office_id", None) == office.pk
    if user.role == UserRole.OFFICE_MANAGER:
        return office.managers.filter(pk=user.id).exists()
    return False


def get_offices_queryset(user):
    """
    Offices the user can access for list/detail resolution.
    Org Admin: all in org. Office Admin / Supervisor: their office.
    Office Manager: offices they manage (same organization only).
    """
    base = Office.objects.select_related("organization").prefetch_related("managers")
    if is_superadmin(user):
        return base.filter(organization__is_active=True)
    if user.role == UserRole.OFFICE_MANAGER:
        qs = base.filter(managers=user)
        if getattr(user, "organization_id", None):
            qs = qs.filter(organization_id=user.organization_id)
        return qs
    if user.organization_id:
        if user.role in (
            UserRole.OFFICE_ADMIN,
            UserRole.SUPERVISOR,
        ):
            if not getattr(user, "office_id", None):
                return base.none()
            return base.filter(pk=user.office_id, organization_id=user.organization_id)
        return base.filter(organization_id=user.organization_id)
    return base.none()
