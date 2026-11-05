from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

FORCE_PASSWORD_CHANGE_SESSION_KEY = "_force_password_change_prompt"


def _is_exempt(view_name: str) -> bool:
    if not view_name:
        return False
    exempt = set(settings.PROFILE_COMPLETION_EXEMPT_URLS)
    exempt.update({"login", "logout", "accounts:update", "healthz"})
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


def _is_password_change_exempt(view_name: str) -> bool:
    if not view_name:
        return False
    exempt = {"accounts:update", "accounts:sign-in-heartbeat", "logout", "login", "healthz"}
    if view_name in exempt:
        return True
    if view_name.startswith("admin:"):
        return True
    return False


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
