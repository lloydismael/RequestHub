from datetime import timedelta
from io import BytesIO

from PIL import Image
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import StoredFile, User


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

    def test_management_has_changelogs_tab(self):
        response = self.client.get(self.management, {"tab": "changelogs"})
        self.assertContains(response, 'id="changelogs-tab"')
        self.assertEqual(response.context["active_tab"], "changelogs")

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
