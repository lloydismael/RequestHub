import json
import re
import shutil
import subprocess
from datetime import date
from html import unescape
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User

REMEMBER = "localStorage.setItem('reqHubLoginTheme', 'christmas');"
FORGET = "localStorage.removeItem('reqHubLoginTheme');"


class ChristmasLoginThemeTests(TestCase):
    def test_login_tab_title_has_a_clean_separator(self):
        response = self.client.get(reverse("login"))
        title = re.search(r"<title>(.*?)</title>", response.content.decode()).group(1)

        self.assertEqual(unescape(title), "Login \N{MIDDLE DOT} Request Hub")

    def test_first_visit_uses_christmas_only_during_the_2026_default_window(self):
        cases = (
            (date(2026, 11, 5), False),
            (date(2026, 11, 6), True),
            (date(2026, 11, 7), True),
            (date(2026, 12, 31), True),
            (date(2027, 1, 1), False),
            (date(2027, 11, 6), False),
        )
        for today, expected in cases:
            with self.subTest(today=today), patch("accounts.models.timezone.localdate", return_value=today):
                response = self.client.get(reverse("login"))
                root = re.search(r"<html\b[^>]*>", response.content.decode()).group(0)
                self.assertEqual("login-christmas" in root, expected)

    @skipUnless(shutil.which("node"), "Node.js is required for JavaScript regressions")
    def test_login_default_and_remembered_choices_handle_storage_and_expiry(self):
        pages = {}
        for seasonal, today in ((True, date(2026, 12, 31)), (False, date(2027, 1, 1))):
            with patch("accounts.models.timezone.localdate", return_value=today):
                html = self.client.get(reverse("login")).content.decode()
            root = re.search(r"<html\b[^>]*>", html).group(0)
            scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
            pages[seasonal] = {
                "initial": "login-christmas" in root,
                "script": "\n".join(script for script in scripts if "reqHubLoginTheme" in script),
            }
        expired = {"reqHubLoginTheme": "christmas", "reqHubLoginThemeExpiresAt": "2027-01-01T00:00:00+08:00"}
        cases = (
            ("seasonal-first-visit", True, {}, False, True),
            ("seasonal-other-choice", True, {"reqHubLoginTheme": "blue", "reqHubTheme": "dark"}, False, True),
            ("seasonal-expired-flag", True, expired, False, True),
            ("seasonal-blocked-storage", True, {}, True, True),
            ("after-first-visit", False, {}, False, False),
            ("after-expired-default", False, expired, False, False),
            ("after-explicit-christmas", False, {"reqHubLoginTheme": "christmas"}, False, True),
            ("after-other-choice", False, {"reqHubLoginTheme": "blue"}, False, False),
            ("after-blocked-storage", False, {}, True, False),
            ("after-invalid-expiry", False, {**expired, "reqHubLoginThemeExpiresAt": "invalid"}, False, False),
        )
        payload = [
            {"name": name, **pages[seasonal], "storage": storage, "blocked": blocked}
            for name, seasonal, storage, blocked, _ in cases
        ]
        result = subprocess.run(
            [shutil.which("node"), "-e", """
                const vm = require('node:vm');
                const cases = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
                const results = Object.fromEntries(cases.map(item => {
                    const classes = new Set(item.initial ? ['login-christmas'] : []);
                    vm.runInNewContext(item.script, {
                        document: { documentElement: { classList: { add: name => classes.add(name) } } },
                        localStorage: { getItem: key => {
                            if (item.blocked) throw new Error('Storage unavailable');
                            return item.storage[key] ?? null;
                        } },
                        Date: class extends Date {
                            static now() { return Date.parse('2027-01-01T00:00:00+08:00'); }
                        },
                    });
                    return [item.name, classes.has('login-christmas')];
                }));
                process.stdout.write(JSON.stringify(results));
            """],
            input=json.dumps(payload), capture_output=True, encoding="utf-8", timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {name: expected for name, _, _, _, expected in cases})

    def signed_in_page(self, username, **fields):
        user = User.objects.create_user(
            username=username, role=User.Roles.ADMIN, must_change_password=False, **fields,
        )
        self.client.force_login(user)
        return self.client.get(reverse("accounts:update"))

    def test_login_loads_christmas_design_behind_the_browser_flag(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, static("css/login-christmas.css") + "?v=")
        self.assertContains(response, static("js/login-christmas.js") + "?v=")
        self.assertContains(response, "localStorage.getItem('reqHubLoginTheme') === 'christmas'")
        self.assertContains(response, "document.documentElement.classList.add('login-christmas')")
        self.assertContains(response, 'class="xmas-scene xmas-only" aria-hidden="true"')
        self.assertContains(response, 'class="xmas-card-deco xmas-only" aria-hidden="true" data-xmas-card-deco')
        self.assertContains(response, '<i class="bi bi-person-fill login-form__icon xmas-only" aria-hidden="true"></i>', html=True)
        self.assertContains(response, '<i class="bi bi-lock-fill login-form__icon xmas-only" aria-hidden="true"></i>', html=True)

    def test_login_loads_shared_snowflake_and_santa_effect_assets(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, static("css/christmas-theme.css") + "?v=")
        self.assertContains(response, static("js/christmas-theme.js") + "?v=")
        self.assertContains(response, static("js/christmas-theme-snapshot.js") + "?v=")
        html = response.content.decode()
        self.assertLess(html.index(static("js/christmas-theme-snapshot.js")), html.index(static("js/christmas-theme.js")))
        self.assertEqual(html.count(static("js/christmas-theme-snapshot.js")), 1)
        self.assertContains(response, f'rel="preload" as="image" href="{static("images/christmas-santa-sleigh.svg")}"')
        self.assertNotContains(response, static("js/christmas-lights.js"))

    def test_signed_in_pages_load_the_snapshot_helper_before_santa(self):
        response = self.signed_in_page("santa-snapshot-wiring", banner_gradient="christmas")
        html = response.content.decode()
        helper = static("js/christmas-theme-snapshot.js")
        self.assertEqual(html.count(helper), 1)
        self.assertLess(html.index(helper), html.index(static("js/christmas-theme.js")))
        self.assertContains(response, f'rel="preload" as="image" href="{static("images/christmas-santa-sleigh.svg")}"')

    def test_christmas_design_keeps_login_form_contracts(self):
        response = self.client.get(reverse("login"))
        form = response.context["form"]

        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'class="login-card login-card--glass lg-surface lg-enter"')
        self.assertContains(response, ">Username</label>")
        self.assertContains(response, ">Password</label>")
        self.assertEqual(form.fields["username"].widget.attrs["placeholder"], " ")
        self.assertEqual(form.fields["password"].widget.attrs["placeholder"], " ")
        self.assertIsNone(form["username"].value())
        self.assertIsNone(form["password"].value())

    @override_settings(CHRISTMAS_LOGIN_THEME_ENABLED=False)
    def test_site_switch_turns_the_christmas_login_off(self):
        response = self.client.get(reverse("login"))

        self.assertNotContains(response, "login-christmas")
        self.assertNotContains(response, "reqHubLoginTheme")
        self.assertNotContains(response, "xmas-")
        self.assertNotContains(response, static("css/christmas-theme.css"))
        self.assertNotContains(response, static("js/christmas-theme.js"))
        self.assertContains(response, 'class="login-card login-card--glass lg-surface lg-enter"')

    def test_signed_in_christmas_account_is_remembered_for_the_login_page(self):
        response = self.signed_in_page("christmas-login", banner_gradient="christmas")

        self.assertContains(response, REMEMBER)
        self.assertNotContains(response, FORGET)

    @patch("accounts.models.timezone.localdate", new=lambda **kwargs: date(2027, 1, 1))
    def test_other_themes_or_festive_effects_off_forget_the_christmas_login(self):
        cases = {
            "other-theme": {"banner_gradient": "blue"},
            "effects-off": {"banner_gradient": "christmas", "pref_christmas_enabled": False},
        }
        for name, fields in cases.items():
            with self.subTest(name):
                response = self.signed_in_page(f"christmas-login-{name}", **fields)
                self.assertContains(response, FORGET)
                self.assertNotContains(response, REMEMBER)

    def test_signed_out_pages_leave_the_remembered_theme_alone(self):
        response = self.client.get(reverse("login"))

        self.assertNotContains(response, REMEMBER)
        self.assertNotContains(response, FORGET)


class ChristmasLoginStaticFilesTests(SimpleTestCase):
    def test_background_image_ships_with_the_stylesheet(self):
        css_path = finders.find("css/login-christmas.css")
        with open(css_path, encoding="utf-8") as stylesheet:
            css = stylesheet.read()

        # Manifest static storage fails collectstatic if a referenced file is missing.
        self.assertRegex(css, r'url\("\.\./img/login-christmas-bg\.webp(?:\?[^"#]*)?"\)')
        self.assertIsNotNone(finders.find("img/login-christmas-bg.webp"))
        self.assertIsNotNone(finders.find("js/login-christmas.js"))


@skipUnless(shutil.which("node"), "Node.js is required for JavaScript regressions")
class LoginAnimationScriptTests(SimpleTestCase):
    def test_login_animation_regressions(self):
        checks = Path(settings.BASE_DIR) / "scripts_check"
        result = subprocess.run(
            [
                shutil.which("node"), "--test",
                str(checks / "test_login_christmas.cjs"),
                str(checks / "test_liquid_glass.cjs"),
                str(checks / "test_christmas_theme.cjs"),
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
