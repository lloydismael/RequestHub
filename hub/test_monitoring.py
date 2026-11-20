import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from requests import Response, Timeout
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from accounts.models import User


@override_settings(AZURE_MONITOR_POSTGRES_RESOURCE_ID="", AZURE_MONITOR_WEB_APP_RESOURCE_ID="")
class MonitoringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="monitoring-admin", role=User.Roles.ADMIN, must_change_password=False,
        )
        cls.reader = User.objects.create_user(
            username="monitoring-reader", role=User.Roles.REQUESTOR, is_staff=True, must_change_password=False,
        )

    def test_only_request_hub_admins_can_fetch_live_diagnostics(self):
        url = "/management/monitoring/live/"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url).status_code, 405)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])

    def test_live_diagnostics_read_real_postgresql_and_exclude_monitoring_polls(self):
        self.client.force_login(self.admin)
        url = reverse("hub:monitoring-live")
        baseline = self.client.get(url).json()
        self.client.get(reverse("healthz"))
        self.client.get(reverse("readyz"))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["app"]["requests"], baseline["app"]["requests"])
        baseline = response.json()
        self.assertEqual(baseline["database"]["status"], "reachable")
        self.assertGreaterEqual(baseline["database"]["latency_ms"], 0)
        self.assertGreater(baseline["database"]["size_bytes"], 0)
        self.assertIsNotNone(parse_datetime(baseline["database"]["sampled_at"]))
        self.assertIsInstance(baseline["database"]["lock_waits"], int)

        self.client.get(reverse("hub:management"), {"tab": "changelogs"})
        for _ in range(2):
            update = self.client.get(url).json()
            self.assertEqual(update["app"]["requests"], baseline["app"]["requests"] + 1)
            self.assertGreater(update["app"]["p95_ms"], 0)

    def test_database_probe_failure_has_no_connection_details(self):
        self.client.force_login(self.admin)
        with patch("hub.monitoring._query", side_effect=OperationalError("private-db.internal credentials")):
            response = self.client.get("/management/monitoring/live/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"]["status"], "unreachable")
        self.assertIsNone(response.json()["database"]["latency_ms"])
        self.assertNotContains(response, "private-db.internal")

    def test_failed_statistics_keep_the_database_ping_and_app_measurements(self):
        self.client.force_login(self.admin)
        with patch("hub.monitoring._query", side_effect=[((1,), 0.5), OperationalError("private-db.internal")]):
            response = self.client.get(reverse("hub:monitoring-live"))
        payload = response.json()
        self.assertEqual(payload["database"]["status"], "partial")
        self.assertGreaterEqual(payload["database"]["latency_ms"], 0)
        self.assertIsNone(payload["database"]["size_bytes"])
        self.assertIn("requests", payload["app"])
        self.assertNotContains(response, "private-db.internal")


    def test_platform_requires_admin_and_does_not_query_unconfigured_resources(self):
        url = "/management/monitoring/platform/"
        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.json()["postgres"]["status"], "unconfigured")
        self.assertEqual(response.json()["web_app"]["status"], "unconfigured")


POSTGRES_ID = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example/providers/Microsoft.DBforPostgreSQL/flexibleServers/example-db"


class PlatformMetricsTests(SimpleTestCase):
    @staticmethod
    def metric(name, aggregation, **samples):
        return {
            "name": {"value": name}, "errorCode": "Success",
            "timeseries": [{"data": [{"timeStamp": time, aggregation: value} for time, value in samples.items()]}],
        }

    def test_missing_and_stale_samples_do_not_hide_a_reported_zero(self):
        from hub.monitoring_azure import POSTGRES, NORMAL_REFRESH_SECONDS, AzureMetrics

        now = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)
        recent = (now - timedelta(minutes=2)).isoformat()
        old = (now - timedelta(minutes=10)).isoformat()
        provider = AzureMetrics(POSTGRES)
        with patch("hub.monitoring_azure.read_metrics", return_value=([
            self.metric("cpu_percent", "average", **{recent: 0, now.isoformat(): None}),
            self.metric("storage_used", "average", **{old: 21474836480}),
        ], {})) as read:
            result = provider.snapshot(POSTGRES_ID, now=now, tick=0)
            provider.snapshot(POSTGRES_ID, now=now + timedelta(seconds=25), tick=25)
            self.assertEqual(read.call_count, 1)
            with patch("hub.monitoring_azure.read_metrics", side_effect=Timeout("private details")) as failed:
                after_failure = provider.snapshot(POSTGRES_ID, now=now + timedelta(minutes=1), tick=NORMAL_REFRESH_SECONDS)
                provider.snapshot(POSTGRES_ID, now=now + timedelta(minutes=2), tick=NORMAL_REFRESH_SECONDS * 2)
                self.assertEqual(failed.call_count, 1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["metrics"]["cpu_percent"], {
            "status": "current", "value": 0, "sampled_at": recent, "history": [[recent, 0]],
        })
        self.assertEqual(result["metrics"]["storage_used"]["status"], "stale")
        self.assertEqual(result["metrics"]["storage_free"]["status"], "missing")
        self.assertEqual(after_failure["status"], "delayed")
        self.assertEqual(after_failure["metrics"]["cpu_percent"]["value"], 0)

    @override_settings(
        AZURE_MONITOR_POSTGRES_RESOURCE_ID=POSTGRES_ID,
        AZURE_MONITOR_WEB_APP_RESOURCE_ID="/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example/providers/Microsoft.Web/sites/example-app",
    )
    def test_concurrent_requests_share_one_nonblocking_azure_refresh_slot(self):
        from hub.monitoring_azure import POSTGRES, WEB_APP, AzureMetrics, platform_snapshot

        entered, release = Event(), Event()

        def read(resource_id, resource, now):
            if resource is POSTGRES:
                entered.set()
                if not release.wait(5):
                    raise Timeout("Test did not release the PostgreSQL Azure request")
                return [self.metric("cpu_percent", "average", **{now.isoformat(): 12})], {}
            return [self.metric("Requests", "total", **{now.isoformat(): 42})], {}

        with (
            patch("hub.monitoring_azure._postgres", AzureMetrics(POSTGRES)),
            patch("hub.monitoring_azure._web_app", AzureMetrics(WEB_APP)),
            patch("hub.monitoring_azure.read_metrics", side_effect=read),
            ThreadPoolExecutor(max_workers=2) as worker,
        ):
            first = worker.submit(platform_snapshot)
            try:
                self.assertTrue(entered.wait(5))
                concurrent = worker.submit(platform_snapshot).result(timeout=2)
                self.assertEqual(concurrent["postgres"]["status"], "loading")
                self.assertEqual(concurrent["web_app"]["status"], "loading")
            finally:
                release.set()
            refreshed = first.result(timeout=5)
        self.assertEqual(refreshed["postgres"]["metrics"]["cpu_percent"]["value"], 12)
        self.assertEqual(refreshed["web_app"]["metrics"]["Requests"]["value"], 42)

    def test_metric_errors_preserve_timestamped_samples_unless_access_is_lost(self):
        from hub.monitoring_azure import POSTGRES, AzureMetrics

        def response(status, *metrics):
            result = Response()
            result.status_code = status
            result._content = json.dumps({"value": metrics}).encode()
            result._content_consumed = True
            return result

        now = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)
        sample_time = (now - timedelta(minutes=1)).isoformat()
        cpu = self.metric("cpu_percent", "average", **{sample_time: 0})
        disk = self.metric("storage_free", "average", **{sample_time: 21474836480})
        connections = self.metric("connections_failed", "total", **{sample_time: 2})
        cpu_error = {"name": {"value": "cpu_percent"}, "errorCode": "InternalServerError"}
        disk_denied = {"name": {"value": "storage_free"}, "errorCode": "AuthorizationFailed"}
        provider = AzureMetrics(POSTGRES)
        with (
            patch("hub.monitoring_azure._credential") as credential,
            patch("hub.monitoring_azure.requests.get", side_effect=[
                response(200, cpu, disk), response(200, connections),
                response(200, cpu_error, disk_denied), response(200, connections),
                Timeout("temporarily unavailable"), response(403),
                Timeout("temporarily unavailable"), Timeout("temporarily unavailable"),
            ]),
        ):
            credential.return_value.get_token.return_value = SimpleNamespace(token="test-token")
            provider.snapshot(POSTGRES_ID, now=now, tick=0)
            delayed = provider.snapshot(POSTGRES_ID, now=now + timedelta(minutes=1), tick=60)
            denied = provider.snapshot(POSTGRES_ID, now=now + timedelta(minutes=3), tick=180)
            still_unavailable = provider.snapshot(POSTGRES_ID, now=now + timedelta(minutes=5), tick=300)
        self.assertEqual(delayed["metrics"]["cpu_percent"], {
            "status": "stale", "value": 0, "sampled_at": sample_time, "history": [[sample_time, 0]],
        })
        self.assertIsNone(delayed["metrics"]["storage_free"]["value"])
        self.assertEqual(delayed["metrics"]["storage_free"]["status"], "access_denied")
        self.assertEqual(denied["status"], "access_denied")
        self.assertIsNone(denied["metrics"]["cpu_percent"]["value"])
        self.assertEqual(denied["metrics"]["cpu_percent"]["history"], [])
        self.assertIsNone(denied["metrics"]["connections_failed"]["value"])
        self.assertIsNone(still_unavailable["metrics"]["cpu_percent"]["value"])

    def test_provider_rejects_invalid_ids_without_requesting_a_token(self):
        from hub.monitoring_azure import POSTGRES, AzureMetrics

        for resource_id in ("https://example.invalid/db", POSTGRES_ID + "/../other", POSTGRES_ID + "?x=Y"):
            with self.subTest(resource_id=resource_id), patch("hub.monitoring_azure.read_metrics") as read:
                self.assertEqual(AzureMetrics(POSTGRES).snapshot(resource_id)["status"], "invalid_configuration")
                read.assert_not_called()

    def test_postgresql_accepts_a_63_character_server_name(self):
        from hub.monitoring_azure import POSTGRES, AzureMetrics

        reference = POSTGRES_ID.rsplit("/", 1)[0] + "/"
        with patch("hub.monitoring_azure.read_metrics", return_value=([], {})):
            self.assertEqual(AzureMetrics(POSTGRES).snapshot(reference + "a" * 63)["status"], "no_data")
            self.assertEqual(AzureMetrics(POSTGRES).snapshot(reference + "a" * 64)["status"], "invalid_configuration")

    def test_arm_fetch_uses_scoped_identity_no_redirects_and_approved_metrics(self):
        from hub.monitoring_azure import POSTGRES, read_metrics

        response = Response()
        response.status_code = 200
        response._content = b'{"value": []}'
        now = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)
        with (
            patch("hub.monitoring_azure._credential") as credential,
            patch("hub.monitoring_azure.requests.get", return_value=response) as get,
        ):
            credential.return_value.get_token.return_value = SimpleNamespace(token="test-token")
            self.assertEqual(read_metrics(POSTGRES_ID, POSTGRES, now), ([], {}))
        credential.return_value.get_token.assert_called_once_with("https://management.azure.com/.default")
        groups = {}
        for args, kwargs in (call for call in get.call_args_list):
            self.assertEqual(args[0], "https://management.azure.com" + POSTGRES_ID + "/providers/Microsoft.Insights/metrics")
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["params"]["interval"], "PT1M")
            groups[kwargs["params"]["aggregation"]] = kwargs["params"]["metricnames"].split(",")
        self.assertIn("storage_free", groups["Average"])
        self.assertNotIn("connections_failed", groups["Average"])
        self.assertEqual(groups["Total"], ["connections_failed"])


class WorkerWindowTests(SimpleTestCase):
    def test_p95_overflow_reports_a_lower_bound_instead_of_the_slowest_request(self):
        from hub.monitoring import RequestWindow

        window = RequestWindow()
        for elapsed_ms in [10100] * 95 + [90000] * 5:
            window.record(50, elapsed_ms, 200)
        sample = window.snapshot(51)
        self.assertEqual(sample["p95_ms"], 10000)
        self.assertTrue(sample["p95_is_lower_bound"])

    def test_window_expires_traffic_and_counts_server_errors(self):
        from hub.monitoring import RequestWindow

        window = RequestWindow()
        window.record(0, 2000, 503)
        for elapsed in range(1, 21):
            window.record(16, elapsed, 503 if elapsed == 19 else 200)
        snapshot = window.snapshot(61)
        self.assertEqual(
            snapshot, {"requests": 20, "errors": 1, "average_ms": 10.5, "p95_ms": 19, "p95_is_lower_bound": False},
        )
        self.assertEqual(
            window.snapshot(80),
            {"requests": 0, "errors": 0, "average_ms": None, "p95_ms": None, "p95_is_lower_bound": False},
        )
