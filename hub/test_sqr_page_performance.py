from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from hub.models import SqrSubmission
from hub.views import SqrListView, SqrPrototypeDataView


class SqrPagePerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='sqr-perf-admin', role=User.Roles.ADMIN)
        cls.pm = User.objects.create(username='sqr-perf-pm', role=User.Roles.PM_ESG)
        cls.engineer = User.objects.create(username='sqr-perf-engineer', role=User.Roles.ENGINEER)
        SqrSubmission.objects.bulk_create([
            SqrSubmission(reference_code=f'SQR-PERF-{i:03}', year=2026, sequence_number=i + 1,
                          engineer=cls.engineer, pm_esg_reviewer=cls.pm,
                          customer_name='Hidden needle' if i == 0 else 'Example account', project_title='Service',
                          status=SqrSubmission.Status.FOR_PROCESSING)
            for i in range(151)
        ])

    def context(self, params=None, user=None):
        request = RequestFactory().get('/sqr/', params or {})
        request.user = user or self.admin
        view = SqrListView()
        view.request = request
        return view.get_context_data()

    def test_tracker_fetch_is_bounded_and_counts_cover_full_scope(self):
        with CaptureQueriesContext(connection) as queries:
            context = self.context()
        self.assertEqual(len(context['proposal_submissions']), 50)
        self.assertEqual(context['proposal_counts']['total'], 151)
        self.assertTrue(any('LIMIT 50' in q['sql'] for q in queries))

    def test_tracker_does_not_compute_report_only_payload(self):
        self.assertIsNone(self.context().get('sqr_reports_summary'))
        self.assertIsNotNone(self.context({'tab': 'reports'})['sqr_reports_summary'])

    def test_search_spans_all_pages(self):
        context = self.context({'q': 'needle'})
        self.assertEqual([s.reference_code for s in context['proposal_submissions']], ['SQR-PERF-000'])

    def test_pm_assigned_default_preserved(self):
        SqrSubmission.objects.filter(reference_code='SQR-PERF-000').update(pm_esg_reviewer=self.admin)
        context = self.context(user=self.pm)
        self.assertEqual(context['page_obj'].paginator.count, 150)
        self.assertEqual(context['proposal_counts']['total'], 151)
        self.assertEqual(self.context({'assigned': 'all'}, self.pm)['page_obj'].paginator.count, 151)

    def test_invalid_focus_parameters_do_not_reach_database_as_invalid_ids(self):
        for value in ('²', '999999999999999999999999999999'):
            self.assertEqual(self.context({'focus': value})['page_obj'].paginator.count, 0)

    def test_extreme_amount_filters_are_safely_bounded(self):
        self.assertEqual(self.context({'amount_min': '1e1000000'})['page_obj'].paginator.count, 0)
        self.assertEqual(self.context({'amount_max': '1e1000000'})['page_obj'].paginator.count, 151)

    def test_engineer_scope_preserved(self):
        SqrSubmission.objects.filter(reference_code='SQR-PERF-000').update(engineer=self.admin)
        context = self.context(user=self.engineer)
        self.assertEqual(context['proposal_counts']['total'], 150)
        self.assertEqual(context['page_obj'].paginator.count, 150)

    def test_prototype_stage_matches_delivery_completion_contract(self):
        from datetime import date
        import json
        row = SqrSubmission.objects.get(reference_code='SQR-PERF-000')
        for health, signed, stage in [('completed', None, 'billing'), ('', date(2026, 11, 1), 'delivery')]:
            SqrSubmission.objects.filter(pk=row.pk).update(proposal_status='closed_won', status='reviewed',
                delivery_health=health, delivery_completion_signed_date=signed, delivery_actual_finish_date=None)
            request = RequestFactory().get('/sqr/prototype-data/', {'page': '1', 'stage': stage})
            request.user = self.admin
            data = json.loads(SqrPrototypeDataView.as_view()(request).content)
            self.assertEqual(data['pipeline'][stage]['count'], 1)
            self.assertEqual([record['stage'] for record in data['records']], [stage])

    def test_classic_search_matches_displayed_status_labels(self):
        SqrSubmission.objects.filter(reference_code='SQR-PERF-000').update(status='reviewed')
        context = self.context({'q': 'Approved'})
        self.assertEqual([row.reference_code for row in context['proposal_submissions']], ['SQR-PERF-000'])
        self.assertEqual(self.context({'q': 'For Processing'})['page_obj'].paginator.count, 150)

    def test_classic_search_matches_displayed_dates_and_money(self):
        from datetime import datetime, timezone
        from decimal import Decimal
        SqrSubmission.objects.filter(reference_code='SQR-PERF-000').update(
            created_at=datetime(2020, 3, 20, 1, tzinfo=timezone.utc), sse_amount=Decimal('10000'))
        for query in ('Mar 20, 2020', '10,000.00'):
            context = self.context({'q': query})
            self.assertEqual([row.reference_code for row in context['proposal_submissions']], ['SQR-PERF-000'])

    def test_summary_refresh_is_lightweight_and_reflects_price_changes(self):
        import json
        from decimal import Decimal
        SqrSubmission.objects.filter(reference_code='SQR-PERF-000').update(managed_support_amount=Decimal('100'))
        request = RequestFactory().get('/sqr/prototype-data/', {'page': '1', 'summary': '1'})
        request.user = self.admin
        with CaptureQueriesContext(connection) as queries:
            data = json.loads(SqrPrototypeDataView.as_view()(request).content)
        self.assertEqual(Decimal(data['pipeline']['all']['total']), Decimal('100'))
        self.assertNotIn('records', data)
        self.assertLessEqual(len(queries), 2)

    def test_prototype_price_uses_half_even_rounding(self):
        from decimal import Decimal
        SqrSubmission.objects.filter(reference_code='SQR-PERF-000').update(
            sse_amount=Decimal('0.05'), discount_rate=10, quotation_total_price=Decimal('1.00'))
        request = RequestFactory().get('/sqr/prototype-data/', {'page': '1'})
        request.user = self.admin
        import json
        data = json.loads(SqrPrototypeDataView.as_view()(request).content)
        self.assertEqual(Decimal(data['pipeline']['all']['total']), Decimal('0.04'))

    def test_empty_search_still_has_pagination_controls(self):
        self.client.force_login(self.admin)
        response = self.client.get('/sqr/', {'q': 'definitely-no-record'})
        self.assertContains(response, 'id="sqr-pagination"')

    def test_prototype_opt_in_page_is_bounded(self):
        request = RequestFactory().get('/sqr/prototype-data/', {'page': '1', 'page_size': '25'})
        request.user = self.admin
        response = SqrPrototypeDataView.as_view()(request)
        import json
        data = json.loads(response.content)
        self.assertEqual(len(data['records']), 25)
        self.assertEqual(data['pagination']['total'], 151)
        self.assertEqual(data['pipeline']['all']['count'], 151)
