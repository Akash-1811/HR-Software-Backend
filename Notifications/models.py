from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class NotificationType(models.TextChoices):
    REGULARIZATION_REQUEST = "REGULARIZATION_REQUEST", "Regularization Request"
    REGULARIZATION_APPROVED = "REGULARIZATION_APPROVED", "Regularization Approved"
    REGULARIZATION_REJECTED = "REGULARIZATION_REJECTED", "Regularization Rejected"


# Display style for frontend (success|info|warning). Add new types here when extending NotificationType.
DISPLAY_TYPE_MAP = {
    NotificationType.REGULARIZATION_APPROVED: "success",
    NotificationType.REGULARIZATION_REJECTED: "warning",
    NotificationType.REGULARIZATION_REQUEST: "info",
}


class Notification(models.Model):
    """
    Centralized notification model.
    Uses GenericForeignKey so any app can attach a related object
    (e.g. attendance regularizations) without schema changes.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    # User who triggered this notification (e.g. approver). Null = system-generated.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications_triggered",
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
    )
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notification"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["recipient", "is_read", "-created_at"],
                name="notif_recipient_read_idx",
            ),
        ]

    def __str__(self):
        return f"{self.recipient} – {self.title}"
