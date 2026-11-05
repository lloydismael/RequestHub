from __future__ import annotations

from typing import Dict

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

from accounts.models import User
from hub.assistant_knowledge import allowed_destinations
from hub.models import Notification
from hub.services.notifications import visible_notifications


def new_ticket_count(request: HttpRequest) -> int:
    user = request.user
    cached = getattr(request, "_new_ticket_count", None)
    if cached is None or cached[0] != user.pk:
        count = visible_notifications(user).filter(is_read=False).count()
        cached = (user.pk, count)
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


def assistant_widget(request: HttpRequest) -> dict[str, object]:
    user = getattr(request, "user", None)
    native_enabled = bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "show_chatbot", False)
        and settings.AI_ASSISTANT_NATIVE_ENABLED
    )
    config: dict[str, object] = {}
    if native_enabled:
        destinations = allowed_destinations(user, "", None)
        current_destination = next(
            (card for card in destinations if card.url == request.path_info),
            None,
        )
        config = {
            "stateUrl": reverse("hub:assistant-state"),
            "messagesUrl": reverse("hub:assistant-messages"),
            "resetUrl": reverse("hub:assistant-reset"),
            "guideUrlTemplate": reverse(
                "hub:assistant-guide",
                kwargs={"key": "__guide_key__"},
            ),
            "departmentsUrl": reverse("hub:assistant-departments"),
            "membersUrl": reverse("hub:assistant-members"),
            "pageKey": current_destination.key if current_destination else None,
            "pageLabel": current_destination.title if current_destination else "Request Hub",
        }
    return {
        "assistant_widget": {
            "native_enabled": native_enabled,
            "config": config,
        }
    }


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
                Notification.EventType.MENTION,
            ],
        )
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
        or 0
    )

    return {
        "new_ticket_count": new_ticket_count(request),
        "app_version": settings.APP_VERSION,
        "notification_toast_initial_cursor": notification_toast_initial_cursor,
    }
