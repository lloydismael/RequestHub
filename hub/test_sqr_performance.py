"""Frozen-date normalization and isolated read-only snapshot endpoint regressions."""

import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User
from hub.models import SqrProjectPerformance, SqrSubmission, SqrSubmissionHistory
from hub.services.sqr_performance import build_sqr_performance_snapshot, normalize_sqr_project
from hub.views import SqrListView, SqrPerformanceDataView, _sqr_queryset_for_user


TODAY = date(2026, 9, 28)
FETCHED_AT = datetime(2026, 9, 27, 16, 30, tzinfo=dt_timezone.utc)
PROJECT_KEYS = {
    "pk", "reference_code", "project_name", "customer_name", "pm_id", "pm_name",
    "pm_fallback", "sse_name", "status_key", "status_label", "health_key", "health_label",
    "progress", "start_date", "target_date", "actual_finish_date", "completion_signed_date",
    "updates_risks", "active_delivery", "delivery_candidate", "needs_attention", "overdue",
    "due_soon", "days_to_target", "missing_data", "inconsistencies", "reasons", "priority",
    "search_text",
}


class SqrPerformanceNormalizationTests(SimpleTestCase):
    """SimpleTestCase forbids DB access, including accidentally lazy relations."""

    def row(self, **changes):
        pm = User(pk=2, username="reviewer", first_name="Review", last_name="Owner")
        values = {
            "pk": 1, "reference_code": "SQR-001", "customer_name": "Customer",
            "project_title": "Project", "pm_esg_reviewer": pm,
            "assigned_pm": None, "assigned_sse": None,
        }
        values.update(changes)
        return SqrSubmission(**values)

    def project(self, **changes):
        return normalize_sqr_project(self.row(**changes), today=TODAY)

    def clean(self, **changes):
        values = {
            "assigned_pm": User(pk=3, username="assigned"),
            "overall_status": "in_progress", "delivery_health": "on_track",
            "delivery_progress": 40, "delivery_start_date": TODAY - timedelta(days=30),
            "delivery_target_finish_date": TODAY + timedelta(days=40),
        }
        values.update(changes)
        return self.row(**values)

    def test_exact_contract_and_explicit_times(self):
        snapshot = build_sqr_performance_snapshot([self.row()], today=TODAY, fetched_at=FETCHED_AT)
        self.assertEqual(set(snapshot), {"ok", "as_of", "fetched_at", "projects"})
        self.assertIs(snapshot["ok"], True)
        self.assertEqual(snapshot["as_of"], "2026-09-28")
        self.assertEqual(snapshot["fetched_at"], "2026-09-27T16:30:00+00:00")
        self.assertEqual(set(snapshot["projects"][0]), PROJECT_KEYS)
        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)

    def test_empty_portfolio(self):
        self.assertEqual(build_sqr_performance_snapshot([], today=TODAY, fetched_at=FETCHED_AT)["projects"], [])

    def test_plain_proposal_is_not_deficient_delivery(self):
        project = self.project(key_updates_risks="Blocked!", assigned_pm=User(pk=4, username="pm"))
        for key in ("active_delivery", "delivery_candidate", "needs_attention", "overdue", "due_soon"):
            self.assertIs(project[key], False)
        self.assertEqual(project["missing_data"], [])
        self.assertEqual(project["priority"], 6)

    def test_every_recorded_enum_label_preserved(self):
        for field, prefix, choices in (
            ("overall_status", "status", SqrSubmission.OverallStatus.choices),
            ("delivery_health", "health", SqrSubmission.DeliveryHealth.choices),
        ):
            for value, label in [*choices, ("", "Not set"), (None, "Not set"), ("future_value", "future_value")]:
                with self.subTest(field=field, value=value):
                    project = self.project(**{field: value})
                    self.assertEqual(project[f"{prefix}_key"], value or "not_set")
                    self.assertEqual(project[f"{prefix}_label"], label)

    def test_all_proposal_statuses(self):
        for value in ["", *SqrSubmission.ProposalStatus.values]:
            with self.subTest(value=value):
                project = self.project(proposal_status=value)
                self.assertEqual(project["active_delivery"], value == "closed_won")
                self.assertEqual(project["delivery_candidate"], value == "closed_won")

    def test_operational_evidence_including_zero(self):
        for changes in (
            {"overall_status": "planning"}, {"overall_status": "in_progress"},
            {"overall_status": "on_hold"}, {"delivery_health": "on_track"},
            {"delivery_progress": 0}, {"delivery_start_date": TODAY},
            {"delivery_target_finish_date": TODAY},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(self.project(**changes)["active_delivery"])

    def test_terminal_evidence_suppresses_operational_alerts(self):
        for terminal in (
            {"overall_status": "completed"}, {"overall_status": "cancelled"},
            {"delivery_health": "completed"}, {"delivery_health": "cancelled"},
            {"delivery_actual_finish_date": TODAY}, {"delivery_completion_signed_date": TODAY},
            {"proposal_status": "closed_lost"}, {"proposal_status": "closed_canceled"},
        ):
            for delta in (-1, 0, 14):
                values = {"proposal_status": "closed_won", "delivery_health": "off_track",
                          "overall_status": "on_hold", "delivery_target_finish_date": TODAY + timedelta(days=delta)}
                values.update(terminal)
                with self.subTest(terminal=terminal, delta=delta):
                    project = self.project(**values)
                    self.assertFalse(project["active_delivery"])
                    self.assertFalse(project["overdue"])
                    self.assertFalse(project["due_soon"])
                    self.assertTrue(all(reason["key"] in {"missing_data", "inconsistent_data"} for reason in project["reasons"]))

    def test_progress_clamped_and_unknown_is_not_zero(self):
        for raw, expected in ((None, None), (0, 0), (-10, 0), (105, 100), ("42", 42), ("bad", None), (float("inf"), None)):
            with self.subTest(raw=raw):
                project = self.project(proposal_status="closed_won", delivery_progress=raw)
                self.assertEqual(project["progress"], expected)
                self.assertEqual("Progress" in project["missing_data"], expected is None)

    def test_dates_inclusive_boundaries_and_no_target(self):
        for delta in (-1, 0, 14, 15, 30, 31, None):
            with self.subTest(delta=delta):
                project = self.project(delivery_target_finish_date=TODAY + timedelta(days=delta) if delta is not None else None)
                self.assertEqual(project["days_to_target"], delta)
                self.assertEqual(project["overdue"], delta == -1)
                self.assertEqual(project["due_soon"], delta in (0, 14))

    def test_open_100_stays_overdue_and_recorded_health_unchanged(self):
        project = self.project(delivery_health="on_track", delivery_progress=100, delivery_target_finish_date=TODAY - timedelta(days=1))
        self.assertTrue(project["overdue"])
        self.assertEqual(project["health_key"], "on_track")
        self.assertEqual(project["health_label"], "On Track")
        self.assertIn("Open delivery has progress at 100%", project["inconsistencies"])

    def test_assigned_owner_precedes_reviewer_and_sse_is_explicit(self):
        project = self.project(assigned_pm=User(pk=3, username="assigned"), assigned_sse=User(pk=4, username="sse"))
        self.assertEqual((project["pm_id"], project["pm_name"], project["pm_fallback"]), ("3", "assigned", False))
        self.assertEqual(project["sse_name"], "sse")

    def test_reviewer_fallback_does_not_hide_missing_assigned_pm(self):
        project = self.project(proposal_status="closed_won")
        self.assertEqual((project["pm_id"], project["pm_name"], project["pm_fallback"]), ("2", "Review Owner", True))
        self.assertEqual(project["missing_data"], ["Assigned PM", "Delivery health", "Progress", "Start date", "Target date"])

    def test_completed_under_100_and_unknown_progress(self):
        for terminal in ({"overall_status": "completed"}, {"delivery_health": "completed"}, {"delivery_actual_finish_date": TODAY}, {"delivery_completion_signed_date": TODAY}):
            with self.subTest(terminal=terminal):
                self.assertIn("Completed delivery has progress below 100%", self.project(delivery_progress=99, **terminal)["inconsistencies"])
                self.assertNotIn("Completed delivery has progress below 100%", self.project(delivery_progress=None, **terminal)["inconsistencies"])

    def test_contradictory_completion_and_cancellation(self):
        for changes in (
            {"overall_status": "completed", "delivery_health": "cancelled"},
            {"overall_status": "cancelled", "delivery_health": "completed"},
            {"proposal_status": "closed_canceled", "delivery_actual_finish_date": TODAY},
        ):
            with self.subTest(changes=changes):
                self.assertIn("Conflicting completed and cancelled signals", self.project(**changes)["inconsistencies"])

    def test_terminal_vs_open_signals(self):
        for changes in (
            {"overall_status": "in_progress", "delivery_actual_finish_date": TODAY},
            {"delivery_health": "on_track", "overall_status": "completed"},
            {"overall_status": "planning", "delivery_health": "cancelled"},
        ):
            with self.subTest(changes=changes):
                self.assertIn("Terminal delivery conflicts with open status or health", self.project(**changes)["inconsistencies"])

    def test_date_order_and_malformed_dates(self):
        project = self.project(delivery_start_date=TODAY, delivery_target_finish_date=TODAY - timedelta(days=1), delivery_actual_finish_date=TODAY - timedelta(days=2))
        self.assertIn("Start date is after target date", project["inconsistencies"])
        self.assertIn("Actual finish date is before start date", project["inconsistencies"])
        for field, result_key, label in (
            ("delivery_start_date", "start_date", "start date"),
            ("delivery_target_finish_date", "target_date", "target date"),
            ("delivery_actual_finish_date", "actual_finish_date", "actual finish date"),
            ("delivery_completion_signed_date", "completion_signed_date", "completion signed date"),
        ):
            with self.subTest(field=field):
                project = self.project(**{field: "2026-02-30"})
                self.assertIsNone(project[result_key])
                self.assertIn(f"Invalid {label}", project["inconsistencies"])

    def test_date_serialization(self):
        project = self.project(delivery_start_date=TODAY, delivery_target_finish_date="2026-10-01", delivery_actual_finish_date=TODAY, delivery_completion_signed_date=TODAY)
        self.assertEqual(project["start_date"], "2026-09-28")
        self.assertEqual(project["target_date"], "2026-10-01")
        self.assertEqual(project["actual_finish_date"], "2026-09-28")
        self.assertEqual(project["completion_signed_date"], "2026-09-28")

    def test_rank_and_stable_target_pk_order(self):
        rows = [
            self.clean(pk=7),
            self.clean(pk=6, delivery_progress=None),
            self.clean(pk=5, delivery_target_finish_date=TODAY),
            self.clean(pk=4, overall_status="on_hold"),
            self.clean(pk=3, delivery_health="at_risk"),
            self.clean(pk=2, delivery_target_finish_date=TODAY - timedelta(days=1)),
            self.clean(pk=1, delivery_health="off_track"),
        ]
        projects = build_sqr_performance_snapshot(rows, today=TODAY, fetched_at=FETCHED_AT)["projects"]
        self.assertEqual([p["priority"] for p in projects], list(range(7)))
        self.assertEqual([p["pk"] for p in projects], list(range(1, 8)))
        rows = [self.clean(pk=pk, delivery_health="off_track", delivery_target_finish_date=target) for pk, target in (
            (4, None), (3, TODAY), (2, TODAY - timedelta(days=1)), (1, TODAY),
        )]
        projects = build_sqr_performance_snapshot(rows, today=TODAY, fetched_at=FETCHED_AT)["projects"]
        self.assertEqual([p["pk"] for p in projects], [2, 1, 3, 4])

    def test_multiple_reasons_do_not_duplicate_project(self):
        row = self.row(overall_status="on_hold", delivery_health="off_track", delivery_progress=100, delivery_target_finish_date=TODAY - timedelta(days=1))
        projects = build_sqr_performance_snapshot([row], today=TODAY, fetched_at=FETCHED_AT)["projects"]
        self.assertEqual(len(projects), 1)
        keys = [reason["key"] for reason in projects[0]["reasons"]]
        self.assertEqual(keys, ["off_track", "overdue", "on_hold", "missing_data", "inconsistent_data"])
        self.assertEqual(len(keys), len(set(keys)))

    def test_no_mutation_and_text_is_data_not_markup(self):
        row = self.row(project_title="<script>alert(1)</script>", key_updates_risks="Risk & UPDATE", delivery_progress=150)
        before = row.__dict__.copy()
        project = normalize_sqr_project(row, today=TODAY)
        self.assertEqual(row.__dict__, before)
        self.assertEqual(project["project_name"], "<script>alert(1)</script>")
        self.assertEqual(project["updates_risks"], "Risk & UPDATE")
        self.assertIn("risk & update", project["search_text"])

    def test_search_preserves_unicode_for_browser_lowercase_matching(self):
        project = self.project(project_title="Straße", customer_name="Großkunde")
        self.assertIn("straße", project["search_text"])
        self.assertIn("großkunde", project["search_text"])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SqrPerformanceEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pm = User.objects.create_user(username="snapshot-pm", role=User.Roles.PM_ESG)
        cls.admin = User.objects.create_user(username="snapshot-admin", role=User.Roles.ADMIN)
        cls.engineer = User.objects.create_user(username="snapshot-engineer", role=User.Roles.ENGINEER)
        cls.other_engineer = User.objects.create_user(username="snapshot-other", role=User.Roles.ENGINEER)
        cls.submission = SqrSubmission.objects.create(
            engineer=cls.engineer, pm_esg_reviewer=cls.pm, assigned_pm=cls.pm,
            assigned_sse=cls.engineer, project_title="Snapshot <unsafe>", customer_name="Customer",
            proposal_status="closed_won", delivery_progress=0,
        )

    def setUp(self):
        self.url = reverse("hub:sqr-performance-data")

    def test_authorized_roles_get_contract_and_private_no_store(self):
        self.assertEqual(self.url, "/sqr/performance-data/")
        for user in (self.pm, self.admin):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                with patch("hub.views.timezone.now", return_value=FETCHED_AT):
                    response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/json")
                self.assertIn("private", response["Cache-Control"])
                self.assertIn("no-store", response["Cache-Control"])
                data = response.json()
                self.assertEqual(data["as_of"], "2026-09-28")
                self.assertEqual(data["fetched_at"], FETCHED_AT.isoformat())
                self.assertEqual(set(data["projects"][0]), PROJECT_KEYS)
                self.assertEqual(data["projects"][0]["project_name"], "Snapshot <unsafe>")

    def test_every_other_role_is_denied_without_reading_projects(self):
        for role in User.Roles.values:
            if role in (User.Roles.ADMIN, User.Roles.PM_ESG):
                continue
            with self.subTest(role=role):
                request = RequestFactory().get(self.url)
                request.user = User(pk=999, username="denied", role=role)
                with self.assertNumQueries(0):
                    response = SqrPerformanceDataView.as_view()(request)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(json.loads(response.content), {"ok": False, "error": "Permission denied"})
                self.assertIn("no-store", response["Cache-Control"])

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response.url)
        self.assertIn("no-store", response["Cache-Control"])

    def test_get_only_including_head_and_options(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"):
            with self.subTest(method=method):
                request = RequestFactory().generic(method, self.url)
                request.user = self.pm
                with self.assertNumQueries(0):
                    response = SqrPerformanceDataView.as_view()(request)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET")
                self.assertIn("no-store", response["Cache-Control"])

    def test_no_writes_no_history_or_legacy_reads_no_full_context(self):
        before = list(SqrSubmission.objects.values())
        legacy_before = list(SqrProjectPerformance.objects.values())
        history_before = list(SqrSubmissionHistory.objects.values())
        self.client.force_login(self.pm)
        with patch.object(SqrListView, "get_context_data", side_effect=AssertionError("Full context called")):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for query in queries:
            sql = query["sql"].upper()
            self.assertTrue(sql.lstrip().startswith("SELECT"), sql)
            self.assertNotIn("HUB_SQRPROJECTPERFORMANCE", sql)
            self.assertNotIn("HUB_SQRSUBMISSIONHISTORY", sql)
        self.assertEqual(list(SqrSubmission.objects.values()), before)
        self.assertEqual(list(SqrProjectPerformance.objects.values()), legacy_before)
        self.assertEqual(list(SqrSubmissionHistory.objects.values()), history_before)

    def test_constant_queries_for_one_and_one_hundred_related_rows(self):
        self.client.force_login(self.pm)
        with CaptureQueriesContext(connection) as one:
            response = self.client.get(self.url)
        self.assertEqual(len(response.json()["projects"]), 1)
        SqrSubmission.objects.bulk_create([
            SqrSubmission(
                engineer=self.other_engineer, pm_esg_reviewer=self.admin,
                assigned_pm=self.admin if index % 2 else None, assigned_sse=self.other_engineer,
                reviewed_by=self.admin, project_title=f"Project {index}", customer_name="Other",
                year=2026, reference_code=f"SNAPSHOT-{index}",
            ) for index in range(99)
        ])
        with CaptureQueriesContext(connection) as hundred:
            response = self.client.get(self.url)
        self.assertEqual(len(response.json()["projects"]), 100)
        self.assertEqual(len(one), len(hundred))
        self.assertEqual(sum('FROM "hub_sqrsubmission"' in q["sql"] for q in hundred), 1)
        self.assertLessEqual(len(hundred), 3)

    def test_shared_scope_and_joins_match_tracker_exactly(self):
        other = SqrSubmission.objects.create(engineer=self.other_engineer, pm_esg_reviewer=self.admin, customer_name="Other", project_title="Other")
        for user, expected in ((self.engineer, {self.submission.pk}), (self.other_engineer, {other.pk}), (self.pm, {self.submission.pk, other.pk}), (self.admin, {self.submission.pk, other.pk})):
            with self.subTest(role=user.role, pk=user.pk):
                view = SqrListView()
                view.request = RequestFactory().get("/sqr/")
                view.request.user = user
                shared = _sqr_queryset_for_user(user)
                self.assertEqual(str(view.get_queryset().query), str(shared.query))
                self.assertEqual(set(shared.values_list("pk", flat=True)), expected)
                self.assertEqual(shared.query.order_by, ("-created_at", "-pk"))
                self.assertEqual(shared.query.select_related, {
                    "engineer": {}, "pm_esg_reviewer": {}, "reviewed_by": {},
                    "assigned_pm": {}, "assigned_sse": {}, "linked_request": {"account": {}},
                })

    def test_empty_portfolio_response(self):
        SqrSubmission.objects.all().delete()
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(self.url).json()["projects"], [])