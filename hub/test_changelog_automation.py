import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.conf import settings
from django import forms
from django.urls import reverse
from PIL import Image

from accounts.models import User
from hub.changelog_forms import ChangelogForm
from hub.changelog_sync import sync_release
from hub.models import Changelog


def release(*, evidence=False, error=None):
    item = {
        "sha": "1" * 40, "parent_sha": "2" * 40, "type": "maintenance",
        "title": "Update release checks", "requires_comparison": False,
    }
    if error:
        item["capture_error"] = error
    if evidence:
        item["comparisons"] = [{
            "title": "Release checks", "before_text": "Previous synthetic result.",
            "after_text": "Updated synthetic result.",
            "before_image": "v1/before.png", "after_image": "v1/after.png",
            "before_revision": "2" * 40, "after_revision": "1" * 40,
            "capture_note": "Captured from synthetic report fixtures.",
        }]
    return {
        "version": "v1", "revision": "3" * 40,
        "generated_at": "2026-11-06T08:00:00+00:00",
        "evidence_policy": "all_changes", "commits": [item],
    }


class AutomaticEvidenceTests(TestCase):
    def setUp(self):
        Changelog.objects.all().delete()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.assets = Path(temporary.name)
        folder = self.assets / "v1"
        folder.mkdir()
        for name, color in (("before.png", "white"), ("after.png", "green")):
            Image.new("RGB", (30, 20), color).save(folder / name)
        patch = mock.patch("hub.changelog_sync.ASSET_DIR", self.assets)
        patch.start()
        self.addCleanup(patch.stop)

    def test_nonvisual_change_waits_for_screenshots_and_remembers_auto_publication(self):
        entry = sync_release(release(error="No capture recipe configured."))
        self.assertFalse(entry.is_published)
        self.assertEqual(entry.changes.get("evidence_policy"), "all_changes")
        self.assertTrue(getattr(entry, "publish_when_ready", False))
        self.assertEqual(entry.missing_comparisons, ["Update release checks"])

    def test_retry_publishes_original_system_draft_when_evidence_arrives(self):
        entry = sync_release(release(error="No capture recipe configured."))
        updated = sync_release(release(evidence=True))
        entry.refresh_from_db()
        self.assertIsNotNone(updated)
        self.assertTrue(entry.is_published)
        self.assertNotIn("capture_error", entry.changes["commits"][0])
        self.assertEqual(entry.missing_comparisons, [])
        self.assertEqual(Changelog.objects.count(), 1)
        self.assertIsNone(sync_release(release(evidence=True)))

    def test_admin_hold_and_edits_survive_evidence_retry(self):
        entry = sync_release(release())
        entry.publish_when_ready = False
        entry.title = "Admin title"
        entry.summary = "Admin summary"
        entry.save()
        sync_release(release(evidence=True))
        entry.refresh_from_db()
        self.assertFalse(entry.is_published)
        self.assertEqual(entry.title, "Admin title")
        self.assertEqual(entry.summary, "Admin summary")

    def test_model_rejects_publication_even_when_commit_flag_is_tampered_with(self):
        entry = sync_release(release())
        entry.changes["evidence_policy"] = "all_changes"
        entry.changes["commits"][0]["requires_comparison"] = False
        entry.previous_version = "v0"
        entry.is_published = True
        with self.assertRaises(ValidationError) as caught:
            entry.full_clean()
        self.assertIn("is_published", caught.exception.message_dict)

    def test_empty_or_malformed_change_list_cannot_be_published(self):
        entry = sync_release(release())
        entry.previous_version = "v0"
        entry.is_published = True
        for commits in ([], {}, [1]):
            with self.subTest(commits=commits):
                entry.changes["commits"] = commits
                with self.assertRaises(ValidationError) as caught:
                    entry.full_clean()
                self.assertIn("is_published", caught.exception.message_dict)

    def test_unrelated_database_failure_is_not_reported_as_nothing_new(self):
        with mock.patch.object(Changelog.objects, "create", side_effect=IntegrityError("simulated database failure")):
            with self.assertRaises(IntegrityError):
                sync_release(release())

    def test_manual_recovery_uses_exact_revisions_and_valid_images(self):
        entry = sync_release(release(error="No capture recipe configured."))
        data = {
            "title": entry.title, "previous_version": "", "new_version": "v1",
            "release_date": entry.release_date.isoformat(), "summary": entry.summary,
            "publish_when_ready": "on",
            "comparison_0_0_title": "Actual report",
            "comparison_0_0_before_text": "Before result",
            "comparison_0_0_after_text": "After result",
            "comparison_0_0_before_revision": "2" * 40,
            "comparison_0_0_after_revision": "1" * 40,
        }
        files = {
            f"comparison_0_0_{field}_image": SimpleUploadedFile(
                f"{field}.png", (self.assets / "v1" / f"{field}.png").read_bytes(), content_type="image/png",
            )
            for field in ("before", "after")
        }
        form = ChangelogForm(data, files, instance=entry)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form.save_comparisons()
        entry.refresh_from_db()
        self.assertTrue(entry.is_published)
        self.assertNotIn("capture_error", entry.changes["commits"][0])
        pair = entry.changes["commits"][0]["comparisons"][0]
        self.assertEqual(pair["before_revision"], "2" * 40)
        self.assertEqual(pair["after_revision"], "1" * 40)
        self.assertIn("manually", pair["capture_note"])

    def test_editor_exposes_exact_revisions_and_capture_error(self):
        entry = sync_release(release(error="No capture recipe configured."))
        form = ChangelogForm(instance=entry)
        self.assertIn("publish_when_ready", form.fields)
        self.assertIn("comparison_0_0_before_revision", form.fields)
        self.assertEqual(form.fields["comparison_0_0_before_revision"].initial, "2" * 40)
        self.assertEqual(form.fields["comparison_0_0_after_revision"].initial, "1" * 40)
        self.assertEqual(form.comparison_groups[0].get("capture_error"), "No capture recipe configured.")

    def test_clearing_publish_also_disables_later_automatic_republication(self):
        entry = sync_release(release(evidence=True))
        data = {
            "title": entry.title, "previous_version": "", "new_version": "v1",
            "release_date": entry.release_date.isoformat(), "summary": entry.summary,
            "publish_when_ready": "on",
        }
        form = ChangelogForm(data, instance=entry)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form.save_comparisons()
        entry.refresh_from_db()
        self.assertFalse(entry.is_published)
        self.assertFalse(entry.publish_when_ready)

    def test_command_reports_draft_reason_and_evidence_retry_as_update(self):
        path = self.assets / "manifest.json"
        path.write_text(json.dumps(release(error="No capture recipe configured.")), encoding="utf-8")
        output = StringIO()
        call_command("sync_release_changelog", manifest=str(path), stdout=output)
        self.assertIn("1 change(s) need before/after screenshots", output.getvalue())
        self.assertIn("will publish automatically when complete", output.getvalue())
        path.write_text(json.dumps(release(evidence=True)), encoding="utf-8")
        output = StringIO()
        call_command("sync_release_changelog", manifest=str(path), stdout=output)
        self.assertIn("Updated 1 change(s)", output.getvalue())
        self.assertNotIn("Saved as draft", output.getvalue())

    def test_editor_rejects_wrong_revision_even_with_existing_valid_images(self):
        entry = sync_release(release(evidence=True))
        form = ChangelogForm({
            "title": entry.title, "previous_version": "", "new_version": "v1",
            "release_date": entry.release_date.isoformat(), "summary": entry.summary, "is_published": "on",
            "comparison_0_0_before_revision": "a" * 40,
        }, instance=entry)
        self.assertFalse(form.is_valid())
        self.assertIn("comparison_0_0_before_revision", form.errors)

    def test_management_editor_shows_actionable_capture_error_and_publication_control(self):
        entry = sync_release(release(error="No recipe configured. <script>synthetic</script>"))
        admin = User.objects.create_user(username="capture-test-admin", role=User.Roles.ADMIN, must_change_password=False)
        self.client.force_login(admin)
        response = self.client.get(reverse("hub:changelog-edit", args=[entry.pk]))
        self.assertContains(response, "Automatic capture needs attention")
        self.assertContains(response, "No recipe configured. &lt;script&gt;synthetic&lt;/script&gt;")
        self.assertContains(response, 'name="publish_when_ready"')
        self.assertContains(response, 'name="comparison_0_0_before_revision"')

    def test_large_draft_editor_stays_within_submission_limit_and_preserves_all_changes(self):
        manifest = release()
        original = manifest["commits"][0]
        manifest["commits"] = [{**original, "sha": f"{index + 100:040x}"} for index in range(200)]
        entry = sync_release(manifest)
        last = dict(entry.changes["commits"][-1])
        form = ChangelogForm(instance=entry)
        data = {
            name: form[name].value() or ""
            for name, field in form.fields.items()
            if not isinstance(field, (forms.FileField, forms.BooleanField))
        }
        self.assertLessEqual(len(data), settings.DATA_UPLOAD_MAX_NUMBER_FIELDS)
        admin = User.objects.create_user(username="large-capture-admin", role=User.Roles.ADMIN, must_change_password=False)
        self.client.force_login(admin)
        response = self.client.post(reverse("hub:changelog-edit", args=[entry.pk]), data)
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(len(entry.changes["commits"]), 200)
        self.assertEqual(entry.changes["commits"][-1], last)
        self.assertFalse(entry.publish_when_ready)
        self.assertFalse(entry.is_published)

    def test_comparison_page_updates_only_its_changes(self):
        manifest = release(evidence=True)
        manifest["commits"].extend([
            {**release()["commits"][0], "sha": f"{index:040x}"} for index in (100, 101)
        ])
        entry = sync_release(manifest)
        original = dict(entry.changes["commits"][0])
        form = ChangelogForm({
            "title": entry.title, "previous_version": "", "new_version": "v1",
            "release_date": entry.release_date.isoformat(), "summary": entry.summary,
        }, instance=entry, comparison_page=2)
        self.assertNotIn("comparison_0_0_title", form.fields)
        self.assertIn("comparison_2_0_title", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form.save_comparisons()
        entry.refresh_from_db()
        self.assertEqual(entry.changes["commits"][0], original)
        self.assertEqual(len(entry.changes["commits"]), 3)
        admin = User.objects.create_user(username="paged-capture-admin", role=User.Roles.ADMIN, must_change_password=False)
        self.client.force_login(admin)
        response = self.client.get(reverse("hub:changelog-edit", args=[entry.pk]), {"comparison_page": 2})
        self.assertContains(response, 'name="comparison_2_0_title"')
        self.assertNotContains(response, 'name="comparison_0_0_title"')
        self.assertContains(response, "?comparison_page=2")

    def test_maximum_comparisons_per_page_respect_file_upload_limit(self):
        manifest = release(evidence=True)
        source = manifest["commits"][0]
        commits = []
        for index in range(5):
            sha = f"{index + 100:040x}"
            commits.append({
                **source, "sha": sha,
                "comparisons": [
                    {**source["comparisons"][0], "title": f"Pair {pair}", "after_revision": sha}
                    for pair in range(20)
                ],
            })
        manifest["commits"] = commits
        form = ChangelogForm(instance=sync_release(manifest))
        files = sum(isinstance(field, forms.FileField) for field in form.fields.values())
        self.assertLessEqual(files, settings.DATA_UPLOAD_MAX_NUMBER_FILES)
        self.assertLessEqual(len(form.fields) + files, settings.DATA_UPLOAD_MAX_NUMBER_FIELDS)
        self.assertEqual(len(form.comparison_groups), 2)

    def test_publishing_checks_missing_evidence_on_other_pages(self):
        manifest = release()
        manifest["commits"][0]["title"] = "Missing evidence on another page"
        for index in (100, 101):
            source = release(evidence=True)["commits"][0]
            sha = f"{index:040x}"
            manifest["commits"].append({
                **source, "sha": sha,
                "comparisons": [{**source["comparisons"][0], "after_revision": sha}],
            })
        entry = sync_release(manifest)
        form = ChangelogForm({
            "title": entry.title, "previous_version": "", "new_version": "v1",
            "release_date": entry.release_date.isoformat(), "summary": entry.summary, "is_published": "on",
        }, instance=entry, comparison_page=2)
        self.assertFalse(form.is_valid())
        self.assertIn("Missing evidence on another page", str(form.non_field_errors()))
