from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.middleware import IdleTimeoutMiddleware
from accounts.models import SignInLog, User
from accounts.views import SignInHeartbeatView


class IdleTimeoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="idle-timeout", password="test-password",
            role=User.Roles.ADMIN, must_change_password=False,
        )
        self.signed_in_at = timezone.now()
        with patch.object(timezone, "now", return_value=self.signed_in_at):
            self.client.force_login(self.user)

    def test_background_requests_do_not_extend_idle_deadline(self):
        dashboard = reverse("hub:dashboard")
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=5)):
            self.assertEqual(self.client.get(dashboard).status_code, 200)
            self.assertEqual(self.client.post(reverse("accounts:sign-in-heartbeat")).status_code, 204)
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=6)):
            response = self.client.get(dashboard)
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(SignInLog.objects.get(user=self.user).end_reason, SignInLog.EndReason.IDLE_TIMEOUT)

    def test_explicit_activity_renews_only_a_live_session(self):
        url = reverse("accounts:sign-in-heartbeat")
        headers = {"HTTP_X_REQUEST_HUB_ACTIVITY": "interactive", "HTTP_ACCEPT": "application/json"}
        for minutes, status in ((5, 204), (11, 401)):
            with self.subTest(minutes=minutes), patch.object(
                timezone, "now", return_value=self.signed_in_at + timedelta(minutes=minutes),
            ):
                response = self.client.post(url, **headers)
                self.assertEqual(response.status_code, status)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_idle_deadline_does_not_apply_when_opted_out(self):
        self.user.idle_timeout_enabled = False
        self.user.save(update_fields=["idle_timeout_enabled"])
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=20)):
            self.assertEqual(self.client.get(reverse("hub:dashboard")).status_code, 200)

    def test_stale_tab_cannot_log_out_after_timeout_is_disabled(self):
        response = self.client.post(
            reverse("accounts:update"),
            {"save_settings": "1"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("logout"), {"reason": "idle"})

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("X-Request-Hub-Idle-Remaining", response.headers)
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.user.pk))
        self.assertFalse(SignInLog.objects.filter(user=self.user, signed_out_at__isnull=False).exists())

    def test_stale_tab_cannot_log_out_a_session_renewed_in_another_tab(self):
        heartbeat = reverse("accounts:sign-in-heartbeat")
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=5)):
            self.client.post(heartbeat, HTTP_X_REQUEST_HUB_ACTIVITY="interactive")
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=6)):
            response = self.client.post(reverse("logout"), {"reason": "idle"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("X-Request-Hub-Idle-Remaining"), "300000")
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.user.pk))

    def test_unrelated_session_save_cannot_replace_a_renewed_deadline(self):
        stale_session = self.client.session
        stale_session["example_form_step"] = 2
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=5)):
            self.client.post(reverse("accounts:sign-in-heartbeat"), HTTP_X_REQUEST_HUB_ACTIVITY="interactive")
            stale_session.save()
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=6)):
            response = self.client.post(reverse("logout"), {"reason": "idle"}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("X-Request-Hub-Idle-Remaining"), "300000")
        self.assertEqual(self.client.session["example_form_step"], 2)

    def test_expiration_ignores_a_previously_loaded_session_snapshot(self):
        request = RequestFactory().post(reverse("logout"), {"reason": "idle"}, HTTP_ACCEPT="application/json")
        request.session, request.user = self.client.session, self.user
        self.assertIn("_auth_user_id", request.session)
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=5)):
            self.client.post(reverse("accounts:sign-in-heartbeat"), HTTP_X_REQUEST_HUB_ACTIVITY="interactive")
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=6)):
            response = IdleTimeoutMiddleware(lambda _: HttpResponse(status=204))(request)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("X-Request-Hub-Idle-Remaining"), "300000")

    def test_late_activity_cannot_renew_a_session_expired_by_another_request(self):
        heartbeat = reverse("accounts:sign-in-heartbeat")
        request = RequestFactory().post(
            heartbeat, HTTP_X_REQUEST_HUB_ACTIVITY="interactive", HTTP_ACCEPT="application/json",
        )
        request.session, request.user = self.client.session, self.user
        session_key = request.session.session_key

        def finish_after_expiry(pending):
            with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=6)):
                expiry = self.client.post(reverse("logout"), {"reason": "idle"}, HTTP_ACCEPT="application/json")
                self.assertEqual(expiry.status_code, 401)
                return SignInHeartbeatView.as_view()(pending)

        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=5)):
            response = IdleTimeoutMiddleware(finish_after_expiry)(request)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(Session.objects.filter(pk=session_key).exists())

    def test_activity_only_does_not_refresh_the_session_cookie_or_its_database_age(self):
        session_key = self.client.session.session_key
        expiry = Session.objects.get(pk=session_key).expire_date
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=5)):
            response = self.client.post(reverse("accounts:sign-in-heartbeat"), HTTP_X_REQUEST_HUB_ACTIVITY="interactive")
        self.assertEqual(response.status_code, 204)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertEqual(Session.objects.get(pk=session_key).expire_date, expiry)

    def test_user_initiated_navigation_keeps_ordinary_work_active(self):
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=5)):
            response = self.client.get(reverse("hub:dashboard"), HTTP_SEC_FETCH_MODE="navigate",
                                       HTTP_SEC_FETCH_SITE="same-origin", HTTP_SEC_FETCH_USER="?1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Request-Hub-Idle-Remaining"), "360000")

    def test_admin_form_includes_the_same_idle_controller(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("admin:password_change"))
        self.assertContains(response, 'id="aloStayBtn"')
        self.assertContains(response, reverse("accounts:sign-in-heartbeat"))

    def test_idle_logout_remains_enabled_when_preference_is_on(self):
        with patch.object(timezone, "now", return_value=self.signed_in_at + timedelta(minutes=6)):
            response = self.client.post(reverse("logout"), {"reason": "idle"})

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(SignInLog.objects.get(user=self.user).end_reason, SignInLog.EndReason.IDLE_TIMEOUT)

    def test_signed_out_tab_gets_json_denial_and_legacy_form_redirect(self):
        self.client.post(reverse("logout"))
        idle_post = {"reason": "idle"}
        self.assertEqual(self.client.post(reverse("logout"), idle_post, HTTP_ACCEPT="application/json").status_code, 401)
        self.assertRedirects(self.client.post(reverse("logout"), idle_post), reverse("login"))
        self.assertEqual(SignInLog.objects.get(user=self.user).end_reason, SignInLog.EndReason.SIGNED_OUT)

    def test_legacy_session_gets_a_window_without_rewriting_its_django_session(self):
        current = self.client.session
        legacy_session = SessionStore()
        legacy_session.update(dict(current))
        legacy_session.save()
        cookie = settings.SESSION_COOKIE_NAME
        self.client.cookies[cookie] = legacy_session.session_key
        response = self.client.get(reverse("hub:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Request-Hub-Idle-Remaining"), "360000")
        self.assertNotIn(cookie, response.cookies)

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
