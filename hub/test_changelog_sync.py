import json
import tempfile
from datetime import date
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.changelog_sync import sync_release
from hub.models import Changelog


def commit(n, kind, title=None):
    return {"sha": f"{n:040x}", "type": kind, "title": title or f"Change {n}"}


def manifest(version="v56.0", revision="a" * 40, generated_at="2026-11-04T10:00:00+00:00", commits=()):
    return {"version": version, "revision": revision, "generated_at": generated_at, "commits": list(commits)}


class SyncReleaseTests(TestCase):
    def setUp(self):
        # Release entries seeded by migrations are cleared so each test controls the feed.
        Changelog.objects.all().delete()

    def manual(self, version, release_date, **extra):
        return Changelog.objects.create(
            title=f"Manual {version}", previous_version="", new_version=version,
            release_date=release_date, summary="Manual entry.", is_published=True, **extra,
        )

    def test_creates_one_published_entry(self):
        commits = [commit(1, "new"), commit(2, "new"), commit(3, "improved"), commit(4, "fixed")]
        entry = sync_release(manifest(commits=commits))
        self.assertEqual(entry.title, "What's new in v56.0")
        self.assertEqual(entry.new_version, "v56.0")
        self.assertEqual(entry.previous_version, "")
        self.assertTrue(entry.is_published)
        self.assertEqual(entry.release_sha, "a" * 40)
        self.assertEqual(entry.changes, {"commits": commits})
        self.assertEqual(entry.summary, "This release includes 2 new, 1 improved and 1 fixed changes.")

    def test_summary_wording(self):
        cases = [
            ([commit(1, "improved")], "This release includes 1 improved change."),
            ([commit(2, "new"), commit(3, "fixed")], "This release includes 1 new and 1 fixed changes."),
            ([commit(4, "maintenance"), commit(5, "maintenance"), commit(6, "maintenance")],
             "This release includes 3 maintenance changes."),
        ]
        for index, (commits, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                entry = sync_release(manifest(revision=f"{index + 1:040x}", version=f"v6{index}.0", commits=commits))
                self.assertEqual(entry.summary, expected)

    def test_same_revision_again_creates_nothing(self):
        data = manifest(commits=[commit(1, "new")])
        self.assertIsNotNone(sync_release(data))
        self.assertIsNone(sync_release(data))
        self.assertIsNone(sync_release(manifest(version="v56.1", commits=[commit(1, "new"), commit(2, "fixed")])))
        self.assertEqual(Changelog.objects.count(), 1)

    def test_new_revision_logs_only_unlogged_commits(self):
        first = [commit(1, "new"), commit(2, "fixed")]
        sync_release(manifest(commits=first))
        second = sync_release(manifest(version="v56.1", revision="b" * 40, commits=first + [commit(3, "improved")]))
        self.assertEqual(second.changes, {"commits": [commit(3, "improved")]})
        self.assertEqual(second.previous_version, "v56.0")

    def test_rollback_creates_nothing(self):
        newer = [commit(1, "new"), commit(2, "fixed")]
        sync_release(manifest(version="v56.1", revision="b" * 40, commits=newer))
        self.assertIsNone(sync_release(manifest(version="v56.0", revision="a" * 40, commits=newer[:1])))
        self.assertIsNone(sync_release(manifest(version="v56.0", revision="c" * 40, commits=[])))
        self.assertEqual(Changelog.objects.count(), 1)

    def test_unpublished_entry_commits_are_not_relogged(self):
        first = sync_release(manifest(commits=[commit(1, "new")]))
        first.is_published = False
        first.save()
        later = sync_release(manifest(version="v56.1", revision="b" * 40, commits=[commit(1, "new"), commit(2, "fixed")]))
        self.assertEqual(later.changes, {"commits": [commit(2, "fixed")]})

    def test_duplicate_shas_in_manifest_collapse(self):
        entry = sync_release(manifest(commits=[commit(1, "new"), commit(1, "new"), commit(2, "fixed")]))
        self.assertEqual(entry.changes, {"commits": [commit(1, "new"), commit(2, "fixed")]})

    def test_malformed_stored_changes_do_not_raise(self):
        for index, changes in enumerate([[], {"commits": "x"}, {"commits": [1, {"sha": 5}, {}]}]):
            self.manual(f"v1.{index}", date(2026, 1, 1), release_sha=f"{index + 1:040x}", changes=changes)
        entry = sync_release(manifest(commits=[commit(1, "new")]))
        self.assertEqual(entry.changes, {"commits": [commit(1, "new")]})

    def test_racing_insert_is_swallowed(self):
        Changelog.objects.create(
            title="Racer", previous_version="", new_version="v56.0", summary="x",
            release_sha="a" * 40, changes={},
        )
        with mock.patch("hub.changelog_sync._already_synced", return_value=False):
            self.assertIsNone(sync_release(manifest(commits=[commit(1, "new")])))
        self.assertEqual(Changelog.objects.filter(release_sha="a" * 40).count(), 1)

    def test_previous_version_rules(self):
        self.manual("v55.1", date(2026, 11, 1))
        self.manual("v99.0", date(2030, 1, 1))
        self.manual("v56.0", date(2026, 11, 3))
        entry = sync_release(manifest(commits=[commit(1, "new")]))
        self.assertEqual(entry.previous_version, "v55.1")

    def test_release_date_uses_manila_day(self):
        late = sync_release(manifest(generated_at="2026-11-04T17:30:00+00:00", commits=[commit(1, "new")]))
        self.assertEqual(late.release_date, date(2026, 11, 5))
        early = sync_release(manifest(
            version="v56.1", revision="b" * 40, generated_at="2026-11-04T15:59:00+00:00", commits=[commit(2, "new")],
        ))
        self.assertEqual(early.release_date, date(2026, 11, 4))


class SyncCommandTests(TestCase):
    def setUp(self):
        Changelog.objects.all().delete()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "manifest.json"

    def run_command(self):
        out = StringIO()
        call_command("sync_release_changelog", "--manifest", str(self.path), stdout=out)
        return out.getvalue().strip()

    def test_command_without_manifest_exits_cleanly(self):
        self.assertEqual(self.run_command(), f"No release manifest at {self.path}; nothing to sync.")
        self.assertEqual(Changelog.objects.count(), 0)

    def test_command_rejects_invalid_manifest(self):
        bad_sha = manifest(commits=[commit(1, "new")])
        bad_sha["commits"][0]["sha"] = "a" * 39
        for content in ("{not json", json.dumps(bad_sha)):
            with self.subTest(content=content[:12]):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaisesMessage(CommandError, "Invalid release manifest"):
                    self.run_command()
        self.path.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaisesMessage(CommandError, "Invalid release manifest"):
            self.run_command()
        self.assertEqual(Changelog.objects.count(), 0)

    def test_command_reports_counts(self):
        self.path.write_text(json.dumps(manifest(commits=[commit(1, "new"), commit(2, "fixed")])), encoding="utf-8")
        self.assertEqual(self.run_command(), "Logged 2 change(s) for v56.0.")
        self.assertEqual(self.run_command(), "Nothing new to log for v56.0.")
