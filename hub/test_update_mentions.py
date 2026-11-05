from unittest.mock import patch

from django.core import mail
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from hub.models import Account, Notification, Request, StatusLog


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", ACS_EMAIL_CONNECTION_STRING="", ACS_EMAIL_SENDER="")
class UpdateMentionTests(TestCase):
    def setUp(self):
        User.objects.filter(role__in=[User.Roles.ADMIN, User.Roles.PM_ESG]).update(is_active=False)
        self.requestor = User.objects.create_user(username="mention-owner", role=User.Roles.REQUESTOR, email="owner@example.com")
        self.engineer = User.objects.create_user(username="mention-engineer", role=User.Roles.ENGINEER, email="engineer@example.com", first_name="Assigned", last_name="Engineer")
        self.backup = User.objects.create_user(username="mention-backup", role=User.Roles.ENGINEER, email="backup@example.com")
        self.admin = User.objects.create_user(username="mention-admin", role=User.Roles.ADMIN, email="admin@example.com")
        self.outsider = User.objects.create_user(username="mention-outsider", role=User.Roles.ENGINEER, email="outsider@example.com")
        self.inactive = User.objects.create_user(username="mention-inactive", role=User.Roles.ADMIN, is_active=False)
        self.obj = Request.objects.create(requestor=self.requestor, engineer=self.engineer, backup_engineer=self.backup, account=Account.objects.create(name="Mention Account"), account_manager="Owner", product_category="Azure", engagement_type=Request.Engagement.SUPPORT)
        self.url = reverse("hub:request-manage-collab", args=[self.obj.pk])
        self.client.force_login(self.requestor)

    def post_update(self, message, url=None):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url or self.url, {"form_type": "status_log", "message": message})

    def test_composer_lists_only_active_request_people_and_administrators(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("mention_people", response.context)
        usernames = {person["username"] for person in response.context["mention_people"]}
        self.assertEqual(usernames, {"mention-engineer", "mention-backup", "mention-admin"})
        self.assertContains(response, "data-mention-composer")

    def test_mention_creates_one_notification_email_and_toast_event(self):
        response = self.post_update("Please review @mention-engineer and @mention-engineer.")
        self.assertEqual(response.status_code, 302)
        notification = Notification.objects.get(recipient=self.engineer, related_request=self.obj)
        self.assertEqual(notification.event_type, "mention")
        self.assertEqual(Notification.objects.filter(recipient=self.engineer).count(), 1)
        self.assertTrue(Notification.objects.filter(recipient=self.backup).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["engineer@example.com"])
        self.assertIn("Please review", mail.outbox[0].body)
        self.assertIn(self.obj.reference_code, mail.outbox[0].subject)
        self.client.force_login(self.engineer)
        events = self.client.get(reverse("hub:notification-toast-data"), {"cursor": 0}).json()["events"]
        self.assertEqual([item["type"] for item in events], ["mention"])

    def test_unrelated_and_inactive_mentions_are_rejected_without_side_effects(self):
        for username in (self.outsider.username, self.inactive.username, "does-not-exist"):
            with self.subTest(username=username):
                response = self.post_update(f"Hello @{username}")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["log_form"].errors)
                self.assertEqual(StatusLog.objects.filter(request=self.obj).count(), 0)
                self.assertEqual(Notification.objects.count(), 0)
                self.assertEqual(len(mail.outbox), 0)

    def test_email_addresses_and_ordinary_updates_do_not_generate_mentions(self):
        response = self.post_update("Contact support@example.com for assistance.")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(Notification.objects.filter(recipient=self.backup).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.outsider).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.inactive).exists())

    def test_self_mention_is_rejected_without_saving_or_notifying(self):
        response = self.post_update("Reminder for @mention-owner")
        self.assertEqual(response.status_code, 200)
        self.assertIn("yourself", response.context["log_form"].errors["message"][0])
        self.assertEqual(StatusLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_self_mention_is_also_restricted_on_admin_and_edit_forms(self):
        self.client.force_login(self.admin)
        url = reverse("hub:request-manage", args=[self.obj.pk])
        response = self.client.get(url)
        self.assertNotIn("mention-admin", {person["username"] for person in response.context["mention_people"]})
        self.assertEqual(self.post_update("Hello @mention-admin", url).status_code, 200)
        self.client.force_login(self.requestor)
        self.post_update("Original update")
        log = StatusLog.objects.get(request=self.obj)
        edit_url = reverse("hub:status-log-edit", args=[log.pk])
        response = self.client.get(edit_url)
        self.assertNotIn("mention-owner", {person["username"] for person in response.context["form"].mention_people})
        self.assertEqual(self.post_update("Hello @mention-owner", edit_url).status_code, 200)
        log.refresh_from_db()
        self.assertEqual(log.message, "Original update")

    def test_mention_count_matches_notification_page_for_non_admin(self):
        self.post_update("Please review @mention-engineer")
        self.client.force_login(self.engineer)
        page = self.client.get(reverse("hub:notifications"), {"category": "mention"})
        self.assertEqual(page.context["new_ticket_count"], page.context["unread_count"])
        self.assertEqual(page.context["new_ticket_count"], 1)
        endpoint = reverse("hub:notification-toast-data")
        payload = self.client.get(endpoint, {"cursor": 0}).json()
        self.assertEqual(payload["unread_count"], 1)
        self.assertEqual(payload["total_count"], 1)
        event_id = payload["events"][0]["id"]
        Notification.objects.filter(pk=event_id).update(is_read=True)
        refreshed = self.client.get(endpoint, {"cursor": event_id}).json()
        self.assertEqual(refreshed["unread_count"], 0)
        self.assertEqual(refreshed["total_count"], 1)
        self.assertEqual(refreshed["events"], [])
        baseline = self.client.get(endpoint).json()
        self.assertEqual(baseline["unread_count"], 0)

    def test_edit_notifies_new_mentions_only_and_never_repeats_a_nudge(self):
        self.post_update("Please check @mention-engineer")
        log = StatusLog.objects.get(request=self.obj)
        edit_url = reverse("hub:status-log-edit", args=[log.pk])
        self.post_update("Please check @mention-engineer and @mention-backup", edit_url)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(Notification.objects.filter(recipient=self.backup, event_type="mention").count(), 1)
        self.post_update("Please check @mention-backup", edit_url)
        self.post_update("Please check @mention-engineer and @mention-backup", edit_url)
        self.assertEqual(len(mail.outbox), 2)

    def test_admin_composer_and_post_use_the_same_scope(self):
        self.client.force_login(self.admin)
        url = reverse("hub:request-manage", args=[self.obj.pk])
        response = self.client.get(url)
        self.assertIn("mention_people", response.context)
        self.assertEqual(self.post_update("Please check @mention-backup", url).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["backup@example.com"])

    def test_email_style_usernames_can_be_mentioned(self):
        person = User.objects.create_user(username="mention-email@example.com", role=User.Roles.ADMIN, email="mentioned@example.com")
        response = self.post_update("Please review @mention-email@example.com.")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Notification.objects.filter(recipient=person, event_type="mention").exists())
        self.assertEqual(mail.outbox[0].to, ["mentioned@example.com"])

    def test_admin_mentions_are_visible_in_notification_center_and_badge(self):
        self.post_update("Please review @mention-admin")
        notification = Notification.objects.get(recipient=self.admin, event_type="mention")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("hub:notifications"), {"category": "mention"})
        self.assertContains(response, notification.message)
        self.assertEqual(response.context["unread_count"], 1)
        self.assertEqual(response.context["new_ticket_count"], 1)

    def test_mention_toast_cursor_includes_mentions_on_page_load(self):
        self.post_update("Please review @mention-engineer")
        notification = Notification.objects.get(recipient=self.engineer, event_type="mention")
        self.client.force_login(self.engineer)
        response = self.client.get(self.url)
        self.assertEqual(response.context["notification_toast_initial_cursor"], notification.pk)

    def test_mention_follow_opens_the_specific_update(self):
        self.post_update("Please review @mention-engineer")
        log = StatusLog.objects.get(request=self.obj)
        notification = Notification.objects.get(recipient=self.engineer, event_type="mention")
        self.client.force_login(self.engineer)
        response = self.client.get(reverse("hub:notification-follow", args=[notification.pk]))
        self.assertEqual(response["Location"], f"{self.url}#update-{log.pk}")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_missing_email_configuration_warns_without_losing_update(self):
        with self.assertLogs("hub.services.update_mentions", level="WARNING"):
            response = self.post_update("Please review @mention-engineer")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusLog.objects.filter(request=self.obj).count(), 1)
        self.assertTrue(Notification.objects.filter(recipient=self.engineer, event_type="mention").exists())
        feedback = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("email could not be sent" in message for message in feedback))

    def test_email_failure_does_not_lose_the_update_or_notification(self):
        with patch("hub.services.update_mentions.EmailMultiAlternatives.send", side_effect=RuntimeError("Mail delivery unavailable")), self.assertLogs("hub.services.update_mentions", level="ERROR"):
            response = self.post_update("Please review @mention-backup")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusLog.objects.filter(request=self.obj).count(), 1)
        self.assertTrue(Notification.objects.filter(recipient=self.backup, event_type="mention").exists())

    @override_settings(ACS_EMAIL_CONNECTION_STRING="test-configuration", ACS_EMAIL_SENDER="hub@example.com")
    def test_acs_email_contains_only_the_mentioned_recipient_and_safe_content(self):
        with patch("azure.communication.email.EmailClient.from_connection_string") as client:
            client.return_value.begin_send.return_value.result.return_value = {"status": "Succeeded"}
            response = self.post_update("Please check @mention-engineer <script>alert(1)</script>")
        self.assertEqual(response.status_code, 302)
        payload = client.return_value.begin_send.call_args.args[0]
        self.assertEqual(payload["recipients"]["to"], [{"address": "engineer@example.com"}])
        self.assertIn("Please check", payload["content"]["plainText"])
        self.assertIn("&lt;script&gt;", payload["content"]["html"])
        self.assertNotIn("<script>", payload["content"]["html"])
        log = StatusLog.objects.get(request=self.obj)
        self.assertIn(f"#update-{log.pk}", payload["content"]["plainText"])

    def test_missing_recipient_email_preserves_in_app_notification(self):
        self.engineer.email = ""
        self.engineer.save(update_fields=["email"])
        response = self.post_update("Please review @mention-engineer")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Notification.objects.filter(recipient=self.engineer, event_type="mention").exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(any("email could not be sent" in str(message) for message in get_messages(response.wsgi_request)))

    def test_removed_participant_cannot_edit_old_updates(self):
        self.client.force_login(self.engineer)
        self.post_update("Engineer update")
        log = StatusLog.objects.get(request=self.obj)
        self.obj.engineer = None
        self.obj.save(update_fields=["engineer"])
        response = self.client.post(reverse("hub:status-log-edit", args=[log.pk]), {"message": "Changed"})
        self.assertEqual(response.status_code, 404)
        log.refresh_from_db()
        self.assertEqual(log.message, "Engineer update")

    def test_unrelated_user_cannot_post_or_read_candidates(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.post_update("Hello @mention-engineer").status_code, 302)
        self.assertEqual(StatusLog.objects.count(), 0)
