from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.utils.html import escape

from hub.views import _send_sqr_new_submission_email


@override_settings(
    PUBLIC_BASE_URL="https://esgrequesthub.com",
    ACS_EMAIL_CONNECTION_STRING="mock-connection",
    ACS_EMAIL_SENDER="DoNotReply@esgrequesthub.com",
)
class PmEsgEmailTests(SimpleTestCase):
    def setUp(self):
        self.reviewer = SimpleNamespace(
            email="reviewer@example.com", username="reviewer",
            get_full_name=lambda: "PM Reviewer",
        )
        self.submission = SimpleNamespace(
            pm_esg_reviewer=self.reviewer,
            engineer=SimpleNamespace(username="engineer", get_full_name=lambda: "Engineer & Team"),
            reference_code="SQR-1", customer_name="Customer & Co",
            customer_company="Company Group", project_title="Network upgrade",
            project_details="Review network\nUpdate quotation",
            created_at=datetime(2026, 10, 22, 20, 30, tzinfo=timezone.utc),
        )

    def send(self):
        with patch("azure.communication.email.EmailClient.from_connection_string") as factory:
            _send_sqr_new_submission_email(self.submission)
            factory.return_value.begin_send.assert_called_once()
            factory.return_value.begin_send.return_value.result.assert_called_once()
            return factory.return_value.begin_send.call_args.args[0]

    def test_html_summary_scope_action_and_existing_text(self):
        message = self.send()
        self.assertEqual(message["recipients"]["to"], [{"address": "reviewer@example.com"}])
        self.assertEqual(message["senderAddress"], "DoNotReply@esgrequesthub.com")
        self.assertEqual(message["content"]["subject"], "[Request Hub] New SQR Submitted — SQR-1 (Customer & Co)")
        markup = message["content"]["html"]
        for value in (
            "New SQR Assigned for Review", "Hi PM Reviewer,", "Review requested", "SQR-1",
            "Engineer &amp; Team", "Customer &amp; Co", "Company Group", "Network upgrade",
            "Date Submitted (Manila)", "October 23, 2026  04:30 AM",
            "Scope of services", "Review network<br>Update quotation", "Next steps",
            "Approve or request revision with your comments.", "Open SQR Tracker",
            "Please do not reply directly to this message.",
        ):
            self.assertIn(value, markup)
        self.assertEqual(markup.count('href="https://esgrequesthub.com/sqr/"'), 2)
        divider = "─" * 56
        self.assertEqual(message["content"]["plainText"], "\n".join([
            "Hi PM Reviewer,", "",
            "A new SQR submission has been created and assigned to you for review.",
            "Please review it at your earliest convenience.", "", divider,
            "  SUBMISSION DETAILS", divider,
            "  Reference:      SQR-1", "  Submitted By:   Engineer & Team",
            "  Customer:       Customer & Co", "  Company/Group:  Company Group",
            "  Project Title:  Network upgrade", "  Scope of Svc:   Review network\nUpdate quotation",
            "  Date Submitted: October 23, 2026  04:30 AM", "", divider,
            "  NEXT STEPS", divider,
            "  1. Log in to Request Hub and open the SQR Review page.",
            "  2. Review the submission details and quotation.",
            "  3. Approve or request revision with your comments.", "",
            "  View SQR Tracker:  https://esgrequesthub.com/sqr/", "", divider,
            "This is an automated notification from Request Hub.",
            "Please do not reply directly to this message.",
        ]))

    def test_username_email_fallback_and_missing_optional_fields(self):
        self.reviewer.email = " "
        self.reviewer.username = "pm@example.com"
        self.reviewer.get_full_name = lambda: ""
        self.submission.engineer.get_full_name = lambda: ""
        self.submission.customer_company = " "
        self.submission.created_at = None
        self.submission.project_details = ""
        message = self.send()
        self.assertEqual(message["recipients"]["to"], [{"address": "pm@example.com"}])
        for body in (message["content"]["html"], message["content"]["plainText"]):
            self.assertIn("Hi pm@example.com,", body)
            self.assertIn("engineer", body)
            self.assertNotIn("Company/Group", body)
            self.assertIn("—", body)

    def test_user_content_escaped_without_truncating_multiline_scope(self):
        unsafe = '<img src=x onerror="alert(1)"> & <script>alert(1)</script>'
        scope = unsafe + "\n" + "LongWord" * 400 + "\nLast line"
        self.submission.project_details = scope
        self.submission.reference_code = unsafe
        self.submission.customer_name = unsafe
        self.submission.customer_company = unsafe
        self.submission.project_title = unsafe
        self.reviewer.get_full_name = lambda: unsafe
        self.submission.engineer.get_full_name = lambda: unsafe
        message = self.send()
        markup = message["content"]["html"]
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<img src=x", markup)
        self.assertIn("Hi " + str(escape(unsafe)), markup)
        self.assertGreaterEqual(markup.count(str(escape(unsafe))), 7)
        self.assertIn("LongWord" * 400 + "<br>Last line", markup)
        self.assertIn(scope, message["content"]["plainText"])

    @patch("azure.communication.email.EmailClient.from_connection_string")
    def test_missing_reviewer_or_email_skips_delivery(self, factory):
        self.submission.pm_esg_reviewer = None
        _send_sqr_new_submission_email(self.submission)
        self.submission.pm_esg_reviewer = self.reviewer
        self.reviewer.email = ""
        _send_sqr_new_submission_email(self.submission)
        factory.assert_not_called()

    @override_settings(ACS_EMAIL_CONNECTION_STRING="")
    @patch("azure.communication.email.EmailClient.from_connection_string")
    def test_missing_configuration_skips_delivery(self, factory):
        _send_sqr_new_submission_email(self.submission)
        factory.assert_not_called()

    @patch("azure.communication.email.EmailClient.from_connection_string")
    def test_delivery_failure_is_logged_without_raising(self, factory):
        factory.return_value.begin_send.side_effect = RuntimeError("test delivery failure")
        with self.assertLogs("hub.views", level="WARNING") as logs:
            _send_sqr_new_submission_email(self.submission)
        self.assertIn("SQR new-submission email send failed", logs.output[0])
