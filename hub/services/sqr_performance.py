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
    """Normalize recorded assessments and lifecycle without altering the source."""
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
        or health in {"completed", "cancelled"}
        or submission.schedule_performance
        or submission.budget_effort_performance
        or submission.quality_performance
        or submission.delivery_progress is not None
        or submission.delivery_start_date
        or finished
    )
    missing_data = []
    if delivery_candidate:
        missing_data = [
            label for value, label in (
                (submission.assigned_pm_id, "Assigned PM"),
                (progress is not None, "Progress"),
                (start, "Start date"),
            ) if not value
        ]
    if start and finish and finish < start:
        inconsistencies.append("Actual finish date is before start date")
    if completed and progress is not None and progress < 100:
        inconsistencies.append("Completed delivery has progress below 100%")
    if operationally_open and progress == 100:
        inconsistencies.append("Open delivery has progress at 100%")
    if completed and cancelled:
        inconsistencies.append("Conflicting completed and cancelled signals")
    explicitly_open = status in {"planning", "in_progress", "on_hold"}
    if (completed or cancelled) and explicitly_open:
        inconsistencies.append("Terminal delivery conflicts with open status or health")

    pm = submission.assigned_pm or submission.pm_esg_reviewer
    pm_name = _owner_name(pm)
    sse_name = _owner_name(submission.assigned_sse)
    updates = submission.key_updates_risks or ""
    if cancelled or submission.proposal_status == "closed_lost":
        portfolio_status = "removed"
    elif completed:
        portfolio_status = "closed"
    elif status == "planning" or not delivery_candidate:
        portfolio_status = "pipeline"
    else:
        portfolio_status = "ongoing"

    definitions = (
        ("schedule", "schedule_performance", "Schedule Performance", SqrSubmission.SchedulePerformance,
         {"on_track": "good", "at_risk": "warning", "delayed": "danger"}),
        ("budget_effort", "budget_effort_performance", "Budget/Effort Performance", SqrSubmission.BudgetEffortPerformance,
         {"within_budget": "good", "at_risk": "warning", "over_budget": "danger"}),
        ("quality", "quality_performance", "Quality Performance", SqrSubmission.QualityPerformance,
         {"met": "good", "at_risk": "warning", "unmet": "danger"}),
    )
    indicators = {}
    for key, field, label, choices, tones in definitions:
        raw = getattr(submission, field) or ""
        value = raw if raw in tones else ""
        if raw and not value:
            inconsistencies.append(f"Invalid {label.lower()}")
        indicators[key] = {"value": value, "label": dict(choices.choices).get(value, "N/R"),
                           "state": tones.get(value, "unknown")}
        if delivery_candidate and not value:
            missing_data.append(label)
    states = {indicator["state"] for indicator in indicators.values()}
    rateable = portfolio_status in {"ongoing", "closed"}
    overall_rating = "unknown"
    if rateable:
        if "danger" in states:
            overall_rating = "danger"
        elif "warning" in states:
            overall_rating = "warning"
        elif states == {"good"}:
            overall_rating = "good"

    reasons = []
    priority = 6
    for key, field, label, _choices, _tones in definitions:
        indicator = indicators[key]
        if rateable and indicator["state"] in {"warning", "danger"}:
            reasons.append({"key": field, "label": f"{label}: {indicator['label']}"})
            priority = min(priority, 0 if indicator["state"] == "danger" else 1)
    for condition, key, label, rank in (
        (submission.status == "for_revision", "revision", "Revision requested", 0),
        (submission.status == "submitted", "awaiting", "Awaiting SQR approval", 3),
        (operationally_open and status == "on_hold", "on_hold", "On Hold", 2),
        (inconsistencies, "inconsistent_data", "Inconsistent delivery data", 4),
        (missing_data, "missing_data", "Missing delivery data", 5),
    ):
        if condition:
            reasons.append({"key": key, "label": label})
            priority = min(priority, rank)
    return {
        "pk": submission.pk,
        "reference_code": submission.reference_code or "—",
        "project_name": submission.project_title or "—",
        "customer_name": submission.customer_name or "",
        "pm_id": str(pm.pk) if pm else "",
        "pm_name": pm_name,
        "pm_fallback": bool(not submission.assigned_pm_id and pm),
        "approver_id": str(submission.pm_esg_reviewer_id or ""),
        "sse_name": sse_name,
        "status_key": status,
        "status_label": status_label,
        "health_key": health,
        "health_label": health_label,
        "progress": progress,
        "start_date": start.isoformat() if start else None,
        "target_date": None,
        "actual_finish_date": finish.isoformat() if finish else None,
        "completion_signed_date": signed.isoformat() if signed else None,
        "updates_risks": updates,
        "active_delivery": delivery_candidate and operationally_open,
        "delivery_candidate": delivery_candidate,
        "needs_attention": bool(reasons),
        "overdue": False,
        "due_soon": False,
        "days_to_target": None,
        "missing_data": missing_data,
        "missing_assessment": any(not indicator["value"] for indicator in indicators.values()),
        "inconsistencies": inconsistencies,
        "reasons": reasons,
        "priority": priority,
        "portfolio_status": portfolio_status,
        "indicators": indicators,
        "overall_rating": overall_rating,
        "search_text": " ".join((
            submission.reference_code or "", submission.project_title or "",
            submission.customer_name or "", pm_name, sse_name,
            status_label, *(indicator["label"] for indicator in indicators.values()), updates,
        )).strip().lower(),
    }


def build_sqr_performance_snapshot(
    submissions: Iterable[SqrSubmission], *, today: date, fetched_at: datetime,
) -> dict:
    """Return the JSON contract in stable attention/PK order; time is explicit."""
    projects = [normalize_sqr_project(submission, today=today) for submission in submissions]
    projects.sort(key=lambda project: (project["priority"], project["pk"]))
    return {
        "ok": True,
        "as_of": today.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "projects": projects,
    }