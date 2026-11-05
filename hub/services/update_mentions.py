import logging
import re
from functools import partial

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string

from accounts.models import User
from hub.models import Notification

logger = logging.getLogger(__name__)
MENTION_PATTERN = re.compile(r"(?<![\w@.+-])@([\w.+-]+(?:@[\w.-]+)?)")


def mention_handles(message):
    return {match.group(1).rstrip(".").casefold() for match in MENTION_PATTERN.finditer(message or "")}


def request_people(request_obj):
    if not request_obj or not request_obj.pk:
        return User.objects.none()
    return User.objects.filter(is_active=True).filter(
        Q(pk__in=[request_obj.requestor_id, request_obj.engineer_id, request_obj.backup_engineer_id])
        | Q(role__in=[User.Roles.ADMIN, User.Roles.PM_ESG])
    ).order_by("first_name", "last_name", "username")


def mention_people_data(request_obj, *, exclude_user=None):
    people = request_people(request_obj)
    if exclude_user:
        people = people.exclude(pk=exclude_user.pk)
    return [
        {"id": user.pk, "username": user.username, "name": user.get_full_name() or user.username, "role": user.get_role_display()}
        for user in people
    ]


def send_mention_email(log, recipient):
    author = log.author.get_full_name() or log.author.username
    request_obj = log.request
    url = f"{settings.PUBLIC_BASE_URL}{request_obj.get_absolute_url()}#update-{log.pk}"
    subject = f"{request_obj.reference_code}: You were mentioned in an update"
    body = f"{author} mentioned you in an update on {request_obj.reference_code}.\n\n{log.message}\n\nView update: {url}"
    email_html = render_to_string("hub/emails/engineer_assignment.html", {
        "notification_title": "You were mentioned",
        "details_heading": "Request update",
        "details": [("Reference", request_obj.reference_code), ("Mentioned by", author)],
        "description": log.message,
        "action_label": "View Update",
        "action_url": url,
    })
    if not recipient.email:
        return "missing_email"
    try:
        if settings.ACS_EMAIL_CONNECTION_STRING and settings.ACS_EMAIL_SENDER:
            from azure.communication.email import EmailClient

            client = EmailClient.from_connection_string(settings.ACS_EMAIL_CONNECTION_STRING)
            result = client.begin_send({
                "senderAddress": settings.ACS_EMAIL_SENDER,
                "recipients": {"to": [{"address": recipient.email}]},
                "content": {"subject": subject, "plainText": body, "html": email_html},
            }).result(timeout=30)
            if result.get("status") != "Succeeded":
                logger.warning("Mention email was not accepted for update %s", log.pk)
                return "delivery_failed"
        elif settings.EMAIL_BACKEND not in {
            "django.core.mail.backends.console.EmailBackend",
            "django.core.mail.backends.dummy.EmailBackend",
            "django.core.mail.backends.filebased.EmailBackend",
        }:
            email = EmailMultiAlternatives(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient.email])
            email.attach_alternative(email_html, "text/html")
            if email.send() != 1:
                return "delivery_failed"
        else:
            logger.warning("Mention email is not configured for update %s", log.pk)
            return "missing_configuration"
    except Exception:
        logger.exception("Mention email delivery failed for update %s", log.pk)
        return "delivery_failed"
    return "sent"


def notify_update(log, source_label, *, previous_message=None, email_feedback=None):
    people = list(request_people(log.request))
    handles = mention_handles(log.message)
    old_handles = mention_handles(previous_message) if previous_message is not None else set()
    author_name = log.author.get_full_name() or log.author.username
    for user in people:
        if user.pk == log.author_id:
            continue
        mentioned = user.username.casefold() in handles
        new_mention = mentioned and user.username.casefold() not in old_handles
        if previous_message is not None and not new_mention:
            continue
        if mentioned:
            notification, created = Notification.objects.get_or_create(
                recipient=user,
                event_key=f"update:{log.pk}:mention:{user.pk}",
                defaults={
                    "message": f"{author_name} mentioned you in an update on {log.request.reference_code}."[:255],
                    "related_request": log.request,
                    "actor": author_name,
                    "source": source_label,
                    "event_type": Notification.EventType.MENTION,
                },
            )
            if created:
                def deliver(recipient):
                    result = send_mention_email(log, recipient)
                    if email_feedback and result != "sent":
                        email_feedback(result)
                transaction.on_commit(partial(deliver, user))
        else:
            Notification.objects.create(
                recipient=user,
                message=f"{author_name} posted an update on {log.request.reference_code or 'a request'}."[:255],
                related_request=log.request,
                actor=author_name,
                source=source_label,
            )
