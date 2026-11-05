import re
from datetime import timedelta
from io import BytesIO

from PIL import Image
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import StoredFile, User
from hub.release_changelog import GROUPS


def screenshot(name="screen.png"):
    buffer = BytesIO()
    Image.new("RGB", (20, 10), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ChangelogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="release-admin", role=User.Roles.ADMIN, must_change_password=False,
        )
        cls.reader = User.objects.create_user(
            username="release-reader", role=User.Roles.ENGINEER, must_change_password=False,
        )

    def setUp(self):
        # Release entries seeded by migrations are cleared so each test controls the feed.
        self.model().objects.all().delete()
        self.client.force_login(self.admin)
        self.management = reverse("hub:management")
        self.notifications = reverse("hub:notifications")

    def model(self):
        return apps.get_model("hub", "Changelog")

    def payload(self, **overrides):
        data = {
            "title": "Visual release history",
            "previous_version": "v1", "new_version": "v2",
            "release_date": timezone.localdate().isoformat(),
            "summary": "See what changed.",
            "before_text": "Only request activity was visible.",
            "after_text": "Release comparisons are available.",
        }
        data.update(overrides)
        return data

    def entry(self, **overrides):
        data = self.payload()
        data.update(overrides)
        return self.model().objects.create(**data)

    def automatic(self, **overrides):
        commits = [
            {"sha": "1" * 40, "type": "new", "title": "Shiny export"},
            {"sha": "2" * 40, "type": "improved", "title": "Quicker list"},
            {"sha": "3" * 40, "type": "fixed", "title": "Typo fix"},
            {"sha": "4" * 40, "type": "maintenance", "title": "Dependency bump"},
        ]
        data = self.payload(
            title="What's new in v56.0", previous_version="", new_version="v56.0",
            before_text="", after_text="",
        )
        data.update(release_sha="c" * 40, changes={"commits": commits}, is_published=True)
        data.update(overrides)
        return self.model().objects.create(**data)

    def test_release_sha_is_unique_but_nullable(self):
        self.entry(release_sha=None)
        self.entry(release_sha=None)
        self.automatic(release_sha="a" * 40)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.automatic(release_sha="a" * 40)

    def test_is_automatic_and_has_comparison(self):
        manual = self.entry()
        self.assertFalse(manual.is_automatic)
        self.assertTrue(manual.has_comparison)
        auto = self.automatic()
        self.assertTrue(auto.is_automatic)
        self.assertFalse(auto.has_comparison)
        auto.after_text = "Now with exports."
        self.assertTrue(auto.has_comparison)

    def test_change_groups(self):
        commits = [
            {"sha": "1" * 40, "type": "maintenance", "title": "Bump"},
            {"sha": "2" * 40, "type": "fixed", "title": "Fix"},
            {"sha": "3" * 40, "type": "new", "title": "First new"},
            {"sha": "4" * 40, "type": "new", "title": "Second new"},
            {"sha": "5" * 40, "type": "improved", "title": "Better"},
        ]
        groups = self.automatic(changes={"commits": commits}).change_groups
        self.assertEqual([g["key"] for g in groups], ["new", "improved", "fixed", "maintenance"])
        self.assertEqual([g["label"] for g in groups], [label for _, label in GROUPS])
        self.assertEqual(groups[0]["titles"], ["First new", "Second new"])

    def test_change_groups_tolerates_malformed_changes(self):
        for changes in (
            {}, [], {"commits": "x"},
            {"commits": [1, {"type": "zzz", "title": "t"}, {"type": "new"}]},
            {"commits": [{"type": ["new"], "title": "t"}, {"type": {"a": 1}, "title": "t"}]},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.automatic(changes=changes, release_sha=None).change_groups, [])

    def test_changes_column_has_database_default(self):
        with connection.cursor() as cursor:
            columns = {
                c.name: c
                    for c in connection.introspection.get_table_description(cursor, self.model()._meta.db_table)
            }
        default = columns["changes"].default
        self.assertIsNotNone(default)
        self.assertIn("{}", default)

    def feed(self):
        return self.client.get(self.notifications, {"tab": "changelogs"})

    def test_automatic_entry_lists_changes_by_group(self):
        self.automatic()
        response = self.feed()
        self.assertContains(response, "What changed")
        for title in ("Shiny export", "Quicker list", "Typo fix", "Dependency bump"):
            self.assertContains(response, title)
        self.assertContains(response, '<details class="cl-change-group">')
        self.assertNotContains(response, '<details class="cl-change-group" open')
        self.assertNotContains(response, "cl-comparison")
        self.assertNotContains(response, "bi-arrow-right")
        self.assertContains(response, "v56.0")

    def test_change_titles_are_escaped(self):
        title = "<script>alert(1)</script>"
        commits = [{"sha": "1" * 40, "type": "new", "title": title}]
        self.automatic(changes={"commits": commits})
        response = self.feed()
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, title)

    def test_long_unbroken_title_renders_whole(self):
        title = "x" * 200
        self.automatic(changes={"commits": [{"sha": "1" * 40, "type": "fixed", "title": title}]})
        self.assertContains(self.feed(), title)

    def test_manual_entry_is_unchanged(self):
        self.entry(is_published=True)
        response = self.feed()
        self.assertContains(response, "cl-comparison")
        self.assertContains(response, "bi-arrow-right")
        self.assertNotContains(response, "What changed")

    def test_changes_and_comparison_coexist(self):
        self.automatic(
            previous_version="v55.1", before_text="Old way.", after_text="New way.",
        )
        response = self.feed()
        self.assertContains(response, "What changed")
        self.assertContains(response, "cl-comparison")

    def test_management_has_changelogs_tab(self):
        response = self.client.get(self.management, {"tab": "changelogs"})
        self.assertContains(response, 'id="changelogs-tab"')
        self.assertEqual(response.context["active_tab"], "changelogs")

    def test_changelogs_link_exposes_pane_target_without_intercepting_navigation(self):
        for tab in ("changelogs", "activity"):
            with self.subTest(tab=tab):
                response = self.client.get(self.management, {"tab": tab})
                match = re.search(r'<a\b[^>]*\bid="changelogs-tab"[^>]*>', response.content.decode())
                self.assertIsNotNone(match)
                link = match.group()
                self.assertIn('data-bs-target="#changelogs-pane"', link)
                self.assertIn(f'href="{self.management}?tab=changelogs"', link)
                self.assertNotIn("data-bs-toggle", link)

    def test_admin_can_save_edit_publish_and_unpublish(self):
        url = reverse("hub:changelog-create")
        response = self.client.post(url, self.payload())
        self.assertRedirects(response, self.management + "?tab=changelogs")
        entry = self.model().objects.get()
        self.assertFalse(entry.is_published)
        edit = reverse("hub:changelog-edit", args=[entry.pk])
        response = self.client.post(edit, self.payload(
            title="Updated release", is_published="on",
            before_image=screenshot(), after_image=screenshot(),
        ))
        self.assertRedirects(response, self.management + "?tab=changelogs")
        entry.refresh_from_db()
        self.assertTrue(entry.is_published)
        self.assertEqual(entry.title, "Updated release")
        image_name = entry.before_image.name
        response = self.client.post(edit, self.payload())
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertFalse(entry.is_published)
        self.assertEqual(entry.before_image.name, image_name)

    def test_non_admin_cannot_create_or_edit(self):
        entry = self.entry()
        self.client.force_login(self.reader)
        for url in (reverse("hub:changelog-create"), reverse("hub:changelog-edit", args=[entry.pk])):
            for method in (self.client.get, self.client.post):
                self.assertEqual(method(url, self.payload()).status_code, 403)
        self.assertEqual(self.model().objects.count(), 1)

    def test_publishing_requires_both_texts_and_screenshots(self):
        response = self.client.post(reverse("hub:changelog-create"), self.payload(
            is_published="on", before_text="", after_text="",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.context["changelog_form"].errors),
            {"before_text", "after_text", "before_image", "after_image"},
        )
        self.assertFalse(self.model().objects.exists())

    def test_invalid_images_and_equal_versions_are_rejected(self):
        response = self.client.post(reverse("hub:changelog-create"), self.payload(
            new_version="v1",
            before_image=SimpleUploadedFile("bad.png", b"not an image", content_type="image/png"),
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn("before_image", response.context["changelog_form"].errors)
        self.assertIn("new_version", response.context["changelog_form"].errors)
        self.assertFalse(self.model().objects.exists())

    def test_size_limit_and_non_screenshot_formats(self):
        from .changelog_forms import ChangelogForm

        oversized = screenshot()
        oversized.size = 5 * 1024 * 1024 + 1
        form = ChangelogForm(self.payload(), {"before_image": oversized})
        self.assertFalse(form.is_valid())
        self.assertIn("5 MB", str(form.errors["before_image"]))
        buffer = BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, "GIF")
        form = ChangelogForm(self.payload(), {
            "before_image": SimpleUploadedFile("image.gif", buffer.getvalue()),
        })
        self.assertFalse(form.is_valid())
        self.assertIn("PNG, JPEG or WebP", str(form.errors["before_image"]))

    def test_public_feed_excludes_drafts_and_future_releases(self):
        self.entry(title="Private draft")
        self.entry(title="Future release", is_published=True, release_date=timezone.localdate() + timedelta(days=1))
        older = self.entry(title="Earlier update", is_published=True, release_date=timezone.localdate() - timedelta(days=1))
        newest = self.entry(title="<script>alert(1)</script>", is_published=True)
        self.client.force_login(self.reader)
        response = self.client.get(self.notifications, {"tab": "changelogs"})
        self.assertEqual(list(response.context["changelogs"]), [newest, older])
        self.assertNotContains(response, "Private draft")
        self.assertNotContains(response, "Future release")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertContains(response, "Only request activity was visible.")
        self.assertContains(response, "Release comparisons are available.")
        self.assertEqual(response.context["unread_count"], 0)

    def test_screenshots_follow_visibility_and_disable_caching(self):
        entry = self.entry(before_image=screenshot(), after_image=screenshot())
        url = entry.before_image.url
        self.assertTrue(StoredFile.objects.filter(name=entry.before_image.name).exists())
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        entry.is_published = True
        entry.save()
        self.client.force_login(self.reader)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("no-store", response["Cache-Control"])
        entry.is_published = False
        entry.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_automatic_entry_saves_published_without_previous_version_or_comparison(self):
        entry = self.automatic()
        response = self.client.post(
            reverse("hub:changelog-edit", args=[entry.pk]),
            self.payload(
                previous_version="", before_text="", after_text="", title="Edited", is_published="on",
            ),
        )
        self.assertEqual(response.status_code, 302)
        saved = self.model().objects.get(pk=entry.pk)
        self.assertEqual(saved.title, "Edited")
        self.assertTrue(saved.is_published)
        self.assertEqual(saved.release_sha, "c" * 40)
        self.assertEqual(saved.changes, entry.changes)

    def test_partial_comparison_on_automatic_entry_is_rejected(self):
        entry = self.automatic()
        response = self.client.post(
            reverse("hub:changelog-edit", args=[entry.pk]),
            self.payload(previous_version="", before_text="x", after_text="", is_published="on"),
        )
        self.assertEqual(response.status_code, 200)
        errors = response.context["changelog_form"].errors
        for name in ("after_text", "before_image", "after_image"):
            self.assertEqual(errors[name], ["This field is required before publishing."])

    def test_new_manual_entry_still_requires_previous_version(self):
        response = self.client.post(
            reverse("hub:changelog-create"), self.payload(previous_version=""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")

    def test_management_marks_automatic_entries(self):
        automatic = self.automatic()
        manual = self.entry()
        badge = '<span class="badge text-bg-info">Automatic</span>'
        response = self.client.get(self.management, {"tab": "changelogs"})
        self.assertContains(response, badge, count=1)
        self.assertContains(response, "v1 &rarr; v2")
        self.assertNotContains(response, "&rarr; v56.0")
        response = self.client.get(reverse("hub:changelog-edit", args=[automatic.pk]))
        self.assertContains(response, "Automatic entries can be published as they are.")
        self.assertNotContains(response, "Publishing requires both descriptions and screenshots.")
        self.assertNotContains(response, "Required for publishing.")
        response = self.client.get(reverse("hub:changelog-edit", args=[manual.pk]))
        self.assertContains(response, "Publishing requires both descriptions and screenshots.")
        self.assertNotContains(response, "Automatic entries can be published as they are.")
        self.assertContains(response, "Required for publishing.")

    def test_feed_pagination_preserves_tab_and_empty_state(self):
        self.client.force_login(self.reader)
        response = self.client.get(self.notifications, {"tab": "changelogs"})
        self.assertContains(response, "No changelogs published yet")
        for index in range(11):
            self.entry(title=f"Release {index}", is_published=True)
        response = self.client.get(self.notifications, {"tab": "changelogs"})
        self.assertEqual(len(response.context["changelogs"]), 10)
        self.assertContains(response, "?tab=changelogs&amp;page=2")
        response = self.client.get(self.notifications, {"tab": "changelogs", "page": 2})
        self.assertEqual(len(response.context["changelogs"]), 1)
        self.assertContains(response, "Release 0")

    def test_notification_tab_preserves_unread_feed(self):
        from .models import Notification

        Notification.objects.create(recipient=self.reader, message="Unread activity")
        Notification.objects.create(recipient=self.reader, message="Read activity", is_read=True)
        self.client.force_login(self.reader)
        response = self.client.get(self.notifications)
        self.assertContains(response, "Unread activity")
        self.assertNotContains(response, "Read activity")
        self.assertEqual(response.context["unread_count"], 1)
        self.assertEqual(response.context["total_count"], 2)


class ReleaseChangelogTests(TestCase):
    def test_v55_1_release_is_logged_with_descriptions_and_screenshots(self):
        reader = User.objects.create_user(
            username="release-viewer", role=User.Roles.ENGINEER, must_change_password=False,
        )
        self.client.force_login(reader)
        entries = apps.get_model("hub", "Changelog").objects.filter(previous_version="v55.0", new_version="v55.1")
        self.assertEqual(entries.count(), 2)
        response = self.client.get(reverse("hub:notifications"), {"tab": "changelogs"})
        self.assertNotContains(response, "No changelogs published yet")
        for entry in entries:
            self.assertTrue(entry.is_published)
            self.assertTrue(entry.summary and entry.before_text and entry.after_text)
            self.assertContains(response, entry.title)
            for image in (entry.before_image, entry.after_image):
                self.assertContains(response, image.url)
                image_response = self.client.get(image.url)
                self.assertEqual(image_response.status_code, 200)
                self.assertEqual(image_response["Content-Type"], "image/jpeg")
                Image.open(BytesIO(image_response.content)).verify()
