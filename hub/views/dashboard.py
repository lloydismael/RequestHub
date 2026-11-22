"""Role-scoped request dashboard and live data views."""

from accounts.models import User
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from hub import views as legacy_views
from hub.forms import AdminRequestFilterForm, RequestForm
from hub.models import Request, RequestCommunication, StatusLog
from hub.services import request_lifecycle
from hub.services.notifications import queue_new_request_notifications
from hub.services.reports import build_engineer_report_data, build_request_report_data
from hub.services.sla import annotate_acknowledgement_status, format_duration
from hub.views.common import (
    ADMIN_PANEL_ROLES,
    ENGINEER_ACCESS_ROLES,
    MANILA_TZ,
    PM_ESG_ROLE,
    PM_ESS_ROLE,
    REQUEST_CREATOR_ROLES,
    REQUESTOR_ROLES,
)
from hub.views.notifications import flash_assignment_email_feedback


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "hub/dashboard.html"
    # Cap rendered table rows so heavy filters stay light in the browser.
    # Metrics/reports still use the full filtered set before this limit.
    LIST_ROW_LIMIT = 100

    def _limit_dashboard_rows(self, requests: list[Request]) -> tuple[list[Request], dict]:
        from hub.services.pagination import paginate
        page, metadata = paginate(requests, self.request.GET)
        rows = list(page.object_list)
        return rows, {
            **metadata,
            "requests_total_count": page.paginator.count,
            "requests_displayed_count": len(rows),
            "requests_list_limited": page.paginator.num_pages > 1,
            "requests_list_limit": page.paginator.per_page,
        }

    _build_request_report_data = staticmethod(build_request_report_data)
    _build_engineer_report_data = staticmethod(build_engineer_report_data)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        session = getattr(self.request, "session", None)
        context["request_success_reference"] = (
            session.pop("request_success_reference", "") if session is not None else ""
        )
        if user.role in ADMIN_PANEL_ROLES:
            context["role"] = User.Roles.ADMIN
        elif user.role in ENGINEER_ACCESS_ROLES:
            context["role"] = User.Roles.ENGINEER
        else:
            context["role"] = user.role
        context["is_admin_ui"] = user.role in ADMIN_PANEL_ROLES
        context["is_requestor_role"] = user.role in REQUESTOR_ROLES
        context["is_pm_ess"] = user.role == PM_ESS_ROLE
        context["is_pm_esg"] = user.role == PM_ESG_ROLE
        context["is_requestor_ui"] = user.role in REQUESTOR_ROLES or user.role == PM_ESS_ROLE
        context["is_engineer_access_role"] = user.role in ENGINEER_ACCESS_ROLES
        context["can_create_request"] = user.role in REQUEST_CREATOR_ROLES
        context["notifications"] = user.notifications.filter(is_read=False)[:10]

        if user.role == PM_ESS_ROLE:
            form = kwargs.get("form")
            if form is None:
                form = RequestForm(actor_role=user.role, actor_user=user)
            context["form"] = form
            context["account_name_choices"] = form.account_name_suggestions
            metric_filter = self.request.GET.get("metric_filter") or ""
            metric_keys = {"ongoing", "completed"}
            if metric_filter not in metric_keys:
                metric_filter = ""

            request_tab = (self.request.GET.get("request_tab") or "all").strip().lower()
            if request_tab not in {"all", "mine"}:
                request_tab = "all"

            all_requests = list(
                Request.objects.filter(
                    Q(requestor__role=User.Roles.REQUESTOR_ESS) | Q(requestor=user)
                )
                .select_related("account", "engineer", "requestor")
                .order_by("-created_at")
            )

            my_requests = [req for req in all_requests if req.requestor_id == user.id]
            requests = my_requests if request_tab == "mine" else all_requests

            metrics = {
                "ongoing": sum(1 for req in requests if req.status == Request.Status.ONGOING),
                "completed": sum(1 for req in requests if req.status == Request.Status.COMPLETED),
            }

            filtered_requests = requests
            if metric_filter == "ongoing":
                filtered_requests = [req for req in requests if req.status == Request.Status.ONGOING]
            elif metric_filter == "completed":
                filtered_requests = [req for req in requests if req.status == Request.Status.COMPLETED]

            metric_links = {}
            for key in ["ongoing", "completed"]:
                params = self.request.GET.copy()
                if params.get("metric_filter") == key:
                    params.pop("metric_filter", None)
                else:
                    params["metric_filter"] = key
                encoded = params.urlencode()
                metric_links[key] = f"?{encoded}" if encoded else "?"

            request_tab_links = {}
            for key in ("all", "mine"):
                params = self.request.GET.copy()
                if key == "all":
                    params.pop("request_tab", None)
                else:
                    params["request_tab"] = "mine"
                encoded = params.urlencode()
                request_tab_links[key] = f"?{encoded}" if encoded else "?"

            display_requests, list_meta = self._limit_dashboard_rows(filtered_requests)
            context["requests"] = display_requests
            context.update(list_meta)
            context["metrics"] = metrics
            context["metric_links"] = metric_links
            context["active_metric_filter"] = metric_filter
            context["pm_ess_request_tab"] = request_tab
            context["pm_ess_request_tab_links"] = request_tab_links
            context["form_has_errors"] = form.is_bound and bool(form.errors)
            context["request_report_summary"] = {
                "total": len(requests),
                "ongoing": metrics["ongoing"],
                "completed": metrics["completed"],
            }
            context["request_report_data"] = self._build_request_report_data(requests)
        elif user.role in REQUEST_CREATOR_ROLES and user.role not in ADMIN_PANEL_ROLES:
            form = kwargs.get("form")
            if form is None:
                form = RequestForm(actor_role=user.role, actor_user=user)
            context["form"] = form
            context["account_name_choices"] = form.account_name_suggestions
            metric_filter = self.request.GET.get("metric_filter") or ""
            metric_keys = {"ongoing", "completed"}
            if metric_filter not in metric_keys:
                metric_filter = ""

            requests = list(
                Request.objects.filter(requestor=user)
                .select_related("account", "engineer")
                .order_by("-created_at")
            )

            metrics = {
                "ongoing": sum(1 for req in requests if req.status == Request.Status.ONGOING),
                "completed": sum(1 for req in requests if req.status == Request.Status.COMPLETED),
            }

            filtered_requests = requests
            if metric_filter == "ongoing":
                filtered_requests = [req for req in requests if req.status == Request.Status.ONGOING]
            elif metric_filter == "completed":
                filtered_requests = [req for req in requests if req.status == Request.Status.COMPLETED]

            metric_links = {}
            for key in ["ongoing", "completed"]:
                params = self.request.GET.copy()
                if params.get("metric_filter") == key:
                    params.pop("metric_filter", None)
                else:
                    params["metric_filter"] = key
                encoded = params.urlencode()
                metric_links[key] = f"?{encoded}" if encoded else "?"

            request_tab = (self.request.GET.get("request_tab") or "mine").strip().lower()
            if request_tab not in {"all", "mine"}:
                request_tab = "mine"

            request_tab_links = {}
            for key in ("all", "mine"):
                params = self.request.GET.copy()
                if key == "all":
                    params.pop("request_tab", None)
                else:
                    params["request_tab"] = "mine"
                encoded = params.urlencode()
                request_tab_links[key] = f"?{encoded}" if encoded else "?"

            display_requests, list_meta = self._limit_dashboard_rows(filtered_requests)
            context["requests"] = display_requests
            context.update(list_meta)
            context["metrics"] = metrics
            context["metric_links"] = metric_links
            context["active_metric_filter"] = metric_filter
            context["pm_ess_request_tab"] = request_tab
            context["pm_ess_request_tab_links"] = request_tab_links
            context["form_has_errors"] = form.is_bound and bool(form.errors)
            context["request_report_summary"] = {
                "total": len(requests),
                "ongoing": metrics["ongoing"],
                "completed": metrics["completed"],
            }
            context["request_report_data"] = self._build_request_report_data(requests)
        elif user.role in ENGINEER_ACCESS_ROLES:
            metric_filter = (self.request.GET.get("metric_filter") or "").strip()
            tab = self.request.GET.get("tab") or "assigned"
            valid_metrics = {"ongoing", "due_soon", "overdue", "completed"}
            if metric_filter not in valid_metrics:
                metric_filter = ""
            effective_metric_filter = metric_filter or "ongoing"

            assignment = {"backup_engineer" if tab == "backup" else "engineer": user}
            requests = list(
                Request.objects.filter(**assignment)
                .select_related("account", "requestor", "engineer", "backup_engineer")
                .order_by("status", "due_date")
            )

            context["active_tab"] = tab
            if tab == "report":
                context["engineer_report_data"] = self._build_engineer_report_data(
                    requests=[req for req in Request.objects.filter(engineer=user).select_related("requestor")],
                    user=user
                )

            request_ids = [req.pk for req in requests]
            outlook_limited: set[int] = set()
            teams_limited: set[int] = set()
            if request_ids:
                communications = RequestCommunication.objects.filter(
                    request_id__in=request_ids,
                    user__role__in=ENGINEER_ACCESS_ROLES,
                ).only("request_id", "channel", "user_id")
                for comm in communications:
                    if comm.channel == RequestCommunication.Channel.OUTLOOK:
                        outlook_limited.add(comm.request_id)
                    elif comm.channel == RequestCommunication.Channel.TEAMS:
                        if getattr(comm, "user_id", None) == user.id:
                            teams_limited.add(comm.request_id)
            for req in requests:
                setattr(req, "outlook_limit_reached", req.pk in outlook_limited)
            today = timezone.now().astimezone(MANILA_TZ).date()
            metrics = {
                "ongoing": sum(1 for req in requests if req.status == Request.Status.ONGOING),
                "due_soon": sum(
                    1
                    for req in requests
                    if req.status == Request.Status.ONGOING
                and req.engagement_type not in {
                    Request.Engagement.DEPLOYMENT, Request.Engagement.CERTIFICATION
                }
                    and req.due_date
                    and 0 <= (req.due_date - today).days <= 3
                ),
                "overdue": sum(
                    1
                    for req in requests
                    if req.status == Request.Status.ONGOING and req.due_date and req.due_date < today
                ),
                "completed": sum(1 for req in requests if req.status == Request.Status.COMPLETED),
            }

            filtered_requests = requests
            if effective_metric_filter == "ongoing":
                filtered_requests = [req for req in requests if req.status == Request.Status.ONGOING]
            elif effective_metric_filter == "due_soon":
                filtered_requests = [
                    req
                    for req in requests
                    if req.status == Request.Status.ONGOING
                    and req.engagement_type not in {
                        Request.Engagement.DEPLOYMENT, Request.Engagement.CERTIFICATION
                    }
                    and req.due_date
                    and 0 <= (req.due_date - today).days <= 3
                ]
            elif effective_metric_filter == "overdue":
                filtered_requests = [
                    req
                    for req in requests
                    if req.status == Request.Status.ONGOING and req.due_date and req.due_date < today
                ]
            elif effective_metric_filter == "completed":
                filtered_requests = [req for req in requests if req.status == Request.Status.COMPLETED]
                filtered_requests = sorted(filtered_requests, key=lambda req: req.created_at, reverse=True)

            display_requests, list_meta = self._limit_dashboard_rows(filtered_requests)
            self._annotate_acknowledgement_status(display_requests)
            self._annotate_engineer_activity(display_requests)

            metric_links = {}
            for key in ("ongoing", "due_soon", "overdue", "completed"):
                params = self.request.GET.copy()
                is_active_key = metric_filter == key or (key == "ongoing" and metric_filter == "")
                if is_active_key:
                    params.pop("metric_filter", None)
                else:
                    params["metric_filter"] = key
                encoded = params.urlencode()
                metric_links[key] = f"?{encoded}" if encoded else "?"

            context["requests"] = display_requests
            context.update(list_meta)
            context["metrics"] = metrics
            context["metric_links"] = metric_links
            context["active_metric_filter"] = effective_metric_filter
            context["active_tab"] = tab
            context["new_ticket_count"] = user.notifications.filter(
                is_read=False,
                source__icontains="assignment",
            ).count()
        else:
            context.update(self._build_admin_dashboard_context(user))

        if context.get("can_create_request") and "form" not in context:
            form = kwargs.get("form")
            if form is None:
                form = RequestForm(actor_role=user.role, actor_user=user)
            context["form"] = form
            context["account_name_choices"] = form.account_name_suggestions
            context["form_has_errors"] = form.is_bound and bool(form.errors)
        return context

    _format_duration = staticmethod(format_duration)

    def _annotate_acknowledgement_status(self, requests: list[Request]) -> None:
        """Attach acknowledgement SLA information to the displayed requests."""
        annotate_acknowledgement_status(requests)

    def _build_admin_dashboard_context(self, user) -> dict:
        """Shared admin/PM-ESG dashboard table + metrics context (full page + live poll)."""
        metric_keys = [
            "open",
            "overdue",
            "due_soon",
            "completed",
            "new_this_week",
        ]
        metric_filter = self.request.GET.get("metric_filter")
        if not metric_filter or metric_filter not in metric_keys:
            metric_filter = "open"

        pm_esg_tab = ""
        if user.role == PM_ESG_ROLE:
            pm_esg_tab = (self.request.GET.get("pm_esg_tab") or "all").strip().lower()
            if pm_esg_tab not in {"all", "assigned", "my_requests"}:
                pm_esg_tab = "all"

        queryset = Request.objects.select_related("account", "engineer", "backup_engineer", "requestor")
        if pm_esg_tab == "assigned":
            queryset = queryset.filter(engineer=user)
        elif pm_esg_tab == "my_requests":
            queryset = queryset.filter(requestor=user)
        filter_form = AdminRequestFilterForm(self.request.GET or None)
        from hub.services.dashboard_queries import dashboard_data
        from hub.context_processors import new_ticket_count

        data = dashboard_data(queryset, filter_form, self.request.GET, metric_filter,
                              timezone.now().astimezone(MANILA_TZ).date())
        filters_active = filter_form.has_active_filters()
        show_filters = self.request.GET.get("show_filters") == "1"
        sort_key = data["current_sort"]
        direction = data["current_direction"]
        search_query = data["dashboard_search_query"]
        display_requests = data["requests"]
        self._annotate_acknowledgement_status(display_requests)

        columns = ["reference_code", "account", "account_manager", "engineer", "backup_engineer",
                   "engagement", "product_category", "status", "created", "end_date", "days", "due"]
        next_directions = {}
        for column in columns:
            if column == sort_key:
                next_directions[column] = "asc" if direction == "desc" else "desc"
            else:
                next_directions[column] = "asc"

        base_params = self.request.GET.copy()
        base_params.pop("sort", None)
        base_params.pop("direction", None)
        base_params.pop("version", None)
        sort_links = {}
        for column in columns:
            params = base_params.copy()
            params["sort"] = column
            params["direction"] = next_directions[column]
            encoded = params.urlencode()
            sort_links[column] = f"?{encoded}" if encoded else "?"

        toggle_params = self.request.GET.copy()
        toggle_params.pop("version", None)
        if show_filters:
            toggle_params.pop("show_filters", None)
        else:
            toggle_params["show_filters"] = "1"
        toggle_encoded = toggle_params.urlencode()
        filter_toggle_link = f"?{toggle_encoded}" if toggle_encoded else ("?" if show_filters else "?show_filters=1")

        metric_links = {}
        for key in metric_keys:
            params = self.request.GET.copy()
            params.pop("version", None)
            params.pop("page", None)
            if params.get("metric_filter") == key:
                params.pop("metric_filter", None)
            else:
                params["metric_filter"] = key
            encoded_params = params.urlencode()
            metric_links[key] = f"?{encoded_params}" if encoded_params else "?"

        pm_esg_tab_links = {}
        if user.role == PM_ESG_ROLE:
            for key in ("all", "assigned", "my_requests"):
                params = self.request.GET.copy()
                params.pop("version", None)
                if key == "all":
                    params.pop("pm_esg_tab", None)
                else:
                    params["pm_esg_tab"] = key
                encoded = params.urlencode()
                pm_esg_tab_links[key] = f"?{encoded}" if encoded else "?"

        live_version = self._admin_dashboard_live_version(user, self.request.GET)

        return {
            **data,
            "role": User.Roles.ADMIN,
            "is_admin_ui": True,
            "is_pm_esg": user.role == PM_ESG_ROLE,
            "requests": display_requests,
            "new_ticket_count": new_ticket_count(self.request),
            "current_sort": sort_key,
            "current_direction": direction,
            "sort_next": next_directions,
            "sort_links": sort_links,
            "filter_form": filter_form,
            "filters_active": filters_active or (metric_filter and metric_filter != "open"),
            "show_filters": show_filters,
            "filter_toggle_link": filter_toggle_link,
            "metric_links": metric_links,
            "active_metric_filter": "" if search_query else metric_filter,
            "dashboard_search_query": search_query,
            "pm_esg_tab": pm_esg_tab,
            "pm_esg_tab_links": pm_esg_tab_links,
            "dashboard_live_version": live_version,
        }

    @staticmethod
    def _admin_dashboard_live_version(user, params=None) -> str:
        """Fingerprint active + deleted request watermarks so soft-delete/restore poll correctly."""

        def _iso(value) -> str:
            return value.isoformat() if value else ""

        active_stats = Request.objects.aggregate(max_updated=Max("updated_at"), total=Count("id"))
        deleted_stats = Request.all_objects.filter(is_deleted=True).aggregate(
            max_deleted=Max("deleted_at"),
            max_updated=Max("updated_at"),
            total=Count("id"),
        )
        parts = [
            str(active_stats.get("total") or 0),
            _iso(active_stats.get("max_updated")),
            str(deleted_stats.get("total") or 0),
            _iso(deleted_stats.get("max_deleted")),
            _iso(deleted_stats.get("max_updated")),
            str(getattr(user, "pk", "") or ""),
        ]
        from hashlib import sha256
        query_items = sorted((key, value) for key, value in (params.items() if params else [])
                             if key != "version")
        parts.append(sha256(repr(query_items).encode()).hexdigest()[:16])
        return "|".join(parts)

    def _annotate_engineer_activity(self, requests: list[Request]) -> None:
        """Attach status-log and recent-change hints used by the engineer dashboard."""
        if not requests:
            return
        request_ids = [req.pk for req in requests if req.pk]
        log_counts: dict[int, int] = {}
        latest_logs: dict[int, StatusLog] = {}
        if request_ids:
            from django.db.models import Count

            count_rows = (
                StatusLog.objects.filter(request_id__in=request_ids)
                .values("request_id")
                .annotate(total=Count("id"))
            )
            log_counts = {row["request_id"]: row["total"] for row in count_rows}

            latest_qs = (
                StatusLog.objects.filter(request_id__in=request_ids)
                .select_related("author")
                .order_by("request_id", "-created_at")
            )
            seen: set[int] = set()
            for log in latest_qs:
                if log.request_id in seen:
                    continue
                seen.add(log.request_id)
                latest_logs[log.request_id] = log

        for req in requests:
            count = log_counts.get(req.pk, 0)
            latest = latest_logs.get(req.pk)
            req.status_log_count = count
            if latest:
                author_name = (
                    latest.author.get_full_name()
                    or latest.author.username
                    or "Unknown"
                )
                message = (latest.message or "").strip()
                excerpt = message[:140] + ("…" if len(message) > 140 else "")
                created_local = timezone.localtime(latest.created_at, MANILA_TZ)
                req.latest_status_log_tooltip = (
                    f"Latest update by {author_name} on "
                    f"{created_local.strftime('%b %d, %Y %I:%M %p')}"
                    + (f" — {excerpt}" if excerpt else "")
                )
            else:
                req.latest_status_log_tooltip = ""

            # Consider the request "changed" when updated_at is clearly after created_at.
            has_changes = bool(
                req.updated_at
                and req.created_at
                and (req.updated_at - req.created_at).total_seconds() > 60
            )
            req.has_recent_changes = has_changes
            if has_changes:
                updated_local = timezone.localtime(req.updated_at, MANILA_TZ)
                req.recent_change_tooltip = (
                    f"Request last updated on "
                    f"{updated_local.strftime('%b %d, %Y %I:%M %p')}"
                )
            else:
                req.recent_change_tooltip = ""

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Create a request from the dashboard and send assignment feedback."""
        if request.user.role not in REQUEST_CREATOR_ROLES:
            return redirect("hub:dashboard")
        form = RequestForm(request.POST, actor_role=request.user.role, actor_user=request.user)
        if form.is_valid():
            req = form.save(commit=False)
            req.requestor = request.user
            full_name = request.user.get_full_name().strip()
            req.account_manager = full_name or request.user.username
            req._actor_user = request.user
            req._actor_source = "Dashboard · New Request"
            if request.user.role in ADMIN_PANEL_ROLES:
                req._allow_capacity_override = True
            legacy_views.save_configured_instance(form, req)
            lifecycle_result = request_lifecycle.record_created(
                req.pk,
                actor=request.user,
                source="Dashboard · New Request",
            )
            req = lifecycle_result.request
            legacy_views.notify_engineer_assignment_notification(req, actor_user=request.user)
            assignment_email_result = legacy_views.notify_engineer_assignment_email(
                req,
                actor_user=request.user,
                request=request,
            )
            self._notify_admins_new_request(req)
            request.session["request_success_reference"] = req.reference_code
            messages.success(request, f"Request submitted · {req.reference_code}", extra_tags="request-success")
            flash_assignment_email_feedback(
                request,
                assignment_email_result,
                action_label="submitted",
                notify_on_no_assignee=True,
            )
            return redirect("hub:dashboard")
        context = self.get_context_data(form=form)
        return self.render_to_response(context)

    @staticmethod
    def _notify_admins_new_request(request_obj):
        queue_new_request_notifications(request_obj, actor_user=request_obj.requestor)


class DashboardLiveDataView(LoginRequiredMixin, View):
    """AJAX endpoint: admin dashboard table rows + metrics partials for polling."""

    def get(self, request, *args, **kwargs):
        if request.user.role not in ADMIN_PANEL_ROLES:
            return JsonResponse({"ok": False, "error": "Permission denied"}, status=403)

        client_version = (request.GET.get("version") or "").strip()
        live_version = DashboardView._admin_dashboard_live_version(request.user, request.GET)
        if client_version and client_version == live_version:
            return JsonResponse(
                {
                    "ok": True,
                    "changed": False,
                    "version": live_version,
                }
            )

        dashboard = DashboardView()
        dashboard.request = request
        dashboard.args = args
        dashboard.kwargs = kwargs
        context = dashboard._build_admin_dashboard_context(request.user)
        context.setdefault("role", User.Roles.ADMIN)
        context.setdefault("is_admin_ui", True)
        context.setdefault("is_requestor_ui", False)
        context.setdefault("is_pm_ess", False)
        context.setdefault("is_pm_esg", request.user.role == PM_ESG_ROLE)
        context.setdefault("can_create_request", request.user.role in REQUEST_CREATOR_ROLES)

        html_metrics = render_to_string(
            "hub/partials/dashboard_admin_metrics.html",
            context,
            request=request,
        )
        html_rows = render_to_string(
            "hub/partials/dashboard_admin_rows.html",
            context,
            request=request,
        )
        return JsonResponse(
            {
                "ok": True,
                "changed": True,
                "version": context.get("dashboard_live_version") or live_version,
                "request_count": len(context.get("requests") or []),
                "total_count": context.get("requests_total_count", 0),
                "search_query": context.get("dashboard_search_query", ""),
                "html_metrics": html_metrics,
                "html_rows": html_rows,
                "html_pagination": render_to_string("hub/partials/pagination.html",
                    {**context, "pagination_id": "dashboard-live-pagination"}, request=request),
            }
        )
