import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from math import isfinite
from threading import Lock
from time import monotonic

import requests
from azure.core.exceptions import AzureError, ClientAuthenticationError
from azure.identity import ManagedIdentityCredential
from django.conf import settings
from django.utils import timezone

ARM_ORIGIN = "https://management.azure.com"
HISTORY_MINUTES = 15
STALE_MINUTES = 5
NORMAL_REFRESH_SECONDS = 60
FAILED_REFRESH_SECONDS = 120
ACCESS_ERROR_CODES = frozenset({
    "accessdenied", "authorizationfailed", "forbidden", "invalidauthenticationtoken", "unauthorized",
})
IO_ERRORS = (AzureError, requests.RequestException, ValueError)
_refresh_slot = Lock()
RESOURCE_PREFIX = (
    r"/subscriptions/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    r"/resourceGroups/[a-z0-9_.()-]{1,90}/providers/"
)


@dataclass(frozen=True)
class Resource:
    namespace: str
    average: tuple[str, ...]
    total: tuple[str, ...]
    max_name_length: int

    def valid_id(self, value):
        pattern = RESOURCE_PREFIX + re.escape(self.namespace) + rf"/[a-z0-9][a-z0-9-]{{0,{self.max_name_length - 1}}}"
        return re.fullmatch(pattern, value, re.I | re.ASCII)

    def groups(self):
        return (("Average", self.average), ("Total", self.total))


POSTGRES = Resource(
    "Microsoft.DBforPostgreSQL/flexibleServers",
    ("cpu_percent", "memory_percent", "storage_used", "storage_free", "storage_percent",
     "iops", "active_connections", "backup_storage_used"),
    ("connections_failed",),
    max_name_length=63,
)
WEB_APP = Resource(
    "Microsoft.Web/sites",
    ("HttpResponseTime", "AverageMemoryWorkingSet"),
    ("Requests", "Http5xx", "CpuTime"),
    max_name_length=60,
)


@lru_cache(maxsize=1)
def _credential():
    return ManagedIdentityCredential(
        connection_timeout=2, read_timeout=2, retry_total=0, redirect_max=0, logging_enable=False,
    )


def read_metrics(resource_id, resource, now):
    token = _credential().get_token(f"{ARM_ORIGIN}/.default").token
    url = f"{ARM_ORIGIN}{resource_id}/providers/Microsoft.Insights/metrics"
    params = {
        "api-version": "2023-10-01", "metricnamespace": resource.namespace, "interval": "PT1M",
        "timespan": f"{(now - timedelta(minutes=HISTORY_MINUTES)).isoformat()}/{now.isoformat()}",
    }
    records, failed = [], {}
    for aggregation, names in resource.groups():
        try:
            with requests.get(
                url, params={**params, "aggregation": aggregation, "metricnames": ",".join(names)},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=(2, 3), allow_redirects=False,
            ) as response:
                if response.status_code in (401, 403):
                    return [], dict.fromkeys(resource.average + resource.total, "access_denied")
                if response.status_code != 200:
                    raise requests.HTTPError()
                metrics = response.json()["value"]
                if not isinstance(metrics, list):
                    raise ValueError("Expected an Azure metric list")
                records.extend(metrics)
        except (*IO_ERRORS, KeyError, TypeError):
            failed.update(dict.fromkeys(names, "unavailable"))
    return records, failed


def _series(records, resource, now):
    allowed = {
        name.casefold(): (name, aggregation.lower())
        for aggregation, names in resource.groups() for name in names
    }
    history, failed = {}, {}
    for metric in records:
        if not isinstance(metric, dict):
            continue
        name = (metric.get("name") or {}).get("value", "").casefold()
        if name not in allowed:
            continue
        key, aggregation = allowed[name]
        code = str(metric.get("errorCode", "Success")).casefold()
        if code != "success":
            failed[key] = "access_denied" if code in ACCESS_ERROR_CODES else "unavailable"
            continue
        series = metric.get("timeseries") or []
        if len(series) != 1 or series[0].get("metadatavalues"):
            continue
        points = {}
        for row in series[0].get("data") or []:
            value = row.get(aggregation)
            if type(value) not in (float, int) or not isfinite(value) or value < 0:
                continue
            try:
                sampled = datetime.fromisoformat(row.get("timeStamp", ""))
            except (TypeError, ValueError):
                continue
            if sampled.tzinfo and now - timedelta(minutes=HISTORY_MINUTES) <= sampled <= now:
                points[sampled] = value
        history[key] = tuple(sorted(points.items())[-HISTORY_MINUTES:])
    return history, failed


@dataclass(frozen=True)
class Reading:
    resource_id: str
    next_check: float
    history: dict
    failed: dict


class AzureMetrics:
    def __init__(self, resource):
        self.resource = resource
        self._reading = None

    def snapshot(self, resource_id, *, now=None, tick=None):
        if not resource_id:
            self._reading = None
            return {"status": "unconfigured", "metrics": {}}
        if not self.resource.valid_id(resource_id):
            self._reading = None
            return {"status": "invalid_configuration", "metrics": {}}
        now = timezone.now() if now is None else now
        tick = monotonic() if tick is None else tick
        reading = self._reading
        if reading is None or reading.resource_id != resource_id or tick >= reading.next_check:
            if _refresh_slot.acquire(blocking=False):
                try:
                    reading = self._reading
                    if reading is None or reading.resource_id != resource_id or tick >= reading.next_check:
                        reading = self._read(resource_id, now, tick, reading)
                        self._reading = reading
                finally:
                    _refresh_slot.release()
            else:
                reading = self._reading
        if reading is None or reading.resource_id != resource_id:
            return {"status": "loading", "metrics": {}}
        return self._present(reading, now)

    def _read(self, resource_id, now, tick, previous):
        names = self.resource.average + self.resource.total
        try:
            raw, failed = read_metrics(resource_id, self.resource, now)
            history, metric_failures = _series(raw, self.resource, now)
            failed = {**metric_failures, **failed}
        except ClientAuthenticationError:
            history, failed = {}, dict.fromkeys(names, "access_denied")
        except (*IO_ERRORS, AttributeError, TypeError, OverflowError):
            history, failed = {}, dict.fromkeys(names, "unavailable")
        if previous is not None and previous.resource_id == resource_id:
            for key, status in failed.items():
                if status == "unavailable" and key in previous.history:
                    history[key] = previous.history[key]
        wait = FAILED_REFRESH_SECONDS if failed else NORMAL_REFRESH_SECONDS
        return Reading(resource_id, tick + wait, history, failed)

    def _present(self, reading, now):
        metrics = {}
        cutoff = now - timedelta(minutes=HISTORY_MINUTES)
        for name in self.resource.average + self.resource.total:
            failure = reading.failed.get(name)
            series = [] if failure == "access_denied" else [
                (time, value) for time, value in reading.history.get(name, ()) if cutoff <= time <= now
            ]
            time, value = series[-1] if series else (None, None)
            status = failure or "missing"
            if time:
                status = "stale" if failure or now - time >= timedelta(minutes=STALE_MINUTES) else "current"
            metrics[name] = {
                "status": status, "value": value, "sampled_at": time.isoformat() if time else None,
                "history": [[time.isoformat(), value] for time, value in series],
            }
        statuses = {metric["status"] for metric in metrics.values()}
        if statuses == {"current"}:
            status = "current"
        elif "current" in statuses:
            status = "partial"
        elif "stale" in statuses:
            status = "delayed"
        elif "access_denied" in statuses and "unavailable" not in statuses:
            status = "access_denied"
        else:
            status = "unavailable" if reading.failed else "no_data"
        return {"status": status, "metrics": metrics}


_postgres = AzureMetrics(POSTGRES)
_web_app = AzureMetrics(WEB_APP)


def platform_snapshot():
    return {
        "postgres": _postgres.snapshot(settings.AZURE_MONITOR_POSTGRES_RESOURCE_ID),
        "web_app": _web_app.snapshot(settings.AZURE_MONITOR_WEB_APP_RESOURCE_ID),
    }
