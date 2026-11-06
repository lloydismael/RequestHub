import json
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

from .changelog_forms import ScreenshotField
from .models import MANILA_TZ, Changelog
from .release_changelog import ASSET_DIR, COMPARISON_FIELDS, EVIDENCE_POLICY, GROUPS, MANIFEST_PATH, MAX_COMPARISONS, ManifestError, missing_comparisons, validate_manifest


def load_manifest(path: Path = MANIFEST_PATH) -> dict | None:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ManifestError(f"not valid JSON: {exc}") from None
    return validate_manifest(data)


def summarize(commits: list[dict]) -> str:
    parts = []
    for key, label in GROUPS:
        count = sum(1 for item in commits if item["type"] == key)
        if count:
            parts.append(f"{count} {label.lower()}")
    text = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    noun = "change" if len(commits) == 1 else "changes"
    return f"This release includes {text} {noun}."


def _already_synced(revision: str) -> bool:
    return Changelog.objects.filter(release_sha=revision).exists()


def _logged_shas() -> set[str]:
    logged = set()
    for changes in Changelog.objects.exclude(release_sha__isnull=True).values_list("changes", flat=True):
        commits = changes.get("commits") if isinstance(changes, dict) else None
        if not isinstance(commits, list):
            continue
        for item in commits:
            if isinstance(item, dict) and isinstance(item.get("sha"), str):
                logged.add(item["sha"])
    return logged


def _store_comparisons(item: dict) -> dict:
    item = deepcopy(item)
    storage = Changelog._meta.get_field("before_image").storage
    root = Path(ASSET_DIR).resolve()
    for pair in item.get("comparisons", []):
        for field in ("before_image", "after_image"):
            relative = pair[field]
            path = root.joinpath(*relative.split("/")).resolve()
            if not path.is_relative_to(root):
                raise ManifestError(f"image is outside the changelog assets: {relative}")
            try:
                if path.stat().st_size > 5 * 1024 * 1024:
                    raise ManifestError(f"screenshot exceeds 5 MB: {relative}")
                data = path.read_bytes()
                image = ScreenshotField().clean(ContentFile(data, name=path.name))
            except (OSError, ValidationError) as exc:
                raise ManifestError(f"cannot import screenshot {relative}: {exc}") from None
            extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.image.format]
            name = f"changelogs/{sha256(data).hexdigest()}.{extension}"
            if not storage.exists(name):
                name = storage.save(name, image)
            pair[field] = name
    return item


def _merge_comparisons(current: list, supplied: list) -> list:
    pairs = deepcopy([pair for pair in current if isinstance(pair, dict)]) if isinstance(current, list) else []
    matched = set()
    for index, source in enumerate(supplied):
        target = next((i for i, pair in enumerate(pairs) if i not in matched and pair.get("title") == source["title"]), None)
        # Titles are editable; position preserves an admin's renamed comparison.
        if target is None and index < len(pairs) and index not in matched:
            target = index
        if target is None:
            if len(pairs) >= MAX_COMPARISONS:
                continue
            pairs.append(_store_comparisons({"comparisons": [source]})["comparisons"][0])
            matched.add(len(pairs) - 1)
            continue
        matched.add(target)
        pair = pairs[target]
        missing = [field for field in COMPARISON_FIELDS if not isinstance(pair.get(field), str) or not pair[field].strip()]
        incoming = source
        if "before_image" in missing or "after_image" in missing:
            incoming = _store_comparisons({"comparisons": [source]})["comparisons"][0]
        for field in missing:
            pair[field] = incoming[field]
        for image_field, revision_field in (("before_image", "before_revision"), ("after_image", "after_revision")):
            if image_field in missing and incoming.get(revision_field) and not pair.get(revision_field):
                pair[revision_field] = incoming[revision_field]
        if not pair.get("capture_note") and incoming.get("capture_note") and all(
            pair.get(field) == incoming[field] for field in ("before_image", "after_image")
        ):
            pair["capture_note"] = incoming["capture_note"]
    return pairs


def _enrich_release(manifest: dict) -> Changelog | None:
    with transaction.atomic():
        entry = Changelog.objects.select_for_update().get(release_sha=manifest["revision"])
        if entry.new_version != manifest["version"] or not isinstance(entry.changes, dict):
            return None
        changes = deepcopy(entry.changes)
        commits = changes.get("commits")
        if not isinstance(commits, list):
            return None
        notes = {item["sha"]: item for item in manifest["commits"]}
        for item in commits:
            if not isinstance(item, dict) or not isinstance(item.get("sha"), str):
                continue
            note = notes.get(item["sha"])
            if not note:
                continue
            if note.get("before_text") or note.get("after_text") or note.get("comparisons"):
                item["title"] = note["title"]
            fields = ["before_text", "after_text", "parent_sha"]
            if entry.requires_all_evidence or manifest.get("evidence_policy") != EVIDENCE_POLICY:
                fields.append("requires_comparison")
            for field in fields:
                if note.get(field) and not item.get(field):
                    item[field] = note[field]
            if note.get("capture_error") and missing_comparisons([item], require_all=entry.requires_all_evidence):
                item["capture_error"] = note["capture_error"]
            if note.get("comparisons"):
                item["comparisons"] = _merge_comparisons(item.get("comparisons", []), note["comparisons"])
                candidate = {key: value for key, value in item.items() if key != "capture_error"}
                if not missing_comparisons([candidate], require_all=entry.requires_all_evidence):
                    item.pop("capture_error", None)
        published = entry.is_published
        if entry.publish_when_ready:
            published = not missing_comparisons(commits, require_all=entry.requires_all_evidence)
        if changes == entry.changes and published == entry.is_published:
            return None
        entry.changes = changes
        entry.is_published = published
        entry.clean()
        entry.save(update_fields=["changes", "is_published", "updated_at"])
        return entry


def _enrich_previous_releases(manifest: dict):
    notes = {
        item["sha"]: item for item in manifest["commits"]
        if item.get("before_text") or item.get("after_text") or item.get("comparisons") or item.get("capture_error")
    }
    if not notes:
        return
    entries = Changelog.objects.exclude(release_sha__isnull=True).exclude(release_sha=manifest["revision"])
    for entry in entries.iterator():
        commits = entry.changes.get("commits") if isinstance(entry.changes, dict) else None
        if not isinstance(commits, list):
            continue
        matches = [
            notes[item["sha"]] for item in commits
            if isinstance(item, dict) and isinstance(item.get("sha"), str) and item["sha"] in notes
        ]
        if matches:
            _enrich_release({**manifest, "version": entry.new_version, "revision": entry.release_sha, "commits": matches})


def sync_release(manifest: dict) -> Changelog | None:
    manifest = validate_manifest(manifest)
    revision = manifest["revision"]
    inserting = False
    try:
        with transaction.atomic():
            _enrich_previous_releases(manifest)
            if _already_synced(revision):
                return _enrich_release(manifest)
            seen = _logged_shas()
            fresh = []
            for item in manifest["commits"]:
                if item["sha"] not in seen:
                    seen.add(item["sha"])
                    fresh.append(item)
            if not fresh:
                return None
            version = manifest["version"]
            release_date = datetime.fromisoformat(manifest["generated_at"]).astimezone(MANILA_TZ).date()
            previous = (
                Changelog.objects.filter(release_date__lte=release_date)
                .exclude(new_version=version)
                .values_list("new_version", flat=True)
                .first()
            )
            require_all = manifest.get("evidence_policy") == EVIDENCE_POLICY
            changes = {"commits": [_store_comparisons(item) for item in fresh]}
            if require_all:
                changes.update(evidence_policy=EVIDENCE_POLICY, publish_when_ready=True)
            inserting = True
            return Changelog.objects.create(
                title=f"What's new in {version}",
                previous_version=previous or "",
                new_version=version,
                release_date=release_date,
                summary=summarize(fresh),
                is_published=not missing_comparisons(fresh, require_all=require_all),
                release_sha=revision,
                changes=changes,
            )
    except IntegrityError:
        if inserting and Changelog.objects.filter(release_sha=revision).exists():
            return None
        raise
