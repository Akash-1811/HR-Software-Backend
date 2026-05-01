"""
Shared JSON API helpers (parsing, pagination).
"""

import json
from typing import Any, Optional

from django.http import HttpRequest, JsonResponse


def parse_json_request(
    request: HttpRequest,
    *,
    object_required: bool = True,
) -> tuple[Optional[dict], Optional[JsonResponse]]:
    """
    Parse request body as JSON object.

    Returns (data, None) on success, or (None, JsonResponse error) on failure.
    Empty body yields {}.
    """
    if not request.body:
        return {}, None
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "Invalid JSON"}, status=400)
    if object_required and not isinstance(body, dict):
        return None, JsonResponse({"error": "JSON object expected"}, status=400)
    return body, None


def pagination_params(
    query_params,
    *,
    default_page_size: int = 20,
    max_page_size: int = 100,
) -> tuple[int, int, int]:
    """
    Read page / page_size from a QueryDict-like mapping.

    Returns (page_1_based, page_size_clamped, start_offset).
    """
    try:
        page = max(int(query_params.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(
            max(int(query_params.get("page_size", default_page_size)), 1),
            max_page_size,
        )
    except (TypeError, ValueError):
        page_size = default_page_size
    start = (page - 1) * page_size
    return page, page_size, start


def parse_int_optional(value: Any) -> Optional[int]:
    """Parse int from query/header value; invalid → None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_iso_date(value: Any):
    """Parse YYYY-MM-DD from str or passthrough date; invalid → None."""
    from datetime import date, datetime

    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
