from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from hub.changelog_sync import load_manifest, sync_release
from hub.release_changelog import MANIFEST_PATH, ManifestError


class Command(BaseCommand):
    help = "Log the commits of the current release manifest as a changelog entry."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Path to the release manifest JSON.")

    def handle(self, *args, **options):
        path = Path(options["manifest"])
        try:
            manifest = load_manifest(path)
        except ManifestError as exc:
            raise CommandError(f"Invalid release manifest: {exc}") from None
        if manifest is None:
            self.stdout.write(f"No release manifest at {path}; nothing to sync.")
            return
        entry = sync_release(manifest)
        if entry is None:
            self.stdout.write(f"Nothing new to log for {manifest['version']}.")
        else:
            count = len(entry.changes["commits"])
            self.stdout.write(f"Logged {count} change(s) for {manifest['version']}.")
