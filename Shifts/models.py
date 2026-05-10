from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Shift(models.Model):
    """Shift definition for an office. Supports grace period and night shift."""

    office = models.ForeignKey(
        "Organization.Office",
        on_delete=models.CASCADE,
        related_name="shifts",
    )
    name = models.CharField(max_length=255)
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=0)
    #: Weekly off days: integers 0–6 meaning Monday–Sunday (same as Python weekday()).
    weekoff_days = models.JSONField(default=list, blank=True)
    #: Minimum billable / expected working hours per shift day (optional).
    min_working_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    lunch_break_minutes = models.PositiveSmallIntegerField(default=0)
    tea_break_minutes = models.PositiveSmallIntegerField(default=0)
    lunch_break_paid = models.BooleanField(default=True)
    tea_breaks_paid = models.BooleanField(default=True)
    is_night_shift = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "shift"
        ordering = ["office", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["office"],
                condition=models.Q(is_default=True),
                name="shift_office_one_default_unique",
            ),
        ]

    def clean(self):
        super().clean()
        days = self.weekoff_days
        if days is None:
            days = []
        if not isinstance(days, list):
            raise ValidationError({"weekoff_days": "Must be a list of weekday numbers."})
        norm = []
        seen = set()
        for d in days:
            try:
                i = int(d)
            except (TypeError, ValueError):
                raise ValidationError({"weekoff_days": "Each weekday must be an integer 0–6."}) from None
            if i < 0 or i > 6:
                raise ValidationError({"weekoff_days": "Weekdays must be between 0 (Monday) and 6 (Sunday)."})
            if i not in seen:
                seen.add(i)
                norm.append(i)
        self.weekoff_days = sorted(norm)
        if self.min_working_hours is not None:
            h = self.min_working_hours
            if h < Decimal("0") or h > Decimal("24"):
                raise ValidationError({"min_working_hours": "Must be between 0 and 24 hours."})

    def __str__(self):
        return f"{self.name} ({self.office.name})"
