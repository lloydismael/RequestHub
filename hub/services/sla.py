"""Manila business hours and acknowledgement SLA for dashboard rows."""

from datetime import datetime, timedelta

from django.db.models import Min
from django.utils import timezone

from accounts.models import User
from hub.mixins import ENGINEER_ACCESS_ROLES
from hub.models import MANILA_TZ, Request, RequestCommunication


def working_seconds_between(start_dt: datetime, end_dt: datetime) -> int:
    """Return working seconds between two datetimes within 8:30–18:30, Monday–Friday (Manila)."""
    if end_dt <= start_dt:
        return 0

    # Normalize to Manila timezone to align with working hours definition.
    start = timezone.localtime(start_dt, MANILA_TZ)
    end = timezone.localtime(end_dt, MANILA_TZ)

    work_start_hour = 8
    work_start_minute = 30
    work_end_hour = 18
    work_end_minute = 30

    def next_work_start(dt: datetime) -> datetime:
        """Find the next weekday opening time."""
        dt = dt.astimezone(MANILA_TZ)
        # Move to today's start if before work start.
        candidate = dt.replace(hour=work_start_hour, minute=work_start_minute, second=0, microsecond=0)
        if dt.weekday() < 5 and dt < candidate:
            return candidate
        # Otherwise move to next weekday 8:30.
        offset_days = 1
        next_day = dt + timedelta(days=offset_days)
        while next_day.weekday() >= 5:  # skip weekends
            offset_days += 1
            next_day = dt + timedelta(days=offset_days)
        return next_day.replace(hour=work_start_hour, minute=work_start_minute, second=0, microsecond=0, tzinfo=MANILA_TZ)

    total_seconds = 0
    current = start

    # If starting outside working hours, jump to next window.
    work_start_today = current.replace(hour=work_start_hour, minute=work_start_minute, second=0, microsecond=0)
    work_end_today = current.replace(hour=work_end_hour, minute=work_end_minute, second=0, microsecond=0)
    if current.weekday() >= 5 or current >= work_end_today or current < work_start_today:
        current = next_work_start(current)

    while current < end:
        if current.weekday() >= 5:
            current = next_work_start(current)
            continue

        work_start = current.replace(hour=work_start_hour, minute=work_start_minute, second=0, microsecond=0)
        work_end = current.replace(hour=work_end_hour, minute=work_end_minute, second=0, microsecond=0)

        current = max(current, work_start)

        if current >= work_end:
            current = next_work_start(current)
            continue

        slice_end = min(work_end, end)
        total_seconds += int((slice_end - current).total_seconds())
        current = slice_end

        if current >= work_end:
            current = next_work_start(current)

    return total_seconds


def format_duration(delta: timedelta) -> str:
    """Format a duration for the acknowledgement tooltip."""
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "<1 minute"
    minutes = total_seconds // 60
    if minutes < 1:
        return "<1 minute"
    hours, rem_minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if rem_minutes:
        parts.append(f"{rem_minutes} minute{'s' if rem_minutes != 1 else ''}")
    if not parts:
        return "<1 minute"
    return " ".join(parts)


def annotate_acknowledgement_status(requests: list[Request]) -> None:
    """Attach the existing business-hours SLA status to dashboard rows."""
    if not requests:
        return
    request_ids = [req.pk for req in requests if req.pk]
    if not request_ids:
        return

    ack_rows = (
        RequestCommunication.objects.filter(
            request_id__in=request_ids,
            user__role__in=[*ENGINEER_ACCESS_ROLES, User.Roles.ADMIN, User.Roles.PM_ESG],
            channel__in=[
                RequestCommunication.Channel.OUTLOOK,
                RequestCommunication.Channel.TEAMS,
            ],
        )
        .values("request_id")
        .annotate(first_ack=Min("created_at"))
    )
    ack_map = {row["request_id"]: row["first_ack"] for row in ack_rows}

    now = timezone.now()
    amber_threshold_seconds = int(timedelta(minutes=45).total_seconds())
    sla_threshold_seconds = int(timedelta(hours=1).total_seconds())

    for req in requests:
        if not req.engineer_id:
            req.ack_sla_status = ""
            req.ack_sla_tooltip = "Awaiting engineer assignment; acknowledgement SLA starts after assignment."
            req.ack_start_iso = ""
            req.ack_first_iso = ""
            req.is_acknowledged = False
            continue

        start_anchor = req.created_at
        if req.updated_at and req.updated_at > req.created_at:
            start_anchor = req.updated_at

        ack_time = ack_map.get(req.pk)
        status = ""
        tooltip = ""
        if ack_time:
            delta_seconds = working_seconds_between(start_anchor, ack_time)
            # If delta is negative, the start_anchor (likely updated_at) is later than ack_time.
            # This happens on reassignment. We treat it as acknowledged.
            if delta_seconds <= sla_threshold_seconds:
                status = "green"
                if delta_seconds <= 0:
                    # Fallback calculation using created_at to give a roughly meaningful duration
                    fallback_delta = working_seconds_between(req.created_at, ack_time)
                    duration_str = format_duration(timedelta(seconds=max(0, fallback_delta)))
                    tooltip = f"Acknowledged previously ({duration_str} from creation)"
                else:
                    tooltip = f"Acknowledged within SLA ({format_duration(timedelta(seconds=delta_seconds))})"
            else:
                status = "red"
                tooltip = f"Acknowledged after 1-hour SLA ({format_duration(timedelta(seconds=delta_seconds))})"
        else:
            age_seconds = working_seconds_between(start_anchor, now)
            if age_seconds <= 0:
                tooltip = "Awaiting acknowledgement (outside working hours)"
            elif age_seconds >= sla_threshold_seconds:
                status = "red"
                tooltip = f"No acknowledgement after {format_duration(timedelta(seconds=age_seconds))} (working hours)"
            elif age_seconds >= amber_threshold_seconds:
                status = "amber"
                tooltip = f"Awaiting acknowledgement ({format_duration(timedelta(seconds=age_seconds))} elapsed in working hours)"
            else:
                tooltip = f"Awaiting acknowledgement ({format_duration(timedelta(seconds=age_seconds))} elapsed in working hours)"

        req.ack_sla_status = status
        req.ack_sla_tooltip = tooltip or "Acknowledgement status unavailable"
        req.ack_start_iso = start_anchor.isoformat() if start_anchor else ""
        req.ack_first_iso = ack_time.isoformat() if ack_time else ""
        req.is_acknowledged = bool(ack_time)
