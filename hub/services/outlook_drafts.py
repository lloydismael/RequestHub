"""Delegated mailbox drafts; never sends mail or overwrites an existing draft."""
import hashlib
import uuid
from urllib.parse import quote, urlsplit

import msal
import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction

from hub.models import OutlookConnection, OutlookSqrDraft

SCOPES = ["Mail.ReadWrite"]
GRAPH_URL = "https://graph.microsoft.com/v1.0/me/messages"
MARKER_PROPERTY = "String {5a85cb08-9d53-4ad6-a2c9-ccbf2e125bd1} Name RequestHubSqrDraft"
TIMEOUT = (5, 25)


class OutlookError(Exception):
    """Safe, user-visible error (never includes provider responses)."""


class OutlookSignInRequired(OutlookError):
    pass


SETUP_MESSAGE = "Microsoft 365 drafts need setup: configure SQR_OUTLOOK_TENANT_ID, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI and TOKEN_KEY, with a separate Web app and delegated Mail.ReadWrite permission. Use server-side sessions."


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def outlook_is_configured():
    try:
        tenant = settings.SQR_OUTLOOK_TENANT_ID
        uuid.UUID(tenant)
        uuid.UUID(settings.SQR_OUTLOOK_CLIENT_ID)
        Fernet(settings.SQR_OUTLOOK_TOKEN_KEY.encode("ascii"))
        url = urlsplit(settings.SQR_OUTLOOK_REDIRECT_URI)
        valid_scheme = url.scheme == "https" or (
            settings.DEBUG and url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        return bool(settings.SQR_OUTLOOK_CLIENT_SECRET and valid_scheme and url.hostname
                    and not url.username and not url.password and not url.query and not url.fragment
                    and url.path == "/sqr/outlook/callback/" and url.port != 0
                    and settings.SESSION_ENGINE != "django.contrib.sessions.backends.signed_cookies")
    except (AttributeError, ValueError, TypeError, UnicodeError):
        return False


def require_configuration(request):
    if not outlook_is_configured() or request.session.__class__.__module__ == "django.contrib.sessions.backends.signed_cookies":
        raise OutlookError(SETUP_MESSAGE)
    if not request.session.session_key:
        request.session.save()
    return digest(request.session.session_key)


def client_and_cache(encrypted_cache=None):
    cache = msal.SerializableTokenCache()
    if encrypted_cache:
        try:
            cache.deserialize(Fernet(settings.SQR_OUTLOOK_TOKEN_KEY.encode("ascii")).decrypt(encrypted_cache.encode("ascii")).decode("utf-8"))
        except (InvalidToken, ValueError, UnicodeError):
            raise OutlookSignInRequired("Microsoft 365 connection expired. Sign in again.") from None
    app = msal.ConfidentialClientApplication(
        settings.SQR_OUTLOOK_CLIENT_ID,
        client_credential=settings.SQR_OUTLOOK_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{settings.SQR_OUTLOOK_TENANT_ID}",
        token_cache=cache, timeout=TIMEOUT, enable_pii_log=False,
    )
    return app, cache


def encrypt_cache(cache):
    return Fernet(settings.SQR_OUTLOOK_TOKEN_KEY.encode("ascii")).encrypt(cache.serialize().encode("utf-8")).decode("ascii")


def access_token(request, session_hash):
    with transaction.atomic():
        connection = OutlookConnection.objects.select_for_update().filter(user=request.user, session_hash=session_hash).first()
        if not connection:
            raise OutlookSignInRequired("Sign in to Microsoft 365 to create the draft.")
        if connection.tenant_id.lower() != settings.SQR_OUTLOOK_TENANT_ID.lower():
            raise OutlookSignInRequired("Microsoft 365 connection changed. Sign in again.")
        app, cache = client_and_cache(connection.encrypted_cache)
        account = next((item for item in app.get_accounts()
                        if item.get("local_account_id", "").lower() == connection.mailbox_oid.lower()
                        and item.get("realm", "").lower() == connection.tenant_id.lower()), None)
        result = app.acquire_token_silent(SCOPES, account=account) if account else None
        connection.encrypted_cache = encrypt_cache(cache)
        connection.save(update_fields=["encrypted_cache", "updated_at"])
        if not result or not result.get("access_token"):
            raise OutlookSignInRequired("Microsoft 365 connection expired. Sign in again.")
        return result["access_token"], connection


def safe_draft_link(value):
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and url.hostname in {"outlook.office.com", "outlook.office365.com"}
                and not url.username and not url.password and url.port in (None, 443)
                and not any(character.isspace() or ord(character) < 32 for character in value)
                and "\\" not in value):
            return value
    except (ValueError, TypeError):
        pass
    raise OutlookError("The Microsoft draft link was not valid. Open Outlook Drafts directly.")


def graph_get(token, url, params=None):
    response = requests.get(url, headers={"Authorization": f"Bearer {token}", "Prefer": 'IdType="ImmutableId"'}, params=params,
                            timeout=TIMEOUT, allow_redirects=False)
    if response.status_code == 401:
        raise OutlookSignInRequired("Microsoft 365 connection expired. Sign in again.")
    if response.status_code != 200:
        raise OutlookError("Unable to check Microsoft 365 drafts. Try again later.")
    data = response.json()
    if not isinstance(data, dict):
        raise OutlookError("Unable to check Microsoft 365 drafts. Try again later.")
    return data


def _editable_link(record, data):
    if data.get("isDraft") is not True:
        record.state = "sent"
        record.save(update_fields=["state", "updated_at"])
        raise OutlookError("This advisory is no longer an unsent draft. Open Outlook to review it; no new draft was created.")
    if not isinstance(data.get("id"), str) or not data["id"]:
        raise OutlookError("Unable to identify the Microsoft draft. Open Outlook Drafts directly.")
    record.message_id = data["id"]
    record.state = "ready"
    record.save(update_fields=["message_id", "state", "updated_at"])
    return safe_draft_link(data.get("webLink"))


def create_or_reuse_draft(user, submission, connection, token, context):
    # Commit ownership before any POST. Concurrent workers can only reconcile pending.
    with transaction.atomic():
        record, _ = OutlookSqrDraft.objects.get_or_create(
            user=user, submission=submission, tenant_id=connection.tenant_id,
            mailbox_oid=connection.mailbox_oid, defaults={"correlation_id": uuid.uuid4()},
        )
        record = OutlookSqrDraft.objects.select_for_update().get(pk=record.pk)
        create = record.state == "new"
        if create:
            record.state = "pending"
            record.save(update_fields=["state", "updated_at"])
    if record.state == "sent":
        raise OutlookError("This advisory is no longer an unsent draft. No new draft was created.")
    if record.message_id:
        data = graph_get(token, f"{GRAPH_URL}/{quote(record.message_id, safe='')}", {"$select": "id,isDraft,webLink"})
        return _editable_link(record, data)
    if not create:
        marker_filter = ("singleValueExtendedProperties/Any(ep: ep/id eq '" + MARKER_PROPERTY
                         + "' and ep/value eq '" + str(record.correlation_id) + "')")
        data = graph_get(token, GRAPH_URL, {"$filter": marker_filter, "$select": "id,isDraft,webLink", "$top": "2"})
        matches = data.get("value")
        if not isinstance(matches, list) or len(matches) != 1 or data.get("@odata.nextLink"):
            raise OutlookError("Draft creation is still being checked. Try again later or review Outlook Drafts; no duplicate was created.")
        return _editable_link(record, matches[0])
    payload = {
        "subject": context["subject"],
        "body": {"contentType": "HTML", "content": context["html_draft"]},
        "toRecipients": [{"emailAddress": {"address": email}} for email in context["recipients"] if email],
        "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", **{
            key: attachment[key] for key in ("name", "contentType", "contentBytes", "isInline", "contentId") if key in attachment
        }} for attachment in context.get("attachments", [])],
        "singleValueExtendedProperties": [{"id": MARKER_PROPERTY, "value": str(record.correlation_id)}],
    }
    response = requests.post(GRAPH_URL, headers={"Authorization": f"Bearer {token}", "Prefer": 'IdType="ImmutableId"'},
                             json=payload, timeout=TIMEOUT, allow_redirects=False)
    if response.status_code in (400, 401, 403, 404, 413, 429):
        # These explicit rejections cannot have accepted the create. Everything else stays pending.
        record.state = "new"
        record.save(update_fields=["state", "updated_at"])
        if response.status_code == 401:
            raise OutlookSignInRequired("Microsoft 365 connection expired. Sign in again.")
        raise OutlookError("Microsoft 365 could not create this draft. Check mailbox permissions or try again later.")
    if response.status_code != 201:
        raise OutlookError("Draft creation could not be confirmed. Try again later; Request Hub will check before creating anything else.")
    return _editable_link(record, response.json())
