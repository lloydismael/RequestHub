from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from hub.models import Account, Request
from hub.views import DashboardLiveDataView, DashboardView


class DashboardPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='perf-admin', role=User.Roles.ADMIN)
        cls.pm = User.objects.create(username='perf-pm', role=User.Roles.PM_ESG)
        cls.account = Account.objects.create(name='Performance account')
        Request.objects.bulk_create([
            Request(reference_code=f'PERF-{i:03}', account=cls.account, requestor=cls.admin,
                    engagement_type=Request.Engagement.SUPPORT, priority=Request.Priority.MEDIUM,
                    start_date=date(2026, 10, 30), due_date=date(2026, 11, 10),
                    description='needle beyond first page' if i == 0 else '',
                    status=Request.Status.ONGOING)
            for i in range(151)
        ])

    def context(self, params=None, user=None):
        request = RequestFactory().get('/dashboard/', params or {})
        request.user = user or self.admin
        view = DashboardView()
        view.request = request
        return view._build_admin_dashboard_context(request.user)

    def test_page_fetch_is_bounded(self):
        with CaptureQueriesContext(connection) as queries:
            result = self.context()
        self.assertEqual(len(result['requests']), 50)
        self.assertEqual(result['requests_total_count'], 151)
        self.assertTrue(any('LIMIT 50' in q['sql'] for q in queries))
        self.assertEqual(result['page_obj'].number, 1)

    def test_search_finds_beyond_first_page(self):
        result = self.context({'q': 'needle first'})
        self.assertEqual([r.reference_code for r in result['requests']], ['PERF-000'])

    def test_unicode_casefold_search_preserved(self):
        Request.objects.filter(reference_code='PERF-000').update(description='Straße ﬃ Σ')
        result = self.context({'q': 'strasse ffi σ'})
        self.assertEqual([r.reference_code for r in result['requests']], ['PERF-000'])

    def test_query_count_does_not_grow_per_row(self):
        with CaptureQueriesContext(connection) as small:
            self.context({'page_size': '25'})
        with CaptureQueriesContext(connection) as large:
            self.context({'page_size': '100'})
        self.assertEqual(len(small), len(large))

    def test_metrics_cover_full_filtered_population(self):
        result = self.context({'page': '3', 'page_size': '25'})
        self.assertEqual(len(result['requests']), 25)
        self.assertEqual(result['metrics']['open'], 151)
        self.assertEqual(result['status_counts']['all'], 151)
        self.assertEqual(result['page_obj'].number, 3)

    def test_role_scope_and_soft_delete(self):
        Request.objects.filter(reference_code='PERF-000').update(engineer=self.pm)
        result = self.context({'pm_esg_tab': 'assigned'}, self.pm)
        self.assertEqual(result['requests_total_count'], 1)
        Request.objects.filter(reference_code='PERF-000').update(is_deleted=True)
        self.assertEqual(self.context({'pm_esg_tab': 'assigned'}, self.pm)['requests_total_count'], 0)

    def test_invalid_page_size_is_bounded(self):
        self.assertEqual(len(self.context({'page_size': '999999'})['requests']), 50)
        self.assertEqual(self.context({'page': 'bad'})['page_obj'].number, 1)

    def test_date_weekend_boundaries_match_existing(self):
        Request.objects.update(start_date=date(2026, 10, 30), end_date=None)
        with patch('django.utils.timezone.now', return_value=datetime(2026, 11, 5, 1, tzinfo=dt_timezone.utc)):
            result = self.context({'days_min': '4', 'days_max': '4', 'sort': 'days'})
            self.assertEqual(result['requests_total_count'], 151)
            self.assertTrue(all(r.days_since_creation == 4 for r in result['requests']))
            self.assertEqual(result['metrics']['overdue'], 0)

    def test_null_dates_sort_last_both_directions(self):
        Request.objects.filter(reference_code='PERF-000').update(due_date=None)
        for direction in ('asc', 'desc'):
            result = self.context({'sort': 'due', 'direction': direction, 'page': '4'})
            self.assertIsNone(result['requests'][-1].due_date)

    def test_live_page_change_not_suppressed_by_version(self):
        first = self.context()
        request = RequestFactory().get('/dashboard/live-data/', {'version': first['dashboard_live_version'], 'page': '2'})
        request.user = self.admin
        response = DashboardLiveDataView.as_view()(request)
        import json
        self.assertTrue(json.loads(response.content)['changed'])
