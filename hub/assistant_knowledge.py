import re
from dataclasses import dataclass
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.urls import NoReverseMatch, reverse

from accounts.models import User
from hub.assistant_types import (
    DepartmentSummary,
    DestinationCard,
    Guide,
    SourceRef,
)


class AssistantCatalogError(ValueError):
    pass


class GuideNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class _Destination:
    key: str
    title: str
    description: str
    route_name: str
    view_name: str
    search_terms: tuple[str, ...]


_DESTINATIONS = (
    _Destination(
        "dashboard",
        "Dashboard",
        "View the requests and activity available to your role.",
        "hub:dashboard",
        "DashboardView",
        ("home", "requests", "work"),
    ),
    _Destination(
        "activity_logs",
        "Activity Logs",
        "Review your own engineering activity logs.",
        "hub:activity-logs",
        "EngineerActivityLogView",
        ("engineering", "hours", "time", "work log"),
    ),
    _Destination(
        "sqr",
        "Service Quotations",
        "Open the Service Quotation Request workspace.",
        "hub:sqr",
        "SqrListView",
        ("sqr", "service quotation", "quotation", "proposal"),
    ),
    _Destination(
        "management",
        "Management",
        "Open administrator tools for users and application management.",
        "hub:management",
        "UserManagementView",
        ("admin", "users", "accounts", "settings"),
    ),
    _Destination(
        "reports",
        "Reports",
        "Review request summary reports.",
        "hub:report",
        "RequestReportView",
        ("report", "analytics", "charts"),
    ),
    _Destination(
        "notifications",
        "Notifications",
        "Review your notifications and published changelogs.",
        "hub:notifications",
        "NotificationListView",
        ("alerts", "changelog", "updates"),
    ),
    _Destination(
        "profile",
        "Profile & Preferences",
        "Update your profile and personal application preferences.",
        "accounts:update",
        "ProfileUpdateView",
        ("account", "appearance", "theme", "preferences", "password"),
    ),
)


def _build_guides() -> dict[str, Guide]:
    content = (
        (
            "dashboard",
            "Dashboard",
            "Review the Request Hub dashboard for the work and summaries available to your application role.",
            (
                "Choose Dashboard from the main navigation.",
                "Use the filters and actions shown for your role; available records and controls vary by permission.",
            ),
        ),
        (
            "activity_logs",
            "Activity Logs",
            "Review your engineering activity log entries.",
            (
                "Choose Activity Logs from the main navigation.",
                "Review the entries associated with your account and use the available month filter.",
            ),
        ),
        (
            "sqr",
            "Service Quotations",
            "Find and review service quotation requests and the workspace sections available to your role.",
            (
                "Choose Service Quotations from the main navigation.",
                "Use the workspace tabs and controls available to your role; restricted actions remain protected by Request Hub.",
            ),
        ),
        (
            "management",
            "Management",
            "Find administrator-only user and application management tools.",
            (
                "Choose Management from the main navigation.",
                "Use only the management controls available to your administrator account.",
            ),
        ),
        (
            "reports",
            "Reports",
            "Review request reporting and summaries available to administrators and PM-ESG users.",
            (
                "Choose Reports from the main navigation.",
                "Use the report filters and export controls shown on the page.",
            ),
        ),
        (
            "notifications",
            "Notifications",
            "Review notifications and published release information.",
            (
                "Choose Notifications from the main navigation.",
                "Use the available filters and tabs to review notifications or published changelogs.",
            ),
        ),
        (
            "profile",
            "Profile & Preferences",
            "Update your profile and personal appearance and accessibility preferences.",
            (
                "Open your account menu and choose Settings.",
                "Save changes using the controls provided on your profile page.",
            ),
        ),
    )
    return {
        key: Guide(
            key=key,
            title=title,
            purpose=purpose,
            steps=steps,
            destination_keys=(key,),
            source=SourceRef(
                key=f"guide:{key}",
                title=title,
                revision="1",
                kind="guide",
            ),
        )
        for key, title, purpose, steps in content
    }


GUIDE_CATALOG = _build_guides()
DEPARTMENT_CATALOG: dict[str, DepartmentSummary] = {}

_IGNORED_QUERY_WORDS = {
    "a", "an", "are", "can", "do", "find", "for", "how", "i", "in", "is",
    "me", "of", "on", "the", "to", "where", "with",
}


def normalize_department(value: str) -> str:
    return value.strip().casefold()


def _destination_registry() -> dict[str, tuple[_Destination, type]]:
    from accounts.views import ProfileUpdateView
    from hub.views import (
        DashboardView,
        EngineerActivityLogView,
        NotificationListView,
        RequestReportView,
        SqrListView,
        UserManagementView,
    )

    view_classes = {
        "DashboardView": DashboardView,
        "EngineerActivityLogView": EngineerActivityLogView,
        "SqrListView": SqrListView,
        "UserManagementView": UserManagementView,
        "RequestReportView": RequestReportView,
        "NotificationListView": NotificationListView,
        "ProfileUpdateView": ProfileUpdateView,
    }
    return {
        item.key: (item, view_classes[item.view_name])
        for item in _DESTINATIONS
    }


def _is_active_authenticated_user(user: User) -> bool:
    return (
        bool(getattr(user, "is_authenticated", False))
        and not isinstance(user, AnonymousUser)
        and bool(getattr(user, "is_active", False))
    )


def _view_allows_user(view_class: type, user: User) -> bool:
    from hub.mixins import RoleRequiredMixin
    from hub.views import SQR_ACCESS_ROLES

    if view_class.__name__ == "SqrListView":
        return user.role in SQR_ACCESS_ROLES

    if issubclass(view_class, RoleRequiredMixin):
        view = view_class()
        view.request = SimpleNamespace(user=user)
        return bool(view.test_func())

    return True


def validate_catalog() -> None:
    registry = _destination_registry()
    destination_keys = set(registry)

    if len(destination_keys) != len(_DESTINATIONS):
        raise AssistantCatalogError("Destination keys must be unique.")

    for key, (destination, _) in registry.items():
        try:
            reverse(destination.route_name)
        except NoReverseMatch as exc:
            raise AssistantCatalogError(
                f"Destination {key!r} does not resolve to a named route."
            ) from exc

    for key, guide in GUIDE_CATALOG.items():
        if key != guide.key or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise AssistantCatalogError("Guide keys must be canonical identifiers.")
        if not guide.steps or not guide.purpose.strip():
            raise AssistantCatalogError("Guides require a purpose and verified steps.")
        if not set(guide.destination_keys) <= destination_keys:
            raise AssistantCatalogError("Guide destinations must be allowlisted.")
        if guide.source.key != f"guide:{key}" or guide.source.kind != "guide":
            raise AssistantCatalogError("Guide sources must use their canonical key.")

    for key, overview in DEPARTMENT_CATALOG.items():
        if key != normalize_department(key) or not re.fullmatch(r"[a-z0-9]+(?:[ -][a-z0-9]+)*", key):
            raise AssistantCatalogError("Department keys must be normalized identifiers.")
        if overview.key != key or not overview.name.strip():
            raise AssistantCatalogError("Department descriptions require canonical keys and names.")
        if not overview.summary or not overview.summary.strip() or not overview.responsibilities:
            raise AssistantCatalogError("Department descriptions require approved facts.")


def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(
        term
        for term in re.findall(r"[a-z0-9]+", query.casefold())
        if term not in _IGNORED_QUERY_WORDS
    )


def allowed_destinations(
    user: User,
    query: str,
    current_page_key: str | None,
) -> tuple[DestinationCard, ...]:
    if not _is_active_authenticated_user(user):
        return ()

    validate_catalog()
    terms = _query_terms(query)
    cards = []
    for key, (destination, view_class) in _destination_registry().items():
        if key == current_page_key or not _view_allows_user(view_class, user):
            continue
        searchable = " ".join(
            (
                destination.key,
                destination.title,
                destination.description,
                *destination.search_terms,
            )
        ).casefold()
        if terms and not all(term in searchable for term in terms):
            continue
        cards.append(
            DestinationCard(
                key=key,
                title=destination.title,
                description=destination.description,
                url=reverse(destination.route_name),
            )
        )
    return tuple(cards)


def get_guide(user: User, key: str) -> Guide:
    if not _is_active_authenticated_user(user):
        raise PermissionDenied

    validate_catalog()
    guide = GUIDE_CATALOG.get(key)
    if guide is None:
        raise GuideNotFoundError(key)

    registry = _destination_registry()
    if any(
        destination_key not in registry
        or not _view_allows_user(registry[destination_key][1], user)
        for destination_key in guide.destination_keys
    ):
        raise PermissionDenied
    return guide


def approved_department_overviews() -> tuple[DepartmentSummary, ...]:
    validate_catalog()
    return tuple(DEPARTMENT_CATALOG.values())
