import json
from datetime import date, timedelta
from io import BytesIO

from openpyxl import Workbook
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from hub.forms import SqrDeliveryForm, SqrTrackerEditForm
from hub.models import SqrSubmission, SqrSubmissionChange, SqrSubmissionHistory
from hub.form_editor_runtime import configure_form
from hub.views import SqrImportView, _get_sqr_export_columns
from hub.services import sqr_prototype


class DeliveryDateContractTests(SimpleTestCase):
    def test_warranty_finish_only_preserves_each_scope_allowlist(self):
        finish = date(2028, 2, 1)
        for scope in SqrSubmission._WARRANTY_SCOPES | {"Project Management"}:
            with self.subTest(scope=scope):
                row = SqrSubmission(project_details=scope, delivery_actual_finish_date=finish,
                                    delivery_completion_signed_date=date(2028, 1, 1))
                self.assertEqual(row.computed_post_svc_warranty_end_date,
                                 finish + timedelta(days=30) if scope in row._IMPL_SCOPES else None)
                self.assertEqual(row.computed_warranty_end_date,
                                 finish + timedelta(days=30) if scope in row._WARRANTY_SCOPES else None)
                row.delivery_actual_finish_date = None
                row.legacy_delivery_completed = True
                self.assertIsNone(row.computed_warranty_end_date)
                self.assertIsNone(row.computed_post_svc_warranty_end_date)

    def test_manual_only_maintenance_respects_zero_amount_and_scope(self):
        start, end = date(2028, 2, 1), date(2028, 2, 8)
        row = SqrSubmission(project_details="Support", managed_support_amount=Decimal("0"),
                            delivery_actual_finish_date=start)
        self.assertIsNone(row.computed_support_start_date)
        self.assertIsNone(row.computed_managed_support_end_date)
        row.managed_support_start_date, row.managed_support_end_date = start, end
        self.assertEqual((row.computed_support_start_date, row.computed_managed_support_end_date), (start, end))
        row.delivery_actual_finish_date = end
        self.assertEqual((row.computed_support_start_date, row.computed_managed_support_end_date), (start, end))
        row.project_details = "Project Management"
        self.assertIsNone(row.computed_support_start_date)
        self.assertIsNone(row.computed_managed_support_end_date)
        self.assertEqual((row.managed_support_start_date, row.managed_support_end_date), (start, end))

    def test_manual_maintenance_pair_validation(self):
        from hub.services.sqr_maintenance import validate_manual_maintenance_dates
        day = date(2028, 2, 1)
        for start, end in ((None, None), (day, None), (day, day), (day, day + timedelta(days=1))):
            validate_manual_maintenance_dates(start, end)
        for start, end, field in ((None, day, "managed_support_start_date"),
                                  (day, day - timedelta(days=1), "managed_support_end_date")):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValidationError) as caught:
                    validate_manual_maintenance_dates(start, end)
                self.assertIn(field, caught.exception.message_dict)

    def test_all_active_editors_share_assessment_contract(self):
        assessments = {"schedule_performance", "budget_effort_performance", "quality_performance"}
        retired = {"delivery_health", "delivery_target_finish_date", "delivery_completion_signed_date",
                   "warranty_end_date", "support_start_date"}
        for form_class in (SqrDeliveryForm, SqrTrackerEditForm):
            with self.subTest(editor=form_class.__name__):
                self.assertLessEqual(assessments | {"managed_support_start_date", "managed_support_end_date"},
                                     set(form_class.base_fields))
                self.assertFalse(retired & set(form_class.base_fields))
        specs = {field["key"]: field for field in sqr_prototype.FIELD_SPECS}
        self.assertFalse(retired & specs.keys())
        self.assertEqual([f["key"] for f in specs.values() if f["section"] == "Schedule"],
                         ["delivery_start_date", "delivery_actual_finish_date"])
        self.assertEqual(specs["managed_support_end_date"]["type"], "date")
        self.assertEqual(specs["post_svc_warranty_end_date"]["section"], "MAINTENANCE")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DeliveryEditorContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pm = User.objects.create_user(username="delivery-contract-pm", role=User.Roles.PM_ESG)
        cls.engineer = User.objects.create_user(username="delivery-contract-eng", role=User.Roles.ENGINEER)
        cls.row = SqrSubmission.objects.create(engineer=cls.engineer, pm_esg_reviewer=cls.pm,
            customer_name="Contract Customer", project_title="Contract Project", project_details="Implementation",
            proposal_status="closed_won", managed_support_amount=Decimal("0"))

    def setUp(self):
        self.client.force_login(self.pm)

    def update(self, field, value):
        return self.client.post(reverse("hub:sqr-inline-update", args=[self.row.pk]),
                                json.dumps({"field": field, "value": value}), content_type="application/json")

    def test_export_import_roundtrip_preserves_new_and_legacy_fields(self):
        self.row.schedule_performance = "delayed"
        self.row.budget_effort_performance = "within_budget"
        self.row.quality_performance = "at_risk"
        self.row.delivery_health = "off_track"
        self.row.delivery_target_finish_date = date(2028, 1, 1)
        self.row.delivery_completion_signed_date = date(2028, 1, 4)
        self.row.warranty_end_date = date(2028, 1, 8)
        self.row.delivery_actual_finish_date = date(2028, 1, 5)
        self.row.managed_support_start_date = date(2028, 2, 1)
        self.row.managed_support_end_date = date(2028, 2, 2)
        self.row.project_details = "Project Management"
        row_data = {label: getter(self.row) for label, getter in _get_sqr_export_columns()}
        self.assertEqual(row_data["Maintenance End Date"], "2028-02-02")
        self.assertIn("Legacy Health Status", row_data)
        importer = SqrImportView()
        payload = importer._prepare_row_payload(row_data=row_data, request_map={},
            pm_lookup=importer._build_user_lookup([self.pm]),
            engineer_lookup=importer._build_user_lookup([self.engineer]), actor=self.pm)["data"]
        for field in ("schedule_performance", "budget_effort_performance", "quality_performance",
                      "delivery_health", "delivery_target_finish_date", "delivery_completion_signed_date",
                      "warranty_end_date", "delivery_actual_finish_date", "managed_support_start_date",
                      "managed_support_end_date"):
            self.assertEqual(payload[field], getattr(self.row, field), field)
        self.assertNotIn("legacy_delivery_completed", payload)

    def test_headerless_legacy_spreadsheet_keeps_original_column_positions(self):
        workbook = Workbook()
        row = [None] * 45
        row[1] = "SQR-2028-0001"
        row[4] = "Implementation"
        row[27] = "2028-01-01"
        row[28] = "2028-01-03"
        row[30] = "off_track"
        row[33] = "2028-01-05"
        row[34] = "2028-01-06"
        row[36] = "2028-02-01"
        row[37] = "2028-02-05"
        row[44] = "Preserved billing note"
        workbook.active.append(row)
        stream = BytesIO()
        workbook.save(stream)
        payload = SqrImportView()._parse_import_file(file_bytes=stream.getvalue(), actor=self.pm,
                                                   user_id=self.pm.pk, job_id="legacy-layout")[0]["data"]
        self.assertEqual(payload["delivery_target_finish_date"], date(2028, 1, 3))
        self.assertEqual(payload["delivery_actual_finish_date"], date(2028, 1, 5))
        self.assertEqual(payload["delivery_health"], "off_track")
        self.assertEqual(payload.get("managed_support_end_date"), date(2028, 2, 5))
        self.assertEqual(payload["revenue_overview"], "Preserved billing note")

    def test_import_rejects_invalid_canonical_assessments_and_dates(self):
        importer = SqrImportView()
        base = {"Scope of Services": "Implementation", "Health Status": "Off Track"}
        for extra in ({"Schedule Performance": "Off Track"}, {"Quality Performance": "bad"},
                      {"Maintenance End Date": "2028-02-02"},
                      {"Maintenance Start Date": "not-a-date"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                importer._prepare_row_payload(row_data={**base, **extra}, request_map={},
                    pm_lookup={}, engineer_lookup={}, actor=self.pm)
        legacy = importer._prepare_row_payload(row_data=base, request_map={},
                    pm_lookup={}, engineer_lookup={}, actor=self.pm)["data"]
        self.assertEqual(legacy.get("schedule_performance"), "")
        self.assertEqual(legacy["delivery_health"], "off_track")

    def test_restore_validates_saved_counterpart_without_mutation(self):
        for permanent in (False, True):
            with self.subTest(permanent=permanent):
                SqrSubmission.objects.filter(pk=self.row.pk).update(managed_support_start_date=None,
                                                                   managed_support_end_date=None)
                self.assertEqual(self.update("managed_support_start_date", "2028-02-01").status_code, 200)
                change = SqrSubmissionChange.objects.filter(submission=self.row).latest("pk")
                history = SqrSubmissionHistory.objects.filter(submission=self.row).latest("pk")
                self.assertEqual(self.update("managed_support_end_date", "2028-02-05").status_code, 200)
                count = self.row.history_entries.count()
                route = "hub:sqr-history-restore" if permanent else "hub:sqr-inline-undo"
                item = history if permanent else change
                response = self.client.post(reverse(route, args=[self.row.pk, item.pk]))
                self.assertEqual(response.status_code, 400, response.content)
                self.row.refresh_from_db()
                item.refresh_from_db()
                self.assertEqual(self.row.managed_support_start_date, date(2028, 2, 1))
                self.assertFalse(item.is_restored if permanent else item.is_undone)
                self.assertEqual(self.row.history_entries.count(), count)

    def test_permanent_restore_obeys_terminal_delivery_lock(self):
        self.assertEqual(self.update("quality_performance", "met").status_code, 200)
        history = SqrSubmissionHistory.objects.filter(submission=self.row).latest("pk")
        SqrSubmission.objects.filter(pk=self.row.pk).update(proposal_status="closed_canceled")
        response = self.client.post(reverse("hub:sqr-history-restore", args=[self.row.pk, history.pk]))
        self.assertEqual(response.status_code, 400)
        self.row.refresh_from_db()
        self.assertEqual(self.row.quality_performance, "met")
        history.refresh_from_db()
        self.assertFalse(history.is_restored)

    def test_invalid_or_unauthorized_updates_create_no_history(self):
        before = self.row.history_entries.count()
        self.assertEqual(self.update("managed_support_start_date", "invalid").status_code, 400)
        self.client.force_login(self.engineer)
        self.assertEqual(self.update("quality_performance", "met").status_code, 403)
        self.assertEqual(self.row.history_entries.count(), before)
        self.assertFalse(SqrSubmissionChange.objects.filter(submission=self.row).exists())

    def test_forms_merge_omitted_dates_and_preserve_canonical_configured_fields(self):
        self.row.managed_support_start_date = date(2028, 2, 1)
        self.row.managed_support_end_date = date(2028, 2, 5)
        for form_class in (SqrDeliveryForm, SqrTrackerEditForm):
            with self.subTest(form=form_class.__name__):
                form = form_class({"managed_support_start_date": "2028-02-02"}, instance=self.row)
                form.is_valid()
                self.assertNotIn("managed_support_end_date", form.errors)
                self.assertEqual(form.cleaned_data.get("managed_support_end_date"), date(2028, 2, 5))
                schema = {"fields": [{"key": "delivery_health", "label": "Old Health"},
                    {"key": "schedule_performance", "label": "Old Health", "section": "Progress"}]}
                configured = configure_form(form_class(instance=self.row, _form_editor_skip=True),
                                            schema=schema)
                self.assertNotIn("delivery_health", configured.fields)
                self.assertEqual(configured.fields["schedule_performance"].label, "Schedule Performance")
                self.assertIn("quality_performance", configured.fields)

    def test_delivery_form_save_records_atomic_history_and_can_undo(self):
        response = self.client.post(reverse("hub:sqr-delivery-update", args=[self.row.pk]), {
            "schedule_performance": "delayed", "quality_performance": "met",
            "managed_support_start_date": "2028-02-01", "managed_support_end_date": "2028-02-05",
        })
        self.assertEqual(response.status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.schedule_performance, "delayed")
        change = SqrSubmissionChange.objects.filter(submission=self.row).first()
        self.assertIsNotNone(change)
        history = self.row.history_entries.get()
        self.assertEqual(history.old_values["schedule_performance"], "")
        self.assertEqual(history.new_values["schedule_performance"], "delayed")
        self.assertEqual(history.new_values["managed_support_end_date"], "2028-02-05")
        no_op = self.client.post(reverse("hub:sqr-delivery-update", args=[self.row.pk]), {
            "schedule_performance": "delayed", "quality_performance": "met",
            "managed_support_start_date": "2028-02-01", "managed_support_end_date": "2028-02-05",
        })
        self.assertEqual(no_op.status_code, 302)
        self.assertEqual(self.row.history_entries.count(), 1)
        self.assertEqual(SqrSubmissionChange.objects.filter(submission=self.row).count(), 1)
        undo = self.client.post(reverse("hub:sqr-inline-undo", args=[self.row.pk, change.pk]))
        self.assertEqual(undo.status_code, 200, undo.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.schedule_performance, "")
        self.assertIsNone(self.row.managed_support_start_date)
        self.assertIsNone(self.row.managed_support_end_date)

    def test_classic_editor_and_read_only_rows_use_canonical_delivery(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(
            schedule_performance="delayed", budget_effort_performance="at_risk", quality_performance="met",
            delivery_actual_finish_date=date(2028, 2, 1), managed_support_amount=Decimal("0"),
            managed_support_start_date=date(2028, 2, 4), managed_support_end_date=date(2028, 2, 8),
        )
        for user, editable in ((self.pm, True), (self.engineer, False)):
            with self.subTest(editable=editable):
                self.client.force_login(user)
                response = self.client.get(reverse("hub:sqr") + "?view=tracker")
                self.assertEqual(response.status_code, 200)
                body = response.content.decode("utf-8")
                table = body.split('<table class="sqr-tracker table-hover">', 1)[-1].split('</table>', 1)[0]
                self.assertNotIn('data-sqr-field="delivery_target_finish_date"', table)
                self.assertNotIn('data-sqr-field="delivery_completion_signed_date"', table)
                self.assertNotIn('data-sqr-field="delivery_health"', table)
                for field, label in (("schedule_performance", "Delayed"),
                                     ("budget_effort_performance", "At Risk"), ("quality_performance", "Met")):
                    self.assertIn(label, table)
                    if editable:
                        self.assertIn('data-sqr-field="' + field + '"', table)
                self.assertIn("Mar 02, 2028", table)
                self.assertIn("Feb 08, 2028", table)
                if editable:
                    self.assertIn('data-sqr-field="managed_support_end_date"', table)
                    self.assertIn('data-sqr-value="2028-02-08"', table)

    def test_computed_refresh_returns_raw_manual_dates_separately(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(
            managed_support_start_date=date(2028, 2, 4), managed_support_end_date=date(2028, 2, 8),
        )
        response = self.update("delivery_actual_finish_date", "2028-02-01")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("maintenance_start_value"), "2028-02-04")
        self.assertEqual(response.json().get("maintenance_end_value"), "2028-02-08")

    def test_delivery_form_endpoint_respects_terminal_lock(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(proposal_status="closed_lost")
        response = self.client.post(reverse("hub:sqr-delivery-update", args=[self.row.pk]),
                                    {"quality_performance": "met"})
        self.assertEqual(response.status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.quality_performance, "")

    def test_partial_manual_dates_merge_and_reject_invalid_pairs(self):
        self.assertEqual(self.update("managed_support_end_date", "2028-02-05").status_code, 400)
        self.assertEqual(self.update("managed_support_start_date", "2028-02-01").status_code, 200)
        self.assertEqual(self.update("managed_support_end_date", "2028-02-05").status_code, 200)
        self.assertEqual(self.update("managed_support_start_date", "2028-02-06").status_code, 400)
        self.assertEqual(self.update("managed_support_start_date", "").status_code, 400)
        self.row.refresh_from_db()
        self.assertEqual((self.row.managed_support_start_date, self.row.managed_support_end_date),
                         (date(2028, 2, 1), date(2028, 2, 5)))

    def test_finish_change_never_overwrites_manual_dates(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(managed_support_start_date=date(2028, 2, 1),
                                                           managed_support_end_date=date(2028, 2, 5))
        response = self.update("delivery_actual_finish_date", "2028-02-10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["post_svc_warranty_end_date"], "Mar 11, 2028")
        self.row.refresh_from_db()
        self.assertEqual((self.row.managed_support_start_date, self.row.managed_support_end_date),
                         (date(2028, 2, 1), date(2028, 2, 5)))

    def test_unchanged_invalid_legacy_pair_does_not_block_unrelated_edit(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(managed_support_start_date=date(2028, 2, 5),
                                                           managed_support_end_date=date(2028, 2, 1))
        self.assertEqual(self.update("key_updates_risks", "Manual dates need review").status_code, 200)
        self.row.refresh_from_db()
        form = SqrDeliveryForm({"managed_support_start_date": "2028-02-05", "managed_support_end_date": "2028-02-01",
                                "key_updates_risks": "Retained dates"}, instance=self.row)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.row.refresh_from_db()
        self.assertEqual(self.row.managed_support_end_date, date(2028, 2, 1))

    def test_saved_invalid_maintenance_has_visible_review_notice(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(
            managed_support_start_date=date(2028, 2, 5), managed_support_end_date=date(2028, 2, 1))
        self.row.refresh_from_db()
        warning = "Maintenance End Date cannot be before Start Date."
        for form_class in (SqrDeliveryForm, SqrTrackerEditForm):
            form = form_class(instance=self.row)
            configure_form(form, schema={"fields": [
                {"key": "managed_support_end_date", "type": "date", "help_text": "Configured hint"}
            ]})
            self.assertIn(warning, form.as_p())
        for user in (self.pm, self.engineer):
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse("hub:sqr")), warning)
        flags = sqr_prototype.derive_attention(self.row, "delivery", date(2028, 1, 1))
        self.assertTrue(any(warning in flag["label"] for flag in flags), flags)
        self.row.status = SqrSubmission.Status.ON_HOLD
        flags = sqr_prototype.derive_attention(self.row, "delivery", date(2028, 1, 1))
        self.assertTrue(any(warning in flag["label"] for flag in flags), flags)
        self.client.force_login(self.pm)
        response = self.update("key_updates_risks", "Preserve history")
        self.assertEqual(response.status_code, 200)
        self.assertIn(warning, response.json().get("maintenance_notice", ""))
        corrected = self.update("managed_support_end_date", "2028-02-06")
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(corrected.json().get("maintenance_notice"), "")

    def test_ineligible_scope_preserves_stored_dates_with_inactive_notice(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(
            project_details="Project Management", managed_support_start_date=date(2028, 2, 5),
            managed_support_end_date=date(2028, 2, 6))
        self.row.refresh_from_db()
        warning = "Stored Maintenance dates are inactive"
        for user in (self.pm, self.engineer):
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse("hub:sqr")), warning)
        values = sqr_prototype._values(self.row)
        self.assertEqual(values["managed_support_start_date"], "2028-02-05")
        self.assertEqual(values["managed_support_end_date"], "2028-02-06")
        record = sqr_prototype.serialize_record(
            self.row, self.pm, editable_fields=(), can_review=True, quote_locked=False,
            status_choices=[], today=date(2028, 1, 1), is_pm=True, is_admin=False, is_engineer=False,
        )
        self.assertEqual(record["managed_support_start_date"], "")
        self.assertEqual(record["managed_support_end_date"], "")
        form = SqrDeliveryForm(instance=self.row)
        self.assertIn(warning, form.as_p())
        self.client.force_login(self.pm)
        response = self.update("project_details", "Implementation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("maintenance_notice"), "")
        self.row.refresh_from_db()
        self.assertEqual((self.row.managed_support_start_date, self.row.managed_support_end_date),
                         (date(2028, 2, 5), date(2028, 2, 6)))
        self.assertEqual((self.row.computed_support_start_date, self.row.computed_managed_support_end_date),
                         (date(2028, 2, 5), date(2028, 2, 6)))
        change = SqrSubmissionChange.objects.filter(submission=self.row, field="project_details").latest("pk")
        undo = self.client.post(reverse("hub:sqr-inline-undo", args=[self.row.pk, change.pk]))
        self.assertEqual(undo.status_code, 200)
        self.assertIn(warning, undo.json().get("maintenance_notice", ""))
        self.assertEqual(undo.json()["support_start_date"], "")
        self.assertEqual(undo.json()["maintenance_start_value"], "2028-02-05")

    def test_assessment_and_manual_date_history_undo(self):
        for field, value in (("schedule_performance", "delayed"), ("budget_effort_performance", "over_budget"),
                             ("quality_performance", "unmet"), ("managed_support_start_date", "2028-02-01")):
            with self.subTest(field=field):
                response = self.update(field, value)
                self.assertEqual(response.status_code, 200, response.content)
                change = SqrSubmissionChange.objects.filter(submission=self.row, field=field).latest("pk")
                undo = self.client.post(reverse("hub:sqr-inline-undo", args=[self.row.pk, change.pk]))
                self.assertEqual(undo.status_code, 200, undo.content)
                self.row.refresh_from_db()
                self.assertIn(getattr(self.row, field), ("", None))

    def test_assessments_reject_invalid_choices_and_closed_lost_edits(self):
        for field in ("schedule_performance", "budget_effort_performance", "quality_performance"):
            with self.subTest(field=field):
                self.assertEqual(self.update(field, "made_up").status_code, 400)
                form = SqrDeliveryForm({field: "made_up"}, instance=self.row)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
        SqrSubmission.objects.filter(pk=self.row.pk).update(proposal_status="closed_lost")
        self.assertEqual(self.update("quality_performance", "met").status_code, 400)
        self.assertEqual(self.update("managed_support_end_date", "2028-02-01").status_code, 400)
