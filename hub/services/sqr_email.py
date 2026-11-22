"""SQR advisory composition and existing synchronous email delivery."""

import base64
import html
import mimetypes
import re
from decimal import Decimal
from email.charset import QP, Charset
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging import getLogger
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse

from accounts.models import User
from hub.constants import ACCOUNT_MANAGERS
from hub.models import MANILA_TZ, SqrSubmission

logger = getLogger("hub.views")

SQR_APPROVAL_SERVICE_COMPONENT = "Systems Support & Maintenance Service - 1 Year"
SQR_APPROVAL_LOGO_URL = "https://raw.githubusercontent.com/lloydismael/request-hub/dev/static/img/phil-data-full-logo.png"
SQR_APPROVAL_LOGO_PATH = Path(settings.BASE_DIR) / "static" / "img" / "phil-data-full-logo.png"
SQR_APPROVAL_LOGO_CID = "sqr-approval-logo@request-hub"
SQR_APPROVAL_MAILTO_BODY_URL_LIMIT = 8000
SQR_APPROVAL_HUB_EMAIL = "ESGRequestHub@phildata.com"
SQR_ACCOUNT_MANAGER_EMAILS = {
    manager["name"].strip().casefold(): manager["email"]
    for manager in ACCOUNT_MANAGERS
    if manager.get("name") and manager.get("email")
}


def _format_sqr_approval_greeting(requestor_name: str) -> str:
    """Format the named or generic advisory greeting."""
    cleaned_name = (requestor_name or "").strip()
    return f"Hi @{cleaned_name}" if cleaned_name else "Hi"


def _get_sqr_service_component(submission: "SqrSubmission") -> str:
    """Return the standard component for an SQR advisory."""
    return SQR_APPROVAL_SERVICE_COMPONENT


def _normalize_sqr_person_name(value: str) -> str:
    """Normalize a contact name for matching."""
    return re.sub(r"\s+", " ", (value or "").replace(",", " ").strip()).casefold()


def _resolve_sqr_account_manager_email(submission: "SqrSubmission") -> str:
    """Resolve the account manager from the roster or active users."""
    manager_name = (submission.customer_contact or "").strip()
    if not manager_name:
        return ""

    roster_email = SQR_ACCOUNT_MANAGER_EMAILS.get(manager_name.casefold())
    if roster_email:
        return roster_email.strip()

    normalized_name = _normalize_sqr_person_name(manager_name)
    for user in User.objects.exclude(email="").only("first_name", "last_name", "username", "email"):
        candidates = [
            user.get_full_name(),
            f"{user.last_name} {user.first_name}".strip(),
            user.username,
        ]
        if any(_normalize_sqr_person_name(candidate) == normalized_name for candidate in candidates if candidate):
            return (user.email or "").strip()
    return ""


def _unique_sqr_approval_emails(*emails: str) -> list[str]:
    """Deduplicate nonempty recipients without changing their order."""
    unique_emails: list[str] = []
    seen = set()
    for email in emails:
        cleaned = (email or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique_emails.append(cleaned)
    return unique_emails


def _get_sqr_approval_to_emails(submission: "SqrSubmission") -> list[str]:
    """Return the advisory recipients in their original order."""
    engineer_email = (getattr(submission.engineer, "email", "") or "").strip()
    return _unique_sqr_approval_emails(
        engineer_email,
        _resolve_sqr_account_manager_email(submission),
        SQR_APPROVAL_HUB_EMAIL,
    )


def _get_sqr_logo_attachment() -> dict | None:
    """Build the inline logo attachment when the image is available."""
    try:
        logo_bytes = SQR_APPROVAL_LOGO_PATH.read_bytes()
    except OSError:
        logger.warning("SQR approval logo file is unavailable at %s", SQR_APPROVAL_LOGO_PATH)
        return None

    content_type = mimetypes.guess_type(str(SQR_APPROVAL_LOGO_PATH))[0] or "image/png"
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": SQR_APPROVAL_LOGO_PATH.name,
        "contentType": content_type,
        "isInline": True,
        "contentId": SQR_APPROVAL_LOGO_CID,
        "contentBytes": base64.b64encode(logo_bytes).decode("ascii"),
    }


def _build_sqr_approval_mime_message(context: dict, sender_email: str, *, mark_as_unsent: bool = False) -> str:
    """Encode the multipart advisory for an Outlook draft."""
    windows_charset = Charset("windows-1252")
    windows_charset.body_encoding = QP

    message = MIMEMultipart("related")
    message.set_param("type", "multipart/alternative")
    message["Subject"] = Header(context["subject"], "windows-1252")
    message["From"] = sender_email
    recipients = context.get("recipients") or []
    if recipients:
        message["To"] = ", ".join(recipients)
    if mark_as_unsent:
        message["X-Unsent"] = "1"

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(context["plain_text"], "plain", windows_charset))
    alternative.attach(MIMEText(context.get("html_draft") or context["html_preview"], "html", windows_charset))
    message.attach(alternative)

    for attachment in context.get("attachments") or []:
        raw_bytes = base64.b64decode(attachment["contentBytes"])
        maintype, _, subtype = (attachment.get("contentType") or "application/octet-stream").partition("/")
        if maintype == "image":
            part = MIMEImage(raw_bytes, _subtype=subtype or "png")
        else:
            part = MIMEApplication(raw_bytes, _subtype=subtype or "octet-stream")
        part.add_header("Content-Disposition", "inline", filename=attachment.get("name") or "attachment")
        if attachment.get("contentId"):
            part.add_header("Content-ID", f'<{attachment["contentId"]}>')
        message.attach(part)

    return base64.b64encode(message.as_bytes()).decode("ascii")


def _format_sqr_approval_subject(submission: "SqrSubmission") -> str:
    """Build the approval advisory subject."""
    rq_id = submission.linked_request.reference_code if submission.linked_request else ""
    if rq_id:
        return f"{submission.reference_code} • {rq_id} • {submission.customer_name}"
    return f"{submission.reference_code} • {submission.customer_name}"


def _format_sqr_approval_date(submission: "SqrSubmission") -> str:
    """Format the approval date for the advisory."""
    if not submission.reviewed_at:
        return "—"
    return submission.reviewed_at.astimezone(MANILA_TZ).strftime("%B %d, %Y")


def _format_sqr_approval_total_price(submission: "SqrSubmission") -> str:
    """Format the selected quotation total."""
    if submission.computed_total_price is not None:
        return f"PHP {submission.computed_total_price:,.2f}"
    if submission.quotation_total_price is not None:
        return f"PHP {submission.quotation_total_price:,.2f}"
    return "—"


def _format_sqr_approval_validity_date(submission: "SqrSubmission") -> str:
    """Format the date through which the quote is valid."""
    return submission.validity_due_date.strftime("%B %d, %Y") if submission.validity_due_date else "—"


def _format_sqr_approval_currency(value: object) -> str:
    """Format the advisory amount in Philippine pesos."""
    if value is None:
        return "—"
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"PHP {amount:,.2f}"


def _build_sqr_approval_body_lines(submission: "SqrSubmission", requestor_name: str) -> list[str]:
    """Build the plain-text advisory lines."""
    service_component = _get_sqr_service_component(submission)
    total_price = _format_sqr_approval_total_price(submission)
    approval_date = _format_sqr_approval_date(submission)
    validity_date = _format_sqr_approval_validity_date(submission)
    customer_name = submission.customer_name or "—"
    service_description = (submission.project_title or "").strip() or "—"
    account_manager = (submission.customer_contact or "").strip() or "—"
    scope_of_services = (submission.project_details or "").strip() or "—"
    investment_total = _format_sqr_approval_currency(
        submission.computed_total_price if submission.computed_total_price is not None else submission.quotation_total_price
    )

    lines = [
        "SQR Approval Advisory",
        _format_sqr_approval_greeting(requestor_name),
        "Submitted SQR is now approved, please refer to the ff. details below.",
        "",
        f"SQR ID : {submission.reference_code or '—'}",
        f"Customer Name : {customer_name}",
        f"Service Description : {service_description}",
        f"Account Manager : {account_manager}",
        f"Scope of Services : {scope_of_services}",
        *([f"Add-On Service : {service_component}"] if submission.managed_support_amount is not None else []),
        "Included Services:",
        "• Proactive System Health Checks",
        "• System/Platform Patching (scheduled)",
        "• Incident Support",
        "• Basic Troubleshooting and Issue Isolation",
        "• Monthly System Status Report",
        "• Advisory Support",
        "Quantity: 1 Lot",
        f"Approval Date : {approval_date}",
        f"Quotation Validity Until: {validity_date}",
        "",
        "Professional Services Investment Summary",
        f"Project Implementation: {_format_sqr_approval_currency(submission.sse_amount)}",
        f"Project Management: {_format_sqr_approval_currency(submission.pm_amount)}",
        f"Systems Support & Maintenance Service - 1 Year (Optional): {_format_sqr_approval_currency(submission.managed_support_amount)}",
        f"Total Professional Services Investment: {investment_total}",
        "",
        "TERMS AND CONDITIONS",
        "VAT: This quote excludes Value Added Tax (VAT).",
        "For P&L documentation purposes, a VAT-inclusive total may be applied. Internal billing and revenue reporting remain VAT-exclusive.",
        "This is a budgetary quote.",
        "This quote is issued for internal billing purposes only.",
        "Travel costs within Metro Manila are included.",
        "This quote does not include hardware, software licenses, or subscriptions unless stated.",
        "",
        "For any questions or to discuss this quote further, please don't hesitate to contact us:",
        "EnterpriseServices@phildata.com",
    ]
    summary = [f"Engineer : {requestor_name or 'Not assigned'}"]
    reviewer = submission.reviewed_by or submission.pm_esg_reviewer
    if reviewer:
        summary.append(f"Reviewer : {reviewer.get_full_name() or reviewer.username}")
    for label, hours, amount in (
        ("Implementation", submission.sse_manhrs, submission.sse_amount),
        ("Project Management", submission.pm_manhrs, submission.pm_amount),
    ):
        if hours is not None:
            rate = amount / hours if amount is not None and hours else None
            summary.append(f"{label} : {hours} man-hours" + (f" at {_format_sqr_approval_currency(rate)} / hour" if rate is not None else ""))
    if submission.computed_gross_total is not None:
        summary.extend([
            f"Subtotal: {_format_sqr_approval_currency(submission.computed_gross_total)}",
            f"Discount ({submission.discount_rate or 0}%): {_format_sqr_approval_currency(submission.computed_discount_amount)}",
        ])
    lines[lines.index("Professional Services Investment Summary") + 1:lines.index("Professional Services Investment Summary") + 1] = summary
    lines.extend(["", "Next steps", "Review the approved quotation and supporting documents before proceeding with internal billing."])
    for label, url in _sqr_approval_document_links(submission):
        lines.append(f"{label}: {url}")
    if submission.managed_support_amount is None:
        start = lines.index("Included Services:")
        end = lines.index("Quantity: 1 Lot")
        del lines[start:end]
        lines = [line for line in lines if not line.startswith("Systems Support & Maintenance Service - 1 Year (Optional):")]
    return lines


def _sqr_approval_document_links(submission: SqrSubmission) -> list[str]:
    """Return the document links included in the approval advisory."""
    links = []
    for label, value in (("Supporting document", submission.documentation_links), ("SQR folder", submission.sqr_folder_link)):
        for url in (value or "").splitlines():
            url = url.strip()
            if re.match(r"^https?://[^\s]+$", url, re.IGNORECASE):
                links.append((label, url))
    return links


def _build_sqr_approval_html(submission: "SqrSubmission", requestor_name: str) -> str:
    """Render the HTML advisory with its hosted logo."""
    return _build_sqr_approval_html_markup(submission, requestor_name, logo_src=SQR_APPROVAL_LOGO_URL)


def _build_sqr_approval_draft_html(submission: "SqrSubmission", requestor_name: str) -> str:
    """Render the HTML advisory with its attached logo."""
    return _build_sqr_approval_html_markup(submission, requestor_name, logo_src=f"cid:{SQR_APPROVAL_LOGO_CID}")


def _build_sqr_approval_html_markup(submission: "SqrSubmission", requestor_name: str, *, logo_src: str) -> str:
    """Compose the HTML advisory for the specified logo source."""
    service_component = html.escape(_get_sqr_service_component(submission))
    approval_date = html.escape(_format_sqr_approval_date(submission))
    total_price = html.escape(_format_sqr_approval_total_price(submission))
    validity_date = html.escape(_format_sqr_approval_validity_date(submission))
    greeting = html.escape(_format_sqr_approval_greeting(requestor_name))
    logo_url = html.escape(logo_src)
    professional_total = html.escape(
        _format_sqr_approval_currency(
            submission.computed_total_price if submission.computed_total_price is not None else submission.quotation_total_price
        )
    )

    summary_rows = []
    reviewer = submission.reviewed_by or submission.pm_esg_reviewer
    for label, value in (
        ("Engineer", requestor_name),
        ("Reviewer", reviewer.get_full_name() or reviewer.username if reviewer else ""),
    ):
        if value:
            summary_rows.append(f'<p style="font:13px Arial;color:#101828;margin:8px 0;"><b>{label}</b>: {html.escape(value)}</p>')
    cost_rows = []
    for label, hours, amount in (
        ("Project Implementation", submission.sse_manhrs, submission.sse_amount),
        ("Project Management", submission.pm_manhrs, submission.pm_amount),
        ("Systems Support & Maintenance Service - 1 Year (Optional)", None, submission.managed_support_amount),
    ):
        if amount is None and hours is None:
            continue
        rate = amount / hours if amount is not None and hours else None
        cells = [label, str(hours) if hours is not None else "1 Lot", _format_sqr_approval_currency(rate) if rate is not None else "—", _format_sqr_approval_currency(amount)]
        cost_rows.append('<tr>' + ''.join(f'<td style="border:1px solid #cfd8e3;padding:12px;font:13px Arial;color:#101828;">{html.escape(value)}</td>' for value in cells) + '</tr>')
    costing = '<table style="width:100%;border-collapse:collapse;"><thead><tr>' + ''.join(f'<th scope="col" style="background:#edf2f7;border:1px solid #cfd8e3;padding:12px;text-align:left;font: bold 13px Arial;color:#101828;">{label}</th>' for label in ("Service", "Man-hours / quantity", "Rate", "Amount")) + '</tr></thead><tbody>' + ''.join(cost_rows)
    for label, amount in (("Subtotal", submission.computed_gross_total), (f"Discount ({submission.discount_rate or 0}%)", submission.computed_discount_amount)):
        if amount is not None:
            costing += f'<tr><td colspan="3" style="padding:12px;font:13px Arial;color:#101828;">{label}</td><td style="padding:12px;font:13px Arial;color:#101828;">{html.escape(_format_sqr_approval_currency(amount))}</td></tr>'
    costing += f'<tr style="background:#e8f1fa;"><td colspan="3" style="padding:14px;font:14px Arial;color:#101828;"><b>Total Professional Services Investment</b></td><td style="padding:14px;font: bold 16px Arial;color:#101828;">{professional_total}</td></tr></tbody></table>'
    next_steps = '<div style="margin-top:24px;font:13px Arial;color:#101828;"><h2 style="font-size:16px;">Next steps</h2><p>Review the approved quotation and supporting documents before proceeding with internal billing.</p>'
    for label, url in _sqr_approval_document_links(submission):
        next_steps += f'<p><a href="{html.escape(url, quote=True)}" style="color:#156082;">{label}</a></p>'
    next_steps += '</div>'

    markup = "".join(
        [
            '<html><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"><style type="text/css" style="display:none;"> P {margin-top:0;margin-bottom:0;} </style></head><body dir="ltr">',
            '<div class="elementToProof" style="background-color:rgb(244,247,251);padding:24px 12px;">',
            '<div class="elementToProof" style="background-color:rgb(255,255,255);margin:0 auto;border-width:1px;border-style:solid;border-color:rgb(215,222,232);max-width:860px;">',
            '<div class="elementToProof" style="padding:28px 30px 32px;overflow-wrap:anywhere;">',
            '<h1 style="font:bold 20px Arial;color:#101828;margin:0 0 18px;">SQR Approval Advisory</h1>',
            ''.join(summary_rows),
            f'<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 12px;font-size:14px;"><span style="font-family:Arial,Helvetica,sans-serif;color:rgb(16,24,40);">{greeting}</span></p>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 22px;font-size:14px;"><span style="font-family:Arial,Helvetica,sans-serif;color:rgb(16,24,40);">Submitted SQR is now approved, please refer to the ff. details below.</span></p>',
            '<div class="elementToProof" style="direction:ltr;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:rgb(16,24,40);">',
            f'<img width="193" height="80" style="width:193.667px;height:80px;max-width:1070px;" src="{logo_url}" alt="Phil-Data">',
            '</div>',
            '<table role="presentation" style="direction:ltr;margin:0 0 18px;width:100%;box-sizing:border-box;border-collapse:collapse;border-spacing:0;">',
            '<tbody>',
            '<tr>',
            '<td style="direction:ltr;vertical-align:bottom;">',
            '<div class="elementToProof" style="direction:ltr;letter-spacing:0.08em;font-family:Arial,Helvetica,sans-serif;font-size:11px;font-weight:700;color:rgb(71,84,103);"><span style="letter-spacing:0.08em;">SQR ID</span></div>',
            f'<div class="elementToProof" style="direction:ltr;margin-top:6px;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:rgb(16,24,40);">{html.escape(submission.reference_code or "—")}</div>',
            '</td>',
            '<td style="direction:ltr;text-align:right;vertical-align:bottom;">',
            '<div class="elementToProof" style="direction:ltr;text-align:right;letter-spacing:0.05em;font-family:Arial,Helvetica,sans-serif;font-size:28px;font-weight:700;color:rgb(17,24,39);"><span style="letter-spacing:0.05em;">SERVICE QUOTATION</span></div>',
            '</td>',
            '</tr>',
            '</tbody></table>',
            '<table role="presentation" style="direction:ltr;width:100%;table-layout:fixed;box-sizing:border-box;border-collapse:collapse;border-spacing:0;">',
            '<tbody>',
            '<tr>',
            '<td style="direction:ltr;border-width:1px;border-style:solid;border-color:rgb(207,216,227);padding:14px;vertical-align:top;color:rgb(16,24,40);"><div class="elementToProof" style="direction:ltr;font-family:Arial,Helvetica,sans-serif;font-size:13px;"><b>SQR ID</b> : ' + html.escape(submission.reference_code or "—") + '</div></td>',
            '<td style="direction:ltr;border-width:1px;border-style:solid;border-color:rgb(207,216,227);padding:14px;vertical-align:top;color:rgb(16,24,40);"><div class="elementToProof" style="direction:ltr;font-family:Arial,Helvetica,sans-serif;font-size:13px;"><b>Customer Name</b> : ' + html.escape(submission.customer_name or "—") + '</div></td>',
            '</tr>',
            '<tr>',
            '<td style="direction:ltr;border-width:1px;border-style:solid;border-color:rgb(207,216,227);padding:14px;vertical-align:top;color:rgb(16,24,40);"><div class="elementToProof" style="direction:ltr;font-family:Arial,Helvetica,sans-serif;font-size:13px;"><b>Service Description</b> : ' + html.escape((submission.project_title or "").strip() or "—") + '</div></td>',
            '<td style="direction:ltr;border-width:1px;border-style:solid;border-color:rgb(207,216,227);padding:14px;vertical-align:top;color:rgb(16,24,40);"><div class="elementToProof" style="direction:ltr;font-family:Arial,Helvetica,sans-serif;font-size:13px;"><b>Account Manager</b> : ' + html.escape((submission.customer_contact or "").strip() or "—") + '</div></td>',
            '</tr>',
            '<tr>',
            '<td colspan="2" style="direction:ltr;border-width:1px;border-style:solid;border-color:rgb(207,216,227);padding:14px;vertical-align:top;color:rgb(16,24,40);"><div class="elementToProof" style="direction:ltr;font-family:Arial,Helvetica,sans-serif;font-size:13px;"><b>Scope of Services</b> : ' + html.escape((submission.project_details or "").strip() or "—") + '</div></td>',
            '</tr>',
            '<tr>',
            ('<td colspan="2" style="direction:ltr;border-width:1px;border-style:solid;border-color:rgb(207,216,227);padding:14px;vertical-align:top;color:rgb(16,24,40);"><div class="elementToProof" style="direction:ltr;font-family:Arial,Helvetica,sans-serif;font-size:13px;"><b>Add-On Service</b> : ' + service_component + '</div></td>') if submission.managed_support_amount is not None else '',
            '</tr>',
            '</tbody></table>',
            '<div class="elementToProof" style="direction:ltr;margin-top:18px;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:rgb(16,24,40);"><div style="font-weight:700;">Included Services:</div><ul style="margin:8px 0 0 18px;padding:0;line-height:1.7;">',
            '<li>Proactive System Health Checks</li>',
            '<li>System/Platform Patching (scheduled)</li>',
            '<li>Incident Support</li>',
            '<li>Basic Troubleshooting and Issue Isolation</li>',
            '<li>Monthly System Status Report</li>',
            '<li>Advisory Support</li>',
            '</ul></div>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:12px 0 0;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;"><b>Quantity</b>: 1 Lot</span></p>',
            f'<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:8px 0 0;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;"><b>Approval Date</b> : {approval_date}</span></p>',
            f'<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:8px 0 0;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;"><b>Quotation Validity Until</b>: {validity_date}</span></p>',
            '<div class="elementToProof" style="margin-top:24px;">',
            '<div class="elementToProof" style="direction:ltr;margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:16px;color:rgb(16,24,40);"><span style="font-weight:700;">Professional Services Investment Summary</span></div>',
            costing,
            '</div>',
            '<div class="elementToProof" style="margin-top:24px;">',
            '<div class="elementToProof" style="direction:ltr;margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:16px;color:rgb(16,24,40);"><span style="font-weight:700;">Terms and Conditions</span></div>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 8px;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;">VAT: This quote excludes Value Added Tax (VAT).</span></p>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 8px;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;"><i>For P&amp;L documentation purposes, a VAT-inclusive total may be applied. Internal billing and revenue reporting remain VAT-exclusive.</i></span></p>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 8px;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;">This is a budgetary quote.</span></p>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 8px;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;">This quote is issued for internal billing purposes only.</span></p>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 8px;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;">Travel costs within Metro Manila are included.</span></p>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 8px;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;">This quote does not include hardware, software licenses, or subscriptions unless stated.</span></p>',
            '</div>',
            next_steps,
            '<div class="elementToProof" style="margin-top:22px;padding-top:16px;border-top:1px solid rgb(228,231,236);">',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0 0 6px;font-size:13px;color:rgb(16,24,40);"><span style="font-family:Arial,Helvetica,sans-serif;">For any questions or to discuss this quote further, please don\'t hesitate to contact us:</span></p>',
            '<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:0;font-size:13px;"><span style="font-family:Arial,Helvetica,sans-serif;color:rgb(21,96,130);font-weight:700;"><a href="mailto:EnterpriseServices@phildata.com" style="color:rgb(21,96,130);text-decoration:none;margin-top:0;margin-bottom:0;">EnterpriseServices@phildata.com</a></span></p>',
            '</div>',
            '</div>',
            '</div>',
            '</div>',
            '</body></html>',
        ]
    )
    if submission.managed_support_amount is None:
        start = markup.index('<div class="elementToProof" style="direction:ltr;margin-top:18px;')
        end = markup.index('<p class="elementToProof" style="direction:ltr;line-height:1.6;margin:12px', start)
        markup = markup[:start] + markup[end:]
    return markup


def _get_sqr_approval_email_context(submission: SqrSubmission) -> dict:
    """Build the common preview, MIME, and Outlook advisory payload."""
    engineer = submission.engineer
    engineer_name = (engineer.get_full_name() or engineer.username or "").strip() if engineer else ""
    recipients = _get_sqr_approval_to_emails(submission)
    logo_attachment = _get_sqr_logo_attachment()
    attachments = [logo_attachment] if logo_attachment else []
    return {
        "submission": submission,
        "subject": _format_sqr_approval_subject(submission),
        "recipients": recipients,
        "plain_text": "\n".join(_build_sqr_approval_body_lines(submission, engineer_name)),
        "html_preview": _build_sqr_approval_html(submission, engineer_name),
        "html_draft": _build_sqr_approval_draft_html(submission, engineer_name),
        "attachments": attachments,
    }


def _build_sqr_approval_mailto_url(context: dict) -> str:
    """Encode advisory recipients, subject, and body in a compose URL."""
    recipients = context.get("recipients") or []
    to_recipient = ";".join(recipients)
    cc_recipients = ""
    mailto_parts = [
        f"mailto:{quote(to_recipient)}",
        f"subject={quote(context['subject'])}",
        f"body={quote(context['plain_text'])}",
    ]
    if cc_recipients:
        mailto_parts.insert(1, f"cc={quote(cc_recipients)}")
    return "?".join([mailto_parts[0], "&".join(mailto_parts[1:])])


def _send_sqr_new_submission_email(
    submission: SqrSubmission,
    *,
    http_request: HttpRequest | None = None,
) -> None:
    """Send an email to the assigned PM-ESG reviewer when a new SQR is submitted."""
    reviewer = submission.pm_esg_reviewer
    if not reviewer:
        logger.warning(
            "SQR new-submission email skipped: no PM-ESG reviewer assigned (SQR %s)",
            submission.reference_code,
        )
        return

    reviewer_email = (getattr(reviewer, "email", "") or "").strip()
    if not reviewer_email:
        # Fall back to username if it looks like an email address
        username = (getattr(reviewer, "username", "") or "").strip()
        if "@" in username:
            reviewer_email = username
    if not reviewer_email:
        logger.warning(
            "SQR new-submission email skipped: reviewer %s has no email (SQR %s)",
            getattr(reviewer, "username", "?"),
            submission.reference_code,
        )
        return

    use_acs = bool(settings.ACS_EMAIL_CONNECTION_STRING and settings.ACS_EMAIL_SENDER)
    if not use_acs:
        logger.warning(
            "ACS email not configured; SQR new-submission email skipped for %s",
            submission.reference_code,
        )
        return

    reviewer_name = reviewer.get_full_name() or reviewer.username
    engineer_name = submission.engineer.get_full_name() or submission.engineer.username
    sqr_path = reverse("hub:sqr")
    sqr_url = f"{settings.PUBLIC_BASE_URL}{sqr_path}"

    customer_company = (submission.customer_company or "").strip()
    submitted_date = (
        submission.created_at.astimezone(MANILA_TZ).strftime("%B %d, %Y  %I:%M %p")
        if submission.created_at
        else "—"
    )
    divider = "─" * 56

    body_lines = [
        f"Hi {reviewer_name},",
        "",
        "A new SQR submission has been created and assigned to you for review.",
        "Please review it at your earliest convenience.",
        "",
        divider,
        "  SUBMISSION DETAILS",
        divider,
        f"  Reference:      {submission.reference_code}",
        f"  Submitted By:   {engineer_name}",
        f"  Customer:       {submission.customer_name}",
    ]
    if customer_company:
        body_lines.append(f"  Company/Group:  {customer_company}")
    body_lines += [
        f"  Project Title:  {submission.project_title}",
        f"  Scope of Svc:   {submission.project_details or '—'}",
        f"  Date Submitted: {submitted_date}",
        "",
        divider,
        "  NEXT STEPS",
        divider,
        "  1. Log in to Request Hub and open the SQR Review page.",
        "  2. Review the submission details and quotation.",
        "  3. Approve or request revision with your comments.",
        "",
        f"  View SQR Tracker:  {sqr_url}",
        "",
        divider,
        "This is an automated notification from Request Hub.",
        "Please do not reply directly to this message.",
    ]

    subject = f"[Request Hub] New SQR Submitted — {submission.reference_code} ({submission.customer_name})"
    plain_text = "\n".join(body_lines)

    try:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(settings.ACS_EMAIL_CONNECTION_STRING)
        message = {
            "senderAddress": settings.ACS_EMAIL_SENDER,
            "recipients": {"to": [{"address": reviewer_email}]},
            "content": {
                "subject": subject,
                "plainText": plain_text,
                "html": render_to_string("hub/emails/sqr_new_submission.html", {
                    "notification_title": "New SQR Assigned for Review",
                    "details_heading": "Submission details",
                    "reviewer_name": reviewer_name,
                    "details": [
                        ("Reference", submission.reference_code),
                        ("Submitted By", engineer_name),
                        ("Customer", submission.customer_name),
                        *([("Company/Group", customer_company)] if customer_company else []),
                        ("Project Title", submission.project_title),
                        ("Date Submitted (Manila)", submitted_date),
                    ],
                    "project_details": submission.project_details or "—",
                    "action_label": "Open SQR Tracker",
                    "action_url": sqr_url,
                }),
            },
        }
        poller = client.begin_send(message)
        poller.result()
        logger.info(
            "SQR new-submission email sent to %s for %s",
            reviewer_email,
            submission.reference_code,
        )
    except Exception as exc:
        logger.warning(
            "SQR new-submission email send failed for %s to %s",
            submission.reference_code,
            reviewer_email,
            exc_info=exc,
        )


def _send_sqr_for_revision_email(
    submission: SqrSubmission,
    reviewer_name: str,
    *,
    http_request: HttpRequest | None = None,
) -> None:
    """Send an email to the SQR creator when the status is set to For Revision."""
    engineer = submission.engineer
    engineer_email = (getattr(engineer, "email", "") or "").strip()
    if not engineer_email:
        logger.warning(
            "SQR for-revision email skipped: engineer %s has no email (SQR %s)",
            getattr(engineer, "username", "?"),
            submission.reference_code,
        )
        return

    use_acs = bool(settings.ACS_EMAIL_CONNECTION_STRING and settings.ACS_EMAIL_SENDER)
    if not use_acs:
        logger.warning(
            "ACS email not configured; SQR for-revision email skipped for %s",
            submission.reference_code,
        )
        return

    engineer_name = engineer.get_full_name() or engineer.username
    sqr_edit_path = reverse("hub:sqr")
    sqr_url = f"{settings.PUBLIC_BASE_URL}{sqr_edit_path}"

    review_notes = (submission.review_notes or "").strip()
    customer_company = (submission.customer_company or "").strip()
    reviewed_date = (
        submission.updated_at.strftime("%B %d, %Y  %I:%M %p")
        if submission.updated_at
        else "—"
    )
    divider = "─" * 56

    body_lines = [
        f"Hi {engineer_name},",
        "",
        "Your SQR submission has been reviewed and marked For Revision.",
        "Please address the reviewer's comments and resubmit at your earliest convenience.",
        "",
        divider,
        "  SQR DETAILS",
        divider,
        f"  Reference:      {submission.reference_code}",
        f"  Customer:       {submission.customer_name}",
    ]
    if customer_company:
        body_lines.append(f"  Company/Group:  {customer_company}")
    body_lines += [
        f"  Project Title:  {submission.project_title}",
        f"  Reviewed By:    {reviewer_name}",
        f"  Date Reviewed:  {reviewed_date}",
        "",
        divider,
        "  REVIEWER COMMENTS",
        divider,
        (
            review_notes
            if review_notes
            else "No specific comments were provided. Please follow up with the reviewer for details."
        ),
        "",
        divider,
        "  NEXT STEPS",
        divider,
        "  1. Log in to Request Hub and navigate to your SQR submissions.",
        "  2. Carefully review the comments above.",
        "  3. Update your SQR submission accordingly.",
        "  4. Resubmit for review once the changes are complete.",
        "",
        f"  View SQR Tracker:  {sqr_url}",
        "",
        divider,
        "If you have questions, please reach out to your reviewer directly.",
        "",
        "This is an automated notification from Request Hub.",
        "ESG Request Hub  |  ESGRequestHub@phildata.com",
    ]

    subject = f"[Request Hub] {submission.reference_code} \u2014 Action Required: For Revision"
    plain_text = "\n".join(body_lines)

    try:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(settings.ACS_EMAIL_CONNECTION_STRING)
        message = {
            "senderAddress": settings.ACS_EMAIL_SENDER,
            "recipients": {
                "to": [{"address": engineer_email}],
            },
            "content": {
                "subject": subject,
                "plainText": plain_text,
                "html": render_to_string("hub/emails/sqr_revision.html", {
                    "notification_title": "SQR Requires Revision",
                    "details_heading": "SQR details",
                    "engineer_name": engineer_name,
                    "details": [
                        ("Reference", submission.reference_code),
                        ("Customer", submission.customer_name),
                        *([("Company/Group", customer_company)] if customer_company else []),
                        ("Project Title", submission.project_title),
                        ("Reviewed By", reviewer_name),
                        ("Date Reviewed", reviewed_date),
                    ],
                    "review_notes": review_notes,
                    "action_label": "Open SQR Tracker",
                    "action_url": sqr_url,
                }),
            },
        }
        poller = client.begin_send(message)
        poller.result()
        logger.info(
            "SQR for-revision email sent to %s for %s",
            engineer_email,
            submission.reference_code,
        )
    except Exception as exc:
        logger.warning(
            "SQR for-revision email send failed for %s to %s",
            submission.reference_code,
            engineer_email,
            exc_info=exc,
        )


def _send_sqr_approved_email(
    submission: SqrSubmission,
    reviewer_name: str,
    *,
    http_request: HttpRequest | None = None,
) -> None:
    """Send an email to the SQR creator when the status is set to Approved."""
    engineer = submission.engineer
    engineer_email = (getattr(engineer, "email", "") or "").strip()
    if not engineer_email:
        logger.warning(
            "SQR approved email skipped: engineer %s has no email (SQR %s)",
            getattr(engineer, "username", "?"),
            submission.reference_code,
        )
        return

    use_acs = bool(settings.ACS_EMAIL_CONNECTION_STRING and settings.ACS_EMAIL_SENDER)
    if not use_acs:
        logger.warning(
            "ACS email not configured; SQR approved email skipped for %s",
            submission.reference_code,
        )
        return

    engineer_name = (engineer.get_full_name() or engineer.username or "").strip()
    subject = _format_sqr_approval_subject(submission)
    body_lines = _build_sqr_approval_body_lines(submission, engineer_name)
    plain_text = "\n".join(body_lines)
    html_text = _build_sqr_approval_html(submission, engineer_name)
    to_emails = _get_sqr_approval_to_emails(submission)

    try:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(settings.ACS_EMAIL_CONNECTION_STRING)
        message = {
            "senderAddress": settings.ACS_EMAIL_SENDER,
            "recipients": {
                "to": [{"address": email} for email in to_emails]
            },
            "content": {
                "subject": subject,
                "plainText": plain_text,
                "html": html_text,
            },
        }
        poller = client.begin_send(message)
        poller.result()
        logger.info(
            "SQR approved email sent to %s for %s",
            ", ".join(to_emails),
            submission.reference_code,
        )
    except Exception as exc:
        logger.warning(
            "SQR approved email send failed for %s to %s",
            submission.reference_code,
            engineer_email,
            exc_info=exc,
        )
