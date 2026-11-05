from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


MANILA_TZ = ZoneInfo("Asia/Manila")


def _display_name(user):
    if not user:
        return ""
    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name or user.username


def backfill_request_lifecycle(apps, schema_editor):
    Request = apps.get_model("hub", "Request")
    RequestCommunication = apps.get_model("hub", "RequestCommunication")
    RequestLifecycleEvent = apps.get_model("hub", "RequestLifecycleEvent")

    for request in Request._base_manager.select_related("engineer", "backup_engineer").iterator():
        events = []
        sequence = 1
        revision = 1 if request.engineer_id else 0
        primary_label = _display_name(request.engineer)
        backup_label = _display_name(request.backup_engineer)

        def add_event(stage, event_type, occurred_at, key, previous_stage="", reason=""):
            nonlocal sequence
            events.append(
                RequestLifecycleEvent(
                    request_id=request.pk,
                    sequence=sequence,
                    stage=stage,
                    event_type=event_type,
                    previous_stage=previous_stage,
                    actor_id=None,
                    primary_owner_id=request.engineer_id,
                    backup_owner_id=request.backup_engineer_id,
                    actor_label="System migration",
                    primary_owner_label=primary_label,
                    backup_owner_label=backup_label,
                    assignment_revision=revision,
                    occurred_at=occurred_at,
                    source="legacy_backfill",
                    is_synthetic=True,
                    metadata={"inference": reason},
                    idempotency_key=f"legacy:{request.pk}:{key}",
                )
            )
            sequence += 1

        add_event(
            "created",
            "created",
            request.created_at,
            "created",
            reason="request_created_at",
        )
        current_stage = "created"

        if request.engineer_id:
            add_event(
                "assigned",
                "assigned",
                request.created_at,
                "assigned",
                previous_stage="created",
                reason="current_primary_assignment; historical assignment time unavailable",
            )
            current_stage = "assigned"

            communication = (
                RequestCommunication.objects.filter(
                    request_id=request.pk,
                    user_id=request.engineer_id,
                )
                .order_by("created_at", "pk")
                .first()
            )
            if communication:
                add_event(
                    "acknowledged",
                    "acknowledged",
                    communication.created_at,
                    "acknowledged",
                    previous_stage="assigned",
                    reason="earliest communication by current primary",
                )
                add_event(
                    "ongoing",
                    "started",
                    communication.created_at,
                    "ongoing",
                    previous_stage="acknowledged",
                    reason="legacy acknowledgement immediately starts work",
                )
                current_stage = "ongoing"
            elif request.status == "ongoing":
                add_event(
                    "ongoing",
                    "started",
                    request.updated_at,
                    "ongoing",
                    previous_stage="assigned",
                    reason="legacy active assigned request without acceptance evidence",
                )
                current_stage = "ongoing"

        if request.status == "completed":
            completed_at = request.updated_at
            if request.end_date:
                completed_at = datetime.combine(request.end_date, time(23, 59, 59), tzinfo=MANILA_TZ)
            add_event(
                "completed",
                "completed",
                completed_at,
                "completed",
                previous_stage=current_stage,
                reason="legacy completed status",
            )
            current_stage = "completed"

        RequestLifecycleEvent.objects.bulk_create(events)
        Request._base_manager.filter(pk=request.pk).update(
            lifecycle_stage=current_stage,
            assignment_revision=revision,
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("hub", "0042_update_sqr_scope_proposal_billing_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="request",
            name="assignment_revision",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="request",
            name="lifecycle_stage",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("assigned", "Assigned"),
                    ("acknowledged", "Acknowledged"),
                    ("ongoing", "Ongoing"),
                    ("completed", "Completed"),
                ],
                db_index=True,
                default="created",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="RequestLifecycleEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sequence", models.PositiveIntegerField()),
                ("stage", models.CharField(choices=[("created", "Created"), ("assigned", "Assigned"), ("acknowledged", "Acknowledged"), ("ongoing", "Ongoing"), ("completed", "Completed")], max_length=20)),
                ("event_type", models.CharField(choices=[("created", "Created"), ("assigned", "Assigned"), ("unassigned", "Unassigned"), ("acknowledged", "Acknowledged"), ("started", "Work started"), ("completed", "Completed"), ("reopened", "Reopened")], max_length=20)),
                ("previous_stage", models.CharField(blank=True, choices=[("created", "Created"), ("assigned", "Assigned"), ("acknowledged", "Acknowledged"), ("ongoing", "Ongoing"), ("completed", "Completed")], default="", max_length=20)),
                ("actor_label", models.CharField(blank=True, default="", max_length=255)),
                ("primary_owner_label", models.CharField(blank=True, default="", max_length=255)),
                ("backup_owner_label", models.CharField(blank=True, default="", max_length=255)),
                ("assignment_revision", models.PositiveIntegerField(default=0)),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("source", models.CharField(blank=True, default="", max_length=120)),
                ("is_synthetic", models.BooleanField(default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("idempotency_key", models.CharField(max_length=160)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="request_lifecycle_actions", to=settings.AUTH_USER_MODEL)),
                ("backup_owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="request_lifecycle_backup_snapshots", to=settings.AUTH_USER_MODEL)),
                ("primary_owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="request_lifecycle_primary_snapshots", to=settings.AUTH_USER_MODEL)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lifecycle_events", to="hub.request")),
            ],
            options={"ordering": ["sequence"]},
        ),
        migrations.AddConstraint(
            model_name="requestlifecycleevent",
            constraint=models.UniqueConstraint(fields=("request", "sequence"), name="uniq_req_lifecycle_sequence"),
        ),
        migrations.AddConstraint(
            model_name="requestlifecycleevent",
            constraint=models.UniqueConstraint(fields=("request", "idempotency_key"), name="uniq_req_lifecycle_key"),
        ),
        migrations.AddConstraint(
            model_name="requestlifecycleevent",
            constraint=models.CheckConstraint(condition=models.Q(("sequence__gte", 1)), name="req_lifecycle_sequence_gte_1"),
        ),
        migrations.AddIndex(
            model_name="requestlifecycleevent",
            index=models.Index(fields=["request", "occurred_at"], name="req_life_req_time_idx"),
        ),
        migrations.AddIndex(
            model_name="requestlifecycleevent",
            index=models.Index(fields=["primary_owner", "stage"], name="req_life_owner_stage_idx"),
        ),
        migrations.RunPython(backfill_request_lifecycle, migrations.RunPython.noop),
    ]
