"""Sign-in activity: records sign-ins, failed attempts, sign-outs and session heartbeats."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from functools import wraps

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.dispatch import receiver
from django.utils import timezone

from accounts.models import SignInLog, User

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 60
ACTIVE_WINDOW = timedelta(minutes=10)
IDLE_LOGOUT_REASON = "idle"
INVALID_CREDENTIALS_REASON = "Invalid username or password"

STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"
STATUS_INACTIVE = "inactive"
STATUS_FAILED = "failed"
SIGN_IN_STATUS_CHOICES = (
    ("all", "All statuses"),
    (STATUS_ACTIVE, "Active"),
    (STATUS_ENDED, "Ended"),
    (STATUS_INACTIVE, "Inactive"),
    (STATUS_FAILED, "Failed"),
)
SEARCH_FIELDS = ("username", "user__first_name", "user__last_name", "user__email", "ip_address", "browser", "operating_system")
_STATUS_PRESENTATION = {
    STATUS_ACTIVE: ("Active", "text-bg-success"),
    STATUS_ENDED: ("Signed out", "text-bg-secondary"),
    STATUS_INACTIVE: ("No sign-out", "text-bg-info"),
    STATUS_FAILED: ("Failed", "text-bg-danger"),
}

_BROWSER_RULES = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/|Chromium/")),
    ("Safari", re.compile(r"Safari/")),
)
_OS_RULES = (
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Android", re.compile(r"Android")),
    ("Windows", re.compile(r"Windows")),
    ("macOS", re.compile(r"Macintosh|Mac OS X")),
    ("ChromeOS", re.compile(r"CrOS")),
    ("Linux", re.compile(r"Linux")),
)


def client_ip(request) -> str:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR") or "unknown"
    return request.META.get("REMOTE_ADDR") or "unknown"


def parse_user_agent(user_agent: str) -> tuple[str, str, str]:
    """Return (browser, operating system, device type) for a User-Agent string."""
    if not user_agent:
        return "", "", ""
    browser = next((name for name, pattern in _BROWSER_RULES if pattern.search(user_agent)), "Other")
    operating_system = next((name for name, pattern in _OS_RULES if pattern.search(user_agent)), "Other")
    if re.search(r"iPad|Tablet", user_agent) or ("Android" in user_agent and "Mobile" not in user_agent):
        device_type = "Tablet"
    elif re.search(r"Mobile|iPhone|iPod", user_agent):
        device_type = "Mobile"
    else:
        device_type = "Desktop"
    return browser, operating_system, device_type


def format_elapsed(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def touch_sign_in_session(session_key: str | None) -> None:
    """Refresh last_seen_at for an open session, at most once per heartbeat interval."""
    if not session_key or not cache.add(f"sign-in-heartbeat:{session_key}", 1, HEARTBEAT_INTERVAL_SECONDS):
        return
    SignInLog.objects.filter(
        session_key=session_key,
        outcome=SignInLog.Outcome.SIGNED_IN,
        signed_out_at__isnull=True,
    ).update(last_seen_at=timezone.now())


def sign_in_status(log: SignInLog, now) -> str:
    if log.outcome == SignInLog.Outcome.FAILED:
        return STATUS_FAILED
    if log.signed_out_at:
        return STATUS_ENDED
    if log.last_seen_at and log.last_seen_at >= now - ACTIVE_WINDOW:
        return STATUS_ACTIVE
    return STATUS_INACTIVE


def status_filter(status: str, now) -> Q:
    """Database mirror of sign_in_status() for filtering."""
    cutoff = now - ACTIVE_WINDOW
    signed_in = Q(outcome=SignInLog.Outcome.SIGNED_IN)
    open_session = signed_in & Q(signed_out_at__isnull=True)
    if status == STATUS_ACTIVE:
        return open_session & Q(last_seen_at__gte=cutoff)
    if status == STATUS_INACTIVE:
        return open_session & (Q(last_seen_at__lt=cutoff) | Q(last_seen_at__isnull=True))
    if status == STATUS_ENDED:
        return signed_in & Q(signed_out_at__isnull=False)
    if status == STATUS_FAILED:
        return Q(outcome=SignInLog.Outcome.FAILED)
    return Q()


def filter_sign_in_logs(logs, *, query: str, status: str, now):
    for term in query.split():
        match = Q()
        for field in SEARCH_FIELDS:
            match |= Q(**{f"{field}__icontains": term})
        logs = logs.filter(match)
    if status != "all":
        logs = logs.filter(status_filter(status, now))
    return logs


def annotate_sign_in_logs(logs, now) -> list[SignInLog]:
    """Attach display fields and elapsed time to each log row."""
    rows = list(logs)
    role_labels = dict(User.Roles.choices)
    for log in rows:
        status = sign_in_status(log, now)
        log.status = status
        log.status_label, log.status_badge = _status_presentation(log, status)
        log.is_live = status == STATUS_ACTIVE
        log.display_name = _display_name(log)
        log.role_label = role_labels.get(log.role, "")
        log.department = log.user.department if log.user else ""
        if status == STATUS_FAILED:
            log.end_at = None
            log.elapsed_seconds = None
        else:
            log.end_at = {STATUS_ENDED: log.signed_out_at, STATUS_INACTIVE: log.last_seen_at}.get(status)
            end = now if status == STATUS_ACTIVE else log.end_at
            log.elapsed_seconds = max(int((end - log.started_at).total_seconds()), 0) if end else None
        log.elapsed_label = format_elapsed(log.elapsed_seconds) if log.elapsed_seconds is not None else "—"
    return rows


def summarize_sign_ins(now) -> dict[str, int]:
    logs = SignInLog.objects.all()
    since = now - timedelta(hours=24)
    return {
        "active": logs.filter(status_filter(STATUS_ACTIVE, now)).count(),
        "signed_in_24h": logs.filter(outcome=SignInLog.Outcome.SIGNED_IN, started_at__gte=since).count(),
        "failed_24h": logs.filter(outcome=SignInLog.Outcome.FAILED, started_at__gte=since).count(),
        "total": logs.count(),
    }


def _status_presentation(log: SignInLog, status: str) -> tuple[str, str]:
    if status == STATUS_ENDED and log.end_reason == SignInLog.EndReason.IDLE_TIMEOUT:
        return "Idle timeout", "text-bg-warning"
    return _STATUS_PRESENTATION[status]


def _display_name(log: SignInLog) -> str:
    if log.user:
        return log.user.get_full_name() or log.user.get_username()
    return log.username or "Unknown"


def _request_details(request) -> dict[str, str]:
    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:500] if request else ""
    browser, operating_system, device_type = parse_user_agent(user_agent)
    return {
        "ip_address": client_ip(request)[:45] if request else "",
        "user_agent": user_agent,
        "browser": browser,
        "operating_system": operating_system,
        "device_type": device_type,
    }


def _session_key(request) -> str:
    session = getattr(request, "session", None)
    if session is None:
        return ""
    if not session.session_key:
        # login() flushes the session when switching accounts, so the key only exists once saved.
        session.save()
    return session.session_key or ""


def _never_block_auth(handler):
    """Audit writes must not fail sign-in or sign-out when the log table is unavailable."""

    @wraps(handler)
    def wrapper(*args, **kwargs):
        try:
            with transaction.atomic():
                handler(*args, **kwargs)
        except DatabaseError:
            logger.exception("Sign-in activity could not be recorded.")

    return wrapper


@receiver(user_logged_in)
@_never_block_auth
def record_sign_in(sender, request, user, **kwargs):
    session_key = _session_key(request)
    if not session_key:
        return
    now = timezone.now()
    SignInLog.objects.create(
        user=user,
        username=user.get_username(),
        role=user.role,
        session_key=session_key,
        started_at=now,
        last_seen_at=now,
        **_request_details(request),
    )


@receiver(user_login_failed)
@_never_block_auth
def record_failed_sign_in(sender, credentials=None, request=None, **kwargs):
    username = str((credentials or {}).get("username") or "").strip()
    SignInLog.objects.create(
        username=username[:150],
        outcome=SignInLog.Outcome.FAILED,
        failure_reason=INVALID_CREDENTIALS_REASON,
        started_at=timezone.now(),
        **_request_details(request),
    )


@receiver(user_logged_out)
@_never_block_auth
def record_sign_out(sender, request, user=None, **kwargs):
    session_key = getattr(getattr(request, "session", None), "session_key", None)
    if not session_key:
        return
    now = timezone.now()
    is_idle = request.POST.get("reason") == IDLE_LOGOUT_REASON
    SignInLog.objects.filter(
        session_key=session_key,
        outcome=SignInLog.Outcome.SIGNED_IN,
        signed_out_at__isnull=True,
    ).update(
        signed_out_at=now,
        last_seen_at=now,
        end_reason=SignInLog.EndReason.IDLE_TIMEOUT if is_idle else SignInLog.EndReason.SIGNED_OUT,
    )
