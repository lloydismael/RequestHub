"""Notification inbox and notification-related view helpers."""

from collections.abc import Iterable

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest

from accounts.models import User
from hub.models import Request, StatusLog
from hub.notification_views import (
    NotificationDeleteView as NotificationDeleteView,  # noqa: PLC0414 - Legacy re-export.
)
from hub.notification_views import (
    NotificationFollowRedirectView as NotificationFollowRedirectView,  # noqa: PLC0414 - Legacy re-export.
)
from hub.notification_views import (
    NotificationListView as NotificationListView,  # noqa: PLC0414 - Legacy re-export.
)
from hub.notification_views import (
    NotificationMarkAllReadView as NotificationMarkAllReadView,  # noqa: PLC0414 - Legacy re-export.
)
from hub.notification_views import (
    NotificationReadView as NotificationReadView,  # noqa: PLC0414 - Legacy re-export.
)
from hub.notification_views import (
    NotificationToastDataView as NotificationToastDataView,  # noqa: PLC0414 - Legacy re-export.
)
from hub.services.notifications import (
    notify_request_edit,
    queue_assignment_notifications,
)
from hub.services.request_email import AssignmentEmailResult


def flash_assignment_email_feedback(
    request: HttpRequest,
    result: AssignmentEmailResult,
    *,
    action_label: str,
    notify_on_no_assignee: bool = False,
) -> None:
    """Show the delivery outcome without changing the assignment itself."""
    if result.status == "sent":
        return

    if result.status == "no_new_assignee":
        if notify_on_no_assignee:
            messages.info(
                request,
                f"Request {action_label}, but no assignment email was sent because no engineer or backup engineer was assigned.",
            )
        return

    if result.status == "missing_assignee_email":
        messages.warning(
            request,
            f"Request {action_label}, but no assignment email was sent because the assigned engineer or backup engineer has no email address configured.",
        )
        return

    if result.status == "missing_acs_config":
        messages.warning(
            request,
            f"Request {action_label}, but Azure Communication Services email is not configured.",
        )
        return

    if result.status == "delivery_failed":
        messages.error(
            request,
            f"Request {action_label}, but the assignment email could not be delivered from {settings.ACS_EMAIL_SENDER or 'the configured sender address'}. Check Azure Communication Services email configuration and logs.",
        )


def notify_status_update(
    log: StatusLog,
    source_label: str,
    *,
    previous_message: str | None = None,
    http_request: HttpRequest | None = None,
) -> None:
    """Notify mentioned users and keep delivery failures visible to the actor."""
    from hub.services.update_mentions import notify_update

    def email_feedback(result: str) -> None:
        """Report an email delivery failure without discarding the update."""
        messages.warning(
            http_request,
            "The update and mention notification were saved, but a mention email could not be sent. "
            "Check the recipient's email address and email delivery configuration.",
        )

    notify_update(
        log,
        source_label,
        previous_message=previous_message,
        email_feedback=email_feedback if http_request else None,
    )


def notify_account_manager_request_update(
    actor_user: User, original: Request, updated: Request, changed_fields: Iterable[str], source_label: str,
) -> None:
    """Record the edit and notify management as well as the request participants."""
    notify_request_edit(actor_user, original, updated, changed_fields, source_label, include_management=True)


def notify_engineer_assignment_notification(
    request_obj: Request,
    *,
    actor_user: User,
    previous_engineer_id: int | None = None,
    previous_backup_id: int | None = None,
) -> None:
    """Create in-app notifications when an engineer or backup is newly assigned."""
    queue_assignment_notifications(
        request_obj,
        actor_user=actor_user,
        previous_engineer_id=previous_engineer_id,
        previous_backup_id=previous_backup_id,
    )
