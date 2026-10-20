from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView, UpdateView
import json
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone
from django.apps import apps
from django.core import serializers as django_serializers
from django.db import transaction

from accounts.middleware import FORCE_PASSWORD_CHANGE_SESSION_KEY
from accounts.models import StoredFile, User
from hub.mixins import AdminRequiredMixin

from .forms import (
    ProfileForm,
    ThemeForm,
    ROLE_CHOICES,
    ROLE_LABELS,
    RoleAuthenticationForm,
)

_PROFILE_PHOTO_PREFIX = "profile_photos/"
LOGIN_THROTTLE_LIMIT = 5
LOGIN_THROTTLE_TIMEOUT = 15 * 60
LOGIN_THROTTLE_MESSAGE = "Too many failed login attempts. Try again later."


def _client_ip(request) -> str:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR") or "unknown"
    return request.META.get("REMOTE_ADDR") or "unknown"


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
            from hub.models import Request as HubRequest
            ctx["deleted_requests"] = (
                HubRequest.all_objects.filter(is_deleted=True)
                .select_related("requestor", "engineer", "account")
                .order_by("-deleted_at")
            )
        return ctx

    def post(self, request, *args, **kwargs):
        if request.POST.get("save_theme") or request.POST.get("save_banner"):
            self.object = self.get_object()
            theme_form = ThemeForm(request.POST, instance=self.object)
            if theme_form.is_valid():
                self.object.banner_gradient = theme_form.cleaned_data["banner_gradient"]
                self.object.save(update_fields=["banner_gradient"])
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


# ── Backup / Restore ─────────────────────────────────────────────────────

# Models included in backup (order = save order on restore; delete reversed)
_BACKUP_MODEL_LABELS = [
    "hub.account",
    "hub.request",
    "hub.sqrsubmission",
    "hub.requestcommunication",
    "hub.engineeractivitylog",
    "hub.statuslog",
]


class BackupDataView(AdminRequiredMixin, View):
    """GET → download a JSON backup of all hub data."""

    required_role = User.Roles.ADMIN

    def get(self, request):
        all_objects = []
        for label in _BACKUP_MODEL_LABELS:
            model = apps.get_model(label)
            all_objects.extend(model.objects.all())

        data_json = django_serializers.serialize("json", all_objects, indent=2)
        payload = {
            "backup_version": "1",
            "created_at": datetime.now(dt_timezone.utc).isoformat(),
            "app": "request-hub",
            "data": json.loads(data_json),
        }

        filename = f"request-hub-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        response = HttpResponse(
            json.dumps(payload, indent=2),
            content_type="application/json",
        )
        response["Content-Disposition"] = f'attachment; filename="{_safe_download_filename(filename)}"'
        return response


class RestoreDataView(AdminRequiredMixin, View):
    """POST → restore hub data from an uploaded JSON backup file."""

    required_role = User.Roles.ADMIN
    _BACK_URL = reverse_lazy("accounts:update")

    def _back(self, request, error=None, success=None):
        if error:
            messages.error(request, error)
        if success:
            messages.success(request, success)
        return redirect(str(self._BACK_URL) + "#backup")

    def post(self, request):
        if request.POST.get("confirm_restore") != "1":
            return self._back(request, error="You must tick the confirmation checkbox before restoring.")

        backup_file = request.FILES.get("backup_file")
        if not backup_file:
            return self._back(request, error="No backup file was uploaded.")

        try:
            raw = backup_file.read().decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._back(request, error=f"Could not read backup file: {exc}")

        if payload.get("app") != "request-hub" or "data" not in payload:
            return self._back(request, error="This does not appear to be a valid Request Hub backup file.")

        data_json = json.dumps(payload["data"])

        try:
            with transaction.atomic():
                # Delete children first to avoid FK constraint violations
                for label in reversed(_BACKUP_MODEL_LABELS):
                    apps.get_model(label).objects.all().delete()

                # Deserialize and group by model so we can save in dependency order
                grouped = defaultdict(list)
                for obj in django_serializers.deserialize("json", data_json):
                    lbl = f"{obj.object._meta.app_label}.{obj.object._meta.model_name}"
                    grouped[lbl].append(obj)

                for label in _BACKUP_MODEL_LABELS:
                    for obj in grouped.get(label, []):
                        obj.save()

        except Exception as exc:  # noqa: BLE001
            return self._back(request, error=f"Restore failed: {exc}")

        created_at = payload.get("created_at", "unknown date")
        return self._back(request, success=f"Data restored successfully from backup dated {created_at}.")


class LandingView(TemplateView):

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("hub:dashboard")
        return redirect("login")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        descriptions = {
            User.Roles.ADMIN: "Oversee requests, assign engineers, and close engagements.",
            User.Roles.PM_ESG: "Manage requests like administrators, coordinate engineers, and create tickets without user-management access.",
            User.Roles.ENGINEER: "Review assigned tickets, respond to updates, and act on SLAs.",
            User.Roles.ON_HOLD: "Retain engineer access to previously assigned work while staying unavailable for new assignments.",
            User.Roles.REQUESTOR: "Create new customer requests and monitor progress.",
            User.Roles.REQUESTOR_ESS: "Submit customer requests without Support engagements and track progress.",
            User.Roles.PM_ESS: "Oversee Requestor-ESS requests, create tickets, and track status across accounts.",
        }
        context["role_cards"] = [
            {
                "role": role,
                "label": ROLE_LABELS[role],
                "description": descriptions.get(role, ""),
            }
            for role, _ in ROLE_CHOICES
        ]
        return context


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


class StoredFileServeView(LoginRequiredMixin, View):
    def get(self, request, name):
        if not name.startswith(_PROFILE_PHOTO_PREFIX):
            raise Http404
        stored_file = get_object_or_404(StoredFile, name=name)
        if not stored_file.data:
            raise Http404
        response = HttpResponse(stored_file.data, content_type=stored_file.content_type or "application/octet-stream")
        filename = _safe_download_filename(stored_file.original_name or name.split("/")[-1])
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        response["Cache-Control"] = "private, max-age=86400"
        return response
