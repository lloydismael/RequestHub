import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from accounts.models import User


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_navigation = False
        self.current_link = None
        self.links = []
        self.sqr_header = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "glass-card" in attrs.get("class", "").split() and attrs.get("data-navbar-surface") == "theme":
            self.sqr_header = attrs
        if tag == "ul" and "navbar-nav" in attrs.get("class", "").split():
            self.in_navigation = True
        elif self.in_navigation and tag == "a":
            self.current_link = [attrs, ""]

    def handle_data(self, data):
        if self.current_link is not None:
            self.current_link[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current_link is not None:
            attrs, text = self.current_link
            self.links.append((text.strip(), attrs))
            self.current_link = None
        elif tag == "ul" and self.in_navigation:
            self.in_navigation = False


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class SqrNavigationTests(SimpleTestCase):
    def request_for(self, role):
        request = RequestFactory().get(reverse("hub:dashboard"))
        request.user = User(username="navigation-fixture", role=role, christmas_default_overridden=True)
        return request

    def test_ppt_navigation_order_and_existing_role_permissions(self):
        for role in (User.Roles.ADMIN, User.Roles.PM_ESG, User.Roles.ENGINEER, User.Roles.ON_HOLD,
                     User.Roles.REQUESTOR, User.Roles.PM_ESS):
            with self.subTest(role=role):
                html = render_to_string("base.html", {"request": self.request_for(role)})
                parser = NavigationParser()
                parser.feed(html)
                labels = [text for text, _ in parser.links]
                if role in (User.Roles.ADMIN, User.Roles.PM_ESG):
                    index = labels.index("Dashboard")
                    self.assertEqual(labels[index:index + 3], ["Dashboard", "PPT", "SQR"])
                    self.assertEqual(parser.links[index + 1][1]["href"], reverse("hub:sqr") + "?view=performance")
                    self.assertEqual(parser.links[index + 2][1]["href"], reverse("hub:sqr") + "?view=tracker")
                else:
                    self.assertNotIn("PPT", labels)
                    self.assertEqual("SQR" in labels, role in (User.Roles.ENGINEER, User.Roles.ON_HOLD))

    def test_current_navigation_is_identified_on_initial_render(self):
        for query, role, active_tab, expected in (
            ({"view": "performance"}, User.Roles.PM_ESG, "proposal", "PPT"),
            ({"view": "tracker"}, User.Roles.PM_ESG, "proposal", "SQR"),
            ({"tab": "reports", "view": "performance"}, User.Roles.PM_ESG, "reports", "SQR"),
            ({"tab": "  REPORTS  ", "view": "performance"}, User.Roles.PM_ESG, "reports", "SQR"),
            ({"view": "performance"}, User.Roles.ENGINEER, "proposal", "SQR"),
        ):
            with self.subTest(query=query, role=role):
                request = RequestFactory().get(reverse("hub:sqr"), query)
                request.resolver_match = resolve(request.path)
                request.user = User(username="fixture", role=role, christmas_default_overridden=True)
                parser = NavigationParser()
                parser.feed(render_to_string("base.html", {"request": request, "active_sqr_tab": active_tab}))
                current = [name for name, attrs in parser.links if attrs.get("aria-current") == "page"]
                self.assertEqual(current, [expected])

    def test_performance_moves_out_of_the_sqr_view_selector(self):
        request = self.request_for(User.Roles.PM_ESG)
        with patch("hub.form_editor.get_definition", return_value=None):
            html = render_to_string("hub/sqr.html", {
                "request": request, "is_pm": True, "active_sqr_tab": "proposal",
            })
        self.assertFalse('data-sqr-view-button="performance"' in html, "Performance still appears in the SQR selector")
        self.assertTrue('data-sqr-view-button="tracker"' in html)
        self.assertTrue('data-sqr-view-button="prototype"' in html)
        self.assertIsNotNone(re.search(r'<section\b[^>]*data-sqr-view-panel="performance"', html))

    def test_sqr_header_is_initially_hidden_only_for_ppt(self):
        for query, role, tab, hidden in (
            ({"view": "performance"}, User.Roles.ADMIN, "proposal", True),
            ({"view": "performance"}, User.Roles.PM_ESG, "proposal", True),
            ({"view": "tracker"}, User.Roles.PM_ESG, "proposal", False),
            ({"view": "prototype"}, User.Roles.PM_ESG, "proposal", False),
            ({"view": "performance", "focus": "42"}, User.Roles.PM_ESG, "proposal", False),
            ({"view": "performance", "focus": ""}, User.Roles.PM_ESG, "proposal", False),
            ({"view": "performance", "tab": "reports"}, User.Roles.PM_ESG, "reports", False),
            ({"view": "performance"}, User.Roles.ENGINEER, "proposal", False),
        ):
            with self.subTest(query=query, role=role, tab=tab):
                request = RequestFactory().get(reverse("hub:sqr"), query)
                request.user = User(username="fixture", role=role, christmas_default_overridden=True)
                is_pm = role in (User.Roles.ADMIN, User.Roles.PM_ESG)
                with patch("hub.form_editor.get_definition", return_value=None):
                    html = render_to_string("hub/sqr.html", {
                        "request": request, "is_pm": is_pm, "active_sqr_tab": tab,
                        "sqr_reports_summary": {"at_risk_count": 0, "off_track_count": 0},
                    })
                parser = NavigationParser()
                parser.feed(html)
                self.assertIsNotNone(parser.sqr_header)
                self.assertEqual("hidden" in parser.sqr_header, hidden)

    @skipUnless(shutil.which("node"), "Node.js is required for JavaScript regressions")
    def test_ppt_and_sqr_navigation_runs_the_real_view_controller(self):
        script = Path(settings.BASE_DIR) / "scripts_check" / "test_sqr_navigation.js"
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
