from datetime import timedelta


_IMPL_SCOPES = frozenset({
    "Deployment Only", "Deployment and Project Management",
    "Implementation", "Implementation and Project Management",
})
_WARRANTY_SCOPES = _IMPL_SCOPES | {
    "Maintenance", "On-Call Services", "Managed Support and Maintenance Service", "Support",
}
_SCHEDULE_ALIASES = {"on_track": "on_track", "at_risk": "at_risk", "off_track": "delayed"}


def plan_delivery_transition(submission) -> dict:
    """Plan the frozen legacy conversion without mutating a submission."""
    metadata = getattr(submission, "delivery_transition_metadata", None)
    if isinstance(metadata, dict) and metadata.get("version") == 1:
        return {"updates": {}, "provenance": metadata}
    scope = submission.project_details
    signed = submission.delivery_completion_signed_date
    finish = submission.delivery_actual_finish_date
    old_warranty = signed + timedelta(days=30) if signed and scope in _WARRANTY_SCOPES else None
    visible_warranty = old_warranty if scope in _IMPL_SCOPES else None
    eligible = scope in _WARRANTY_SCOPES and submission.managed_support_amount is not None
    old_start = (submission.managed_support_start_date or old_warranty) if eligible else None
    old_end = old_start + timedelta(days=365) if old_start else None
    updates, copied, warnings = {}, [], []
    for field, previous in (("warranty_end_date", visible_warranty),
                            ("managed_support_start_date", old_start),
                            ("managed_support_end_date", old_end)):
        raw = getattr(submission, field)
        if raw is None and previous is not None:
            updates[field] = previous
            copied.append(field)
        elif raw is not None and previous is not None and raw != previous:
            warnings.append("raw_end_differs_from_displayed" if field == "managed_support_end_date"
                            else "raw_warranty_differs_from_displayed")
    schedule = _SCHEDULE_ALIASES.get(submission.delivery_health)
    if schedule and not getattr(submission, "schedule_performance", ""):
        updates["schedule_performance"] = schedule
    if old_warranty and not finish:
        warnings.append("actual_finish_missing_for_legacy_warranty")
    elif old_warranty and old_warranty != finish + timedelta(days=30):
        warnings.append("warranty_source_changed")
    terminal = submission.delivery_health
    if terminal in {"completed", "cancelled"} and submission.overall_status != terminal:
        warnings.append("legacy_terminal_health_conflicts_with_status")
    provenance = {"version": 1, "copied_fields": copied, "warnings": warnings,
                  "previous_visible": {key: value.isoformat() if value else None for key, value in (
                      ("warranty_end", visible_warranty), ("maintenance_start", old_start),
                      ("maintenance_end", old_end))}}
    return {"updates": updates, "provenance": provenance}
