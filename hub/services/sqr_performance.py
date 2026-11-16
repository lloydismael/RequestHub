"""Read-only snapshots of scoped, related-loaded SQR rows; no clock or DB calls."""

from collections.abc import Iterable
from datetime import date, datetime

from hub.models import SqrSubmission


def build_assessment_snapshot(submission: SqrSubmission) -> dict:
    indicators = {}
    inconsistencies = []
    assessed_count = 0
    for field_name, choices in (
        ("schedule_performance", SqrSubmission.SchedulePerformance),
        ("budget_effort_performance", SqrSubmission.BudgetEffortPerformance),
        ("quality_performance", SqrSubmission.QualityPerformance),
    ):
        value = getattr(submission, field_name)
        if value in choices.values:
            index = choices.values.index(value)
            indicators[field_name] = {
                "key": value, "label": choices.labels[index],
                "state": ("good", "warning", "danger")[index],
            }
            assessed_count += 1
        else:
            indicators[field_name] = {"key": "", "label": "Not assessed", "state": "unknown"}
            if value:
                inconsistencies.append(f"Invalid {field_name} assessment.")
    states = {indicator["state"] for indicator in indicators.values()}
    if "danger" in states:
        rating, label = "danger", "Action needed"
    elif "warning" in states:
        rating, label = "warning", "At Risk"
    elif assessed_count == 3:
        rating, label = "good", "Healthy"
    elif assessed_count:
        rating, label = "unknown", "Incomplete assessment"
    else:
        rating, label = "unknown", "Not assessed"
    return {
        "indicators": indicators, "overall_rating": rating, "overall_label": label,
        "assessed_count": assessed_count, "assessment_total": 3,
        "inconsistencies": inconsistencies,
    }


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
    if status == "not_set":
        status_label = "Not set"
    assessment = build_assessment_snapshot(submission)
    progress = _progress(submission.delivery_progress)
    inconsistencies = list(assessment["inconsistencies"])
    start = _date(submission.delivery_start_date, "Start date", inconsistencies)
    finish = _date(submission.delivery_actual_finish_date, "Actual finish date", inconsistencies)
    maintenance_start = _date(submission.computed_support_start_date, "Maintenance start date", inconsistencies)
    maintenance_end = _date(submission.computed_managed_support_end_date, "Maintenance end date", inconsistencies)

    # Malformed non-null finish evidence must not silently reopen delivery.
    finished = bool(submission.delivery_actual_finish_date or submission.legacy_delivery_completed)
    completed = status == "completed" or health == "completed" or finished
    cancelled = status == "cancelled" or health == "cancelled" or submission.proposal_status == "closed_canceled"
    operationally_open = not (
        completed or cancelled or submission.proposal_status == "closed_lost"
    )
    delivery_candidate = bool(
        submission.proposal_status == "closed_won"
        or status in SqrSubmission.OverallStatus.values
        or health in {"completed", "cancelled"}
        or any(getattr(submission, key) for key in assessment["indicators"])
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
                *((indicator["key"], label) for indicator, label in zip(
                    assessment["indicators"].values(),
                    ("Schedule Performance", "Budget/Effort Performance", "Quality Performance"),
                )),
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
    if (completed or cancelled) and status in {"planning", "in_progress", "on_hold"}:
        inconsistencies.append("Terminal delivery conflicts with open status")

    reasons = []
    priority = 6
    for state, rank in (("danger", 0), ("warning", 2)):
        for key, indicator in assessment["indicators"].items():
            if indicator["state"] == state:
                reasons.append({"key": key, "label": indicator["label"]})
                priority = min(priority, rank)
    for condition, key, label, rank in (
        (operationally_open and status == "on_hold", "on_hold", "On Hold", 3),
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
    if cancelled or submission.proposal_status == "closed_lost":
        portfolio_status = "removed"
    elif completed:
        portfolio_status = "closed"
    elif status == "planning" or not delivery_candidate:
        portfolio_status = "pipeline"
    else:
        portfolio_status = "ongoing"

    # Reuse tracker stage/billing semantics without introducing a module import cycle.
    from .sqr_prototype import derive_stage, revenue_state

    stage, _ = derive_stage(submission)
    warranty = submission.computed_post_svc_warranty_end_date if isinstance(
        submission.delivery_actual_finish_date, date
    ) else None
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
        "status": submission.status,
        "overall_status": submission.overall_status or "",
        "proposal_status": submission.proposal_status or "",
        "approver_id": submission.pm_esg_reviewer_id,
        "sse_id": str(submission.assigned_sse_id or ""),
        "scope": submission.project_details or "",
        "group": submission.customer_company or "",
        "billing_type": submission.revenue_source or "",
        "billing_state": revenue_state(submission),
        "linked_request_id": str(submission.linked_request_id or ""),
        "stage": stage,
        "total": str(submission.computed_total_price or submission.quotation_total_price or 0),
        **{key: indicator["key"] for key, indicator in assessment["indicators"].items()},
        "progress": progress,
        "start_date": start.isoformat() if start else None,
        "actual_finish_date": finish.isoformat() if finish else None,
        "delivery_start_date": start.isoformat() if start else None,
        "delivery_actual_finish_date": finish.isoformat() if finish else None,
        "post_svc_warranty_end_date": warranty.isoformat() if warranty else None,
        "managed_support_start_date": maintenance_start.isoformat() if maintenance_start else None,
        "managed_support_end_date": maintenance_end.isoformat() if maintenance_end else None,
        "updates_risks": updates,
        "active_delivery": delivery_candidate and operationally_open,
        "delivery_candidate": delivery_candidate,
        "needs_attention": bool(reasons),
        "attention": [{**reason, "tone": "warning"} for reason in reasons],
        "missing_data": missing_data,
        "inconsistencies": inconsistencies,
        "reasons": reasons,
        "priority": priority,
        "portfolio_status": portfolio_status,
        "indicators": assessment["indicators"],
        "overall_rating": assessment["overall_rating"],
        "overall_label": assessment["overall_label"],
        "assessed_count": assessment["assessed_count"],
        "assessment_total": assessment["assessment_total"],
        "search_text": " ".join((
            submission.reference_code or "", submission.project_title or "",
            submission.customer_name or "", pm_name, sse_name,
            status_label, updates,
            *(indicator["label"] for indicator in assessment["indicators"].values()),
        )).strip().lower(),
    }


def build_sqr_performance_snapshot(
    submissions: Iterable[SqrSubmission], *, today: date, fetched_at: datetime,
) -> dict:
    """Return the JSON contract in attention/project/pk order; time is explicit."""
    projects = [normalize_sqr_project(submission, today=today) for submission in submissions]
    projects.sort(key=lambda project: (
        project["priority"], project["project_name"].casefold(), project["pk"],
    ))
    return {
        "ok": True,
        "as_of": today.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "projects": projects,
    }