import os
from bisect import bisect_left
from hashlib import blake2s
from math import ceil
from threading import Lock
from time import monotonic, perf_counter
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.utils import timezone

WINDOW_SECONDS = 60
LATENCY_LIMITS_MS = (*range(1, 101), *range(110, 501, 10), *range(550, 2001, 50), *range(2250, 10001, 250))
_worker_seed = uuid4().hex


class RequestWindow:
    def __init__(self):
        self._lock = Lock()
        self._seconds = [None] * WINDOW_SECONDS

    def record(self, when, latency_ms, status):
        second = int(when)
        slot = second % WINDOW_SECONDS
        index = bisect_left(LATENCY_LIMITS_MS, latency_ms)
        with self._lock:
            bucket = self._seconds[slot]
            if bucket is None or bucket[0] < second:
                bucket = self._seconds[slot] = [second, 0, 0, 0.0, 0.0, [0] * (len(LATENCY_LIMITS_MS) + 1)]
            if bucket[0] == second:
                bucket[1] += 1
                bucket[2] += status >= 500
                bucket[3] += latency_ms
                bucket[4] = max(bucket[4], latency_ms)
                bucket[5][index] += 1

    def snapshot(self, when):
        cutoff = int(when) - WINDOW_SECONDS
        with self._lock:
            active = [bucket for bucket in self._seconds if bucket is not None and bucket[0] > cutoff]
            count = sum(bucket[1] for bucket in active)
            errors = sum(bucket[2] for bucket in active)
            elapsed_ms = sum(bucket[3] for bucket in active)
            maximum_ms = max((bucket[4] for bucket in active), default=0.0)
            histogram = [sum(column) for column in zip(*(bucket[5] for bucket in active))]

        p95 = None
        p95_is_lower_bound = False
        if count:
            remaining = ceil(0.95 * count)
            for limit, frequency in zip(LATENCY_LIMITS_MS, histogram):
                remaining -= frequency
                if remaining <= 0:
                    p95 = round(min(limit, maximum_ms), 2)
                    break
            else:
                p95 = LATENCY_LIMITS_MS[-1]
                p95_is_lower_bound = True
        return {
            "requests": count,
            "errors": errors,
            "average_ms": round(elapsed_ms / count, 2) if count else None,
            "p95_ms": p95,
            "p95_is_lower_bound": p95_is_lower_bound,
        }


request_window = RequestWindow()


def app_snapshot():
    return {
        **request_window.snapshot(monotonic()),
        "worker": blake2s(f"{_worker_seed}:{os.getpid()}".encode(), digest_size=8).hexdigest(),
        "window_seconds": WINDOW_SECONDS,
        "sampled_at": timezone.now().isoformat(),
    }


def _query(sql):
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = '750ms'")
        start = perf_counter()
        cursor.execute(sql)
        result = cursor.fetchone()
        elapsed_ms = (perf_counter() - start) * 1000
        # Roll back the local timeout even when called inside an outer transaction.
        transaction.set_rollback(True)
        return result, elapsed_ms


def database_snapshot():
    result = {
        "status": "unreachable", "latency_ms": None, "size_bytes": None,
        "connections": None, "transactions": None, "stats_reset_at": None,
        "lock_waits": None, "cache_hit_percent": None,
    }
    try:
        _, elapsed_ms = _query("SELECT 1")
        result.update(status="partial", latency_ms=round(elapsed_ms, 2))
        row, _ = _query("""
            SELECT pg_database_size(current_database()), numbackends,
                   xact_commit + xact_rollback, stats_reset, blks_hit, blks_read,
                   (SELECT count(*) FROM pg_stat_activity
                    WHERE datname = current_database() AND wait_event_type = 'Lock')
            FROM pg_stat_database WHERE datname = current_database()
        """)
        if row is not None:
            size, connections, transactions, reset, hits, reads, locks = row
            result.update(
                status="reachable", size_bytes=size, connections=connections,
                transactions=transactions, stats_reset_at=reset.isoformat() if reset else None,
                lock_waits=locks, cache_hit_percent=round(hits * 100 / (hits + reads), 2) if hits + reads else None,
            )
    except DatabaseError:
        pass
    return {**result, "sampled_at": timezone.now().isoformat()}
