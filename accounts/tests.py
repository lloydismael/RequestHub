import json
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core import serializers as django_serializers
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.templatetags.static import static
from django.urls import reverse

from accounts.models import StoredFile, User
from accounts.views import LOGIN_THROTTLE_LIMIT, LOGIN_THROTTLE_MESSAGE


class LoginPageTests(TestCase):
    def test_login_renders_accessible_liquid_glass_contract(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")
        self.assertContains(response, 'class="login-card login-card--glass lg-surface lg-enter"')
        self.assertContains(response, 'id="lg-edge-refraction"')
        self.assertContains(response, "feDisplacementMap")
        self.assertContains(response, '<span class="login-card__lens" aria-hidden="true"></span>', html=True)
        self.assertContains(response, '<span class="login-card__rim" aria-hidden="true"></span>', html=True)
        self.assertContains(response, '<span class="login-card__sheen" aria-hidden="true"></span>', html=True)

    def test_login_preserves_form_and_theme_contracts(self):
        response = self.client.get(reverse("login"))
        content = response.content.decode()

        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertEqual(content.count('id="theme-checkbox"'), 1)
        self.assertContains(response, "var useDark = savedTheme === 'dark';")
        self.assertContains(response, "const startsDark = currentTheme === 'dark';")
        self.assertContains(response, "document.documentElement.style.colorScheme")
        self.assertContains(response, "const THEME_REVEAL_DURATION = 620;")
        self.assertContains(response, "body.classList.contains('pref-reduce-motion')")
        self.assertContains(response, "body.classList.contains('pref-reduce-effects')")
        self.assertContains(response, "activeTransition.skipTransition()")
        self.assertContains(response, "typeof overlay.animate !== 'function'")
        self.assertNotContains(response, "themeTransitionInFlight")
        self.assertNotContains(response, "brightness(1.08) saturate(0.9)")
        self.assertNotContains(response, "!savedTheme && window.matchMedia('(prefers-color-scheme: dark)').matches")
        self.assertEqual(response.context["form"].fields["username"].widget.attrs["placeholder"], " ")
        self.assertEqual(response.context["form"].fields["password"].widget.attrs["placeholder"], " ")

    def test_login_includes_progressive_page_transition_contract(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, 'id="request-hub-page"')
        self.assertContains(response, 'class="request-hub-page"')
        self.assertContains(response, static("css/app.css") + "?v=")
        self.assertContains(response, "/static/js/page-transitions.")
        self.assertContains(response, "js?v=1")
        self.assertContains(response, "reqHubPageTransitionDirection")
        self.assertContains(response, "dataset.pageTransitionDirection")

    def test_login_includes_adaptive_navbar_contrast_contract(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, 'data-nav-surface="light"')
        self.assertContains(response, 'data-nav-default-surface="light"')
        self.assertContains(response, "/static/js/navbar-contrast.")
        self.assertContains(response, "js?v=1")
        self.assertContains(response, "navbar.dataset.navSurface = surface;")
        self.assertContains(response, "navbar.dataset.navDefaultSurface = surface;")
        self.assertContains(response, "requesthub:theme-changed")

    def test_login_loads_shared_semantic_theme_stylesheet(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, static("css/liquid-glass.css") + "?v=")

    def test_anonymous_landing_still_redirects_to_login(self):
        response = self.client.get(reverse("landing"))

        self.assertRedirects(response, reverse("login"))

    def test_login_page_does_not_expose_credentials(self):
        response = self.client.get(reverse("login"))
        html = response.content.decode()

        self.assertNotIn("@Password", html)
        self.assertNotIn("Default login", html)
        self.assertNotIn("default_password", html)


class AccountSettingsTests(TestCase):
    def test_new_users_have_chatbot_disabled_by_default(self):
        user = User.objects.create_user(username="chatbot_default_user", password="TestPass123!")

        self.assertFalse(user.show_chatbot)


class ProfileSmokeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="smoke-profile", password="Example-password-937!",
            role=User.Roles.ADMIN, must_change_password=False,
        )
        self.client.force_login(self.user)

    def test_saved_profile_renders_for_every_role(self):
        for role in User.Roles.values:
            with self.subTest(role=role):
                self.user.role = role
                self.user.save(update_fields=["role"])
                response = self.client.get(reverse("accounts:update"))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "accounts/profile_form.html")
                self.assertIs(type(response.context["form"].instance), User)

    def test_saved_profile_post_and_reopen(self):
        opened = self.client.get(reverse("accounts:update"))
        response = self.client.post(reverse("accounts:update"), {
            "first_name": "Smoke", "last_name": "Profile", "email": "smoke@example.com",
            "phone_number": "", "department": "ESS", "banner_gradient": "blue",
            "_form_version": opened.context["form"].initial["_form_version"],
        })
        self.assertRedirects(response, reverse("accounts:update"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Smoke")
        self.assertEqual(self.user.department, "ESS")

    def test_profile_settings_persist(self):
        response = self.client.post(reverse("accounts:update"), {
            "save_settings": "1", "pref_reduce_motion": "1", "pref_compact_tables": "1",
        })
        self.assertRedirects(response, reverse("accounts:update") + "#settings")
        self.user.refresh_from_db()
        self.assertTrue(self.user.pref_reduce_motion)
        self.assertTrue(self.user.pref_compact_tables)

    def test_themes_persist_for_every_role_without_changing_profile(self):
        other = User.objects.create_user(username="other-theme", banner_gradient="blue")
        for index, (theme, _) in enumerate(User.BANNER_GRADIENT_CHOICES):
            role = User.Roles.values[index % len(User.Roles.values)]
            with self.subTest(role=role, theme=theme):
                self.user.role = role
                self.user.save(update_fields=["role"])
                response = self.client.post(reverse("accounts:update"), {
                    "save_theme": "1", "banner_gradient": theme,
                    "first_name": "Must not change", "show_chatbot": "1",
                })
                self.assertRedirects(response, reverse("accounts:update") + "#themes")
                self.user.refresh_from_db()
                self.assertEqual(self.user.banner_gradient, theme)
                self.assertEqual(self.user.first_name, "")
                self.assertFalse(self.user.profile_completed)
                self.assertFalse(self.user.show_chatbot)
                reopened = self.client.get(reverse("accounts:update"))
                self.assertContains(reopened, f'data-account-theme="{theme}"')
                self.assertContains(reopened, "Apply Theme")
                from django.templatetags.static import static
                self.assertContains(reopened, static("css/account-themes.css"))
        other.refresh_from_db()
        self.assertEqual(other.banner_gradient, "blue")

    def test_theme_validation_retains_saved_theme(self):
        for value in ["invalid", "", "<script>"]:
            with self.subTest(value=value):
                response = self.client.post(reverse("accounts:update"), {
                    "save_theme": "1", "banner_gradient": value,
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["theme_form"].errors)
                self.assertContains(response, 'id="theme-errors"')
                self.user.refresh_from_db()
                self.assertEqual(self.user.banner_gradient, "blue")

    def test_legacy_banner_save_uses_account_theme(self):
        response = self.client.post(reverse("accounts:update"), {
            "save_banner": "1", "banner_gradient": "teal",
        })
        self.assertRedirects(response, reverse("accounts:update") + "#themes")
        self.user.refresh_from_db()
        self.assertEqual(self.user.banner_gradient, "teal")

    def test_theme_picker_has_all_choices_and_native_radios(self):
        response = self.client.get(reverse("accounts:update"))
        for theme, label in User.BANNER_GRADIENT_CHOICES:
            self.assertContains(response, f'data-account-theme="{theme}"')
            self.assertContains(response, f'type="radio" name="banner_gradient" value="{theme}"')
        self.assertNotContains(response, "Edit Banner")
        self.assertNotContains(response, "Save Banner")

    def test_anonymous_profile_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("accounts:update"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class SecretHygieneTests(TestCase):
    root = Path(__file__).resolve().parents[1]
    secret_keys = {
        "DJANGO_SECRET_KEY",
        "DB_USER",
        "DB_PASSWORD",
        "ACS_EMAIL_CONNECTION_STRING",
        "ACS_EMAIL_SENDER",
        "PHILDATA_TENANT_ID",
        "PHILDATA_CLIENT_ID",
        "PHILDATA_CLIENT_SECRET",
    }

    def test_landing_template_does_not_document_default_login(self):
        landing = (self.root / "templates" / "landing.html").read_text(encoding="utf-8")

        self.assertNotIn("Default login", landing)
        self.assertNotIn("default_password", landing)
        self.assertNotIn("default_username", landing)

    def test_settings_do_not_ship_a_shared_user_password(self):
        self.assertFalse(hasattr(settings, "DEFAULT_USER_PASSWORD"))

    def test_env_files_are_ignored_and_example_has_no_secret_values(self):
        gitignore = (self.root / ".gitignore").read_text(encoding="utf-8")
        dockerignore = (self.root / ".dockerignore").read_text(encoding="utf-8")
        example = (self.root / ".env.example").read_text(encoding="utf-8")

        self.assertIn(".env", gitignore)
        self.assertIn(".env.*", gitignore)
        self.assertIn("!.env.example", gitignore)
        self.assertIn(".env", dockerignore)
        self.assertIn(".env.*", dockerignore)
        self.assertNotIn("DJANGO_DEFAULT_USER_PASSWORD", example)

        for line in example.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key in self.secret_keys:
                self.assertEqual(value, "", msg=f"{key} must be empty in .env.example")