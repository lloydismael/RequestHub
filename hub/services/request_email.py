"""Request email composition and synchronous assignment delivery."""

from dataclasses import dataclass
from logging import getLogger
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest
from django.template.loader import render_to_string

from accounts.models import User
from hub.mixins import ENGINEER_ACCESS_ROLES
from hub.models import Request
from hub.services import outlook_threads

logger = getLogger("hub.views")


@dataclass(frozen=True)
class AssignmentEmailResult:
    """Outcome of an attempted assignment email, with the selected recipients."""

    status: str
    recipients: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequestEmailDraft:
    """Shared Outlook and fallback-mail content for a request."""

    subject: str
    body: str
    to: list[str]
    cc: list[str]

    def mailto_url(self) -> str:
        """Encode the same recipients and message for the default mail client."""
        query = [f"subject={quote(self.subject)}", f"body={quote(self.body)}"]
        if self.cc:
            query.insert(0, f"cc={quote(','.join(self.cc))}")
        return f"mailto:{','.join(self.to)}?{'&'.join(query)}"


def _draft_cc(request_obj: Request) -> list[str]:
    """Include the shared mailbox, ESS contact and backup as applicable."""
    addresses = {"ESGRequestHub@phildata.com"}
    if request_obj.requestor and request_obj.requestor.role == User.Roles.REQUESTOR_ESS:
        addresses.add("JoanI@phildata.com")
    if request_obj.backup_engineer and request_obj.backup_engineer.email:
        addresses.add(request_obj.backup_engineer.email)
    return sorted(addresses)


def _requestor_name(request_obj: Request) -> str:
    """Use the requestor's name or the recorded account manager as a fallback."""
    if requestor := request_obj.requestor:
        return requestor.get_full_name() or requestor.username
    return request_obj.account_manager or "Requestor"


def build_acknowledgement_draft(
    request_obj: Request, actor: User, *, engineer_email: str, manager_email: str,
) -> RequestEmailDraft:
    """Compose the acknowledgement using the actor's existing wording."""
    assigned_engineer = request_obj.engineer or request_obj.backup_engineer
    assigned_name = (assigned_engineer.get_full_name() or assigned_engineer.username) if assigned_engineer else ""
    description = (request_obj.description or "").strip()
    description_line = description.replace("\r", " ").replace("\n", " ") if description else "No description provided."

    if actor.role in ENGINEER_ACCESS_ROLES:
        intro = "This is to acknowledge your request in Request Hub. We've logged the details below and started processing it."
        update = "I will reach out with updates or any follow-up questions."
    else:
        intro = "This is to acknowledge your request in Request Hub. We've logged the details below and started processing it"
        update = f"{assigned_name or 'our engineering team'} is looped in and will reach out with updates or any follow-up questions."
    body = (
        f"Hello {_requestor_name(request_obj)},\n\n"
        f"{intro}\n\n"
        f"Reference: {request_obj.reference_code}\n"
        f"Request Type: {request_obj.get_engagement_type_display()}\n"
        f"Product: {request_obj.get_product_category_display()}\n"
        f"Description: {description_line}\n\n"
        f"{update}\n"
        "If you have additional information or questions, simply reply to this email and we'll continue the thread.\n"
    )
    return RequestEmailDraft(
        outlook_threads.acknowledgement_subject(request_obj), body,
        sorted({engineer_email, manager_email}), _draft_cc(request_obj),
    )


def build_closing_draft(
    request_obj: Request, *, engineer_email: str, manager_email: str,
) -> RequestEmailDraft:
    """Compose the closing draft on the acknowledgement's existing subject."""
    detail_url = f"{settings.PUBLIC_BASE_URL}{request_obj.get_absolute_url()}"
    reference = request_obj.reference_code
    body = (
        f"Hello {_requestor_name(request_obj)},\n\n"
        f"This is to confirm that {reference} has been completed and closed.\n\n"
        f"Request details: {reference} — {detail_url}\n\n"
        f"If additional assistance is needed, please create a new request through {settings.PUBLIC_BASE_URL}\n\n"
        "Thanks,\n"
        "ESG"
    )
    return RequestEmailDraft(
        outlook_threads.acknowledgement_subject(request_obj), body,
        sorted({engineer_email, manager_email}), _draft_cc(request_obj),
    )


def notify_engineer_assignment_email(
    request_obj: Request,
    *,
    actor_user: User,
    request: HttpRequest | None = None,
    previous_engineer_id: int | None = None,
    previous_backup_id: int | None = None,
) -> AssignmentEmailResult:
    """Email the assigned engineer or backup when they receive a request."""

    recipients: list[str] = []
    missing_email_roles: list[str] = []

    if request_obj.engineer_id and request_obj.engineer_id != previous_engineer_id:
        email = (request_obj.engineer.email or "").strip()
        if email:
            recipients.append(email)
        else:
            missing_email_roles.append("engineer")

    if request_obj.backup_engineer_id and request_obj.backup_engineer_id != previous_backup_id:
        email = (request_obj.backup_engineer.email or "").strip()
        if email:
            recipients.append(email)
        else:
            missing_email_roles.append("backup engineer")

    if not recipients:
        if missing_email_roles:
            logger.warning(
                "Assignment email skipped: assigned recipient missing email for %s (%s)",
                request_obj.reference_code,
                ", ".join(missing_email_roles),
            )
            return AssignmentEmailResult("missing_assignee_email")
        logger.info("Assignment email skipped: no engineer/backup recipient for %s", request_obj.reference_code)
        return AssignmentEmailResult("no_new_assignee")

    actor_name = actor_user.get_full_name() or actor_user.username or "Request Hub"
    due_display = request_obj.due_date.strftime("%b %d, %Y") if request_obj.due_date else "Not set"
    detail_url = f"{settings.PUBLIC_BASE_URL}{request_obj.get_absolute_url()}"

    subject = outlook_threads.assignment_subject(request_obj.reference_code)
    body_lines = [
        "You have been assigned a request in Request Hub.",
        "",
        f"Reference: {request_obj.reference_code or 'Request'}",
        f"Account: {request_obj.account.name if request_obj.account else 'Not set'}",
        f"Engagement: {request_obj.get_engagement_type_display()}",
        f"Priority: {request_obj.get_priority_display()}",
        f"Due date: {due_display}",
        f"Assigned by: {actor_name}",
    ]

    description = (request_obj.description or "").strip()
    if description:
        body_lines.extend(["", "Description:", description])

    if detail_url:
        body_lines.extend(["", f"View request: {detail_url}"])

    use_acs = bool(settings.ACS_EMAIL_CONNECTION_STRING and settings.ACS_EMAIL_SENDER)

    if not use_acs:
        logger.warning("ACS email not configured; set ACS_EMAIL_CONNECTION_STRING and ACS_EMAIL_SENDER")
        return AssignmentEmailResult("missing_acs_config", tuple(recipients))

    try:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(settings.ACS_EMAIL_CONNECTION_STRING)
        message = {
            "senderAddress": settings.ACS_EMAIL_SENDER,
            "recipients": {"to": [{"address": addr} for addr in recipients]},
            "content": {
                "subject": subject,
                "plainText": "\n".join(body_lines),
                "html": render_to_string("hub/emails/engineer_assignment.html", {
                    "notification_title": "New Request Assignment",
                    "details_heading": "Request details",
                    "details": [
                        ("Reference", request_obj.reference_code or "Request"),
                        ("Account", request_obj.account.name if request_obj.account else "Not set"),
                        ("Engagement", request_obj.get_engagement_type_display()),
                        ("Priority", request_obj.get_priority_display()),
                        ("Due date", due_display),
                        ("Assigned by", actor_name),
                    ],
                    "description": description,
                    "action_label": "View Request",
                    "action_url": detail_url,
                }),
            },
        }
        poller = client.begin_send(message)
        poller.result()
        return AssignmentEmailResult("sent", tuple(recipients))
    except Exception as exc:
        logger.warning(
            "ACS email send failed for %s to %s",
            request_obj.reference_code,
            ", ".join(recipients),
            exc_info=exc,
        )
        return AssignmentEmailResult("delivery_failed", tuple(recipients))
