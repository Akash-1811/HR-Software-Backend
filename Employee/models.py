from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Designation(models.TextChoices):
    ORG_ADMIN = "ORG_ADMIN", "Org Admin"
    OFFICE_ADMIN = "OFFICE_ADMIN", "Office Admin"
    MANAGER = "MANAGER", "Manager"
    SUPERVISOR = "SUPERVISOR", "Supervisor"
    REGULAR_EMPLOYEE = "EMPLOYEE", "Staff"
    SUPPORT_STAFF = "SUPPORT_STAFF", "Support Staff"


class Gender(models.TextChoices):
    MALE = "M", "Male"
    FEMALE = "F", "Female"
    OTHER = "O", "Other"


class GovernmentIdType(models.TextChoices):
    LICENSE = "License", "License"
    PANCARD = "PanCard", "Pan Card"
    AADHAARCARD = "AadhaarCard", "Aadhaar Card"
    VOTERID = "VoterID", "Voter ID"


class Employee(models.Model):
    """Employee belonging to an organization and one office. emp_code = ESSL user ID."""

    organization = models.ForeignKey(
        "Organization.Organization",
        on_delete=models.CASCADE,
        related_name="employees",
    )
    office = models.ForeignKey(
        "Organization.Office",
        on_delete=models.CASCADE,
        related_name="employees",
    )
    shift = models.ForeignKey(
        "Shifts.Shift",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
    )
    department = models.ForeignKey(
        "Organization.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
    )
    emp_code = models.CharField(max_length=64)  # ESSL user ID, unique per org
    name = models.CharField(max_length=255)
    designation = models.CharField(max_length=32, choices=Designation.choices, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    government_id_type = models.CharField(max_length=32, choices=GovernmentIdType.choices, blank=True)
    government_id_value = models.CharField(max_length=128, blank=True)
    profile_pic = models.ImageField(upload_to="employee_profiles/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    # Optional link to User (e.g. Office Admin who is also an employee in the roster).
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_record",
    )
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
        db_table = "employee"
        ordering = ["organization", "office", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "emp_code"],
                name="employee_org_emp_code_unique",
            )
        ]
        indexes = [
            # Fast filter for duplicate check and list views (office + active).
            models.Index(fields=["office_id", "is_active"], name="emp_office_active_idx"),
            # Duplicate check: index-backed lookups for phone/email/govt_id per office.
            models.Index(
                fields=["office_id", "is_active", "phone_number"],
                name="emp_office_active_phone_idx",
            ),
            models.Index(
                fields=["office_id", "is_active", "email"],
                name="emp_office_active_email_idx",
            ),
            models.Index(
                fields=["office_id", "is_active", "government_id_value"],
                name="emp_office_active_govt_id_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.emp_code})"

    def clean(self):
        super().clean()
        if self.office_id and self.organization_id and self.office.organization_id != self.organization_id:
            raise ValidationError({"office": "Office must belong to the same organization."})
        if self.shift_id and self.office_id and self.shift.office_id != self.office_id:
            raise ValidationError({"shift": "Shift must belong to the same office as the employee."})
        if self.department_id and self.office_id and self.department.office_id != self.office_id:
            raise ValidationError({"department": "Department must belong to the same office as the employee."})


class MaritalStatus(models.TextChoices):
    SINGLE = "SINGLE", "Single"
    MARRIED = "MARRIED", "Married"
    DIVORCED = "DIVORCED", "Divorced"
    WIDOWED = "WIDOWED", "Widowed"
    OTHER = "OTHER", "Other"


class EmploymentType(models.TextChoices):
    FULL_TIME = "FULL_TIME", "Full-time"
    PART_TIME = "PART_TIME", "Part-time"
    CONTRACT = "CONTRACT", "Contract"
    INTERN = "INTERN", "Intern"
    OTHER = "OTHER", "Other"


class EmployeeProfile(models.Model):
    """Extended HR-style profile linked one-to-one to Employee (self-service + directory)."""

    employee = models.OneToOneField(
        Employee,
        on_delete=models.CASCADE,
        related_name="extended_profile",
    )
    marital_status = models.CharField(max_length=16, choices=MaritalStatus.choices, blank=True)
    blood_group = models.CharField(max_length=16, blank=True)
    nationality = models.CharField(max_length=100, blank=True)
    alternate_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_relation = models.CharField(max_length=64, blank=True)
    current_address = models.TextField(blank=True)
    permanent_address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    joining_date = models.DateField(null=True, blank=True)
    employment_type = models.CharField(max_length=16, choices=EmploymentType.choices, blank=True)
    work_location = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional display label; canonical site is office.",
    )
    employment_status_note = models.CharField(
        max_length=64,
        blank=True,
        help_text="e.g. ON_LEAVE — informational; does not replace is_active.",
    )
    education_entries = models.JSONField(blank=True, default=list)
    certifications = models.TextField(blank=True)
    skills = models.TextField(blank=True)
    linkedin_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)
    portfolio_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    reporting_manager = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profile_managed_employees",
        help_text="HR-assigned reporting line (read-only for employee self-service API).",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "employee_profile"
        verbose_name = "Employee profile"
        verbose_name_plural = "Employee profiles"

    def __str__(self):
        return f"Profile · {self.employee.name}"
