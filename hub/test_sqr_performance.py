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
    "pm_fallback", "sse_name", "status_key", "status_label",
    "progress", "start_date", "actual_finish_date",
    "updates_risks", "active_delivery", "delivery_candidate", "needs_attention",
    "missing_data", "inconsistencies", "reasons", "priority", "search_text", "attention",
    "portfolio_status", "indicators", "overall_rating", "overall_label", "assessed_count", "assessment_total",
    "status", "overall_status", "proposal_status", "approver_id", "sse_id", "scope", "group",
    "billing_type", "billing_state", "linked_request_id", "stage", "total",
    "schedule_performance", "budget_effort_performance", "quality_performance",
    "delivery_start_date", "delivery_actual_finish_date", "post_svc_warranty_end_date",
    "managed_support_start_date", "managed_support_end_date",
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
            "overall_status": "in_progress", "schedule_performance": "on_track",
            "budget_effort_performance": "within_budget", "quality_performance": "met",
            "delivery_progress": 40, "delivery_start_date": TODAY - timedelta(days=30),
        }
        values.update(changes)
        return self.row(**values)

    def test_ppt_matches_saved_sqr_assessments_with_coverage(self):
        project = self.project(
            overall_status="in_progress", schedule_performance="on_track",
            budget_effort_performance="at_risk", quality_performance="unmet",
            delivery_health="on_track", delivery_progress=100,
        )
        self.assertEqual(project["indicators"], {
            "schedule_performance": {"key": "on_track", "label": "On Track", "state": "good"},
            "budget_effort_performance": {"key": "at_risk", "label": "At Risk", "state": "warning"},
            "quality_performance": {"key": "unmet", "label": "Unmet", "state": "danger"},
        })
        self.assertEqual((project["overall_rating"], project["overall_label"]), ("danger", "Action needed"))
        self.assertEqual((project["assessed_count"], project["assessment_total"]), (3, 3))

    def test_partial_assessment_keeps_unknown_coverage_and_invalid_values_visible(self):
        project = self.project(schedule_performance="on_track", quality_performance="future")
        self.assertEqual((project["overall_rating"], project["overall_label"], project["assessed_count"]),
                         ("unknown", "Incomplete assessment", 1))
        self.assertEqual(project["indicators"]["quality_performance"],
                         {"key": "", "label": "Not assessed", "state": "unknown"})
        self.assertIn("Invalid quality_performance assessment.", project["inconsistencies"])
        self.assertEqual(project["quality_performance"], "")

    def test_target_and_signed_date_no_longer_drive_active_calculations(self):
        before = self.project()
        after = self.project(delivery_target_finish_date="invalid", delivery_completion_signed_date=TODAY)
        self.assertEqual(after, before)
        for retired in ("target_date", "completion_signed_date", "days_to_target", "overdue", "due_soon", "health_key", "health_label"):
            self.assertNotIn(retired, after)
        self.assertNotIn("Target date", after["missing_data"])

    def test_legacy_terminal_evidence_remains_compatible_without_rating_inference(self):
        for evidence in ({"legacy_delivery_completed": True}, {"delivery_health": "completed"},
                         {"delivery_actual_finish_date": TODAY}):
            with self.subTest(evidence=evidence):
                project = self.project(**evidence)
                self.assertEqual(project["portfolio_status"], "closed")
                self.assertEqual((project["overall_rating"], project["assessed_count"]), ("unknown", 0))
        self.assertEqual(self.project(delivery_health="cancelled")["portfolio_status"], "removed")

    def test_on_hold_attention_separate_from_assessment(self):
        project = self.project(overall_status="on_hold", schedule_performance="on_track",
                               budget_effort_performance="within_budget", quality_performance="met")
        self.assertTrue(project["needs_attention"])
        self.assertIn("on_hold", [reason["key"] for reason in project["reasons"]])
        self.assertEqual((project["overall_rating"], project["overall_label"], project["assessed_count"]), ("good", "Healthy", 3))

    def test_recorded_amber_red_add_attention_without_replacing_rating(self):
        project = self.project(quality_performance="unmet", budget_effort_performance="at_risk")
        self.assertTrue(project["needs_attention"])
        self.assertEqual((project["overall_rating"], project["assessed_count"]), ("danger", 2))
        self.assertEqual([reason["key"] for reason in project["reasons"]][:2],
                         ["quality_performance", "budget_effort_performance"])

    def test_recorded_legacy_health_cannot_create_active_rating_or_attention(self):
        before = self.project()
        for health in ("on_track", "at_risk", "off_track", "future"):
            with self.subTest(health=health):
                self.assertEqual(self.project(delivery_health=health), before)

    def test_sort_ignores_retired_target_and_uses_identity_then_pk(self):
        rows = [self.row(pk=3, project_title="B", delivery_target_finish_date=TODAY),
                self.row(pk=2, project_title="A", delivery_target_finish_date=TODAY - timedelta(days=1)),
                self.row(pk=1, project_title="A", quality_performance="unmet"),
                self.row(pk=4, project_title="A", delivery_target_finish_date=TODAY - timedelta(days=90))]
        projects = build_sqr_performance_snapshot(rows, today=TODAY, fetched_at=FETCHED_AT)["projects"]
        self.assertEqual([p["pk"] for p in projects], [1, 2, 4, 3])

    def test_malformed_maintenance_dates_are_attention_visible(self):
        row = self.clean(overall_status="completed", delivery_progress=100,
                         delivery_actual_finish_date=TODAY, project_details="Implementation",
                         managed_support_amount=0, managed_support_start_date="2026-02-30")
        project = normalize_sqr_project(row, today=TODAY)
        self.assertIsNone(project["managed_support_start_date"])
        self.assertIn("Invalid maintenance start date", project["inconsistencies"])
        self.assertTrue(project["needs_attention"])
        self.assertIn("inconsistent_data", {reason["key"] for reason in project["reasons"]})

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

    def test_portfolio_status_uses_terminal_evidence_and_preserves_pipeline(self):
        for changes, expected in (
            ({}, "pipeline"),
            ({"overall_status": "planning"}, "pipeline"),
            ({"overall_status": "in_progress"}, "ongoing"),
            ({"proposal_status": "closed_won"}, "ongoing"),
            ({"delivery_actual_finish_date": TODAY}, "closed"),
            ({"delivery_health": "completed"}, "closed"),
            ({"overall_status": "cancelled"}, "removed"),
            ({"proposal_status": "closed_lost"}, "removed"),
            ({"overall_status": "completed", "delivery_health": "cancelled"}, "removed"),
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.project(**changes)["portfolio_status"], expected)

    def test_recorded_schedule_is_independent_of_delay_boundaries(self):
        for days in (-1, 0, 1, 14, 15):
            for value, expected in (("on_track", "good"), ("at_risk", "warning"), ("delayed", "danger")):
                with self.subTest(days=days, value=value):
                    project = self.project(overall_status="in_progress", schedule_performance=value,
                                           delivery_target_finish_date=TODAY - timedelta(days=days))
                    self.assertEqual(project["indicators"]["schedule_performance"]["state"], expected)
                    self.assertNotIn("overdue", {reason["key"] for reason in project["reasons"]})

    def test_finish_and_lifecycle_do_not_fill_unassessed_schedule(self):
        for changes in (
            {"overall_status": "completed", "delivery_actual_finish_date": TODAY},
            {"overall_status": "planning"}, {"overall_status": "cancelled"},
            {"delivery_start_date": TODAY + timedelta(days=1)},
        ):
            with self.subTest(changes=changes):
                project = self.project(**changes)
                self.assertEqual(project["indicators"]["schedule_performance"]["state"], "unknown")
                self.assertEqual(project["assessed_count"], 0)

    def test_unavailable_indicators_and_notes_never_invent_ratings(self):
        project = self.project(key_updates_risks="No issue. Scope change. Blocked resources.")
        self.assertEqual(project["overall_rating"], "unknown")
        self.assertEqual(set(project["indicators"]), {"schedule_performance", "budget_effort_performance", "quality_performance"})
        for indicator in project["indicators"].values():
            self.assertEqual(indicator, {"key": "", "state": "unknown", "label": "Not assessed"})

    def test_closed_project_does_not_inherit_open_on_hold_warning(self):
        project = self.project(
            overall_status="on_hold", delivery_health="completed",
            delivery_start_date=TODAY - timedelta(days=30),
            delivery_target_finish_date=TODAY - timedelta(days=5),
            delivery_actual_finish_date=TODAY - timedelta(days=10),
        )
        self.assertEqual(project["portfolio_status"], "closed")
        self.assertEqual(project["indicators"]["schedule_performance"]["state"], "unknown")
        self.assertEqual(project["overall_rating"], "unknown")
        self.assertNotIn("on_hold", {reason["key"] for reason in project["reasons"]})
        self.assertIn("Terminal delivery conflicts with open status", project["inconsistencies"])

    def test_recorded_assessment_rating_is_preserved_across_portfolio_lifecycle(self):
        for status in ("planning", "in_progress", "on_hold", "completed", "cancelled"):
            with self.subTest(status=status):
                project = self.project(overall_status=status, quality_performance="unmet")
                self.assertEqual(project["overall_rating"], "danger")
                self.assertEqual(project["assessed_count"], 1)

    def test_plain_proposal_is_not_deficient_delivery(self):
        project = self.project(key_updates_risks="Blocked!", assigned_pm=User(pk=4, username="pm"))
        for key in ("active_delivery", "delivery_candidate", "needs_attention"):
            self.assertIs(project[key], False)
        self.assertEqual(project["missing_data"], [])
        self.assertEqual(project["priority"], 6)

    def test_every_recorded_enum_label_preserved(self):
        for field, prefix, choices in (
            ("overall_status", "status", SqrSubmission.OverallStatus.choices),
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
            {"overall_status": "on_hold"}, {"schedule_performance": "on_track"},
            {"delivery_progress": 0}, {"delivery_start_date": TODAY},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(self.project(**changes)["active_delivery"])

    def test_terminal_evidence_suppresses_operational_alerts(self):
        for terminal in (
            {"overall_status": "completed"}, {"overall_status": "cancelled"},
            {"delivery_health": "completed"}, {"delivery_health": "cancelled"},
            {"delivery_actual_finish_date": TODAY}, {"legacy_delivery_completed": True},
            {"proposal_status": "closed_lost"}, {"proposal_status": "closed_canceled"},
        ):
            for delta in (-1, 0, 14):
                values = {"proposal_status": "closed_won", "delivery_health": "off_track",
                          "overall_status": "on_hold", "delivery_target_finish_date": TODAY + timedelta(days=delta)}
                values.update(terminal)
                with self.subTest(terminal=terminal, delta=delta):
                    project = self.project(**values)
                    self.assertFalse(project["active_delivery"])
                    self.assertNotIn("overdue", project)
                    self.assertNotIn("due_soon", project)
                    self.assertTrue(all(reason["key"] in {"missing_data", "inconsistent_data"} for reason in project["reasons"]))

    def test_progress_clamped_and_unknown_is_not_zero(self):
        for raw, expected in ((None, None), (0, 0), (-10, 0), (105, 100), ("42", 42), ("bad", None), (float("inf"), None)):
            with self.subTest(raw=raw):
                project = self.project(proposal_status="closed_won", delivery_progress=raw)
                self.assertEqual(project["progress"], expected)
                self.assertEqual("Progress" in project["missing_data"], expected is None)

    def test_retired_target_never_adds_delivery_evidence_or_attention(self):
        for delta in (-1, 0, 14, 15, 30, 31, None):
            with self.subTest(delta=delta):
                project = self.project(delivery_target_finish_date=TODAY + timedelta(days=delta) if delta is not None else None)
                self.assertFalse(project["active_delivery"])
                self.assertFalse(project["needs_attention"])
                self.assertEqual(project["overall_rating"], "unknown")

    def test_open_100_preserves_recorded_rating_and_adds_inconsistency(self):
        project = self.project(schedule_performance="delayed", delivery_progress=100,
                               delivery_target_finish_date=TODAY - timedelta(days=1))
        self.assertEqual(project["overall_rating"], "danger")
        self.assertEqual(project["assessed_count"], 1)
        self.assertIn("Open delivery has progress at 100%", project["inconsistencies"])

    def test_assigned_owner_precedes_reviewer_and_sse_is_explicit(self):
        project = self.project(assigned_pm=User(pk=3, username="assigned"), assigned_sse=User(pk=4, username="sse"))
        self.assertEqual((project["pm_id"], project["pm_name"], project["pm_fallback"]), ("3", "assigned", False))
        self.assertEqual(project["sse_name"], "sse")
        self.assertEqual(project["approver_id"], 2)
        self.assertEqual(project["sse_id"], "4")

    def test_shared_filter_adapter_uses_canonical_billing_and_manual_dates_without_queries(self):
        project = self.project(
            project_details="Deployment Only", customer_company="Example Group",
            revenue_source="milestone", revenue_date=TODAY, linked_request_id=9,
            quotation_total_price=125, managed_support_amount=0,
            delivery_actual_finish_date=date(2026, 9, 1),
            delivery_completion_signed_date=date(2026, 8, 1),
            managed_support_start_date=date(2026, 10, 3),
            managed_support_end_date=date(2026, 10, 31),
        )
        self.assertEqual(project["scope"], "Deployment Only")
        self.assertEqual(project["group"], "Example Group")
        self.assertEqual(project["billing_type"], "milestone")
        self.assertEqual(project["billing_state"], "billed")
        self.assertEqual(project["linked_request_id"], "9")
        self.assertEqual(project["total"], "125")
        self.assertEqual(project["post_svc_warranty_end_date"], "2026-10-01")
        self.assertEqual(project["managed_support_start_date"], "2026-10-03")
        self.assertEqual(project["managed_support_end_date"], "2026-10-31")

    def test_reviewer_fallback_does_not_hide_missing_assigned_pm(self):
        project = self.project(proposal_status="closed_won")
        self.assertEqual((project["pm_id"], project["pm_name"], project["pm_fallback"]), ("2", "Review Owner", True))
        self.assertEqual(project["missing_data"], ["Assigned PM", "Progress", "Start date", "Schedule Performance", "Budget/Effort Performance", "Quality Performance"])
        self.assertEqual(project["approver_id"], 2)

    def test_completed_under_100_and_unknown_progress(self):
        for terminal in ({"overall_status": "completed"}, {"delivery_health": "completed"}, {"delivery_actual_finish_date": TODAY}, {"legacy_delivery_completed": True}):
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
            {"overall_status": "on_hold", "legacy_delivery_completed": True},
            {"overall_status": "planning", "delivery_health": "cancelled"},
        ):
            with self.subTest(changes=changes):
                self.assertIn("Terminal delivery conflicts with open status", self.project(**changes)["inconsistencies"])

    def test_date_order_and_malformed_dates(self):
        project = self.project(delivery_start_date=TODAY, delivery_target_finish_date=TODAY - timedelta(days=1), delivery_actual_finish_date=TODAY - timedelta(days=2))
        self.assertIn("Actual finish date is before start date", project["inconsistencies"])
        for field, result_key, label in (
            ("delivery_start_date", "start_date", "start date"),
            ("delivery_actual_finish_date", "actual_finish_date", "actual finish date"),
        ):
            with self.subTest(field=field):
                project = self.project(**{field: "2026-02-30"})
                self.assertIsNone(project[result_key])
                self.assertIn(f"Invalid {label}", project["inconsistencies"])

    def test_date_serialization(self):
        project = self.project(delivery_start_date=TODAY, delivery_target_finish_date="2026-10-01", delivery_actual_finish_date=TODAY, delivery_completion_signed_date=TODAY)
        self.assertEqual(project["start_date"], "2026-09-28")
        self.assertEqual(project["delivery_start_date"], "2026-09-28")
        self.assertEqual(project["actual_finish_date"], "2026-09-28")
        self.assertEqual(project["delivery_actual_finish_date"], "2026-09-28")

    def test_attention_priority_precedes_identity_and_pk(self):
        rows = [self.clean(pk=5), self.clean(pk=4, delivery_progress=None),
                self.clean(pk=3, overall_status="on_hold"),
                self.clean(pk=2, budget_effort_performance="at_risk"),
                self.clean(pk=1, quality_performance="unmet")]
        projects = build_sqr_performance_snapshot(rows, today=TODAY, fetched_at=FETCHED_AT)["projects"]
        self.assertEqual([p["priority"] for p in projects], [0, 2, 3, 5, 6])
        self.assertEqual([p["pk"] for p in projects], [1, 2, 3, 4, 5])

    def test_multiple_reasons_do_not_duplicate_project(self):
        row = self.row(overall_status="on_hold", quality_performance="unmet", delivery_progress=100)
        projects = build_sqr_performance_snapshot([row], today=TODAY, fetched_at=FETCHED_AT)["projects"]
        self.assertEqual(len(projects), 1)
        keys = [reason["key"] for reason in projects[0]["reasons"]]
        self.assertEqual(keys, ["quality_performance", "on_hold", "missing_data", "inconsistent_data"])
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