"""
Management command to send daily attendance report to managers, supervisors,
and office admins for each office. Runs at 1 AM daily via cron.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from Reports.email_report import run_send_daily_attendance_emails
from Reports.utils import parse_date


class Command(BaseCommand):
    help = (
        "Send daily attendance report (parent-only Excel) to managers, supervisors, "
        "and office admins for each office. Runs at 1 AM daily via cron."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Report date YYYY-MM-DD (default: yesterday)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without sending",
        )
        parser.add_argument(
            "--office",
            type=int,
            metavar="OFFICE_ID",
            help="Send only for this office ID",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        date_str = options.get("date")
        office_id_filter = options.get("office")

        if date_str:
            report_date = parse_date(date_str)
            if not report_date:
                self.stderr.write(self.style.ERROR(f"Invalid date: {date_str}"))
                return
        else:
            report_date = (timezone.now() - timedelta(days=1)).date()

        self.stdout.write(f"Report date: {report_date}")

        result = run_send_daily_attendance_emails(
            report_date=report_date,
            office_id_filter=office_id_filter,
            dry_run=dry_run,
        )
        self.stdout.write(self.style.SUCCESS(result["message"]))
