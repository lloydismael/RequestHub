import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from hub.models import SqrSubmission
from hub.services.sqr_performance import build_assessment_snapshot


def build_transition_preview(submissions):
    health_counts = Counter()
    backfills = Counter({"on_track": 0, "at_risk": 0})
    warranty_differences = Counter({"post_service": 0, "general": 0})
    maintenance = Counter({"invalid_pairs": 0, "computed_only_start": 0, "manual_end_differs_from_computed": 0})
    records = conflicts = off_track = signed_completion = invalid_assessments = 0
    for submission in submissions:
        records += 1
        health = submission.delivery_health
        health_counts[health if health in SqrSubmission.DeliveryHealth.values else "__invalid__" if health else "__not_set__"] += 1
        if not submission.schedule_performance and health in backfills:
            backfills[health] += 1
        if health == SqrSubmission.DeliveryHealth.OFF_TRACK:
            off_track += 1
        has_completion = bool(
            submission.delivery_actual_finish_date or submission.legacy_delivery_completed
            or submission.overall_status == SqrSubmission.OverallStatus.COMPLETED
            or health == SqrSubmission.DeliveryHealth.COMPLETED
        )
        canceled = (
            submission.overall_status == SqrSubmission.OverallStatus.CANCELLED
            or health == SqrSubmission.DeliveryHealth.CANCELLED
            or submission.proposal_status == SqrSubmission.ProposalStatus.CLOSED_CANCELED
        )
        if (canceled and has_completion) or (
            health == SqrSubmission.DeliveryHealth.COMPLETED
            and submission.overall_status not in {"", SqrSubmission.OverallStatus.COMPLETED}
        ) or (
            health == SqrSubmission.DeliveryHealth.CANCELLED
            and submission.overall_status not in {"", SqrSubmission.OverallStatus.CANCELLED}
        ):
            conflicts += 1
        if (submission.delivery_completion_signed_date and not submission.delivery_actual_finish_date
                and not submission.legacy_delivery_completed):
            signed_completion += 1
        invalid_assessments += bool(build_assessment_snapshot(submission)["inconsistencies"])
        old_end = (submission.delivery_completion_signed_date + timedelta(days=30)
                   if submission.delivery_completion_signed_date else None)
        new_end = (submission.delivery_actual_finish_date + timedelta(days=30)
                   if submission.delivery_actual_finish_date else None)
        for key, scopes in (
            ("post_service", SqrSubmission._IMPL_SCOPES),
            ("general", SqrSubmission._WARRANTY_SCOPES),
        ):
            if submission.project_details in scopes and old_end != new_end:
                warranty_differences[key] += 1
        start, end = submission.managed_support_start_date, submission.managed_support_end_date
        if end and (not start or end < start):
            maintenance["invalid_pairs"] += 1
        eligible = submission.is_managed_support_start_eligible
        old_start = start or old_end
        if eligible and old_start and not start:
            maintenance["computed_only_start"] += 1
        if eligible and end and old_start and end != old_start + timedelta(days=365):
            maintenance["manual_end_differs_from_computed"] += 1
    return {
        "read_only": True, "total_records": records,
        "legacy_health_counts": dict(health_counts), "safe_schedule_backfills": dict(backfills),
        "legacy_signed_completion_snapshots": signed_completion,
        "terminal_conflicts": conflicts, "off_track_needing_review": off_track,
        "invalid_assessments": invalid_assessments,
        "warranty_differences": dict(warranty_differences), "maintenance": dict(maintenance),
    }


class Command(BaseCommand):
    help = "Report the unified SQR transition impact without changing project data."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Path for the aggregate JSON report.")

    def handle(self, *args, **options):
        report = build_transition_preview(SqrSubmission.objects.order_by("pk").iterator())
        try:
            Path(options["output"]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise CommandError("Could not write the SQR transition report.") from exc
        self.stdout.write(self.style.SUCCESS(f"Previewed {report['total_records']} SQR records; no project data changed."))
