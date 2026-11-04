import logging
from contextlib import ExitStack
from time import perf_counter

from django.conf import settings
from django.db import connections

logger = logging.getLogger('hub.performance')


class PagePerformanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'PAGE_PERFORMANCE_ENABLED', False):
            return self.get_response(request)

        database_seconds = 0.0
        query_count = 0

        def measure(execute, sql, params, many, context):
            nonlocal database_seconds, query_count
            start = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                database_seconds += perf_counter() - start
                query_count += 1

        start = perf_counter()
        with ExitStack() as stack:
            for connection in connections.all():
                stack.enter_context(connection.execute_wrapper(measure))
            response = self.get_response(request)
        app_ms = (perf_counter() - start) * 1000
        db_ms = database_seconds * 1000
        match = getattr(request, 'resolver_match', None)
        route = match.view_name if match else 'unresolved'
        logger.info('route=%s status=%s app_ms=%.2f db_ms=%.2f db_queries=%s',
                    route, response.status_code, app_ms, db_ms, query_count)
        response.headers['Server-Timing'] = f'app;dur={app_ms:.2f}, db;dur={db_ms:.2f}'
        return response
