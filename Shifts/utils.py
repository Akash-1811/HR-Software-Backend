"""Shift API helpers: serialization, parsing, and PATCH application."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse

from Organization.access import get_offices_queryset
from Shifts.models import Shift


def serialize_shift(shift: Shift) -> dict:
    mwh = shift.min_working_hours
    return {
        "id": shift.id,
        "office_id": shift.office_id,
        "name": shift.name,
        "start_time": shift.start_time.strftime("%H:%M") if shift.start_time else None,
        "end_time": shift.end_time.strftime("%H:%M") if shift.end_time else None,
        "grace_minutes": shift.grace_minutes,
        "weekoff_days": list(shift.weekoff_days or []),
        "min_working_hours": float(mwh) if mwh is not None else None,
        "lunch_break_minutes": shift.lunch_break_minutes,
        "tea_break_minutes": shift.tea_break_minutes,
        "lunch_break_paid": shift.lunch_break_paid,
        "tea_breaks_paid": shift.tea_breaks_paid,
        "is_night_shift": shift.is_night_shift,
        "is_active": shift.is_active,
        "is_default": shift.is_default,
        "created_at": shift.created_at.isoformat() if shift.created_at else None,
    }


def get_accessible_shifts_queryset(user):
    offices = get_offices_queryset(user)
    return Shift.objects.filter(office__in=offices).select_related("office")


def parse_weekoff_days(raw):
    """Accept JSON list of ints 0–6 (Mon–Sun)."""
    if raw is None or not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for x in raw:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= i <= 6 and i not in seen:
            seen.add(i)
            out.append(i)
    return sorted(out)


def parse_min_working_hours(raw):
    if raw is None or raw == "":
        return None
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d.quantize(Decimal("0.01"))


def parse_shift_time(value):
    """Parse 'HH:MM' or 'HH:MM:SS' string to time. Returns None on failure."""
    if value is None:
        return None
    if hasattr(value, "hour"):
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


_INT_NONNEG_KEYS = (
    ("grace_minutes", "grace_minutes"),
    ("lunch_break_minutes", "lunch_break_minutes"),
    ("tea_break_minutes", "tea_break_minutes"),
)

_BOOL_KEYS = (
    ("lunch_break_paid", "lunch_break_paid"),
    ("tea_breaks_paid", "tea_breaks_paid"),
    ("is_night_shift", "is_night_shift"),
    ("is_active", "is_active"),
    ("is_default", "is_default"),
)


def apply_shift_patch(shift: Shift, body: dict) -> tuple[list[str], JsonResponse | None]:
    """
    Apply recognized PATCH keys to `shift`. Returns (model field names touched, error response or None).
    """
    updated: list[str] = []

    if "name" in body:
        v = (body.get("name") or "").strip()
        if v:
            shift.name = v
            updated.append("name")

    for json_key, attr in (("start_time", "start_time"), ("end_time", "end_time")):
        if json_key not in body:
            continue
        t = parse_shift_time(body.get(json_key))
        if t is not None:
            setattr(shift, attr, t)
            updated.append(attr)

    for json_key, attr in _INT_NONNEG_KEYS:
        if json_key not in body:
            continue
        setattr(shift, attr, max(0, int(body.get(json_key) or 0)))
        updated.append(attr)

    if "weekoff_days" in body:
        shift.weekoff_days = parse_weekoff_days(body.get("weekoff_days"))
        updated.append("weekoff_days")

    if "min_working_hours" in body:
        raw = body.get("min_working_hours")
        if raw is None or raw == "":
            shift.min_working_hours = None
        else:
            mwh = parse_min_working_hours(raw)
            if mwh is None:
                return updated, JsonResponse(
                    {"error": "min_working_hours must be a number between 0 and 24"},
                    status=400,
                )
            shift.min_working_hours = mwh
        updated.append("min_working_hours")

    for json_key, attr in _BOOL_KEYS:
        if json_key not in body:
            continue
        setattr(shift, attr, bool(body[json_key]))
        updated.append(attr)

    return updated, None
