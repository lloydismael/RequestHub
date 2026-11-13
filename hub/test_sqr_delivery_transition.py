import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase

from hub.models import SqrSubmission, SqrSubmissionHistory
from hub.test_sqr_prototype import make


class SqrDeliveryTransitionValueTests(SimpleTestCase):
    def test_actual_finish_drives_warranty_without_mutating_manual_dates(self):
        row = make(project_details="Deployment Only", managed_support_amount=Decimal("0"),
                   delivery_actual_finish_date=date(2026, 11, 13),
                   delivery_completion_signed_date=date(2026, 11, 1),
                   managed_support_start_date=date(2026, 12, 1),
                   managed_support_end_date=date(2027, 2, 9))
        self.assertEqual(row.computed_post_svc_warranty_end_date, date(2026, 12, 13))
        self.assertEqual(row.computed_warranty_end_date, date(2026, 12, 13))
        self.assertEqual(row.computed_support_start_date, date(2026, 12, 1))
        self.assertEqual(row.computed_managed_support_end_date, date(2027, 2, 9))
        row.delivery_actual_finish_date = None
        self.assertIsNone(row.computed_post_svc_warranty_end_date)
        self.assertIsNone(row.computed_warranty_end_date)
        self.assertEqual(row.managed_support_start_date, date(2026, 12, 1))
        self.assertEqual(row.managed_support_end_date, date(2027, 2, 9))
        row.delivery_actual_finish_date = date(2024, 2, 10)
        self.assertEqual(row.computed_post_svc_warranty_end_date, date(2024, 3, 11))
        row.project_details = "Maintenance"
        self.assertIsNone(row.computed_post_svc_warranty_end_date)
        self.assertEqual(row.computed_warranty_end_date, date(2024, 3, 11))
        row.project_details = "Consultation"
        self.assertIsNone(row.computed_warranty_end_date)
        self.assertIsNone(row.computed_support_start_date)
        self.assertEqual(row.managed_support_end_date, date(2027, 2, 9))

    def test_manual_dates_do_not_reappear_after_clear(self):
        from hub.services.sqr_delivery_transition import plan_delivery_transition

        row = make(project_details="Implementation", managed_support_amount=Decimal("0"),
                   delivery_completion_signed_date=date(2026, 11, 1))
        planned = plan_delivery_transition(row)
        self.assertEqual(planned["updates"]["managed_support_start_date"], date(2026, 12, 1))
        self.assertEqual(planned["updates"]["managed_support_end_date"], date(2027, 12, 1))
        for field, value in planned["updates"].items():
            setattr(row, field, value)
        row.delivery_transition_metadata = planned["provenance"]
        row.managed_support_start_date = None
        row.managed_support_end_date = None
        self.assertIsNone(row.computed_support_start_date)
        self.assertIsNone(row.computed_managed_support_end_date)
        self.assertEqual(plan_delivery_transition(row)["updates"], {})

    def test_zero_support_amount_keeps_manual_end_visible_without_a_start(self):
        row = make(project_details="Deployment Only", managed_support_amount=Decimal("0"),
                   managed_support_end_date=date(2027, 2, 9))
        self.assertIsNone(row.computed_support_start_date)
        self.assertEqual(row.computed_managed_support_end_date, date(2027, 2, 9))
        row.managed_support_amount = None
        self.assertIsNone(row.computed_managed_support_end_date)
        self.assertEqual(row.managed_support_end_date, date(2027, 2, 9))


class SqrDeliveryTransitionMigrationTests(TransactionTestCase):
    def test_preview_and_migration_preserve_raw_conflicts_and_materialize_only_blanks(self):
        previous = [("hub", "0059_merge_20261106_0316")]
        schema = [("hub", "0060_sqr_recorded_performance")]
        current = [("hub", "0061_sqr_delivery_transition")]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        previous += [node for node in leaves if node[0] != "hub"]
        try:
            executor.migrate(previous)
            old_apps = executor.loader.project_state(previous).apps
            User = old_apps.get_model("accounts", "User")
            OldSqr = old_apps.get_model("hub", "SqrSubmission")
            engineer = User.objects.create(username="transition-engineer", role="engineer")
            pm = User.objects.create(username="transition-pm", role="pm_esg")
            common = {"year": 2026, "engineer_id": engineer.pk, "pm_esg_reviewer_id": pm.pk,
                      "customer_name": "Synthetic account", "project_title": "Transition fixture",
                      "project_details": "Deployment Only", "status": "reviewed",
                      "proposal_status": "closed_won", "overall_status": "in_progress"}
            old = OldSqr.objects.create(**common, reference_code="SQR-TRANSITION-1", sequence_number=1,
                                       delivery_health="off_track", managed_support_amount=Decimal("0"),
                                       delivery_completion_signed_date=date(2026, 11, 1),
                                       delivery_actual_finish_date=date(2026, 11, 13),
                                       delivery_target_finish_date=date(2026, 10, 1),
                                       warranty_end_date=date(2026, 12, 5),
                                       managed_support_end_date=date(2027, 2, 9))
            terminal_ids = []
            for number, health in ((2, "completed"), (3, "cancelled")):
                terminal_ids.append(OldSqr.objects.create(**common, sequence_number=number,
                    reference_code=f"SQR-TRANSITION-{number}", delivery_health=health).pk)
            history = old_apps.get_model("hub", "SqrSubmissionHistory").objects.create(
                submission_id=old.pk, actor_id=pm.pk, field="delivery_health",
                old_values={"delivery_health": "at_risk"}, new_values={"delivery_health": "off_track"})
            executor = MigrationExecutor(connection)
            executor.migrate(schema)
            row = SqrSubmission.objects.get(pk=old.pk)
            from hub.services.sqr_delivery_transition import plan_delivery_transition

            planned = plan_delivery_transition(row)
            self.assertEqual(planned["updates"]["managed_support_start_date"], date(2026, 12, 1))
            self.assertNotIn("managed_support_end_date", planned["updates"])
            self.assertIn("raw_end_differs_from_displayed", planned["provenance"]["warnings"])
            self.assertEqual(planned["provenance"]["previous_visible"], {
                "warranty_end": "2026-12-01", "maintenance_start": "2026-12-01",
                "maintenance_end": "2027-12-01"})
            with TemporaryDirectory() as directory:
                output = Path(directory) / "transition.json"
                before = row.updated_at
                call_command("preview_sqr_delivery_transition", output=str(output), verbosity=0)
                report = json.loads(output.read_text(encoding="utf-8"))
                preview = next(item for item in report["rows"] if item["pk"] == row.pk)
                self.assertEqual(preview["updates"]["managed_support_start_date"], "2026-12-01")
                self.assertEqual(preview["provenance"], planned["provenance"])
                row.refresh_from_db()
                self.assertIsNone(row.managed_support_start_date)
                self.assertEqual(row.delivery_transition_metadata, {})
                self.assertEqual(row.updated_at, before)
                self.assertEqual(SqrSubmissionHistory.objects.count(), 1)
            # A recorded value written after the schema step is not legacy input.
            SqrSubmission.objects.filter(pk=terminal_ids[0]).update(schedule_performance="on_track")
            MigrationExecutor(connection).migrate(current)
            row.refresh_from_db()
            self.assertEqual(row.schedule_performance, "delayed")
            self.assertEqual((row.budget_effort_performance, row.quality_performance), ("", ""))
            self.assertEqual(row.managed_support_start_date, date(2026, 12, 1))
            self.assertEqual(row.managed_support_end_date, date(2027, 2, 9))
            self.assertEqual(row.computed_post_svc_warranty_end_date, date(2026, 12, 13))
            self.assertEqual(row.delivery_transition_metadata, planned["provenance"])
            self.assertEqual((row.delivery_health, row.delivery_completion_signed_date,
                              row.delivery_target_finish_date, row.warranty_end_date),
                             ("off_track", date(2026, 11, 1), date(2026, 10, 1), date(2026, 12, 5)))
            completed, cancelled = [SqrSubmission.objects.get(pk=pk) for pk in terminal_ids]
            self.assertEqual((completed.delivery_health, completed.schedule_performance), ("completed", "on_track"))
            self.assertEqual((cancelled.delivery_health, cancelled.schedule_performance), ("cancelled", ""))
            self.assertTrue(cancelled.delivery_transition_metadata["warnings"])
            self.assertEqual(SqrSubmissionHistory.objects.count(), 1)
            self.assertEqual(SqrSubmissionHistory.objects.get(pk=history.pk).actor_id, pm.pk)
            row.managed_support_start_date = None
            row.managed_support_end_date = None
            row.save(update_fields=["managed_support_start_date", "managed_support_end_date"])
            self.assertEqual(plan_delivery_transition(row)["updates"], {})
            # Data rollback must not erase a human's post-transition assessment.
            MigrationExecutor(connection).migrate(schema)
            completed.refresh_from_db()
            self.assertEqual(completed.schedule_performance, "on_track")
        finally:
            MigrationExecutor(connection).migrate(leaves)
