import logging
import os
import subprocess
import sys
from unittest.mock import patch

from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from hub.middleware import PagePerformanceMiddleware


class PagePerformanceTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/private/search/?q=secret-value')

    def test_staging_diagnostics_emit_log_records(self):
        environment = os.environ.copy()
        environment.update(DJANGO_SETTINGS_MODULE='request_hub.settings', DJANGO_DEBUG='False',
            DJANGO_ENVIRONMENT='staging', DJANGO_PAGE_PERFORMANCE_ENABLED='true',
            DJANGO_SECRET_KEY='disposable-staging-test-key', DB_HOST='127.0.0.1', DB_USER='postgres', DB_PASSWORD='disposable')
        script = "from django.conf import settings; from django.utils.log import configure_logging; import logging; configure_logging(settings.LOGGING_CONFIG, settings.LOGGING); logging.getLogger('hub.performance').info('safe-diagnostic-marker')"
        result = subprocess.run([sys.executable, '-c', script], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('safe-diagnostic-marker', result.stderr)

    @override_settings(PAGE_PERFORMANCE_ENABLED=False)
    def test_timing_disabled_by_default(self):
        response = PagePerformanceMiddleware(lambda request: HttpResponse('ok'))(self.request)
        self.assertNotIn('Server-Timing', response.headers)

    @override_settings(PAGE_PERFORMANCE_ENABLED=True)
    def test_enabled_timing_excludes_private_values(self):
        with self.assertLogs('hub.performance', logging.INFO) as logs:
            response = PagePerformanceMiddleware(lambda request: HttpResponse('ok'))(self.request)
        self.assertIn('app;dur=', response.headers['Server-Timing'])
        self.assertIn('db;dur=', response.headers['Server-Timing'])
        self.assertNotIn('private', str(logs.output))
        self.assertNotIn('secret-value', str(logs.output))

    @override_settings(PAGE_PERFORMANCE_ENABLED=True)
    def test_exception_preserves_original_response(self):
        def fail(request):
            raise ValueError('original-error')
        with self.assertRaisesRegex(ValueError, 'original-error'):
            PagePerformanceMiddleware(fail)(self.request)

    @override_settings(PAGE_PERFORMANCE_ENABLED=True)
    def test_no_forced_debug_query_capture(self):
        with patch.object(connection, 'force_debug_cursor', False):
            with self.assertLogs('hub.performance', logging.INFO):
                PagePerformanceMiddleware(lambda request: HttpResponse('ok'))(self.request)
            self.assertFalse(connection.force_debug_cursor)


class PageDatabaseTimingTests(TestCase):
    @override_settings(PAGE_PERFORMANCE_ENABLED=True)
    def test_records_database_duration_and_query_count_without_sql(self):
        def query(request):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            return HttpResponse('ok')
        with self.assertLogs('hub.performance', logging.INFO) as logs:
            response = PagePerformanceMiddleware(query)(RequestFactory().get('/'))
        self.assertIn('db_queries=1', str(logs.output))
        self.assertNotIn('SELECT', str(logs.output))
        self.assertIn('db;dur=', response.headers['Server-Timing'])
