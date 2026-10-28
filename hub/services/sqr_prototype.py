"""Data contract for the SQR Tracker Prototype view.

Pure functions only: the view passes in the viewer's allowed fields so this
module never imports views (and stays trivially unit-testable).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.urls import reverse

from ..models import SqrSubmission

Status = SqrSubmission.Status
Proposal = SqrSubmission.ProposalStatus
Overall = SqrSubmission.OverallStatus
Health = SqrSubmission.DeliveryHealth
Billing = SqrSubmission.RevenueStatus

STAGES = (
    ("opportunity", "Opportunity"),
    ("delivery", "Delivery"),
    ("billing", "Billing"),
    ("closed", "Closed"),
)

TABS = (
    ("opportunity", "Opportunity"),
    ("delivery", "Delivery"),
    ("billing", "Billing"),
)

SCOPE_CHOICES = [
    ["Deployment Only", "Deployment Only"],
    ["On-Call Services", "On-Call Services"],
    ["Maintenance", "Maintenance"],
    ["Project Management", "Project Management"],
    ["Deployment and Project Management", "Deployment and Project Management"],
]
GROUP_CHOICES = [[value, value] for value in ("ESS", "HP", "Dell", "ENS", "ESG", "ISG", "Other")]
BILLING_TYPE_CHOICES = [["internal", "Internal"], ["invoiced", "Invoiced"], ["unbilled", "Unbilled"]]

VALIDITY_WARNING_DAYS = 7


def _choices(enum) -> list[list[str]]:
    return [[value, label] for value, label in enum.choices]


def _field(key, label, kind, tab, section, **extra):
    spec = {"key": key, "label": label, "type": kind, "tab": tab, "section": section}
    spec.update(extra)
    return spec


# type: text | textarea | number | date | select | user | request | link | readonly | computed
# computed/readonly fields are never editable; `format` tells the client how to render them.
FIELD_SPECS = [
    _field("customer_name", "Account name", "text", "opportunity", "Request details"),
    _field("project_title", "Service description", "text", "opportunity", "Request details"),
    _field("project_details", "Scope of services", "select", "opportunity", "Request details",
           choices=SCOPE_CHOICES),
    _field("customer_company", "Group name", "select", "opportunity", "Request details",
           choices=GROUP_CHOICES),
    _field("customer_contact", "Account manager", "text", "opportunity", "Request details"),
    _field("linked_request", "RQ ID", "request", "opportunity", "Request details"),
    _field("requester", "Requester", "readonly", "opportunity", "Request details"),
    _field("approver", "Approver", "readonly", "opportunity", "Request details"),
    _field("sqr_folder_link", "SQR document link", "link", "opportunity", "Request details"),
    _field("remarks", "Remarks", "readonly", "opportunity", "Request details",
           multiline=True, hint="Optional SSE filing identifier. Edit through Edit submission."),

    _field("sse_manhrs", "SSE man-hours", "number", "opportunity", "Pricing", unit="hrs", min=0),
    _field("sse_amount", "SSE amount", "computed", "opportunity", "Pricing", format="money"),
    _field("pm_manhrs", "PM man-hours", "number", "opportunity", "Pricing", unit="hrs", min=0),
    _field("pm_amount", "PM amount", "computed", "opportunity", "Pricing", format="money"),
    _field("managed_support_amount", "Maintenance amount", "number", "opportunity", "Pricing",
           unit="₱", min=0),
    _field("discount_rate", "Discount rate", "number", "opportunity", "Pricing",
           unit="%", min=0, max=100),
    _field("discount_amount", "Discount amount", "computed", "opportunity", "Pricing", format="money"),
    _field("total_price", "Total price", "computed", "opportunity", "Pricing", format="money",
           emphasis=True),

    _field("status", "SQR status", "select", "opportunity", "Approval",
           choices=_choices(Status), workflow=True),
    _field("hold_reason", "Hold reason (retained)", "readonly", "opportunity", "Approval",
           multiline=True, hint="Separate from Remarks; retained after manual resumption."),
    _field("reviewed_at", "Approval date", "computed", "opportunity", "Approval", format="text"),
    _field("validity_due_date", "Validity due date", "computed", "opportunity", "Approval", format="text"),
    _field("proposal_status", "Proposal status", "select", "opportunity", "Approval",
           choices=_choices(Proposal), allow_blank=True),

    _field("assigned_pm", "Assigned PM", "user", "delivery", "Team", options="pm_users"),
    _field("assigned_sse", "Assigned SSE", "user", "delivery", "Team", options="sse_users"),
    _field("delivery_start_date", "Start date", "date", "delivery", "Schedule"),
    _field("delivery_target_finish_date", "Target finish date", "date", "delivery", "Schedule"),
    _field("delivery_actual_finish_date", "Actual finish date", "date", "delivery", "Schedule"),
    _field("delivery_completion_signed_date", "Completion signed date", "date", "delivery", "Schedule"),
    _field("overall_status", "Overall status", "select", "delivery", "Progress",
           choices=_choices(Overall), allow_blank=True),
    _field("delivery_health", "Health status", "select", "delivery", "Progress",
           choices=_choices(Health), allow_blank=True),
    _field("delivery_progress", "Overall progress", "number", "delivery", "Progress",
           unit="%", min=0, max=100),
    _field("key_updates_risks", "Key updates / risks / issues", "textarea", "delivery", "Progress"),
    _field("post_svc_warranty_end_date", "Completion warranty end", "computed", "delivery",
           "After delivery", format="text"),
    _field("managed_support_start_date", "Maintenance start date", "date", "delivery", "After delivery"),
    _field("support_start_date", "Effective maintenance start", "computed", "delivery",
           "After delivery", format="text"),
    _field("managed_support_end_date", "Maintenance end date", "computed", "delivery",
           "After delivery", format="text"),

    _field("po_pnl_date", "PO / PNL date", "date", "billing", "Purchase order"),
    _field("revenue_remarks", "PO remarks", "textarea", "billing", "Purchase order"),
    _field("revenue_source", "Billing type", "select", "billing", "Billing",
           choices=BILLING_TYPE_CHOICES, allow_blank=True),
    _field("revenue_status", "Billing status", "select", "billing", "Billing",
           choices=_choices(Billing), allow_blank=True),
    _field("revenue_date", "Billed date", "date", "billing", "Billing"),
    _field("revenue_reference_no", "Billing reference", "text", "billing", "Billing", uppercase=True),
    _field("revenue_overview", "Billing remarks", "textarea", "billing", "Billing"),
]

EDIT_FIELD_KEYS = frozenset(spec["key"] for spec in FIELD_SPECS if spec["type"] not in {"computed", "readonly", "link"})


def _iso(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return ""


def _decimal(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def _money(value) -> str:
    return "" if value is None else format(Decimal(value).quantize(Decimal("0.01")), "f")


def _date_text(value) -> str:
    return value.strftime("%b %d, %Y") if value else ""


def _user_name(user) -> str:
    if not user:
        return ""
    return user.get_full_name() or user.username


def revenue_state(submission: SqrSubmission) -> str:
    if submission.revenue_status:
        return submission.revenue_status
    return Billing.BILLED if submission.revenue_date else Billing.NOT_YET_BILLED


def delivery_finished(submission: SqrSubmission) -> bool:
    return bool(
        submission.overall_status == Overall.COMPLETED
        or submission.delivery_health == Health.COMPLETED
        or submission.delivery_actual_finish_date
    )


def derive_stage(submission: SqrSubmission) -> tuple[str, str]:
    """Return (stage, closed_reason); closed_reason is "" unless the stage is closed."""
    proposal = submission.proposal_status
    if proposal == Proposal.CLOSED_LOST:
        return "closed", "lost"
    if proposal == Proposal.CLOSED_CANCELED:
        return "closed", "canceled"
    if proposal != Proposal.CLOSED_WON:
        return "opportunity", ""
    if submission.overall_status == Overall.CANCELLED:
        return "closed", "canceled"
    if revenue_state(submission) == Billing.BILLED:
        return "closed", "billed"
    if delivery_finished(submission):
        return "billing", ""
    return "delivery", ""


def derive_attention(submission: SqrSubmission, stage: str, today: date) -> list[dict]:
    flags: list[dict] = []
    if submission.status == Status.ON_HOLD:
        return [{"key": "hold", "label": "On Hold", "tone": "muted"}]
    if submission.status == Status.FOR_REVISION:
        flags.append({"key": "revision", "label": "Revision requested", "tone": "danger"})
    elif submission.status == Status.FOR_PROCESSING:
        flags.append({"key": "awaiting", "label": "Awaiting approval", "tone": "warning"})

    due = submission.validity_due_date
    if stage == "opportunity" and submission.status == Status.APPROVED and due:
        days = (due - today).days
        if days < 0:
            flags.append({"key": "validity", "label": "Validity expired", "tone": "danger"})
        elif days <= VALIDITY_WARNING_DAYS:
            label = "Validity ends today" if days == 0 else f"Validity ends in {days} day{'s' if days != 1 else ''}"
            flags.append({"key": "validity", "label": label, "tone": "warning"})

    if stage == "delivery":
        target = submission.delivery_target_finish_date
        if target and target < today:
            flags.append({"key": "overdue", "label": "Delivery overdue", "tone": "danger"})
        if submission.delivery_health == Health.AT_RISK:
            flags.append({"key": "health", "label": "At risk", "tone": "danger"})
        elif submission.delivery_health == Health.OFF_TRACK:
            flags.append({"key": "health", "label": "Off track", "tone": "warning"})
    if stage == "billing":
        flags.append({"key": "billing", "label": "Ready to bill", "tone": "info"})
    return flags


def derive_next_step(submission: SqrSubmission, stage: str, closed_reason: str, *, is_pm: bool) -> dict:
    """Plain-language guidance; `action` lets the client render workflow buttons."""
    if submission.status == Status.ON_HOLD:
        return {"tone": "muted", "title": "SQR On Hold",
                "hint": ("Use SQR status to manually resume processing, request revision, or approve. "
                         "Opportunity and delivery statuses remain unchanged." if is_pm else
                         "Paused until a PM or Admin manually changes SQR status. See the hold reason below."),
                "action": "status" if is_pm else ""}
    if submission.status == Status.FOR_REVISION:
        if is_pm:
            return {"tone": "danger", "title": "Waiting for revision",
                    "hint": "Waiting on the engineer's update.",
                    "action": "review"}
        return {"tone": "danger", "title": "Revision requested",
                "hint": "Update the submission using the revision notes.",
                "action": "edit"}
    if submission.status == Status.FOR_PROCESSING:
        if is_pm:
            return {"tone": "warning", "title": "Review this SQR",
                    "hint": "Approve it or send it back for revision.",
                    "action": "review"}
        return {"tone": "info", "title": "Awaiting approval",
                "hint": "The approver has been notified.",
                "action": ""}
    if stage == "opportunity":
        if submission.proposal_status == Proposal.NEGOTIATION_REVIEW:
            return {"tone": "info", "title": "Negotiation in progress",
                    "hint": "Set Proposal Status once the customer decides.",
                    "action": ""}
        return {"tone": "info", "title": "Follow up with the customer",
                "hint": "Update Proposal Status when they respond.", "action": ""}
    if stage == "delivery":
        if not (submission.delivery_start_date and submission.delivery_target_finish_date):
            return {"tone": "info", "title": "Plan the delivery",
                    "hint": "Assign the PM and SSE and set the dates.", "action": ""}
        progress = submission.delivery_progress
        suffix = f" ({progress}% complete)" if progress is not None else ""
        return {"tone": "info", "title": "Delivery in progress",
                "hint": f"Keep progress and health current{suffix}.", "action": ""}
    if stage == "billing":
        return {"tone": "info", "title": "Ready for billing",
                "hint": "Add the billing reference, then mark it Billed.", "action": ""}
    reasons = {
        "lost": ("Closed-Lost", "Delivery and billing are locked."),
        "canceled": ("Closed-Canceled", "Delivery and billing are locked."),
        "billed": ("Complete", "Delivered and billed."),
    }
    title, hint = reasons.get(closed_reason, ("Closed", ""))
    return {"tone": "muted", "title": title, "hint": hint, "action": ""}


def _values(submission: SqrSubmission) -> dict:
    support_start = submission.computed_support_start_date
    return {
        "customer_name": submission.customer_name,
        "project_title": submission.project_title,
        "project_details": submission.project_details or "",
        "customer_company": submission.customer_company or "",
        "customer_contact": submission.customer_contact or "",
        "linked_request": str(submission.linked_request_id or ""),
        "requester": _user_name(submission.engineer),
        "approver": _user_name(submission.pm_esg_reviewer),
        "sqr_folder_link": submission.sqr_folder_link or "",
        "remarks": submission.remarks or "",
        "hold_reason": submission.hold_reason or "",
        "sse_manhrs": _decimal(submission.sse_manhrs),
        "sse_amount": _money(submission.sse_amount),
        "pm_manhrs": _decimal(submission.effective_pm_manhrs),
        "pm_amount": _money(submission.effective_pm_amount),
        "managed_support_amount": _decimal(submission.managed_support_amount),
        "discount_rate": str(submission.discount_rate or 0),
        "discount_amount": _money(submission.computed_discount_amount),
        "total_price": _money(submission.computed_total_price or submission.quotation_total_price),
        "status": submission.status,
        "reviewed_at": _date_text(submission.reviewed_at),
        "validity_due_date": _date_text(submission.validity_due_date),
        "proposal_status": submission.proposal_status or "",
        "assigned_pm": str(submission.assigned_pm_id or submission.pm_esg_reviewer_id or ""),
        "assigned_sse": str(submission.assigned_sse_id or ""),
        "delivery_start_date": _iso(submission.delivery_start_date),
        "delivery_target_finish_date": _iso(submission.delivery_target_finish_date),
        "delivery_actual_finish_date": _iso(submission.delivery_actual_finish_date),
        "delivery_completion_signed_date": _iso(submission.delivery_completion_signed_date),
        "overall_status": submission.overall_status or "",
        "delivery_health": submission.delivery_health or "",
        "delivery_progress": "" if submission.delivery_progress is None else str(submission.delivery_progress),
        "key_updates_risks": submission.key_updates_risks or "",
        "post_svc_warranty_end_date": _date_text(submission.computed_post_svc_warranty_end_date),
        "managed_support_start_date": _iso(submission.managed_support_start_date),
        "support_start_date": _date_text(support_start),
        "managed_support_end_date": _date_text(submission.computed_managed_support_end_date),
        "po_pnl_date": _iso(submission.po_pnl_date),
        "revenue_remarks": submission.revenue_remarks or "",
        "revenue_source": submission.revenue_source or "",
        "revenue_status": revenue_state(submission),
        "revenue_date": _iso(submission.revenue_date),
        "revenue_reference_no": submission.revenue_reference_no or "",
        "revenue_overview": submission.revenue_overview or "",
    }


def serialize_record(
    submission: SqrSubmission,
    user,
    *,
    allowed_fields,
    locked_fields,
    today: date,
    is_pm: bool,
    is_admin: bool,
    is_engineer: bool,
) -> dict:
    stage, closed_reason = derive_stage(submission)
    locked = submission.proposal_status in (Proposal.CLOSED_LOST, Proposal.CLOSED_CANCELED)
    editable = sorted(
        key for key in (allowed_fields or ()) if key in EDIT_FIELD_KEYS and not (locked and key in locked_fields)
    )
    values = _values(submission)
    linked = submission.linked_request
    owns = is_engineer and submission.engineer_id == user.pk
    record = {
        "pk": submission.pk,
        "ref": submission.reference_code or f"SQR #{submission.pk}",
        "created_at": _iso(submission.created_at.date()),
        "account": submission.customer_name,
        "service": submission.project_title,
        "scope": submission.project_details or "",
        "group": submission.customer_company or "",
        "requester": values["requester"],
        "approver": values["approver"],
        "approver_id": submission.pm_esg_reviewer_id,
        "assigned_pm_name": _user_name(submission.assigned_pm or submission.pm_esg_reviewer),
        "assigned_sse_name": _user_name(submission.assigned_sse),
        "status": submission.status,
        "proposal_status": submission.proposal_status or "",
        "overall_status": submission.overall_status or "",
        "delivery_health": submission.delivery_health or "",
        "revenue_status": values["revenue_status"],
        "progress": submission.delivery_progress,
        "total": values["total_price"],
        "stage": stage,
        "closed_reason": closed_reason,
        "attention": derive_attention(submission, stage, today),
        "next_step": derive_next_step(submission, stage, closed_reason, is_pm=is_pm),
        "editable": editable,
        "locked": locked,
        "review_notes": submission.review_notes if submission.status == Status.FOR_REVISION else "",
        "linked_request": (
            {"pk": linked.pk, "code": linked.reference_code,
             "url": reverse("hub:request-manage-collab", args=[linked.pk])}
            if linked else None
        ),
        "target_date": _iso(submission.delivery_target_finish_date),
        "values": values,
        "perms": {
            "edit_form": owns,
            "delete": is_admin,
            "history": is_pm,
        },
        "urls": {
            "inline": reverse("hub:sqr-inline-update", args=[submission.pk]),
            "history": reverse("hub:sqr-history", args=[submission.pk]) if is_pm else "",
            "edit": reverse("hub:sqr-edit", args=[submission.pk]) if owns else "",
            "delete": reverse("hub:sqr-delete", args=[submission.pk]) if is_admin else "",
        },
        "search": " ".join(filter(None, [
            submission.reference_code, submission.customer_name, submission.project_title,
            submission.customer_company, submission.customer_contact, values["requester"],
            values["approver"], values["remarks"], values["hold_reason"],
            linked.reference_code if linked else "",
        ])).lower(),
    }
    if is_pm:
        record["engineer_email"] = submission.engineer.email if submission.engineer_id else ""
        record["pm_email"] = submission.pm_esg_reviewer.email if submission.pm_esg_reviewer_id else ""
        record["engineer_name"] = values["requester"]
    return record


def _option(user) -> dict:
    return {"value": str(user["pk"]), "label": (f"{user['first_name']} {user['last_name']}").strip() or user["username"]}


def build_options(*, pm_users, sse_users, rq_options) -> dict:
    return {
        "pm_users": [_option(user) for user in pm_users],
        "sse_users": [_option(user) for user in sse_users],
        "rq_options": [{"value": str(item["pk"]), "label": item["reference_code"]} for item in rq_options],
    }
