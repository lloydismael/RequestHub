from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from hub.changelog_sync import load_manifest, sync_release
from hub.models import Changelog
from hub.release_changelog import ASSET_DIR, MANIFEST_PATH, ManifestError, curated_manifests


class Command(BaseCommand):
    help = "Log release commits and restore packaged screenshot evidence for existing releases."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Path to the release manifest JSON.")

    def handle(self, *args, **options):
        path = Path(options["manifest"])
        try:
            manifest = load_manifest(path)
            packaged = curated_manifests(ASSET_DIR)
            for notes in packaged:
                if Changelog.objects.filter(release_sha=notes["revision"], new_version=notes["version"]).exists():
                    if sync_release(notes) is not None:
                        self.stdout.write(f"Updated screenshot evidence for {notes['version']}.")
        except ManifestError as exc:
            raise CommandError(f"Invalid release manifest: {exc}") from None
        if manifest is None:
            self.stdout.write(f"No release manifest at {path}; nothing to sync.")
            return
        try:
            existed = Changelog.objects.filter(release_sha=manifest["revision"]).exists()
            entry = sync_release(manifest)
        except ManifestError as exc:
            raise CommandError(f"Invalid release manifest: {exc}") from None
        if entry is None:
            self.stdout.write(f"Nothing new to log for {manifest['version']}.")
        else:
            count = len(entry.changes["commits"])
            action = "Updated" if existed else "Logged"
            self.stdout.write(f"{action} {count} change(s) for {manifest['version']}.")
            if entry.missing_comparisons:
                intent = "will publish automatically when complete" if entry.publish_when_ready else "automatic publication is held"
                self.stdout.write(
                    f"Saved as draft: {len(entry.missing_comparisons)} change(s) need before/after screenshots; {intent}. "
                    "Review capture errors or add evidence in Management > Changelogs."
                )
