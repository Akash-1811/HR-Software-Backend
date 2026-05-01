"""
Centralized helpers for creating notifications.
Import from any app:  from Notifications.utils import create_notification
"""

from django.contrib.contenttypes.models import ContentType

from Notifications.models import Notification


def _generic_fk_for_object(related_object):
    """Resolve ContentType + pk for a related model instance."""
    if related_object is None:
        return None, None
    return ContentType.objects.get_for_model(related_object), related_object.pk


def create_notification(
    recipient,
    notification_type: str,
    title: str,
    message: str = "",
    related_object=None,
    created_by=None,
):
    """
    Create a single notification, optionally linked to any model instance.
    created_by: User who triggered the notification; None for system-generated.
    """
    content_type, object_id = _generic_fk_for_object(related_object)

    return Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        message=message,
        content_type=content_type,
        object_id=object_id,
        created_by=created_by,
    )


def create_bulk_notifications(
    recipients,
    notification_type: str,
    title: str,
    message: str = "",
    related_object=None,
    created_by=None,
):
    """
    Create the same notification for every user in *recipients*.
    created_by: User who triggered the notification; None for system-generated.
    """
    content_type, object_id = _generic_fk_for_object(related_object)

    notifications = [
        Notification(
            recipient=user,
            notification_type=notification_type,
            title=title,
            message=message,
            content_type=content_type,
            object_id=object_id,
            created_by=created_by,
        )
        for user in recipients
    ]
    return Notification.objects.bulk_create(notifications)
