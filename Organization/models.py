from django.conf import settings
from django.db import models


class Organization(models.Model):
    """Top-level tenant. Holds multiple offices."""

    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    pincode = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
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
        db_table = "organization"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Office(models.Model):
    """Office under an organization. Can have multiple managers (OFFICE_MANAGER)."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="offices",
    )
    name = models.CharField(max_length=255)  # e.g. Headquarters, Tech Park Office
    location = models.CharField(max_length=255, blank=True)  # City/Location e.g. Mumbai, India
    full_address = models.TextField(blank=True)  # Complete address
    num_biometric_devices = models.PositiveIntegerField(default=0)
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="managed_offices",
        blank=True,
    )
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
        db_table = "office"
        ordering = ["organization", "name"]
        verbose_name_plural = "offices"

    def __str__(self):
        return f"{self.name} ({self.organization.name})"


class Department(models.Model):
    """Department within an office (e.g. HR, Finance). Scoped to office for multi-site orgs."""

    office = models.ForeignKey(
        Office,
        on_delete=models.CASCADE,
        related_name="departments",
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "department"
        ordering = ["office", "name"]
        verbose_name_plural = "departments"
        constraints = [
            models.UniqueConstraint(
                fields=["office", "name"],
                name="department_office_name_unique",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.office.name})"
