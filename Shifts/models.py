from django.conf import settings
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

    def __str__(self):
        return f"{self.name} ({self.office.name})"
