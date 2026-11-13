import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import User
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


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SqrDeliveryProgressEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="delivery-admin", role="admin")
        cls.pm = User.objects.create_user(username="delivery-pm", role="pm_esg")
        cls.engineer = User.objects.create_user(username="delivery-engineer", role="engineer")
        common = {"engineer": cls.engineer, "pm_esg_reviewer": cls.pm,
                  "customer_name": "Synthetic account", "project_title": "Delivery test",
                  "project_details": "Deployment Only", "managed_support_amount": Decimal("0"),
                  "status": "reviewed", "proposal_status": "closed_won", "overall_status": "in_progress"}
        cls.row = SqrSubmission.objects.create(**common)
        cls.sibling = SqrSubmission.objects.create(**common)

    def post_field(self, field, value, user=None):
        self.client.force_login(user or self.pm)
        return self.client.post(reverse("hub:sqr-inline-update", args=[self.row.pk]),
                                {"field": field, "value": value}, content_type="application/json")

    def test_progress_assessments_save_with_actor_and_round_trip_through_undo(self):
        for actor in (self.admin, self.pm):
            for field, value in (("schedule_performance", "delayed"),
                                 ("budget_effort_performance", "over_budget"), ("quality_performance", "unmet")):
                with self.subTest(actor=actor.role, field=field):
                    response = self.post_field(field, value, actor)
                    self.assertEqual(response.status_code, 200)
                    self.row.refresh_from_db()
                    self.assertEqual(getattr(self.row, field), value)
                    self.sibling.refresh_from_db()
                    self.assertEqual(getattr(self.sibling, field), "")
                    history = self.row.history_entries.first()
                    self.assertEqual(history.actor_id, actor.pk)
                    undo = self.client.post(response.json()["undo_url"])
                    self.assertEqual(undo.status_code, 200)
                    self.row.refresh_from_db()
                    self.assertEqual(getattr(self.row, field), "")
        count = self.row.history_entries.count()
        self.assertEqual(self.post_field("schedule_performance", "not_recorded").status_code, 400)
        self.assertEqual(self.row.history_entries.count(), count)
        self.assertEqual(self.post_field("schedule_performance", "on_track", self.engineer).status_code, 403)
        for proposal in ("closed_lost", "closed_canceled"):
            SqrSubmission.objects.filter(pk=self.row.pk).update(proposal_status=proposal)
            self.assertEqual(self.post_field("quality_performance", "met", self.admin).status_code, 400)

    def test_manual_end_validation_is_shared_by_inline_and_full_form(self):
        from hub.forms import SqrDeliveryForm

        SqrSubmission.objects.filter(pk=self.row.pk).update(managed_support_start_date=date(2026, 12, 1))
        count = self.row.history_entries.count()
        response = self.post_field("managed_support_end_date", "2026-11-30")
        self.assertEqual(response.status_code, 400)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.managed_support_end_date)
        self.assertEqual(self.row.history_entries.count(), count)
        form = SqrDeliveryForm({"managed_support_start_date": "2026-12-01", "managed_support_end_date": "2026-11-30"}, instance=self.row)
        self.assertFalse(form.is_valid())
        self.assertIn("managed_support_end_date", form.errors)
        for actor in (self.admin, self.pm):
            self.assertEqual(self.post_field("managed_support_end_date", "2026-12-01", actor).status_code, 200)
            self.assertEqual(self.post_field("managed_support_start_date", "", actor).status_code, 200)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.managed_support_start_date)
        self.assertEqual(self.row.managed_support_end_date, date(2026, 12, 1))
        SqrSubmission.objects.filter(pk=self.row.pk).update(managed_support_amount=None)
        self.assertEqual(self.post_field("managed_support_end_date", "2026-12-02").status_code, 400)
        self.row.refresh_from_db()
        self.assertEqual(self.row.managed_support_end_date, date(2026, 12, 1))

    def test_finish_save_clear_and_restore_recompute_only_warranty(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(
            managed_support_start_date=date(2026, 12, 1), managed_support_end_date=date(2027, 2, 9))
        response = self.post_field("delivery_actual_finish_date", "2026-11-13")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["post_svc_warranty_end_date"], "Dec 13, 2026")
        clear = self.post_field("delivery_actual_finish_date", "")
        self.assertEqual(clear.status_code, 200)
        self.assertEqual(clear.json()["post_svc_warranty_end_date"], "")
        undone = self.client.post(clear.json()["undo_url"])
        self.assertEqual(undone.json()["post_svc_warranty_end_date"], "Dec 13, 2026")
        self.row.refresh_from_db()
        self.assertEqual((self.row.managed_support_start_date, self.row.managed_support_end_date),
                         (date(2026, 12, 1), date(2027, 2, 9)))

    def test_stale_form_definition_cannot_restore_retired_inputs(self):
        from hub.form_editor_runtime import configure_form
        from hub.forms import SqrDeliveryForm

        self.row.delivery_health = "off_track"
        schema = {"fields": [{"key": key, "label": key, "section": "After delivery"}
                             for key in ("delivery_target_finish_date", "delivery_completion_signed_date", "delivery_health", "warranty_end_date")]
                  + [{"key": "custom_delivery_note", "label": "Custom note", "type": "text",
                      "condition": {"field": "delivery_health", "operator": "equals", "value": "off_track"}}]}
        form = configure_form(SqrDeliveryForm(instance=self.row), schema=schema)
        self.assertIn("custom_delivery_note", form.fields)
        self.assertIn("custom_delivery_note", form.editor_visible_custom_keys)
        posted = configure_form(SqrDeliveryForm({"custom_delivery_note": "Retained note"}, instance=self.row), schema=schema)
        self.assertIn("custom_delivery_note", posted.editor_visible_custom_keys)
        for key in ("delivery_target_finish_date", "delivery_completion_signed_date", "delivery_health", "warranty_end_date"):
            self.assertNotIn(key, form.fields)
        self.assertIn("schedule_performance", form.fields)
        from hub.services.sqr_prototype import serialize_record
        from hub.views import SqrInlineFieldUpdateView as Inline
        record = serialize_record(self.row, self.admin, allowed_fields=Inline._ADMIN_ALLOWED,
                                  locked_fields=Inline._CLOSED_LOST_LOCKED_FIELDS, today=date(2026, 11, 13),
                                  is_pm=True, is_admin=True, is_engineer=False)
        self.assertEqual(record["values"]["managed_support_end_date"], "")
        self.assertIn("managed_support_end_date", record["editable"])

    def test_legacy_import_alias_does_not_override_explicit_new_schedule_value(self):
        from hub.views import SqrImportView

        importer = SqrImportView()
        kwargs = {"request_map": {}, "pm_lookup": {"pm": [self.pm]},
                  "engineer_lookup": {"engineer": [self.engineer]}, "actor": self.admin}
        for additional, expected in (({}, "delayed"), ({"Schedule Performance": ""}, ""),
                                     ({"Schedule Performance": "On Track"}, "on_track")):
            prepared = importer._prepare_row_payload(row_data={"Health Status": "Off Track", **additional}, **kwargs)
            self.assertEqual(prepared["data"]["schedule_performance"], expected)

    def test_undo_and_history_restore_reject_an_invalid_merged_date_pair(self):
        for mode in ("undo", "history"):
            with self.subTest(mode=mode):
                SqrSubmission.objects.filter(pk=self.row.pk).update(
                    managed_support_start_date=date(2026, 12, 1), managed_support_end_date=date(2026, 12, 10))
                clear = self.post_field("managed_support_start_date", "")
                self.assertEqual(clear.status_code, 200)
                history = self.row.history_entries.filter(field="managed_support_start_date").first()
                self.assertEqual(self.post_field("managed_support_end_date", "2026-11-20").status_code, 200)
                count = self.row.history_entries.count()
                url = clear.json()["undo_url"] if mode == "undo" else reverse("hub:sqr-history-restore", args=[self.row.pk, history.pk])
                response = self.client.post(url)
                self.assertEqual(response.status_code, 400)
                self.row.refresh_from_db()
                self.assertIsNone(self.row.managed_support_start_date)
                self.assertEqual(self.row.managed_support_end_date, date(2026, 11, 20))
                self.assertEqual(self.row.history_entries.count(), count)

    def test_inline_date_validation_checks_the_latest_other_date(self):
        from unittest.mock import patch
        from hub.views import SqrInlineFieldUpdateView as Inline

        SqrSubmission.objects.filter(pk=self.row.pk).update(managed_support_start_date=date(2026, 12, 1))
        snapshot = Inline._snapshot_values
        interleaved = False
        def interleave(submission, fields):
            nonlocal interleaved
            if not interleaved:
                interleaved = True
                SqrSubmission.objects.filter(pk=self.row.pk).update(managed_support_start_date=date(2027, 1, 1))
            return snapshot(submission, fields)
        with patch.object(Inline, "_snapshot_values", side_effect=interleave):
            response = self.post_field("managed_support_end_date", "2026-12-10")
        self.assertEqual(response.status_code, 400)
        self.row.refresh_from_db()
        self.assertEqual(self.row.managed_support_start_date, date(2027, 1, 1))
        self.assertIsNone(self.row.managed_support_end_date)
        self.assertFalse(self.row.history_entries.exists())
