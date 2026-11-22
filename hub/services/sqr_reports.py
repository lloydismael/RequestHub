"""Proposal, delivery, revenue, and SQR analytics from scoped submissions."""

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from django.utils import timezone

from hub.models import MANILA_TZ, SqrSubmission


def decimal_to_float(value: Decimal | int | None) -> float:
    """Convert a currency amount to a numeric chart value."""
    return float(value or Decimal(0))

def submission_discounted_value(submission: SqrSubmission) -> Decimal:
    """Select the available discounted valuation for a quote."""
    return (
        submission.computed_total_price
        or submission.discounted_price
        or submission.quotation_total_price
        or Decimal(0)
    )

def submission_gross_value(submission: SqrSubmission) -> Decimal:
    """Select the available gross valuation for a quote."""
    return (
        submission.computed_gross_total
        or submission.quotation_total_price
        or submission.computed_total_price
        or submission.discounted_price
        or Decimal(0)
    )

def compact_currency(value: Decimal) -> str:
    """Format a compact peso label for a chart or summary."""
    amount = decimal_to_float(value)
    abs_amount = abs(amount)
    if abs_amount >= 1_000_000:
        return f"₱{amount / 1_000_000:.2f}M"
    if abs_amount >= 1_000:
        return f"₱{amount / 1_000:.1f}K"
    return f"₱{amount:,.2f}"


def build_sqr_report_context(all_submissions: list[SqrSubmission], *, is_pm: bool) -> dict[str, Any]:
    """Build the tracker summary and report payload with the existing ordering and totals."""
    # ── Proposal Stage ──────────────────────────────────────────────────
    proposal_counts = {
        "total": len(all_submissions),
        "processing": sum(1 for s in all_submissions if s.status == SqrSubmission.Status.FOR_PROCESSING),
        "for_revision": sum(1 for s in all_submissions if s.status == SqrSubmission.Status.FOR_REVISION),
        "approved": sum(1 for s in all_submissions if s.status == SqrSubmission.Status.APPROVED),
        "on_hold": sum(1 for s in all_submissions if s.status == SqrSubmission.Status.ON_HOLD),
        "submitted_pending": sum(
            1 for s in all_submissions
            if s.proposal_status == SqrSubmission.ProposalStatus.SUBMITTED_PENDING
        ),
        "negotiation": sum(
            1 for s in all_submissions
            if s.proposal_status == SqrSubmission.ProposalStatus.NEGOTIATION_REVIEW
        ),
        "closed_won": sum(
            1 for s in all_submissions
            if s.proposal_status == SqrSubmission.ProposalStatus.CLOSED_WON
        ),
        "closed_lost": sum(
            1 for s in all_submissions
            if s.proposal_status == SqrSubmission.ProposalStatus.CLOSED_LOST
        ),
        "closed_canceled": sum(
            1 for s in all_submissions
            if s.proposal_status == SqrSubmission.ProposalStatus.CLOSED_CANCELED
        ),
    }

    # ── Service Delivery Stage ───────────────────────────────────────────
    delivery_submissions = (
        [s for s in all_submissions if s.proposal_status == SqrSubmission.ProposalStatus.CLOSED_WON]
        if is_pm else []
    )
    delivery_health_counts = {}
    for choice_val, choice_label in SqrSubmission.DeliveryHealth.choices:
        delivery_health_counts[choice_val] = sum(
            1 for s in delivery_submissions if s.delivery_health == choice_val
        )

    # ── Revenue & Report Stage ──────────────────────────────────────────
    won_submissions = (
        [s for s in all_submissions if s.proposal_status == SqrSubmission.ProposalStatus.CLOSED_WON]
        if is_pm else []
    )
    lost_submissions = (
        [s for s in all_submissions if s.proposal_status == SqrSubmission.ProposalStatus.CLOSED_LOST]
        if is_pm else []
    )
    in_negotiation = (
        [s for s in all_submissions if s.proposal_status == SqrSubmission.ProposalStatus.NEGOTIATION_REVIEW]
        if is_pm else []
    )
    total_with_deal_status = sum(
        1 for s in all_submissions if s.proposal_status
    ) if is_pm else 0
    won_total_price = sum(submission_gross_value(s) for s in won_submissions)
    won_discounted_total = sum(submission_discounted_value(s) for s in won_submissions)
    lost_total_price = sum(submission_discounted_value(s) for s in lost_submissions)
    win_rate = (
        round(len(won_submissions) / total_with_deal_status * 100, 1)
        if total_with_deal_status > 0 else 0
    )
    top_won = sorted(won_submissions, key=submission_discounted_value, reverse=True)[:5]

    revenue_data = {
        "won_count": len(won_submissions),
        "lost_count": len(lost_submissions),
        "negotiation_count": len(in_negotiation),
        "won_total_price": won_total_price,
        "won_discounted_total": won_discounted_total,
        "lost_total_price": lost_total_price,
        "win_rate": win_rate,
        "top_won": top_won,
        "lost_submissions": lost_submissions,
        "revenue_submissions": [s for s in all_submissions if s.status == SqrSubmission.Status.APPROVED] if is_pm else [],
    }

    sqr_reports_summary = None
    sqr_reports_status_chart = {"labels": [], "totals": []}
    sqr_reports_deal_chart = {"labels": [], "totals": []}
    sqr_reports_delivery_chart = {"labels": [], "totals": []}
    sqr_reports_group_chart = {"labels": [], "totals": []}
    sqr_reports_scope_chart = {"labels": [], "totals": []}
    sqr_reports_funnel_chart = {"labels": [], "totals": []}
    sqr_reports_monthly_chart = {"labels": [], "totals": []}
    sqr_reports_revenue_chart = {"labels": [], "totals": []}
    sqr_reports_quick_overview_chart = {"labels": [], "totals": []}
    sqr_reports_top_accounts = []
    sqr_reports_top_won = []
    sqr_reports_pipeline = {}
    sqr_reports_focus_items = []

    if is_pm:
        approved_submissions = [
            submission for submission in all_submissions
            if submission.status == SqrSubmission.Status.APPROVED
        ]
        billed_submissions = [
            submission for submission in approved_submissions if submission.revenue_date
        ]
        for_billing_submissions = [
            submission for submission in approved_submissions if not submission.revenue_date
        ]
        at_risk_submissions = [
            submission for submission in delivery_submissions
            if submission.delivery_health == SqrSubmission.DeliveryHealth.AT_RISK
        ]
        off_track_submissions = [
            submission for submission in delivery_submissions
            if submission.delivery_health == SqrSubmission.DeliveryHealth.OFF_TRACK
        ]
        on_track_submissions = [
            submission for submission in delivery_submissions
            if submission.delivery_health == SqrSubmission.DeliveryHealth.ON_TRACK
        ]
        pending_approval_count = proposal_counts["processing"] + proposal_counts["for_revision"]

        status_rows = [
            ("For Processing", proposal_counts["processing"]),
            ("For Revision", proposal_counts["for_revision"]),
            ("Approved", proposal_counts["approved"]),
            ("On Hold", proposal_counts["on_hold"]),
        ]
        sqr_reports_status_chart = {
            "labels": [label for label, total in status_rows if total],
            "totals": [total for _, total in status_rows if total],
        }

        deal_rows = [
            ("Submitted", proposal_counts["submitted_pending"]),
            ("On Review", proposal_counts["negotiation"]),
            ("Closed-Won", proposal_counts["closed_won"]),
            ("Closed-Lost", proposal_counts["closed_lost"]),
            ("Closed-Canceled", proposal_counts.get("closed_canceled", 0)),
        ]
        sqr_reports_deal_chart = {
            "labels": [label for label, total in deal_rows if total],
            "totals": [total for _, total in deal_rows if total],
        }

        sqr_reports_delivery_chart = {
            "labels": [label for value, label in SqrSubmission.DeliveryHealth.choices if delivery_health_counts.get(value)],
            "totals": [delivery_health_counts.get(value, 0) for value, _ in SqrSubmission.DeliveryHealth.choices if delivery_health_counts.get(value)],
        }

        group_counter = Counter(
            ((submission.customer_company or "Unspecified").strip() or "Unspecified")
            for submission in all_submissions
        )
        sorted_group_rows = sorted(group_counter.items(), key=lambda item: (-item[1], item[0]))[:8]
        sqr_reports_group_chart = {
            "labels": [label for label, _ in sorted_group_rows],
            "totals": [total for _, total in sorted_group_rows],
        }

        scope_counter = Counter(
            ((submission.project_details or "Unspecified").strip() or "Unspecified")
            for submission in all_submissions
        )
        sorted_scope_rows = sorted(scope_counter.items(), key=lambda item: (-item[1], item[0]))[:8]
        sqr_reports_scope_chart = {
            "labels": [label for label, _ in sorted_scope_rows],
            "totals": [total for _, total in sorted_scope_rows],
        }

        sqr_reports_funnel_chart = {
            "labels": ["Total SQR", "Approved", "Closed Won", "Billed"],
            "totals": [
                proposal_counts["total"],
                proposal_counts["approved"],
                len(won_submissions),
                len(billed_submissions),
            ],
        }
        sqr_reports_quick_overview_chart = {
            "labels": ["Pending", "Approved", "Won", "Billed"],
            "totals": [
                pending_approval_count,
                proposal_counts["approved"],
                len(won_submissions),
                len(billed_submissions),
            ],
        }

        monthly_counter = Counter()
        monthly_labels = {}
        for submission in all_submissions:
            localized_created = timezone.localtime(submission.created_at, MANILA_TZ)
            month_key = localized_created.strftime("%Y-%m")
            monthly_counter[month_key] += 1
            monthly_labels[month_key] = localized_created.strftime("%b %Y")

        sorted_month_keys = sorted(monthly_counter.keys())
        sqr_reports_monthly_chart = {
            "labels": [monthly_labels[key] for key in sorted_month_keys],
            "totals": [monthly_counter[key] for key in sorted_month_keys],
        }

        total_managed_support = sum(
            submission.managed_support_amount or Decimal(0)
            for submission in all_submissions
        )
        revenue_rows = [
            ("Won Gross", won_total_price),
            ("Won Discounted", won_discounted_total),
            ("Lost Value", lost_total_price),
            ("Managed Support", total_managed_support),
        ]
        sqr_reports_revenue_chart = {
            "labels": [label for label, value in revenue_rows if value and value > 0],
            "totals": [decimal_to_float(value) for _, value in revenue_rows if value and value > 0],
        }

        account_totals = defaultdict(lambda: {"count": 0, "value": Decimal(0)})
        for submission in won_submissions:
            account_name = (submission.customer_name or "Unspecified Account").strip() or "Unspecified Account"
            account_totals[account_name]["count"] += 1
            account_totals[account_name]["value"] += submission_discounted_value(submission)

        sqr_reports_top_accounts = [
            {
                "name": account_name,
                "count": metrics["count"],
                "value": metrics["value"],
            }
            for account_name, metrics in sorted(
                account_totals.items(),
                key=lambda item: (-item[1]["value"], -item[1]["count"], item[0]),
            )[:6]
        ]

        sqr_reports_top_won = [
            {
                "reference_code": submission.reference_code,
                "customer_name": submission.customer_name,
                "group_name": submission.customer_company or "—",
                "discounted_price": submission_discounted_value(submission),
                "status": submission.get_delivery_health_display() if submission.delivery_health else "Awaiting delivery setup",
            }
            for submission in top_won
        ]

        approval_lead_days = [
            max(
                (timezone.localtime(submission.reviewed_at, MANILA_TZ).date() - timezone.localtime(submission.created_at, MANILA_TZ).date()).days,
                0,
            )
            for submission in approved_submissions
            if submission.reviewed_at
        ]
        avg_approval_days = (
            round(sum(approval_lead_days) / len(approval_lead_days), 1)
            if approval_lead_days else 0
        )
        avg_won_value = (
            won_discounted_total / len(won_submissions)
            if won_submissions else Decimal(0)
        )

        approval_rate = (
            round((proposal_counts["approved"] / proposal_counts["total"]) * 100, 1)
            if proposal_counts["total"] else 0
        )
        billed_conversion_rate = (
            round((len(billed_submissions) / proposal_counts["approved"]) * 100, 1)
            if proposal_counts["approved"] else 0
        )
        win_from_approved_rate = (
            round((len(won_submissions) / proposal_counts["approved"]) * 100, 1)
            if proposal_counts["approved"] else 0
        )
        sqr_reports_summary = {
            "total_submissions": proposal_counts["total"],
            "approval_rate": approval_rate,
            "win_rate": win_rate,
            "active_delivery": len(
                [
                    submission for submission in delivery_submissions
                    if submission.delivery_health in {
                        SqrSubmission.DeliveryHealth.ON_TRACK,
                        SqrSubmission.DeliveryHealth.OFF_TRACK,
                        SqrSubmission.DeliveryHealth.AT_RISK,
                    }
                ]
            ),
            "won_discounted_total": won_discounted_total,
            "won_discounted_total_label": compact_currency(won_discounted_total),
            "lost_total_price": lost_total_price,
            "lost_total_price_label": compact_currency(lost_total_price),
            "managed_support_total": total_managed_support,
            "managed_support_total_label": compact_currency(total_managed_support),
            "billed_count": len(billed_submissions),
            "for_billing_count": len(for_billing_submissions),
            "at_risk_count": len(at_risk_submissions),
            "off_track_count": len(off_track_submissions),
            "on_track_count": len(on_track_submissions),
            "pending_approval_count": pending_approval_count,
            "avg_won_value_label": compact_currency(avg_won_value),
            "avg_approval_days": avg_approval_days,
            "billed_conversion_rate": billed_conversion_rate,
            "win_from_approved_rate": win_from_approved_rate,
        }

        sqr_reports_pipeline = {
            "pending_approval_count": pending_approval_count,
            "approved_count": proposal_counts["approved"],
            "negotiation_count": proposal_counts["negotiation"],
            "won_count": len(won_submissions),
            "lost_count": len(lost_submissions),
            "billed_count": len(billed_submissions),
            "for_billing_count": len(for_billing_submissions),
            "at_risk_count": len(at_risk_submissions),
            "off_track_count": len(off_track_submissions),
            "on_track_count": len(on_track_submissions),
            "avg_won_value_label": compact_currency(avg_won_value),
            "avg_approval_days": avg_approval_days,
        }

        sqr_reports_focus_items = [
            {
                "title": "Pending internal action",
                "value": pending_approval_count,
                "description": "SQRs still waiting for processing or revision closure.",
                "icon": "bi-hourglass-split",
                "tone": "warning",
            },
            {
                "title": "Revenue still pending",
                "value": len(for_billing_submissions),
                "description": "Approved quotations not yet billed in the revenue stage.",
                "icon": "bi-receipt-cutoff",
                "tone": "primary",
            },
            {
                "title": "Delivery watchlist",
                "value": len(at_risk_submissions) + len(off_track_submissions),
                "description": "Won projects marked at risk or off track and needing attention.",
                "icon": "bi-exclamation-diamond",
                "tone": "danger",
            },
            {
                "title": "Average won deal size",
                "value": compact_currency(avg_won_value),
                "description": "Typical discounted value of each successfully won quotation.",
                "icon": "bi-cash-stack",
                "tone": "success",
            },
        ]

    return {
        "proposal_counts": proposal_counts,
        "delivery_submissions": delivery_submissions,
        "delivery_health_counts": delivery_health_counts,
        "revenue_data": revenue_data,
        "sqr_reports_summary": sqr_reports_summary,
        "sqr_reports_status_chart": sqr_reports_status_chart,
        "sqr_reports_deal_chart": sqr_reports_deal_chart,
        "sqr_reports_delivery_chart": sqr_reports_delivery_chart,
        "sqr_reports_group_chart": sqr_reports_group_chart,
        "sqr_reports_scope_chart": sqr_reports_scope_chart,
        "sqr_reports_funnel_chart": sqr_reports_funnel_chart,
        "sqr_reports_monthly_chart": sqr_reports_monthly_chart,
        "sqr_reports_revenue_chart": sqr_reports_revenue_chart,
        "sqr_reports_quick_overview_chart": sqr_reports_quick_overview_chart,
        "sqr_reports_top_accounts": sqr_reports_top_accounts,
        "sqr_reports_top_won": sqr_reports_top_won,
        "sqr_reports_pipeline": sqr_reports_pipeline,
        "sqr_reports_focus_items": sqr_reports_focus_items,
    }
