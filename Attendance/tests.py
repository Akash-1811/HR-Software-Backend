"""Attendance pipeline QA/regression tests (tenant isolation, concurrency-safe persistence)."""

from datetime import datetime, time

from django.test import TestCase
from django.utils import timezone

from Attendance.models import Attendance
from Attendance.processing import BiometricAttendanceProcessor
from Biometric.models import DummyEsslBiometricAttendanceData
from Employee.models import Employee
from Organization.models import Office, Organization


class BiometricProcessorRegressionTests(TestCase):
    """
    Raw biometric ``UserId`` is only unique per organization.

    If the same emp_code exists in multiple tenants, attributing punches via
    ``.first()`` would corrupt attendance — processor must skip, not guess.
    """

    def test_ambiguous_emp_code_skips_all_matching_days_without_corruption(self):
        org_a = Organization.objects.create(name="Tenant A")
        org_b = Organization.objects.create(name="Tenant B")
        office_a = Office.objects.create(organization=org_a, name="Office A")
        office_b = Office.objects.create(organization=org_b, name="Office B")
        Employee.objects.create(
            organization=org_a,
            office=office_a,
            emp_code="E001",
            name="Alice",
        )
        Employee.objects.create(
            organization=org_b,
            office=office_b,
            emp_code="E001",
            name="Bob",
        )

        d = timezone.now().date()
        ts_in = timezone.make_aware(datetime.combine(d, time(9, 0)))
        ts_out = timezone.make_aware(datetime.combine(d, time(18, 0)))
        DummyEsslBiometricAttendanceData.objects.create(
            DeviceLogId=900001,
            UserId="E001",
            LogDate=ts_in,
            Direction="in",
        )
        DummyEsslBiometricAttendanceData.objects.create(
            DeviceLogId=900002,
            UserId="E001",
            LogDate=ts_out,
            Direction="out",
        )

        stats = BiometricAttendanceProcessor().process(from_date=d, to_date=d)

        self.assertGreaterEqual(stats.skipped_ambiguous_emp_code, 1)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_single_tenant_emp_code_processes_normally(self):
        org = Organization.objects.create(name="Tenant Solo")
        office = Office.objects.create(organization=org, name="HQ")
        Employee.objects.create(
            organization=org,
            office=office,
            emp_code="S001",
            name="Solo User",
        )
        d = timezone.now().date()
        ts_in = timezone.make_aware(datetime.combine(d, time(9, 0)))
        ts_out = timezone.make_aware(datetime.combine(d, time(18, 0)))
        DummyEsslBiometricAttendanceData.objects.create(
            DeviceLogId=910001,
            UserId="S001",
            LogDate=ts_in,
            Direction="in",
        )
        DummyEsslBiometricAttendanceData.objects.create(
            DeviceLogId=910002,
            UserId="S001",
            LogDate=ts_out,
            Direction="out",
        )

        stats = BiometricAttendanceProcessor().process(from_date=d, to_date=d)

        self.assertEqual(stats.skipped_ambiguous_emp_code, 0)
        self.assertEqual(stats.created, 1)
        self.assertEqual(Attendance.objects.count(), 1)
