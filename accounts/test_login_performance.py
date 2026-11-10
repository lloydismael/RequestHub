from html.parser import HTMLParser
from pathlib import Path

from PIL import Image
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.templatetags.static import static
from django.urls import reverse

from accounts.models import User


FULL_PROFILE_ASSETS = (
    "css/liquid-glass-dashboard.css", "css/account-themes.css", "hub/form_runtime.css",
    "js/smooth-scroll.js", "js/notification-toasts.js", "hub/form_runtime.js",
)
BOOTSTRAP_BUNDLE = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"


class AssetParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@override_settings(DEBUG=True)
class LoginImageResponseTests(TestCase):
    def test_entry_routes_reserve_logo_space_and_use_responsive_sources(self):
        for url in ("/", "/accounts/login/"):
            with self.subTest(url=url):
                response = self.client.get(url, follow=True)
                self.assertEqual(response.status_code, 200)
                parser = AssetParser(response.content.decode())
                logo = next(attrs for tag, attrs in parser.nodes if tag == "img" and "login-card__logo" in attrs.get("class", ""))
                self.assertEqual((logo.get("width"), logo.get("height")), ("384", "192"))
                self.assertEqual(logo["src"], static("img/phil-data-full-logo-login-384.webp"))
                self.assertIn(static("img/phil-data-full-logo-login-768.webp"), logo["srcset"])
                self.assertEqual(logo.get("loading"), "eager")
                self.assertEqual(logo.get("fetchpriority"), "high")
                self.assertEqual(logo["alt"], "Phil-Data Business Systems logo")

    def test_login_uses_a_small_favicon(self):
        parser = AssetParser(self.client.get("/").content.decode())
        favicon = next(attrs for tag, attrs in parser.nodes if tag == "link" and attrs.get("rel") == "icon")
        self.assertEqual(favicon["href"], static("images/request-hub-favicon-32.png"))
        self.assertEqual(favicon.get("sizes"), "32x32")


@override_settings(DEBUG=True)
class LoginAssetProfileTests(TestCase):
    def test_login_omits_ui_assets_it_does_not_use(self):
        for url in ("/", "/accounts/login/"):
            response = self.client.get(url, follow=True)
            for name in FULL_PROFILE_ASSETS:
                with self.subTest(url=url, asset=name):
                    self.assertNotContains(response, static(name))
            self.assertNotContains(response, BOOTSTRAP_BUNDLE)

    def test_login_keeps_theme_and_form_interaction_dependencies(self):
        response = self.client.get("/")
        for name in ("css/app.css", "css/liquid-glass.css", "css/liquid-glass-ambient.css", "js/liquid-glass.js", "js/page-transitions.js", "js/navbar-contrast.js"):
            self.assertContains(response, static(name))
        self.assertContains(response, 'id="theme-checkbox"')

    def test_other_anonymous_templates_keep_the_full_profile(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        html = render_to_string("base.html", request=request)
        for name in FULL_PROFILE_ASSETS:
            self.assertIn(static(name), html)
        self.assertIn(BOOTSTRAP_BUNDLE, html)

    def test_authenticated_profile_keeps_the_full_profile(self):
        user = User.objects.create_user(username="login-asset-profile", role=User.Roles.ADMIN, must_change_password=False)
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:update"))
        self.assertEqual(response.status_code, 200)
        for name in FULL_PROFILE_ASSETS:
            self.assertContains(response, static(name))
        self.assertContains(response, BOOTSTRAP_BUNDLE)

    def test_login_keeps_no_store_headers_and_csrf_cookie(self):
        response = self.client.get("/")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("csrftoken", response.cookies)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_invalid_login_keeps_errors_on_the_fast_form(self):
        response = self.client.post("/", {"username": "unknown-login-performance", "password": "invalid"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertTrue(response.context.get("login_asset_profile"))
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        for name in FULL_PROFILE_ASSETS:
            self.assertNotContains(response, static(name))


class LoginImageFileTests(SimpleTestCase):
    def test_logo_variants_are_small_and_keep_the_source_aspect_ratio(self):
        total = 0
        for width in (384, 768):
            name = f"img/phil-data-full-logo-login-{width}.webp"
            filename = finders.find(name)
            self.assertIsNotNone(filename, name)
            total += Path(filename).stat().st_size
            with Image.open(filename) as image:
                self.assertEqual(image.size, (width, width // 2))
        self.assertLessEqual(total, 200_000)

    def test_favicon_is_small_and_preserves_transparency(self):
        filename = finders.find("images/request-hub-favicon-32.png")
        self.assertIsNotNone(filename)
        self.assertLessEqual(Path(filename).stat().st_size, 10_000)
        with Image.open(filename) as image:
            self.assertEqual(image.size, (32, 32))
            self.assertIn(image.mode, ("RGBA", "LA", "P"))
