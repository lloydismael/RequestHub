from django.test import TestCase
from django.urls import reverse

from accounts.models import SignInLog, User


class IdleTimeoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="idle-timeout", password="test-password",
            role=User.Roles.ADMIN, must_change_password=False,
        )
        self.client.force_login(self.user)

    def test_stale_tab_cannot_log_out_after_timeout_is_disabled(self):
        response = self.client.post(
            reverse("accounts:update"),
            {"save_settings": "1"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("logout"), {"reason": "idle"})

        self.assertRedirects(response, reverse("accounts:update"))
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.user.pk))
        self.assertFalse(SignInLog.objects.filter(user=self.user, signed_out_at__isnull=False).exists())

    def test_idle_logout_remains_enabled_when_preference_is_on(self):
        response = self.client.post(reverse("logout"), {"reason": "idle"})

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(SignInLog.objects.get(user=self.user).end_reason, SignInLog.EndReason.IDLE_TIMEOUT)

    def test_manual_logout_still_works_when_timeout_is_disabled(self):
        self.user.idle_timeout_enabled = False
        self.user.save(update_fields=["idle_timeout_enabled"])

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_saved_settings_response_reports_authoritative_timeout_preference(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                data = {"save_settings": "1"}
                if enabled:
                    data["idle_timeout_enabled"] = "1"
                response = self.client.post(
                    reverse("accounts:update"), data, HTTP_ACCEPT="application/json",
                )
                self.assertIs(response.json().get("idle_timeout_enabled"), enabled)
