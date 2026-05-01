"""Shared constants for the Reports app."""

from Biometric.constants import BIOMETRIC_DIRECTION_IN, BIOMETRIC_DIRECTION_OUT

# --- Wide matrix CSV (email / vendor-style export) ------------------------------------

MAX_MATRIX_DATE_RANGE_DAYS = 93

# Re-export biometric direction sets for callers that import from Reports.
__all__ = [
    "MAX_MATRIX_DATE_RANGE_DAYS",
    "BIOMETRIC_DIRECTION_IN",
    "BIOMETRIC_DIRECTION_OUT",
    "MATRIX_PRESENTISH_STATUSES",
]

# Status codes that count toward the matrix "Total" column on the punch-type row.
MATRIX_PRESENTISH_STATUSES = frozenset({"P", "L"})
