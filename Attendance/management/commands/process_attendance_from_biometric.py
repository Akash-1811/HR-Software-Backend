"""
Django management command to process raw biometric data into Attendance records.

Fetches from DummyEsslBiometricAttendanceData, groups by employee+date, computes
first_in/last_out/working_hours/late_minutes/early_out_minutes/status, stores
Attendance and AttendancePunch records.

Usage:
    python manage.py process_attendance_from_biometric
    python manage.py process_attendance_from_biometric --from-date 2026-03-01 --to-date 2026-03-31

Schedule via cron, e.g. daily at 2 AM:
    0 2 * * * cd /path/to/project && python manage.py process_attendance_from_biometric
"""

from datetime import datetime

from django.core.management.base import BaseCommand

from Attendance.processing import BiometricAttendanceProcessor


class Command(BaseCommand):
    help = (
        "Process raw biometric data (DummyEsslBiometricAttendanceData) into "
        "Attendance and AttendancePunch records. Schedulable via cron."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-date",
            type=str,
            default=None,
            help="Start date (YYYY-MM-DD). Default: process all raw data.",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            default=None,
            help="End date (YYYY-MM-DD). Default: process all raw data.",
        )

    def handle(self, *args, **options):
        from_date = self._parse_date(options["from_date"])
        to_date = self._parse_date(options["to_date"])

        if from_date and to_date and from_date > to_date:
            self.stderr.write(self.style.ERROR("from-date must be <= to-date"))
            return

        processor = BiometricAttendanceProcessor()
        stats = processor.process(from_date=from_date, to_date=to_date)

        self.stdout.write(self.style.SUCCESS(str(stats)))

    def _parse_date(self, value: str | None):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            self.stderr.write(self.style.ERROR(f"Invalid date format: {value}. Use YYYY-MM-DD."))
            return None
