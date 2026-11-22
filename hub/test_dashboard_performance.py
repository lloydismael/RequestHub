from datetime import UTC, date, datetime
from importlib.util import find_spec
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from hub.models import Account, Request
from hub.views import DashboardLiveDataView, DashboardView


class DashboardImportCompatibilityTests(SimpleTestCase):
    def test_domain_import_and_legacy_route_use_the_same_dashboard(self) -> None:
        """Keep existing dashboard imports and routing bound to the same class."""
        from django.urls import resolve, reverse

        self.assertIsNotNone(find_spec('hub.views').submodule_search_locations)
        from hub.views.dashboard import DashboardView as DomainDashboardView

        self.assertIs(DomainDashboardView, DashboardView)
        self.assertIs(resolve(reverse('hub:dashboard')).func.view_class, DashboardView)

    def test_sla_working_seconds_skip_the_weekend_and_overnight(self) -> None:
        """Apply the Manila working-hours calendar across a weekend."""
        self.assertIsNotNone(find_spec('hub.services.sla'))
        from hub.services.sla import working_seconds_between

        manila = ZoneInfo('Asia/Manila')
        friday = datetime(2026, 11, 20, 18, 15, tzinfo=manila)
        monday = datetime(2026, 11, 23, 8, 45, tzinfo=manila)
        self.assertEqual(working_seconds_between(friday, monday), 30 * 60)
        self.assertEqual(working_seconds_between(monday, friday), 0)

    def test_report_month_range_swaps_reversed_months_across_years(self) -> None:
        """Keep the selected months inclusive even when the inputs are reversed."""
        self.assertIsNotNone(find_spec('hub.services.reports'))
        from hub.services.reports import resolve_month_filters

        period = resolve_month_filters(' 2027-01 ', '2026-12')
        self.assertEqual(period['start_month_date'], date(2026, 12, 1))
        self.assertEqual(period['end_exclusive_date'], date(2027, 2, 1))
        self.assertEqual(period['label'], 'December 2026 to January 2027')
        self.assertTrue(period['range_swapped'])


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

    def test_engineer_lookup_count_does_not_grow_on_assigned_or_backup_tabs(self):
        engineer = User.objects.create(username='perf-engineer', role=User.Roles.ENGINEER, must_change_password=False)
        peer = User.objects.create(username='perf-peer', role=User.Roles.ENGINEER)
        self.client.force_login(engineer)
        for tab in ('assigned', 'backup'):
            with self.subTest(tab=tab):
                eligible = {'engineer': engineer} if tab == 'assigned' else {'engineer': peer, 'backup_engineer': engineer}
                Request.objects.update(engineer=None, backup_engineer=None)
                Request.objects.filter(reference_code='PERF-000').update(**eligible)
                with CaptureQueriesContext(connection) as small:
                    response = self.client.get('/dashboard/', {'tab': tab})
                self.assertContains(response, 'PERF-000')
                Request.objects.filter(reference_code__lte='PERF-011').update(**eligible)
                with CaptureQueriesContext(connection) as large:
                    response = self.client.get('/dashboard/', {'tab': tab})
                self.assertContains(response, 'PERF-011')
                table = User._meta.db_table
                user_only = lambda queries: sum(f'FROM "{table}"' in query['sql'] for query in queries)
                self.assertEqual(user_only(small), user_only(large), 'Standalone user lookups grew with request rows')

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
        with patch('django.utils.timezone.now', return_value=datetime(2026, 11, 5, 1, tzinfo=UTC)):
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
