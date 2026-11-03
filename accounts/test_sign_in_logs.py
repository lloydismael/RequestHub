from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import SignInLog, User
from accounts.sign_in_log import (
    ACTIVE_WINDOW,
    INVALID_CREDENTIALS_REASON,
    annotate_sign_in_logs,
    format_elapsed,
    parse_user_agent,
    sign_in_status,
)

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
EDGE_WINDOWS = CHROME_WINDOWS + " Edg/125.0.0.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0"
ANDROID_TABLET = (
    "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class UserAgentAndElapsedTests(SimpleTestCase):
    def test_parse_user_agent_identifies_browser_os_and_device(self):
        self.assertEqual(parse_user_agent(CHROME_WINDOWS), ("Chrome", "Windows", "Desktop"))
        self.assertEqual(parse_user_agent(EDGE_WINDOWS), ("Edge", "Windows", "Desktop"))
        self.assertEqual(parse_user_agent(SAFARI_IPHONE), ("Safari", "iOS", "Mobile"))
        self.assertEqual(parse_user_agent(FIREFOX_LINUX), ("Firefox", "Linux", "Desktop"))
        self.assertEqual(parse_user_agent(ANDROID_TABLET), ("Chrome", "Android", "Tablet"))
        self.assertEqual(parse_user_agent(""), ("", "", ""))

    def test_format_elapsed_is_specific(self):
        self.assertEqual(format_elapsed(5), "5s")
        self.assertEqual(format_elapsed(65), "1m 05s")
        self.assertEqual(format_elapsed(3725), "1h 02m 05s")


class SignInRecordingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="signin-user",
            password="pass12345",
            role=User.Roles.ENGINEER,
            first_name="Sam",
            last_name="Lee",
            department="ESS",
            email="sam@example.com",
        )

    def _login(self, password="pass12345"):
        return self.client.post(
            reverse("login"),
            {"username": "signin-user", "password": password},
            HTTP_USER_AGENT=CHROME_WINDOWS,
            REMOTE_ADDR="10.0.0.5",
        )

    def test_successful_sign_in_records_session_details(self):
        response = self._login()

        self.assertEqual(response.status_code, 302)
        log = SignInLog.objects.get()
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.username, "signin-user")
        self.assertEqual(log.role, User.Roles.ENGINEER)
        self.assertEqual(log.outcome, SignInLog.Outcome.SIGNED_IN)
        self.assertEqual(log.session_key, self.client.session.session_key)
        self.assertEqual(log.ip_address, "10.0.0.5")
        self.assertEqual((log.browser, log.operating_system, log.device_type), ("Chrome", "Windows", "Desktop"))
        self.assertIsNone(log.signed_out_at)
        self.assertEqual(sign_in_status(log, timezone.now()), "active")

    def test_failed_sign_in_is_recorded_without_user(self):
        response = self._login(password="wrong-password")

        self.assertEqual(response.status_code, 200)
        log = SignInLog.objects.get()
        self.assertIsNone(log.user)
        self.assertEqual(log.username, "signin-user")
        self.assertEqual(log.outcome, SignInLog.Outcome.FAILED)
        self.assertEqual(log.failure_reason, INVALID_CREDENTIALS_REASON)
        self.assertEqual(log.ip_address, "10.0.0.5")
        self.assertEqual(sign_in_status(log, timezone.now()), "failed")

    def test_sign_in_still_succeeds_when_activity_log_write_fails(self):
        with patch.object(SignInLog.objects, "create", side_effect=DatabaseError("log table unavailable")), \
                self.assertLogs("accounts.sign_in_log", level="ERROR"):
            response = self._login()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertFalse(SignInLog.objects.exists())

    def test_sign_out_records_end_time_and_elapsed_session(self):
        self._login()
        log = SignInLog.objects.get()
        SignInLog.objects.filter(pk=log.pk).update(started_at=timezone.now() - timedelta(minutes=5, seconds=3))

        self.client.post(reverse("logout"))

        log.refresh_from_db()
        self.assertIsNotNone(log.signed_out_at)
        self.assertEqual(log.end_reason, SignInLog.EndReason.SIGNED_OUT)
        [row] = annotate_sign_in_logs(SignInLog.objects.all(), timezone.now())
        self.assertEqual(row.status_label, "Signed out")
        self.assertGreaterEqual(row.elapsed_seconds, 303)
        self.assertLess(row.elapsed_seconds, 310)

    def test_idle_timeout_logout_is_labelled_separately(self):
        self._login()

        self.client.post(reverse("logout"), {"reason": "idle"})

        log = SignInLog.objects.get()
        self.assertEqual(log.end_reason, SignInLog.EndReason.IDLE_TIMEOUT)
        [row] = annotate_sign_in_logs(SignInLog.objects.all(), timezone.now())
        self.assertEqual(row.status_label, "Idle timeout")

    def test_heartbeat_endpoint_refreshes_last_seen_at_at_most_once_per_interval(self):
        self._login()
        log = SignInLog.objects.get()
        url = reverse("accounts:sign-in-heartbeat")
        stale = timezone.now() - ACTIVE_WINDOW - timedelta(minutes=1)
        SignInLog.objects.filter(pk=log.pk).update(last_seen_at=stale)

        self.assertEqual(self.client.post(url).status_code, 204)
        log.refresh_from_db()
        self.assertGreater(log.last_seen_at, stale)

        SignInLog.objects.filter(pk=log.pk).update(last_seen_at=stale)
        self.assertEqual(self.client.post(url).status_code, 204)
        log.refresh_from_db()
        self.assertEqual(log.last_seen_at, stale)

        self.assertEqual(self.client.get(url).status_code, 405)

    def test_heartbeat_requires_sign_in(self):
        response = self.client.post(reverse("accounts:sign-in-heartbeat"))

        self.assertEqual(response.status_code, 302)

    def test_plain_page_views_do_not_write_sign_in_logs(self):
        self._login()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("accounts:update"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("accounts:sign-in-heartbeat"))
        writes = [
            query["sql"]
            for query in queries.captured_queries
            if "accounts_signinlog" in query["sql"].lower() and not query["sql"].lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(writes, [])

    def test_session_without_sign_out_becomes_inactive_after_active_window(self):
        self._login()
        log = SignInLog.objects.get()
        last_seen = timezone.now() - ACTIVE_WINDOW - timedelta(minutes=2)
        SignInLog.objects.filter(pk=log.pk).update(last_seen_at=last_seen)
        log.refresh_from_db()

        now = timezone.now()
        self.assertEqual(sign_in_status(log, now), "inactive")
        [row] = annotate_sign_in_logs(SignInLog.objects.all(), now)
        self.assertEqual(row.status_label, "No sign-out")
        self.assertEqual(row.end_at, last_seen)


class ActivityLogsTabTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="activity-admin", password="pass12345", role=User.Roles.ADMIN)
        self.engineer = User.objects.create_user(
            username="activity-engineer",
            password="pass12345",
            role=User.Roles.ENGINEER,
            first_name="Ana",
            last_name="Cruz",
            department="HP",
        )
        self.client.force_login(self.admin)
        # force_login records its own sign-in; start from a known set of rows.
        SignInLog.objects.all().delete()

        now = timezone.now()
        self.live = SignInLog.objects.create(
            user=self.engineer,
            username=self.engineer.username,
            role=self.engineer.role,
            session_key="live-session",
            started_at=now - timedelta(seconds=90),
            last_seen_at=now,
            ip_address="10.0.0.9",
            user_agent=CHROME_WINDOWS,
            browser="Chrome",
            operating_system="Windows",
            device_type="Desktop",
        )
        ended_start = now - timedelta(hours=2)
        self.ended = SignInLog.objects.create(
            user=self.engineer,
            username=self.engineer.username,
            role=self.engineer.role,
            session_key="ended-session",
            started_at=ended_start,
            last_seen_at=ended_start + timedelta(hours=1, minutes=2, seconds=5),
            signed_out_at=ended_start + timedelta(hours=1, minutes=2, seconds=5),
            end_reason=SignInLog.EndReason.IDLE_TIMEOUT,
            ip_address="10.0.0.20",
            browser="Firefox",
            operating_system="Linux",
            device_type="Desktop",
        )
        self.failed = SignInLog.objects.create(
            username="ghost-user",
            outcome=SignInLog.Outcome.FAILED,
            failure_reason=INVALID_CREDENTIALS_REASON,
            started_at=now - timedelta(minutes=3),
            ip_address="203.0.113.7",
            browser="Edge",
            operating_system="Windows",
            device_type="Desktop",
        )

    def _activity(self, **params):
        return self.client.get(reverse("hub:management"), {"tab": "activity", **params})

    def test_activity_tab_renders_rows_with_specific_elapsed_time(self):
        response = self._activity()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "activity")
        self.assertRegex(response.content.decode(), r'class="tab-pane show active"\s+id="activity-pane"')
        self.assertContains(response, 'id="activity-tab"')
        self.assertContains(response, "10.0.0.9")
        self.assertContains(response, "203.0.113.7")
        self.assertContains(response, "1h 02m 05s")
        self.assertContains(response, "Idle timeout")
        self.assertContains(response, "Ana Cruz")
        self.assertContains(response, 'data-live-elapsed="')
        live_row = next(row for row in response.context["activity_logs_page_obj"].object_list if row.pk == self.live.pk)
        self.assertGreaterEqual(live_row.elapsed_seconds, 90)
        self.assertLess(live_row.elapsed_seconds, 96)

    def test_status_filter_limits_rows(self):
        response = self._activity(activity_status="failed")

        rows = response.context["activity_logs_page_obj"].object_list
        self.assertEqual([row.pk for row in rows], [self.failed.pk])
        self.assertEqual(response.context["activity_status"], "failed")

    def test_search_matches_ip_and_username(self):
        by_ip = self._activity(activity_q="10.0.0.20")
        self.assertEqual([row.pk for row in by_ip.context["activity_logs_page_obj"].object_list], [self.ended.pk])

        by_name = self._activity(activity_q="ghost")
        self.assertEqual([row.pk for row in by_name.context["activity_logs_page_obj"].object_list], [self.failed.pk])

    def test_summary_counts_active_sessions_and_recent_activity(self):
        summary = self._activity().context["activity_summary"]

        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["signed_in_24h"], 2)
        self.assertEqual(summary["failed_24h"], 1)
        self.assertEqual(summary["total"], 3)

    def test_non_admin_cannot_open_activity_logs(self):
        self.client.force_login(self.engineer)

        response = self.client.get(reverse("hub:management"), {"tab": "activity"})

        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b'id="activity-pane"', response.content)
