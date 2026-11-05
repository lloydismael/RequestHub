from collections import defaultdict
from typing import TypeAlias

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q

from accounts.models import User
from hub.assistant_knowledge import (
    approved_department_overviews,
    normalize_department,
)
from hub.assistant_types import DepartmentSummary, MemberPage, MemberSummary


class AssistantDirectoryValidationError(ValueError):
    pass


class AssistantDirectoryNotFoundError(LookupError):
    pass


DEPARTMENT_NOT_SPECIFIED_KEY = "department-not-specified"
DEPARTMENT_NOT_SPECIFIED_NAME = "Department not specified"
DIRECTORY_PAGE_SIZE = 20
MAX_DIRECTORY_SEARCH_LENGTH = 100

_DepartmentLabels: TypeAlias = dict[str, int]


def _require_active_user(user: User) -> None:
    if (
        not getattr(user, "is_authenticated", False)
        or isinstance(user, AnonymousUser)
        or not getattr(user, "is_active", False)
    ):
        raise PermissionDenied


def _validate_search_and_page(query: str, page: int) -> None:
    if not isinstance(query, str) or len(query) > MAX_DIRECTORY_SEARCH_LENGTH:
        raise AssistantDirectoryValidationError("Search text must be at most 100 characters.")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise AssistantDirectoryValidationError("Page must be a positive integer.")


def _department_key(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_DIRECTORY_SEARCH_LENGTH:
        raise AssistantDirectoryValidationError("Department key is invalid.")
    normalized = normalize_department(value)
    if normalized in {
        DEPARTMENT_NOT_SPECIFIED_KEY,
        normalize_department(DEPARTMENT_NOT_SPECIFIED_NAME),
    }:
        return DEPARTMENT_NOT_SPECIFIED_KEY
    return normalized


def _department_summaries() -> dict[str, DepartmentSummary]:
    approved = {item.key: item for item in approved_department_overviews()}
    label_groups: dict[str, _DepartmentLabels] = defaultdict(dict)

    rows = (
        User.objects.filter(is_active=True)
        .values("department")
        .annotate(member_count=Count("pk"))
        .order_by("department")
    )
    for row in rows:
        raw_label = row["department"] or ""
        normalized = normalize_department(raw_label)
        if not normalized:
            key = DEPARTMENT_NOT_SPECIFIED_KEY
            display_label = DEPARTMENT_NOT_SPECIFIED_NAME
        else:
            key = normalized
            if key in {
                DEPARTMENT_NOT_SPECIFIED_KEY,
                normalize_department(DEPARTMENT_NOT_SPECIFIED_NAME),
            }:
                raise AssistantDirectoryValidationError(
                    "A registered department conflicts with the reserved department key."
                )
            display_label = raw_label.strip()
        label_groups[key][display_label] = (
            label_groups[key].get(display_label, 0) + row["member_count"]
        )

    summaries = {
        key: DepartmentSummary(
            key=key,
            name=DEPARTMENT_NOT_SPECIFIED_NAME,
            summary=None,
            responsibilities=(),
            member_count=sum(label_counts.values()),
        )
        for key, label_counts in label_groups.items()
        if key == DEPARTMENT_NOT_SPECIFIED_KEY
    }

    for key, label_counts in label_groups.items():
        if key == DEPARTMENT_NOT_SPECIFIED_KEY:
            continue
        published = approved.get(key)
        display_name = min(
            label_counts,
            key=lambda label: (
                -label_counts[label],
                label.casefold(),
                label != label.title(),
                label,
            ),
        )
        summaries[key] = DepartmentSummary(
            key=key,
            name=published.name if published else display_name,
            summary=published.summary if published else None,
            responsibilities=published.responsibilities if published else (),
            member_count=sum(label_counts.values()),
        )

    for key, published in approved.items():
        current = summaries.get(key)
        summaries[key] = DepartmentSummary(
            key=key,
            name=published.name,
            summary=published.summary,
            responsibilities=published.responsibilities,
            member_count=current.member_count if current else 0,
        )

    return summaries


def list_departments(
    user: User,
    *,
    query: str = "",
    page: int = 1,
) -> tuple[tuple[DepartmentSummary, ...], int]:
    _require_active_user(user)
    _validate_search_and_page(query, page)

    normalized_query = query.strip().casefold()
    matching = [
        item
        for item in _department_summaries().values()
        if not normalized_query
        or normalized_query
        in " ".join(
            (
                item.key,
                item.name,
                item.summary or "",
                *item.responsibilities,
            )
        ).casefold()
    ]
    matching.sort(key=lambda item: (item.name.casefold(), item.key))

    start = (page - 1) * DIRECTORY_PAGE_SIZE
    return tuple(matching[start : start + DIRECTORY_PAGE_SIZE]), len(matching)


def get_department_overview(user: User, key: str) -> DepartmentSummary:
    _require_active_user(user)
    normalized_key = _department_key(key)
    department = _department_summaries().get(normalized_key)
    if department is None:
        raise AssistantDirectoryNotFoundError(normalized_key)
    return department


def _department_variants(normalized_key: str) -> tuple[str, ...]:
    labels = (
        User.objects.filter(is_active=True)
        .values_list("department", flat=True)
        .distinct()
    )
    if normalized_key == DEPARTMENT_NOT_SPECIFIED_KEY:
        return tuple(label for label in labels if not normalize_department(label or ""))
    return tuple(
        label
        for label in labels
        if normalize_department(label or "") == normalized_key
    )


def find_department_members(
    user: User,
    *,
    department_key: str,
    query: str = "",
    page: int = 1,
) -> MemberPage:
    _require_active_user(user)
    _validate_search_and_page(query, page)
    normalized_key = _department_key(department_key)
    department = _department_summaries().get(normalized_key)
    if department is None:
        raise AssistantDirectoryNotFoundError(normalized_key)

    variants = _department_variants(normalized_key)
    members = User.objects.filter(is_active=True, department__in=variants)
    for term in query.strip().split():
        members = members.filter(
            Q(first_name__icontains=term) | Q(last_name__icontains=term)
        )

    total = members.count()
    start = (page - 1) * DIRECTORY_PAGE_SIZE
    rows = (
        members.order_by("first_name", "last_name", "pk")
        .values("first_name", "last_name", "role")[start : start + DIRECTORY_PAGE_SIZE]
    )
    department_name = department.name
    items = tuple(
        MemberSummary(
            display_name=" ".join(
                part
                for part in (
                    row["first_name"].strip(),
                    row["last_name"].strip(),
                )
                if part
            )
            or "Unnamed member",
            department=department_name,
            application_role=dict(User.Roles.choices).get(row["role"], row["role"]),
        )
        for row in rows
    )
    return MemberPage(
        items=items,
        page=page,
        page_size=DIRECTORY_PAGE_SIZE,
        total=total,
        department_key=normalized_key,
        query=query.strip(),
    )
