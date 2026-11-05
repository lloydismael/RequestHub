from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Max, Q

from accounts.models import User
from hub.models import Notification, Request


ADMIN_NOTIFICATION_ROLES = {User.Roles.ADMIN, User.Roles.PM_ESG}


def visible_notifications(user: User):
    queryset = user.notifications.all()
    if user.role in ADMIN_NOTIFICATION_ROLES:
        queryset = queryset.filter(Q(source__icontains="new request") | Q(event_type=Notification.EventType.MENTION))
    return queryset


def notification_summary(user: User) -> dict:
    summary = visible_notifications(user).aggregate(
        unread_count=Count("pk", filter=Q(is_read=False)),
        total_count=Count("pk"),
        latest_id=Max("pk"),
    )
    summary["latest_id"] = summary["latest_id"] or 0
    return summary


def _user_label(user: User) -> str:
    return user.get_full_name().strip() or user.username or "Request Hub"


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
