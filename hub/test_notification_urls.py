import os
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from hub.views import (
    _send_sqr_for_revision_email,
    _send_sqr_new_submission_email,
    notify_engineer_assignment_email,
)


class PublicBaseUrlSettingsTests(SimpleTestCase):
    def load_settings(self, value=None, debug=False):
        environment = {
            "DJANGO_DEBUG": str(debug),
            "DJANGO_SECRET_KEY": "test-only-secret",
            "DB_HOST": "localhost",
            "DB_USER": "test",
            "DB_PASSWORD": "test",
        }
        if value is not None:
            environment["DJANGO_PUBLIC_BASE_URL"] = value
        settings_path = Path(__file__).resolve().parents[1] / "request_hub" / "settings.py"
        with patch.dict(os.environ, environment, clear=True), patch("dotenv.load_dotenv"):
            return runpy.run_path(str(settings_path))["PUBLIC_BASE_URL"]

    def test_default_and_normalized_override(self):
        self.assertEqual(self.load_settings(), "https://esgrequesthub.com")
        self.assertEqual(self.load_settings(" https://canonical.example.com/ "), "https://canonical.example.com")
        self.assertEqual(self.load_settings("http://localhost:8000/", debug=True), "http://localhost:8000")

    def test_invalid_origins_are_rejected(self):
        for value in (
            "", "esgrequesthub.com", "http://esgrequesthub.com", "https:///",
            "https://user:password@example.com", "https://example.com/path",
            "https://example.com?query=1", "https://example.com#fragment",
        ):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                self.load_settings(value)


@override_settings(
    PUBLIC_BASE_URL="https://esgrequesthub.com",
    ACS_EMAIL_CONNECTION_STRING="mock-connection",
    ACS_EMAIL_SENDER="DoNotReply@esgrequesthub.com",
)
class NotificationUrlTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            email="engineer@example.com", username="engineer", get_full_name=lambda: "Engineer"
        )
        self.submission = SimpleNamespace(
            engineer=self.user, pm_esg_reviewer=self.user, reference_code="SQR-1",
            customer_company="", created_at=None, updated_at=None,
            customer_name="Customer", project_title="Project", project_details="",
            review_notes="Update quotation",
        )

    def incoming_requests(self):
        return (
            None,
            RequestFactory().get("/", HTTP_HOST="retired.example.com"),
            RequestFactory().get("/", HTTP_HOST="app.azurewebsites.net"),
        )

    @patch("azure.communication.email.EmailClient.from_connection_string")
    def test_assignment_links_use_primary_origin_with_or_without_request(self, client_factory):
        request_obj = SimpleNamespace(
            engineer_id=1, engineer=self.user, backup_engineer_id=None,
            reference_code="REQ-1", due_date=None, account=None, description="",
            get_absolute_url=lambda: "/requests/1/",
            get_engagement_type_display=lambda: "Support",
            get_priority_display=lambda: "Medium",
        )
        for incoming in self.incoming_requests():
            with self.subTest(host=incoming.META.get("HTTP_HOST") if incoming else None):
                client_factory.reset_mock()
                result = notify_engineer_assignment_email(
                    request_obj, actor_user=self.user, request=incoming
                )
                self.assertEqual(result.status, "sent")
                client_factory.return_value.begin_send.assert_called_once()
                message = client_factory.return_value.begin_send.call_args.args[0]
                self.assertIn("https://esgrequesthub.com/requests/1/", message["content"]["plainText"])
                self.assertNotIn("retired.example.com", message["content"]["plainText"])
                self.assertNotIn("azurewebsites.net", message["content"]["plainText"])
                self.assertIn('href="https://esgrequesthub.com/requests/1/"', message["content"]["html"])
                self.assertNotIn("retired.example.com", message["content"]["html"])
                self.assertNotIn("azurewebsites.net", message["content"]["html"])

    @patch("azure.communication.email.EmailClient.from_connection_string")
    def test_sqr_links_use_primary_origin_with_or_without_request(self, client_factory):
        for incoming in self.incoming_requests():
            for send in (_send_sqr_new_submission_email, _send_sqr_for_revision_email):
                with self.subTest(sender=send.__name__, host=incoming.META.get("HTTP_HOST") if incoming else None):
                    args = (self.submission, "Reviewer") if send == _send_sqr_for_revision_email else (self.submission,)
                    client_factory.reset_mock()
                    send(*args, http_request=incoming)
                    client_factory.return_value.begin_send.assert_called_once()
                    message = client_factory.return_value.begin_send.call_args.args[0]
                    self.assertIn(f"https://esgrequesthub.com{reverse('hub:sqr')}", message["content"]["plainText"])
                    self.assertNotIn("retired.example.com", message["content"]["plainText"])
                    self.assertNotIn("azurewebsites.net", message["content"]["plainText"])
                    self.assertIn(f'href="https://esgrequesthub.com{reverse("hub:sqr")}"', message["content"]["html"])
                    self.assertNotIn("retired.example.com", message["content"]["html"])
                    self.assertNotIn("azurewebsites.net", message["content"]["html"])
