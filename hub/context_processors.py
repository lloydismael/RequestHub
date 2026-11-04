from __future__ import annotations

from typing import Dict

from django.conf import settings
from django.http import HttpRequest

from accounts.models import User
from hub.models import Notification


def new_ticket_count(request: HttpRequest) -> int:
    user = request.user
    cached = getattr(request, "_new_ticket_count", None)
    if cached is None or cached[0] != user.pk:
        cached = (user.pk, user.notifications.filter(is_read=False, source__icontains="new request").count())
        request._new_ticket_count = cached
    return cached[1]


def notification_badges(request: HttpRequest) -> Dict[str, int | str]:
    """Reuse private badge data only within this HTTP request."""
    user = getattr(request, "user", None)
    user_id = getattr(user, "pk", None)
    cached = getattr(request, "_notification_badges", None)
    if cached is None or cached[0] != user_id:
        cached = (user_id, _notification_badges(request))
        request._notification_badges = cached
    return cached[1]


def _notification_badges(request: HttpRequest) -> Dict[str, int | str]:
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "new_ticket_count": 0,
            "app_version": settings.APP_VERSION,
            "notification_toast_initial_cursor": 0,
        }

    notification_toast_initial_cursor = (
        Notification.objects.filter(
            recipient=user,
            event_type__in=[
                Notification.EventType.NEW_REQUEST,
                Notification.EventType.ASSIGNMENT,
            ],
        )
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
        or 0
    )

    if getattr(user, "role", None) not in {User.Roles.ADMIN, User.Roles.PM_ESG}:
        return {
            "new_ticket_count": 0,
            "app_version": settings.APP_VERSION,
            "notification_toast_initial_cursor": notification_toast_initial_cursor,
        }

    return {
        "new_ticket_count": new_ticket_count(request),
        "app_version": settings.APP_VERSION,
        "notification_toast_initial_cursor": notification_toast_initial_cursor,
    }
