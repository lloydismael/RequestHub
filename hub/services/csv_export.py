"""Select, format, and write the request and report CSV downloads."""

import csv
from collections.abc import Callable, Iterable, Iterator
from datetime import date, datetime
from typing import Any, Literal, Protocol

from django.utils import timezone

from accounts.models import User
from hub.models import EngineerActivityLog, Request

ReportExportType = Literal["operational", "activity"]
CellSanitizer = Callable[[object], object]
UserFormatter = Callable[[User | None], str]
DateFormatter = Callable[[date | None], str]
DatetimeFormatter = Callable[[datetime | None], str]

REQUEST_EXPORT_COLUMNS = (
    "Reference",
    "Account",
    "Requestor",
    "Requestor Email",
    "Engineer",
    "Engineer Email",
    "Priority",
    "Status",
    "Engagement",
    "Start Date",
    "Due Date",
    "End Date",
    "Days",
    "Description",
    "Created",
    "Updated",
)
_OPERATIONAL_EXPORT_COLUMNS = (
    "Reference",
    "Requestor",
    "Account",
    "Engagement",
    "Product Category",
    "Priority",
    "Status",
    "Engineer",
    "Backup Engineer",
    "Start Date",
    "Due Date",
    "End Date",
    "Created",
)
_ACTIVITY_EXPORT_COLUMNS = (
    "Request Date",
    "Engineer",
    "Account",
    "Request Reference",
    "Activity Type",
    "Details",
    "Hours",
    "Location",
    "Billable",
    "Created",
)


class _CsvOutput(Protocol):
    """A text sink accepted by the CSV writer."""

    def write(self, text: str, /) -> object:
        """Write serialized CSV text to the destination."""
        ...


def format_export_user(user: User | None) -> str:
    """Use the user's full name, falling back to the username."""
    if not user:
        return ""
    full_name = user.get_full_name()
    return full_name or user.username


def format_export_date(value: date | None) -> str:
    """Format a populated date without a time component."""
    return value.strftime("%Y-%m-%d") if value else ""


def format_report_export_datetime(value: datetime | None) -> str:
    """Format a report timestamp in the active local time zone."""
    if not value:
        return ""
    localized = timezone.localtime(value)
    return localized.strftime("%Y-%m-%d %H:%M:%S")


def report_csv_filename(report_type: ReportExportType, month_filters: dict[str, Any]) -> str:
    """Name the report for its resolved period before any rows are loaded."""
    prefix = "activity-logs" if report_type == "activity" else "operational-report"
    start_month = month_filters.get("start_month") or ""
    end_month = month_filters.get("end_month") or ""
    range_slug = ""
    if start_month or end_month:
        range_slug = f"-{start_month or 'start'}_to_{end_month or 'end'}"
    return f"{prefix}{range_slug}-{timezone.now().strftime('%Y%m%d-%H%M%S')}.csv"


def _write_csv(
    output: _CsvOutput,
    columns: Iterable[str],
    rows: Iterable[tuple[object, ...]],
    *,
    safe_cell: CellSanitizer,
) -> None:
    """Write headers verbatim, applying the shared text safety rule to data cells."""
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(map(safe_cell, row))


def _operational_csv_rows(
    month_filters: dict[str, Any],
    format_user: UserFormatter,
    format_date: DateFormatter,
    format_datetime: DatetimeFormatter,
) -> Iterator[tuple[object, ...]]:
    """Yield requests ordered by creation time within the local-date period."""
    queryset = (
        Request.objects.select_related("requestor", "account", "engineer", "backup_engineer")
        .order_by("created_at")
    )
    if month_filters.get("start_month_date"):
        queryset = queryset.filter(created_at__date__gte=month_filters["start_month_date"])
    if month_filters.get("end_exclusive_date"):
        queryset = queryset.filter(created_at__date__lt=month_filters["end_exclusive_date"])
    status_labels = dict(Request.Status.choices)
    engagement_labels = dict(Request.Engagement.choices)
    for request_obj in queryset:
        yield (
            request_obj.reference_code,
            format_user(getattr(request_obj, "requestor", None)),
            request_obj.account.name if request_obj.account else "",
            engagement_labels.get(request_obj.engagement_type, request_obj.engagement_type or ""),
            request_obj.product_category or "",
            request_obj.get_priority_display(),
            status_labels.get(request_obj.status, request_obj.status or ""),
            format_user(getattr(request_obj, "engineer", None)),
            format_user(getattr(request_obj, "backup_engineer", None)),
            format_date(request_obj.start_date),
            format_date(request_obj.due_date),
            format_date(request_obj.end_date),
            format_datetime(request_obj.created_at),
        )


def _activity_csv_rows(
    month_filters: dict[str, Any],
    format_user: UserFormatter,
    format_date: DateFormatter,
    format_datetime: DatetimeFormatter,
) -> Iterator[tuple[object, ...]]:
    """Yield the activity rows for the requested service dates, newest first."""
    logs = (
        EngineerActivityLog.objects.select_related("engineer", "account", "request")
        .order_by("-request_date", "-created_at")
    )
    if month_filters.get("start_month_date"):
        logs = logs.filter(request_date__gte=month_filters["start_month_date"])
    if month_filters.get("end_exclusive_date"):
        logs = logs.filter(request_date__lt=month_filters["end_exclusive_date"])
    activity_labels = dict(EngineerActivityLog.ActivityType.choices)
    location_labels = dict(EngineerActivityLog.Location.choices)
    for log in logs:
        yield (
            format_date(log.request_date),
            format_user(log.engineer),
            log.account.name if log.account else "",
            log.request.reference_code if log.request else "",
            activity_labels.get(log.activity_type, log.activity_type or ""),
            log.details or "",
            log.actual_hours,
            location_labels.get(log.location, log.location or ""),
            "Yes" if log.is_billable else "No",
            format_datetime(log.created_at),
        )


def write_report_csv(
    output: _CsvOutput,
    report_type: ReportExportType,
    month_filters: dict[str, Any],
    *,
    safe_cell: CellSanitizer,
    format_user: UserFormatter,
    format_date: DateFormatter,
    format_datetime: DatetimeFormatter,
) -> None:
    """Write the resolved report using the caller's cell formatting hooks."""
    if report_type == "activity":
        columns, rows = _ACTIVITY_EXPORT_COLUMNS, _activity_csv_rows
    else:
        columns, rows = _OPERATIONAL_EXPORT_COLUMNS, _operational_csv_rows
    _write_csv(output, columns, rows(month_filters, format_user, format_date, format_datetime), safe_cell=safe_cell)


def _request_csv_rows() -> Iterator[tuple[object, ...]]:
    """Yield requests by reference, keeping working days and stored timestamps."""
    queryset = Request.objects.select_related("account", "requestor", "engineer").order_by("reference_code")
    for req in queryset:
        requestor = req.requestor
        engineer = req.engineer
        yield (
            req.reference_code,
            req.account.name if req.account else "",
            format_export_user(requestor),
            requestor.email if requestor else "",
            format_export_user(engineer),
            engineer.email if engineer else "",
            req.get_priority_display(),
            req.get_status_display(),
            req.get_engagement_type_display(),
            format_export_date(req.start_date),
            format_export_date(req.due_date),
            format_export_date(req.end_date),
            req.days_since_creation,
            (req.description or "").replace("\r\n", " ").replace("\n", " "),
            req.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            req.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        )


def write_request_csv(output: _CsvOutput, columns: Iterable[str], *, safe_cell: CellSanitizer) -> None:
    """Write the all-requests CSV with the view's configured headers."""
    _write_csv(output, columns, _request_csv_rows(), safe_cell=safe_cell)
