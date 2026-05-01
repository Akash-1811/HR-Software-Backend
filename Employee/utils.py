"""
Helper functions for the Employee app.
"""

from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from Attenova.api_utils import parse_iso_date
from Organization.access import is_superadmin, user_can_access_office
from Organization.models import Office
from Users.models import UserRole

from Employee.constants import (
    ALLOWED_CREATE_DESIGNATIONS,
    DESIGNATION_CHOICES_LIST,
    DESIGNATION_ORDER,
    MIN_AGE_YEARS,
    ROLE_MIN_DESIGNATION_INDEX,
)
from Employee.models import Employee, Gender

# Valid gender codes for request parsing (single source of truth)
GENDER_VALID = set(Gender.values)


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Parse value to int; return default on failure. Handles str and int."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_date_request(value: Any) -> Optional[date]:
    """Parse date from JSON/request body (str YYYY-MM-DD or date-like). Returns None if invalid."""
    return parse_iso_date(value)


def normalize_gender(value: str) -> str:
    """Return value if it is in GENDER_VALID, else empty string."""
    return value if (value and value in GENDER_VALID) else ""


def user_can_see_organization(user, org_id: int) -> bool:
    """True if user is allowed to filter/list by this organization_id."""
    if is_superadmin(user):
        return True
    return getattr(user, "organization_id", None) == org_id


def apply_list_filters(queryset, user, query_params: dict):
    """
    Apply organization_id and office_id from query params.
    - Manager, Office Admin, Supervisor: always see only their office(s); request office_id is ignored.
    - Org Admin: see data based on office_id when provided; when not provided, see all org.
    """
    org_id = safe_int(query_params.get("organization_id"))
    if org_id is not None:
        if user_can_see_organization(user, org_id):
            queryset = queryset.filter(organization_id=org_id)
        else:
            queryset = queryset.none()

    office_id = safe_int(query_params.get("office_id"))
    # Manager, Office Admin, Supervisor: only their office(s); do not use request office_id
    if user.role in (UserRole.OFFICE_MANAGER, UserRole.OFFICE_ADMIN, UserRole.SUPERVISOR):
        if user.role == UserRole.OFFICE_MANAGER:
            # Base queryset is already restricted to their offices; optional office_id to narrow to one office
            if office_id is not None:
                queryset = queryset.filter(office_id=office_id)
        else:
            # Office Admin / Supervisor: only their office (user.office_id required)
            if getattr(user, "office_id", None):
                queryset = queryset.filter(office_id=user.office_id)
            else:
                queryset = queryset.none()
    else:
        # Org Admin (and superadmin): filter by office_id when provided
        if office_id is not None:
            queryset = queryset.filter(office_id=office_id)

    return queryset


def office_belongs_to_organization(office, org_id: int) -> bool:
    """True if office exists and belongs to the given organization."""
    return office is not None and office.organization_id == org_id


def user_can_create_employees(user) -> bool:
    """True if this user's role is allowed to create employees."""
    if is_superadmin(user):
        return True
    return getattr(user, "role", None) in ROLE_MIN_DESIGNATION_INDEX


def allowed_designation_index_for_user(user) -> Optional[int]:
    """Minimum designation index (0=highest) this user can assign. None if cannot create."""
    if is_superadmin(user):
        return 0
    return ROLE_MIN_DESIGNATION_INDEX.get(getattr(user, "role", None))


def designation_index(designation_value: str) -> Optional[int]:
    """Index in DESIGNATION_ORDER, or None if invalid."""
    if not designation_value:
        return None
    try:
        return DESIGNATION_ORDER.index(designation_value)
    except ValueError:
        return None


def user_can_assign_designation(user, designation_value: str) -> bool:
    """True if user can create/assign an employee with this designation."""
    min_idx = allowed_designation_index_for_user(user)
    if min_idx is None:
        return False
    idx = designation_index(designation_value)
    if idx is None:
        return True  # invalid designation will fail validation elsewhere
    return idx >= min_idx


def allowed_designations_for_user(user) -> list:
    """List of { value, label } this user can assign when creating/editing an employee."""
    min_idx = allowed_designation_index_for_user(user)
    if min_idx is None:
        return []
    return [DESIGNATION_CHOICES_LIST[i] for i in range(min_idx, len(DESIGNATION_CHOICES_LIST))]


def employee_payload(emp) -> dict:
    """Build API payload dict for an employee."""
    dept = getattr(emp, "department", None)
    return {
        "id": emp.id,
        "organization_id": emp.organization_id,
        "office_id": emp.office_id,
        "shift_id": emp.shift_id,
        "department_id": emp.department_id,
        "department_name": dept.name if dept else "",
        "emp_code": emp.emp_code,
        "name": emp.name,
        "designation": emp.designation or "",
        "gender": emp.gender or "",
        "date_of_birth": emp.date_of_birth.isoformat() if emp.date_of_birth else None,
        "email": emp.email or "",
        "phone_number": emp.phone_number or "",
        "government_id_type": emp.government_id_type or "",
        "government_id_value": emp.government_id_value or "",
        "profile_pic": emp.profile_pic.url if emp.profile_pic else None,
        "is_active": emp.is_active,
        "created_at": emp.created_at.isoformat() if emp.created_at else None,
    }


def user_can_access_employee(user, emp) -> bool:
    """Manager/Office Admin/Supervisor: only employees in their office. Org Admin: any employee in org."""
    if is_superadmin(user):
        return True
    if user.organization_id != emp.organization_id:
        return False
    if user.role == UserRole.ORG_ADMIN:
        return True
    if user.role in (UserRole.OFFICE_ADMIN, UserRole.SUPERVISOR):
        return getattr(user, "office_id", None) == emp.office_id
    if user.role == UserRole.OFFICE_MANAGER and emp.office_id:
        office = Office.objects.filter(pk=emp.office_id).prefetch_related("managers").first()
        if office and office.managers.filter(pk=user.id).exists():
            return True
    return False


def get_employees_queryset(user):
    """Manager/Office Admin/Supervisor: only their office(s). Org Admin: all in org (then apply_list_filters can narrow by office_id)."""
    if is_superadmin(user):
        return Employee.objects.select_related("organization", "office", "department", "shift")
    if user.role == UserRole.ORG_ADMIN and user.organization_id:
        return Employee.objects.filter(organization_id=user.organization_id).select_related(
            "organization", "office", "department", "shift"
        )
    if user.role in (UserRole.OFFICE_ADMIN, UserRole.SUPERVISOR):
        if not getattr(user, "office_id", None) or not user.organization_id:
            return Employee.objects.none()
        return Employee.objects.filter(
            organization_id=user.organization_id,
            office_id=user.office_id,
        ).select_related("organization", "office", "department", "shift")
    if user.role == UserRole.OFFICE_MANAGER:
        return Employee.objects.filter(office__managers=user).select_related(
            "organization", "office", "department", "shift"
        )
    return Employee.objects.none()


# --- Import/export helpers ---


def normalize_df_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names: lowercase, spaces/hyphens to underscore."""
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
    return df


def parse_dob(value: Any) -> Optional[date]:
    """Parse date of birth from string or pandas value. Returns None if invalid."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        try:
            return pd.to_datetime(value).date()
        except Exception:
            return None


def age_years(d: Optional[date]) -> Optional[int]:
    """Age in years as of today. None if d is None."""
    if d is None:
        return None
    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def validate_and_prepare_import_row(
    row_series: pd.Series,
    row_index: int,
    existing_emp_codes: set,
    existing_emails: set,
    existing_phones: set,
    existing_govt_ids: set,
    seen_emp_codes: set,
    seen_emails: set,
    seen_phones: set,
    seen_govt_ids: set,
    org_id: int,
    office_id: int,
    user,
) -> tuple:
    """
    Validate one row and prepare employee data if valid.
    Returns (row_index, employee_dict_or_None, error_message_or_None).
    """

    def str_val(key: str) -> str:
        v = row_series.get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v).strip()

    emp_code = str_val("emp_code")
    name = str_val("name")

    if not emp_code or not name:
        return (
            row_index,
            None,
            f"Row {row_index}: Required fields emp_code and name must be filled",
        )

    if emp_code in existing_emp_codes:
        return (
            row_index,
            None,
            f"Row {row_index}: Duplicate emp_code '{emp_code}' already exists in organization",
        )

    if emp_code in seen_emp_codes:
        return (row_index, None, f"Row {row_index}: Duplicate emp_code '{emp_code}' in file")
    seen_emp_codes.add(emp_code)

    email = str_val("email")
    phone = str_val("phone_number")
    govt_id_value = str_val("government_id_value")

    if email:
        if email in existing_emails:
            return (row_index, None, f"Row {row_index}: Duplicate email already exists")
        if email in seen_emails:
            return (row_index, None, f"Row {row_index}: Duplicate email in file")
        seen_emails.add(email)

    if phone:
        if phone in existing_phones:
            return (row_index, None, f"Row {row_index}: Duplicate phone number already exists")
        if phone in seen_phones:
            return (row_index, None, f"Row {row_index}: Duplicate phone number in file")
        seen_phones.add(phone)

    if govt_id_value:
        if govt_id_value in existing_govt_ids:
            return (row_index, None, f"Row {row_index}: Duplicate government ID already exists")
        if govt_id_value in seen_govt_ids:
            return (row_index, None, f"Row {row_index}: Duplicate government ID in file")
        seen_govt_ids.add(govt_id_value)

    govt_id_type_raw = str_val("government_id_type")
    govt_id_type = ""
    if govt_id_type_raw:
        n = govt_id_type_raw.lower().replace(" ", "").replace("_", "").replace("-", "")
        if n in ("license", "drivinglicense"):
            govt_id_type = "License"
        elif n in ("pancard", "pan"):
            govt_id_type = "PanCard"
        elif n in ("aadhaarcard", "aadhaar", "adhaar"):
            govt_id_type = "AadhaarCard"
        elif n in ("voterid", "voter"):
            govt_id_type = "VoterID"
        else:
            return (
                row_index,
                None,
                "Row {}: government_id_type must be one of License, PanCard, AadhaarCard, VoterID".format(row_index),
            )

    dob_raw = row_series.get("date_of_birth")
    date_of_birth = parse_dob(dob_raw)
    if date_of_birth is not None:
        age = age_years(date_of_birth)
        if age is not None and age < MIN_AGE_YEARS:
            return (
                row_index,
                None,
                f"Row {row_index}: Age must be 18 or above (DOB {date_of_birth})",
            )

    designation_raw = str_val("designation")
    designation = ""
    if designation_raw:
        raw_upper = designation_raw.strip().upper().replace(" ", "_")
        if raw_upper in ("EMPLOYEE", "STAFF"):
            designation = "EMPLOYEE"
        elif raw_upper in ("SUPPORT_STAFF", "SUPPORTSTAFF"):
            designation = "SUPPORT_STAFF"
        elif designation_raw.strip() in ALLOWED_CREATE_DESIGNATIONS:
            designation = designation_raw.strip()
        else:
            return (
                row_index,
                None,
                "Row {}: designation must be one of EMPLOYEE (Staff), SUPPORT_STAFF (Support Staff) (invalid: '{}')".format(
                    row_index, designation_raw[:50]
                ),
            )
    if designation and not user_can_assign_designation(user, designation):
        return (row_index, None, f"Row {row_index}: Designation not allowed")
    if not designation:
        designation = "EMPLOYEE"

    gender = normalize_gender(str_val("gender"))

    data = {
        "organization_id": org_id,
        "office_id": office_id,
        "emp_code": emp_code,
        "name": name,
        "designation": designation,
        "gender": gender,
        "date_of_birth": date_of_birth,
        "email": email,
        "phone_number": phone,
        "government_id_type": govt_id_type,
        "government_id_value": govt_id_value,
    }
    return (row_index, data, None)
