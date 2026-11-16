import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command, get_commands
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase

from accounts.models import User
from hub.models import SqrSubmission
from hub.services import sqr_performance, sqr_prototype


class AssessmentSnapshotTests(SimpleTestCase):
    def snapshot(self, **fields):
        builder = getattr(sqr_performance, "build_assessment_snapshot", None)
        self.assertIsNotNone(builder, "Recorded assessment snapshot is missing")
        return builder(SqrSubmission(**fields))

    def test_assessment_severity_and_coverage(self):
        cases = [
            ({}, "unknown", "Not assessed", 0),
            ({"schedule_performance": "on_track"}, "unknown", "Incomplete assessment", 1),
            ({"budget_effort_performance": "at_risk"}, "warning", "At Risk", 1),
            ({"quality_performance": "unmet"}, "danger", "Action needed", 1),
            ({"schedule_performance": "on_track", "budget_effort_performance": "within_budget",
              "quality_performance": "met"}, "good", "Healthy", 3),
            ({"schedule_performance": "at_risk", "budget_effort_performance": "over_budget",
              "quality_performance": "met"}, "danger", "Action needed", 3),
        ]
        for fields, rating, label, count in cases:
            with self.subTest(fields=fields):
                result = self.snapshot(**fields)
                self.assertEqual((result["overall_rating"], result["overall_label"], result["assessed_count"]),
                                 (rating, label, count))
                self.assertEqual(result["assessment_total"], 3)
                self.assertEqual(set(result["indicators"]),
                                 {"schedule_performance", "budget_effort_performance", "quality_performance"})

    def test_invalid_stored_assessment_is_neutral_and_flagged(self):
        result = self.snapshot(schedule_performance="<unsafe>", quality_performance="met")
        self.assertEqual(result["indicators"]["schedule_performance"],
                         {"key": "", "label": "Not assessed", "state": "unknown"})
        self.assertEqual(result["assessed_count"], 1)
        self.assertEqual(result["inconsistencies"], ["Invalid schedule_performance assessment."])

    def test_terminal_legacy_conflict_preserves_stage_without_green(self):
        builder = getattr(sqr_performance, "build_assessment_snapshot", None)
        self.assertIsNotNone(builder, "Recorded assessment snapshot is missing")
        row = SqrSubmission(proposal_status="closed_won", overall_status="cancelled",
                            delivery_health="completed", delivery_progress=100)
        self.assertEqual(sqr_prototype.derive_stage(row), ("closed", "canceled"))
        self.assertEqual(builder(row)["overall_rating"], "unknown")
        row.overall_status = "in_progress"
        self.assertEqual(sqr_prototype.derive_stage(row), ("billing", ""))
        self.assertEqual(builder(row)["assessed_count"], 0)
        row.delivery_health = "cancelled"
        self.assertEqual(sqr_prototype.derive_stage(row), ("closed", "canceled"))

    def test_legacy_signed_only_completion_stays_closed_without_finish(self):
        self.assertIn("legacy_delivery_completed", {field.name for field in SqrSubmission._meta.fields})
        row = SqrSubmission(proposal_status="closed_won", legacy_delivery_completed=True,
                            pm_esg_reviewer=User(pk=1, username="reviewer"))
        self.assertEqual(sqr_prototype.derive_stage(row), ("billing", ""))
        self.assertEqual(sqr_performance.normalize_sqr_project(row, today=date(2026, 11, 16))["portfolio_status"], "closed")
        row.legacy_delivery_completed = False
        row.delivery_completion_signed_date = date(2026, 11, 1)
        self.assertEqual(sqr_prototype.derive_stage(row), ("delivery", ""))
        self.assertEqual(sqr_performance.normalize_sqr_project(row, today=date(2026, 11, 16))["portfolio_status"], "ongoing")


    def test_preview_manual_dates_preserves_zero_amount_and_scope_eligibility(self):
        from decimal import Decimal
        from hub.management.commands.preview_sqr_unified_transition import build_transition_preview

        rows = [
            SqrSubmission(project_details="Implementation", managed_support_amount=Decimal("0"),
                          delivery_completion_signed_date=date(2026, 11, 1)),
            SqrSubmission(project_details="Implementation", managed_support_amount=Decimal("0"),
                          managed_support_start_date=date(2026, 11, 5),
                          managed_support_end_date=date(2026, 11, 4)),
            SqrSubmission(project_details="Staging and Configuration", managed_support_amount=Decimal("0"),
                          delivery_completion_signed_date=date(2026, 11, 1)),
        ]
        report = build_transition_preview(rows)
        self.assertEqual(report["maintenance"], {
            "invalid_pairs": 1, "computed_only_start": 1, "manual_end_differs_from_computed": 1,
        })
        self.assertEqual(report["warranty_differences"], {"post_service": 1, "general": 1})


class UnifiedTransitionTests(TransactionTestCase):
    before = ("hub", "0063_sqr_performance_assessments")
    after = ("hub", "0064_backfill_sqr_schedule_performance")

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        self.assertIn(self.after, executor.loader.graph.nodes, "Additive transition migration is missing")
        targets = [target] + [node for node in executor.loader.graph.leaf_nodes() if node[0] != "hub"]
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def migrate_fixtures(self):
        apps = self.migrate_to(self.before)
        users = apps.get_model("accounts", "User")
        engineer = users.objects.create(username="transition-engineer", role="engineer")
        pm = users.objects.create(username="transition-pm", role="pm_esg")
        rows = apps.get_model("hub", "SqrSubmission")
        for index, health in enumerate(("on_track", "at_risk", "off_track", "completed", "cancelled", "on_track")):
            rows.objects.create(
                engineer_id=engineer.pk, pm_esg_reviewer_id=pm.pk, year=2026,
                reference_code=f"LEGACY-{index}", project_title="Preserved", project_details="Deployment Only",
                proposal_status="closed_won", delivery_health=health,
                schedule_performance="delayed" if index == 5 else "",
                delivery_target_finish_date=date(2026, 11, 2),
                delivery_completion_signed_date=date(2026, 11, 3) if index == 2 else None,
                delivery_transition_metadata={"preserved": True},
            )
        apps.get_model("hub", "SqrTrackerSettings").objects.create(id=1, show_classic_sqr_tracker=False)
        self.migrate_to(self.after)
        return {row.reference_code: row for row in SqrSubmission.objects.all()}

    def test_backfill_only_safe_schedule_values(self):
        rows = self.migrate_fixtures()
        self.assertEqual([rows[f"LEGACY-{index}"].schedule_performance for index in range(6)],
                         ["on_track", "at_risk", "", "", "", "delayed"])
        self.assertTrue(rows["LEGACY-2"].legacy_delivery_completed)
        self.assertEqual(sqr_prototype.derive_stage(rows["LEGACY-2"]), ("billing", ""))
        self.assertTrue(all(row.budget_effort_performance == "" and row.quality_performance == "" for row in rows.values()))
        rows["LEGACY-0"].quality_performance = "unmet"
        rows["LEGACY-0"].save(update_fields=["quality_performance"])
        self.migrate_to(self.before)
        self.assertEqual(SqrSubmission.objects.get(pk=rows["LEGACY-0"].pk).quality_performance, "unmet")
        self.migrate_to(self.after)

    def test_fresh_and_existing_migration_state_preserved(self):
        rows = self.migrate_fixtures()
        from hub.models import SqrTrackerSettings
        self.assertFalse(SqrTrackerSettings.objects.get(pk=1).show_classic_sqr_tracker)
        for row in rows.values():
            self.assertEqual(row.delivery_transition_metadata, {"preserved": True})
            self.assertEqual(row.delivery_target_finish_date, date(2026, 11, 2))
        fresh = SqrSubmission.objects.create(engineer=User.objects.get(username="transition-engineer"),
                                             pm_esg_reviewer=User.objects.get(username="transition-pm"),
                                             customer_name="Fresh", project_title="Fresh")
        fresh.refresh_from_db()
        self.assertFalse(fresh.legacy_delivery_completed)
        self.assertEqual(fresh.schedule_performance, "")
        self.assertEqual(fresh.delivery_transition_metadata, {})

    def test_preview_is_read_only(self):
        self.assertIn("preview_sqr_unified_transition", get_commands(), "Read-only preview command is missing")
        self.migrate_fixtures()
        before = list(SqrSubmission.objects.order_by("pk").values())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "preview.json"
            call_command("preview_sqr_unified_transition", output=str(path), verbosity=0)
            report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(list(SqrSubmission.objects.order_by("pk").values()), before)
        self.assertEqual(report["total_records"], 6)
        self.assertEqual(report["off_track_needing_review"], 1)
        self.assertEqual(report["warranty_differences"]["post_service"], 1)
        self.assertEqual(report["warranty_differences"]["general"], 1)
