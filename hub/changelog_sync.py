import json
from datetime import datetime
from pathlib import Path

from django.db import IntegrityError, transaction

from .models import MANILA_TZ, Changelog
from .release_changelog import GROUPS, MANIFEST_PATH, ManifestError, validate_manifest


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


def sync_release(manifest: dict) -> Changelog | None:
    revision = manifest["revision"]
    if _already_synced(revision):
        return None
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
    try:
        with transaction.atomic():
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
                is_published=True,
                release_sha=revision,
                changes={"commits": fresh},
            )
    except IntegrityError:
        return None
