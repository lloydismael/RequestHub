"""Local hub-only recovery. SHA-256 detects corruption, not authenticity."""
import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.management.color import no_style
from django.db import connection, models, transaction
from django.utils import timezone

from .models import BackupSnapshot, RecoveryEvent, RecoveryPreview, User

VERSION = "2"
LEGACY_LABELS = ("hub.account", "hub.request", "hub.sqrsubmission", "hub.requestcommunication", "hub.engineeractivitylog", "hub.statuslog")
MODEL_LABELS = (
    "hub.account", "hub.request", "hub.requestlifecycleevent", "hub.requestcommunication",
    "hub.engineeractivitylog", "hub.sqrsubmission", "hub.sqrsubmissionchange",
    "hub.sqrprojectperformance", "hub.sqrsubmissionhistory", "hub.notification",
    "hub.statuslog", "hub.formdefinition", "hub.formversion", "hub.formanswerset",
    "hub.formeditorauditevent",
)
EXCLUSIONS = ["Users, credentials, permissions and sessions", "Media and stored files", "Backup history and recovery audit events"]
CONFIRMATION_PHRASE = "RESTORE"


class RecoveryError(ValueError):
    pass


def max_bytes():
    return int(getattr(settings, "BACKUP_MAX_BYTES", 25 * 1024 * 1024))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def checksum(payload):
    return hashlib.sha256(canonical({k: v for k, v in payload.items() if k != "checksum"})).hexdigest()


def hub_models():
    result = [apps.get_model(label) for label in MODEL_LABELS]
    actual = {m._meta.label_lower for m in apps.get_app_config("hub").get_models()}
    if actual != set(MODEL_LABELS) or any(m._meta.local_many_to_many for m in result):
        raise RecoveryError("Hub schema changed; recovery coverage must be updated before proceeding.")
    return result


def audit(actor, action, outcome, snapshot=None, **details):
    return RecoveryEvent.objects.create(actor=actor, action=action, outcome=outcome, snapshot=snapshot, details=details)


def require_admin(actor):
    if not actor or not actor.is_authenticated or actor.role != User.Roles.ADMIN or not actor.is_active:
        raise RecoveryError("Administrator access is required.")


def related_models():
    """Lock both referenced tables and all reverse/cascading dependencies."""
    found = set(hub_models())
    pending = list(found)
    while pending:
        model = pending.pop()
        neighbors = [f.related_model for f in model._meta.get_fields() if f.is_relation and f.related_model]
        for neighbor in neighbors:
            if neighbor not in found:
                found.add(neighbor)
                pending.append(neighbor)
    return sorted(found, key=lambda m: m._meta.db_table)


@contextmanager
def locked_hub():
    with transaction.atomic():
        if connection.vendor == "postgresql":
            tables = set()
            for model in related_models():
                tables.add(model._meta.db_table)
                for field in model._meta.local_many_to_many:
                    tables.add(field.remote_field.through._meta.db_table)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s'")
                cursor.execute("LOCK TABLE " + ", ".join(connection.ops.quote_name(t) for t in sorted(tables)) + " IN EXCLUSIVE MODE")
        elif connection.vendor == "sqlite":
            # Upgrade the deferred transaction to a database-wide writer lock before reads.
            with connection.cursor() as cursor:
                cursor.execute("UPDATE " + connection.ops.quote_name(apps.get_model("hub.Account")._meta.db_table) + " SET id=id WHERE 0=1")
        else:
            raise RecoveryError("Recovery supports PostgreSQL and SQLite only.")
        yield


def records_for(labels):
    records = []
    for label in labels:
        model = apps.get_model(label)
        records.extend(json.loads(serializers.serialize("json", model._base_manager.all().order_by("pk"))))
    return records


def build_payload():
    hub_models()
    payload = {"app": "request-hub", "backup_version": VERSION, "created_at": timezone.now().isoformat(),
               "models": list(MODEL_LABELS), "exclusions": EXCLUSIONS, "data": records_for(MODEL_LABELS)}
    payload["checksum"] = checksum(payload)
    if len(canonical(payload)) > max_bytes():
        raise RecoveryError("Backup exceeds the configured size limit.")
    return payload


def persist_snapshot(actor, payload, reason):
    counts = {label: 0 for label in MODEL_LABELS}
    for record in payload["data"]:
        counts[record["model"]] += 1
    return BackupSnapshot.objects.create(created_by=actor, reason=reason[:255], payload=payload,
                                        checksum=checksum(payload), size=len(canonical(payload)), counts=counts)


def create_snapshot(actor, reason="Manual backup"):
    require_admin(actor)
    try:
        with locked_hub():
            payload = build_payload()
            validate_payload(payload)
            _dry_run(payload)
            snapshot = persist_snapshot(actor, payload, reason or "Manual backup")
            audit(actor, "create", "success", snapshot)
        return snapshot
    except Exception as exc:
        audit(actor, "create", "failed", error=str(exc)[:1000])
        raise


def read_upload(upload):
    if upload.size > max_bytes():
        raise RecoveryError("Backup exceeds the configured size limit.")
    raw = upload.read(max_bytes() + 1)
    if len(raw) > max_bytes():
        raise RecoveryError("Backup exceeds the configured size limit.")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RecoveryError("Duplicate JSON key: " + key)
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                          parse_constant=lambda value: (_ for _ in ()).throw(RecoveryError("Invalid JSON number.")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RecoveryError("Could not read backup JSON: " + str(exc)) from exc


def _integer(value):
    return type(value) is int and value > 0 and value <= 9223372036854775807


def validate_payload(payload):
    hub_models()
    if not isinstance(payload, dict) or len(canonical(payload)) > max_bytes():
        raise RecoveryError("Invalid or oversized backup payload.")
    version = payload.get("backup_version")
    if type(version) not in (str, int) or version not in ("1", "2", 1, 2) or payload.get("app") != "request-hub":
        raise RecoveryError("Unsupported backup format or version.")
    legacy = str(version) == "1"
    allowed = {"app", "backup_version", "created_at", "data", "checksum"}
    if not legacy:
        allowed |= {"models", "exclusions"}
        if payload.get("models") != list(MODEL_LABELS) or payload.get("exclusions") != EXCLUSIONS:
            raise RecoveryError("Backup coverage does not match the complete hub schema.")
    if set(payload) - allowed or not isinstance(payload.get("data"), list) or not isinstance(payload.get("created_at"), str):
        raise RecoveryError("Invalid backup envelope.")
    try:
        from django.utils.dateparse import parse_datetime
        if parse_datetime(payload["created_at"]) is None:
            raise ValueError()
    except (ValueError, TypeError):
        raise RecoveryError("Invalid backup timestamp.")
    verified = "checksum" in payload
    if (not legacy and not verified) or (verified and payload["checksum"] != checksum(payload)):
        raise RecoveryError("Backup checksum mismatch (checksum is not proof of authenticity).")
    labels = LEGACY_LABELS if legacy else MODEL_LABELS
    ids = {label: set() for label in labels}
    grouped = {label: [] for label in labels}
    for row in payload["data"]:
        if not isinstance(row, dict) or set(row) != {"model", "pk", "fields"} or row["model"] not in ids:
            raise RecoveryError("Unknown model or malformed backup record.")
        label, pk = row["model"], row["pk"]
        if not _integer(pk) or pk in ids[label]:
            raise RecoveryError("Invalid or duplicate primary key in " + label)
        ids[label].add(pk)
        model = apps.get_model(label)
        fields = {f.name: f for f in model._meta.local_fields if not f.primary_key}
        values = row["fields"]
        if not isinstance(values, dict) or set(values) - set(fields):
            raise RecoveryError("Unknown fields in " + label)
        # Legacy snapshots predate some fields; only explicitly defaulted/nullable additions are safe.
        missing = set(fields) - set(values)
        if missing and (not legacy or any(not fields[n].has_default() and not fields[n].null for n in missing)):
            raise RecoveryError("Missing required fields in " + label)
        for name, value in values.items():
            field = fields[name]
            if field.is_relation:
                if value is not None and not _integer(value):
                    raise RecoveryError("Invalid foreign key in " + label + "." + name)
            else:
                if isinstance(value, (dict, list)) and not isinstance(field, models.JSONField):
                    raise RecoveryError("Invalid field value in " + label + "." + name)
                if isinstance(field, models.BooleanField) and type(value) is not bool:
                    raise RecoveryError("Invalid boolean in " + label + "." + name)
                if isinstance(field, models.IntegerField) and value is not None and type(value) is not int:
                    raise RecoveryError("Invalid integer in " + label + "." + name)
            try:
                converted = field.to_python(value)
                if field.is_relation:
                    if value is None and not field.null:
                        raise ValidationError("This foreign key cannot be null.")
                else:
                    if isinstance(field, (models.CharField, models.TextField)) and value is not None and not isinstance(value, str):
                        raise ValidationError("Expected text.")
                    # blank is a form rule, not a storage constraint: existing rows
                    # can legitimately contain empty strings or default empty JSON.
                    if converted is None and not field.null:
                        raise ValidationError("This field cannot be null.")
                    if converted not in field.empty_values:
                        field.validate(converted, model())
                field.run_validators(converted)
            except (ValidationError, ValueError, TypeError, OverflowError) as exc:
                raise RecoveryError("Invalid field " + label + "." + name + ": " + str(exc)) from exc
        grouped[label].append(row)
    external_ids = {}
    for label, rows in grouped.items():
        for field in apps.get_model(label)._meta.local_fields:
            if not field.is_relation:
                continue
            target = field.related_model._meta.label_lower
            wanted = {row["fields"].get(field.name) for row in rows} - {None}
            if target in ids:
                existing = ids[target]
            else:
                key = (target, tuple(sorted(wanted)))
                if key not in external_ids:
                    external_ids[key] = set(field.related_model._base_manager.filter(pk__in=wanted).values_list("pk", flat=True))
                existing = external_ids[key]
            if not wanted <= existing:
                raise RecoveryError("Missing referenced records in " + label + "." + field.name)
    if legacy:
        for label in set(MODEL_LABELS) - set(LEGACY_LABELS):
            if apps.get_model(label)._base_manager.exists():
                raise RecoveryError("Legacy backup excludes existing " + label + "; restore rejected to prevent data loss.")
    _validate_form_references(grouped, ids)
    counts = {label: len(grouped.get(label, [])) for label in MODEL_LABELS}
    warnings = ["Users and media are excluded; referenced users must already exist.", "Checksums detect corruption, not authenticity. Only restore trusted backups."]
    if legacy:
        warnings.append("Legacy six-model backup: excluded hub tables must remain empty; missing defaulted fields use current defaults.")
        if not verified:
            warnings.append("Legacy backup has no checksum.")
    return {"version": str(version), "created_at": payload["created_at"], "counts": counts,
            "total_records": len(payload["data"]), "warnings": warnings, "exclusions": EXCLUSIONS,
            "checksum_verified": verified,
            "rows": [{"label": label, "current": apps.get_model(label)._base_manager.count(), "incoming": counts[label]} for label in MODEL_LABELS]}


def _validate_form_references(grouped, ids):
    definitions = {r["pk"]: r["fields"] for r in grouped.get("hub.formdefinition", [])}
    versions = {r["pk"]: r["fields"] for r in grouped.get("hub.formversion", [])}
    for pk, fields in definitions.items():
        version = fields.get("published_version")
        if version is not None and versions[version]["definition"] != pk:
            raise RecoveryError("Published form version belongs to another definition.")
    for row in grouped.get("hub.formeditorauditevent", []):
        fields = row["fields"]
        version = fields.get("version")
        if version is not None and versions[version]["definition"] != fields["definition"]:
            raise RecoveryError("Audit version belongs to another form.")
    for row in grouped.get("hub.formanswerset", []):
        fields = row["fields"]
        ct = ContentType.objects.get(pk=fields["content_type"])
        target = ct.model_class()
        if target is None or target._meta.label_lower not in ids or fields["object_id"] not in ids[target._meta.label_lower]:
            raise RecoveryError("Missing or excluded form answer target.")
        version = fields.get("schema_version")
        if version is not None and versions[version]["definition"] != fields["definition"]:
            raise RecoveryError("Answer version belongs to another form.")


def _check_external_dependents():
    included = set(hub_models())
    for model in included:
        for relation in model._meta.related_objects:
            if relation.related_model not in included:
                if relation.related_model._base_manager.filter(**{relation.field.name + "__isnull": False}).exists():
                    raise RecoveryError("Restore would affect excluded dependent records: " + relation.related_model._meta.label_lower)


def _replace(payload):
    """Raw SQL avoids domain save hooks, notifications and immutable-record managers."""
    _check_external_dependents()
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
        for model in reversed(hub_models()):
            cursor.execute("DELETE FROM " + quote(model._meta.db_table))
        for item in serializers.deserialize("python", payload["data"]):
            obj = item.object
            fields = obj._meta.local_concrete_fields
            values = [f.get_db_prep_save(getattr(obj, f.attname), connection) for f in fields]
            cursor.execute("INSERT INTO " + quote(obj._meta.db_table) + " (" + ",".join(quote(f.column) for f in fields) + ") VALUES (" + ",".join(["%s"] * len(fields)) + ")", values)
        connection.check_constraints(table_names=[m._meta.db_table for m in hub_models()])
        if connection.vendor == "postgresql":
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    expected = sorted(payload["data"], key=lambda row: (row["model"], row["pk"]))
    actual = sorted(records_for(LEGACY_LABELS if str(payload["backup_version"]) == "1" else MODEL_LABELS), key=lambda row: (row["model"], row["pk"]))
    if str(payload["backup_version"]) == "1":
        actual = [{**r, "fields": {k: v for k, v in r["fields"].items() if k in e["fields"]}} for r, e in zip(actual, expected)]
    if canonical(actual) != canonical(expected):
        raise RecoveryError("Post-restore record verification failed.")


def _dry_run(payload):
    with transaction.atomic():
        _replace(payload)
        transaction.set_rollback(True)


def create_preview(actor, payload, snapshot=None):
    require_admin(actor)
    try:
        with locked_hub():
            report = validate_payload(payload)
            _dry_run(payload)
            preview = RecoveryPreview.objects.create(created_by=actor, payload=payload, snapshot=snapshot,
                                                     expires_at=timezone.now() + timedelta(minutes=15))
            audit(actor, "validate", "success", snapshot, counts=report["counts"])
        return preview, report
    except Exception as exc:
        audit(actor, "validate", "failed", snapshot, error=str(exc)[:1000])
        raise


def restore_preview(actor, token):
    require_admin(actor)
    if connection.in_atomic_block:
        raise RecoveryError("Restore must run outside a request-wide transaction so the safety snapshot is committed independently.")
    safety = None
    try:
        token = UUID(str(token))
        # Consume and commit before attempting restore: tokens cannot be replayed even after failure.
        with transaction.atomic():
            preview = RecoveryPreview.objects.select_for_update().get(token=token, created_by=actor)
            if preview.consumed_at or preview.expires_at <= timezone.now():
                raise RecoveryError("Preview has expired or was already used. Validate the backup again.")
            preview.consumed_at = timezone.now()
            preview.save(update_fields=["consumed_at"])
        with locked_hub():
            validate_payload(preview.payload)
            safety_payload = build_payload()
            validate_payload(safety_payload)
            _dry_run(safety_payload)
            safety = persist_snapshot(actor, safety_payload, "Automatic safety copy before restore")
            audit(actor, "create", "success", safety, automatic=True)
        # The independently committed safety copy survives any subsequent rollback.
        with locked_hub():
            if canonical(records_for(MODEL_LABELS)) != canonical(safety_payload["data"]):
                raise RecoveryError("Hub data changed after the safety snapshot. Validate and retry; no data was restored.")
            report = validate_payload(preview.payload)
            _replace(preview.payload)
            with connection.cursor() as cursor:
                for sql in connection.ops.sequence_reset_sql(no_style(), hub_models()):
                    cursor.execute(sql)
            audit(actor, "restore", "success", safety, source_snapshot=preview.snapshot_id, counts=report["counts"])
        return safety
    except Exception as exc:
        audit(actor, "restore", "failed", safety, error=str(exc)[:1000])
        if isinstance(exc, (RecoveryPreview.DoesNotExist, ValueError)) and not isinstance(exc, RecoveryError):
            raise RecoveryError("Invalid restore preview token.") from exc
        raise


def recover_requests(actor, request_ids):
    require_admin(actor)
    try:
        if not request_ids or len(request_ids) > 200:
            raise RecoveryError("Select between 1 and 200 deleted requests.")
        ids = [int(value) for value in request_ids]
        if len(ids) != len(set(ids)) or not all(_integer(value) for value in ids):
            raise RecoveryError("Invalid or duplicate request selection.")
        model = apps.get_model("hub.Request")
        with locked_hub():
            rows = list(model.all_objects.select_for_update().filter(pk__in=ids, is_deleted=True))
            if len(rows) != len(ids):
                raise RecoveryError("Selection changed or contains requests that are not deleted. Refresh and retry.")
            changed = model.all_objects.filter(pk__in=ids, is_deleted=True).update(is_deleted=False, deleted_at=None, updated_at=timezone.now())
            if changed != len(ids) or model.all_objects.filter(pk__in=ids, is_deleted=True).exists():
                raise RecoveryError("Deleted request recovery verification failed.")
            audit(actor, "recover", "success", request_ids=ids, count=changed)
        return changed
    except Exception as exc:
        audit(actor, "recover", "failed", error=str(exc)[:1000])
        raise
