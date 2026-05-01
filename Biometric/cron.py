"""
Scheduled entry point for django-crontab: pull new rows from ESSL DeviceLogs into
DummyEsslBiometricAttendanceData, then run BiometricAttendanceProcessor for a recent window.

Configured in settings.CRONJOBS as:
    ("*/5 * * * *", "Biometric.cron.run_attendance_sync")
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pymysql
from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from Attendance.processing import BiometricAttendanceProcessor
from Biometric.models import DummyEsslBiometricAttendanceData
from Biometric.utils import get_essl_conn_params

logger = logging.getLogger(__name__)

# How many calendar days back to reprocess (covers late-arriving device logs).
PROCESS_LOOKBACK_DAYS = 7


def _row_to_dummy(row: tuple) -> DummyEsslBiometricAttendanceData:
    """Map a SELECT row to DummyEsslBiometricAttendanceData (14 columns)."""
    return DummyEsslBiometricAttendanceData(
        DeviceLogId=row[0],
        DownloadDate=row[1],
        DeviceId=(row[2] or "")[:128],
        UserId=(row[3] or "")[:64],
        LogDate=row[4],
        Direction=(row[5] or "")[:32],
        AttDirection=(row[6] or "")[:32],
        C1=(row[7] or "")[:64],
        C2=(row[8] or "")[:64],
        C3=(row[9] or "")[:64],
        C4=(row[10] or "")[:64],
        C5=(row[11] or "")[:64],
        C6=(row[12] or "")[:64],
        C7=(row[13] or "")[:64],
        WorkCode=(row[14] or "")[:64],
        hrapp_syncstatus=(row[15] or "")[:32],
    )


def sync_essl_device_logs_to_dummy() -> int:
    """
    Copy new rows from ESSL MySQL DeviceLogs table into DummyEsslBiometricAttendanceData.

    Uses MAX(DeviceLogId) already stored in the app DB as a cursor. Respects
    ESSL_DEVICE_LOGS_TABLE, ESSL_SYNC_BATCH_SIZE, ESSL_SYNC_MAX_BATCHES from settings.

    Returns the number of rows attempted for insert (including duplicates ignored).
    """
    if "essl_db" not in settings.DATABASES:
        logger.debug("essl_db not configured; skipping ESSL pull.")
        return 0

    table = getattr(settings, "ESSL_DEVICE_LOGS_TABLE", "DeviceLogs_2_2026")
    batch_size = getattr(settings, "ESSL_SYNC_BATCH_SIZE", 5000)
    max_batches = getattr(settings, "ESSL_SYNC_MAX_BATCHES", 20)

    max_id = DummyEsslBiometricAttendanceData.objects.aggregate(m=Max("DeviceLogId"))["m"]
    if max_id is None:
        max_id = 0

    params = get_essl_conn_params()
    if not params.get("database"):
        logger.warning("ESSL database name missing; skipping ESSL pull.")
        return 0

    # Column list must match ESSL DeviceLogs-style tables used with this project.
    sql = f"""
        SELECT DeviceLogId, DownloadDate, DeviceId, UserId, LogDate, Direction, AttDirection,
               C1, C2, C3, C4, C5, C6, C7, WorkCode, hrapp_syncstatus
        FROM `{table}`
        WHERE DeviceLogId > %s
        ORDER BY DeviceLogId ASC
        LIMIT %s
    """

    total = 0
    for _ in range(max_batches):
        with pymysql.connect(**params) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (max_id, batch_size))
                rows = cursor.fetchall()

        if not rows:
            break

        records = [_row_to_dummy(r) for r in rows]
        DummyEsslBiometricAttendanceData.objects.bulk_create(records, ignore_conflicts=True)
        total += len(records)
        max_id = rows[-1][0]

    if total:
        logger.info("ESSL sync: pulled %s row(s) into DummyEsslBiometricAttendanceData.", total)
    return total


def run_attendance_sync() -> None:
    """
    Cron hook: optional ESSL -> dummy staging, then biometric -> Attendance processing.

    Safe to run when ESSL is not configured (only processes existing dummy rows).
    """
    try:
        sync_essl_device_logs_to_dummy()
    except Exception:
        logger.exception("ESSL sync step failed; continuing with attendance processing.")

    today = timezone.localdate()
    start = today - timedelta(days=PROCESS_LOOKBACK_DAYS)
    processor = BiometricAttendanceProcessor()
    try:
        stats = processor.process(from_date=start, to_date=today)
        logger.info("Attendance processing: %s", stats)
    except Exception:
        logger.exception("BiometricAttendanceProcessor failed.")
        raise
