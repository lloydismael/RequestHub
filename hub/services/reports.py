"""Date ranges, chart data, and report aggregations shared by Django views."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.core.paginator import InvalidPage, Page, Paginator
from django.db.models import Count, F, Min, Q, QuerySet, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.safestring import mark_safe

from accounts.models import User
from hub.mixins import ENGINEER_ACCESS_ROLES
from hub.models import EngineerActivityLog, Request, RequestCommunication


def build_request_report_data(requests: list[Request]) -> dict[str, Any]:
    """Summarize the dashboard requests without reordering category labels."""
    status_labels = dict(Request.Status.choices)
    priority_labels = dict(Request.Priority.choices)
    engagement_labels = dict(Request.Engagement.choices)

    status_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    engagement_counts: dict[str, int] = {}
    monthly_counts: dict[str, int] = {}

    for request_obj in requests:
        status_key = request_obj.status or ""
        priority_key = request_obj.priority or ""
        engagement_key = request_obj.engagement_type or ""

        if status_key:
            status_counts[status_key] = status_counts.get(status_key, 0) + 1
        if priority_key:
            priority_counts[priority_key] = priority_counts.get(priority_key, 0) + 1
        if engagement_key:
            engagement_counts[engagement_key] = engagement_counts.get(engagement_key, 0) + 1

        month_source = request_obj.created_at
        month_key = month_source.strftime("%b %Y")
        monthly_counts[month_key] = monthly_counts.get(month_key, 0) + 1

    return {
        "request_status": {
            "labels": [status_labels.get(key, key) for key in status_counts],
            "values": list(status_counts.values()),
        },
        "request_priority": {
            "labels": [priority_labels.get(key, key) for key in priority_counts],
            "values": list(priority_counts.values()),
        },
        "request_engagement": {
            "labels": [engagement_labels.get(key, key) for key in engagement_counts],
            "values": list(engagement_counts.values()),
        },
        "request_monthly": {
            "labels": list(monthly_counts.keys()),
            "values": list(monthly_counts.values()),
        },
    }


def build_engineer_report_data(requests: list[Request], user: User) -> dict[str, Any]:
    """Summarize assigned work using elapsed rather than business hours."""
    assigned_requests = [req for req in requests if req.engineer_id == user.id]

    # 1. Count of requests assigned by requestor
    requestor_counts = {}
    for req in assigned_requests:
        name = "Unknown"
        if req.requestor:
            name = req.requestor.get_full_name() or req.requestor.username or "Unknown"
        requestor_counts[name] = requestor_counts.get(name, 0) + 1

    # 2. Avg Ack Response Time
    request_ids = [req.pk for req in assigned_requests if req.pk]
    ack_map = {}
    if request_ids:
        ack_rows = (
            RequestCommunication.objects.filter(
                request_id__in=request_ids,
                user__role__in=ENGINEER_ACCESS_ROLES,
                channel__in=[RequestCommunication.Channel.OUTLOOK, RequestCommunication.Channel.TEAMS],
            )
            .values("request_id")
            .annotate(first_ack=Min("created_at"))
        )
        ack_map = {row["request_id"]: row["first_ack"] for row in ack_rows}

    ack_seconds_list = []
    for req in assigned_requests:
        ack_time = ack_map.get(req.pk)
        if ack_time:
            start = req.updated_at if req.updated_at and req.updated_at > req.created_at else req.created_at
            delta = (ack_time - start).total_seconds()
            if delta >= 0:
                # simplistic fallback without working hour logic to maintain performance on db scale
                # and decouple from the helper method inside `_annotate...`
                ack_seconds_list.append(delta)

    if ack_seconds_list:
        avg_ack_seconds = sum(ack_seconds_list) / len(ack_seconds_list)
        hours = int(avg_ack_seconds // 3600)
        minutes = int((avg_ack_seconds % 3600) // 60)
        if hours > 0:
            avg_ack_time = f"{hours}h {minutes}m"
        else:
            avg_ack_time = f"{minutes}m"
    else:
        avg_ack_time = "N/A"

    # 3. Avg Resolution Time
    completed_reqs = [req for req in assigned_requests if req.status == Request.Status.COMPLETED]
    if completed_reqs:
        avg_resolution_days = sum(req.days_since_creation for req in completed_reqs) / len(completed_reqs)
        avg_resolution_time = f"{avg_resolution_days:.1f} days"
    else:
        avg_resolution_time = "N/A"

    # 4. Avg Requests per week
    if assigned_requests:
        min_date = min(req.created_at for req in assigned_requests)
        days_span = (timezone.now() - min_date).days
        weeks = max(1, days_span / 7)
        avg_requests_per_week = f"{len(assigned_requests) / weeks:.1f}"
    else:
        avg_requests_per_week = "0"

    return {
        "requests_by_requestor": {
            "labels": list(requestor_counts.keys()),
            "values": list(requestor_counts.values()),
        },
        "avg_ack_time": avg_ack_time,
        "avg_resolution_time": avg_resolution_time,
        "avg_requests_per_week": avg_requests_per_week,
    }


def normalize_month_value(value: str | None) -> str:
    """Normalize a month parameter, leaving invalid values empty."""
    month_value = (value or "").strip()
    if not month_value:
        return ""
    try:
        datetime.strptime(month_value, "%Y-%m")
    except ValueError:
        return ""
    return month_value


def _month_start(month_value: str | None) -> date | None:
    """Return the first day of a selected month."""
    if not month_value:
        return None
    parsed = datetime.strptime(month_value, "%Y-%m")
    return date(parsed.year, parsed.month, 1)


def _next_month_start(month_start: date) -> date:
    """Return the exclusive upper bound of a selected month."""
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def resolve_month_filters(
    start_month_value: str | None,
    end_month_value: str | None,
) -> dict[str, Any]:
    """Resolve the inclusive months and the exclusive date boundary."""
    start_month = normalize_month_value(start_month_value)
    end_month = normalize_month_value(end_month_value)

    start_month_date = _month_start(start_month)
    end_month_date = _month_start(end_month)

    range_swapped = False
    if start_month_date and end_month_date and start_month_date > end_month_date:
        range_swapped = True
        start_month, end_month = end_month, start_month
        start_month_date, end_month_date = end_month_date, start_month_date

    end_exclusive_date = _next_month_start(end_month_date) if end_month_date else None

    if start_month_date and end_month_date:
        if start_month_date == end_month_date:
            label = start_month_date.strftime("%B %Y")
        else:
            label = f"{start_month_date.strftime('%B %Y')} to {end_month_date.strftime('%B %Y')}"
    elif start_month_date:
        label = f"{start_month_date.strftime('%B %Y')} onwards"
    elif end_month_date:
        label = f"Up to {end_month_date.strftime('%B %Y')}"
    else:
        label = "All months"

    return {
        "start_month": start_month,
        "end_month": end_month,
        "start_month_date": start_month_date,
        "end_exclusive_date": end_exclusive_date,
        "label": label,
        "range_swapped": range_swapped,
    }


def period_query(month_filters: dict[str, Any]) -> str:
    """Encode the selected range for report and export links."""
    params = {}
    if month_filters.get("start_month"):
        params["start_month"] = month_filters["start_month"]
    if month_filters.get("end_month"):
        params["end_month"] = month_filters["end_month"]
    # Values are strict YYYY-MM strings and urlencoded, so the result is safe to embed in hrefs.
    return mark_safe("&" + urlencode(params)) if params else ""


def _month_key(value: date | datetime) -> str:
    """Return a sortable key for a report month."""
    return f"{value.year:04d}-{value.month:02d}"


def _month_label(month_key: str) -> str:
    """Return a display label for a report month."""
    return datetime.strptime(month_key, "%Y-%m").strftime("%b %Y")


def compact_ranked_chart(rows: list[dict[str, Any]], *, ranking_limit: int) -> dict[str, Any]:
    """Summarize rows outside the visible ranking as Others."""
    visible_rows = rows[: ranking_limit]
    overflow_rows = rows[ranking_limit :]
    labels = [row["name"] for row in visible_rows]
    totals = [row["total"] for row in visible_rows]
    ongoing = [row["ongoing"] for row in visible_rows]
    completed = [row["completed"] for row in visible_rows]
    if overflow_rows:
        labels.append("Others")
        totals.append(sum(row["total"] for row in overflow_rows))
        ongoing.append(sum(row["ongoing"] for row in overflow_rows))
        completed.append(sum(row["completed"] for row in overflow_rows))
    return {
        "labels": labels,
        "totals": totals,
        "ongoing": ongoing,
        "completed": completed,
    }


def full_ranked_chart(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain all ranked rows for the expanded chart."""
    return {
        "labels": [row["name"] for row in rows],
        "totals": [row["total"] for row in rows],
        "ongoing": [row["ongoing"] for row in rows],
        "completed": [row["completed"] for row in rows],
    }


def build_request_health_context(requests_qs: QuerySet[Request]) -> dict[str, Any]:
    """Build due-date, turnaround, and trend metrics for scoped requests."""
    today = timezone.localdate()
    soon_limit = today + timedelta(days=7)
    ongoing_qs = requests_qs.filter(status=Request.Status.ONGOING)

    ongoing = Q(status=Request.Status.ONGOING)
    # Ignore completions dated before the request started (bad legacy dates would skew metrics).
    valid_completion = Q(status=Request.Status.COMPLETED, end_date__gte=F("start_date"))
    health = requests_qs.aggregate(
        overdue=Count("pk", filter=ongoing & Q(due_date__lt=today)),
        due_soon=Count("pk", filter=ongoing & Q(due_date__range=(today, soon_limit))),
        unassigned=Count("pk", filter=ongoing & Q(engineer__isnull=True)),
        valid_completions=Count("pk", filter=valid_completion),
        total_turnaround=Sum(F("end_date") - F("start_date"), filter=valid_completion),
        measured=Count("pk", filter=valid_completion & Q(due_date__isnull=False)),
        on_time=Count("pk", filter=valid_completion & Q(end_date__lte=F("due_date"))),
    )

    attention_qs = (
        ongoing_qs.filter(due_date__lte=soon_limit)
        .select_related("account", "engineer")
        .only("pk", "reference_code", "account__name", "engineer__first_name", "engineer__last_name",
              "engineer__username", "priority", "due_date")
        .order_by("due_date", "priority", "reference_code")[:10]
    )
    attention_requests = []
    for item in attention_qs:
        days_delta = (item.due_date - today).days
        engineer = item.engineer
        attention_requests.append(
            {
                "pk": item.pk,
                "url": item.get_absolute_url(),
                "reference": item.reference_code,
                "account": item.account.name,
                "engineer": (engineer.get_full_name() or engineer.username) if engineer else "Unassigned",
                "priority": item.get_priority_display(),
                "is_high": item.priority == Request.Priority.HIGH,
                "due_date": item.due_date,
                "is_overdue": days_delta < 0,
                "days": abs(days_delta),
            }
        )

    created_rows = (
        requests_qs.annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Count("id"))
    )
    completed_month_rows = (
        requests_qs.filter(valid_completion).annotate(month=TruncMonth("end_date"))
        .values("month")
        .annotate(total=Count("id"))
    )
    created_by_month = {_month_key(row["month"]): row["total"] for row in created_rows if row["month"]}
    completed_by_month = {
        _month_key(row["month"]): row["total"] for row in completed_month_rows if row["month"]
    }
    month_keys = sorted(set(created_by_month) | set(completed_by_month))
    trend_chart = {
        "labels": [_month_label(key) for key in month_keys],
        "created": [created_by_month.get(key, 0) for key in month_keys],
        "completed": [completed_by_month.get(key, 0) for key in month_keys],
    }

    return {
        "health": {
            "overdue": health["overdue"],
            "due_soon": health["due_soon"],
            "unassigned": health["unassigned"],
            "on_time_rate": round(health["on_time"] * 100 / health["measured"]) if health["measured"] else None,
            "avg_turnaround_days": (
                round(health["total_turnaround"].days / health["valid_completions"], 1)
                if health["valid_completions"] else None
            ),
        },
        "attention_requests": attention_requests,
        "request_trend_chart": trend_chart,
        "request_trend_has_data": bool(month_keys),
    }


def build_operational_context(
    month_filters: dict[str, Any],
    *,
    ranking_limit: int,
) -> dict[str, Any]:
    """Build the operational report for the requested period."""
    all_requests = Request.objects.all()
    if month_filters["start_month_date"]:
        all_requests = all_requests.filter(created_at__date__gte=month_filters["start_month_date"])
    if month_filters["end_exclusive_date"]:
        all_requests = all_requests.filter(created_at__date__lt=month_filters["end_exclusive_date"])

    account_manager_qs = (
        all_requests.values("account_manager")
        .annotate(
            total=Count("id"),
            ongoing=Count("id", filter=Q(status=Request.Status.ONGOING)),
            completed=Count("id", filter=Q(status=Request.Status.COMPLETED)),
        )
        .order_by("-total", "account_manager")
    )
    account_manager_data = []
    account_manager_count = 0
    for item in account_manager_qs:
        if item["account_manager"]:
            account_manager_count += 1
        label = item["account_manager"] or "Unspecified"
        account_manager_data.append(
            {
                "name": label,
                "total": item["total"],
                "ongoing": item["ongoing"],
                "completed": item["completed"],
            }
        )

    engineer_qs = (
        all_requests.values(
            "engineer",
            "engineer__first_name",
            "engineer__last_name",
            "engineer__username",
        )
        .annotate(
            total=Count("id"),
            ongoing=Count("id", filter=Q(status=Request.Status.ONGOING)),
            completed=Count("id", filter=Q(status=Request.Status.COMPLETED)),
        )
        .order_by("-total", "engineer__username")
    )
    engineer_data = []
    engineer_count = 0
    for item in engineer_qs:
        if item["engineer"] is None:
            name = "Unassigned"
        else:
            engineer_count += 1
            first = item.get("engineer__first_name") or ""
            last = item.get("engineer__last_name") or ""
            full_name = f"{first} {last}".strip()
            name = full_name or item.get("engineer__username") or "Engineer"
        engineer_data.append(
            {
                "name": name,
                "total": item["total"],
                "ongoing": item["ongoing"],
                "completed": item["completed"],
            }
        )

    status_labels = dict(Request.Status.choices)
    status_counts = {status: 0 for status, _ in Request.Status.choices}
    for item in all_requests.values("status").annotate(total=Count("id")):
        status_counts[item["status"]] = item["total"]
    status_breakdown = [
        {
            "label": status_labels.get(status, status.title()),
            "total": total,
            "status": status,
        }
        for status, total in status_counts.items()
        if total > 0
    ]

    engagement_counts = {
        value: {"total": 0, "ongoing": 0, "completed": 0}
        for value, _ in Request.Engagement.choices
    }
    for item in all_requests.values("engagement_type", "status").annotate(total=Count("id")):
        key = item["engagement_type"]
        bucket = engagement_counts.setdefault(
            key,
            {"total": 0, "ongoing": 0, "completed": 0},
        )
        bucket["total"] += item["total"]
        if item["status"] == Request.Status.ONGOING:
            bucket["ongoing"] += item["total"]
        elif item["status"] == Request.Status.COMPLETED:
            bucket["completed"] += item["total"]

    engagement_labels_map = dict(Request.Engagement.choices)
    engagement_order = [value for value, _ in Request.Engagement.choices]

    product_choices = list(Request._meta.get_field("product_category").choices)
    product_categories = [value for value, _ in product_choices]
    product_buckets = {
        category: {"total": 0, "ongoing": 0, "completed": 0}
        for category in product_categories
    }
    for item in all_requests.values("product_category", "status").annotate(total=Count("id")):
        category = item["product_category"] or "Others"
        bucket = product_buckets.setdefault(category, {"total": 0, "ongoing": 0, "completed": 0})
        bucket["total"] += item["total"]
        if item["status"] == Request.Status.ONGOING:
            bucket["ongoing"] += item["total"]
        elif item["status"] == Request.Status.COMPLETED:
            bucket["completed"] += item["total"]

    engagement_chart_payload = {
        "labels": [engagement_labels_map.get(value, value.title()) for value in engagement_order],
        "totals": [engagement_counts.get(value, {"total": 0})["total"] for value in engagement_order],
        "ongoing": [engagement_counts.get(value, {"ongoing": 0})["ongoing"] for value in engagement_order],
        "completed": [engagement_counts.get(value, {"completed": 0})["completed"] for value in engagement_order],
    }
    product_chart_payload = {
        "labels": [dict(product_choices).get(key, key) for key in product_buckets],
        "totals": [bucket["total"] for bucket in product_buckets.values()],
        "ongoing": [bucket["ongoing"] for bucket in product_buckets.values()],
        "completed": [bucket["completed"] for bucket in product_buckets.values()],
    }
    engagement_chart_has_data = any(value > 0 for value in engagement_chart_payload["totals"])
    product_chart_has_data = any(value > 0 for value in product_chart_payload["totals"])

    context = {
        "period_start_month": month_filters["start_month"],
        "period_end_month": month_filters["end_month"],
        "period_label": month_filters["label"],
        "period_query": period_query(month_filters),
        "period_range_swapped": month_filters.get("range_swapped", False),
        "totals": {
            "requests": sum(status_counts.values()),
            "account_managers": account_manager_count,
            "engineers": engineer_count,
            "ongoing": status_counts[Request.Status.ONGOING],
            "completed": status_counts[Request.Status.COMPLETED],
        },
        "account_manager_chart": compact_ranked_chart(account_manager_data, ranking_limit=ranking_limit),
        "account_manager_chart_full": full_ranked_chart(account_manager_data),
        "engineer_chart": compact_ranked_chart(engineer_data, ranking_limit=ranking_limit),
        "engineer_chart_full": full_ranked_chart(engineer_data),
        "engagement_chart": engagement_chart_payload,
        "engagement_chart_has_data": engagement_chart_has_data,
        "product_chart": product_chart_payload,
        "product_chart_has_data": product_chart_has_data,
        "account_manager_stats": account_manager_data,
        "engineer_stats": engineer_data,
        "status_breakdown": status_breakdown,
    }
    context.update(build_request_health_context(all_requests))
    return context


def _activity_page(logs: QuerySet[EngineerActivityLog], page_num: int, *, count: int) -> Page:
    """Paginate with the count already returned by the report aggregate."""
    paginator = Paginator(logs, 50)
    paginator.count = count
    try:
        return paginator.page(page_num)
    except InvalidPage:
        return paginator.page(1)


def build_engineer_activity_log_context(
    logs_qs: QuerySet[EngineerActivityLog],
    *,
    engineer: User,
    selected_month: str,
    page_num: int,
) -> dict[str, Any]:
    """Combine the selected log page and hours with the engineer's all-time report."""
    agg = logs_qs.aggregate(
        log_count=Count("pk"),
        total_hours=Sum("actual_hours"),
        billable_hours=Sum("actual_hours", filter=Q(is_billable=True)),
    )
    total_hours_val = agg.get("total_hours") or Decimal(0)
    billable_hours_val = agg.get("billable_hours") or Decimal(0)

    logs_page_obj = _activity_page(logs_qs, page_num, count=agg["log_count"])
    logs = list(logs_page_obj.object_list)

    monthly_activity_rows = list(
        EngineerActivityLog.objects.filter(engineer=engineer)
        .annotate(month=TruncMonth("request_date"))
        .values("month")
        .annotate(total_logs=Count("id"), total_hours=Sum("actual_hours"))
        .order_by("month")
    )
    month_options = [
        {
            "value": row["month"].strftime("%Y-%m"),
            "label": row["month"].strftime("%B %Y"),
            "count": row["total_logs"],
        }
        for row in reversed(monthly_activity_rows)
        if row.get("month")
    ]
    selected_month_label = ""
    if selected_month:
        selected_month_label = next(
            (option["label"] for option in month_options if option["value"] == selected_month),
            "",
        )

    related_requests = Request.objects.filter(Q(engineer=engineer) | Q(backup_engineer=engineer)).distinct()
    request_status_counts = {
        row["status"]: row["total"]
        for row in related_requests.values("status").annotate(total=Count("id")).order_by("status")
    }
    request_priority_counts = related_requests.values("priority").annotate(total=Count("id")).order_by("priority")
    activity_type_counts = (
        EngineerActivityLog.objects.filter(engineer=engineer)
        .values("activity_type")
        .annotate(total=Count("id"))
        .order_by("activity_type")
    )
    activity_report_data = {
        "request_status": {
            "labels": [Request.Status(status).label for status in request_status_counts],
            "values": list(request_status_counts.values()),
        },
        "request_priority": {
            "labels": [Request.Priority(value["priority"]).label for value in request_priority_counts],
            "values": [value["total"] for value in request_priority_counts],
        },
        "activity_type": {
            "labels": [EngineerActivityLog.ActivityType(value["activity_type"]).label for value in activity_type_counts],
            "values": [value["total"] for value in activity_type_counts],
        },
        "monthly": {
            "labels": [value["month"].strftime("%b %Y") for value in monthly_activity_rows if value["month"]],
            "hours": [float(value["total_hours"] or 0) for value in monthly_activity_rows if value["month"]],
            "entries": [value["total_logs"] for value in monthly_activity_rows if value["month"]],
        },
    }

    return {
        "logs": logs,
        "logs_page_obj": logs_page_obj,
        "logs_total_count": agg["log_count"],
        "hours_summary": {
            "total": total_hours_val,
            "billable": billable_hours_val,
            "non_billable": total_hours_val - billable_hours_val,
        },
        "requests_summary": {
            "total": sum(request_status_counts.values()),
            "ongoing": request_status_counts.get(Request.Status.ONGOING, 0),
            "completed": request_status_counts.get(Request.Status.COMPLETED, 0),
        },
        "activity_report_data": activity_report_data,
        "month_options": month_options,
        "selected_month": selected_month,
        "selected_month_label": selected_month_label,
    }


def build_activity_log_context(
    month_filters: dict[str, Any],
    *,
    ranking_limit: int,
    page_num: int = 1,
) -> dict[str, Any]:
    """Build activity rollups and the existing recent-log page."""
    logs_qs = EngineerActivityLog.objects.select_related("engineer", "account", "request")
    if month_filters["start_month_date"]:
        logs_qs = logs_qs.filter(request_date__gte=month_filters["start_month_date"])
    if month_filters["end_exclusive_date"]:
        logs_qs = logs_qs.filter(request_date__lt=month_filters["end_exclusive_date"])

    totals = logs_qs.aggregate(
        entries=Count("pk"),
        unique_accounts=Count("account_id", distinct=True),
        total_hours=Sum("actual_hours"),
        billable_hours=Sum("actual_hours", filter=Q(is_billable=True)),
        non_billable_hours=Sum("actual_hours", filter=Q(is_billable=False)),
    )
    total_hours_value = totals["total_hours"] or Decimal(0)
    billable_hours_value = totals["billable_hours"] or Decimal(0)
    non_billable_hours_value = totals["non_billable_hours"] or Decimal(0)

    engineer_hours = (
        logs_qs.values(
            "engineer",
            "engineer__first_name",
            "engineer__last_name",
            "engineer__username",
        )
        .annotate(
            total_hours=Sum("actual_hours"),
            billable_hours=Sum("actual_hours", filter=Q(is_billable=True)),
        )
        .order_by("engineer__first_name", "engineer__last_name", "engineer__username")
    )

    engineer_chart = {
        "labels": [],
        "billable": [],
        "non_billable": [],
    }
    engineer_table = []
    for row in engineer_hours:
        if row["engineer"] is None:
            display_name = "Unassigned"
        else:
            first = (row.get("engineer__first_name") or "").strip()
            last = (row.get("engineer__last_name") or "").strip()
            full_name = f"{first} {last}".strip()
            display_name = full_name or row.get("engineer__username") or "Engineer"
        total_val = row.get("total_hours") or Decimal(0)
        billable_val = row.get("billable_hours") or Decimal(0)
        non_billable_val = total_val - billable_val
        engineer_chart["labels"].append(display_name)
        engineer_chart["billable"].append(float(billable_val))
        engineer_chart["non_billable"].append(float(non_billable_val))
        engineer_table.append(
            {
                "name": display_name,
                "total_hours": total_val,
                "billable_hours": billable_val,
                "non_billable_hours": non_billable_val,
            }
        )

    activity_label_map = dict(EngineerActivityLog.ActivityType.choices)
    activity_hours = (
        logs_qs.values("activity_type")
        .annotate(
            total_hours=Sum("actual_hours"),
            billable_hours=Sum("actual_hours", filter=Q(is_billable=True)),
        )
        .order_by("activity_type")
    )

    activity_chart = {
        "labels": [],
        "billable": [],
        "non_billable": [],
    }
    activity_table = []
    for row in activity_hours:
        activity_key = row.get("activity_type")
        label = activity_label_map.get(activity_key, "Unspecified")
        total_val = row.get("total_hours") or Decimal(0)
        billable_val = row.get("billable_hours") or Decimal(0)
        non_billable_val = total_val - billable_val
        activity_chart["labels"].append(label)
        activity_chart["billable"].append(float(billable_val))
        activity_chart["non_billable"].append(float(non_billable_val))
        activity_table.append(
            {
                "label": label,
                "total_hours": total_val,
                "billable_hours": billable_val,
                "non_billable_hours": non_billable_val,
            }
        )

    location_label_map = dict(EngineerActivityLog.Location.choices)
    location_hours = (
        logs_qs.values("location")
        .annotate(total_hours=Sum("actual_hours"))
        .order_by("location")
    )
    location_chart = {
        "labels": [],
        "totals": [],
    }
    for row in location_hours:
        location_key = row.get("location")
        label = location_label_map.get(location_key, "Unspecified")
        total_val = row.get("total_hours") or Decimal(0)
        location_chart["labels"].append(label)
        location_chart["totals"].append(float(total_val))

    billable_chart = {
        "labels": ["Billable", "Not Billable"],
        "totals": [
            float(billable_hours_value),
            float(non_billable_hours_value),
        ],
    }

    monthly_chart = {"labels": [], "billable": [], "non_billable": []}
    monthly_rows = (
        logs_qs.annotate(month=TruncMonth("request_date"))
        .values("month")
        .annotate(
            total_hours=Sum("actual_hours"),
            billable_hours=Sum("actual_hours", filter=Q(is_billable=True)),
        )
        .order_by("month")
    )
    for row in monthly_rows:
        if not row["month"]:
            continue
        total_val = row.get("total_hours") or Decimal(0)
        billable_val = row.get("billable_hours") or Decimal(0)
        monthly_chart["labels"].append(row["month"].strftime("%b %Y"))
        monthly_chart["billable"].append(float(billable_val))
        monthly_chart["non_billable"].append(float(total_val - billable_val))

    account_rows = (
        logs_qs.values("account", "account__name")
        .annotate(
            total_hours=Sum("actual_hours"),
            billable_hours=Sum("actual_hours", filter=Q(is_billable=True)),
            entries=Count("id"),
        )
        .order_by("-total_hours", "account__name")
    )
    account_table = []
    for row in account_rows:
        total_val = row.get("total_hours") or Decimal(0)
        billable_val = row.get("billable_hours") or Decimal(0)
        account_table.append(
            {
                "name": row.get("account__name") or "Unspecified",
                "entries": row["entries"],
                "total_hours": total_val,
                "billable_hours": billable_val,
                "non_billable_hours": total_val - billable_val,
            }
        )
    account_chart = {
        "labels": [row["name"] for row in account_table[: ranking_limit]],
        "billable": [float(row["billable_hours"]) for row in account_table[: ranking_limit]],
        "non_billable": [float(row["non_billable_hours"]) for row in account_table[: ranking_limit]],
    }

    billable_rate = (
        round(float(billable_hours_value) * 100 / float(total_hours_value)) if total_hours_value else None
    )

    page_obj = _activity_page(logs_qs.order_by("-request_date", "-created_at"), page_num, count=totals["entries"])
    recent_logs = list(page_obj.object_list)

    return {
        "activity_totals": {
            "entries": totals["entries"],
            "total_hours": total_hours_value,
            "billable_hours": billable_hours_value,
            "non_billable_hours": non_billable_hours_value,
            "unique_accounts": totals["unique_accounts"],
            "billable_rate": billable_rate,
        },
        "period_start_month": month_filters["start_month"],
        "period_end_month": month_filters["end_month"],
        "period_label": month_filters["label"],
        "period_query": period_query(month_filters),
        "period_range_swapped": month_filters.get("range_swapped", False),
        "activity_monthly_chart": monthly_chart,
        "activity_account_chart": account_chart,
        "activity_account_table": account_table,
        "activity_start_month": month_filters["start_month"],
        "activity_end_month": month_filters["end_month"],
        "activity_month_filter_label": month_filters["label"],
        "activity_range_swapped": month_filters.get("range_swapped", False),
        "activity_engineer_chart": engineer_chart,
        "activity_engineer_table": engineer_table,
        "activity_type_chart": activity_chart,
        "activity_type_table": activity_table,
        "activity_location_chart": location_chart,
        "activity_billable_chart": billable_chart,
        "activity_logs": recent_logs,
        "activity_logs_page_obj": page_obj,
    }
