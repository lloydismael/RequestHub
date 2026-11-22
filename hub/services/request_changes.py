"""Request change summaries, assignment capacity, and related activity totals."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Count, FilteredRelation, Q, Sum

from accounts.models import User
from hub.mixins import ASSIGNABLE_ENGINEER_ROLES
from hub.models import Request, StatusLog


def normalize_request_form_changed_fields(changed_fields: Iterable[str]) -> list[str]:
    """Map submitted form field names to the corresponding saved fields."""
    normalized = []
    for field in changed_fields:
        if field == "needed_by":
            normalized.append("start_date")
        elif field != "account_name":
            normalized.append(field)
    return normalized


def summarize_request_changes(original: Request, updated: Request, changed_fields: Iterable[str]) -> str:
    """Describe changed request fields in their supplied order."""
    choice_maps = {
        "priority": dict(Request.Priority.choices),
        "status": dict(Request.Status.choices),
        "engagement_type": dict(Request.Engagement.choices),
    }

    def fmt_user(u: User | None) -> str | None:
        """Prefer the assignee's full name when available."""
        return (u.get_full_name() or u.username) if u else None

    def fmt_date(v: date | None) -> str | None:
        """Format an optional request date for the change summary."""
        return v.strftime("%b %d, %Y") if v else None

    def fmt_choice(field: str, v: str | None) -> str | None:
        """Use the field's display label when it exists."""
        return choice_maps[field].get(v, v) if v else None

    change_summaries = []
    for field in changed_fields:
        old = getattr(original, field, None)
        new = getattr(updated, field, None)
        if old == new:
            continue

        if field == "status":
            label = fmt_choice("status", new) or "Unknown"
            change_summaries.append(f"Marked as {label}")

        elif field == "engineer":
            if new:
                change_summaries.append(f"Assigned engineer: {fmt_user(new)}")
            else:
                change_summaries.append("Engineer unassigned")

        elif field == "backup_engineer":
            if new:
                change_summaries.append(f"Assigned backup engineer: {fmt_user(new)}")
            else:
                change_summaries.append("Backup engineer unassigned")

        elif field == "priority":
            change_summaries.append(f"Priority set to {fmt_choice('priority', new)}")

        elif field == "due_date":
            if new:
                change_summaries.append(f"Due date set to {fmt_date(new)}")
            else:
                change_summaries.append("Due date cleared")

        elif field == "end_date":
            if new:
                change_summaries.append(f"Closed on {fmt_date(new)}")
            else:
                change_summaries.append("End date cleared")

        elif field == "start_date":
            if new:
                change_summaries.append(f"Start date set to {fmt_date(new)}")

        elif field == "account":
            if new:
                change_summaries.append(f"Account changed to {new.name}")

        elif field == "engagement_type":
            change_summaries.append(f"Engagement type set to {fmt_choice('engagement_type', new)}")

        elif field == "product_category":
            if new:
                change_summaries.append(f"Product category set to {new}")

        elif field == "description":
            change_summaries.append("Description updated")

        elif field == "account_manager" and new:
            change_summaries.append(f"Requestor set to {new}")

    return "; ".join(change_summaries)


def create_change_status_log(request_obj: Request, actor_user: User, source_label: str, summary_text: str) -> None:
    """Record an automatic status update for a non-empty change summary."""
    if not summary_text:
        return

    actor_name = actor_user.get_full_name() or actor_user.username
    StatusLog.objects.create(request=request_obj, author=actor_user, message=f"{actor_name} — {summary_text}")


def get_request_activity_log_context(request_obj: Request) -> dict[str, Any]:
    """Collect the activity rows and billable hours related to a request."""
    related_activity_logs = (
        request_obj.activity_logs.select_related("engineer", "account")
        .order_by("-request_date", "-created_at")
    )
    activity_summary = related_activity_logs.aggregate(
        count=Count("pk"),
        total_hours=Sum("actual_hours"),
        billable_hours=Sum("actual_hours", filter=Q(is_billable=True)),
    )
    total_hours = activity_summary.get("total_hours") or Decimal(0)
    billable_hours = activity_summary.get("billable_hours") or Decimal(0)
    return {
        "related_activity_logs": related_activity_logs,
        "related_activity_summary": {
            "count": activity_summary["count"],
            "total_hours": total_hours,
            "billable_hours": billable_hours,
            "non_billable_hours": total_hours - billable_hours,
        },
    }


def build_engineer_capacity_map(request_id: int | None) -> dict[str, dict[str, str | int]]:
    """Calculate engineer loads and limits, excluding the request being edited."""
    active = Q(requests_assigned__status=Request.Status.ONGOING, requests_assigned__is_deleted=False)
    if request_id:
        active &= ~Q(requests_assigned__pk=request_id)
    engineers = (
        User.objects.filter(role__in=ASSIGNABLE_ENGINEER_ROLES)
        .alias(active_requests=FilteredRelation("requests_assigned", condition=active))
        .annotate(
            load=Count("active_requests"),
            deployments=Count("active_requests", filter=Q(active_requests__engagement_type__in=(
                Request.Engagement.DEPLOYMENT, Request.Engagement.CERTIFICATION,
            ))),
        )
        .only("pk", "first_name", "last_name", "username")
    )
    return {
        str(engineer.pk): {
            "name": engineer.get_full_name() or engineer.username or "Engineer",
            "load": engineer.load,
            "capacity": 3 if engineer.deployments else 5,
        }
        for engineer in engineers
    }
