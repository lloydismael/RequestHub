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
from .release_changelog import ASSET_DIR, GROUPS, MANIFEST_PATH, ManifestError, missing_comparisons, validate_manifest


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
            for field in ("before_text", "after_text", "requires_comparison"):
                if note.get(field) and not item.get(field):
                    item[field] = note[field]
            if note.get("comparisons"):
                packaged = _store_comparisons(note)["comparisons"]
                comparisons = item.get("comparisons")
                if not isinstance(comparisons, list):
                    comparisons = []
                for index, comparison in enumerate(packaged):
                    if index >= len(comparisons) or not isinstance(comparisons[index], dict):
                        if index >= len(comparisons):
                            comparisons.append(comparison)
                        else:
                            comparisons[index] = comparison
                        continue
                    for field, value in comparison.items():
                        if value and not comparisons[index].get(field):
                            comparisons[index][field] = value
                item["comparisons"] = comparisons
        if changes == entry.changes:
            return None
        entry.changes = changes
        entry.save(update_fields=["changes", "updated_at"])
        return entry


def _enrich_previous_releases(manifest: dict):
    notes = {
        item["sha"]: item for item in manifest["commits"]
        if item.get("before_text") or item.get("after_text") or item.get("comparisons")
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
            return Changelog.objects.create(
                title=f"What's new in {version}",
                previous_version=previous or "",
                new_version=version,
                release_date=release_date,
                summary=summarize(fresh),
                is_published=not missing_comparisons(fresh),
                release_sha=revision,
                changes={"commits": [_store_comparisons(item) for item in fresh]},
            )
    except IntegrityError:
        return None
