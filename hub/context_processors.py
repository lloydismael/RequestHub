from __future__ import annotations

from typing import Dict

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

from hub.assistant_knowledge import allowed_destinations
from hub.services.notifications import notification_summary


def request_notification_summary(request: HttpRequest) -> dict[str, int]:
    """Share the same scoped counts between a page and its navigation."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"unread_count": 0, "total_count": 0, "latest_id": 0, "toast_cursor": 0}
    identity = (user.pk, user.role)
    cached = getattr(request, "_notification_summary", None)
    if cached is None or cached[0] != identity:
        cached = (identity, notification_summary(user, include_toast_cursor=True))
        request._notification_summary = cached
    return cached[1]


def new_ticket_count(request: HttpRequest) -> int:
    return request_notification_summary(request)["unread_count"]


def notification_badges(request: HttpRequest) -> Dict[str, int | str]:
    summary = request_notification_summary(request)
    return {
        "new_ticket_count": summary["unread_count"],
        "app_version": settings.APP_VERSION,
        "notification_toast_initial_cursor": summary["toast_cursor"],
    }


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