import os
from contextlib import ExitStack

from azure.identity import ManagedIdentityCredential
from azure.monitor.opentelemetry.exporter import (
    ApplicationInsightsSampler,
    AzureMonitorTraceExporter,
)
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

_provider = None


def configure_telemetry():
    global _provider
    if not settings.AZURE_TELEMETRY_ENABLED or _provider is not None:
        return
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not connection_string:
        raise ImproperlyConfigured(
            "APPLICATIONINSIGHTS_CONNECTION_STRING is required when Azure telemetry is enabled."
        )
    exporter = AzureMonitorTraceExporter(
        connection_string=connection_string,
        credential=ManagedIdentityCredential(),
        disable_offline_storage=True,
    )
    provider = TracerProvider(
        sampler=ApplicationInsightsSampler(),
        resource=Resource({"service.name": "request-hub"}),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    _provider = provider


def get_tracer():
    if _provider is None:
        raise ImproperlyConfigured("Azure telemetry was enabled but has not been configured.")
    return _provider.get_tracer("request_hub")


class TelemetryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.AZURE_TELEMETRY_ENABLED or request.path_info in ("/healthz/", "/readyz/"):
            return self.get_response(request)

        tracer = get_tracer()

        def measure(execute, sql, params, many, context):
            with tracer.start_as_current_span(
                "database.query",
                kind=SpanKind.CLIENT,
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                vendor = context["connection"].vendor
                span.set_attribute(
                    "db.system.name", vendor if vendor in ("postgresql", "sqlite") else "other"
                )
                try:
                    return execute(sql, params, many, context)
                except Exception:
                    span.set_status(Status(StatusCode.ERROR))
                    raise

        with tracer.start_as_current_span(
            "unresolved",
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            method = request.method
            span.set_attribute(
                "http.request.method",
                method if method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS") else "_OTHER",
            )
            try:
                with ExitStack() as stack:
                    for connection in connections.all():
                        stack.enter_context(connection.execute_wrapper(measure))
                    response = self.get_response(request)
                span.set_attribute("http.response.status_code", response.status_code)
                if response.status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                return response
            except Exception:
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                match = getattr(request, "resolver_match", None)
                if match and match.view_name:
                    span.update_name(match.view_name)
