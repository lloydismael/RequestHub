from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import UpdateView

from accounts.middleware import FORCE_PASSWORD_CHANGE_SESSION_KEY
from accounts.models import StoredFile, User
from accounts.sign_in_log import client_ip as _client_ip, touch_sign_in_session
from .recovery_views import recovery_context

from .forms import (
    ProfileForm,
    ThemeForm,
    RoleAuthenticationForm,
)

_PROFILE_PHOTO_PREFIX = "profile_photos/"
LOGIN_THROTTLE_LIMIT = 5
LOGIN_THROTTLE_TIMEOUT = 15 * 60
LOGIN_THROTTLE_MESSAGE = "Too many failed login attempts. Try again later."


def _safe_download_filename(filename: str) -> str:
    cleaned = (filename or "download").replace("\\", "/").split("/")[-1]
    for char in ("\r", "\n", '"'):
        cleaned = cleaned.replace(char, "")
    cleaned = cleaned.strip() or "download"
    return cleaned


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "accounts/profile_form.html"
    success_url = reverse_lazy("accounts:update")

    def get_object(self):
        return User.objects.get(pk=self.request.user.pk)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["is_admin"] = self.request.user.role == "admin"
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("theme_form", ThemeForm(instance=self.request.user))
        if self.request.user.role == User.Roles.ADMIN:
            ctx.update(recovery_context(self.request))
        return ctx

    def post(self, request, *args, **kwargs):
        if request.POST.get("save_theme") or request.POST.get("save_banner"):
            self.object = self.get_object()
            theme_form = ThemeForm(request.POST, instance=self.object)
            if theme_form.is_valid():
                cleaned = theme_form.cleaned_data
                self.object.banner_gradient = cleaned["banner_gradient"]
                update_fields = ["banner_gradient"]
                if cleaned["banner_gradient"] == User.CUSTOM_THEME:
                    for name in ThemeForm.CUSTOM_FIELDS:
                        setattr(self.object, name, cleaned[name])
                    update_fields.extend(ThemeForm.CUSTOM_FIELDS)
                self.object.save(update_fields=update_fields)
                messages.success(request, "Theme applied to your account.")
                return redirect(reverse_lazy("accounts:update") + "#themes")
            return self.render_to_response(self.get_context_data(theme_form=theme_form))
        # Settings tab has its own lightweight POST — handle before the main form
        if request.POST.get("save_settings"):
            user = request.user
            # Behavior
            user.show_chatbot = request.POST.get("show_chatbot") == "1"
            user.idle_timeout_enabled = request.POST.get("idle_timeout_enabled") == "1"
            user.show_login_banner = request.POST.get("show_login_banner") == "1"
            # Appearance / Performance / Accessibility
            user.pref_compact_tables = request.POST.get("pref_compact_tables") == "1"
            user.pref_large_text = request.POST.get("pref_large_text") == "1"
            user.pref_reduce_effects = request.POST.get("pref_reduce_effects") == "1"
            user.pref_reduce_motion = request.POST.get("pref_reduce_motion") == "1"
            user.save(update_fields=[
                "show_chatbot", "idle_timeout_enabled", "show_login_banner",
                "pref_compact_tables", "pref_large_text",
                "pref_reduce_effects", "pref_reduce_motion",
            ])
            if request.headers.get("Accept") == "application/json":
                return JsonResponse({"message": "Settings saved."})
            messages.success(request, "Settings saved.")
            return redirect(reverse_lazy("accounts:update") + "#settings")
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        password_changed = bool(form.cleaned_data.get("new_password1"))
        delete_photo_requested = form.data.get("delete_photo")
        photo_deleted = False

        if delete_photo_requested:
            existing_photo = form.instance.profile_photo
            if existing_photo:
                existing_photo.delete(save=False)
                photo_deleted = True
            form.instance.profile_photo = None
            if "profile_photo" in form.cleaned_data:
                form.cleaned_data["profile_photo"] = None

        response = super().form_valid(form)
        user = self.request.user
        user.profile_completed = True
        user.save(update_fields=["profile_completed"])
        if password_changed:
            self.request.session.pop(FORCE_PASSWORD_CHANGE_SESSION_KEY, None)
            update_session_auth_hash(self.request, self.object)
        message_details = []
        if password_changed:
            message_details.append("Your password has been changed")
        if photo_deleted:
            message_details.append("Profile photo deleted")
        if message_details:
            details_text = " ".join(f"{detail}." for detail in message_details)
            messages.success(self.request, f"Profile updated successfully. {details_text}")
        else:
            messages.success(self.request, "Profile updated successfully.")
        return response


@method_decorator(never_cache, name="dispatch")
@method_decorator(ensure_csrf_cookie, name="dispatch")
class RoleLoginView(LoginView):
    authentication_form = RoleAuthenticationForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def _throttle_key(self) -> str:
        username = (self.request.POST.get("username") or "").strip().lower()
        return f"login-fail:{_client_ip(self.request)}:{username}"

    def _failure_count(self) -> int:
        try:
            return int(cache.get(self._throttle_key()) or 0)
        except (TypeError, ValueError):
            return 0

    def _is_throttled(self) -> bool:
        return self._failure_count() >= LOGIN_THROTTLE_LIMIT

    def _record_failure(self) -> int:
        key = self._throttle_key()
        if cache.add(key, 1, LOGIN_THROTTLE_TIMEOUT):
            return 1
        try:
            return int(cache.incr(key))
        except ValueError:
            cache.set(key, 1, LOGIN_THROTTLE_TIMEOUT)
            return 1

    def confirm_login_allowed(self, user):
        selected_role = self.cleaned_data.get("role")
        if selected_role and user.role != selected_role:
            raise forms.ValidationError(
                "Selected role does not match the provided credentials.",
                code="invalid_role",
            )
        super().confirm_login_allowed(user)

    def form_valid(self, form):
        if self._is_throttled():
            form.add_error(None, LOGIN_THROTTLE_MESSAGE)
            return super().form_invalid(form)
        cache.delete(self._throttle_key())
        response = super().form_valid(form)
        if self.request.user.show_login_banner:
            messages.success(self.request, "Login successful. Redirecting to your dashboard\u2026", extra_tags="login-success")
        return response

    def form_invalid(self, form):
        if self._is_throttled():
            form.add_error(None, LOGIN_THROTTLE_MESSAGE)
        else:
            self._record_failure()
            if self._is_throttled():
                form.add_error(None, LOGIN_THROTTLE_MESSAGE)
        return super().form_invalid(form)


class SignInHeartbeatView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        touch_sign_in_session(request.session.session_key)
        return HttpResponse(status=204)


class StoredFileServeView(LoginRequiredMixin, View):
    def get(self, request, name):
        is_changelog = name.startswith("changelogs/")
        if is_changelog:
            from django.db.models import Q
            from hub.models import Changelog

            entries = Changelog.objects.filter(Q(before_image=name) | Q(after_image=name))
            if request.user.role != User.Roles.ADMIN:
                entries = entries.filter(is_published=True, release_date__lte=timezone.localdate())
            if not entries.exists():
                raise Http404
        elif not name.startswith(_PROFILE_PHOTO_PREFIX):
            raise Http404
        stored_file = get_object_or_404(StoredFile, name=name)
        if not stored_file.data:
            raise Http404
        response = HttpResponse(stored_file.data, content_type=stored_file.content_type or "application/octet-stream")
        filename = _safe_download_filename(stored_file.original_name or name.split("/")[-1])
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        response["Cache-Control"] = "private, no-store" if is_changelog else "private, max-age=86400"
        response["X-Content-Type-Options"] = "nosniff"
        return response
