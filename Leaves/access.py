"""Tenant isolation and RBAC for Leave APIs."""

from Employee.models import Employee
from Employee.utils import get_employees_queryset, user_can_access_employee
from Organization.access import is_superadmin
from Organization.models import Office
from Users.models import UserRole

from Leaves.models import LeaveApplication, LeaveType


def user_can_manage_leave_types(user) -> bool:
    """Leave catalog per office: Org Admin (any office in org) and Office Admin."""
    if is_superadmin(user):
        return True
    return bool(user.organization_id) and user.role in (
        UserRole.ORG_ADMIN,
        UserRole.OFFICE_ADMIN,
    )


def user_can_assign_leave_balances(user) -> bool:
    if is_superadmin(user):
        return True
    return bool(user.organization_id) and user.role in (
        UserRole.ORG_ADMIN,
        UserRole.OFFICE_ADMIN,
        UserRole.OFFICE_MANAGER,
    )


def user_can_review_leave_applications(user) -> bool:
    if is_superadmin(user):
        return True
    return bool(user.organization_id) and user.role in (
        UserRole.ORG_ADMIN,
        UserRole.OFFICE_ADMIN,
        UserRole.OFFICE_MANAGER,
        UserRole.SUPERVISOR,
    )


def user_can_submit_leave_application(user, employee: Employee) -> bool:
    """Apply for own linked profile or on behalf of any employee the user may access under Employee RBAC."""
    if is_superadmin(user):
        return True
    return user_can_access_employee(user, employee)


def get_accessible_leave_types_queryset(user):
    qs = LeaveType.objects.select_related("office", "office__organization")
    if is_superadmin(user):
        return qs.filter(office__organization__is_active=True, office__is_active=True)

    if user.organization_id and user.role == UserRole.ORG_ADMIN:
        return qs.filter(office__organization_id=user.organization_id)

    if user.role in (UserRole.OFFICE_ADMIN, UserRole.SUPERVISOR):
        if not getattr(user, "office_id", None):
            return qs.none()
        return qs.filter(office_id=user.office_id)

    if user.role == UserRole.OFFICE_MANAGER:
        managed = Office.objects.filter(managers=user)
        if getattr(user, "organization_id", None):
            managed = managed.filter(organization_id=user.organization_id)
        return qs.filter(office_id__in=managed.values_list("pk", flat=True))

    emp_oid = Employee.objects.filter(user=user).values_list("office_id", flat=True).first()
    if emp_oid:
        return qs.filter(office_id=emp_oid)

    return qs.none()


def get_subject_employee(user, requested_employee_id: int | None):
    """
    Resolve target employee for apply/balance/history.
    - None → linked Employee for user if any.
    - Explicit id → allowed only if user_can_access_employee.
    """
    if requested_employee_id is None:
        emp = Employee.objects.filter(user=user).select_related("organization", "office", "shift").first()
        return emp
    emp = Employee.objects.filter(pk=requested_employee_id).select_related("organization", "office", "shift").first()
    if not emp or not user_can_access_employee(user, emp):
        return None
    return emp


def get_leave_applications_queryset(user):
    """Applications visible to this user (employee self-service vs manager/admin scope)."""
    base = LeaveApplication.objects.select_related(
        "employee",
        "employee__office",
        "employee__organization",
        "leave_type",
        "applied_by",
        "reviewed_by",
    )
    if is_superadmin(user):
        return base

    if user.organization_id and user.role == UserRole.ORG_ADMIN:
        return base.filter(employee__organization_id=user.organization_id)

    if user.role in (UserRole.OFFICE_ADMIN, UserRole.SUPERVISOR):
        if not getattr(user, "office_id", None):
            return base.none()
        return base.filter(
            employee__organization_id=user.organization_id,
            employee__office_id=user.office_id,
        )

    if user.role == UserRole.OFFICE_MANAGER:
        qs = get_employees_queryset(user)
        emp_ids = qs.values_list("pk", flat=True)
        return base.filter(employee_id__in=emp_ids)

    emp = Employee.objects.filter(user=user).values_list("pk", flat=True).first()
    if emp:
        return base.filter(employee_id=emp)

    return base.none()


def user_can_review_application(user, application: LeaveApplication) -> bool:
    if not user_can_review_leave_applications(user):
        return False
    return user_can_access_employee(user, application.employee)


def leave_type_belongs_to_employee_office(leave_type: LeaveType, employee: Employee) -> bool:
    return leave_type.office_id == employee.office_id


def resolve_leave_context_office_id(user, linked_employee: Employee | None) -> int | None:
    """Office used for leave catalog context (linked employee office wins over user.office_id)."""
    if linked_employee is not None and linked_employee.office_id:
        return linked_employee.office_id
    oid = getattr(user, "office_id", None)
    return oid if oid else None
