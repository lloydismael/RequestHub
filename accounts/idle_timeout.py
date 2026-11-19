from datetime import timedelta
from math import ceil

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from accounts.models import SessionIdleDeadline, User

ACTIVITY_HEADER = "X-Request-Hub-Activity"
REMAINING_HEADER = "X-Request-Hub-Idle-Remaining"
IDLE_LOGOUT_REASON = "idle"
DATABASE_SESSIONS = "django.contrib.sessions.backends.db"


class IdleAwareSessionStore(SessionStore):
    """Read the idle deadline with Django's normal session fetch, not its mutable payload."""

    @property
    def key_salt(self):
        return "django.contrib.sessions.SessionStore"

    def _idle_session_rows(self):
        return self.model.objects.filter(pk=self.session_key, expire_date__gt=timezone.now()).values_list(
            "session_data", "expire_date", "idle_timeout__deadline",
        )

    def _load_idle_snapshot(self, row):
        if row is None:
            self._session_key = None
            return {}
        data, expiry, deadline = row
        self.idle_snapshot = (self.session_key, expiry, deadline)
        return self.decode(data)

    def load(self):
        return self._load_idle_snapshot(self._idle_session_rows().first())

    async def aload(self):
        return self._load_idle_snapshot(await self._idle_session_rows().afirst())


def _new_deadline(now):
    return now + timedelta(seconds=settings.IDLE_TIMEOUT_SECONDS)


def start_idle_timeout(session_key, user):
    # Called at login with a saved session key. Normal activity never reinitializes a deadline.
    if user.idle_timeout_enabled and settings.SESSION_ENGINE == DATABASE_SESSIONS:
        with transaction.atomic():
            if Session.objects.select_for_update().filter(pk=session_key).exists():
                SessionIdleDeadline.objects.get_or_create(
                    session_id=session_key, defaults={"deadline": _new_deadline(timezone.now())},
                )


def check_idle_timeout(request, *, renew=False, allow_snapshot=False):
    """Return remaining milliseconds, None for opt-out, and zero after ending an expired session."""
    snapshot = getattr(request.session, "idle_snapshot", None) if allow_snapshot else None
    now = timezone.now()
    if not renew and snapshot and snapshot[0] == request.session.session_key and snapshot[1] > now:
        # A live request may use its authentication snapshot. Always recheck due or mutating requests.
        deadline = snapshot[2]
        if request.user.idle_timeout_enabled and deadline is not None and deadline > now:
            remaining = ceil((deadline - now).total_seconds() * 1000)
        elif not request.user.idle_timeout_enabled and deadline is None:
            remaining = None
        else:
            remaining = _read_idle_timeout(request, renew=renew)
    else:
        remaining = _read_idle_timeout(request, renew=renew)
    request.idle_remaining_ms = remaining
    return remaining


def _read_idle_timeout(request, *, renew):
    if settings.SESSION_ENGINE != DATABASE_SESSIONS:
        return None
    session_key = request.session.session_key
    with transaction.atomic():
        # Serialize renewal and revocation on the parent, including while the deadline is absent.
        expiry = (
            Session.objects.select_for_update()
            .filter(pk=session_key).values_list("expire_date", flat=True).first()
        )
        now = timezone.now()
        if expiry is None or expiry <= now:
            # Another request ended this session, or its normal Django lifetime has elapsed.
            # Don't flush here: a stale response should not clear a replacement login's cookie.
            return 0
        enabled = User.objects.filter(pk=request.user.pk, idle_timeout_enabled=True).exists()
        request.user.idle_timeout_enabled = enabled
        deadlines = SessionIdleDeadline.objects.filter(session_id=session_key)
        idle = deadlines.first()
        if not enabled:
            if idle is not None:
                idle.delete()
            return None
        if idle is None:
            # A session predating rollout, newly enabled, or rotated by Django gets one window.
            idle = SessionIdleDeadline.objects.create(session_id=session_key, deadline=_new_deadline(now))
        elif idle.deadline <= now:
            request.idle_timeout_expired = True
            logout(request)
            return 0
        elif renew:
            idle.deadline = max(idle.deadline, _new_deadline(now))
            idle.save(update_fields=["deadline"])
        return ceil((idle.deadline - now).total_seconds() * 1000)


def idle_expired_response(request):
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"code": "idle_timeout", "login_url": reverse("login")}, status=401)
    return redirect("login")
