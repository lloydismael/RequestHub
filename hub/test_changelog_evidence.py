import json
import subprocess
import tempfile
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from PIL import Image
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from accounts.models import StoredFile
from hub.changelog_sync import sync_release
from hub.models import Changelog
from hub.release_changelog import ManifestError, build_manifest, validate_manifest
from hub.test_changelog_sync import commit, manifest
from hub.test_release_changelog import RepoMixin, git, valid_manifest


def comparison(title="Request composer", **overrides):
    data = {
        "title": title,
        "before_text": "Updates used a plain text field.",
        "after_text": "The composer now suggests eligible people to mention.",
        "before_image": "v56.4/composer-before.png",
        "after_image": "v56.4/composer-after.png",
    }
    data.update(overrides)
    return data


def visual_commit(n=1, **overrides):
    data = commit(n, "improved", "Mention-aware updates")
    data.update(
        requires_comparison=True,
        before_text="People could not be mentioned in an update.",
        after_text="Eligible stakeholders can be mentioned and notified.",
        comparisons=[comparison()],
    )
    data.update(overrides)
    return data


class EvidenceManifestTests(RepoMixin, SimpleTestCase):
    def test_validation_preserves_per_change_evidence(self):
        item = visual_commit(comparisons=[comparison(
            before_revision="b" * 40, after_revision="c" * 40,
            capture_note="Captured with synthetic data from historical revisions.",
        )])
        self.assertEqual(validate_manifest(valid_manifest(commits=[item]))["commits"], [item])

    def test_validation_rejects_unsafe_image_paths_and_incomplete_pairs(self):
        invalid = [
            {"before_image": "../secret.png"},
            {"after_image": "C:\\secret.png"},
            {"after_image": "/secret.png"},
            {"after_image": "v56.4/image.svg"},
            {"after_image": "v56.4/../secret.png"},
            {"before_text": ""},
            {"title": ""},
            {"before_image": 1},
            {"before_revision": "not a revision"},
            {"capture_note": ["not text"]},
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                item = visual_commit(comparisons=[comparison(**fields)])
                with self.assertRaises(ManifestError):
                    validate_manifest(valid_manifest(commits=[item]))
        for fields in ({"requires_comparison": "yes"}, {"comparisons": "images"}):
            with self.subTest(fields=fields):
                with self.assertRaises(ManifestError):
                    validate_manifest(valid_manifest(commits=[visual_commit(**fields)]))

    def test_missing_evidence_tolerates_malformed_stored_commits(self):
        from hub.release_changelog import missing_comparisons

        self.assertEqual(missing_comparisons([1, {}, {"requires_comparison": True}]), ["Untitled UI/UX change"])

    def test_generator_detects_ui_changes_but_not_documentation_images(self):
        from hub.test_release_changelog import commit as repo_commit

        repo = self.make_repo()
        base = repo_commit(repo, "Baseline")
        (repo / "templates").mkdir()
        (repo / "templates" / "form.html").write_text("<input>", encoding="utf-8")
        git(repo, "add", "templates")
        ui_sha = repo_commit(repo, "Improve request composer")
        (repo / "docs").mkdir()
        (repo / "docs" / "screenshot.png").write_bytes(b"documentation image")
        git(repo, "add", "docs")
        docs_sha = repo_commit(repo, "docs: add screenshot")
        result = build_manifest("v56.5", "HEAD", repo, baseline=base)
        items = {item["sha"]: item for item in result["commits"]}
        self.assertTrue(items[ui_sha].get("requires_comparison"))
        self.assertTrue(items[docs_sha].get("requires_comparison"))

    def test_generator_includes_curated_commit_evidence(self):
        from hub.test_release_changelog import commit as repo_commit

        repo = self.make_repo()
        base = repo_commit(repo, "Baseline")
        sha = repo_commit(repo, "Improve request composer")
        notes_dir = repo / "hub" / "changelog_assets" / "v56.4"
        notes_dir.mkdir(parents=True)
        item = visual_commit(
            sha=sha, parent_sha=base,
            comparisons=[comparison(before_revision=base, after_revision=sha)],
        )
        notes_dir.joinpath("manifest.json").write_text(
            json.dumps(manifest(version="v56.4", revision=sha, commits=[item])), encoding="utf-8",
        )
        result = build_manifest("v56.5", "HEAD", repo, baseline=base)
        self.assertEqual(result["commits"], [item])


class EvidenceSyncTests(TestCase):
    def setUp(self):
        Changelog.objects.all().delete()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assets = Path(tmp.name)
        folder = self.assets / "v56.4"
        folder.mkdir()
        for name, color in (("composer-before.png", "white"), ("composer-after.png", "green")):
            buffer = BytesIO()
            Image.new("RGB", (20, 10), color).save(buffer, "PNG")
            folder.joinpath(name).write_bytes(buffer.getvalue())
        patch = mock.patch("hub.changelog_sync.ASSET_DIR", self.assets, create=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_ui_release_without_screenshots_is_saved_as_draft(self):
        entry = sync_release(manifest(commits=[visual_commit(comparisons=[])]))
        self.assertFalse(entry.is_published)
        self.assertEqual(entry.changes["commits"][0]["requires_comparison"], True)

    def test_imports_images_into_persistent_media_and_is_idempotent(self):
        data = manifest(commits=[visual_commit()])
        entry = sync_release(data)
        self.assertTrue(entry.is_published)
        item = entry.changes["commits"][0]
        self.assertEqual(item["before_text"], "People could not be mentioned in an update.")
        self.assertEqual(item["after_text"], "Eligible stakeholders can be mentioned and notified.")
        pair = item.get("comparisons", [{}])[0]
        for field in ("before_image", "after_image"):
            self.assertTrue(pair.get(field, "").startswith("changelogs/"))
            saved = StoredFile.objects.get(name=pair[field])
            self.assertEqual(saved.content_type, "image/png")
            Image.open(BytesIO(saved.data)).verify()
        before_count = StoredFile.objects.count()
        self.assertIsNone(sync_release(data))
        self.assertEqual(StoredFile.objects.count(), before_count)
        self.assertEqual(Changelog.objects.count(), 1)

    def test_same_revision_enriches_existing_release_without_overwriting_admin_edits(self):
        entry = sync_release(manifest(commits=[commit(1, "improved", "Original subject")]))
        entry.title = "Admin release title"
        entry.summary = "Admin summary"
        entry.is_published = False
        entry.save()
        updated = sync_release(manifest(commits=[visual_commit()]))
        self.assertIsNotNone(updated)
        entry.refresh_from_db()
        self.assertEqual(entry.title, "Admin release title")
        self.assertEqual(entry.summary, "Admin summary")
        self.assertFalse(entry.is_published)
        self.assertEqual(entry.changes["commits"][0]["comparisons"][0]["title"], "Request composer")
        entry.changes["commits"][0]["comparisons"][0]["after_text"] = "Admin comparison description"
        entry.save()
        self.assertIsNone(sync_release(manifest(commits=[visual_commit()])))
        entry.refresh_from_db()
        self.assertEqual(entry.changes["commits"][0]["comparisons"][0]["after_text"], "Admin comparison description")
        self.assertEqual(Changelog.objects.count(), 1)

    def test_later_release_evidence_enriches_original_draft_without_relogging_its_commit(self):
        first = sync_release(manifest(version="v56.5", commits=[visual_commit(comparisons=[])]))
        second = sync_release(manifest(
            version="v56.6", revision="b" * 40, commits=[visual_commit(), commit(2, "maintenance")],
        ))
        first.refresh_from_db()
        self.assertTrue(first.changes["commits"][0].get("comparisons"))
        self.assertFalse(first.is_published)
        self.assertEqual(second.changes["commits"], [commit(2, "maintenance")])

    def test_packaged_evidence_completes_partial_admin_pairs_and_adds_new_pairs(self):
        partial = comparison(title="Admin title", before_text="Admin before", after_text="Admin after", before_image="", after_image="")
        entry = sync_release(manifest(commits=[visual_commit(comparisons=[])]))
        entry.changes["commits"][0]["comparisons"] = [partial]
        entry.save()
        data = manifest(commits=[visual_commit(comparisons=[comparison(), comparison(title="Mobile composer")])])
        self.assertIsNotNone(sync_release(data))
        entry.refresh_from_db()
        pairs = entry.changes["commits"][0]["comparisons"]
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0]["title"], "Admin title")
        self.assertEqual(pairs[0]["before_text"], "Admin before")
        self.assertEqual(pairs[0]["after_text"], "Admin after")
        self.assertTrue(pairs[0]["before_image"].startswith("changelogs/"))
        self.assertTrue(pairs[0]["after_image"].startswith("changelogs/"))
        self.assertEqual(pairs[1]["title"], "Mobile composer")
        self.assertFalse(entry.is_published)
        self.assertIsNone(sync_release(data))

    def test_newer_automatic_capture_replaces_stale_automatic_pair(self):
        entry = sync_release(manifest(commits=[visual_commit()]))
        item = entry.changes["commits"][0]
        item["comparisons"] = [{
            "title": "Repository change report",
            "before_text": "Verified file presence at the parent revision.",
            "after_text": "Verified file presence at this change.",
            "before_image": "changelogs/stale-before.png",
            "after_image": "changelogs/stale-after.png",
            "capture_note": "Automatic report capture, recipe repository-change. Exact Git snapshots.",
        }]
        entry.save()
        ui_pair = comparison(
            title="SQR dashboard portfolio and rating filters",
            capture_note="Automatic ui capture, recipe sqr-dashboard-filters. Exact Git snapshots.",
        )
        self.assertIsNotNone(sync_release(manifest(commits=[visual_commit(comparisons=[ui_pair])])))
        entry.refresh_from_db()
        pairs = entry.changes["commits"][0]["comparisons"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["title"], "SQR dashboard portfolio and rating filters")
        self.assertTrue(pairs[0]["before_image"].startswith("changelogs/"))
        self.assertNotEqual(pairs[0]["before_image"], "changelogs/stale-before.png")

    def test_newer_automatic_capture_cannot_replace_manual_evidence(self):
        entry = sync_release(manifest(commits=[visual_commit()]))
        item = entry.changes["commits"][0]
        item["comparisons"] = [{
            "title": "Hand-authored comparison",
            "before_text": "Hand before",
            "after_text": "Hand after",
            "before_image": "changelogs/hand-before.png",
            "after_image": "changelogs/hand-after.png",
            "capture_note": "Hand-authored screenshot; not automatic capture.",
        }]
        entry.save()
        ui_pair = comparison(
            title="SQR dashboard portfolio and rating filters",
            capture_note="Automatic ui capture, recipe sqr-dashboard-filters. Exact Git snapshots.",
        )
        sync_release(manifest(commits=[visual_commit(comparisons=[ui_pair])]))
        entry.refresh_from_db()
        pairs = entry.changes["commits"][0]["comparisons"]
        self.assertEqual(pairs[0]["title"], "Hand-authored comparison")
        self.assertEqual(pairs[0]["before_image"], "changelogs/hand-before.png")

    def test_older_text_only_manifest_cannot_remove_curated_descriptions_or_titles(self):
        data = manifest(commits=[visual_commit()])
        entry = sync_release(data)
        self.assertIsNone(sync_release(manifest(commits=[commit(1, "improved", "Old subject")])))
        entry.refresh_from_db()
        self.assertEqual(entry.changes["commits"][0]["title"], "Mention-aware updates")
        self.assertEqual(entry.changes["commits"][0]["before_text"], "People could not be mentioned in an update.")

    def test_missing_image_rolls_back_changelog_and_media_writes(self):
        self.assets.joinpath("v56.4", "composer-after.png").unlink()
        initial_files = StoredFile.objects.count()
        with self.assertRaises(ManifestError):
            sync_release(manifest(commits=[visual_commit()]))
        self.assertEqual(Changelog.objects.count(), 0)
        self.assertEqual(StoredFile.objects.count(), initial_files)

    def test_packaged_backfill_updates_only_matching_existing_release(self):
        data = manifest(version="v56.4", commits=[visual_commit()])
        current = sync_release(manifest(version="v56.4", commits=[commit(1, "improved")]))
        older = Changelog.objects.create(
            title="Earlier release", new_version="v56.3", summary="Keep this unchanged.",
            release_sha="b" * 40, changes={"commits": [commit(2, "fixed")]}, is_published=True,
        )
        self.assets.joinpath("v56.4", "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        output = StringIO()
        with mock.patch("hub.management.commands.sync_release_changelog.ASSET_DIR", self.assets, create=True):
            call_command("sync_release_changelog", manifest=str(self.assets / "missing.json"), stdout=output)
        current.refresh_from_db()
        older.refresh_from_db()
        self.assertTrue(current.changes["commits"][0].get("comparisons"))
        self.assertEqual(older.changes, {"commits": [commit(2, "fixed")]})
        self.assertEqual(Changelog.objects.count(), 2)


class PackagedV564EvidenceTests(SimpleTestCase):
    def test_release_metadata_and_every_declared_screenshot_are_packaged(self):
        from hub.release_changelog import ASSET_DIR, curated_manifests

        matches = [data for data in curated_manifests() if data["version"] == "v56.4"]
        self.assertEqual(len(matches), 1)
        data = matches[0]
        self.assertEqual(data["revision"], "f16b9f99be03490b9c99553070862897c9086393")
        self.assertEqual(len(data["commits"]), 9)
        visual_shas = {
            "8a08aa69ba656d8aa3fc77476fc7f63e1a8e5330",
            "2f585e30f17c038105b6138a7c978fa8222cb552",
        }
        self.assertEqual({item["sha"] for item in data["commits"] if item.get("requires_comparison")}, visual_shas)
        for item in data["commits"]:
            self.assertTrue(item.get("before_text"))
            self.assertTrue(item.get("after_text"))
            if item["sha"] in visual_shas:
                self.assertTrue(item.get("comparisons"))
            for pair in item.get("comparisons", []):
                self.assertEqual(pair.get("after_revision"), "f16b9f99be03490b9c99553070862897c9086393")
                self.assertTrue(pair.get("before_revision"))
                self.assertTrue(pair.get("capture_note"))
                paths = [ASSET_DIR.joinpath(*pair[field].split("/")) for field in ("before_image", "after_image")]
                self.assertNotEqual(paths[0].read_bytes(), paths[1].read_bytes())
                for path in paths:
                    with self.subTest(image=path.name):
                        self.assertLessEqual(path.stat().st_size, 5 * 1024 * 1024)
                        with Image.open(path) as image:
                            self.assertIn(image.format, {"PNG", "JPEG", "WEBP"})
                            image.verify()


class PackagedV566ToV568EvidenceTests(TestCase):
    def setUp(self):
        Changelog.objects.all().delete()

    def releases(self):
        from hub.release_changelog import curated_manifests

        versions = {"v56.6", "v56.7", "v56.8"}
        releases = {data["version"]: data for data in curated_manifests() if data["version"] in versions}
        self.assertEqual(set(releases), versions)
        return releases

    def test_all_three_releases_sync_in_order_without_duplicate_commits(self):
        releases = self.releases()
        expected = (
            ("v56.6", "ec2c0c7772d994c5a62f99a5b8cf27db377ce4d0", "c51ad3eb950907805607487464dc53702d048578"),
            ("v56.7", "49675f15a4d4b1d0848e18404287ffa4f8ed84f0", "3bc1f5b7f91229e93b6ded45436932a6455faf50"),
            ("v56.8", "ee2e03ae5bf7b00507b8cfcc1fa256cc7807ae06", "ecc284e1dd19e208e4e843c0ba4a6fc009dd5204"),
        )
        previous = ""
        for version, revision, sha in expected:
            with self.subTest(version=version):
                data = releases[version]
                self.assertEqual(data["revision"], revision)
                self.assertEqual([item["sha"] for item in data["commits"]], [sha])
                entry = sync_release(data)
                self.assertIsNotNone(entry)
                self.assertTrue(entry.is_published)
                self.assertEqual(entry.previous_version, previous)
                for item in entry.changes["commits"]:
                    self.assertTrue(item.get("before_text"))
                    self.assertTrue(item.get("after_text"))
                previous = version
        media_count = StoredFile.objects.count()
        for version, _revision, _sha in expected:
            self.assertIsNone(sync_release(releases[version]))
        self.assertEqual(Changelog.objects.count(), 3)
        self.assertEqual(StoredFile.objects.count(), media_count)

    def test_only_visual_release_has_complete_historical_screenshots(self):
        from hub.release_changelog import ASSET_DIR, missing_comparisons

        releases = self.releases()
        for version in ("v56.6", "v56.7"):
            for item in releases[version]["commits"]:
                self.assertFalse(item.get("requires_comparison", False))
                self.assertFalse(item.get("comparisons"))
        checkpoint = releases["v56.7"]["commits"][0]
        self.assertEqual(checkpoint["type"], "maintenance")
        self.assertIn("no application changes", checkpoint["title"].lower())
        data = releases["v56.8"]
        self.assertEqual(missing_comparisons(data["commits"]), [])
        visual = data["commits"][0]
        self.assertTrue(visual.get("requires_comparison"))
        self.assertGreaterEqual(len(visual.get("comparisons", [])), 3)
        for pair in visual["comparisons"]:
            self.assertEqual(pair.get("before_revision"), releases["v56.7"]["revision"])
            self.assertEqual(pair.get("after_revision"), data["revision"])
            self.assertTrue(pair.get("capture_note"))
            paths = [ASSET_DIR.joinpath(*pair[field].split("/")) for field in ("before_image", "after_image")]
            self.assertNotEqual(paths[0].read_bytes(), paths[1].read_bytes())
            for path in paths:
                with self.subTest(image=path.name):
                    self.assertLessEqual(path.stat().st_size, 5 * 1024 * 1024)
                    with Image.open(path) as image:
                        self.assertIn(image.format, {"PNG", "JPEG", "WEBP"})
                        image.verify()

    def test_packaged_evidence_completes_existing_draft_without_publishing(self):
        data = self.releases()["v56.8"]
        entry = sync_release({**data, "commits": [{
            "sha": data["commits"][0]["sha"], "type": "improved", "title": "Original subject",
            "requires_comparison": True,
        }]})
        entry.title = "Admin release title"
        entry.summary = "Admin summary"
        entry.save()
        self.assertIsNotNone(sync_release(data))
        entry.refresh_from_db()
        self.assertEqual(entry.title, "Admin release title")
        self.assertEqual(entry.summary, "Admin summary")
        self.assertFalse(entry.is_published)
        self.assertEqual(entry.missing_comparisons, [])
        self.assertGreaterEqual(len(entry.change_comparisons), 3)


class PackagedV571ToV576EvidenceTests(SimpleTestCase):
    def test_packaged_images_are_not_lost_in_the_ignored_capture_cache(self):
        from hub.release_changelog import ASSET_DIR, curated_manifests

        paths = [
            str(ASSET_DIR.joinpath(*pair[field].split("/")))
            for data in curated_manifests() if data["version"] in {f"v57.{patch}" for patch in range(1, 7)}
            for item in data["commits"] for pair in item["comparisons"]
            for field in ("before_image", "after_image")
        ]
        self.assertEqual(len(paths), 16)
        result = subprocess.run(
            ["git", "-C", str(ASSET_DIR.parent.parent), "check-ignore", "--no-index", "--", *paths],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_hidden_releases_have_complete_exact_revision_evidence(self):
        from hub.release_changelog import ASSET_DIR, curated_manifests, missing_comparisons

        expected = {
            "v57.1": {"bb2341fdf36cfe9518ad430765d6a6755d3e1f66"},
            "v57.2": {"15e75aaf3c2f0ebdb3625c1735871e4f5f9d3109"},
            "v57.3": {"59c4ad9545daaeb3e6435f4fdcdfdb811bb46119", "d7289df6662e4f5cf3c36833c557fa5fba9032aa"},
            "v57.4": {"0825e1f98ea94338587180a475311281ab11943b", "a85e4429da77eafbaad9eee95969a319b0a67713"},
            "v57.5": {"c7cd3e17c1e93401d199a05d839586d9fcc536bb"},
            "v57.6": {"cf93e4f66cce42c01fd6a3cf7e59fe2317991090"},
        }
        releases = {data["version"]: data for data in curated_manifests() if data["version"] in expected}
        self.assertEqual(set(releases), set(expected))
        for version, shas in expected.items():
            with self.subTest(version=version):
                data = releases[version]
                self.assertEqual({item["sha"] for item in data["commits"]}, shas)
                self.assertEqual(missing_comparisons(data["commits"], require_all=True), [])
                for item in data["commits"]:
                    self.assertTrue(item.get("before_text"))
                    self.assertTrue(item.get("after_text"))
                    for pair in item["comparisons"]:
                        self.assertEqual(pair["before_revision"], item["parent_sha"])
                        self.assertEqual(pair["after_revision"], item["sha"])
                        self.assertTrue(pair.get("capture_note"))
                        paths = [ASSET_DIR.joinpath(*pair[field].split("/")) for field in ("before_image", "after_image")]
                        self.assertNotEqual(paths[0].read_bytes(), paths[1].read_bytes())
                        for path in paths:
                            self.assertLessEqual(path.stat().st_size, 5 * 1024 * 1024)
                            with Image.open(path) as image:
                                image.verify()


class PackagedV565EvidenceTests(SimpleTestCase):
    def test_release_has_complete_packaged_evidence_for_publication(self):
        from hub.release_changelog import ASSET_DIR, curated_manifests, missing_comparisons

        matches = [data for data in curated_manifests() if data["version"] == "v56.5"]
        self.assertEqual(len(matches), 1)
        data = matches[0]
        self.assertEqual(data["revision"], "09771520305297d6ef05533d25795f6dbe5c15e7")
        self.assertEqual({item["sha"] for item in data["commits"]}, {
            "4065e1370b494e98ab0718a4e8010dceb9195baa",
            "78f0544039d269191e3a90abe201dd4b0c601d2f",
            "7f1ba896bcb4634c77fa80a456e54cd0001fcb7e",
        })
        self.assertEqual(missing_comparisons(data["commits"]), [])
        visual = next(item for item in data["commits"] if item["sha"] == "7f1ba896bcb4634c77fa80a456e54cd0001fcb7e")
        self.assertTrue(visual.get("requires_comparison"))
        self.assertTrue(visual.get("comparisons"))
        for item in data["commits"]:
            self.assertTrue(item.get("before_text"))
            self.assertTrue(item.get("after_text"))
            for pair in item.get("comparisons", []):
                self.assertEqual(pair.get("before_revision"), "f16b9f99be03490b9c99553070862897c9086393")
                self.assertEqual(pair.get("after_revision"), data["revision"])
                self.assertTrue(pair.get("capture_note"))
                paths = [ASSET_DIR.joinpath(*pair[field].split("/")) for field in ("before_image", "after_image")]
                self.assertNotEqual(paths[0].read_bytes(), paths[1].read_bytes())
                for path in paths:
                    with self.subTest(image=path.name):
                        self.assertLessEqual(path.stat().st_size, 5 * 1024 * 1024)
                        with Image.open(path) as image:
                            self.assertIn(image.format, {"PNG", "JPEG", "WEBP"})
                            image.verify()
