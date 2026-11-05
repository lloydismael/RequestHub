import json
import re
from html.parser import HTMLParser

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User


class ChristmasControlsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = {}
        self.ids = set()
        self.labels = set()
        self.has_presence_marker = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "label" and attrs.get("for"):
            self.labels.add(attrs["for"])
        if tag == "input":
            if attrs.get("data-christmas-effect"):
                self.controls[attrs["data-christmas-effect"]] = attrs
            if attrs.get("name") == "christmas_preferences_present" and attrs.get("value") == "1":
                self.has_presence_marker = True


class ChristmasPreferencesTests(TestCase):
    KEYS = ("enabled", "border", "snow", "lights", "cursor", "clicks", "santa")

    def setUp(self):
        self.user = User.objects.create_user(
            username="christmas-preferences", role=User.Roles.ADMIN,
            must_change_password=False,
        )
        self.client.force_login(self.user)

    def preferences(self, client=None, url=None):
        response = (client or self.client).get(url or reverse("accounts:update"))
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<script id="christmas-effects-settings" type="application/json">(.*?)</script>',
            response.content.decode(), re.S,
        )
        self.assertIsNotNone(match, "Saved Christmas preferences must be supplied to every page")
        return json.loads(match.group(1))

    def save_preferences(self, preferences, **overrides):
        data = {
            "save_theme": "1", "banner_gradient": "christmas",
            "christmas_preferences_present": "1",
            **{f"pref_christmas_{key}": "on" for key, enabled in preferences.items() if enabled},
            **overrides,
        }
        return self.client.post(reverse("accounts:update"), data)

    def test_profile_exposes_accessible_effect_controls_and_the_save_marker(self):
        response = self.client.get(reverse("accounts:update"))
        parser = ChristmasControlsParser()
        parser.feed(response.content.decode())
        self.assertEqual(set(parser.controls), set(self.KEYS))
        self.assertTrue(parser.has_presence_marker)
        for key, control in parser.controls.items():
            with self.subTest(effect=key):
                self.assertEqual(control["type"], "checkbox")
                self.assertEqual(control["name"], f"pref_christmas_{key}")
                self.assertIn(control["id"], parser.labels)
                for description in control["aria-describedby"].split():
                    self.assertIn(description, parser.ids)

    def test_controls_display_the_saved_account_selections(self):
        expected = {key: key not in ("border", "cursor", "santa") for key in self.KEYS}
        self.save_preferences(expected)
        parser = ChristmasControlsParser()
        parser.feed(self.client.get(reverse("accounts:update")).content.decode())
        self.assertEqual(set(parser.controls), set(self.KEYS))
        for key, enabled in expected.items():
            self.assertEqual("checked" in parser.controls[key], enabled)

    def test_new_accounts_start_with_all_christmas_effects_enabled(self):
        self.assertEqual(self.preferences(), {key: True for key in self.KEYS})

    def test_each_switch_saves_independently_on_the_account(self):
        for key in self.KEYS:
            with self.subTest(effect=key):
                expected = {name: name != key for name in self.KEYS}
                response = self.save_preferences(expected)
                self.assertRedirects(response, reverse("accounts:update") + "#themes")
                self.assertEqual(self.preferences(), expected)

    def test_settings_follow_the_account_in_a_new_session_and_on_other_pages(self):
        expected = {
            "enabled": True, "border": False, "snow": True, "lights": True,
            "cursor": False, "clicks": True, "santa": False,
        }
        self.save_preferences(expected)
        second_device = Client()
        second_device.force_login(self.user)
        self.assertEqual(self.preferences(second_device, reverse("hub:dashboard")), expected)

    def test_master_off_keeps_the_individual_selections(self):
        expected = {key: key != "enabled" for key in self.KEYS}
        self.save_preferences(expected)
        self.assertEqual(self.preferences(), expected)
        expected["enabled"] = True
        self.save_preferences(expected)
        self.assertEqual(self.preferences(), expected)

    def test_unchecked_controls_can_disable_every_effect(self):
        expected = {key: False for key in self.KEYS}
        self.save_preferences(expected)
        self.assertEqual(self.preferences(), expected)

    def test_legacy_theme_post_preserves_saved_christmas_settings(self):
        expected = {key: key in ("enabled", "snow", "lights") for key in self.KEYS}
        self.save_preferences(expected)
        response = self.client.post(reverse("accounts:update"), {
            "save_theme": "1", "banner_gradient": "blue",
        })
        self.assertRedirects(response, reverse("accounts:update") + "#themes")
        self.assertEqual(self.preferences(), expected)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_theme, "blue")

    def test_invalid_legacy_theme_retry_preserves_saved_effect_choices(self):
        saved = {key: key in ("enabled", "snow", "cursor", "santa") for key in self.KEYS}
        self.save_preferences(saved)
        response = self.client.post(reverse("accounts:update"), {
            "save_theme": "1", "banner_gradient": "custom", "custom_theme_primary": "invalid",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["theme_form"].errors)
        parser = ChristmasControlsParser()
        parser.feed(response.content.decode())
        rendered = {key: "checked" in control for key, control in parser.controls.items()}
        self.assertEqual(rendered, saved)
        retried = self.save_preferences(
            rendered, banner_gradient="custom", custom_theme_primary="#2563eb",
            custom_theme_secondary="#7c3aed", custom_theme_tertiary="#06b6d4",
        )
        self.assertRedirects(retried, reverse("accounts:update") + "#themes")
        self.assertEqual(self.preferences(), saved)

    def test_invalid_theme_does_not_save_submitted_effect_settings(self):
        saved = {key: True for key in self.KEYS}
        self.save_preferences(saved)
        response = self.save_preferences({key: False for key in self.KEYS}, banner_gradient="invalid-theme")
        self.assertEqual(response.status_code, 200)
        self.assertIn("banner_gradient", response.context["theme_form"].errors)
        parser = ChristmasControlsParser()
        parser.feed(response.content.decode())
        self.assertTrue(all("checked" not in control for control in parser.controls.values()))
        self.assertEqual(self.preferences(), saved)

    def test_saving_effects_cannot_change_another_account(self):
        other = User.objects.create_user(username="other-christmas-account", role=User.Roles.ADMIN)
        expected = {key: key != "santa" for key in self.KEYS}
        self.save_preferences(expected, user=other.pk)
        other_client = Client()
        other_client.force_login(other)
        self.assertEqual(self.preferences(other_client), {key: True for key in self.KEYS})
        self.assertEqual(self.preferences(), expected)
