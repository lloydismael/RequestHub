from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction
from django.db.models import Count, Max, Q

from accounts.models import User
from hub.mixins import ADMIN_PANEL_ROLES as ADMIN_NOTIFICATION_ROLES
from hub.models import Notification, Request, SqrSubmission
from hub.services.lookup_cache import invalidate_notification_badges
from hub.services.request_changes import (
    create_change_status_log,
    summarize_request_changes,
)

TOAST_EVENT_TYPES = (Notification.EventType.NEW_REQUEST, Notification.EventType.ASSIGNMENT, Notification.EventType.MENTION)


def _activity_scope(user: User):
    if user.role in ADMIN_NOTIFICATION_ROLES:
        return Q(source__icontains="new request") | Q(event_type=Notification.EventType.MENTION)
    return Q()


def visible_notifications(user: User):
    return user.notifications.filter(_activity_scope(user))


def notification_summary(user: User, *, include_toast_cursor: bool = False) -> dict[str, int]:
    queryset = user.notifications.all()
    activity = _activity_scope(user)
    aggregates = {}
    if include_toast_cursor:
        # Toast events aren't restricted to the role's Activity feed.
        aggregates["toast_cursor"] = Max("pk", filter=Q(event_type__in=TOAST_EVENT_TYPES), default=0)
    else:
        queryset = queryset.filter(activity)
        activity = Q()
    return queryset.aggregate(
        unread_count=Count("pk", filter=activity & Q(is_read=False)),
        total_count=Count("pk", filter=activity),
        latest_id=Max("pk", filter=activity, default=0),
        **aggregates,
    )


def _user_label(user: User) -> str:
    return user.get_full_name().strip() or user.username or "Request Hub"


def notify_request_edit(
    actor: User,
    original: Request,
    updated: Request,
    changed_fields: Iterable[str],
    source_label: str,
    *,
    include_management: bool = False,
) -> None:
    """Record a request edit and notify the recipients selected by that flow."""
    summary_text = summarize_request_changes(original, updated, changed_fields)
    if not summary_text:
        return
    create_change_status_log(updated, actor, source_label, summary_text)

    name = actor.get_full_name() or actor.username
    management_ids = (
        User.objects.filter(role__in=ADMIN_NOTIFICATION_ROLES).values_list("pk", flat=True)
        if include_management else ()
    )
    recipient_ids = dict.fromkeys((*management_ids, updated.requestor_id, updated.engineer_id, updated.backup_engineer_id))
    recipient_ids.pop(None, None)
    recipient_ids.pop(actor.pk, None)
    notifications = Notification.objects.bulk_create([
        Notification(
            recipient_id=recipient_id,
            message=f"{name} updated {updated.reference_code}: {summary_text}",
            related_request=updated,
            actor=name,
            source=source_label,
        )
        for recipient_id in recipient_ids
    ])
    if notifications:
        invalidate_notification_badges(recipient_ids, using=notifications[0]._state.db)


def notify_sqr_submission(submission: SqrSubmission) -> None:
    """Notify the reviewer and admins of a newly filed SQR."""
    actor_name = submission.engineer.get_full_name() or submission.engineer.username
    message = f"{actor_name} submitted {submission.reference_code} for {submission.customer_name}."
    admin_ids = User.objects.filter(role=User.Roles.ADMIN).values_list("pk", flat=True)
    recipient_ids = dict.fromkeys((submission.pm_esg_reviewer_id, *admin_ids))
    recipient_ids.pop(submission.engineer_id, None)
    notifications = Notification.objects.bulk_create([
        Notification(
            recipient_id=recipient_id,
            message=message,
            actor=actor_name,
            source="SQR · New Submission",
        )
        for recipient_id in recipient_ids
    ])
    if notifications:
        invalidate_notification_badges(recipient_ids, using=notifications[0]._state.db)


def queue_new_request_notifications(request_obj: Request, *, actor_user: User) -> None:
    """Create one new-request event for each eligible admin after commit."""

    request_id = request_obj.pk
    actor_id = actor_user.pk

    def create_notifications() -> None:
        current = Request.all_objects.select_related("requestor", "account").get(pk=request_id)
        actor = User.objects.get(pk=actor_id)
        actor_name = _user_label(actor)
        due = current.due_date.strftime("%b %d, %Y") if current.due_date else "No due date"
        message = (
            f"New {current.get_priority_display()} ticket {current.reference_code} for "
            f"{current.account.name} ({current.get_product_category_display()}) "
            f"submitted by {actor_name}. Due {due}."
        )
        recipients = User.objects.filter(role__in=ADMIN_NOTIFICATION_ROLES).exclude(pk=actor_id)
        for recipient in recipients:
            Notification.objects.get_or_create(
                recipient=recipient,
                event_key=f"request:{current.pk}:new_request:recipient:{recipient.pk}",
                defaults={
                    "event_type": Notification.EventType.NEW_REQUEST,
                    "event_revision": 0,
                    "message": message,
                    "related_request": current,
                    "actor": actor_name,
                    "source": "Dashboard · New Request",
                },
            )

    transaction.on_commit(create_notifications)


def queue_assignment_notifications(
    request_obj: Request,
    *,
    actor_user: User,
    previous_engineer_id: int | None = None,
    previous_backup_id: int | None = None,
) -> None:
    """Create one assignment event for each distinct newly assigned user."""

    recipient_ids: set[int] = set()
    if request_obj.engineer_id and request_obj.engineer_id != previous_engineer_id:
        recipient_ids.add(request_obj.engineer_id)
    if request_obj.backup_engineer_id and request_obj.backup_engineer_id != previous_backup_id:
        recipient_ids.add(request_obj.backup_engineer_id)
    recipient_ids.discard(actor_user.pk)
    if not recipient_ids:
        return

    request_id = request_obj.pk
    actor_id = actor_user.pk
    revision = request_obj.assignment_revision
    recipients = tuple(recipient_ids)

    def create_notifications() -> None:
        current = Request.all_objects.select_related("account").get(pk=request_id)
        actor_name = _user_label(User.objects.get(pk=actor_id))
        message = (
            f"You were assigned to {current.reference_code} · {current.account.name} "
            f"({current.get_engagement_type_display()})."
        )
        for recipient_id in recipients:
            Notification.objects.get_or_create(
                recipient_id=recipient_id,
                event_key=(
                    f"request:{current.pk}:assignment:revision:{revision}:"
                    f"recipient:{recipient_id}"
                ),
                defaults={
                    "event_type": Notification.EventType.ASSIGNMENT,
                    "event_revision": revision,
                    "message": message,
                    "related_request": current,
                    "actor": actor_name,
                    "source": "Assignment",
                },
            )

    transaction.on_commit(create_notifications)
