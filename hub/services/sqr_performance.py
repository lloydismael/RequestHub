"""Read-only snapshots of scoped, related-loaded SQR rows; no clock or DB calls."""

from collections.abc import Iterable
from datetime import date, datetime

from hub.models import SqrSubmission


def _progress(value) -> int | None:
    if value is None:
        return None
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def _date(value, label: str, inconsistencies: list[str]) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        inconsistencies.append(f"Invalid {label.lower()}")
        return None


def _owner_name(user) -> str:
    return ((user.get_full_name() or user.username).strip() if user else "") or "—"


def normalize_sqr_project(submission: SqrSubmission, *, today: date) -> dict:
    """Normalize a source row without altering it or inferring recorded health."""
    status = submission.overall_status or "not_set"
    health = submission.delivery_health or "not_set"
    status_label = dict(SqrSubmission.OverallStatus.choices).get(status, status)
    health_label = dict(SqrSubmission.DeliveryHealth.choices).get(health, health)
    if status == "not_set":
        status_label = "Not set"
    if health == "not_set":
        health_label = "Not set"
    progress = _progress(submission.delivery_progress)
    inconsistencies: list[str] = []
    start = _date(submission.delivery_start_date, "Start date", inconsistencies)
    target = _date(submission.delivery_target_finish_date, "Target date", inconsistencies)
    finish = _date(submission.delivery_actual_finish_date, "Actual finish date", inconsistencies)
    signed = _date(submission.delivery_completion_signed_date, "Completion signed date", inconsistencies)

    # Malformed non-null finish evidence must not silently reopen delivery.
    finished = bool(submission.delivery_actual_finish_date or submission.delivery_completion_signed_date)
    completed = status == "completed" or health == "completed" or finished
    cancelled = status == "cancelled" or health == "cancelled" or submission.proposal_status == "closed_canceled"
    operationally_open = not (
        completed or cancelled or submission.proposal_status == "closed_lost"
    )
    delivery_candidate = bool(
        submission.proposal_status == "closed_won"
        or status in SqrSubmission.OverallStatus.values
        or submission.delivery_health
        or submission.delivery_progress is not None
        or submission.delivery_start_date
        or submission.delivery_target_finish_date
        or finished
    )
    missing_data = []
    if delivery_candidate:
        missing_data = [
            label for value, label in (
                (submission.assigned_pm_id, "Assigned PM"),
                (submission.delivery_health, "Delivery health"),
                (progress is not None, "Progress"),
                (start, "Start date"),
                (target, "Target date"),
            ) if not value
        ]
    if start and target and start > target:
        inconsistencies.append("Start date is after target date")
    if start and finish and finish < start:
        inconsistencies.append("Actual finish date is before start date")
    if completed and progress is not None and progress < 100:
        inconsistencies.append("Completed delivery has progress below 100%")
    if operationally_open and progress == 100:
        inconsistencies.append("Open delivery has progress at 100%")
    if completed and cancelled:
        inconsistencies.append("Conflicting completed and cancelled signals")
    explicitly_open = status in {"planning", "in_progress", "on_hold"} or health in {
        "on_track", "at_risk", "off_track",
    }
    if (completed or cancelled) and explicitly_open:
        inconsistencies.append("Terminal delivery conflicts with open status or health")

    days_to_target = (target - today).days if target else None
    overdue = bool(operationally_open and days_to_target is not None and days_to_target < 0)
    due_soon = bool(operationally_open and days_to_target is not None and 0 <= days_to_target <= 14)
    reasons = []
    priority = 6
    for condition, key, label, rank in (
        (operationally_open and health == "off_track", "off_track", "Off Track", 0),
        (overdue, "overdue", "Overdue", 1),
        (operationally_open and health == "at_risk", "at_risk", "At Risk", 2),
        (operationally_open and status == "on_hold", "on_hold", "On Hold", 3),
        (due_soon, "due_soon", "Due within 14 days", 4),
        (missing_data, "missing_data", "Missing delivery data", 5),
        (inconsistencies, "inconsistent_data", "Inconsistent delivery data", 5),
    ):
        if condition:
            reasons.append({"key": key, "label": label})
            priority = min(priority, rank)

    pm = submission.assigned_pm or submission.pm_esg_reviewer
    pm_name = _owner_name(pm)
    sse_name = _owner_name(submission.assigned_sse)
    updates = submission.key_updates_risks or ""
    return {
        "pk": submission.pk,
        "reference_code": submission.reference_code or "—",
        "project_name": submission.project_title or "—",
        "customer_name": submission.customer_name or "",
        "pm_id": str(pm.pk) if pm else "",
        "pm_name": pm_name,
        "pm_fallback": bool(not submission.assigned_pm_id and pm),
        "sse_name": sse_name,
        "status_key": status,
        "status_label": status_label,
        "health_key": health,
        "health_label": health_label,
        "progress": progress,
        "start_date": start.isoformat() if start else None,
        "target_date": target.isoformat() if target else None,
        "actual_finish_date": finish.isoformat() if finish else None,
        "completion_signed_date": signed.isoformat() if signed else None,
        "updates_risks": updates,
        "active_delivery": delivery_candidate and operationally_open,
        "delivery_candidate": delivery_candidate,
        "needs_attention": bool(reasons),
        "overdue": overdue,
        "due_soon": due_soon,
        "days_to_target": days_to_target,
        "missing_data": missing_data,
        "inconsistencies": inconsistencies,
        "reasons": reasons,
        "priority": priority,
        "search_text": " ".join((
            submission.reference_code or "", submission.project_title or "",
            submission.customer_name or "", pm_name, sse_name,
            status_label, health_label, updates,
        )).strip().lower(),
    }


def build_sqr_performance_snapshot(
    submissions: Iterable[SqrSubmission], *, today: date, fetched_at: datetime,
) -> dict:
    """Return the JSON contract in attention/deadline/pk order; time is explicit."""
    projects = [normalize_sqr_project(submission, today=today) for submission in submissions]
    projects.sort(key=lambda project: (
        project["priority"], project["target_date"] or date.max.isoformat(), project["pk"],
    ))
    return {
        "ok": True,
        "as_of": today.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "projects": projects,
    }