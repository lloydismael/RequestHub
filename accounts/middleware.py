from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

from accounts.idle_timeout import (
    DATABASE_SESSIONS, REMAINING_HEADER, IdleAwareSessionStore, check_idle_timeout, idle_expired_response,
)

FORCE_PASSWORD_CHANGE_SESSION_KEY = "_force_password_change_prompt"


def _is_exempt(view_name: str) -> bool:
    if not view_name:
        return False
    exempt = set(settings.PROFILE_COMPLETION_EXEMPT_URLS)
    exempt.update({"login", "legacy-login", "logout", "accounts:update", "healthz"})
    if view_name in exempt:
        return True
    if view_name.startswith("admin:"):
        return True
    return False


def _get_view_name(request):
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    return match.view_name


def _is_password_change_exempt(view_name: str | None) -> bool:
    return view_name in {
        "accounts:update", "accounts:sign-in-heartbeat", "logout", "admin:logout",
        "login", "legacy-login", "healthz",
    }


def _is_assistant_api(view_name: str | None) -> bool:
    return bool(view_name and view_name.rsplit(":", 1)[-1].startswith("assistant-"))


def _is_static_or_media_request(request) -> bool:
    path = request.path_info or ""
    static_url = getattr(settings, "STATIC_URL", "") or ""
    media_url = getattr(settings, "MEDIA_URL", "") or ""

    def normalize(prefix: str) -> str:
        if not prefix:
            return ""
        if not prefix.startswith("/"):
            return f"/{prefix}"
        return prefix

    static_prefix = normalize(static_url)
    media_prefix = normalize(media_url)
    return (
        (static_prefix and path.startswith(static_prefix))
        or (media_prefix and path.startswith(media_prefix))
    )


class ProfileCompletionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _is_static_or_media_request(request):
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user and user.is_authenticated and user.must_complete_profile():
            view_name = _get_view_name(request)
            if not _is_exempt(view_name):
                return redirect("accounts:update")
        return self.get_response(request)


class IdleAwareSessionMiddleware(SessionMiddleware):
    def __init__(self, get_response):
        super().__init__(get_response)
        if settings.SESSION_ENGINE == DATABASE_SESSIONS:
            self.SessionStore = IdleAwareSessionStore


class IdleTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and check_idle_timeout(request, allow_snapshot=True) == 0:
            return idle_expired_response(request)

        response = self.get_response(request)
        remaining = getattr(request, "idle_remaining_ms", None)
        if request.user.is_authenticated and remaining:
            response.headers[REMAINING_HEADER] = str(remaining)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if (
            request.user.is_authenticated
            and request.user.idle_timeout_enabled
            and request.headers.get("Sec-Fetch-Mode") == "navigate"
            and request.headers.get("Sec-Fetch-User") == "?1"
            and request.headers.get("Sec-Fetch-Site") in {"same-origin", "none"}
            and request.resolver_match.view_name not in {"logout", "admin:logout", "accounts:sign-in-heartbeat"}
        ):
            if check_idle_timeout(request, renew=True) == 0:
                return idle_expired_response(request)


class PasswordChangeRequiredMiddleware:
    SESSION_KEY = FORCE_PASSWORD_CHANGE_SESSION_KEY

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _is_static_or_media_request(request):
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user and user.is_authenticated and getattr(user, "must_change_password", False):
            view_name = _get_view_name(request)
            if not _is_password_change_exempt(view_name):
                if _is_assistant_api(view_name):
                    return JsonResponse(
                        {
                            "status": "error",
                            "code": "password_change_required",
                            "message": "Change your password before using the assistant.",
                            "request_id": str(uuid4()),
                            "profile_url": reverse("accounts:update"),
                        },
                        status=403,
                    )
                if not request.session.get(self.SESSION_KEY):
                    messages.warning(
                        request,
                        "You are still using the default password. Create a new password now to secure your account.",
                    )
                    request.session[self.SESSION_KEY] = True
                return redirect("accounts:update")
        else:
            if request.session.get(self.SESSION_KEY):
                request.session.pop(self.SESSION_KEY, None)
        return self.get_response(request)
