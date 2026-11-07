import json
import re
import subprocess
from datetime import date, datetime, timezone as datetime_timezone
from html.parser import HTMLParser
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User


class InlineScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self._current_script = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._current_script = (dict(attrs), [])

    def handle_data(self, data):
        if self._current_script is not None:
            self._current_script[1].append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._current_script is not None:
            attrs, data = self._current_script
            self.scripts.append((attrs, "".join(data)))
            self._current_script = None


class InlineScriptParserTests(SimpleTestCase):
    def test_handles_whitespace_before_script_closing_angle_bracket(self):
        parser = InlineScriptParser()
        parser.feed("<script>run()</script >")

        self.assertEqual(parser.scripts, [({}, "run()")])


class ChristmasDefaultThemeTests(SimpleTestCase):
    def test_default_window_uses_manila_dates_and_does_not_repeat_next_year(self):
        cases = (
            (datetime(2026, 11, 5, 15, 59, tzinfo=datetime_timezone.utc), "blue"),
            (datetime(2026, 11, 5, 16, 0, tzinfo=datetime_timezone.utc), "christmas"),
            (datetime(2026, 12, 31, 15, 59, tzinfo=datetime_timezone.utc), "christmas"),
            (datetime(2026, 12, 31, 16, 0, tzinfo=datetime_timezone.utc), "blue"),
            (datetime(2027, 12, 1, tzinfo=datetime_timezone.utc), "blue"),
        )
        for moment, expected in cases:
            with self.subTest(moment=moment), timezone.override("Asia/Manila"), patch(
                "accounts.models.timezone.now", return_value=moment,
            ):
                self.assertEqual(User(banner_gradient="blue").account_theme, expected)

    def test_seasonal_default_covers_existing_palettes_without_rewriting_them(self):
        with patch("accounts.models.timezone.localdate", return_value=date(2026, 11, 6)):
            for theme in [key for key, _ in User.BANNER_GRADIENT_CHOICES] + ["", "unknown"]:
                with self.subTest(theme=theme):
                    user = User(
                        banner_gradient=theme, custom_theme_primary="#2563eb",
                        custom_theme_secondary="#7c3aed", custom_theme_tertiary="#06b6d4",
                    )
                    self.assertEqual(user.account_theme, "christmas")
                    self.assertEqual(user.account_theme_label, "Christmas")
                    self.assertEqual(user.account_theme_style, "")
                    self.assertEqual(user.banner_gradient, theme)

    def test_original_theme_and_custom_palette_return_after_the_deadline(self):
        cases = (("default", "default"), ("blue", "blue"), ("custom", "custom"),
                 ("christmas", "christmas"), ("", "default"), ("unknown", "default"))
        for stored, expected in cases:
            with self.subTest(stored=stored):
                user = User(
                    banner_gradient=stored, custom_theme_primary="#2563eb",
                    custom_theme_secondary="#7c3aed", custom_theme_tertiary="#06b6d4",
                )
                with patch("accounts.models.timezone.localdate", return_value=date(2026, 12, 31)):
                    self.assertEqual(user.account_theme, "christmas")
                with patch("accounts.models.timezone.localdate", return_value=date(2027, 1, 1)):
                    self.assertEqual(user.account_theme, expected)
                    self.assertEqual(user.banner_gradient, stored)
                    if stored == "custom":
                        self.assertIn("--account-c1: #2563eb", user.account_theme_style)


class ChristmasDefaultAccountTests(TestCase):
    def setUp(self):
        today = patch("accounts.models.timezone.localdate", return_value=date(2026, 11, 6))
        today.start()
        self.addCleanup(today.stop)
        self.user = User.objects.create_user(
            username="seasonal-theme", role=User.Roles.ADMIN,
            must_change_password=False, banner_gradient="blue",
        )
        self.client.force_login(self.user)

    def assert_page_theme(self, response, expected):
        self.assertEqual(response.status_code, 200)
        body = re.search(r"<body\b[^>]*>", response.content.decode()).group(0)
        self.assertIn(f'data-account-theme="{expected}"', body)
        self.assertEqual("account-themed" in body, expected != "default")

    def test_user_inserts_from_pre_rollout_code_receive_the_database_default(self):
        quote = connection.ops.quote_name
        table = quote(User._meta.db_table)
        columns = [
            field.column for field in User._meta.concrete_fields
            if not field.primary_key and field.name != "christmas_default_overridden"
        ]
        selected = ["%s" if name == "username" else quote(name) for name in columns]
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {table} ({', '.join(map(quote, columns))}) "
                    f"SELECT {', '.join(selected)} FROM {table} WHERE id = %s RETURNING id",
                    ["seasonal-legacy-insert", self.user.pk],
                )
                user_id = cursor.fetchone()[0]
        except IntegrityError as error:
            self.fail(f"Pre-rollout user inserts must be able to omit the new seasonal flag: {error}")
        user = User.objects.get(pk=user_id)
        self.assertFalse(user.christmas_default_overridden)
        self.assertEqual(user.account_theme, "christmas")

    def test_existing_account_and_picker_start_with_christmas(self):
        response = self.client.get(reverse("accounts:update"))
        self.assert_page_theme(response, "christmas")
        self.assertEqual(response.context["theme_form"]["banner_gradient"].value(), "christmas")
        self.assertEqual(response.context["form"]["banner_gradient"].value(), "blue")
        self.user.refresh_from_db()
        self.assertEqual(self.user.banner_gradient, "blue")

    def test_new_accounts_of_every_role_start_with_christmas(self):
        for role in User.Roles.values:
            with self.subTest(role=role):
                user = User.objects.create_user(
                    username=f"seasonal-{role}", role=role, must_change_password=False,
                )
                self.client.force_login(user)
                response = self.client.get(reverse("accounts:update"))
                self.assert_page_theme(response, "christmas")
                self.assertEqual(user.banner_gradient, "default")

    def test_each_saved_theme_takes_priority_across_sessions_and_after_expiry(self):
        other = User.objects.create_user(username="seasonal-other", banner_gradient="forest")
        for theme, _ in User.BANNER_GRADIENT_CHOICES:
            with self.subTest(theme=theme):
                response = self.client.post(reverse("accounts:update"), {
                    "save_theme": "1", "banner_gradient": theme, "user": other.pk,
                    "custom_theme_primary": "#2563EB", "custom_theme_secondary": "#7c3aed",
                    "custom_theme_tertiary": "#06b6d4",
                })
                self.assertRedirects(response, reverse("accounts:update") + "#themes")
                self.user.refresh_from_db()
                self.assertEqual(self.user.banner_gradient, theme)
                second_device = Client()
                second_device.force_login(self.user)
                self.assert_page_theme(second_device.get(reverse("accounts:update")), theme)
                with patch("accounts.models.timezone.localdate", return_value=date(2027, 1, 1)):
                    self.assert_page_theme(second_device.get(reverse("accounts:update")), theme)
                other.refresh_from_db()
                self.assertEqual(other.banner_gradient, "forest")
                self.assertEqual(other.account_theme, "christmas")

    def test_legacy_banner_save_overrides_the_seasonal_default(self):
        response = self.client.post(reverse("accounts:update"), {
            "save_banner": "1", "banner_gradient": "teal",
        })
        self.assertRedirects(response, reverse("accounts:update") + "#themes")
        self.assert_page_theme(self.client.get(reverse("accounts:update")), "teal")

    def test_invalid_theme_or_custom_palette_does_not_cancel_the_default(self):
        for data in (
            {"banner_gradient": "invalid"},
            {"banner_gradient": "custom", "custom_theme_primary": "invalid"},
        ):
            with self.subTest(data=data):
                response = self.client.post(reverse("accounts:update"), {"save_theme": "1", **data})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["theme_form"].errors)
                self.user.refresh_from_db()
                self.assertEqual(self.user.banner_gradient, "blue")
                self.assert_page_theme(self.client.get(reverse("accounts:update")), "christmas")

    def test_settings_save_does_not_cancel_the_default(self):
        response = self.client.post(reverse("accounts:update"), {
            "save_settings": "1", "pref_reduce_motion": "1", "banner_gradient": "forest",
        })
        self.assertRedirects(response, reverse("accounts:update") + "#settings")
        self.user.refresh_from_db()
        self.assertEqual(self.user.banner_gradient, "blue")
        self.assertTrue(self.user.pref_reduce_motion)
        self.assert_page_theme(self.client.get(reverse("accounts:update")), "christmas")

    def test_profile_save_preserves_the_previous_theme_and_seasonal_default(self):
        opened = self.client.get(reverse("accounts:update"))
        response = self.client.post(reverse("accounts:update"), {
            "first_name": "Seasonal", "last_name": "Profile", "email": "seasonal@example.com",
            "phone_number": "", "department": "ESS", "banner_gradient": "blue",
            "_form_version": opened.context["form"].initial["_form_version"],
        })
        self.assertRedirects(response, reverse("accounts:update"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Seasonal")
        self.assertEqual(self.user.banner_gradient, "blue")
        self.assert_page_theme(self.client.get(reverse("accounts:update")), "christmas")
        with patch("accounts.models.timezone.localdate", return_value=date(2027, 1, 1)):
            self.assert_page_theme(self.client.get(reverse("accounts:update")), "blue")

    def browser_login_theme(self, account_page, moment, storage=None):
        login_page = Client().get(reverse("login"))
        scripts = []
        for response in (account_page, login_page):
            parser = InlineScriptParser()
            parser.feed(response.content.decode())
            matching_scripts = [
                script for _, script in parser.scripts
                if "reqHubLoginTheme" in script
            ]
            self.assertTrue(matching_scripts, "the login Christmas theme script is missing")
            scripts.extend(matching_scripts)
        program = """
            const vm = require('vm');
            const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
            const values = new Map(Object.entries(input.storage));
            const classes = new Set();
            const toggled = new Set();
            const context = {
                localStorage: {
                    getItem: key => values.get(key) ?? null,
                    setItem: (key, value) => values.set(key, String(value)),
                    removeItem: key => values.delete(key),
                },
                document: { documentElement: {
                    classList: {
                        add: value => classes.add(value),
                        toggle: (value, on) => (on ? classes : toggled).add(value),
                    },
                } },
                Date: { now: () => Date.parse(input.moment), parse: Date.parse },
            };
            input.scripts.forEach(script => vm.runInNewContext(script, context));
            console.log(JSON.stringify(classes.has('login-christmas')));
        """
        result = subprocess.run(
            ["node", "-e", program], input=json.dumps({
                "scripts": scripts, "moment": moment, "storage": storage or {},
            }), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_automatic_login_theme_expires_without_another_signed_in_visit(self):
        page = self.client.get(reverse("accounts:update"))
        for moment, expected in (
            ("2026-12-31T15:59:59Z", True),
            ("2026-12-31T16:00:00Z", False),
            ("2027-12-01T00:00:00Z", False),
        ):
            with self.subTest(moment=moment):
                self.assertEqual(self.browser_login_theme(page, moment), expected)

    def test_previous_christmas_choice_keeps_its_login_theme_after_the_deadline(self):
        self.user.banner_gradient = "christmas"
        self.user.christmas_default_overridden = True
        self.user.save(update_fields=["banner_gradient", "christmas_default_overridden"])
        page = self.client.get(reverse("accounts:update"))
        self.assertTrue(self.browser_login_theme(page, "2027-01-01T00:00:00Z", {
            "reqHubLoginTheme": "christmas", "reqHubLoginThemeExpiresAt": "2027-01-01T00:00:00+08:00",
        }))

    def test_explicit_christmas_choice_clears_previous_automatic_login_expiry(self):
        response = self.client.post(reverse("accounts:update"), {
            "save_theme": "1", "banner_gradient": "christmas",
        })
        self.assertRedirects(response, reverse("accounts:update") + "#themes")
        page = self.client.get(reverse("accounts:update"))
        self.assertTrue(self.browser_login_theme(page, "2027-01-01T00:00:00Z", {
            "reqHubLoginTheme": "christmas", "reqHubLoginThemeExpiresAt": "2027-01-01T00:00:00+08:00",
        }))

    def test_seasonal_default_keeps_saved_festive_effect_preferences(self):
        self.user.pref_christmas_enabled = False
        self.user.pref_christmas_snow = False
        self.user.save(update_fields=["pref_christmas_enabled", "pref_christmas_snow"])
        response = self.client.get(reverse("accounts:update"))
        self.assert_page_theme(response, "christmas")
        parser = InlineScriptParser()
        parser.feed(response.content.decode())
        effects = next(
            script for attrs, script in parser.scripts
            if attrs.get("id") == "christmas-effects-settings"
        )
        self.assertEqual(json.loads(effects), {
            "enabled": False, "border": True, "snow": False, "lights": True,
            "cursor": True, "clicks": True, "santa": True,
        })
