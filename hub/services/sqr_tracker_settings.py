from django.core.exceptions import PermissionDenied
from django.db import transaction

from accounts.models import User
from hub.models import SqrTrackerSettings


def get_tracker_settings() -> SqrTrackerSettings:
    settings, _ = SqrTrackerSettings.objects.get_or_create(pk=1)
    return settings


@transaction.atomic
def set_classic_visibility(*, actor: User, visible: bool) -> SqrTrackerSettings:
    if actor.role != User.Roles.ADMIN:
        raise PermissionDenied
    if type(visible) is not bool:
        raise ValueError("Visibility must be a boolean.")
    settings = get_tracker_settings()
    settings = SqrTrackerSettings.objects.select_for_update().get(pk=settings.pk)
    settings.show_classic_sqr_tracker = visible
    settings.save(update_fields=["show_classic_sqr_tracker"])
    return settings


def resolve_sqr_view(
    requested: str | None, *, show_classic: bool, can_view_performance: bool
) -> tuple[str, str]:
    if requested == "tracker":
        if show_classic:
            return "tracker", ""
        return "prototype", "SQR Tracker (Classic) is hidden by Admin. Showing SQR Tracker."
    if requested == "performance" and can_view_performance:
        return "performance", ""
    if requested not in (None, "", "prototype"):
        return "prototype", "That view is unavailable. Showing SQR Tracker."
    return "prototype", ""
