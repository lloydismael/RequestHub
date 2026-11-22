"""Operational reports, activity logs, and CSV exports."""

<<<<<<< HEAD
from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlencode

=======
from accounts.models import User
from datetime import datetime
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
<<<<<<< HEAD

from accounts.models import User
=======
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
from hub import views as legacy_views
from hub.forms import EngineerActivityLogForm
from hub.mixins import AdminOrPmEsgRequiredMixin, EngineerRequiredMixin
from hub.models import EngineerActivityLog, Request
from hub.services.csv_export import (
    ReportExportType,
    format_export_date,
    format_export_user,
    format_report_export_datetime,
    report_csv_filename,
    write_report_csv,
)
from hub.services.reports import (
<<<<<<< HEAD
    _month_key as _month_key,  # noqa: PLC0414 - Legacy re-export.
)
from hub.services.reports import (
    _month_label as _month_label,  # noqa: PLC0414 - Legacy re-export.
)
from hub.services.reports import (
    _month_start as _month_start,  # noqa: PLC0414 - Legacy re-export.
)
from hub.services.reports import (
    _next_month_start as _next_month_start,  # noqa: PLC0414 - Legacy re-export.
)
from hub.services.reports import (
=======
    _month_key as _month_key,
    _month_label as _month_label,
    _month_start as _month_start,
    _next_month_start as _next_month_start,
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    build_activity_log_context,
    build_engineer_activity_log_context,
    build_operational_context,
    build_request_health_context,
    compact_ranked_chart,
    full_ranked_chart,
    normalize_month_value,
    period_query,
    resolve_month_filters,
)
from hub.views.common import _csv_safe_cell
<<<<<<< HEAD


class EngineerActivityLogView(EngineerRequiredMixin, LoginRequiredMixin, TemplateView):
    required_roles: ClassVar[set[str]] = {User.Roles.ENGINEER, User.Roles.ADMIN}
=======
from typing import Any, Optional
from urllib.parse import urlencode


class EngineerActivityLogView(EngineerRequiredMixin, LoginRequiredMixin, TemplateView):
    required_roles = {User.Roles.ENGINEER, User.Roles.ADMIN}
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    template_name = "hub/activity_logs.html"
    form_class = EngineerActivityLogForm

    def _parse_month_filter(self):
        month_value = (self.request.GET.get("month") or "").strip()
        if not month_value:
            return "", None, None
        try:
            parsed = datetime.strptime(month_value, "%Y-%m")
        except ValueError:
            return "", None, None
        return month_value, parsed.year, parsed.month

    def get_queryset(self):
        queryset = (
            EngineerActivityLog.objects.filter(engineer=self.request.user)
            .select_related("account", "request")
            .order_by("-request_date", "-created_at")
        )
        _, year, month = self._parse_month_filter()
        if year and month:
            queryset = queryset.filter(request_date__year=year, request_date__month=month)
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Combine the editing form with the requested log page and engineer report."""
        context = super().get_context_data(**kwargs)
        form = kwargs.get("form")
        editing_log = kwargs.get("editing_log")
        if form is None:
            form = self.form_class(engineer=self.request.user)

        logs_qs = self.get_queryset()
        try:
            page_num = max(1, int(self.request.GET.get("page") or 1))
        except (TypeError, ValueError):
            page_num = 1
        selected_month, _, _ = self._parse_month_filter()
        report = build_engineer_activity_log_context(
            logs_qs, engineer=self.request.user, selected_month=selected_month, page_num=page_num,
        )
        context.update({"form": form, **report, "editing_log": editing_log})
        return context

    def get(self, request, *args, **kwargs):
        edit_id = request.GET.get("edit")
        editing_log = None
        form = None
        prefill_request_id = request.GET.get("request")
        initial = {}

        if prefill_request_id:
            try:
                selected_request_id = int(prefill_request_id)
            except (TypeError, ValueError):
                selected_request_id = None
            if selected_request_id is not None:
                allowed_requests = Request.objects.all()
                if request.user.role != User.Roles.ADMIN:
                    allowed_requests = allowed_requests.filter(
                        Q(engineer=request.user) | Q(backup_engineer=request.user)
                    )
                if allowed_requests.filter(pk=selected_request_id).exists():
                    initial["request"] = selected_request_id

        if edit_id:
            try:
                editing_log = self.get_queryset().get(pk=edit_id)
            except (EngineerActivityLog.DoesNotExist, ValueError):
                messages.error(request, "We could not find that activity log to edit.")
                return redirect("hub:activity-logs")
            form = self.form_class(engineer=request.user, instance=editing_log)
        else:
            form = self.form_class(engineer=request.user, initial=initial)

        context = self.get_context_data(form=form, editing_log=editing_log)
        return self.render_to_response(context)

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Save a new activity log or update an existing log owned by the actor."""
        log_id = request.POST.get("log_id")
        instance = None
        if log_id:
            try:
                instance = self.get_queryset().get(pk=log_id)
            except (EngineerActivityLog.DoesNotExist, ValueError):
                messages.error(request, "Unable to update the selected activity log.")
                return redirect("hub:activity-logs")

        form = self.form_class(data=request.POST, engineer=request.user, instance=instance)
        if form.is_valid():
            activity_log = form.save(commit=False)
            activity_log.engineer = request.user
            if not activity_log.request_date:
                activity_log.request_date = timezone.now().date()
            legacy_views.save_configured_instance(form, activity_log)
            if instance:
                messages.success(request, "Activity log updated successfully.")
            else:
                messages.success(request, "Activity logged successfully.")
            return redirect("hub:activity-logs")
        context = self.get_context_data(form=form, editing_log=instance)
        return self.render_to_response(context)


class EngineerActivityLogDeleteView(EngineerRequiredMixin, LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int, *args: object, **kwargs: object) -> HttpResponse:
        """Delete an activity log only from the actor's own records."""
        log = legacy_views.get_object_or_404(
            EngineerActivityLog.objects.filter(engineer=request.user),
            pk=pk,
        )
        log.delete()
        messages.success(request, "Activity log deleted successfully.")
        return redirect("hub:activity-logs")


class ReportExportView(AdminOrPmEsgRequiredMixin, LoginRequiredMixin, View):
    """Export operational or activity report data as CSV."""

    def get(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Select the report to download, defaulting to the operational report."""
        report_view = (request.GET.get("report_view") or "operational").lower()
        if report_view == "activity":
            return self._export_activity_logs()
        return self._export_operational_report()

    _format_user = staticmethod(format_export_user)
    _format_date = staticmethod(format_export_date)
    _format_datetime = staticmethod(format_report_export_datetime)

    def _export_operational_report(self) -> HttpResponse:
        """Download requests for the selected report period."""
        return self._export_report("operational")

    def _export_activity_logs(self) -> HttpResponse:
        """Download activity logs for the selected report period."""
        return self._export_report("activity")

    def _export_report(self, report_type: ReportExportType) -> HttpResponse:
        """Resolve the requested months and write the report into a CSV attachment."""
        month_filters = resolve_month_filters(self.request.GET.get("start_month"), self.request.GET.get("end_month"))
        filename = report_csv_filename(report_type, month_filters)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        write_report_csv(
            response,
            report_type,
            month_filters,
            safe_cell=_csv_safe_cell,
            format_user=self._format_user,
            format_date=self._format_date,
            format_datetime=self._format_datetime,
        )
        return response


class RequestReportView(AdminOrPmEsgRequiredMixin, LoginRequiredMixin, TemplateView):
    template_name = "hub/report.html"
    CHART_RANKING_LIMIT = 10

    @classmethod
    def _compact_ranked_chart(cls, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the preview ranking at this view's limit."""
        return compact_ranked_chart(rows, ranking_limit=cls.CHART_RANKING_LIMIT)

    _full_ranked_chart = staticmethod(full_ranked_chart)

    @staticmethod
    def _normalize_report_view(value: str | None) -> str:
        report_view = (value or "operational").lower()
        if report_view not in {"operational", "activity"}:
            return "operational"
        return report_view

<<<<<<< HEAD
    def _get_requested_edit_log(self) -> EngineerActivityLog | None:
=======
    def _get_requested_edit_log(self) -> Optional[EngineerActivityLog]:
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
        edit_log_id = (self.request.GET.get("edit_activity") or "").strip()
        if not edit_log_id:
            return None
        try:
            return EngineerActivityLog.objects.select_related("engineer", "account", "request").get(pk=edit_log_id)
        except (EngineerActivityLog.DoesNotExist, ValueError):
            messages.error(self.request, "We could not find that activity log to edit.")
            return None

    _normalize_month_value = staticmethod(normalize_month_value)
    _month_start = staticmethod(_month_start)
    _next_month_start = staticmethod(_next_month_start)

    def _resolve_activity_month_filters(
        self,
        *,
        start_month_value: str | None = None,
        end_month_value: str | None = None,
    ) -> dict[str, Any]:
        """Read the report period from explicit values or the query string."""
        return resolve_month_filters(
            self.request.GET.get("start_month") if start_month_value is None else start_month_value,
            self.request.GET.get("end_month") if end_month_value is None else end_month_value,
        )

    @staticmethod
    def _build_activity_report_url(
        *,
        start_month: str = "",
        end_month: str = "",
<<<<<<< HEAD
        edit_activity: int | None = None,
=======
        edit_activity: Optional[int] = None,
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    ) -> str:
        params = {"report_view": "activity"}
        if start_month:
            params["start_month"] = start_month
        if end_month:
            params["end_month"] = end_month
        if edit_activity is not None:
            params["edit_activity"] = str(edit_activity)
        return f"{reverse('hub:report')}?{urlencode(params)}"

    def post(self, request, *args, **kwargs):
        report_view = self._normalize_report_view(request.POST.get("report_view"))
        if report_view != "activity":
            return redirect("hub:report")

        start_month = self._normalize_month_value(request.POST.get("start_month"))
        end_month = self._normalize_month_value(request.POST.get("end_month"))
        activity_report_url = self._build_activity_report_url(start_month=start_month, end_month=end_month)

        log_id = (request.POST.get("log_id") or "").strip()
        if not log_id:
            messages.error(request, "No activity log was selected for editing.")
            return redirect(activity_report_url)

        try:
            editing_activity_log = EngineerActivityLog.objects.select_related("engineer", "account", "request").get(pk=log_id)
        except (EngineerActivityLog.DoesNotExist, ValueError):
            messages.error(request, "Unable to update the selected activity log.")
            return redirect(activity_report_url)

        activity_form = EngineerActivityLogForm(
            data=request.POST,
            engineer=editing_activity_log.engineer,
            instance=editing_activity_log,
        )
        if activity_form.is_valid():
            activity_form.save()
            engineer_name = editing_activity_log.engineer.get_full_name() or editing_activity_log.engineer.username
            messages.success(request, f"Activity log for {engineer_name} updated successfully.")
            return redirect(activity_report_url)

        context = self.get_context_data(
            report_view="activity",
            editing_activity_log=editing_activity_log,
            activity_form=activity_form,
            activity_start_month=start_month,
            activity_end_month=end_month,
        )
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        report_view = self._normalize_report_view(kwargs.get("report_view") or self.request.GET.get("report_view"))
        context["report_view"] = report_view

        if report_view == "activity":
            editing_activity_log = kwargs.get("editing_activity_log")
            activity_form = kwargs.get("activity_form")
            activity_start_month = kwargs.get("activity_start_month")
            activity_end_month = kwargs.get("activity_end_month")
            if editing_activity_log is None:
                editing_activity_log = self._get_requested_edit_log()
            if editing_activity_log is not None and activity_form is None:
                activity_form = EngineerActivityLogForm(
                    engineer=editing_activity_log.engineer,
                    instance=editing_activity_log,
                )
            try:
                activity_log_page = max(1, int(self.request.GET.get("activity_log_page") or 1))
            except (TypeError, ValueError):
                activity_log_page = 1
            context.update(
                self._build_activity_log_context(
                    start_month_value=activity_start_month,
                    end_month_value=activity_end_month,
                    page_num=activity_log_page,
                )
            )
            context["editing_activity_log"] = editing_activity_log
            context["activity_form"] = activity_form
        else:
            context.update(self._build_operational_context())
        return context

    _period_query = staticmethod(period_query)
    _month_key = staticmethod(_month_key)
    _month_label = staticmethod(_month_label)
    _build_request_health_context = staticmethod(build_request_health_context)

    def _build_operational_context(self) -> dict[str, Any]:
        """Delegate operational reporting for the selected period."""
        return build_operational_context(
            self._resolve_activity_month_filters(), ranking_limit=self.CHART_RANKING_LIMIT,
        )

    def _build_activity_log_context(
        self, *, start_month_value: str | None = None, end_month_value: str | None = None, page_num: int = 1,
    ) -> dict[str, Any]:
        """Delegate activity reporting for the selected period and page."""
        month_filters = self._resolve_activity_month_filters(
            start_month_value=start_month_value, end_month_value=end_month_value,
        )
        return build_activity_log_context(month_filters, page_num=page_num, ranking_limit=self.CHART_RANKING_LIMIT)
