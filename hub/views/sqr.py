"""SQR tracker, submission, history, and workbook routes."""

import base64
import json
import re
import threading
import uuid
from copy import copy
from functools import cache as memoize
from functools import partial
from typing import Any, ClassVar
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.html import format_html, json_script
from django.views import View
from django.views.generic import DeleteView, TemplateView, UpdateView

from accounts.models import User
from hub import views as legacy_views
from hub.form_editor import get_definition
from hub.form_editor_runtime import configure_form
from hub.forms import (
    SqrDeliveryForm,
    SqrImportForm,
    SqrProposalStatusForm,
    SqrRevenueForm,
    SqrRevenueOrderForm,
    SqrRevenueQuotationForm,
    SqrSubmissionForm,
)
from hub.forms import (
    _refresh_sqr_request_account_map as _refresh_sqr_request_account_map,  # noqa: PLC0414
)
from hub.mixins import AdminOrPmEsgRequiredMixin, AdminRequiredMixin
from hub.models import (
    FormDefinition,
    Request,
    SqrSubmission,
    SqrSubmissionChange,
    SqrSubmissionHistory,
)
from hub.services import sqr_prototype, sqr_workflow
from hub.services.notifications import notify_sqr_submission
from hub.services.outlook_drafts import outlook_is_configured
from hub.services.sqr_email import (
    _build_sqr_approval_mailto_url,
    _build_sqr_approval_mime_message,
    _get_sqr_approval_email_context,
)
from hub.services.sqr_exports import (
    _get_sqr_esg_export_columns as _get_sqr_esg_export_columns,
)
from hub.services.sqr_exports import (
    _get_sqr_export_columns as _get_sqr_export_columns,
)
from hub.services.sqr_exports import (
    _get_sqr_performance_export_columns as _get_sqr_performance_export_columns,
)
from hub.services.sqr_exports import (
    build_sqr_export_workbook,
)
from hub.services.sqr_import import (
    SqrWorkbookImport,
)
from hub.services.sqr_import import (
    _build_choice_import_map as _build_choice_import_map,
)
from hub.services.sqr_import import (
    _normalize_import_text as _normalize_import_text,
)
from hub.services.sqr_performance import build_sqr_performance_snapshot
from hub.services.sqr_reports import build_sqr_report_context
from hub.services.sqr_tracker_settings import (
    get_tracker_settings,
    resolve_sqr_view,
    set_classic_visibility,
)
from hub.services.sqr_workflow import (
    SQR_FILING_QUOTE_FIELDS,
    SQR_REOPEN_MESSAGE,
    _can_review_sqr,
    _sqr_quote_locked,
    _sqr_status_choices,
    get_sqr_history_field_label,
)
from hub.services.sqr_workflow import (
    format_sqr_history_value as format_sqr_history_value,  # noqa: PLC0414
)
from hub.views.common import (
    ADMIN_PANEL_ROLES,
    ENGINEER_ACCESS_ROLES,
    MANILA_TZ,
    PM_ESG_ROLE,
    SQR_ACCESS_ROLES,
    _safe_return_url,
    logger,
)


def _sqr_link_choices(queryset: QuerySet[Request]) -> QuerySet[Request]:
    """Project only the request and account fields used in filing dropdowns."""
    return queryset.select_related("account").only("id", "reference_code", "account__name").order_by("-id")


def _perf_sqr_mismatches(submission):
    """Compare a performance row against its SQR Tracker source columns.

    Returns a list of {field, label, sqr_value, perf_value}. PM is compared by
    user ID (not display name) to avoid false flags on name formatting.
    Kept dormant (no UI) for programmatic checks; covered by unit test.
    """
    perf = getattr(submission, "performance", None)
    if not perf:
        return []
    mismatches = []

    def _fmt_date(d):
        return d.strftime("%Y-%m-%d") if d else "—"

    def _fmt_user(u):
        return (u.get_full_name() or u.username) if u else "—"

    sqr_pm = submission.assigned_pm or submission.pm_esg_reviewer
    if (perf.project_manager_id, getattr(sqr_pm, "pk", None)) != (None, None) and perf.project_manager_id != getattr(sqr_pm, "pk", None):
        mismatches.append({
            "field": "project_manager",
            "label": "Project Manager ≠ SQR Assigned PM",
            "sqr_value": _fmt_user(sqr_pm),
            "perf_value": _fmt_user(perf.project_manager),
        })
    if perf.start_date != submission.delivery_start_date and (perf.start_date or submission.delivery_start_date):
        mismatches.append({
            "field": "start_date",
            "label": "Start Date ≠ SQR Start Date",
            "sqr_value": _fmt_date(submission.delivery_start_date),
            "perf_value": _fmt_date(perf.start_date),
        })
    if perf.finish_date != submission.delivery_actual_finish_date and (perf.finish_date or submission.delivery_actual_finish_date):
        mismatches.append({
            "field": "finish_date",
            "label": "Finish Date ≠ SQR Actual Finish Date",
            "sqr_value": _fmt_date(submission.delivery_actual_finish_date),
            "perf_value": _fmt_date(perf.finish_date),
        })
    sqr_folder = (submission.sqr_folder_link or "").strip().rstrip("/")
    perf_folder = (perf.folder_link or "").strip().rstrip("/")
    if sqr_folder != perf_folder and (sqr_folder or perf_folder):
        mismatches.append({
            "field": "folder_link",
            "label": "Folder link ≠ SQR Doc. Ref. Link",
            "sqr_value": submission.sqr_folder_link or "—",
            "perf_value": perf.folder_link or "—",
        })
    if (perf.percent_complete is not None and submission.delivery_progress is not None
            and abs(perf.percent_complete - submission.delivery_progress) > 10):
        mismatches.append({
            "field": "percent_complete",
            "label": "% Complete differs from SQR Overall Progress % by >10",
            "sqr_value": f"{submission.delivery_progress}%",
            "perf_value": f"{perf.percent_complete}%",
        })
    return mismatches


def _make_sqr_edit_form(user: User, *, definition: FormDefinition | None = None) -> SqrSubmissionForm:
    """Build the edit modal with all engineer-accessible requests, not just filings."""
    queryset = Request.objects.filter(Q(engineer=user) | Q(backup_engineer=user) | Q(sqr_links__engineer=user)).distinct()
    form = SqrSubmissionForm(auto_id="edit_%s", linked_requests=_sqr_link_choices(queryset), _form_editor_skip=True)
    return configure_form(form, definition=definition)


def _sqr_queryset_for_user(user):
    """Shared Tracker/dashboard read scope; preserve joins and ordering."""
    queryset = SqrSubmission.objects.select_related(
        "engineer",
        "pm_esg_reviewer",
        "reviewed_by",
        "assigned_pm",
        "assigned_sse",
        "linked_request",
        "linked_request__account",
    ).order_by("-created_at", "-pk")
    if user.role in ENGINEER_ACCESS_ROLES:
        return queryset.filter(engineer=user)
    return queryset


class SqrTrackerSettingsView(AdminRequiredMixin, View):
    http_method_names: ClassVar[list[str]] = ["get", "post", "head", "options"]

    def get(self, request, *args, **kwargs):
        try:
            settings = get_tracker_settings()
        except DatabaseError:
            logger.exception("Unable to read SQR tracker visibility")
            return JsonResponse({"ok": False, "error": "Could not load tracker visibility."}, status=500)
        response = JsonResponse({"ok": True, "show_classic_sqr_tracker": settings.show_classic_sqr_tracker})
        patch_cache_control(response, no_store=True)
        return response

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"ok": False, "error": "Visibility must be a boolean."}, status=400)
        if not isinstance(payload, dict) or type(payload.get("show_classic_sqr_tracker")) is not bool:
            return JsonResponse({"ok": False, "error": "Visibility must be a boolean."}, status=400)
        try:
            settings = set_classic_visibility(actor=request.user, visible=payload["show_classic_sqr_tracker"])
        except DatabaseError:
            logger.exception("Unable to save SQR tracker visibility")
            return JsonResponse({"ok": False, "error": "Could not save tracker visibility."}, status=500)
        return JsonResponse({"ok": True, "show_classic_sqr_tracker": settings.show_classic_sqr_tracker})


class SqrListView(LoginRequiredMixin, TemplateView):
    template_name = "hub/sqr.html"

    VALID_TABS: ClassVar[set[str]] = {"proposal", "delivery", "revenue-report", "reports"}
    PM_ONLY_TABS: ClassVar[set[str]] = {"delivery", "revenue-report", "reports"}

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if request.user.role not in SQR_ACCESS_ROLES:
            messages.error(request, "You are not allowed to access SQR.")
            return redirect("hub:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return _sqr_queryset_for_user(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Build the context for the SQR tab and layout available to the actor."""
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_create = user.role in ENGINEER_ACCESS_ROLES
        can_review = user.role in ADMIN_PANEL_ROLES
        is_pm = user.role in ADMIN_PANEL_ROLES
        is_admin = user.role == User.Roles.ADMIN

        active_tab = (self.request.GET.get("tab") or "proposal").strip().lower()
        if active_tab not in self.VALID_TABS:
            active_tab = "proposal"
        if active_tab in self.PM_ONLY_TABS and not is_pm:
            active_tab = "proposal"

        can_import = is_admin and (
            user.is_superuser or not SqrSubmission.objects.filter(SqrSubmission.protected_from_deletion()).exists()
        )
        show_classic = get_tracker_settings().show_classic_sqr_tracker
        requested_view = "tracker" if "focus" in self.request.GET else self.request.GET.get("view")
        active_view, view_notice = resolve_sqr_view(
            requested_view, show_classic=show_classic, can_view_performance=is_pm,
        )
        if active_tab == "reports" or kwargs.get("force_reports"):
            active_view, view_notice = "prototype", ""
        context.update({
            "show_classic_sqr_tracker": show_classic,
            "active_sqr_view": active_view,
            "sqr_view_notice": view_notice,
        })

        form = kwargs.get("form")
        definition = get_definition("sqr_submission") if can_create else None
        if can_create and form is None:
            form = SqrSubmissionForm(linked_requests=_sqr_link_choices(Request.objects.filter(engineer=user)), _form_editor_skip=True)
            configure_form(form, definition=definition)
        edit_form = _make_sqr_edit_form(user, definition=definition) if can_create else None

        if active_tab != "reports" and not kwargs.get("force_reports"):
            if show_classic:
                from hub.services.sqr_queries import tracker_page
                queryset = self.get_queryset()
                if not user.is_superuser:
                    queryset = queryset.annotate(deletion_protected=SqrSubmission.protected_from_deletion())
                context.update(tracker_page(queryset, self.request.GET, user, timezone.localdate()))
                for submission in context["proposal_submissions"]:
                    submission.can_review_sqr = _can_review_sqr(user, submission)
                    submission.quote_locked = _sqr_quote_locked(submission)
            else:
                context["default_my_assigned_filter"] = user.role == PM_ESG_ROLE
                context["sqr_reports_summary"] = None
            context.update({
                "can_create_sqr": can_create, "can_review_sqr": can_review, "is_pm": is_pm,
                "is_admin": is_admin, "is_pm_esg": user.role == PM_ESG_ROLE,
                "sqr_current_user_id": user.pk, "sqr_form": form,
                "sqr_import_form": SqrImportForm() if can_import else None,
                "sqr_edit_form": edit_form,
                "sqr_form_has_errors": bool(form and form.is_bound and form.errors),
                "active_sqr_tab": active_tab,
                "sqr_pm_users": list(User.objects.filter(role=User.Roles.PM_ESG)
                    .values("pk", "first_name", "last_name", "username").order_by("first_name", "last_name")) if is_pm else [],
                "sqr_sse_users": list(User.objects.filter(role__in=[User.Roles.ENGINEER, User.Roles.ON_HOLD])
                    .values("pk", "first_name", "last_name", "username").order_by("first_name", "last_name")) if is_pm else [],
                "sqr_rq_options": list(Request.objects.order_by("-id")[:300].values("pk", "reference_code")) if is_pm else [],
            })
            if is_pm:
                context["sqr_json_scripts"] = format_html("{}{}{}",
                    json_script(context["sqr_pm_users"], "sgr-pm-users"),
                    json_script(context["sqr_sse_users"], "sgr-sse-users"),
                    json_script(context["sqr_rq_options"], "sgr-rq-options"))
            return context

        all_submissions = list(self.get_queryset())

        report_context = build_sqr_report_context(all_submissions, is_pm=is_pm)

        my_assigned_sqr_count = sum(
            1 for submission in all_submissions if submission.pm_esg_reviewer_id == user.id
        )
        # Approvers (PM-ESG) default to "My Assigned SQR"; Total still shows the full list.
        default_my_assigned_filter = user.role == PM_ESG_ROLE

        context.update(
            {
                "can_create_sqr": can_create,
                "can_review_sqr": can_review,
                "is_pm": is_pm,
                "is_admin": is_admin,
                "is_pm_esg": user.role == PM_ESG_ROLE,
                "my_assigned_sqr_count": my_assigned_sqr_count,
                "default_my_assigned_filter": default_my_assigned_filter,
                "sqr_current_user_id": user.pk,
                "sqr_form": form,
                "sqr_import_form": SqrImportForm() if can_import else None,
                "sqr_edit_form": edit_form,
                "sqr_form_has_errors": bool(form and form.is_bound and form.errors),
                "active_sqr_tab": active_tab,
                "proposal_submissions": all_submissions,
                **report_context,
                "sqr_pm_users": list(
                    User.objects.filter(role=User.Roles.PM_ESG)
                    .values("pk", "first_name", "last_name", "username")
                    .order_by("first_name", "last_name")
                ) if is_pm else [],
                "sqr_sse_users": list(
                    User.objects.filter(role__in=[User.Roles.ENGINEER, User.Roles.ON_HOLD])
                    .values("pk", "first_name", "last_name", "username")
                    .order_by("first_name", "last_name")
                ) if is_pm else [],
                "sqr_rq_options": list(
                    Request.objects.order_by("-id")[:300]
                    .values("pk", "reference_code")
                ) if is_pm else [],
            }
        )
        if is_pm:
            context["sqr_json_scripts"] = format_html(
                "{}{}{}",
                json_script(context["sqr_pm_users"], "sgr-pm-users"),
                json_script(context["sqr_sse_users"], "sgr-sse-users"),
                json_script(context["sqr_rq_options"], "sgr-rq-options"),
            )
        return context

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Validate a filing and show its outcome to the submitting engineer."""
        if request.user.role not in ENGINEER_ACCESS_ROLES:
            messages.error(request, "Only engineers can submit SQR entries.")
            return redirect("hub:sqr")

        form = SqrSubmissionForm(request.POST, linked_requests=_sqr_link_choices(Request.objects.filter(engineer=request.user)))
        if form.is_valid():
            try:
                with transaction.atomic():
                    submission = sqr_workflow.save_new_submission(
                        form, request.user, save_configured=legacy_views.save_configured_instance,
                    )
                    if submission is None:
                        context = self.get_context_data(form=form)
                        return self.render_to_response(context)
            except IntegrityError:
                logger.exception("Unable to save SQR submission for engineer %s", request.user.pk)
                form.add_error(None, "The SQR could not be saved. Please try again.")
                context = self.get_context_data(form=form)
                return self.render_to_response(context)
            # Reload with reviewer to ensure email field is populated
            submission = (
                SqrSubmission.objects.select_related("engineer", "pm_esg_reviewer")
                .get(pk=submission.pk)
            )
            legacy_views._send_sqr_new_submission_email(submission, http_request=request)
            SqrReportsDataView._notify_sqr_submission(submission)
            messages.success(request, f"SQR submitted successfully ({submission.reference_code}).")
            return redirect("hub:sqr")

        context = self.get_context_data(form=form)
        return self.render_to_response(context)


class SqrPerformanceDataView(LoginRequiredMixin, View):
    """Lazy, authorized snapshot; never builds Tracker/Reports context."""

    http_method_names: ClassVar[list[str]] = ["get"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role not in ADMIN_PANEL_ROLES:
            response = JsonResponse({"ok": False, "error": "Permission denied"}, status=403)
        else:
            response = super().dispatch(request, *args, **kwargs)
        patch_cache_control(response, private=True, no_store=True)
        return response

    def get(self, request, *args, **kwargs):
        fetched_at = timezone.now()
        return JsonResponse(build_sqr_performance_snapshot(
            _sqr_queryset_for_user(request.user),
            today=timezone.localdate(fetched_at),
            fetched_at=fetched_at,
        ))


class SqrPrototypeDataView(LoginRequiredMixin, View):
    """Role-scoped records for the SQR Tracker Prototype; edits go through the inline-update endpoint."""

    http_method_names: ClassVar[list[str]] = ["get"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role not in SQR_ACCESS_ROLES:
            response = JsonResponse({"ok": False, "error": "Permission denied"}, status=403)
        else:
            response = super().dispatch(request, *args, **kwargs)
        patch_cache_control(response, private=True, no_store=True)
        return response

    def get(self, request, *args, **kwargs):
        user = request.user
        is_pm = user.role in ADMIN_PANEL_ROLES
        queryset = _sqr_queryset_for_user(user)
        if user.role == User.Roles.ADMIN and not user.is_superuser:
            queryset = queryset.annotate(deletion_protected=SqrSubmission.protected_from_deletion())
        raw_pk = request.GET.get("pk")
        if raw_pk is not None:
            if not raw_pk.isdigit():
                return JsonResponse({"ok": False, "error": "Invalid record"}, status=400)
            queryset = queryset.filter(pk=int(raw_pk))

        serialize = partial(
            sqr_prototype.serialize_record,
            user=user,
            today=timezone.localdate(),
            is_pm=is_pm,
            is_admin=user.role == User.Roles.ADMIN,
            is_engineer=user.role in ENGINEER_ACCESS_ROLES,
        )
        page = None
        pipeline = None
        if raw_pk is None and "page" in request.GET:
            from hub.services.sqr_queries import prototype_page
            page, pipeline = prototype_page(queryset, request.GET, user, timezone.localdate())
            if request.GET.get("summary") == "1":
                return JsonResponse({"ok": True, "pipeline": pipeline})
            queryset = page.object_list
        records = [serialize(
            submission,
            editable_fields=SqrInlineFieldUpdateView.editable_fields(user, submission),
            can_review=_can_review_sqr(user, submission),
            quote_locked=_sqr_quote_locked(submission),
            status_choices=_sqr_status_choices(submission),
        ) for submission in queryset]
        if raw_pk is not None:
            if not records:
                return JsonResponse({"ok": False, "error": "Not found"}, status=404)
            return JsonResponse({"ok": True, "record": records[0]})

        options = sqr_prototype.build_options(
            pm_users=User.objects.filter(role=User.Roles.PM_ESG)
            .values("pk", "first_name", "last_name", "username").order_by("first_name", "last_name"),
            sse_users=User.objects.filter(role__in=[User.Roles.ENGINEER, User.Roles.ON_HOLD])
            .values("pk", "first_name", "last_name", "username").order_by("first_name", "last_name"),
            rq_options=Request.objects.order_by("-id")[:300].values("pk", "reference_code"),
        ) if is_pm else {"pm_users": [], "sse_users": [], "rq_options": []}
        return JsonResponse({
            "ok": True,
            "viewer": {"pk": user.pk, "is_pm": is_pm, "is_admin": user.role == User.Roles.ADMIN,
                   "is_engineer": user.role in ENGINEER_ACCESS_ROLES},
            "schema": {
                "fields": sqr_prototype.FIELD_SPECS,
                "stages": sqr_prototype.STAGES,
                "tabs": sqr_prototype.TABS,
            },
            "options": options,
            "records": records,
            **({"pipeline": pipeline, "pagination": {"page": page.number,
                "total": page.paginator.count, "has_next": page.has_next(),
                "page_size": page.paginator.per_page}} if page is not None else {}),
        })


class SqrReportsDataView(LoginRequiredMixin, View):
    """AJAX endpoint that returns the latest SQR reports fragment for live refresh."""

    def get(self, request, *args, **kwargs):
        if request.user.role not in ADMIN_PANEL_ROLES:
            return JsonResponse({"ok": False, "error": "Permission denied"}, status=403)

        report_view = SqrListView()
        report_view.request = request
        report_view.args = args
        report_view.kwargs = kwargs
        context = report_view.get_context_data(force_reports=True)
        html = render_to_string("hub/partials/sqr_reports.html", context, request=request)
        return JsonResponse({"ok": True, "html": html})

    @staticmethod
    def _notify_sqr_submission(submission: SqrSubmission) -> None:
        """Notify the reviewer and admins when an engineer files an SQR."""
        notify_sqr_submission(submission)


class SqrEngineerUpdateView(LoginRequiredMixin, UpdateView):
    http_method_names: ClassVar[list[str]] = ["get", "post", "head", "options"]

    def get(self, request, *args, **kwargs):
        if request.GET.get("format") == "form-editor":
            self.object = self.get_object()
            form = self.get_form()
            form.auto_id = "edit_%s"
            form._bound_fields_cache = {}
            html = render_to_string("hub/form_custom_fields.html", {
                "editor_hidden_fields": [form[name] for name in form.fields if form.fields[name].widget.is_hidden],
                "editor_custom_fields": [form[name] for name in getattr(form, "editor_custom_keys", [])],
            }, request=request)
            selectors = {name: str(form[name]) for name in ("customer_contact", "pm_esg_reviewer") if name in form.fields}
            member_map = form.fields["customer_company"].widget.attrs.get("data-member-map", "{}")
            return JsonResponse({
                "html": html, "selectors": selectors, "member_map": member_map,
                "disabled_fields": [name for name in SQR_FILING_QUOTE_FIELDS if form.fields[name].disabled],
            })
        return super().get(request, *args, **kwargs)

    model = SqrSubmission
    form_class = SqrSubmissionForm
    template_name = "hub/sqr_submission_form.html"
    success_url = reverse_lazy("hub:sqr")

    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in ENGINEER_ACCESS_ROLES and request.user.role != User.Roles.ADMIN:
            messages.error(request, "Only engineers or admins can edit SQR submissions.")
            return redirect("hub:sqr")
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            messages.error(request, "That SQR submission no longer exists.")
            return redirect("hub:sqr")

    def get_form(self, form_class: type[SqrSubmissionForm] | None = None) -> SqrSubmissionForm:
        """Prepare the historical or assigned choices and preserve the locked filing fields."""
        obj = self.object
        form_class = form_class or self.get_form_class()
        filing_locks = SQR_FILING_QUOTE_FIELDS if _sqr_quote_locked(obj) else ()
        queryset = Request.objects.all()
        if self.request.user.role != User.Roles.ADMIN:
            queryset = queryset.filter(
                Q(engineer=self.request.user)
                | Q(backup_engineer=self.request.user)
                | Q(pk=obj.linked_request_id)
            )
        form = form_class(**self.get_form_kwargs(), linked_requests=_sqr_link_choices(queryset), _form_editor_skip=True)
        configure_form(form, read_only_fields=filing_locks)
        if self.request.method == "POST":
            self.original = copy(obj)
        if filing_locks:
            for name in filing_locks:
                form.fields[name].disabled = True
                form.fields[name].required = False
                form.fields[name].widget.attrs.pop("required", None)
                form.fields[name].widget.attrs.pop("aria-required", None)
                form.fields[name].widget.attrs["data-editor-required"] = "false"
            form.fields["pm_esg_reviewer"].queryset = User.objects.filter(pk=obj.pm_esg_reviewer_id)
        return form

    def get_queryset(self):
        queryset = SqrSubmission.objects.all()
        if self.request.method == "POST":
            queryset = queryset.select_for_update()
        if self.request.user.role != User.Roles.ADMIN:
            queryset = queryset.filter(engineer=self.request.user)
        return queryset

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["back_url"] = reverse("hub:sqr")
        context["form_title"] = f"Edit Submission {self.object.reference_code}"
        context["submit_label"] = "Save changes"
        context["sqr_quote_locked"] = _sqr_quote_locked(self.object)
        return context

    def form_valid(self, form: SqrSubmissionForm) -> HttpResponseRedirect:
        """Apply a filing edit and return to the tracker."""
        self.object = sqr_workflow.save_engineer_edit(
            form, self.original, self.request.user, save_configured=legacy_views.save_configured_instance,
        )
        messages.success(self.request, f"SQR {self.object.reference_code} updated.")
        return HttpResponseRedirect(self.get_success_url())


class SqrEngineerDeleteView(LoginRequiredMixin, DeleteView):
    model = SqrSubmission
    template_name = "hub/sqr_confirm_delete.html"
    success_url = reverse_lazy("hub:sqr")

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (
            request.user.is_superuser or request.user.role in ENGINEER_ACCESS_ROLES | {User.Roles.ADMIN}
        ):
            messages.error(request, "Only engineers or admins can delete SQR submissions.")
            return redirect("hub:sqr")
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            messages.error(request, "That SQR submission no longer exists.")
            return redirect("hub:sqr")

    def get_queryset(self):
        queryset = SqrSubmission.objects.all()
        if not (self.request.user.is_superuser or self.request.user.role == User.Roles.ADMIN):
            queryset = queryset.filter(engineer=self.request.user)
        if self.request.method in {"POST", "DELETE"}:
            queryset = queryset.select_for_update()
        return queryset

    def get_object(self, queryset=None):
        submission = super().get_object(queryset)
        if not self.request.user.is_superuser and SqrSubmission.objects.filter(
            SqrSubmission.protected_from_deletion(), pk=submission.pk,
        ).exists():
            raise PermissionDenied("Only Django superusers can permanently delete protected SQRs.")
        return submission

    def form_valid(self, form):
        reference_code = self.object.reference_code
        response = super().form_valid(form)
        messages.success(self.request, f"SQR {reference_code} deleted.")
        return response


class SqrInlineFieldUpdateView(LoginRequiredMixin, View):
    """AJAX endpoint for inline cell editing of SQR submissions (Admin / PM-ESG)."""

    _ADMIN_ALLOWED = sqr_workflow._ADMIN_ALLOWED
    _PM_ESG_ALLOWED = sqr_workflow._PM_ESG_ALLOWED
    _DATE_FIELDS = sqr_workflow._DATE_FIELDS
    _INT_FIELDS = sqr_workflow._INT_FIELDS
    _DECIMAL_FIELDS = sqr_workflow._DECIMAL_FIELDS
    _FK_FIELDS = sqr_workflow._FK_FIELDS
    _CLOSED_LOST_LOCKED_FIELDS = sqr_workflow._CLOSED_LOST_LOCKED_FIELDS
    _SERIALIZED_FK_FIELDS = sqr_workflow.SERIALIZED_FK_FIELDS
    _RESTORE_RELATED_FIELDS = sqr_workflow._RESTORE_RELATED_FIELDS
    _UNDO_RETENTION = sqr_workflow.UNDO_RETENTION
    _QUOTE_CONTENT_FIELDS = sqr_workflow.QUOTE_CONTENT_FIELDS
    _PRICE_TRIGGERS = frozenset(["pm_amount", "sse_amount", "managed_support_amount", "discount_rate"])

    _allowed_fields_for_user = staticmethod(sqr_workflow.allowed_fields_for_user)
    _policy_error = staticmethod(sqr_workflow.policy_error)
    editable_fields = staticmethod(sqr_workflow.editable_fields)
    _quote_fingerprint = staticmethod(sqr_workflow.quote_fingerprint)
    _latest_quote_edit_at = staticmethod(sqr_workflow.latest_quote_edit_at)
    _restore_policy_error = staticmethod(sqr_workflow.restore_policy_error)
    _tracked_fields_for_change = staticmethod(sqr_workflow.tracked_fields_for_change)
    _serialize_field_value = staticmethod(sqr_workflow.serialize_field_value)
    _snapshot_values = staticmethod(sqr_workflow.snapshot_values)
    _apply_serialized_field_value = staticmethod(sqr_workflow.apply_serialized_field_value)

    @staticmethod
    def _fmt_date_display(value):
        return value.strftime("%b %d, %Y") if value else ""

    @classmethod
    def _build_inline_response(cls, submission, field, save_fields, request=None, change=None, undone=False, approval_urls=None):
        response_data = {
            "ok": True,
            "field": field,
            "field_value": cls._serialize_field_value(submission, field),
        }
        approval_urls = approval_urls or {}
        if approval_urls.get("draft"):
            response_data["approval_email_draft_url"] = approval_urls["draft"]
        if approval_urls.get("eml"):
            response_data["approval_email_eml_url"] = approval_urls["eml"]
        if approval_urls.get("microsoft"):
            response_data["approval_email_microsoft_url"] = approval_urls["microsoft"]
        quote_locked = _sqr_quote_locked(submission)
        undo_reopens_quote = change is not None and quote_locked and not _sqr_quote_locked(submission, change.old_values)
        if change is not None:
            response_data["change_id"] = change.pk
            if not undo_reopens_quote:
                response_data["undo_url"] = reverse("hub:sqr-inline-undo", args=[submission.pk, change.pk])
                response_data["undo_expires_at"] = change.expires_at.isoformat()
        if undone:
            response_data["undone"] = True
        if field == "status" or field == "hold_reason":
            response_data["hold_reason"] = submission.hold_reason
        if field == "status":
            response_data["quote_locked"] = quote_locked
            response_data["workflow_side_effect_warning"] = True
            external_warning = "Emails, Teams chats, or Outlook drafts already opened cannot be undone."
            if undone:
                message = f"Status was reverted in Request Hub. {external_warning}"
            elif undo_reopens_quote:
                message = f"{SQR_REOPEN_MESSAGE} {external_warning}"
            elif change is not None:
                message = f"This status change can be reverted in Request Hub. {external_warning}"
            else:
                message = external_warning
            response_data["workflow_side_effect_message"] = message

        save_fields = set(save_fields or [])
        if "pm_amount" in save_fields:
            response_data["pm_amount"] = str(submission.pm_amount) if submission.pm_amount is not None else ""
        if "pm_manhrs" in save_fields:
            response_data["pm_manhrs"] = str(int(submission.pm_manhrs)) if submission.pm_manhrs is not None else ""
        if "sse_amount" in save_fields:
            response_data["sse_amount"] = str(submission.sse_amount) if submission.sse_amount is not None else ""
        if any(f in save_fields for f in cls._PRICE_TRIGGERS) or field in cls._PRICE_TRIGGERS:
            da = submission.computed_discount_amount
            tp = submission.computed_total_price
            response_data["computed_discount_amount"] = str(da) if da is not None else ""
            response_data["computed_total_price"] = str(tp) if tp is not None else ""
        if "managed_support_amount" in save_fields:
            msa = submission.managed_support_amount
            response_data["managed_support_amount"] = str(msa) if msa is not None else ""
        if "reviewed_at" in save_fields:
            rat = submission.reviewed_at
            rat_manila = rat.astimezone(MANILA_TZ) if rat else None
            response_data["reviewed_at"] = rat_manila.strftime("%b %d, %Y") if rat_manila else ""
        if "validity_due_date" in save_fields:
            vdd = submission.validity_due_date
            response_data["validity_due_date"] = vdd.strftime("%b %d, %Y") if vdd else ""
        if "assigned_pm" in save_fields:
            pm = submission.assigned_pm
            response_data["assigned_pm_pk"] = pm.pk if pm else ""
            response_data["assigned_pm_name"] = (pm.get_full_name() or pm.username) if pm else ""
        if field == "assigned_sse" or "assigned_sse" in save_fields:
            sse = submission.assigned_sse
            response_data["assigned_sse_name"] = (sse.get_full_name() or sse.username) if sse else ""
        if field == "linked_request" or "linked_request" in save_fields:
            req = submission.linked_request
            response_data["rq_pk"] = req.pk if req else ""
            response_data["rq_code"] = req.reference_code if req else ""

        response_data["maintenance_notice"] = submission.maintenance_notice
        date_triggers = {"delivery_actual_finish_date", "project_details", "customer_company",
                         "managed_support_start_date", "managed_support_end_date", "managed_support_amount"}
        if field in date_triggers or save_fields.intersection(date_triggers):
            response_data["post_svc_warranty_end_date"] = cls._fmt_date_display(submission.computed_post_svc_warranty_end_date)
            response_data["support_start_date"] = cls._fmt_date_display(submission.computed_support_start_date)
            response_data["managed_support_end_date"] = cls._fmt_date_display(submission.computed_managed_support_end_date)
            response_data["maintenance_start_value"] = cls._serialize_field_value(submission, "managed_support_start_date")
            response_data["maintenance_end_value"] = cls._serialize_field_value(submission, "managed_support_end_date")
        return response_data

    def post(self, request: HttpRequest, pk: int) -> JsonResponse:
        """Apply an inline edit and return the updated tracker fields."""
        result = self._save_inline(request, pk)
        if isinstance(result, JsonResponse):
            return result
        submission, field, old_status, tracked_fields, change = result
        approval_urls = {}
        if field == "status" and old_status != submission.status:
            if submission.status == SqrSubmission.Status.FOR_REVISION:
                legacy_views._send_sqr_for_revision_email(
                    submission, request.user.get_full_name() or request.user.username, http_request=request,
                )
            elif submission.status == SqrSubmission.Status.APPROVED:
                approval_urls["draft"] = reverse("hub:sqr-approval-email-formatted-draft", args=[submission.pk])
                approval_urls["eml"] = reverse("hub:sqr-approval-email-eml", args=[submission.pk])
                if outlook_is_configured():
                    approval_urls["microsoft"] = reverse("hub:sqr-approval-email-microsoft", args=[submission.pk])
        return JsonResponse(self._build_inline_response(
            submission, field, tracked_fields, request=request, change=change, approval_urls=approval_urls,
        ))

    def _save_inline(
        self, request: HttpRequest, pk: int,
    ) -> JsonResponse | tuple[SqrSubmission, str, str, list[str], SqrSubmissionChange | None]:
        """Parse an inline change and preserve the legacy lookup and error responses."""
        allowed = self._allowed_fields_for_user(request.user)
        if allowed is None:
            return JsonResponse({"ok": False, "error": "Permission denied"}, status=403)

        try:
            data = json.loads(request.body)
        except (ValueError, KeyError):
            return JsonResponse({"ok": False, "error": "Invalid request"}, status=400)

        if not isinstance(data, dict):
            return JsonResponse({"ok": False, "error": "Invalid request"}, status=400)
        try:
            return sqr_workflow.save_inline_change(
                pk, request.user, data.get("field", ""), data.get("value", ""),
                review_notes=data.get("review_notes"), hold_reason=data.get("hold_reason"),
                lookup=lambda queryset, submission_pk: legacy_views.get_object_or_404(queryset, pk=submission_pk),
            )
        except sqr_workflow.SqrWorkflowError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=exc.status)


class SqrInlineFieldUndoView(SqrInlineFieldUpdateView):
    """AJAX endpoint that reverts a previously saved inline SQR field change."""

    def post(self, request: HttpRequest, pk: int, change_id: int) -> JsonResponse:
        """Undo a recent allowed tracker edit and return the restored fields."""
        allowed = self._allowed_fields_for_user(request.user)
        if allowed is None:
            return JsonResponse({"ok": False, "error": "Permission denied"}, status=403)

        with transaction.atomic():
            submission = legacy_views.get_object_or_404(SqrSubmission.objects.select_for_update(), pk=pk)
            change = legacy_views.get_object_or_404(
                SqrSubmissionChange.objects.select_for_update(), pk=change_id, submission_id=pk,
            )
            if change.field not in allowed:
                return JsonResponse({"ok": False, "error": "Field not allowed"}, status=400)
            if change.is_undone:
                return JsonResponse({"ok": False, "error": "This change was already undone."}, status=400)
            if change.is_expired:
                return JsonResponse({"ok": False, "error": "Undo expired for this change."}, status=400)

            metadata = {}
            if change.field == "status":
                metadata = submission.history_entries.filter(metadata__change_id=change.pk).values_list("metadata", flat=True).first() or {}
            error = self._restore_policy_error(
                request.user, submission, change.old_values, change.new_values, change.changed_at, metadata,
            )
            if error:
                message, status = error
                return JsonResponse({"ok": False, "error": message}, status=status)

            try:
                restore_fields = sqr_workflow.undo_inline_change(submission, change, request.user)
            except sqr_workflow.SqrWorkflowError as exc:
                return JsonResponse({"ok": False, "error": str(exc)}, status=exc.status)

        response_data = self._build_inline_response(
            submission,
            change.field,
            restore_fields,
            request=request,
            undone=True,
        )
        return JsonResponse(response_data)


class SqrHistoryView(LoginRequiredMixin, View):
    """Return PM/Admin-visible permanent history for a single SQR."""

    def get(self, request: HttpRequest, pk: int) -> JsonResponse:
        """Render history and restore availability for an authorized reader."""
        if request.user.role not in ADMIN_PANEL_ROLES:
            return JsonResponse({"ok": False, "error": "Permission denied"}, status=403)

        submission = legacy_views.get_object_or_404(SqrSubmission, pk=pk)
        latest_quote_edit = memoize(partial(SqrInlineFieldUpdateView._latest_quote_edit_at, submission))
        history = submission.history_entries.select_related("actor", "restored_by")
        page = Paginator(history, 50).get_page(request.GET["page"]) if "page" in request.GET else None
        history_rows = list(page.object_list if page is not None else history[:200])
        entries = []
        for entry, changes in zip(history_rows, sqr_workflow.history_display_changes(history_rows), strict=True):
            entries.append({
                "id": entry.pk,
                "action": entry.action,
                "action_label": entry.get_action_display(),
                "field": entry.field,
                "field_label": get_sqr_history_field_label(entry.field),
                "summary": entry.summary,
                "actor": entry.actor.get_full_name() or entry.actor.username,
                "created_at": timezone.localtime(entry.created_at, MANILA_TZ).strftime("%b %d, %Y · %I:%M %p"),
                "source": entry.source,
                "is_restored": entry.is_restored,
                "restored_by": (entry.restored_by.get_full_name() or entry.restored_by.username) if entry.restored_by else "",
                "restored_at": timezone.localtime(entry.restored_at, MANILA_TZ).strftime("%b %d, %Y · %I:%M %p") if entry.restored_at else "",
                "restore_url": reverse("hub:sqr-history-restore", args=[submission.pk, entry.pk]),
                "restorable": (
                    entry.action == SqrSubmissionHistory.Action.UPDATED and not entry.is_restored
                    and bool(entry.old_values) and SqrInlineFieldUpdateView._restore_policy_error(
                        request.user, submission, entry.old_values, entry.new_values, entry.created_at, entry.metadata,
                        latest_quote_edit=latest_quote_edit,
                    ) is None
                ),
                "changes": changes,
            })
        return JsonResponse({
            "ok": True,
            "submission": {
                "id": submission.pk,
                "reference_code": submission.reference_code or str(submission),
            },
            "entries": entries,
            **({"pagination": {
                "page": page.number, "page_size": page.paginator.per_page,
                "total": page.paginator.count, "has_next": page.has_next(),
            }} if page is not None else {}),
        })


class SqrHistoryRestoreView(LoginRequiredMixin, View):
    """Restore a single permanent SQR history entry when no later edit touched it."""

    def post(self, request: HttpRequest, pk: int, history_id: int) -> JsonResponse:
        """Restore an eligible history entry without overwriting a later edit."""
        if request.user.role not in ADMIN_PANEL_ROLES:
            return JsonResponse({"ok": False, "error": "Permission denied"}, status=403)

        with transaction.atomic():
            submission = legacy_views.get_object_or_404(SqrSubmission.objects.select_for_update(), pk=pk)
            history = legacy_views.get_object_or_404(
                SqrSubmissionHistory.objects.select_for_update(), pk=history_id, submission_id=pk,
            )
            if history.action != SqrSubmissionHistory.Action.UPDATED or history.is_restored or not history.old_values:
                return JsonResponse({"ok": False, "error": "This history entry cannot be restored."}, status=400)
            error = SqrInlineFieldUpdateView._restore_policy_error(
                request.user, submission, history.old_values, history.new_values, history.created_at, history.metadata,
            )
            if error:
                message, status = error
                return JsonResponse({"ok": False, "error": message}, status=status)
            try:
                restore_entry = sqr_workflow.restore_history_entry(submission, history, request.user)
            except sqr_workflow.SqrWorkflowError as exc:
                return JsonResponse({"ok": False, "error": str(exc)}, status=exc.status)

        return JsonResponse({
            "ok": True,
            "message": "History entry restored.",
            "restored_history_id": history.pk,
            "restore_entry_id": restore_entry.pk if restore_entry else None,
            "reload_required": True,
        })


class SqrReviewUpdateView(LoginRequiredMixin, View):
    """Legacy SQR review page removed; status review now happens inline on the SQR tracker."""

    def get(self, request, *args, **kwargs):
        messages.info(request, "The SQR review page has been removed. Update SQR status directly from the tracker.")
        return redirect("hub:sqr")

    def post(self, request, *args, **kwargs):
        messages.info(request, "The SQR review page has been removed. Update SQR status directly from the tracker.")
        return redirect("hub:sqr")


class SqrRevenueTrackerUpdateView(LoginRequiredMixin, View):
    @staticmethod
    def _flash_form_errors(request, form):
        for field, errors in form.errors.items():
            if field == "__all__":
                for error in errors:
                    messages.error(request, error)
                continue
            label = form.fields.get(field).label if field in form.fields else field
            for error in errors:
                messages.error(request, f"{label}: {error}")

    @staticmethod
    def _tracker_redirect_url():
        return f"{reverse('hub:sqr')}?{urlencode({'tab': 'revenue-tracker'})}"

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Apply a quotation or order form and redirect back to the tracker."""
        if request.user.role not in ADMIN_PANEL_ROLES:
            messages.error(request, "Only PM-ESG or Admin can update Revenue Tracker details.")
            return redirect(self._tracker_redirect_url())

        submission = legacy_views.get_object_or_404(SqrSubmission.objects.select_for_update(), pk=pk)
        if not _can_review_sqr(request.user, submission):
            raise Http404

        stage_action = (request.POST.get("stage_action") or "").strip().lower()
        if stage_action == "quotation":
            if _sqr_quote_locked(submission):
                messages.error(request, SQR_REOPEN_MESSAGE)
                return redirect(self._tracker_redirect_url())

            form = SqrRevenueQuotationForm(request.POST, instance=submission, prefix=f"quote-{submission.pk}")
            if sqr_workflow.save_quotation_pricing(
                submission, form, request.user, SqrRevenueQuotationForm.Meta.fields,
                save_configured=legacy_views.save_configured_instance,
            ):
                messages.success(request, f"Quotation pricing saved for {submission.reference_code}.")
            else:
                self._flash_form_errors(request, form)
            return redirect(self._tracker_redirect_url())

        if stage_action == "order":
            if submission.revenue_stage_key != "order":
                messages.error(request, "Only approved SQR entries in Order stage can attach a P.O.")
                return redirect(self._tracker_redirect_url())

            form = SqrRevenueOrderForm(request.POST, instance=submission, prefix=f"order-{submission.pk}")
            if form.is_valid():
                try:
                    sqr_workflow.attach_purchase_order(
                        form, SqrRevenueOrderForm.Meta.fields, save_configured=legacy_views.save_configured_instance,
                    )
                except sqr_workflow.SqrWorkflowError as exc:
                    messages.error(request, str(exc))
                    return redirect(self._tracker_redirect_url())
                messages.success(request, f"P.O attached for {submission.reference_code}. Moved to Revenue stage.")
            else:
                self._flash_form_errors(request, form)
            return redirect(self._tracker_redirect_url())

        messages.error(request, "Invalid revenue tracker action.")
        return redirect(self._tracker_redirect_url())


class SqrProposalUpdateView(LoginRequiredMixin, View):
    """Assigned reviewer / Admin: update the deal status on an approved SQR."""

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Apply the proposal form without changing the approved quote."""
        if request.user.role not in ADMIN_PANEL_ROLES:
            messages.error(request, "Only PM-ESG or Admin can update Proposal details.")
            return redirect("hub:sqr")

        submission = legacy_views.get_object_or_404(SqrSubmission.objects.select_for_update(), pk=pk)
        stage_error = sqr_workflow.approved_stage_error(request.user, submission)
        if stage_error:
            messages.error(
                request,
                "Only the assigned reviewer can update this proposal." if stage_error == "reviewer"
                else "Proposal details can only be updated after the SQR is Approved.",
            )
            return redirect("hub:sqr")

        form = SqrProposalStatusForm(request.POST, instance=submission, _form_editor_skip=True)
        sqr_workflow.configure_proposal_form(form)
        if form.is_valid():
            legacy_views.save_configured_instance(form, form.save(commit=False), update_fields=["proposal_status", "updated_at"])
            messages.success(request, f"Proposal details updated for {submission.reference_code}.")
        else:
            for field, errors in form.errors.items():
                label = form.fields[field].label if field in form.fields else field
                for error in errors:
                    messages.error(request, f"{label}: {error}")

        return redirect("hub:sqr")


class SqrToRevenueView(LoginRequiredMixin, View):
    """Assigned reviewer / Admin: unlock Step 4 Revenue Stage by clicking 'To Revenue'."""

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Unlock Revenue for an authorized approved SQR and report the result."""
        if request.user.role not in ADMIN_PANEL_ROLES:
            messages.error(request, "Only PM-ESG or Admin can unlock the Revenue Stage.")
            return redirect("hub:sqr")

        submission = legacy_views.get_object_or_404(SqrSubmission.objects.select_for_update(), pk=pk)
        if sqr_workflow.approved_stage_error(request.user, submission):
            messages.error(request, "Only the assigned reviewer or an admin can unlock revenue for an Approved SQR.")
            return redirect("hub:sqr")

        if sqr_workflow.unlock_revenue_stage(submission):
            messages.success(request, f"Revenue Stage unlocked for {submission.reference_code}.")
        return redirect("hub:sqr")


class SqrDeliveryUpdateView(LoginRequiredMixin, View):
    """PM-ESG / Admin: update Service Delivery tracking on a Closed Won SQR."""

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Apply an allowed delivery edit and return to the tracker."""
        if request.user.role not in ADMIN_PANEL_ROLES:
            messages.error(request, "Only PM-ESG or Admin can update Service Delivery details.")
            return redirect("hub:sqr")

        submission = legacy_views.get_object_or_404(SqrSubmission.objects.select_for_update(), pk=pk)
        if sqr_workflow.delivery_is_locked(submission):
            messages.error(request, "Delivery fields are locked when Proposal Status is Closed-Lost or Closed-Canceled.")
            return redirect("hub:sqr")

        form = SqrDeliveryForm(request.POST, instance=submission)
        if sqr_workflow.save_delivery_update(
            submission, form, request.user, SqrDeliveryForm.Meta.fields,
            save_configured=legacy_views.save_configured_instance,
        ):
            messages.success(request, f"Service delivery updated for {submission.reference_code}.")
        else:
            for field, errors in form.errors.items():
                label = form.fields[field].label if field in form.fields else field
                for error in errors:
                    messages.error(request, f"{label}: {error}")

        return redirect("hub:sqr")


class SqrPerformanceUpdateView(LoginRequiredMixin, View):
    """Retain the legacy route without allowing a competing performance writer."""

    def post(self, request, pk):
        if request.user.role not in ADMIN_PANEL_ROLES:
            messages.error(request, "Only PM-ESG or Admin can update Project Performance details.")
            return redirect("hub:sqr")
        return JsonResponse(
            {"ok": False, "error": "Project performance is maintained in SQR Delivery > Progress."},
            status=410,
        )


class SqrRevenueUpdateView(LoginRequiredMixin, View):
    """Assigned reviewer / Admin: record revenue recognition details on an approved SQR."""

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Apply the Revenue form for the assigned reviewer or admin."""
        if request.user.role not in ADMIN_PANEL_ROLES:
            messages.error(request, "Only PM-ESG or Admin can update Revenue details.")
            return redirect("hub:sqr")

        submission = legacy_views.get_object_or_404(SqrSubmission.objects.select_for_update(), pk=pk)
        stage_error = sqr_workflow.approved_stage_error(request.user, submission)
        if stage_error:
            messages.error(
                request,
                "Only the assigned reviewer can update this revenue record." if stage_error == "reviewer"
                else "Revenue details can only be updated after the SQR is Approved.",
            )
            return redirect("hub:sqr")

        form = SqrRevenueForm(request.POST, instance=submission)
        if form.is_valid():
            legacy_views.save_configured_instance(form, form.save(commit=False), update_fields=[*SqrRevenueForm.Meta.fields, "updated_at"])
            messages.success(request, f"Revenue details updated for {submission.reference_code}.")
        else:
            for field, errors in form.errors.items():
                label = form.fields[field].label if field in form.fields else field
                for error in errors:
                    messages.error(request, f"{label}: {error}")

        return redirect("hub:sqr")


class SqrTeamsRedirectView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Prepare the Teams revision discussion for an eligible submission."""
        submission = legacy_views.get_object_or_404(
            SqrSubmission.objects.select_related("engineer", "pm_esg_reviewer"),
            pk=pk,
        )

        redirect_target = _safe_return_url(request, "hub:sqr")

        if request.user.role not in ADMIN_PANEL_ROLES:
            return JsonResponse({"error": "Only PM-ESG or Admin can create SQR revision Teams groups."}, status=403)

        if request.user.role == PM_ESG_ROLE and submission.pm_esg_reviewer_id != request.user.id:
            return JsonResponse({"error": "Only the assigned PM-ESG approver can create the revision Teams group."}, status=403)

        if submission.status != SqrSubmission.Status.FOR_REVISION:
            return JsonResponse({"error": "Set SQR status to For Revision before creating a Teams group."}, status=400)

        approver_email = submission.pm_esg_reviewer.email if submission.pm_esg_reviewer and submission.pm_esg_reviewer.email else None
        requestor_email = submission.engineer.email if submission.engineer and submission.engineer.email else None
        if not approver_email or not requestor_email:
            return JsonResponse({"error": "Unable to create Teams group. Ensure both approver and requestor emails are configured."}, status=400)

        requestor_name = submission.engineer.get_full_name() or submission.engineer.username
        participants = ",".join(sorted({approver_email, requestor_email}))
        topic = f"SQR {submission.reference_code} {submission.customer_name}"
        raw_notes = (submission.review_notes or "").strip()
        if raw_notes:
            lines = [l.strip() for l in raw_notes.splitlines() if l.strip()]
            numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
        else:
            numbered = "1.\n2.\n3.\n4.\n5."
        message_body = (
            f"Hi @{requestor_name}\n"
            "Submitted SQR is for revision, please refer to the ff. comments below.\n\n"
            f"Comments\n{numbered}\n\n"
            "Thanks"
        )
        teams_url = (
            "https://teams.microsoft.com/l/chat/0/0?users="
            f"{quote(participants)}&topicName={quote(topic)}&message={quote(message_body)}"
        )

        return JsonResponse({"teams_url": teams_url})


class _SqrApprovalDraftMixin:
    """Share approval draft lookup and validation with the Outlook flow."""

    def _get_submission(self, pk: int) -> SqrSubmission:
        """Read the same linked entities for every approval draft."""
        return legacy_views.get_object_or_404(
            SqrSubmission.objects.select_related("engineer", "pm_esg_reviewer", "linked_request"),
            pk=pk,
        )

    def _validate_access(self, request: HttpRequest, submission: SqrSubmission) -> str:
        """Validate the actor and quotation before generating a draft."""
        return sqr_workflow.approval_advisory_error(request.user, submission)


class SqrApprovalOutlookRedirectView(_SqrApprovalDraftMixin, LoginRequiredMixin, View):
    """Launch the default mail app compose window for the approved SQR draft."""

    def get(self, request, pk):
        submission = self._get_submission(pk)
        redirect_target = reverse("hub:sqr")
        validation_error = self._validate_access(request, submission)
        if validation_error:
            messages.error(request, validation_error)
            return redirect(redirect_target)

        context = _get_sqr_approval_email_context(submission)
        return HttpResponseRedirect(_build_sqr_approval_mailto_url(context))

    def post(self, request, pk):
        return self.get(request, pk)


class SqrApprovalEmlDownloadView(_SqrApprovalDraftMixin, LoginRequiredMixin, View):
    """Download a formatted Outlook-compatible EML fallback for approved SQR quotations."""

    def get(self, request, pk):
        submission = self._get_submission(pk)
        validation_error = self._validate_access(request, submission)
        if validation_error:
            messages.error(request, validation_error)
            return redirect("hub:sqr")

        sender_email = (getattr(request.user, "email", "") or "").strip() or settings.DEFAULT_FROM_EMAIL
        context = _get_sqr_approval_email_context(submission)
        mime_payload = _build_sqr_approval_mime_message(context, sender_email, mark_as_unsent=True)
        eml_bytes = base64.b64decode(mime_payload)

        filename_base = re.sub(r"[^A-Za-z0-9._-]+", "_", context["subject"]).strip("_") or submission.reference_code or "sqr-approval-draft"
        response = HttpResponse(eml_bytes, content_type="message/rfc822")
        response["Content-Disposition"] = f'attachment; filename="{filename_base}.eml"'
        return response


class SqrApprovalFormattedDraftView(_SqrApprovalDraftMixin, LoginRequiredMixin, View):
    """Preview an approved advisory for formatted copying into the user's mail app."""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Render the approved advisory and the available draft options."""
        submission = self._get_submission(pk)
        validation_error = self._validate_access(request, submission)
        if validation_error:
            messages.error(request, validation_error)
            return redirect("hub:sqr")

        context = _get_sqr_approval_email_context(submission)
        compose_context = {**context, "plain_text": ""}
        # Outlook needs CRLF line breaks; very long mailto links are not launched reliably.
        body_url = _build_sqr_approval_mailto_url({**context, "plain_text": context["plain_text"].replace("\n", "\r\n")})
        response = legacy_views.render(request, "hub/sqr_approval_preview.html", {
            "submission": submission,
            "email_subject": context["subject"],
            "email_recipients": context["recipients"],
            "email_html": context["html_preview"],
            "email_plain_text": context["plain_text"],
            "compose_url": _build_sqr_approval_mailto_url(compose_context),
            "compose_body_url": body_url if len(body_url) <= legacy_views.SQR_APPROVAL_MAILTO_BODY_URL_LIMIT else "",
            "microsoft_draft_url": reverse("hub:sqr-approval-email-microsoft", args=[submission.pk]) if outlook_is_configured() else "",
            "microsoft_draft_autostart": request.GET.get("outlook") == "1",
        })
        patch_cache_control(response, private=True, no_store=True)
        return response


class SqrExportView(AdminOrPmEsgRequiredMixin, LoginRequiredMixin, View):
    """Export all SQR submissions to an Excel (.xlsx) file."""

    def get(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Serve the selected tracker workbook, defaulting to the SQR report."""
        qs = SqrSubmission.objects.select_related(
            "engineer", "pm_esg_reviewer", "reviewed_by",
            "assigned_pm", "assigned_sse", "linked_request",
            "performance", "performance__project_manager",
        ).order_by("-created_at", "-pk")

        report_type = request.GET.get("report", "sqr")
        if report_type not in {"sqr", "esg", "performance"}:
            report_type = "sqr"

        filename_prefix, buf = build_sqr_export_workbook(qs, report_type)
        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        response = HttpResponse(
            buf.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename_prefix}-{timestamp}.xlsx"'
        return response


class SqrImportView(SqrWorkbookImport, AdminRequiredMixin, LoginRequiredMixin, View):
    """Import SQR data from Excel and overwrite the current SQR table."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_superuser and SqrSubmission.objects.filter(SqrSubmission.protected_from_deletion()).exists():
            return JsonResponse({"ok": False, "errors": ["Only Django superusers can replace protected SQRs."]}, status=403)
        form = SqrImportForm(request.POST, request.FILES)
        if not form.is_valid():
            error_list = form.errors.get("import_file", form.non_field_errors())
            return JsonResponse(
                {
                    "ok": False,
                    "errors": [str(error) for error in error_list],
                },
                status=400,
            )

        should_overwrite = str(request.POST.get("confirm_overwrite", "")).strip().lower()
        if should_overwrite not in {"1", "true", "yes", "on"}:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": ["Please confirm that the current SQR table will be replaced before importing."],
                },
                status=400,
            )

        upload = form.cleaned_data["import_file"]
        try:
            file_bytes = upload.read()
        except Exception:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": ["The selected file could not be read. Please upload a valid Excel file."],
                },
                status=400,
            )

        if not file_bytes:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": ["The selected file is empty."],
                },
                status=400,
            )

        job_id = uuid.uuid4().hex
        self._set_status(
            request.user,
            job_id,
            {
                "state": "queued",
                "percent": 0,
                "processed": 0,
                "total": 0,
                "message": "Preparing import…",
                "errors": [],
                "result": None,
            },
        )

        worker = threading.Thread(
            target=self._run_import_job,
            kwargs={
                "job_id": job_id,
                "user_id": request.user.pk,
                "file_bytes": file_bytes,
            },
            daemon=True,
        )
        worker.start()

        return JsonResponse({
            "ok": True,
            "job_id": job_id,
            "message": "Import started.",
        })

    def get(self, request, *args, **kwargs):
        job_id = (request.GET.get("job_id") or "").strip()
        if not job_id:
            return JsonResponse({"ok": False, "error": "Missing import job id."}, status=400)
        status = self._get_status(request.user, job_id)
        if status is None:
            return JsonResponse({"ok": False, "error": "Import job not found."}, status=404)
        return JsonResponse({"ok": True, "status": status})

    def _run_import_job(self, *, job_id: str, user_id: int, file_bytes: bytes) -> None:
        """Run the existing background job while retaining its connection cleanup hook."""
        self.run_import_job(
            job_id=job_id, user_id=user_id, file_bytes=file_bytes,
            close_connections=legacy_views.close_old_connections,
        )
