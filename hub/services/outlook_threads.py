"""Request acknowledgement and closing drafts, kept in the assignment email's conversation."""
from urllib.parse import quote

import requests

from accounts.models import User
from hub.services.outlook_drafts import TIMEOUT, OutlookError, OutlookSignInRequired, graph_get, safe_draft_link

MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
MESSAGE_FIELDS = "id,conversationId,subject,sentDateTime,receivedDateTime,isDraft,webLink"


class AcknowledgementPending(OutlookError):
    """The acknowledgement exists only as an unsent draft, so a closing reply cannot join its thread yet."""


def assignment_subject(reference_code):
    return f"[Request Hub] New assignment - {reference_code or 'Request'}"


def acknowledgement_subject(request_obj):
    account_name = request_obj.account.name if request_obj.account else "Request"
    return f"Re: {request_obj.reference_code} · {account_name}"


def request_email_access_error(user, request_obj):
    if user.role in User.ENGINEER_ACCESS_ROLES and user not in (request_obj.engineer, request_obj.backup_engineer):
        return "You are not allowed to draft emails for this request."
    return ""


def _graph(token, method, url, payload=None):
    response = requests.request(
        method, url, json=payload, timeout=TIMEOUT, allow_redirects=False,
        headers={"Authorization": f"Bearer {token}", "Prefer": 'IdType="ImmutableId"'},
    )
    if response.status_code == 401:
        raise OutlookSignInRequired("Microsoft 365 connection expired. Sign in again.")
    if response.status_code not in (200, 201):
        raise OutlookError("Unable to create the Microsoft 365 draft. Try again later.")
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise OutlookError("Unable to create the Microsoft 365 draft. Try again later.")
    return data


def _messages_with_subject(token, subject, *, draft):
    escaped = subject.replace("'", "''")
    data = graph_get(token, MESSAGES_URL, {
        "$filter": f"subject eq '{escaped}' and isDraft eq {'true' if draft else 'false'}",
        "$select": MESSAGE_FIELDS,
        "$top": "10",
    })
    values = data.get("value")
    if not isinstance(values, list):
        raise OutlookError("Unable to check Microsoft 365 mail. Try again later.")
    return values


def _newest(messages):
    return max(messages, key=lambda item: item.get("sentDateTime") or item.get("receivedDateTime") or "", default=None)


def _assignment_anchor(token, request_obj):
    return _newest(_messages_with_subject(token, assignment_subject(request_obj.reference_code), draft=False))


def _create_draft(token, anchor, *, subject, to, cc, body):
    payload = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": address}} for address in to],
        "ccRecipients": [{"emailAddress": {"address": address}} for address in cc],
    }
    if anchor is None:
        data = _graph(token, "POST", MESSAGES_URL, payload)
    else:
        reply = _graph(token, "POST", f"{MESSAGES_URL}/{quote(anchor['id'], safe='')}/createReply")
        if not isinstance(reply.get("id"), str):
            raise OutlookError("Unable to create the Microsoft 365 draft. Try again later.")
        data = _graph(token, "PATCH", f"{MESSAGES_URL}/{quote(reply['id'], safe='')}", payload)
    return safe_draft_link(data.get("webLink"))


def create_acknowledgement_draft(token, request_obj, *, to, cc, body):
    """Return (web link, anchored); anchored is False when no assignment email was found."""
    anchor = _assignment_anchor(token, request_obj)
    link = _create_draft(token, anchor, subject=acknowledgement_subject(request_obj), to=to, cc=cc, body=body)
    return link, anchor is not None


def create_closing_draft(token, request_obj, *, to, cc, body):
    """Return (web link, anchored); the acknowledgement is preferred as the anchor."""
    subject = acknowledgement_subject(request_obj)
    anchor = _newest(_messages_with_subject(token, subject, draft=False))
    if anchor is None:
        if _messages_with_subject(token, subject, draft=True):
            raise AcknowledgementPending(
                "Send the acknowledgement draft from Outlook first (or delete it), so the closing email joins the same thread."
            )
        anchor = _assignment_anchor(token, request_obj)
    link = _create_draft(token, anchor, subject=subject, to=to, cc=cc, body=body)
    return link, anchor is not None
