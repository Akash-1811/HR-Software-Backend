"""
Process raw biometric data (DummyEsslBiometricAttendanceData) into Attendance records.

Usage:
    from Attendance.processing import BiometricAttendanceProcessor

    processor = BiometricAttendanceProcessor()
    stats = processor.process(from_date=..., to_date=...)
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterator

from django.db import transaction
from django.utils import timezone

from Attendance.models import (
    Attendance,
    AttendancePunch,
    AttendanceSource,
    AttendanceStatus,
)
from Biometric.constants import BIOMETRIC_DIRECTION_IN, BIOMETRIC_DIRECTION_OUT
from Biometric.models import DummyEsslBiometricAttendanceData
from Employee.models import Employee
from Shifts.models import Shift


DIR_IN = BIOMETRIC_DIRECTION_IN
DIR_OUT = BIOMETRIC_DIRECTION_OUT


@dataclass
class ProcessStats:
    """Statistics from a processing run."""

    processed: int = 0
    created: int = 0
    updated: int = 0
    punches_synced: int = 0
    skipped_no_employee: int = 0
    skipped_no_punches: int = 0

    def __str__(self) -> str:
        return (
            f"Processed {self.processed} employee-days: "
            f"{self.created} created, {self.updated} updated, "
            f"{self.punches_synced} punches synced. "
            f"Skipped: {self.skipped_no_employee} (no employee), "
            f"{self.skipped_no_punches} (no punches)."
        )


class BiometricAttendanceProcessor:
    """
    Fetches raw data from DummyEsslBiometricAttendanceData, groups by employee+date,
    computes first_in/last_out/working_hours/late_minutes/early_out_minutes/status,
    and persists Attendance + AttendancePunch records.
    """

    def process(
        self,
        from_date=None,
        to_date=None,
    ) -> ProcessStats:
        """
        Process raw biometric data for the given date range.
        If no range given, processes all available raw records.
        """
        stats = ProcessStats()
        qs = self._raw_queryset(from_date, to_date)
        grouped = self._group_by_employee_date(qs)

        employee_cache: dict[str, Employee] = {}

        with transaction.atomic():
            for (emp_code, att_date), punches in grouped:
                emp = self._resolve_employee(emp_code, employee_cache)
                if not emp:
                    stats.skipped_no_employee += 1
                    continue

                in_punches = [p for p in punches if self._is_in(p)]
                out_punches = [p for p in punches if self._is_out(p)]
                if not in_punches or not out_punches:
                    stats.skipped_no_punches += 1
                    continue

                first_in = min(p["log_date"] for p in in_punches)
                last_out = max(p["log_date"] for p in out_punches)
                working_hours = Decimal(str(round((last_out - first_in).total_seconds() / 3600, 2)))

                shift = self._resolve_shift(emp)
                late_minutes = self._compute_late_minutes(first_in, shift)
                early_out_minutes = self._compute_early_out_minutes(last_out, shift)
                status = AttendanceStatus.L if late_minutes > 0 else AttendanceStatus.P

                attendance, created = Attendance.objects.get_or_create(
                    employee=emp,
                    date=att_date,
                    defaults={
                        "office": emp.office,
                        "shift": shift,
                        "first_in": first_in,
                        "last_out": last_out,
                        "working_hours": working_hours,
                        "late_minutes": late_minutes,
                        "early_out_minutes": early_out_minutes,
                        "status": status,
                        "source": AttendanceSource.BIOMETRIC,
                    },
                )

                if created:
                    stats.created += 1
                else:
                    stats.updated += 1
                    if not attendance.is_regularized:
                        self._update_attendance_computed(
                            attendance,
                            first_in=first_in,
                            last_out=last_out,
                            working_hours=working_hours,
                            late_minutes=late_minutes,
                            early_out_minutes=early_out_minutes,
                            status=status,
                        )

                synced = self._sync_punches(attendance, punches)
                stats.punches_synced += synced
                stats.processed += 1

        return stats

    def _raw_queryset(self, from_date, to_date):
        qs = DummyEsslBiometricAttendanceData.objects.filter(
            LogDate__isnull=False,
            UserId__isnull=False,
        ).exclude(UserId="")

        if from_date:
            qs = qs.filter(LogDate__date__gte=from_date)
        if to_date:
            qs = qs.filter(LogDate__date__lte=to_date)

        return qs.values("UserId", "LogDate", "Direction")

    def _group_by_employee_date(self, qs) -> Iterator[tuple[tuple[str, any], list[dict]]]:
        groups: dict[tuple[str, any], list[dict]] = defaultdict(list)
        for row in qs:
            log_date = row["LogDate"]
            user_id = (row["UserId"] or "").strip()
            if not user_id or not log_date:
                continue
            date_only = log_date.date() if hasattr(log_date, "date") else log_date
            groups[(user_id, date_only)].append(
                {
                    "log_date": log_date,
                    "direction": (row["Direction"] or "").strip().lower(),
                }
            )

        for key in sorted(groups.keys()):
            yield key, groups[key]

    def _resolve_employee(self, emp_code: str, cache: dict) -> Employee | None:
        if emp_code not in cache:
            cache[emp_code] = (
                Employee.objects.filter(
                    emp_code=emp_code,
                    is_active=True,
                )
                .select_related("office", "shift")
                .first()
            )
        return cache[emp_code]

    def _resolve_shift(self, emp: Employee) -> Shift | None:
        if emp.shift_id:
            return emp.shift
        return Shift.objects.filter(
            office=emp.office,
            is_default=True,
            is_active=True,
        ).first()

    def _is_in(self, punch: dict) -> bool:
        return punch["direction"] in DIR_IN

    def _is_out(self, punch: dict) -> bool:
        return punch["direction"] in DIR_OUT

    def _compute_late_minutes(
        self,
        first_in,
        shift: Shift | None,
    ) -> int:
        if not shift or not first_in:
            return 0

        att_date = first_in.date() if hasattr(first_in, "date") else timezone.now().date()
        punch_time = first_in.time() if hasattr(first_in, "time") else first_in
        punch_dt = datetime.combine(att_date, punch_time)
        start_with_grace = datetime.combine(
            att_date,
            shift.start_time,
        ) + timedelta(minutes=shift.grace_minutes)

        if punch_dt <= start_with_grace:
            return 0
        diff = punch_dt - start_with_grace
        return max(0, int(diff.total_seconds() / 60))

    def _compute_early_out_minutes(
        self,
        last_out,
        shift: Shift | None,
    ) -> int:
        if not shift or not last_out:
            return 0

        end = shift.end_time
        punch_time = last_out.time() if hasattr(last_out, "time") else last_out
        out_dt = datetime.combine(
            last_out.date() if hasattr(last_out, "date") else timezone.now().date(),
            punch_time,
        )
        end_dt = datetime.combine(out_dt.date(), end)
        if out_dt >= end_dt:
            return 0
        diff = end_dt - out_dt
        return max(0, int(diff.total_seconds() / 60))

    def _update_attendance_computed(
        self,
        attendance: Attendance,
        *,
        first_in,
        last_out,
        working_hours: Decimal,
        late_minutes: int,
        early_out_minutes: int,
        status: str,
    ) -> None:
        attendance.first_in = first_in
        attendance.last_out = last_out
        attendance.working_hours = working_hours
        attendance.late_minutes = late_minutes
        attendance.early_out_minutes = early_out_minutes
        attendance.status = status
        attendance.source = AttendanceSource.BIOMETRIC
        attendance.save(
            update_fields=[
                "first_in",
                "last_out",
                "working_hours",
                "late_minutes",
                "early_out_minutes",
                "status",
                "source",
                "updated_at",
            ]
        )

    def _sync_punches(self, attendance: Attendance, punches: list[dict]) -> int:
        """Replace punches for this attendance with the latest from raw. Returns count synced."""
        AttendancePunch.objects.filter(attendance=attendance).delete()

        to_create = [
            AttendancePunch(
                attendance=attendance,
                punch_time=p["log_date"],
                direction="in" if self._is_in(p) else "out",
            )
            for p in punches
            if self._is_in(p) or self._is_out(p)
        ]
        AttendancePunch.objects.bulk_create(to_create)
        return len(to_create)
