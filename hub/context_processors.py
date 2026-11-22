from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils.functional import SimpleLazyObject, empty

from hub.assistant_knowledge import allowed_destinations
from hub.services.lookup_cache import NOTIFICATION_BADGES, read_shared_lookup
from hub.services.notifications import notification_summary


class _LazyBadgeNumber(SimpleLazyObject):
    """Defer an integer while retaining numeric template localization."""

    def __int__(self) -> int:
        """Evaluate the wrapped integer for explicit conversions."""
        if self._wrapped is empty:
            self._setup()
        return self._wrapped


def request_notification_summary(request: HttpRequest, *, live: bool = False) -> dict[str, int]:
    """Share one summary per request, using live data for the Activity timeline."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {"unread_count": 0, "total_count": 0, "latest_id": 0, "toast_cursor": 0}
    using = user.notifications.all().db
    identity = (using, user.pk, user.role)
    cached = getattr(request, "_notification_summary", None)
    if cached is not None and cached[0] == identity and (cached[1] or not live):
        return cached[2]

    def load() -> dict[str, int]:
        """Read Activity counts and the broader toast cursor in one query."""
        return notification_summary(user, include_toast_cursor=True)

    summary = load() if live else read_shared_lookup(
        NOTIFICATION_BADGES, load, using=using, scope=user.pk, variant=user.role,
    )
    request._notification_summary = (identity, live, summary)
    return summary


def new_ticket_count(request: HttpRequest) -> int:
    """Return the current request's scoped unread badge count."""
    return request_notification_summary(request)["unread_count"]


def notification_badges(request: HttpRequest) -> dict[str, _LazyBadgeNumber | str]:
    """Defer notification queries until a template needs a badge or cursor."""
    return {
        "new_ticket_count": _LazyBadgeNumber(lambda: new_ticket_count(request)),
        "app_version": settings.APP_VERSION,
        "notification_toast_initial_cursor": _LazyBadgeNumber(lambda: request_notification_summary(request)["toast_cursor"]),
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