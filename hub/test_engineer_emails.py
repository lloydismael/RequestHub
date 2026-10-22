from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.utils.html import escape

from hub.views import _send_sqr_for_revision_email, notify_engineer_assignment_email


@override_settings(
    PUBLIC_BASE_URL="https://esgrequesthub.com",
    ACS_EMAIL_CONNECTION_STRING="mock-connection",
    ACS_EMAIL_SENDER="DoNotReply@esgrequesthub.com",
)
class EngineerEmailTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            email="engineer@example.com", username="engineer",
            get_full_name=lambda: "Engineer & Team",
        )
        self.request_obj = SimpleNamespace(
            engineer_id=1, engineer=self.user, backup_engineer_id=2,
            backup_engineer=SimpleNamespace(email="backup@example.com"),
            reference_code="REQ-1", account=SimpleNamespace(name="Account & Co"),
            due_date=datetime(2026, 10, 25), description="First line\nSecond line",
            get_absolute_url=lambda: "/requests/1/",
            get_engagement_type_display=lambda: "Support",
            get_priority_display=lambda: "High",
        )
        self.submission = SimpleNamespace(
            engineer=self.user, reference_code="SQR-1", customer_name="Customer & Co",
            customer_company="Company Group", project_title="Network upgrade",
            updated_at=datetime(2026, 10, 22, 10, 30),
            review_notes="Update scope\nResubmit quotation",
        )

    def send_assignment(self, **kwargs):
        with patch("azure.communication.email.EmailClient.from_connection_string") as factory:
            result = notify_engineer_assignment_email(self.request_obj, actor_user=self.user, **kwargs)
            factory.return_value.begin_send.assert_called_once()
            factory.return_value.begin_send.return_value.result.assert_called_once()
            return result, factory.return_value.begin_send.call_args.args[0]

    def send_revision(self, reviewer="Reviewer"):
        with patch("azure.communication.email.EmailClient.from_connection_string") as factory:
            _send_sqr_for_revision_email(self.submission, reviewer)
            factory.return_value.begin_send.assert_called_once()
            factory.return_value.begin_send.return_value.result.assert_called_once()
            return factory.return_value.begin_send.call_args.args[0]

    def test_assignment_contains_html_and_unchanged_text_and_recipients(self):
        result, message = self.send_assignment()
        self.assertEqual(result.status, "sent")
        self.assertEqual(message["recipients"]["to"], [
            {"address": "engineer@example.com"}, {"address": "backup@example.com"},
        ])
        self.assertEqual(message["content"]["subject"], "[Request Hub] New assignment - REQ-1")
        self.assertEqual(message["content"]["plainText"], (
            "You have been assigned a request in Request Hub.\n\n"
            "Reference: REQ-1\nAccount: Account & Co\nEngagement: Support\nPriority: High\n"
            "Due date: Oct 25, 2026\nAssigned by: Engineer & Team\n\n"
            "Description:\nFirst line\nSecond line\n\nView request: https://esgrequesthub.com/requests/1/"
        ))
        markup = message["content"]["html"]
        for value in ("New Request Assignment", "REQ-1", "Account &amp; Co", "High", "Oct 25, 2026", "View Request", "First line<br>Second line"):
            self.assertIn(value, markup)
        self.assertEqual(markup.count('href="https://esgrequesthub.com/requests/1/"'), 2)

    def test_assignment_missing_fields_and_unchanged_assignee_filter(self):
        self.request_obj.account = None
        self.request_obj.due_date = None
        self.request_obj.description = "  "
        _, message = self.send_assignment(previous_backup_id=2)
        self.assertEqual(message["recipients"]["to"], [{"address": "engineer@example.com"}])
        self.assertIn("Not set", message["content"]["html"])
        self.assertNotIn(">Description</h2>", message["content"]["html"])

    def test_revision_contains_comments_details_and_action(self):
        message = self.send_revision()
        self.assertEqual(message["recipients"]["to"], [{"address": "engineer@example.com"}])
        self.assertEqual(message["content"]["subject"], "[Request Hub] SQR-1 — Action Required: For Revision")
        markup = message["content"]["html"]
        for value in ("SQR Requires Revision", "For Revision", "Customer &amp; Co", "Company Group", "Network upgrade", "Reviewer", "October 22, 2026", "Update scope<br>Resubmit quotation", "Next steps", "Open SQR Tracker"):
            self.assertIn(value, markup)
        self.assertIn("Update scope\nResubmit quotation", message["content"]["plainText"])
        self.assertEqual(markup.count('href="https://esgrequesthub.com/sqr/"'), 2)

    def test_revision_missing_comments_company_and_date(self):
        self.submission.review_notes = " "
        self.submission.customer_company = " "
        self.submission.updated_at = None
        message = self.send_revision()
        for body in (message["content"]["html"], message["content"]["plainText"]):
            self.assertIn("No specific comments were provided. Please follow up with the reviewer for details.", body)
            self.assertNotIn("Company/Group", body)
        self.assertIn("—", message["content"]["html"])

    def test_untrusted_content_is_escaped_and_long_multiline_text_is_preserved(self):
        unsafe = '<img src=x onerror="alert(1)"> & <script>alert(1)</script>'
        long_text = unsafe + "\n" + "LongWord" * 400 + "\nLast line"
        self.request_obj.description = long_text
        self.request_obj.account.name = unsafe
        self.request_obj.reference_code = unsafe
        _, assignment = self.send_assignment()
        self.submission.review_notes = long_text
        self.submission.customer_name = unsafe
        self.submission.customer_company = unsafe
        self.submission.project_title = unsafe
        self.submission.reference_code = unsafe
        self.user.get_full_name = lambda: unsafe
        revision = self.send_revision(reviewer=unsafe)
        for message in (assignment, revision):
            markup = message["content"]["html"]
            self.assertNotIn("<script>", markup)
            self.assertNotIn("<img src=x", markup)
            self.assertIn(str(escape(unsafe)), markup)
            self.assertIn("LongWord" * 400, markup)
            self.assertIn("<br>Last line", markup)
            self.assertIn(long_text, message["content"]["plainText"])

    def test_unchanged_delivery_failure_and_skipped_email_behavior(self):
        with patch("azure.communication.email.EmailClient.from_connection_string") as factory:
            factory.return_value.begin_send.side_effect = RuntimeError("test delivery failure")
            self.assertEqual(notify_engineer_assignment_email(self.request_obj, actor_user=self.user).status, "delivery_failed")
        with patch("azure.communication.email.EmailClient.from_connection_string") as factory:
            self.assertEqual(notify_engineer_assignment_email(
                self.request_obj, actor_user=self.user, previous_engineer_id=1, previous_backup_id=2,
            ).status, "no_new_assignee")
            self.user.email = ""
            _send_sqr_for_revision_email(self.submission, "Reviewer")
            factory.assert_not_called()
