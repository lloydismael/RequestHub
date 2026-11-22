"""Request management, collaboration, delivery, and lifecycle routes."""

from collections.abc import Callable, Iterable
from copy import copy
from typing import Any

import requests
from accounts.models import User
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DeleteView, UpdateView
from hub import views as legacy_views
from hub.forms import EngineerActivityLogForm, RequestAdminForm, RequestForm, RequestStatusForm, StatusLogForm
from hub.mixins import (
    AdminOrEngineerRequiredMixin,
    AdminOrPmEsgRequiredMixin,
    can_update_request_status,
    is_request_assignee,
)
from hub.models import (
    EngineerActivityLog,
    Notification,
    Request,
    RequestCommunication,
    SqrSubmission,
    StatusLog,
)
from hub.services import outlook_threads, request_lifecycle
from hub.services.csv_export import REQUEST_EXPORT_COLUMNS, write_request_csv
from hub.services.notifications import notify_request_edit
from hub.services.outlook_drafts import OutlookError, OutlookSignInRequired
from hub.services.request_changes import (
    build_engineer_capacity_map,
    get_request_activity_log_context,
    normalize_request_form_changed_fields,
)
from hub.services.request_email import build_acknowledgement_draft, build_closing_draft
from hub.views.common import (
    _csv_safe_cell,
    _safe_return_url,
    ADMIN_PANEL_ROLES,
    create_change_status_log,
    ENGINEER_ACCESS_ROLES,
    PM_ESG_ROLE,
    PM_ESS_ROLE,
    REQUEST_CREATOR_ROLES,
    SQR_ACCESS_ROLES,
    summarize_request_changes,
)
from hub.views.notifications import flash_assignment_email_feedback, notify_account_manager_request_update
from hub.views.sqr import _sqr_queryset_for_user
from urllib.parse import quote, urlencode


def _request_connect_url(request_obj, return_url):
    return f"{reverse('hub:request-outlook-connect', args=[request_obj.pk])}?{urlencode({'next': return_url})}"


def _microsoft_sign_in_redirect(
    request: HttpRequest, request_obj: Request, return_url: str,
) -> HttpResponseRedirect | None:
    """Require mailbox sign-in before recording a lifecycle change."""
    if not legacy_views.outlook_is_configured():
        return None
    try:
        legacy_views.access_token(request, legacy_views.require_configuration(request))
    except OutlookSignInRequired:
        return HttpResponseRedirect(_request_connect_url(request_obj, return_url))
    except OutlookError:
        pass
    return None


def _microsoft_request_draft(
    request: HttpRequest,
    request_obj: Request,
    create_draft: Callable[[str], tuple[str, bool]],
    return_url: str,
) -> tuple[HttpResponseRedirect | None, str]:
    """Create a threaded draft or return the warning for a mailto fallback."""
    if not legacy_views.outlook_is_configured():
        return None, ""
    try:
        token, _ = legacy_views.access_token(request, legacy_views.require_configuration(request))
        link, anchored = create_draft(token)
    except OutlookSignInRequired:
        return HttpResponseRedirect(_request_connect_url(request_obj, return_url)), ""
    except outlook_threads.AcknowledgementPending as exc:
        messages.error(request, str(exc))
        return HttpResponseRedirect(return_url), ""
    except (OutlookError, requests.RequestException):
        return None, "Microsoft 365 draft could not be created, so a basic email was opened instead. It may not join the request thread."
    RequestCommunication.objects.create(
        request=request_obj,
        user=request.user,
        channel=RequestCommunication.Channel.OUTLOOK,
    )
    if not anchored:
        messages.info(request, "No earlier request email was found in your mailbox, so this email starts a new thread.")
    return HttpResponseRedirect(link), ""


def clear_engineer_outlook_lock_on_reassignment(
    request_obj: Request,
    *,
    previous_engineer_id: int | None,
) -> int:
    """Clear engineer Outlook lock records when the primary engineer changes.
    (Disabled per requirement: the new engineer shouldn't need to acknowledge again if already done)"""
    return 0


class RequestAdminUpdateView(AdminOrPmEsgRequiredMixin, LoginRequiredMixin, UpdateView):
    model = Request
    form_class = RequestAdminForm
    template_name = "hub/request_manager_form.html"
    context_object_name = "service_request"
    success_url = reverse_lazy("hub:dashboard")

    def get_queryset(self) -> QuerySet[Request]:
        """Load the account and participants used by the management form and progress card."""
        return super().get_queryset().select_related("account", "engineer", "backup_engineer", "requestor")

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            messages.error(request, "That request no longer exists.")
            return redirect("hub:dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["allow_capacity_override"] = self.request.method == "POST" and self.request.POST.get("override_capacity") == "1"
        return kwargs

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.POST.get("form_type") == "status_log":
            return self._handle_status_log_post(request)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form: RequestAdminForm) -> HttpResponse:
        """Save the management edit and record its lifecycle and notifications."""
        form.instance._actor_user = self.request.user
        form.instance._actor_source = "Admin · Manage Request"
        original = Request.objects.get(pk=form.instance.pk)
        previous_engineer_id = original.engineer_id
        previous_backup_id = original.backup_engineer_id
        changed_fields = normalize_request_form_changed_fields(form.changed_data)
        response = super().form_valid(form)
        lifecycle_result = request_lifecycle.record_assignment_change(
            self.object.pk,
            previous_engineer_id=previous_engineer_id,
            previous_backup_id=previous_backup_id,
            actor=self.request.user,
            source="Admin · Manage Request",
            allow_capacity_override=self.request.POST.get("override_capacity") == "1",
        )
        self.object = lifecycle_result.request
        if original.status != self.object.status:
            lifecycle_status = request_lifecycle.record_status_change(
                self.object.pk,
                previous_status=original.status,
                actor=self.request.user,
                source="Admin · Manage Request",
            )
            self.object = lifecycle_status.request
        clear_engineer_outlook_lock_on_reassignment(
            self.object,
            previous_engineer_id=previous_engineer_id,
        )
        if original.account_id != self.object.account_id and "account" not in changed_fields:
            changed_fields.append("account")
        if "request_date" in form.changed_data and "start_date" not in changed_fields:
            changed_fields.append("start_date")
        if changed_fields:
            self._notify_request_update(original, self.object, changed_fields)
        assignment_email_result = legacy_views.notify_engineer_assignment_email(
            self.object,
            actor_user=self.request.user,
            request=self.request,
            previous_engineer_id=previous_engineer_id,
            previous_backup_id=previous_backup_id,
        )
        legacy_views.notify_engineer_assignment_notification(
            self.object,
            actor_user=self.request.user,
            previous_engineer_id=previous_engineer_id,
            previous_backup_id=previous_backup_id,
        )
        messages.success(self.request, "Request updated.")
        flash_assignment_email_feedback(self.request, assignment_email_result, action_label="updated")
        return response

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Build the request management context, including logs and engineer capacity."""
        context = super().get_context_data(**kwargs)
        back_url = _safe_return_url(self.request, "hub:dashboard")
        if back_url == self.request.build_absolute_uri():
            back_url = reverse("hub:dashboard")
        context["back_url"] = back_url
        context["hide_sign_in_nav"] = True
        context["delete_url"] = reverse("hub:request-delete", args=[self.object.pk])
        context["status_logs"] = (
            StatusLog.objects.filter(request=self.object)
            .select_related("author")
            .order_by("-created_at")
        )
        context.setdefault("log_form", legacy_views.StatusLogForm(request_obj=self.object, actor_user=self.request.user))
        context["mention_people"] = context["log_form"].mention_people
        context["engineer_capacity_map"] = self._build_engineer_capacity_map()
        context["status_form"] = None
        context["status_allowed"] = False
        context["account_name_choices"] = getattr(context.get("form"), "account_name_suggestions", ()) or ()
        context["is_admin_form"] = True
        context["can_change_account"] = True
        context["lifecycle"] = request_lifecycle.build_lifecycle_context(self.object, self.request.user)
        context.update(get_request_activity_log_context(self.object))
        return context

    @transaction.atomic
    def _handle_status_log_post(self, request: HttpRequest) -> HttpResponse:
        """Post an admin status log or return the bound form with its errors."""
        form = legacy_views.StatusLogForm(request.POST, request_obj=self.object, actor_user=request.user)
        if form.is_valid():
            log = form.save(commit=False)
            log.request = self.object
            log.author = request.user
            legacy_views.save_configured_instance(form, log)
            legacy_views.notify_status_update(log, "Admin · Manage Request · Status Update", http_request=request)
            messages.success(request, "Status update posted.")
            return redirect("hub:request-manage", pk=self.object.pk)
        if form.errors:
            message_field = form.fields.get("message")
            if message_field:
                existing_classes = message_field.widget.attrs.get("class", "")
                if "is-invalid" not in existing_classes.split():
                    message_field.widget.attrs["class"] = (existing_classes + " is-invalid").strip()
        context = self.get_context_data(log_form=form)
        return self.render_to_response(context)

    def _notify_request_update(self, original: Request, updated: Request, changed_fields: Iterable[str]) -> None:
        """Notify the request participants about an admin management edit."""
        notify_request_edit(self.request.user, original, updated, changed_fields, "Admin · Manage Request")

    def _build_engineer_capacity_map(self) -> dict[str, dict[str, str | int]]:
        """Show assignment capacity without counting the current request."""
        return build_engineer_capacity_map(self.object.pk)


class RequestUpdateView(LoginRequiredMixin, UpdateView):
    model = Request
    form_class = RequestForm
    template_name = "hub/request_update.html"
    success_url = reverse_lazy("hub:dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.setdefault("actor_role", self.request.user.role)
        return kwargs

    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in REQUEST_CREATOR_ROLES:
            return redirect("hub:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[Request]:
        """Load edit-form relations without changing ownership or write locks."""
        queryset = super().get_queryset().filter(requestor=self.request.user).select_related(
            "account", "engineer", "backup_engineer", "requestor",
        )
        if self.request.method in {"POST", "PUT"}:
            queryset = queryset.select_for_update(of=("self",))
        return queryset

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["account_name_choices"] = context["form"].account_name_suggestions
        context["is_edit"] = True
        return context

    def form_valid(self, form: RequestForm) -> HttpResponse:
        """Save a requestor edit and notify the affected participants."""
        form.instance._actor_user = self.request.user
        form.instance._actor_source = "Requestor · Edit Request"
        original = Request.objects.get(pk=form.instance.pk)
        previous_engineer_id = original.engineer_id
        previous_backup_id = original.backup_engineer_id
        changed_fields = normalize_request_form_changed_fields(form.changed_data)
        response = super().form_valid(form)
        lifecycle_result = request_lifecycle.record_assignment_change(
            self.object.pk,
            previous_engineer_id=previous_engineer_id,
            previous_backup_id=previous_backup_id,
            actor=self.request.user,
            source="Requestor · Edit Request",
        )
        self.object = lifecycle_result.request
        clear_engineer_outlook_lock_on_reassignment(
            self.object,
            previous_engineer_id=previous_engineer_id,
        )
        if original.account_id != self.object.account_id and "account" not in changed_fields:
            changed_fields.append("account")
        if changed_fields:
            notify_account_manager_request_update(
                self.request.user,
                original,
                self.object,
                changed_fields,
                "Requestor · Edit Request",
            )
        legacy_views.notify_engineer_assignment_notification(
            self.object,
            actor_user=self.request.user,
            previous_engineer_id=previous_engineer_id,
            previous_backup_id=previous_backup_id,
        )
        messages.success(self.request, "Request updated.")
        return response


class RequestCollaborativeManageView(LoginRequiredMixin, View):
    template_name = "hub/request_manager_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())

        allowed = ADMIN_PANEL_ROLES | REQUEST_CREATOR_ROLES | ENGINEER_ACCESS_ROLES | {PM_ESS_ROLE}
        if request.user.role not in allowed:
            messages.error(request, "You are not allowed to manage this request.")
            return redirect("hub:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self) -> Request:
        """Find the request in the actor's role-specific visibility scope."""
        queryset = Request.objects.select_related("account", "engineer", "backup_engineer", "requestor")
        pk = self.kwargs["pk"]
        user = self.request.user

        # Admins and PM-ESG can view any request
        if user.role in ADMIN_PANEL_ROLES:
            return legacy_views.get_object_or_404(queryset, pk=pk)

        if user.role == PM_ESS_ROLE:
            queryset = queryset.filter(pk=pk).filter(Q(requestor__role=User.Roles.REQUESTOR_ESS) | Q(requestor=user))
        elif user.role in REQUEST_CREATOR_ROLES:
            # Requestors can only see their own requests
            queryset = queryset.filter(pk=pk, requestor=user)
        elif user.role in ENGINEER_ACCESS_ROLES:
            # Engineers can see requests they are assigned to directly, or requests
            # linked to SQR submissions they own. The linked-SQR case handles
            # historical rows where the SQR requester and request assignee differ.
            queryset = queryset.filter(pk=pk).filter(
                Q(engineer=user) | Q(backup_engineer=user) | Q(sqr_links__engineer=user)
            ).distinct()
        else:
            raise Http404

        return legacy_views.get_object_or_404(queryset)

    def get(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Show the collaborative request form, read-only for linked non-assignees."""
        try:
            request_obj = self.get_object()
        except Http404:
            messages.error(request, "You are not allowed to manage this request.")
            return redirect("hub:dashboard")
        editing_activity_log = (
            self._get_requested_edit_activity_log(request_obj) if self._can_edit_request(request_obj) else None
        )
        context = self.get_context_data(
            request_obj,
            activity_form=None,
            editing_activity_log=editing_activity_log,
        )
        return legacy_views.render(request, self.template_name, context)

    @transaction.atomic
    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Dispatch a collaborative form after rechecking an engineer's assignment."""
        try:
            request_obj = self.get_object()
        except Http404:
            messages.error(request, "You are not allowed to manage this request.")
            return redirect("hub:dashboard")
        if request.user.role in ENGINEER_ACCESS_ROLES:
            request_obj = legacy_views.get_object_or_404(Request.objects.select_for_update(), pk=request_obj.pk)
            if not self._can_edit_request(request_obj):
                raise PermissionDenied("Only current assignees can update this request.")
        form_type = request.POST.get("form_type", "details")
        if form_type == "activity_log":
            return self._handle_activity_log_post(request, request_obj)
        if form_type == "status_log":
            return self._handle_status_log_post(request, request_obj)
        if form_type == "status":
            return self._handle_status_update(request, request_obj)
        return self._handle_details_update(request, request_obj)

    def _can_edit_request(self, request_obj):
        user = self.request.user
        return user.role not in ENGINEER_ACCESS_ROLES or user.pk in {
            request_obj.engineer_id, request_obj.backup_engineer_id,
        }

    def _actor_prefix(self) -> str:
        return "Requestor" if self.request.user.role in REQUEST_CREATOR_ROLES or self.request.user.role == PM_ESS_ROLE else "Engineer"

    def _source_label(self, suffix: str) -> str:
        return f"{self._actor_prefix()} · {suffix}"

    def _get_editable_activity_log(self, request_obj, log_id):
        user = self.request.user
        queryset = EngineerActivityLog.objects.select_related("engineer", "account", "request").filter(
            request=request_obj
        )
        if user.role in ENGINEER_ACCESS_ROLES:
            queryset = queryset.filter(engineer=user)
        elif user.role not in ADMIN_PANEL_ROLES:
            return None
        try:
            return queryset.get(pk=int(log_id))
        except (EngineerActivityLog.DoesNotExist, ValueError):
            return None

    def _get_requested_edit_activity_log(self, request_obj):
        log_id = (self.request.GET.get("edit_activity") or "").strip()
        if not log_id:
            return None
        activity_log = self._get_editable_activity_log(request_obj, log_id)
        if activity_log is None:
            messages.error(self.request, "We could not find that activity log to edit.")
        return activity_log

    def _details_form(self, request_obj, data=None):
        user = self.request.user
        return RequestForm(data, instance=request_obj, actor_role=user.role, actor_user=user, collaborative_edit=True)

    def get_context_data(
        self,
        request_obj: Request,
        form: RequestForm | None = None,
        status_form: RequestStatusForm | None = None,
        log_form: StatusLogForm | None = None,
        activity_form: EngineerActivityLogForm | None = None,
        editing_activity_log: EngineerActivityLog | None = None,
    ) -> dict[str, Any]:
        """Build the collaborative form context with controls scoped to the actor."""
        read_only = not self._can_edit_request(request_obj)
        if form is None:
            form = self._details_form(request_obj)
            if read_only:
                for field in form.fields.values():
                    field.disabled = True
        status_allowed = can_update_request_status(self.request.user, request_obj.engineer_id)
        if status_allowed and status_form is None:
            status_form = RequestStatusForm(instance=request_obj, actor_user=self.request.user)
        elif not status_allowed:
            status_form = None
        if log_form is None and not read_only:
            log_form = legacy_views.StatusLogForm(request_obj=request_obj, actor_user=self.request.user)
        if editing_activity_log is not None and activity_form is None:
            activity_form = EngineerActivityLogForm(
                engineer=editing_activity_log.engineer,
                bound_request=request_obj,
                instance=editing_activity_log,
            )

        back_url = _safe_return_url(self.request, "hub:dashboard")
        if back_url == self.request.build_absolute_uri():
            back_url = reverse("hub:dashboard")

        linked_sqrs = (
            _sqr_queryset_for_user(self.request.user).filter(linked_request=request_obj)
            if self.request.user.role in SQR_ACCESS_ROLES
            else SqrSubmission.objects.none()
        )

        return {
            "object": request_obj,
            "form": form,
            "status_form": status_form,
            "log_form": log_form,
            "mention_people": log_form.mention_people if log_form else (),
            "request_read_only": read_only,
            "activity_form": activity_form,
            "editing_activity_log": editing_activity_log,
            "status_logs": request_obj.status_logs.select_related("author").order_by("-created_at"),
            "account_name_choices": getattr(form, "account_name_suggestions", ()),
            "back_url": back_url,
            "status_allowed": status_allowed,
            "can_change_account": self.request.user.role in ADMIN_PANEL_ROLES,
            "linked_sqrs": linked_sqrs,
            "lifecycle": request_lifecycle.build_lifecycle_context(request_obj, self.request.user),
            **get_request_activity_log_context(request_obj),
        }

    def _handle_activity_log_post(self, request: HttpRequest, request_obj: Request) -> HttpResponse:
        """Save an allowed activity log edit or render its validation errors."""
        log_id = (request.POST.get("log_id") or "").strip()
        if not log_id and request.user.role not in {User.Roles.ENGINEER, User.Roles.ADMIN}:
            raise PermissionDenied("Only engineers and admins can add activity logs.")
        instance = None
        if log_id:
            instance = self._get_editable_activity_log(request_obj, log_id)
            if instance is None:
                messages.error(request, "Unable to update the selected activity log.")
                return redirect("hub:request-manage-collab", pk=request_obj.pk)

        if instance is not None:
            engineer_for_form = instance.engineer
        elif request.user.role == User.Roles.ADMIN:
            engineer_for_form = request_obj.engineer or request.user
        else:
            engineer_for_form = request.user
        form = EngineerActivityLogForm(
            data=request.POST,
            engineer=engineer_for_form,
            bound_request=request_obj,
            instance=instance,
        )
        if form.is_valid():
            activity_log = form.save(commit=False)
            activity_log.engineer = engineer_for_form
            activity_log.request = request_obj
            activity_log.account = request_obj.account
            if not activity_log.request_date:
                activity_log.request_date = timezone.now().date()
            legacy_views.save_configured_instance(form, activity_log)
            messages.success(request, "Activity log updated successfully." if instance else "Activity log saved successfully.")
            return redirect("hub:request-manage-collab", pk=request_obj.pk)

        context = self.get_context_data(request_obj, activity_form=form, editing_activity_log=instance)
        return legacy_views.render(request, self.template_name, context)

    def _handle_details_update(self, request: HttpRequest, request_obj: Request) -> HttpResponse:
        """Save collaborative request details and record the resulting changes."""
        form = self._details_form(request_obj, request.POST)
        if form.is_valid():
            source_label = self._source_label("Manage Request")
            form.instance._actor_user = request.user
            form.instance._actor_source = source_label
            original = Request.objects.get(pk=request_obj.pk)
            previous_engineer_id = original.engineer_id
            previous_backup_id = original.backup_engineer_id
            changed_fields = normalize_request_form_changed_fields(form.changed_data)
            form.save()
            lifecycle_result = request_lifecycle.record_assignment_change(
                request_obj.pk,
                previous_engineer_id=previous_engineer_id,
                previous_backup_id=previous_backup_id,
                actor=request.user,
                source=source_label,
            )
            request_obj = lifecycle_result.request
            clear_engineer_outlook_lock_on_reassignment(
                request_obj,
                previous_engineer_id=previous_engineer_id,
            )
            if original.account_id != request_obj.account_id and "account" not in changed_fields:
                changed_fields.append("account")
            if changed_fields:
                notify_account_manager_request_update(
                    request.user,
                    original,
                    request_obj,
                    changed_fields,
                    source_label,
                )
            legacy_views.notify_engineer_assignment_notification(
                request_obj,
                actor_user=request.user,
                previous_engineer_id=previous_engineer_id,
                previous_backup_id=previous_backup_id,
            )
            messages.success(request, "Request details updated.")

            def deliver_assignment_email():
                result = legacy_views.notify_engineer_assignment_email(
                    request_obj,
                    actor_user=request.user,
                    request=request,
                    previous_engineer_id=previous_engineer_id,
                    previous_backup_id=previous_backup_id,
                )
                flash_assignment_email_feedback(request, result, action_label="updated")

            transaction.on_commit(deliver_assignment_email)
            return redirect("hub:request-manage-collab", pk=request_obj.pk)
        context = self.get_context_data(request_obj, form=form)
        return legacy_views.render(request, self.template_name, context)

    def _handle_status_update(self, request: HttpRequest, request_obj: Request) -> HttpResponse:
        """Apply an authorized status form or render its validation errors."""
        if not can_update_request_status(request.user, request_obj.engineer_id):
            messages.error(request, "Only the primary assignee or a manager can update the status.")
            return redirect("hub:request-manage-collab", pk=request_obj.pk)

        status_form = RequestStatusForm(request.POST, instance=request_obj, actor_user=request.user)
        target_status = request.POST.get("status")
        is_completion = target_status == Request.Status.COMPLETED
        if status_form.is_valid():
            send_closing_email = (request.POST.get("send_closing_email") or "").strip() in {"1", "true", "True", "on"}
            source_label = self._source_label("Manage Request · Status")
            original = Request.objects.get(pk=request_obj.pk)
            request_obj._actor_user = request.user
            request_obj._actor_source = source_label
            status_form.save()
            lifecycle_result = request_lifecycle.record_status_change(
                request_obj.pk,
                previous_status=original.status,
                actor=request.user,
                source=source_label,
            )
            request_obj = lifecycle_result.request
            changed_fields = []
            if original.status != request_obj.status:
                changed_fields.append("status")
            if original.end_date != request_obj.end_date:
                changed_fields.append("end_date")
            if changed_fields:
                notify_account_manager_request_update(
                    request.user,
                    original,
                    request_obj,
                    changed_fields,
                    source_label,
                )
            if (
                send_closing_email
                and original.status != Request.Status.COMPLETED
                and request_obj.status == Request.Status.COMPLETED
            ):
                return RequestClosingOutlookRedirectView().post(request, request_obj.pk)
            messages.success(request, "Request status updated.")
            return redirect("hub:request-manage-collab", pk=request_obj.pk)
        if status_form.errors:
            for field in status_form:
                if field.errors:
                    existing_classes = field.field.widget.attrs.get("class", "")
                    if "is-invalid" not in existing_classes.split():
                        field.field.widget.attrs["class"] = (existing_classes + " is-invalid").strip()
        context = self.get_context_data(request_obj, status_form=status_form)
        return legacy_views.render(request, self.template_name, context)

    @transaction.atomic
    def _handle_status_log_post(self, request: HttpRequest, request_obj: Request) -> HttpResponse:
        """Post a collaborative status log or render its validation errors."""
        log_form = legacy_views.StatusLogForm(request.POST, request_obj=request_obj, actor_user=request.user)
        if log_form.is_valid():
            log = log_form.save(commit=False)
            log.request = request_obj
            log.author = request.user
            legacy_views.save_configured_instance(log_form, log)
            source_label = self._source_label("Manage Request · Status Update")
            legacy_views.notify_status_update(log, source_label, http_request=request)
            messages.success(request, "Status update posted.")
            return redirect("hub:request-manage-collab", pk=request_obj.pk)
        if log_form.errors:
            message_field = log_form.fields.get("message")
            if message_field:
                existing_classes = message_field.widget.attrs.get("class", "")
                if "is-invalid" not in existing_classes.split():
                    message_field.widget.attrs["class"] = (existing_classes + " is-invalid").strip()
        context = self.get_context_data(request_obj, log_form=log_form)
        return legacy_views.render(request, self.template_name, context)


class StatusLogUpdateView(LoginRequiredMixin, UpdateView):
    model = StatusLog
    form_class = StatusLogForm
    template_name = "hub/status_log_form.html"

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().filter(author=user, request__is_deleted=False).select_related("request", "author")
        if user.role in ADMIN_PANEL_ROLES:
            return queryset
        if user.role == PM_ESS_ROLE:
            return queryset.filter(Q(request__requestor=user) | Q(request__requestor__role=User.Roles.REQUESTOR_ESS))
        if user.role in REQUEST_CREATOR_ROLES:
            return queryset.filter(request__requestor=user)
        if user.role in ENGINEER_ACCESS_ROLES:
            return queryset.filter(Q(request__engineer=user) | Q(request__backup_engineer=user))
        return queryset.none()

    @transaction.atomic
    def form_valid(self, form: StatusLogForm) -> HttpResponse:
        """Update the status log after locking and checking the current assignment."""
        user = self.request.user
        if user.role in ENGINEER_ACCESS_ROLES:
            current = legacy_views.get_object_or_404(Request.objects.select_for_update(), pk=self.object.request_id)
            if user.pk not in {current.engineer_id, current.backup_engineer_id}:
                raise PermissionDenied("Only current assignees can edit this update.")
        previous_message = StatusLog.objects.values_list("message", flat=True).get(pk=self.object.pk)
        response = super().form_valid(form)
        legacy_views.notify_status_update(
            self.object,
            "Manage Request · Status Update",
            previous_message=previous_message,
            http_request=self.request,
        )
        messages.success(self.request, "Update saved.")
        return response

    def get_success_url(self):
        request_obj = self.object.request
        user = self.request.user
        if user.role in ADMIN_PANEL_ROLES:
            return reverse("hub:request-manage", args=[request_obj.pk])
        # For engineers, PMs, and requestors
        return reverse("hub:request-manage-collab", args=[request_obj.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Determine back URL similarly to get_success_url
        request_obj = self.object.request
        user = self.request.user
        if user.role in ADMIN_PANEL_ROLES:
            context["back_url"] = reverse("hub:request-manage", args=[request_obj.pk])
        else:
            context["back_url"] = reverse("hub:request-manage-collab", args=[request_obj.pk])
        return context


class RequestDeleteView(LoginRequiredMixin, DeleteView):
    model = Request
    success_url = reverse_lazy("hub:dashboard")
    template_name = "hub/request_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            messages.error(request, "That request no longer exists.")
            return redirect("hub:dashboard")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.role in ADMIN_PANEL_ROLES:
            return qs
        if user.role in REQUEST_CREATOR_ROLES:
            return qs.filter(requestor=user)
        return qs.none()

    def form_valid(self, form):
        return self._soft_delete()

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        return self._soft_delete()

    def _soft_delete(self):
        success_url = self.get_success_url()
        now = timezone.now()
        updated = self.get_queryset().filter(pk=self.object.pk).update(
            is_deleted=True,
            deleted_at=now,
            updated_at=now,
        )
        if updated:
            messages.success(
                self.request,
                f"Request {self.object.reference_code} deleted. An administrator can recover it in Profile → Personalization → Deleted requests.",
            )
        return redirect(success_url)


class RequestRestoreView(LoginRequiredMixin, View):
    """POST → restore a soft-deleted request (admin only)."""

    def post(self, request, pk):
        if request.user.role != User.Roles.ADMIN:
            messages.error(request, "Access denied.")
            return redirect("accounts:update")
        now = timezone.now()
        updated = Request.all_objects.filter(pk=pk, is_deleted=True).update(
            is_deleted=False,
            deleted_at=None,
            updated_at=now,
        )
        if updated:
            ref = Request.all_objects.filter(pk=pk).values_list("reference_code", flat=True).first()
            messages.success(request, f"Request {ref} has been restored.", extra_tags="recovery-feedback")
        else:
            messages.error(request, "Request not found or already active.", extra_tags="recovery-feedback")
        return redirect(str(reverse_lazy("accounts:update")) + "#backup")


class RequestOutlookRedirectView(AdminOrEngineerRequiredMixin, LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int, allow_existing_draft: bool = False) -> HttpResponse:
        """Open an acknowledgement draft for an authorized request participant."""
        request_obj = legacy_views.get_object_or_404(
            Request.objects.select_related("engineer", "backup_engineer", "requestor", "account"),
            pk=pk,
        )

        redirect_target = _safe_return_url(request, "hub:dashboard")

        if request.user.role in ENGINEER_ACCESS_ROLES:
            if not is_request_assignee(request.user, request_obj):
                messages.error(request, "You are not allowed to draft emails for this request.")
                return redirect(redirect_target)
            already_launched = RequestCommunication.objects.filter(
                request=request_obj,
                user__role__in=ENGINEER_ACCESS_ROLES,
                channel=RequestCommunication.Channel.OUTLOOK,
            ).exists()
            if already_launched and not allow_existing_draft:
                messages.warning(request, "You already launched the Outlook draft for this request.")
                return redirect(redirect_target)

        engineer_email = (
            request_obj.engineer.email if request_obj.engineer and request_obj.engineer.email else None
        )
        manager_email = (
            request_obj.requestor.email if request_obj.requestor and request_obj.requestor.email else None
        )
        if not engineer_email or not manager_email:
            messages.error(
                request,
                "Unable to draft an email. Ensure the engineer and requestor both have emails configured.",
            )
            return redirect(redirect_target)

        draft = build_acknowledgement_draft(
            request_obj, request.user, engineer_email=engineer_email, manager_email=manager_email,
        )
        response, warning = _microsoft_request_draft(
            request,
            request_obj,
            lambda token: outlook_threads.create_acknowledgement_draft(
                token, request_obj, to=draft.to, cc=draft.cc, body=draft.body,
            ),
            redirect_target,
        )
        if response is not None:
            return response
        if warning:
            messages.warning(request, warning)

        RequestCommunication.objects.create(
            request=request_obj,
            user=request.user,
            channel=RequestCommunication.Channel.OUTLOOK,
        )

        outlook_url = draft.mailto_url()
        messages.info(request, "Drafting email in your default mail client…")
        return legacy_views.render(
            request,
            "hub/outlook_redirect.html",
            {
                "mailto_url": outlook_url,
                "launch_url": outlook_url,
                "formatted_draft_url": "",
                "return_url": redirect_target,
            },
        )


class RequestClosingOutlookRedirectView(AdminOrEngineerRequiredMixin, LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Open a closing draft after the request is completed."""
        request_obj = legacy_views.get_object_or_404(
            Request.objects.select_related("engineer", "backup_engineer", "requestor", "account"),
            pk=pk,
        )

        redirect_target = _safe_return_url(request, "hub:dashboard")

        if request_obj.status != Request.Status.COMPLETED:
            messages.error(request, "Mark the request as completed before sending a closing email.")
            return redirect(redirect_target)

        if request.user.role in ENGINEER_ACCESS_ROLES:
            if not is_request_assignee(request.user, request_obj):
                messages.error(request, "You are not allowed to close out this request.")
                return redirect(redirect_target)

        engineer_email = None
        if request_obj.engineer and request_obj.engineer.email:
            engineer_email = request_obj.engineer.email
        elif request.user.email:
            engineer_email = request.user.email

        manager_email = request_obj.requestor.email if request_obj.requestor and request_obj.requestor.email else None
        if not manager_email:
            messages.error(request, "Unable to draft a closing email. Ensure the requestor has an email configured.")
            return redirect(redirect_target)

        if not engineer_email:
            messages.error(request, "Unable to draft a closing email. Ensure the engineer has an email configured.")
            return redirect(redirect_target)

        draft = build_closing_draft(request_obj, engineer_email=engineer_email, manager_email=manager_email)
        response, warning = _microsoft_request_draft(
            request,
            request_obj,
            lambda token: outlook_threads.create_closing_draft(
                token, request_obj, to=draft.to, cc=draft.cc, body=draft.body,
            ),
            redirect_target,
        )
        if response is not None:
            return response
        if warning:
            messages.warning(request, warning)

        RequestCommunication.objects.create(
            request=request_obj,
            user=request.user,
            channel=RequestCommunication.Channel.OUTLOOK,
        )

        outlook_url = draft.mailto_url()
        messages.info(request, "Drafting closing email in your default mail client…")
        return legacy_views.render(
            request,
            "hub/outlook_redirect.html",
            {
                "mailto_url": outlook_url,
                "launch_url": outlook_url,
                "formatted_draft_url": "",
                "return_url": redirect_target,
            },
        )


class RequestTeamsRedirectView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Launch Teams with the participants allowed for the actor's role."""
        request_obj = legacy_views.get_object_or_404(
            Request.objects.select_related("engineer", "backup_engineer", "requestor", "account"),
            pk=pk,
        )

        redirect_target = _safe_return_url(request, "hub:dashboard")

        role = request.user.role
        if role not in ({User.Roles.ADMIN, PM_ESG_ROLE, PM_ESS_ROLE} | ENGINEER_ACCESS_ROLES):
            messages.error(request, "You are not allowed to start a Teams chat for this request.")
            return redirect(redirect_target)

        if role in ENGINEER_ACCESS_ROLES:
            if not is_request_assignee(request.user, request_obj):
                messages.error(request, "You are not allowed to start a Teams chat for this request.")
                return redirect(redirect_target)
            already_launched = RequestCommunication.objects.filter(
                request=request_obj,
                user=request.user,
                channel=RequestCommunication.Channel.TEAMS,
            ).exists()
            if already_launched:
                messages.warning(request, "You already launched the Teams chat for this request.")
                return redirect(redirect_target)

        if role == PM_ESS_ROLE:
            if not request_obj.requestor or request_obj.requestor.role != User.Roles.REQUESTOR_ESS:
                messages.error(request, "Teams chat is only available for Requestor-ESS requests.")
                return redirect(redirect_target)
            if not request_obj.engineer or not request_obj.engineer.email:
                messages.error(request, "Assign an engineer with an email before starting a Teams chat.")
                return redirect(redirect_target)
            if not request_obj.requestor.email:
                messages.error(request, "Requestor email is missing; unable to start a Teams chat.")
                return redirect(redirect_target)
            already_launched = RequestCommunication.objects.filter(
                request=request_obj,
                user=request.user,
                channel=RequestCommunication.Channel.TEAMS,
            ).exists()
            if already_launched:
                messages.warning(request, "You already launched the Teams chat for this request.")
                return redirect(redirect_target)

            topic = request_obj._build_teams_chat_topic(reference_code=request_obj.reference_code)
            participants = ",".join(sorted({email for email in [request_obj.engineer.email, request_obj.requestor.email] if email}))
            if not participants:
                messages.error(request, "Unable to start a Teams chat. Missing participant emails.")
                return redirect(redirect_target)
            teams_url = (
                "https://teams.microsoft.com/l/chat/0/0?users="
                f"{quote(participants)}&topicName={quote(topic)}"
            )
        else:
            teams_url = request_obj.teams_chat_url

        if not teams_url:
            messages.error(
                request,
                "Unable to start a Teams chat. Ensure the engineer and requestor have email addresses configured.",
            )
            return redirect(redirect_target)

        RequestCommunication.objects.create(
            request=request_obj,
            user=request.user,
            channel=RequestCommunication.Channel.TEAMS,
        )

        messages.info(request, "Launching Microsoft Teams…")
        return legacy_views.render(
            request,
            "hub/teams_redirect.html",
            {"teams_url": teams_url},
        )


class RequestExportCSVView(AdminOrPmEsgRequiredMixin, LoginRequiredMixin, View):
    """Allow administrators to export all requests to a CSV download."""

    columns = REQUEST_EXPORT_COLUMNS

    def get(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Serve the all-requests CSV as a timestamped attachment."""
        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="requests-{timestamp}.csv"'
        write_request_csv(response, self.columns, safe_cell=_csv_safe_cell)
        return response


class RequestNudgeView(AdminOrPmEsgRequiredMixin, LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Send reminders to the primary and any backup engineer on the request."""
        request_obj = legacy_views.get_object_or_404(Request.objects.select_related("engineer", "backup_engineer"), pk=pk)

        if not request_obj.engineer:
            messages.error(request, "Cannot nudge: No engineer assigned.")
            return redirect("hub:request-manage", pk=pk)

        Notification.objects.create(
            recipient=request_obj.engineer,
            message=f"Reminder: Please update the status for {request_obj.reference_code}.",
            related_request=request_obj,
            actor="Admin",
            source="Request Nudge",
        )

        if request_obj.backup_engineer:
            Notification.objects.create(
                recipient=request_obj.backup_engineer,
                message=f"Reminder: Backup request {request_obj.reference_code} needs attention.",
                related_request=request_obj,
                actor="Admin",
                source="Request Nudge",
            )

        messages.success(
            request,
            f"Nudge sent to {request_obj.engineer.get_full_name() or request_obj.engineer.username}.",
        )
        return redirect("hub:request-manage", pk=pk)


class RequestStatusUpdateView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Authorize and record a primary engineer's status change under a row lock."""
        with transaction.atomic():
            request_obj = legacy_views.get_object_or_404(
                Request.all_objects.select_for_update(of=("self",)).select_related("engineer", "requestor"),
                pk=pk,
            )

            if (
                request_obj.is_deleted
                or request.user.role not in ENGINEER_ACCESS_ROLES
                or request_obj.engineer_id != request.user.id
            ):
                messages.error(request, "You are not allowed to update this request's status.")
                return redirect("hub:request-manage-collab", pk=pk)

            original = copy(request_obj)
            form = RequestStatusForm(request.POST, instance=request_obj, actor_user=request.user)
            if form.is_valid():
                request_obj._actor_user = request.user
                request_obj._actor_source = "Engineer · Status Update"
                form.save(commit=False)
                request_obj.save(update_fields=["status", "end_date", "updated_at"])
                request_lifecycle.record_status_change(
                    request_obj.pk,
                    previous_status=original.status,
                    actor=request.user,
                    source="Engineer · Status Update",
                )
                changed_fields = []
                if original.status != request_obj.status:
                    changed_fields.append("status")
                if original.end_date != request_obj.end_date:
                    changed_fields.append("end_date")
                if changed_fields:
                    summary_text = summarize_request_changes(original, request_obj, changed_fields)
                    create_change_status_log(request_obj, request.user, "Engineer · Status Update", summary_text)
                messages.success(request, "Request status updated.")
            else:
                messages.error(request, "Unable to update status. Please try again.")
        return redirect("hub:request-manage-collab", pk=pk)
