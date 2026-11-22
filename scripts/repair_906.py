"""One-off data repair for REQ-00906 (pk 906).

Forensics confirmed Bucket A: the acknowledge was performed in reality (engineer
Outlook draft exists at 08:34) but the DB transaction never persisted — no
ACKNOWLEDGED/STARTED lifecycle events exist and `lifecycle_stage`=assigned while
`status`=ongoing. This script replays the acknowledge semantics using the exact
production helpers (`acknowledge_request` invariant): creates the ACKNOWLEDGED +
STARTED events and aligns the denormalized field.

Run from project root:
    .venv\\Scripts\\python.exe scripts_check\\repair_906.py
"""
import django
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "request_hub.settings")
django.setup()

from django.db import transaction
from django.utils import timezone

from hub.models import Request, RequestLifecycleEvent
from hub.services.request_lifecycle import _create_event, _next_sequence

REQUEST_ID = 906
REVISION = 1


def main() -> int:
    request = Request.all_objects.select_related("engineer").get(pk=REQUEST_ID)
    print(f"=== BEFORE ===")
    print(f"lifecycle_stage={request.lifecycle_stage} status={request.status} "
          f"assignment_revision={request.assignment_revision} engineer={request.engineer}")

    if request.assignment_revision != REVISION:
        print(f"ABORT: expected revision {REVISION}, found {request.assignment_revision}.")
        return 1
    if not request.engineer_id:
        print("ABORT: no primary engineer assigned.")
        return 1

    ack_exists = request.lifecycle_events.filter(
        event_type=RequestLifecycleEvent.EventType.ACKNOWLEDGED,
        assignment_revision=request.assignment_revision,
    ).exists()
    if ack_exists:
        print("ACKNOWLEDGED event already exists for this revision — nothing to repair.")

    with transaction.atomic():
        req = Request.all_objects.select_for_update(of=("self",)).select_related("engineer", "backup_engineer").get(pk=REQUEST_ID)
        actor = req.engineer
        occurred_at = timezone.now()
        acknowledged = _create_event(
            req,
            event_type=RequestLifecycleEvent.EventType.ACKNOWLEDGED,
            stage=Request.LifecycleStage.ACKNOWLEDGED,
            previous_stage=Request.LifecycleStage.ASSIGNED,
            actor=actor,
            source="Data repair",
            idempotency_key=f"accepted:{req.assignment_revision}",
            occurred_at=occurred_at,
            is_synthetic=True,
        )
        started = _create_event(
            req,
            event_type=RequestLifecycleEvent.EventType.STARTED,
            stage=Request.LifecycleStage.ONGOING,
            previous_stage=Request.LifecycleStage.ACKNOWLEDGED,
            actor=actor,
            source="Data repair",
            idempotency_key=f"started:{req.assignment_revision}",
            occurred_at=occurred_at,
            is_synthetic=True,
        )
        req.lifecycle_stage = Request.LifecycleStage.ONGOING
        req.status = Request.Status.ONGOING
        req.save(update_fields=["lifecycle_stage", "status", "updated_at"])
        print("Repaired: acknowledged", acknowledged.pk, "| started", started.pk)

    # Re-read and print state
    request.refresh_from_db()
    print("=== AFTER ===")
    print(f"lifecycle_stage={request.lifecycle_stage} status={request.status} "
          f"assignment_revision={request.assignment_revision}")
    print("Events:")
    for ev in request.lifecycle_events.order_by("sequence"):
        print(f"  seq={ev.sequence} event={ev.event_type} stage={ev.stage} prev={ev.previous_stage} "
              f"rev={ev.assignment_revision} synthetic={ev.is_synthetic} source={ev.source} "
              f"key={ev.idempotency_key} actor={ev.actor_label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())