import os
import subprocess
import sys
from unittest import TestCase
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.db.utils import ConnectionHandler
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import ResolverMatch

from request_hub import telemetry


class AzureTelemetryTests(TestCase):
    def setUp(self):
        self.telemetry = telemetry
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.addCleanup(self.provider.shutdown)
        self.tracer = self.provider.get_tracer("test")
        self.connections = ConnectionHandler({
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
        })
        self.addCleanup(self.connections.close_all)

    def run_request(self, request, view, enabled=True):
        with override_settings(AZURE_TELEMETRY_ENABLED=enabled):
            with patch.object(self.telemetry, "get_tracer", return_value=self.tracer):
                with patch.object(self.telemetry, "connections", self.connections):
                    return self.telemetry.TelemetryMiddleware(view)(request)

    def test_disabled_does_not_export(self):
        response = self.run_request(
            RequestFactory().get("/private/"), lambda request: HttpResponse("ok"), enabled=False
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.exporter.get_finished_spans(), ())

    def test_health_probes_are_not_exported(self):
        for path in ("/healthz/", "/readyz/"):
            self.run_request(RequestFactory().get(path), lambda request: HttpResponse("ok"))
        self.assertEqual(self.exporter.get_finished_spans(), ())

    def test_request_and_database_spans_exclude_private_values(self):
        request = RequestFactory().post(
            "/private-path-secret/?token=query-secret",
            data="body-secret",
            content_type="text/plain",
            HTTP_AUTHORIZATION="header-secret",
        )
        request.resolver_match = ResolverMatch(lambda request: None, (), {}, url_name="safe-view")

        def view(request):
            with self.connections["default"].cursor() as cursor:
                cursor.execute("SELECT %s AS sql_secret", ["parameter-secret"])
                cursor.fetchone()
            return HttpResponse("ok", status=201)

        response = self.run_request(request, view)
        self.assertEqual(response.status_code, 201)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        self.assertEqual({span.name for span in spans}, {"safe-view", "database.query"})
        from azure.monitor.opentelemetry.exporter.export.trace._exporter import _convert_span_to_envelope

        envelopes = [_convert_span_to_envelope(span) for span in spans]
        request_envelope = next(item for item in envelopes if item.data.base_type == "RequestData")
        dependency_envelope = next(item for item in envelopes if item.data.base_type == "RemoteDependencyData")
        self.assertEqual(request_envelope.data.base_data.response_code, "201")
        self.assertTrue(request_envelope.data.base_data.success)
        self.assertIsNotNone(dependency_envelope.data.base_data.type)
        self.assertTrue(dependency_envelope.data.base_data.success)
        payload = "\n".join(span.to_json() for span in spans) + str(
            [item.as_dict() for item in envelopes]
        )
        for secret in (
            "private-path-secret", "query-secret", "body-secret", "header-secret",
            "sql_secret", "parameter-secret", "SELECT",
        ):
            self.assertNotIn(secret, payload)

    def test_request_exception_is_preserved_without_exporting_its_message(self):
        def view(request):
            raise ValueError("exception-secret")

        with self.assertRaisesRegex(ValueError, "exception-secret"):
            self.run_request(RequestFactory().get("/private/"), view)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertNotIn("exception-secret", spans[0].to_json())

    def test_client_error_is_reported_as_failed_request(self):
        from azure.monitor.opentelemetry.exporter.export.trace._exporter import _convert_span_to_envelope

        self.run_request(RequestFactory().get("/missing/"), lambda request: HttpResponse(status=404))
        envelope = _convert_span_to_envelope(self.exporter.get_finished_spans()[0])
        self.assertEqual(envelope.data.base_data.response_code, "404")
        self.assertFalse(envelope.data.base_data.success)

    def test_database_error_does_not_export_sql_or_exception_text(self):
        def view(request):
            with self.connections["default"].cursor() as cursor:
                cursor.execute("SELECT secret_missing_column")

        with self.assertRaises(OperationalError):
            self.run_request(RequestFactory().get("/private/"), view)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        self.assertTrue(all(span.status.status_code.name == "ERROR" for span in spans))
        self.assertNotIn("secret_missing_column", "".join(span.to_json() for span in spans))

    def test_unknown_http_method_is_not_exported_verbatim(self):
        request = RequestFactory().generic("secret-method", "/private/")
        self.run_request(request, lambda request: HttpResponse())
        payload = self.exporter.get_finished_spans()[0].to_json()
        self.assertNotIn("secret-method", payload)
        self.assertIn("_OTHER", payload)

    def test_enabled_configuration_requires_connection_string(self):
        with override_settings(AZURE_TELEMETRY_ENABLED=True):
            with patch.dict(os.environ, {"APPLICATIONINSIGHTS_CONNECTION_STRING": ""}):
                with self.assertRaises(ImproperlyConfigured):
                    self.telemetry.configure_telemetry()

    def test_disabled_configuration_does_not_initialize_exporter(self):
        with override_settings(AZURE_TELEMETRY_ENABLED=False):
            with patch.object(self.telemetry, "AzureMonitorTraceExporter") as exporter:
                self.telemetry.configure_telemetry()
        exporter.assert_not_called()

    def test_wsgi_initializes_worker_and_exports_real_request(self):
        script = """
from unittest.mock import patch
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from request_hub import telemetry

exporter = InMemorySpanExporter()
with patch.object(telemetry, 'AzureMonitorTraceExporter', return_value=exporter) as factory:
    with patch.object(telemetry, 'ManagedIdentityCredential') as credential:
        import request_hub.wsgi
        telemetry.configure_telemetry()
        from django.test import Client
        client = Client()
        assert client.get('/healthz/').status_code == 200
        assert client.get('/release/').status_code == 200
        assert telemetry._provider.force_flush()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == 'release_info'
        assert factory.call_count == 1
        assert factory.call_args.kwargs['credential'] is credential.return_value
        assert factory.call_args.kwargs['disable_offline_storage'] is True
        telemetry._provider.shutdown()
"""
        environment = os.environ.copy()
        environment.update({
            "AZURE_TELEMETRY_ENABLED": "true",
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=00000000-0000-0000-0000-000000000000",
            "DJANGO_DEBUG": "True",
            "DJANGO_SECRET_KEY": "disposable-test-key",
            "DJANGO_ALLOWED_HOSTS": "testserver",
            "DJANGO_SETTINGS_MODULE": "request_hub.settings_ci",
            "PYTHON_DOTENV_DISABLED": "1",
        })
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_asgi_initializes_telemetry_when_enabled(self):
        script = """
from unittest.mock import patch
from request_hub import telemetry
with patch.object(telemetry, 'AzureMonitorTraceExporter'):
    with patch.object(telemetry, 'ManagedIdentityCredential'):
        import request_hub.asgi
        assert telemetry._provider is not None
        telemetry._provider.shutdown()
"""
        environment = os.environ.copy()
        environment.update({
            "AZURE_TELEMETRY_ENABLED": "true",
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=00000000-0000-0000-0000-000000000000",
            "DJANGO_DEBUG": "True",
            "DJANGO_SECRET_KEY": "disposable-test-key",
            "DJANGO_SETTINGS_MODULE": "request_hub.settings_ci",
            "PYTHON_DOTENV_DISABLED": "1",
        })
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_disabled_wsgi_starts_without_monitoring_dependencies(self):
        script = """
import importlib.abc
import sys
class BlockMonitor(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('azure.monitor', 'opentelemetry')):
            raise ModuleNotFoundError(fullname)
        return None
sys.meta_path.insert(0, BlockMonitor())
import request_hub.wsgi
"""
        environment = os.environ.copy()
        environment.update({
            "AZURE_TELEMETRY_ENABLED": "false",
            "DJANGO_DEBUG": "True",
            "DJANGO_SECRET_KEY": "disposable-test-key",
            "DJANGO_SETTINGS_MODULE": "request_hub.settings_ci",
            "PYTHON_DOTENV_DISABLED": "1",
        })
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
