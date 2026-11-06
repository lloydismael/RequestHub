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
            entry = sync_release(manifest)
        except ManifestError as exc:
            raise CommandError(f"Invalid release manifest: {exc}") from None
        if entry is None:
            self.stdout.write(f"Nothing new to log for {manifest['version']}.")
        else:
            count = len(entry.changes["commits"])
            self.stdout.write(f"Logged {count} change(s) for {manifest['version']}.")
            if entry.missing_comparisons:
                self.stdout.write("Saved as draft: add before and after screenshots in Management > Changelogs.")
