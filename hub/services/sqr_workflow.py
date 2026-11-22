"""SQR quote permissions, inline changes, and history restoration."""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import copy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Literal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.forms import ModelForm
from django.utils import timezone

from accounts.models import User
from hub.form_editor_runtime import configure_form
from hub.mixins import ADMIN_PANEL_ROLES
<<<<<<< HEAD
from hub.models import (
    MANILA_TZ,
    Request,
    SqrSubmission,
    SqrSubmissionChange,
    SqrSubmissionHistory,
)
=======
from hub.models import MANILA_TZ, Request, SqrSubmission, SqrSubmissionChange, SqrSubmissionHistory
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
from hub.services.sqr_maintenance import validate_manual_maintenance_dates


class SqrWorkflowError(Exception):
    """A rejected SQR change with the response status used by its endpoint."""

    def __init__(self, message: str, status: int) -> None:
        """Keep the reason and its response status together at the view boundary."""
        super().__init__(message)
        self.status = status


<<<<<<< HEAD
=======
class InvalidSqrStatusError(SqrWorkflowError):
    """An inline status value outside the supported choices."""

    def __init__(self) -> None:
        """Set the fixed, user-facing validation message and status."""
        super().__init__("Invalid SQR status", 400)


>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
SQR_FILING_QUOTE_FIELDS = frozenset({
    "linked_request", "customer_company", "customer_contact", "pm_esg_reviewer", "customer_name",
    "project_title", "project_details", "sse_manhrs", "sqr_folder_link",
})

SQR_APPROVED_QUOTE_FIELDS = SQR_FILING_QUOTE_FIELDS | {
    "documentation_links", "hourly_rate", "pm_manhrs", "pm_amount", "sse_amount",
    "managed_support_amount", "quotation_total_price", "discount_rate", "validity_due_date",
}

SQR_REVIEW_FIELDS = SQR_APPROVED_QUOTE_FIELDS | {
    "status", "hold_reason", "reviewed_at", "reviewed_by", "review_notes", "proposal_status",
    "po_attachment_link", "po_attached_at", "revenue_unlocked", "revenue_date", "revenue_source",
    "revenue_reference_no", "revenue_status", "revenue_remarks", "revenue_overview", "revenue_declaration",
}

SQR_REOPEN_MESSAGE = "Approved quotation fields are locked. Set SQR status to For Revision before editing them."


def _can_review_sqr(user: User, submission: SqrSubmission) -> bool:
    """Return whether the user is the assigned PM-ESG or an admin."""
    return user.role == User.Roles.ADMIN or (
        user.role == User.Roles.PM_ESG and submission.pm_esg_reviewer_id == user.pk
    )


def _sqr_quote_locked(submission: SqrSubmission, values: Mapping[str, Any] | None = None) -> bool:
    """Test the approval lock against the current or historical quote."""
    values = values or {}
    status = values.get("status", submission.status)
    return status == SqrSubmission.Status.APPROVED or (
        status == SqrSubmission.Status.ON_HOLD and bool(
            values.get("reviewed_at", submission.reviewed_at)
            or values.get("validity_due_date", submission.validity_due_date)
        )
    )


def _sqr_status_choices(submission: SqrSubmission) -> list[str]:
    """Return the available status choices without reopening a locked quote."""
    choices = SqrSubmission.Status.values
    if _sqr_quote_locked(submission):
        return [status for status in choices if status != SqrSubmission.Status.FOR_PROCESSING]
    return choices


SQR_HISTORY_FIELD_LABELS = {
    "customer_name": "Account Name",
    "customer_company": "Group Name",
    "customer_contact": "Account Manager",
    "project_title": "Service Description",
    "project_details": "Scope of Services",
    "linked_request": "RQ ID",
    "sse_manhrs": "SSE Man-hrs",
    "sse_amount": "SSE Amount",
    "pm_manhrs": "PM Man-hrs",
    "pm_amount": "PM Amount",
    "managed_support_amount": "Maintenance Amt.",
    "discount_rate": "Discount Rate",
    "status": "SQR Status",
    "remarks": "Remarks",
    "hold_reason": "Hold Reason",
    "reviewed_at": "Approval Date",
    "reviewed_by": "Reviewed By",
    "review_notes": "Review Notes",
    "validity_due_date": "Validity Due Date",
    "proposal_status": "Proposal Status",
    "po_pnl_date": "PO / PNL Date",
    "assigned_pm": "Assigned PM",
    "assigned_sse": "Assigned SSE",
    "delivery_start_date": "Start Date",
    "delivery_target_finish_date": "Target Finish Date",
    "overall_status": "Overall Status",
    "delivery_health": "Legacy Health Status",
    "schedule_performance": "Schedule Performance",
    "budget_effort_performance": "Budget/Effort Performance",
    "quality_performance": "Quality Performance",
    "managed_support_end_date": "Maintenance End Date",
    "delivery_progress": "Overall Progress %",
    "key_updates_risks": "Key Updates / Risks / Issues",
    "delivery_actual_finish_date": "Actual Finish Date",
    "delivery_completion_signed_date": "Completion Signed Date",
    "warranty_end_date": "Completion Warranty End Date",
    "managed_support_start_date": "Maintenance Start Date",
    "revenue_date": "Billed Date",
    "revenue_source": "Billing Type",
    "revenue_reference_no": "Billing Reference",
    "revenue_status": "Billing Status",
    "revenue_remarks": "PO Remarks",
    "revenue_overview": "Billing Remarks",
    "revenue_declaration": "Revenue Declaration",
}


SQR_HISTORY_CHOICE_LABELS = {
    "status": dict(SqrSubmission.Status.choices),
    "proposal_status": dict(SqrSubmission.ProposalStatus.choices),
    "delivery_health": dict(SqrSubmission.DeliveryHealth.choices),
    "schedule_performance": dict(SqrSubmission.SchedulePerformance.choices),
    "budget_effort_performance": dict(SqrSubmission.BudgetEffortPerformance.choices),
    "quality_performance": dict(SqrSubmission.QualityPerformance.choices),
    "overall_status": dict(SqrSubmission.OverallStatus.choices),
    "revenue_declaration": dict(SqrSubmission.RevenueDeclaration.choices),
    "revenue_status": dict(SqrSubmission.RevenueStatus.choices),
    "revenue_source": {"internal": "Internal", "invoiced": "Invoiced", "unbilled": "Unbilled"},
    "project_details": {
        "Deployment Only": "Deployment Only",
        "On-Call Services": "On-Call Services",
        "Maintenance": "Maintenance",
        "Project Management": "Project Management",
        "Deployment and Project Management": "Deployment and Project Management",
    },
}


def get_sqr_history_field_label(field: str) -> str:
    """Return the staff-facing label for a history field."""
    return SQR_HISTORY_FIELD_LABELS.get(field, (field or "SQR").replace("_", " ").title())


_HISTORY_USER_FIELDS = frozenset({"assigned_pm", "assigned_sse", "reviewed_by"})


def history_display_changes(entries: Sequence[SqrSubmissionHistory]) -> list[list[dict[str, str]]]:
    """Resolve relationship labels in bulk for the displayed history entries."""
    entry_fields = []
    user_ids: set[int] = set()
    request_ids: set[int] = set()
    for entry in entries:
        changed = [key for key in entry.new_values if entry.old_values.get(key) != entry.new_values.get(key)]
        fields = changed or list(entry.new_values) or ([entry.field] if entry.field else [])
        entry_fields.append(fields)
        for field in fields:
            ids = user_ids if field in _HISTORY_USER_FIELDS else request_ids if field == "linked_request" else None
            if ids is not None:
                ids.update(int(values[field]) for values in (entry.old_values, entry.new_values)
                           if values.get(field) not in (None, ""))

    user_labels = {
        user.pk: user.get_full_name() or user.username
        for user in User.objects.filter(pk__in=user_ids).only("pk", "first_name", "last_name", "username")
    }
    request_labels = dict(Request.objects.filter(pk__in=request_ids).values_list("pk", "reference_code"))
    return [
        [
            {
                "field": field,
                "field_label": get_sqr_history_field_label(field),
                "old": format_sqr_history_value(field, entry.old_values.get(field),
                                                user_labels=user_labels, request_labels=request_labels),
                "new": format_sqr_history_value(field, entry.new_values.get(field),
                                                user_labels=user_labels, request_labels=request_labels),
            }
            for field in fields
        ]
        for entry, fields in zip(entries, entry_fields, strict=True)
    ]


def format_sqr_history_value(
    field: str,
    value: Any,
    *,
    user_labels: Mapping[int, str] | None = None,
    request_labels: Mapping[int, str] | None = None,
) -> str:
    """Format a saved value for the SQR audit trail."""
    if value in (None, ""):
        return "—"
    if field in SQR_HISTORY_CHOICE_LABELS:
        return SQR_HISTORY_CHOICE_LABELS[field].get(value, str(value))
    if field in _HISTORY_USER_FIELDS:
        if user_labels is not None:
            return user_labels.get(int(value), str(value))
        user = User.objects.filter(pk=value).first()
        return (user.get_full_name() or user.username) if user else str(value)
    if field == "linked_request":
        if request_labels is not None:
            return request_labels.get(int(value), str(value))
        request_obj = Request.objects.filter(pk=value).first()
        return request_obj.reference_code if request_obj else str(value)
    if field == "discount_rate":
        return f"{value}%" if str(value) != "0" else "No Discount"
    if field.endswith("_amount") or field in {"quotation_total_price", "computed_total_price", "computed_discount_amount"}:
        try:
            amount = Decimal(str(value))
            return f"₱{amount:,.2f}"
        except Exception:
            return str(value)
    return str(value)


def build_sqr_history_summary(field: str, old_values: dict[str, Any], new_values: dict[str, Any], action: str = "updated") -> str:
    """Describe a change to the primary field and related values."""
<<<<<<< HEAD
    changed = [key for key in new_values if old_values.get(key) != new_values.get(key)]
=======
    changed = [key for key in new_values.keys() if old_values.get(key) != new_values.get(key)]
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    primary = field if field in changed else (changed[0] if changed else field)
    label = get_sqr_history_field_label(primary)
    if action == SqrSubmissionHistory.Action.RESTORED:
        return f"Restored {label}."
    if not changed:
        return f"Saved {label}."
    old_display = format_sqr_history_value(primary, old_values.get(primary))
    new_display = format_sqr_history_value(primary, new_values.get(primary))
    extra = len(changed) - 1
    suffix = f" and {extra} related value{'s' if extra != 1 else ''}" if extra > 0 else ""
    return f"Changed {label} from {old_display} to {new_display}{suffix}."


def record_sqr_history(
    submission: SqrSubmission,
    actor: User,
    *,
    field: str,
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    action: str = SqrSubmissionHistory.Action.UPDATED,
    source: str = "inline",
    metadata: dict[str, Any] | None = None,
) -> SqrSubmissionHistory | None:
    """Persist a permanent audit entry only when values differ."""
    if old_values == new_values:
        return None
    return SqrSubmissionHistory.objects.create(
        submission=submission,
        actor=actor,
        action=action,
        field=field or "",
        old_values=old_values or {},
        new_values=new_values or {},
        summary=build_sqr_history_summary(field, old_values or {}, new_values or {}, action),
        source=source,
        metadata=metadata or {},
    )


_ADMIN_ALLOWED = frozenset([
    "customer_name", "customer_company", "customer_contact",
    "project_title", "project_details", "sse_manhrs",
    "pm_manhrs", "discount_rate", "status", "hold_reason", "remarks", "proposal_status",
    "po_pnl_date", "delivery_start_date", "overall_status",
    "schedule_performance", "budget_effort_performance", "quality_performance",
    "delivery_progress", "key_updates_risks", "delivery_actual_finish_date",
    "managed_support_start_date", "managed_support_end_date",
    "revenue_date", "revenue_source", "revenue_reference_no",
    "revenue_status", "revenue_remarks", "revenue_overview", "revenue_declaration",
    "managed_support_amount", "assigned_pm", "assigned_sse", "linked_request",
])
_PM_ESG_ALLOWED = frozenset([
    "project_details", "sse_manhrs", "pm_manhrs", "discount_rate", "status", "hold_reason", "proposal_status",
    "po_pnl_date", "delivery_start_date", "overall_status",
    "schedule_performance", "budget_effort_performance", "quality_performance",
    "delivery_progress", "key_updates_risks", "delivery_actual_finish_date",
    "managed_support_start_date", "managed_support_end_date", "revenue_date", "revenue_source",
    "revenue_reference_no", "revenue_status", "revenue_remarks", "revenue_overview",
    "revenue_declaration", "managed_support_amount", "assigned_pm", "assigned_sse", "linked_request",
])
_DATE_FIELDS = frozenset([
    "po_pnl_date", "delivery_start_date", "delivery_target_finish_date",
    "delivery_actual_finish_date", "delivery_completion_signed_date", "warranty_end_date",
    "managed_support_start_date", "managed_support_end_date", "revenue_date",
])
_INT_FIELDS = frozenset(["discount_rate", "delivery_progress"])
_DECIMAL_FIELDS = frozenset(["sse_manhrs", "pm_manhrs", "managed_support_amount"])
_FK_FIELDS = frozenset(["assigned_pm", "assigned_sse", "linked_request"])
_CLOSED_LOST_LOCKED_FIELDS = frozenset([
    "po_pnl_date", "assigned_pm", "assigned_sse", "delivery_start_date",
    "delivery_target_finish_date", "overall_status", "delivery_health",
    "delivery_progress", "key_updates_risks", "delivery_actual_finish_date",
    "delivery_completion_signed_date", "managed_support_start_date", "managed_support_end_date",
    "schedule_performance", "budget_effort_performance", "quality_performance",
    "revenue_date", "revenue_source", "revenue_reference_no", "revenue_status",
    "revenue_remarks", "revenue_overview", "revenue_declaration",
])
SERIALIZED_FK_FIELDS = _FK_FIELDS | {"reviewed_by", "pm_esg_reviewer"}
_RESTORE_RELATED_FIELDS = {"reviewed_at", "reviewed_by", "validity_due_date", "review_notes", "sse_amount", "pm_amount"}
UNDO_RETENTION = timedelta(hours=24)

# Validity is approval metadata, not a quote content edit.
QUOTE_CONTENT_FIELDS = SQR_APPROVED_QUOTE_FIELDS - {"validity_due_date"}


def allowed_fields_for_user(user: User) -> frozenset[str] | None:
    """Return the inline field vocabulary for the user role."""
    if user.role == User.Roles.ADMIN:
        return _ADMIN_ALLOWED
    if user.role == User.Roles.PM_ESG:
        return _PM_ESG_ALLOWED
    return None


def policy_error(user: User, submission: SqrSubmission, fields: set[str]) -> tuple[str, int] | None:
    """Apply reviewer, quote, and delivery restrictions to a change."""
    if fields & SQR_REVIEW_FIELDS and not _can_review_sqr(user, submission):
        return "Only the assigned reviewer or an admin can edit these fields.", 403
    if _sqr_quote_locked(submission) and fields & SQR_APPROVED_QUOTE_FIELDS:
        return SQR_REOPEN_MESSAGE, 409
    if (submission.proposal_status in (SqrSubmission.ProposalStatus.CLOSED_LOST,
                                       SqrSubmission.ProposalStatus.CLOSED_CANCELED)
            and fields & _CLOSED_LOST_LOCKED_FIELDS):
        return "Delivery and billing fields are locked when Proposal Status is Closed-Lost or Closed-Canceled", 400
    return None


def editable_fields(user: User, submission: SqrSubmission) -> set[str]:
    """Return the fields the actor can currently edit."""
    return {
        field for field in allowed_fields_for_user(user) or ()
        if policy_error(user, submission, {field}) is None
    }


def quote_fingerprint(submission: SqrSubmission) -> str:
    """Fingerprint the quote fields used to validate approval restoration."""
    values = snapshot_values(submission, QUOTE_CONTENT_FIELDS)
    return sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def latest_quote_edit_at(submission: SqrSubmission, *, after: datetime | None = None) -> datetime | None:
    """Find the latest history entry that actually modified quote content."""
    edits = submission.history_entries.filter(new_values__has_any_keys=sorted(QUOTE_CONTENT_FIELDS))
    if after is not None:
        edits = edits.filter(created_at__gt=after)
    snapshots = edits.order_by("-created_at").values_list("created_at", "old_values", "new_values")
    for created_at, old, new in snapshots.iterator(chunk_size=200):
        if any(old.get(field) != new.get(field) for field in QUOTE_CONTENT_FIELDS):
            return created_at
    return None


def restore_policy_error(
    user: User,
    submission: SqrSubmission,
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    created_at: datetime,
    metadata: dict[str, Any],
    *,
    latest_quote_edit: Callable[[], datetime | None] | None = None,
) -> tuple[str, int] | None:
    """Reject stale, locked, or unauthorized attempts to restore a snapshot."""
    fields = set(old_values) | set(new_values)
    changed = {field for field in fields if old_values.get(field) != new_values.get(field)}
    allowed = allowed_fields_for_user(user) or frozenset()
    if changed - (allowed | _RESTORE_RELATED_FIELDS):
        return "You cannot restore one or more fields in this history entry.", 403
    if changed & SQR_REVIEW_FIELDS and not _can_review_sqr(user, submission):
        return "Only the assigned reviewer or an admin can restore these fields.", 403
    # Status snapshots may carry validity as approval metadata, not as a quote edit.
    policy_fields = changed - {"validity_due_date"} if "status" in fields else changed
    if error := policy_error(user, submission, policy_fields):
        return error
    if snapshot_values(submission, new_values) != new_values:
        return "This SQR changed after the selected edit.", 409
    current_locked = _sqr_quote_locked(submission)
    restored_locked = _sqr_quote_locked(submission, old_values)
    if current_locked and not restored_locked:
        return SQR_REOPEN_MESSAGE, 409
    if restored_locked and (not current_locked or "status" in changed):
        if metadata.get("quote_fingerprint") != quote_fingerprint(submission):
            return "The prior approval cannot be restored. Review and approve the current quotation.", 409
        latest = (latest_quote_edit_at(submission, after=created_at)
                  if latest_quote_edit is None else latest_quote_edit())
        if latest is not None and latest > created_at:
            return "The prior approval cannot be restored after a newer quotation edit.", 409
    return None


def tracked_fields_for_change(field: str, save_fields: Iterable[str] | None = None) -> list[str]:
    """Include the derived fields that may change with an inline edit."""
    tracked = set(save_fields or []) | {field}
    if field == "sse_manhrs":
        tracked.update(["sse_amount", "pm_manhrs", "pm_amount"])
    if field == "pm_manhrs":
        tracked.add("pm_amount")
    if field in ("project_details", "customer_company"):
        tracked.add("managed_support_amount")
    if field == "status":
        tracked.update(["reviewed_at", "reviewed_by", "validity_due_date", "review_notes", "assigned_pm", "hold_reason"])
    return sorted(tracked)


def serialize_field_value(submission: SqrSubmission, field: str) -> Any:
    """Serialize an inline field for a response or history snapshot."""
    if field in SERIALIZED_FK_FIELDS:
        value = getattr(submission, f"{field}_id", None)
    else:
        value = getattr(submission, field)
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return format(normalized, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def snapshot_values(submission: SqrSubmission, fields: Iterable[str]) -> dict[str, Any]:
    """Capture the serialized values of the selected SQR fields."""
    return {field: serialize_field_value(submission, field) for field in fields}


def apply_serialized_field_value(submission: SqrSubmission, field: str, value: Any) -> None:
    """Restore a serialized field to its model value without saving."""
    if field in _DATE_FIELDS or field == "validity_due_date":
        restored = date.fromisoformat(value) if value else None
    elif field == "reviewed_at":
        restored = datetime.fromisoformat(value) if value else None
    elif field in _INT_FIELDS:
        restored = int(value) if value not in ("", None) else None
        if field == "discount_rate" and restored is None:
            restored = 0
    elif field in _DECIMAL_FIELDS or field in ("sse_amount", "pm_amount"):
        restored = Decimal(str(value)) if value not in ("", None) else None
    else:
        restored = value

    if field in SERIALIZED_FK_FIELDS:
        setattr(submission, f"{field}_id", restored)
    else:
        setattr(submission, field, restored)


@transaction.atomic
def save_inline_change(
    pk: int,
    actor: User,
    field: str,
    value: Any,
    *,
    review_notes: Any = None,
    hold_reason: Any = None,
    lookup: Callable[[QuerySet[SqrSubmission], int], SqrSubmission] | None = None,
) -> tuple[SqrSubmission, str, str, list[str], SqrSubmissionChange | None]:
    """Apply an inline update under a row lock; callers can supply their own lookup handler."""
    allowed = allowed_fields_for_user(actor)
    if allowed is None:
        raise SqrWorkflowError("Permission denied", 403)
    if not isinstance(field, str) or field not in allowed:
        raise SqrWorkflowError("Field not allowed", 400)

    locked_submissions = SqrSubmission.objects.select_for_update()
    submission = lookup(locked_submissions, pk) if lookup is not None else locked_submissions.get(pk=pk)
    policy_failure = policy_error(actor, submission, {field})
    if policy_failure:
        error, status = policy_failure
        raise SqrWorkflowError(error, status)

    if field == "status":
        if not isinstance(value, str) or value not in SqrSubmission.Status.values:
<<<<<<< HEAD
            raise SqrWorkflowError("Invalid SQR status", 400)
=======
            raise InvalidSqrStatusError()
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
        if value not in _sqr_status_choices(submission):
            raise SqrWorkflowError(SQR_REOPEN_MESSAGE, 409)
        if value == SqrSubmission.Status.ON_HOLD:
            if not isinstance(hold_reason, str) or not hold_reason.strip():
                raise SqrWorkflowError("Hold Reason is required when status is On Hold.", 400)
            hold_reason = hold_reason.strip()

    old_status = submission.status
    original_submission = copy(submission)

    if field == "hold_reason":
        if not isinstance(value, str):
            raise SqrWorkflowError("Invalid Hold Reason", 400)
        value = value.strip()
        if submission.status == SqrSubmission.Status.ON_HOLD and not value:
            raise SqrWorkflowError("Hold Reason is required when status is On Hold.", 400)

    try:
        if field in _INT_FIELDS or field in _FK_FIELDS:
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
        if field in _DATE_FIELDS:
            coerced = date.fromisoformat(value) if value else None
        elif field in _INT_FIELDS:
            coerced = int(value) if value not in ("", None) else None
            if field == "discount_rate" and coerced is None:
                coerced = 0
            if field == "delivery_progress" and coerced is not None:
                coerced = max(0, min(100, coerced))
        elif field in _DECIMAL_FIELDS:
            coerced = Decimal(str(value)) if value not in ("", None) else None
        elif field in _FK_FIELDS:
            coerced = int(value) if value not in ("", None) else None
        else:
            coerced = value
        coerced = submission._meta.get_field(field).clean(coerced, submission)
    except (ValueError, TypeError, OverflowError, InvalidOperation, ValidationError) as exc:
        raise SqrWorkflowError(f"Invalid value for {field}", 400) from exc

    # For FK fields, set the _id attribute directly
    if field in _FK_FIELDS:
        setattr(submission, field + "_id", coerced)
    else:
        setattr(submission, field, coerced)
    save_fields = [field]
    if field in {"managed_support_start_date", "managed_support_end_date"} and coerced != getattr(original_submission, field):
        try:
            validate_manual_maintenance_dates(submission.managed_support_start_date, submission.managed_support_end_date)
        except ValidationError as exc:
            raise SqrWorkflowError("Invalid Maintenance date pair", 400) from exc

    # Auto-recompute PM Amount (col O = col N × 3000) when pm_manhrs changes
    if field == "pm_manhrs":
        submission.pm_amount = (
<<<<<<< HEAD
            (Decimal(str(coerced)) * Decimal(3000)).quantize(Decimal("0.01"))
=======
            (Decimal(str(coerced)) * Decimal("3000")).quantize(Decimal("0.01"))
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
            if coerced is not None else None
        )
        save_fields.append("pm_amount")

    # Auto-recompute SSE Amount (col M = col L × 2000) when sse_manhrs changes
    if field == "sse_manhrs":
        submission.sse_amount = (
<<<<<<< HEAD
            (Decimal(str(coerced)) * Decimal(2000)).quantize(Decimal("0.01"))
=======
            (Decimal(str(coerced)) * Decimal("2000")).quantize(Decimal("0.01"))
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
            if coerced is not None else None
        )
        submission.pm_manhrs = submission.recommended_pm_manhrs_for_sse(coerced)
        submission.pm_amount = (
<<<<<<< HEAD
            (Decimal(str(submission.pm_manhrs)) * Decimal(3000)).quantize(Decimal("0.01"))
=======
            (Decimal(str(submission.pm_manhrs)) * Decimal("3000")).quantize(Decimal("0.01"))
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
            if submission.pm_manhrs is not None else None
        )
        save_fields.extend(["sse_amount", "pm_manhrs", "pm_amount"])

    if field in ("project_details", "customer_company"):
        scope = coerced if field == "project_details" else submission.project_details
        group = coerced if field == "customer_company" else submission.customer_company
        submission.managed_support_amount = managed_support_amount(scope, group)
        save_fields.append("managed_support_amount")

    if field == "status":
        new_approval = (coerced == SqrSubmission.Status.APPROVED and old_status != coerced
                        and (old_status != SqrSubmission.Status.ON_HOLD or not submission.reviewed_at))
        undated_approval = (coerced == SqrSubmission.Status.ON_HOLD and old_status == SqrSubmission.Status.APPROVED
                            and not (submission.reviewed_at or submission.validity_due_date))
        if new_approval or undated_approval:
            # Preserve the lock when pausing a legacy approval with no approval dates.
            submission.reviewed_at = timezone.now()
            submission.validity_due_date = timezone.localtime(submission.reviewed_at, MANILA_TZ).date() + timedelta(days=45)
            submission.reviewed_by = actor
            save_fields += ["reviewed_at", "reviewed_by", "validity_due_date"]
        if coerced == SqrSubmission.Status.APPROVED and old_status != coerced:
            if not submission.assigned_pm_id:
                submission.assigned_pm_id = submission.pm_esg_reviewer_id
                save_fields.append("assigned_pm")
        elif coerced == SqrSubmission.Status.FOR_REVISION:
            if review_notes is not None and not isinstance(review_notes, str):
                raise SqrWorkflowError("Invalid review notes", 400)
            submission.reviewed_at = None
            submission.validity_due_date = None
            submission.reviewed_by = actor
            save_fields += ["reviewed_at", "reviewed_by", "validity_due_date"]
            if review_notes is not None or old_status != coerced:
                submission.review_notes = review_notes or ""
                save_fields.append("review_notes")
        elif coerced == SqrSubmission.Status.FOR_PROCESSING:
            submission.reviewed_at = None
            submission.validity_due_date = None
            submission.reviewed_by = None
            save_fields += ["reviewed_at", "reviewed_by", "validity_due_date"]
        elif coerced == SqrSubmission.Status.ON_HOLD:
            submission.hold_reason = hold_reason
            save_fields.append("hold_reason")

    try:
        for saved_field in save_fields[1:]:
            model_field = submission._meta.get_field(saved_field)
            cleaned = model_field.clean(getattr(submission, model_field.attname), submission)
            setattr(submission, model_field.attname, cleaned)
    except ValidationError as exc:
        raise SqrWorkflowError(f"Invalid value for {field}", 400) from exc

    tracked_fields = tracked_fields_for_change(field, save_fields)
    old_values = snapshot_values(original_submission, tracked_fields)
    submission.save(update_fields=save_fields)
    new_values = snapshot_values(submission, tracked_fields)
    change = None
    if old_values != new_values:
        change = SqrSubmissionChange.objects.create(
            submission=submission, changed_by=actor, field=field,
            old_values=old_values, new_values=new_values, expires_at=timezone.now() + UNDO_RETENTION,
        )
        metadata = {"change_id": change.pk}
        if field == "status" and _sqr_quote_locked(original_submission):
            metadata["quote_fingerprint"] = quote_fingerprint(original_submission)
        record_sqr_history(
            submission, actor, field=field, old_values=old_values, new_values=new_values,
            source="inline", metadata=metadata,
        )
    return submission, field, old_status, tracked_fields, change


def restore_snapshot(submission: SqrSubmission, values: Mapping[str, Any]) -> list[str]:
    """Validate and write a snapshot under the caller's lock without calling model save."""
    restore_fields = list(values)
    saved_dates = (submission.managed_support_start_date, submission.managed_support_end_date)
    for field, value in values.items():
        apply_serialized_field_value(submission, field, value)
    restored_dates = (submission.managed_support_start_date, submission.managed_support_end_date)
    if restored_dates != saved_dates:
        try:
            validate_manual_maintenance_dates(*restored_dates)
        except ValidationError as exc:
            raise SqrWorkflowError("Invalid Maintenance date pair", 400) from exc
    update_values = {}
    for field in restore_fields:
        name = f"{field}_id" if field in SERIALIZED_FK_FIELDS else field
        update_values[name] = getattr(submission, name)
    SqrSubmission.objects.filter(pk=submission.pk).update(**update_values)
    return restore_fields


def undo_inline_change(submission: SqrSubmission, change: SqrSubmissionChange, actor: User) -> list[str]:
    """Restore an already authorized inline change and persist its undo audit."""
    restore_fields = restore_snapshot(submission, change.old_values)
    change.undone_at = timezone.now()
    change.undone_by = actor
    change.save(update_fields=["undone_at", "undone_by"])
    if set(restore_fields) & QUOTE_CONTENT_FIELDS:
        record_sqr_history(
            submission, actor, field=change.field,
            old_values=change.new_values, new_values=change.old_values,
            action=SqrSubmissionHistory.Action.RESTORED, source="inline_undo",
            metadata={"change_id": change.pk},
        )
    return restore_fields


def restore_history_entry(
    submission: SqrSubmission, history: SqrSubmissionHistory, actor: User,
) -> SqrSubmissionHistory | None:
    """Restore an authorized history entry and record the reversal."""
    restore_snapshot(submission, history.old_values)
    history.restored_at = timezone.now()
    history.restored_by = actor
    history.save(update_fields=["restored_at", "restored_by"])
    return record_sqr_history(
        submission,
        actor,
        field=history.field,
        old_values=history.new_values,
        new_values=history.old_values,
        action=SqrSubmissionHistory.Action.RESTORED,
        source="history_restore",
        metadata={"restored_history_id": history.pk},
    )


def save_new_submission(
    form: ModelForm, actor: User, *, save_configured: Callable[..., SqrSubmission],
) -> SqrSubmission | None:
    """Within the caller's transaction, file only if the linked request is still assigned."""
    linked_request = form.cleaned_data.get("linked_request")
    if linked_request:
        assigned_request = Request.objects.select_for_update().filter(
            pk=linked_request.pk, engineer=actor,
        ).first()
        if assigned_request is None:
            form.add_error("linked_request", "This request is no longer assigned to you.")
            return None

    submission = form.save(commit=False)
    submission.engineer = actor
    submission.status = SqrSubmission.Status.FOR_PROCESSING
    scope = form.cleaned_data.get("project_details", "") or ""
    group = form.cleaned_data.get("customer_company", "") or ""
    submission.managed_support_amount = managed_support_amount(scope, group)
    save_configured(form, submission)
    return submission


def save_engineer_edit(
    form: ModelForm,
    original: SqrSubmission,
    actor: User,
    *,
    save_configured: Callable[..., SqrSubmission],
) -> SqrSubmission:
    """Save filing changes or remarks only when the quote is locked."""
    submission = form.save(commit=False)
    if _sqr_quote_locked(original):
        # Cleaning can normalize disabled fields; keep the stored values.
        submission = copy(original)
        submission.remarks = form.cleaned_data["remarks"]
        fields = ["remarks"]
    else:
        fields = [*form._meta.fields, "pm_manhrs", "pm_amount", "sse_amount"]
    before = snapshot_values(original, fields)
    save_configured(form, submission, update_fields=[*fields, "updated_at"])
    after = snapshot_values(submission, fields)
    changed = [field for field in fields if before[field] != after[field]]
    if changed:
        record_sqr_history(
            submission, actor, field=changed[0] if len(changed) == 1 else "",
            old_values={field: before[field] for field in changed},
            new_values={field: after[field] for field in changed}, source="engineer_edit",
        )
    return submission


def save_quotation_pricing(
    submission: SqrSubmission,
    form: ModelForm,
    actor: User,
    fields: Sequence[str],
    *,
    save_configured: Callable[..., SqrSubmission],
) -> bool:
    """Validate an unlocked quotation and record the resulting pricing edit."""
    before = snapshot_values(submission, fields)
    if not form.is_valid():
        return False
    save_configured(form, form.save(commit=False), update_fields=[*fields, "updated_at"])
    record_sqr_history(
        submission, actor, field="quotation_total_price", old_values=before,
        new_values=snapshot_values(submission, fields), source="revenue_quotation",
    )
    return True


def attach_purchase_order(
    form: ModelForm,
    fields: Sequence[str],
    *,
    save_configured: Callable[..., SqrSubmission],
) -> None:
    """Attach the validated order and stamp the transition to Revenue."""
    submission = form.save(commit=False)
    if not submission.quotation_total_price:
        raise SqrWorkflowError("Set the quotation pricing before attaching a P.O.", 400)
    submission.po_attached_at = timezone.now()
    save_configured(form, submission, update_fields=[*fields, "po_attached_at", "updated_at"])


def approved_stage_error(actor: User, submission: SqrSubmission) -> Literal["reviewer", "approval"] | None:
    """Identify the first reason a reviewer cannot change an approved stage."""
    if not _can_review_sqr(actor, submission):
        return "reviewer"
    if submission.status != SqrSubmission.Status.APPROVED:
        return "approval"
    return None


def configure_proposal_form(form: ModelForm) -> None:
    """Keep saved quotation values available to conditions without allowing edits."""
    quote_fields = SQR_APPROVED_QUOTE_FIELDS.intersection(form.fields)
    configure_form(form, read_only_fields=quote_fields)
    for field in quote_fields:
        form.fields.pop(field)


def unlock_revenue_stage(submission: SqrSubmission) -> bool:
    """Unlock Revenue once, returning whether a change was made."""
    if submission.revenue_unlocked:
        return False
    submission.revenue_unlocked = True
    submission.save(update_fields=["revenue_unlocked", "updated_at"])
    return True


def delivery_is_locked(submission: SqrSubmission) -> bool:
    """Prevent delivery edits after a deal is lost or canceled."""
    return submission.proposal_status in (
        SqrSubmission.ProposalStatus.CLOSED_LOST, SqrSubmission.ProposalStatus.CLOSED_CANCELED,
    )


def save_delivery_update(
    submission: SqrSubmission,
    form: ModelForm,
    actor: User,
    fields: Sequence[str],
    *,
    save_configured: Callable[..., SqrSubmission],
) -> bool:
    """Apply a validated delivery edit and record the undo and history details."""
    before = snapshot_values(submission, fields)
    if not form.is_valid():
        return False
    save_configured(form, form.save(commit=False), update_fields=[*fields, "updated_at"])
    after = snapshot_values(submission, fields)
    changed = [field for field in fields if before[field] != after[field]]
    if changed:
        old_values = {field: before[field] for field in changed}
        new_values = {field: after[field] for field in changed}
        change = SqrSubmissionChange.objects.create(
            submission=submission, changed_by=actor, field=changed[0],
            old_values=old_values, new_values=new_values, expires_at=timezone.now() + UNDO_RETENTION,
        )
        record_sqr_history(
            submission, actor, field=changed[0], old_values=old_values, new_values=new_values,
            source="delivery", metadata={"change_id": change.pk},
        )
    return True


MANAGED_SCOPES = frozenset([
    "Deployment Only",
    "Deployment and Project Management",
    "Maintenance",
    "On-Call Services",
    "Implementation",
    "Implementation and Project Management",
    "Managed Support and Maintenance Service",
])


def managed_support_amount(
    scope: str, group: str, *, scopes: frozenset[str] = MANAGED_SCOPES,
) -> Decimal | None:
    """Apply the existing managed support pricing for the selected scope and group."""
    if scope not in scopes:
        return None
    return Decimal("149000.00") if group == "ESS" else Decimal("192000.00")


def approval_advisory_error(user: User, submission: SqrSubmission) -> str:
    """Return the first reason an approval advisory cannot be drafted."""
    if user.role not in ADMIN_PANEL_ROLES:
        return "Only PM-ESG or Admin can draft SQR approval advisory emails."
    if not _can_review_sqr(user, submission):
        return "Only the assigned PM-ESG approver can draft this approval advisory email."
    if submission.status != SqrSubmission.Status.APPROVED:
        return "Set SQR status to Approved before drafting the advisory email."
    requestor_email = submission.engineer.email if submission.engineer and submission.engineer.email else None
    if not requestor_email:
        return "Unable to draft an email. Ensure the requestor (engineer) has an email configured."
    return ""
