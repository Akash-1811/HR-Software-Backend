"""
Bundled profile read/update for the authenticated user (My Profile).

Uses linked Employee + EmployeeProfile when present; otherwise only User fields apply.
"""

from __future__ import annotations

from typing import Any, Optional

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.http import JsonResponse

from Employee.models import Employee, EmployeeProfile, EmploymentType, MaritalStatus
from Employee.utils import normalize_gender, parse_date_request
from Users.user_payload import user_payload

MAX_EDUCATION_ENTRIES = 20

_url_validator = URLValidator()


def _optional_url(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    try:
        _url_validator(s)
    except DjangoValidationError as exc:
        raise ValueError("; ".join(exc.messages)) from exc
    return s[:500]


def _linked_employee(user):
    return Employee.objects.filter(user=user).select_related("department", "office", "organization").first()


def _safe_extended(emp: Employee) -> EmployeeProfile:
    prof, _created = EmployeeProfile.objects.get_or_create(employee=emp)
    return prof


def _empty_extended_public() -> dict[str, Any]:
    return {
        "marital_status": "",
        "blood_group": "",
        "nationality": "",
        "alternate_phone": "",
        "emergency_contact_name": "",
        "emergency_contact_phone": "",
        "emergency_contact_relation": "",
        "current_address": "",
        "permanent_address": "",
        "city": "",
        "state": "",
        "country": "",
        "postal_code": "",
        "joining_date": None,
        "employment_type": "",
        "work_location": "",
        "employment_status_note": "",
        "education_entries": [],
        "certifications": "",
        "skills": "",
        "linkedin_url": "",
        "github_url": "",
        "portfolio_url": "",
        "twitter_url": "",
        "reporting_manager": None,
    }


def _serialize_reporting_manager(prof: EmployeeProfile) -> Optional[dict[str, Any]]:
    mgr = prof.reporting_manager
    if mgr is None:
        return None
    return {"name": mgr.name, "emp_code": mgr.emp_code}


def serialize_extended_profile(emp: Employee) -> dict[str, Any]:
    out = _empty_extended_public()
    try:
        prof = emp.extended_profile
    except EmployeeProfile.DoesNotExist:
        return out

    out.update(
        {
            "marital_status": prof.marital_status or "",
            "blood_group": prof.blood_group or "",
            "nationality": prof.nationality or "",
            "alternate_phone": prof.alternate_phone or "",
            "emergency_contact_name": prof.emergency_contact_name or "",
            "emergency_contact_phone": prof.emergency_contact_phone or "",
            "emergency_contact_relation": prof.emergency_contact_relation or "",
            "current_address": prof.current_address or "",
            "permanent_address": prof.permanent_address or "",
            "city": prof.city or "",
            "state": prof.state or "",
            "country": prof.country or "",
            "postal_code": prof.postal_code or "",
            "joining_date": prof.joining_date.isoformat() if prof.joining_date else None,
            "employment_type": prof.employment_type or "",
            "work_location": prof.work_location or "",
            "employment_status_note": prof.employment_status_note or "",
            "education_entries": list(prof.education_entries or []),
            "certifications": prof.certifications or "",
            "skills": prof.skills or "",
            "linkedin_url": prof.linkedin_url or "",
            "github_url": prof.github_url or "",
            "portfolio_url": prof.portfolio_url or "",
            "twitter_url": prof.twitter_url or "",
            "reporting_manager": _serialize_reporting_manager(prof),
        }
    )
    return out


def serialize_employee_row(emp: Employee) -> dict[str, Any]:
    dept = emp.department
    office = emp.office
    pic = emp.profile_pic
    pic_url = pic.url if pic else None
    return {
        "id": emp.id,
        "name": emp.name,
        "email": emp.email or "",
        "phone_number": emp.phone_number or "",
        "emp_code": emp.emp_code,
        "designation": emp.designation or "",
        "designation_label": emp.get_designation_display() if emp.designation else "",
        "gender": emp.gender or "",
        "date_of_birth": emp.date_of_birth.isoformat() if emp.date_of_birth else None,
        "department_id": emp.department_id,
        "department_name": dept.name if dept else "",
        "office_name": office.name if office else "",
        "profile_pic": pic_url,
        "government_id_type": emp.government_id_type or "",
        "government_id_value": emp.government_id_value or "",
    }


def get_profile_bundle(user) -> dict[str, Any]:
    emp = _linked_employee(user)
    return {
        "user": user_payload(user),
        "has_employee_record": emp is not None,
        "employee": serialize_employee_row(emp) if emp else None,
        "extended_profile": serialize_extended_profile(emp) if emp else None,
    }


def _sanitize_education_entries(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise ValueError("education_entries must be an array")
    out: list[dict[str, str]] = []
    for item in raw[:MAX_EDUCATION_ENTRIES]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "institution": str(item.get("institution") or "").strip()[:255],
                "degree": str(item.get("degree") or "").strip()[:255],
                "field_of_study": str(item.get("field_of_study") or "").strip()[:255],
                "start_year": str(item.get("start_year") or "").strip()[:32],
                "end_year": str(item.get("end_year") or "").strip()[:32],
                "grade": str(item.get("grade") or "").strip()[:64],
            }
        )
    return out


def _apply_user_patch(user, section: dict[str, Any]) -> None:
    if "name" in section:
        user.name = str(section.get("name") or "").strip()[:255]
    if "phone_number" in section:
        user.phone_number = str(section.get("phone_number") or "").strip()[:20]
    if "designation" in section:
        user.designation = str(section.get("designation") or "").strip()[:255]
    user.save()


def patch_profile_bundle(user, body: dict[str, Any]) -> dict[str, Any] | JsonResponse:
    try:
        with transaction.atomic():
            user_section = body.get("user")
            if isinstance(user_section, dict):
                _apply_user_patch(user, user_section)

            emp = _linked_employee(user)
            if emp and user_section:
                touch = False
                if "name" in user_section:
                    emp.name = user.name
                    touch = True
                if "phone_number" in user_section:
                    emp.phone_number = user.phone_number
                    touch = True
                if touch:
                    emp.save()

            emp_patch = body.get("employee")
            if emp_patch is not None:
                if not isinstance(emp_patch, dict):
                    return JsonResponse({"error": "employee must be an object"}, status=400)
                if emp is None:
                    return JsonResponse({"error": "No employee record linked to this account"}, status=400)
                if "gender" in emp_patch:
                    emp.gender = normalize_gender(str(emp_patch.get("gender") or "").strip())
                if "date_of_birth" in emp_patch:
                    emp.date_of_birth = parse_date_request(emp_patch.get("date_of_birth"))
                emp.save()

            ext_patch = body.get("extended_profile")
            if ext_patch is not None:
                if not isinstance(ext_patch, dict):
                    return JsonResponse({"error": "extended_profile must be an object"}, status=400)
                if emp is None:
                    return JsonResponse({"error": "No employee record linked to this account"}, status=400)
                prof = _safe_extended(emp)

                if "marital_status" in ext_patch:
                    v = str(ext_patch.get("marital_status") or "").strip().upper()
                    prof.marital_status = v if v in MaritalStatus.values else ""

                for text_key, maxlen in (
                    ("blood_group", 16),
                    ("nationality", 100),
                    ("alternate_phone", 20),
                    ("emergency_contact_name", 255),
                    ("emergency_contact_phone", 20),
                    ("emergency_contact_relation", 64),
                    ("city", 100),
                    ("state", 100),
                    ("country", 100),
                    ("postal_code", 20),
                    ("work_location", 255),
                ):
                    if text_key in ext_patch:
                        setattr(prof, text_key, str(ext_patch.get(text_key) or "").strip()[:maxlen])

                if "current_address" in ext_patch:
                    prof.current_address = str(ext_patch.get("current_address") or "").strip()[:4000]
                if "permanent_address" in ext_patch:
                    prof.permanent_address = str(ext_patch.get("permanent_address") or "").strip()[:4000]
                if "certifications" in ext_patch:
                    prof.certifications = str(ext_patch.get("certifications") or "").strip()[:8000]
                if "skills" in ext_patch:
                    prof.skills = str(ext_patch.get("skills") or "").strip()[:8000]

                if "employment_type" in ext_patch:
                    v = str(ext_patch.get("employment_type") or "").strip().upper()
                    prof.employment_type = v if v in EmploymentType.values else ""

                if "education_entries" in ext_patch:
                    prof.education_entries = _sanitize_education_entries(ext_patch.get("education_entries"))

                url_fields = ("linkedin_url", "github_url", "portfolio_url", "twitter_url")
                if any(k in ext_patch for k in url_fields):
                    try:
                        for k in url_fields:
                            if k in ext_patch:
                                setattr(prof, k, _optional_url(ext_patch.get(k)))
                    except ValueError as exc:
                        return JsonResponse({"error": str(exc)}, status=400)

                prof.updated_by = user
                prof.save()

            user.refresh_from_db()
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return get_profile_bundle(user)
