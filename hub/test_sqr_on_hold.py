"""Regression coverage for independent SQR remarks and the manual hold workflow."""

import io
import json
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from importlib.util import find_spec
from unittest.mock import MagicMock, patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import User
from hub import form_editor
from hub.form_editor_runtime import TOKEN_FIELD
from hub.models import (
    Notification,
    SqrSubmission,
    SqrSubmissionChange,
    SqrSubmissionHistory,
)
from hub.test_multiple_sqrs import MultipleSqrFixtures
from hub.views import SqrImportView


class TrackerRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = {"attrs": dict(attrs), "cells": []}
        elif tag in ("th", "td") and self.row is not None:
            self.cell = {"tag": tag, "attrs": dict(attrs), "text": ""}
            self.row["cells"].append(self.cell)

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("th", "td"):
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


@override_settings(ACS_EMAIL_CONNECTION_STRING="", ACS_EMAIL_SENDER="")
class SqrOnHoldTests(MultipleSqrFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.paused_engineer = User.objects.create_user(
            username="hold_sqr_engineer", role=User.Roles.ON_HOLD,
        )
        self.submission = self.create_sqr()
        self.submission.remarks = "SSE filing remarks <independent>"
        self.submission.review_notes = "Existing reviewer notes"
        self.submission.save()
        self.client.force_login(self.pm)

    def update_status(self, value, *, submission=None, **extra):
        return self.client.post(
            reverse("hub:sqr-inline-update", args=[(submission or self.submission).pk]),
            data=json.dumps({"field": "status", "value": value, **extra}),
            content_type="application/json",
        )

    def snapshot(self):
        return SqrSubmission.objects.filter(pk=self.submission.pk).values().get()

    def assert_rejected_without_mutation(self, response, before, counts, status=400):
        self.assertEqual(response.status_code, status)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(
            (SqrSubmissionChange.objects.count(), SqrSubmissionHistory.objects.count(), Notification.objects.count()),
            counts,
        )

    def mutation_counts(self):
        return (
            SqrSubmissionChange.objects.count(), SqrSubmissionHistory.objects.count(), Notification.objects.count(),
        )

    def test_model_has_distinct_optional_remarks_and_hold_reason(self):
        self.assertEqual(SqrSubmission.Status.ON_HOLD, "on_hold")
        self.assertEqual(SqrSubmission.Status.ON_HOLD.label, "On Hold")
        self.assertTrue(SqrSubmission._meta.get_field("remarks").blank)
        self.assertTrue(SqrSubmission._meta.get_field("hold_reason").blank)
        self.assertEqual(SqrSubmission._meta.get_field("hold_reason").get_internal_type(), "TextField")
        self.assertEqual(self.submission.hold_reason, "")

    def test_approval_service_preserves_metadata_when_pausing_an_approved_quote(self) -> None:
        """Pausing approval must retain the original validity and assigned PM."""
        self.assertIsNotNone(find_spec('hub.services.sqr_workflow'))
        from hub.services.sqr_workflow import save_inline_change

        _, field, old_status, _, change = save_inline_change(
            self.submission.pk, self.pm, 'status', SqrSubmission.Status.APPROVED,
        )
        self.assertEqual((field, old_status), ('status', SqrSubmission.Status.FOR_PROCESSING))
        self.assertIsNotNone(change)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.assigned_pm, self.pm)
        self.assertIsNotNone(self.submission.reviewed_at)
        self.assertIsNotNone(self.submission.validity_due_date)
        approved_dates = self.submission.reviewed_at, self.submission.validity_due_date

        save_inline_change(
            self.submission.pk, self.pm, 'status', SqrSubmission.Status.ON_HOLD,
            hold_reason='Waiting for the customer',
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SqrSubmission.Status.ON_HOLD)
        self.assertEqual(self.submission.hold_reason, 'Waiting for the customer')
        self.assertEqual((self.submission.reviewed_at, self.submission.validity_due_date), approved_dates)

    def test_inline_edit_keeps_legacy_submission_lookup(self) -> None:
        """Apply status validation to the submission resolved by the legacy lookup."""
        with patch("hub.views.get_object_or_404", return_value=self.submission):
            response = self.client.post(
                reverse("hub:sqr-inline-update", args=[self.submission.pk + 10000]),
                data=json.dumps({"field": "status", "value": "unknown"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid SQR status")
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SqrSubmission.Status.FOR_PROCESSING)

    def test_pm_and_admin_can_hold(self):
        for user in (self.pm, self.admin):
            with self.subTest(role=user.role):
                sqr = self.create_sqr()
                self.client.force_login(user)
                response = self.update_status("on_hold", submission=sqr, hold_reason="Awaiting customer scope")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertTrue(response.json()["ok"])
                self.assertEqual(response.json()["hold_reason"], "Awaiting customer scope")
                sqr.refresh_from_db()
                self.assertEqual(sqr.status, "on_hold")
                self.assertEqual(sqr.hold_reason, "Awaiting customer scope")

    def test_other_roles_cannot_hold_or_resume(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(
            status="on_hold", hold_reason="Existing hold",
        )
        for user in (self.engineer, self.other, self.paused_engineer, self.requestor):
            for target in ("on_hold", "submitted", "for_revision", "reviewed"):
                with self.subTest(role=user.role, target=target):
                    self.client.force_login(user)
                    before, counts = self.snapshot(), self.mutation_counts()
                    response = self.update_status(target, hold_reason="Unauthorized replacement")
                    self.assert_rejected_without_mutation(response, before, counts, status=403)

    def test_hold_requires_nonwhitespace_string_and_failure_is_atomic(self):
        invalid_payloads = [{}, *({"hold_reason": value} for value in ("", " \t\r\n ", None, 0, False, [], {}))]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                before, counts = self.snapshot(), self.mutation_counts()
                response = self.update_status("on_hold", **payload)
                self.assert_rejected_without_mutation(response, before, counts)

    def test_old_reason_cannot_substitute_for_reason_on_next_hold(self):
        self.assertEqual(self.update_status("on_hold", hold_reason="Initial delay").status_code, 200)
        self.assertEqual(self.update_status("submitted").status_code, 200)
        before, counts = self.snapshot(), self.mutation_counts()
        self.assert_rejected_without_mutation(self.update_status("on_hold"), before, counts)
        response = self.update_status("on_hold", hold_reason="New delay")
        self.assertEqual(response.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.hold_reason, "New delay")
        self.assertEqual(self.submission.remarks, "SSE filing remarks <independent>")

    def test_unknown_empty_and_nonstring_statuses_are_rejected_atomically(self):
        for status in ("", " ", "unknown", "approved", None, 0, [], {}):
            with self.subTest(status=status):
                before, counts = self.snapshot(), self.mutation_counts()
                response = self.update_status(status, hold_reason="Must not be saved")
                self.assert_rejected_without_mutation(response, before, counts)

    @patch("hub.views._send_sqr_approved_email")
    @patch("hub.views._send_sqr_for_revision_email")
    def test_pause_preserves_approval_metadata_and_has_no_notifications(self, revision_email, approval_email):
        reviewed_at = timezone.now() - timedelta(days=7)
        due_date = timezone.localdate() + timedelta(days=23)
        SqrSubmission.objects.filter(pk=self.submission.pk).update(
            status="reviewed", reviewed_at=reviewed_at, reviewed_by=self.pm,
            validity_due_date=due_date, assigned_pm=self.pm,
        )
        before = self.snapshot()
        notifications = Notification.objects.count()
        response = self.update_status("on_hold", hold_reason="Budget confirmation pending")
        self.assertEqual(response.status_code, 200, response.content)
        after = self.snapshot()
        for field in ("reviewed_at", "reviewed_by_id", "validity_due_date", "assigned_pm_id", "remarks", "review_notes"):
            self.assertEqual(after[field], before[field], field)
        revision_email.assert_not_called()
        approval_email.assert_not_called()
        self.assertEqual(Notification.objects.count(), notifications)
        self.assertFalse(response.json().get("approval_email_draft_url"))
        self.assertFalse(response.json().get("approval_email_eml_url"))

    def test_unapproved_pause_does_not_create_approval_or_revision_metadata(self):
        before = self.snapshot()
        response = self.update_status("on_hold", hold_reason="Awaiting scope")
        self.assertEqual(response.status_code, 200)
        after = self.snapshot()
        for field in ("reviewed_at", "reviewed_by_id", "validity_due_date", "assigned_pm_id", "review_notes"):
            self.assertEqual(after[field], before[field], field)

    @patch("hub.views._send_sqr_for_revision_email")
    def test_manual_resume_choices_preserve_hold_reason_and_remarks(self, revision_email):
        for status in ("submitted", "for_revision", "reviewed"):
            with self.subTest(status=status):
                sqr = self.create_sqr()
                sqr.remarks = "Independent filing note"
                sqr.save()
                self.assertEqual(self.update_status("on_hold", submission=sqr, hold_reason="Customer delay").status_code, 200)
                response = self.update_status(status, submission=sqr)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["hold_reason"], "Customer delay")
                sqr.refresh_from_db()
                self.assertEqual(sqr.status, status)
                self.assertEqual(sqr.hold_reason, "Customer delay")
                self.assertEqual(sqr.remarks, "Independent filing note")
                if status == "reviewed":
                    self.assertIsNotNone(sqr.reviewed_at)
                    self.assertIsNotNone(sqr.validity_due_date)

    def test_hold_change_and_history_capture_reason_and_undo_restores_it(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(hold_reason="Earlier retained reason")
        before = self.snapshot()
        response = self.update_status("on_hold", hold_reason="New hold reason")
        self.assertEqual(response.status_code, 200)
        change = SqrSubmissionChange.objects.get(pk=response.json()["change_id"])
        history = SqrSubmissionHistory.objects.get(submission=self.submission, action=SqrSubmissionHistory.Action.UPDATED)
        for entry in (change, history):
            self.assertEqual(entry.old_values["hold_reason"], "Earlier retained reason")
            self.assertEqual(entry.new_values["hold_reason"], "New hold reason")
            self.assertEqual(entry.old_values["status"], "submitted")
            self.assertEqual(entry.new_values["status"], "on_hold")
        undo = self.client.post(response.json()["undo_url"])
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertTrue(undo.json()["undone"])
        self.assertEqual(undo.json()["hold_reason"], "Earlier retained reason")
        after = self.snapshot()
        for field in ("status", "hold_reason", "remarks", "reviewed_at", "validity_due_date", "review_notes"):
            self.assertEqual(after[field], before[field], field)

    def test_history_restore_supports_hold_reason(self):
        self.assertEqual(self.update_status("on_hold", hold_reason="Pending decision").status_code, 200)
        history = SqrSubmissionHistory.objects.get(submission=self.submission, action=SqrSubmissionHistory.Action.UPDATED)
        response = self.client.post(reverse("hub:sqr-history-restore", args=[self.submission.pk, history.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["ok"])
        self.submission.refresh_from_db()
        history.refresh_from_db()
        self.assertEqual(self.submission.status, "submitted")
        self.assertEqual(self.submission.hold_reason, "")
        self.assertEqual(self.submission.remarks, "SSE filing remarks <independent>")
        self.assertIsNotNone(history.restored_at)

    def test_stale_hold_reason_blocks_undo_and_history_restore(self):
        response = self.update_status("on_hold", hold_reason="Original delay")
        self.assertEqual(response.status_code, 200)
        history = SqrSubmissionHistory.objects.get(submission=self.submission, action=SqrSubmissionHistory.Action.UPDATED)
        SqrSubmission.objects.filter(pk=self.submission.pk).update(hold_reason="Later reason")
        before, counts = self.snapshot(), self.mutation_counts()
        for url in (response.json()["undo_url"], reverse("hub:sqr-history-restore", args=[self.submission.pk, history.pk])):
            with self.subTest(url=url):
                self.assert_rejected_without_mutation(self.client.post(url), before, counts, status=409)

    @patch("hub.views._send_sqr_new_submission_email")
    def test_filing_and_edit_remarks_are_optional_and_independent_for_siblings(self, send_email):
        self.client.force_login(self.engineer)
        for extra in ({}, {"remarks": ""}, {"remarks": "Only this submission has remarks"}):
            response = self.client.post(reverse("hub:sqr"), {**self.payload, **extra})
            self.assertEqual(response.status_code, 302, response.content)
        siblings = list(SqrSubmission.objects.filter(linked_request=self.rq).exclude(pk=self.submission.pk).order_by("pk"))
        self.assertEqual([s.remarks for s in siblings], ["", "", "Only this submission has remarks"])
        first, second, third = siblings
        SqrSubmission.objects.filter(pk=first.pk).update(status="on_hold", hold_reason="First only")
        response = self.client.post(reverse("hub:sqr-edit", args=[first.pk]), {**self.payload, "remarks": "Edited SSE remark"})
        self.assertEqual(response.status_code, 302, response.content)
        first.refresh_from_db()
        self.assertEqual(first.remarks, "Edited SSE remark")
        self.assertEqual(first.hold_reason, "First only")
        self.assertEqual(first.status, "on_hold")
        response = self.client.post(reverse("hub:sqr-edit", args=[first.pk]), self.payload)
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.remarks, "")
        self.assertEqual(first.hold_reason, "First only")
        second.refresh_from_db()
        third.refresh_from_db()
        self.assertEqual(second.remarks, "")
        self.assertEqual(second.status, "submitted")
        self.assertEqual(second.hold_reason, "")
        self.assertEqual(third.remarks, "Only this submission has remarks")
        self.assertEqual(third.hold_reason, "")

    def test_tracker_remarks_precede_status_and_hold_reason_is_in_status_cell(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(status="on_hold", hold_reason="Customer <decision> pending")
        response = self.client.get(reverse("hub:sqr"))
        self.assertEqual(response.status_code, 200)
        parser = TrackerRows()
        parser.feed(response.content.decode())
        header = next(row for row in parser.rows if any(cell["text"].strip() == "SQR Status" for cell in row["cells"]))
        labels = [cell["text"].strip() for cell in header["cells"]]
        status_index = labels.index("SQR Status")
        self.assertEqual(labels[status_index - 1], "Remarks")
        row = next(row for row in parser.rows if row["attrs"].get("data-sqr-ref") == self.submission.reference_code)
        self.assertEqual(row["attrs"]["data-sqr-status"], "on_hold")
        self.assertIn(self.submission.remarks, row["cells"][status_index - 1]["text"])
        self.assertIn("On Hold", row["cells"][status_index]["text"])
        self.assertIn("Customer <decision> pending", row["cells"][status_index]["text"])
        self.assertContains(response, "Customer &lt;decision&gt; pending")
        content = response.content.decode()
        filter_markup = content.split('id="sqr-table-filter-status"', 1)[1].split("</select>", 1)[0]
        self.assertIn('<option value="on_hold">On Hold</option>', filter_markup)

    def export_workbook(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("hub:sqr-export"))
        self.assertEqual(response.status_code, 200)
        return load_workbook(io.BytesIO(response.content))

    def import_workbook(self, workbook, job_id):
        content = io.BytesIO()
        workbook.save(content)
        importer = SqrImportView()
        with patch("hub.views.close_old_connections"):
            importer._run_import_job(job_id=job_id, user_id=self.admin.pk, file_bytes=content.getvalue())
        return importer._get_status(self.admin, job_id)

    def test_export_import_roundtrip_preserves_status_reason_and_sibling_remarks(self):
        second = self.create_sqr()
        SqrSubmission.objects.filter(pk=self.submission.pk).update(status="on_hold", hold_reason="Customer delay")
        SqrSubmission.objects.filter(pk=second.pk).update(remarks="Second note", hold_reason="Earlier delay")
        expected = {
            self.submission.reference_code: ("on_hold", "Customer delay", self.submission.remarks),
            second.reference_code: ("submitted", "Earlier delay", "Second note"),
        }
        workbook = self.export_workbook()
        rows = list(workbook.active.iter_rows(values_only=True))
        headers = list(rows[0])
        self.assertEqual(headers[headers.index("SQR Status") - 1], "Remarks")
        self.assertIn("Hold Reason", headers)
        held_row = next(row for row in rows[1:] if row[headers.index("SQR ID")] == self.submission.reference_code)
        self.assertEqual(held_row[headers.index("SQR Status")], "On Hold")
        self.assertEqual(held_row[headers.index("Hold Reason")], "Customer delay")
        self.assertEqual(held_row[headers.index("Remarks")], self.submission.remarks)
        status = self.import_workbook(workbook, "hold-roundtrip-test")
        self.assertEqual(status["state"], "completed", status)
        imported = {
            sqr.reference_code: (sqr.status, sqr.hold_reason, sqr.remarks)
            for sqr in SqrSubmission.objects.filter(linked_request=self.rq)
        }
        self.assertEqual(imported, expected)

    def test_import_invalid_hold_reason_does_not_replace_existing_submissions(self):
        workbook = self.export_workbook()
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        sheet.cell(2, headers.index("SQR Status") + 1, "On Hold")
        for reason in ("", " \t "):
            with self.subTest(reason=reason):
                sheet.cell(2, headers.index("Hold Reason") + 1, reason)
                before, counts = self.snapshot(), self.mutation_counts()
                status = self.import_workbook(workbook, "hold-invalid-import-test")
                self.assertEqual(status["state"], "failed", status)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.mutation_counts(), counts)
                self.assertEqual(SqrSubmission.objects.count(), 1)

    def legacy_workbook(self):
        workbook = self.export_workbook()
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        for header in (
            "Hold Reason", "Remarks", "Schedule Performance", "Budget/Effort Performance",
            "Quality Performance", "Legacy Stored Warranty End Date",
        ):
            sheet.delete_cols(headers.index(header) + 1)
            headers.remove(header)
        for old, legacy in (
            ("Legacy Target Finish Date", "Target Finish Date"),
            ("Legacy Health Status", "Health Status"),
            ("Finish Date", "Actual Finish Date"),
            ("Legacy Completion Signed Date", "Completion Signed Date"),
        ):
            index = headers.index(old)
            headers[index] = legacy
            sheet.cell(1, index + 1, legacy)
        self.assertEqual(len(headers), 43)
        self.assertEqual(headers[19], "SQR Status")
        self.assertEqual(headers[37], "PO Remarks")
        return workbook

    def test_named_legacy_headers_keep_status_and_po_remarks_in_original_columns(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(
            status="for_revision", remarks="Not present in legacy file",
            revenue_remarks="Legacy PO note", revenue_overview="Legacy billing note",
        )
        workbook = self.legacy_workbook()
        status = self.import_workbook(workbook, "legacy-named-hold-test")
        self.assertEqual(status["state"], "completed", status)
        imported = SqrSubmission.objects.get(reference_code=self.submission.reference_code)
        self.assertEqual(imported.status, "for_revision")
        self.assertEqual(imported.hold_reason, "")
        self.assertEqual(imported.remarks, "")
        self.assertEqual(imported.revenue_remarks, "Legacy PO note")
        self.assertEqual(imported.revenue_overview, "Legacy billing note")

    def test_headerless_legacy_43_and_shorter_rows_use_legacy_positions(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(
            status="for_revision", revenue_remarks="Headerless PO note",
        )
        for width in (43, 40):
            with self.subTest(width=width):
                workbook = self.legacy_workbook()
                sheet = workbook.active
                sheet.delete_rows(1)
                if width < 43:
                    sheet.delete_cols(width + 1, 43 - width)
                status = self.import_workbook(workbook, f"legacy-headerless-{width}-test")
                self.assertEqual(status["state"], "completed", status)
                imported = SqrSubmission.objects.get(reference_code=self.submission.reference_code)
                self.assertEqual(imported.status, "for_revision")
                self.assertEqual(imported.remarks, "")
                self.assertEqual(imported.hold_reason, "")
                self.assertEqual(imported.revenue_remarks, "Headerless PO note")

    def test_named_new_headers_preserve_three_independent_notes_fields(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(
            status="on_hold", hold_reason="Pending scope", remarks="SSE note",
            revenue_remarks="Separate PO note",
        )
        original = self.export_workbook()
        headers = [cell.value for cell in original.active[1]]
        self.assertEqual(headers[headers.index("SQR Status") - 1], "Remarks")
        self.assertEqual(headers[headers.index("SQR Status") + 1], "Hold Reason")
        status = self.import_workbook(original, "named-new-hold-headers-test")
        self.assertEqual(status["state"], "completed", status)
        imported = SqrSubmission.objects.get(reference_code=self.submission.reference_code)
        self.assertEqual(imported.status, "on_hold")
        self.assertEqual(imported.hold_reason, "Pending scope")
        self.assertEqual(imported.remarks, "SSE note")
        self.assertEqual(imported.revenue_remarks, "Separate PO note")

    def test_stale_engineer_remarks_edit_preserves_concurrent_pm_hold_and_history(self):
        url = reverse("hub:sqr-edit", args=[self.submission.pk])
        self.client.force_login(self.engineer)
        form = self.client.get(url).context["form"]
        self.assertEqual(form.instance.status, "submitted")
        self.assertEqual(form.instance.hold_reason, "")

        self.client.force_login(self.pm)
        response = self.update_status("on_hold", hold_reason="PM hold after engineer loaded form")
        self.assertEqual(response.status_code, 200)
        hold_change = SqrSubmissionChange.objects.get(pk=response.json()["change_id"])
        self.client.force_login(self.engineer)
        response = self.client.post(url, {**self.payload, "remarks": "Updated engineer remarks"})
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.remarks, "Updated engineer remarks")
        self.assertEqual(self.submission.status, "on_hold")
        self.assertEqual(self.submission.hold_reason, "PM hold after engineer loaded form")
        hold_change.refresh_from_db()
        self.assertEqual(hold_change.new_values["status"], "on_hold")
        self.assertEqual(hold_change.new_values["hold_reason"], self.submission.hold_reason)
        history = SqrSubmissionHistory.objects.filter(submission=self.submission).latest("pk")
        self.assertEqual(history.new_values["remarks"], "Updated engineer remarks")
        for field in ("status", "hold_reason"):
            self.assertNotIn(field, history.old_values)
            self.assertNotIn(field, history.new_values)

    def update_hold_reason(self, value):
        return self.client.post(
            reverse("hub:sqr-inline-update", args=[self.submission.pk]),
            data=json.dumps({"field": "hold_reason", "value": value}),
            content_type="application/json",
        )

    def test_direct_hold_reason_invalid_values_are_rejected_atomically(self):
        for user in (self.pm, self.admin):
            self.client.force_login(user)
            for status in ("submitted", "on_hold"):
                SqrSubmission.objects.filter(pk=self.submission.pk).update(
                    status=status, hold_reason="Retained reason",
                )
                invalid_values = [None, False, 0, [], {}]
                if status == "on_hold":
                    invalid_values.extend(["", " \t\r\n "])
                for value in invalid_values:
                    with self.subTest(role=user.role, status=status, value=value):
                        before, counts = self.snapshot(), self.mutation_counts()
                        self.assert_rejected_without_mutation(
                            self.update_hold_reason(value), before, counts,
                        )

    def test_pm_admin_direct_hold_reason_is_trimmed_without_changing_status_or_remarks(self):
        for user in (self.pm, self.admin):
            self.client.force_login(user)
            for status in ("submitted", "on_hold"):
                with self.subTest(role=user.role, status=status):
                    SqrSubmission.objects.filter(pk=self.submission.pk).update(
                        status=status, hold_reason="Previous reason",
                    )
                    before = self.snapshot()
                    response = self.update_hold_reason(" \t Customer confirmation pending \r\n ")
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertTrue(response.json()["ok"])
                    self.assertEqual(response.json()["hold_reason"], "Customer confirmation pending")
                    after = self.snapshot()
                    self.assertEqual(after["hold_reason"], "Customer confirmation pending")
                    for field in ("status", "remarks", "review_notes", "reviewed_at", "validity_due_date"):
                        self.assertEqual(after[field], before[field], field)
                    change = SqrSubmissionChange.objects.get(pk=response.json()["change_id"])
                    self.assertEqual(change.field, "hold_reason")
                    self.assertEqual(change.old_values["hold_reason"], "Previous reason")
                    self.assertEqual(change.new_values["hold_reason"], "Customer confirmation pending")

    def test_sse_cannot_directly_edit_hold_reason(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(
            status="on_hold", hold_reason="PM reason",
        )
        for user in (self.engineer, self.paused_engineer):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                before, counts = self.snapshot(), self.mutation_counts()
                self.assert_rejected_without_mutation(
                    self.update_hold_reason("Unauthorized reason"), before, counts, status=403,
                )

    def test_new_remarks_never_fall_back_into_empty_po_remarks(self):
        SqrSubmission.objects.filter(pk=self.submission.pk).update(
            remarks="SSE note only", revenue_remarks="",
        )
        status = self.import_workbook(self.export_workbook(), "independent-import-remarks-test")
        self.assertEqual(status["state"], "completed", status)
        imported = SqrSubmission.objects.get(reference_code=self.submission.reference_code)
        self.assertEqual(imported.remarks, "SSE note only")
        self.assertEqual(imported.revenue_remarks, "")


@override_settings(ACS_EMAIL_CONNECTION_STRING="", ACS_EMAIL_SENDER="")
class SqrApprovalPolicyTests(MultipleSqrFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.delivery_pm = User.objects.create_user(username="sqr_delivery_pm", role=User.Roles.PM_ESG)
        self.row = self.create_sqr()
        self.payload["project_details"] = "Implementation"
        SqrSubmission.objects.filter(pk=self.row.pk).update(assigned_pm=self.delivery_pm)

    def inline(self, field, value, **extra):
        return self.client.post(reverse("hub:sqr-inline-update", args=[self.row.pk]),
                                {"field": field, "value": value, **extra}, content_type="application/json")

    def edit_url(self):
        return reverse("hub:sqr-edit", args=[self.row.pk])

    def state(self):
        return (SqrSubmission.objects.filter(pk=self.row.pk).values().get(),
                list(self.row.field_changes.values()), list(self.row.history_entries.values()))

    def approve(self):
        self.client.force_login(self.pm)
        self.assertEqual(self.inline("status", "reviewed").status_code, 200)

    def history_url(self):
        return reverse("hub:sqr-history", args=[self.row.pk])

    def restore_url(self, entry):
        return reverse("hub:sqr-history-restore", args=[self.row.pk, entry.pk])

    def test_approved_owner_can_save_remarks_without_saving_quote_fields(self):
        quote = {"sse_amount": Decimal("40123.00"), "pm_amount": Decimal("24678.00")}
        SqrSubmission.objects.filter(pk=self.row.pk).update(**quote)
        self.client.force_login(self.engineer)
        self.assertEqual(self.client.get(self.edit_url()).status_code, 200)
        self.approve()
        self.engineer.role = User.Roles.ON_HOLD
        self.engineer.save(update_fields=["role"])
        self.client.force_login(self.engineer)
        self.assertIn('disabled', str(self.client.get(self.edit_url()).context["form"]["sse_manhrs"]))
        before, changes, entries = self.state()
        response = self.client.post(self.edit_url(), {**self.payload, "remarks": "Filing note", "sse_manhrs": "36"})
        self.assertRedirects(response, reverse("hub:sqr"), fetch_redirect_response=False)
        after, unchanged_changes, updated_entries = self.state()
        self.assertEqual(after.pop("remarks"), "Filing note")
        self.assertNotEqual(after.pop("updated_at"), before.pop("updated_at"))
        before.pop("remarks")
        self.assertEqual(after, before)
        self.assertEqual(unchanged_changes, changes)
        self.assertEqual(len(updated_entries), len(entries) + 1)
        self.assertEqual(updated_entries[0]["new_values"], {"remarks": "Filing note"})

    def test_pausing_legacy_approval_without_dates_keeps_quote_locked(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(status=SqrSubmission.Status.APPROVED)
        self.client.force_login(self.pm)
        response = self.inline("status", "on_hold", hold_reason="Awaiting budget")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["quote_locked"])
        self.client.force_login(self.engineer)
        form = self.client.get(self.edit_url()).context["form"]
        self.assertTrue(form.fields["project_title"].disabled)

    def test_locked_form_preserves_legacy_missing_filing_values_when_saving_remarks(self):
        SqrSubmission.objects.filter(pk=self.row.pk).update(linked_request=None, sqr_folder_link="")
        self.approve()
        self.client.force_login(self.engineer)
        self.assertRedirects(self.client.post(self.edit_url(), {"remarks": "Legacy filing note"}),
                             reverse("hub:sqr"), fetch_redirect_response=False)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.linked_request_id)
        self.assertEqual(self.row.sqr_folder_link, "")
        self.assertEqual(self.row.remarks, "Legacy filing note")

    def test_locked_form_condition_uses_saved_quote_to_store_visible_answer(self):
        key = "sqr_submission"
        schema = form_editor.baseline_schema(key)
        schema["fields"].append({
            "key": "custom_filing_note", "type": "text", "label": "Filing note",
            "condition": {"field": "project_details", "operator": "equals", "value": "Implementation"},
        })
        form_editor.save_draft(key, schema, self.admin, 0)
        form_editor.publish(key, self.admin, 1)
        self.approve()
        self.client.force_login(self.engineer)
        form = self.client.get(self.edit_url()).context["form"]
        self.assertTrue(form.fields["project_details"].disabled)
        response = self.client.post(self.edit_url(), {
            TOKEN_FIELD: form.initial[TOKEN_FIELD], "remarks": "Optional note", "custom_filing_note": "Filed",
        })
        self.assertRedirects(response, reverse("hub:sqr"), fetch_redirect_response=False)
        self.assertEqual(form_editor.get_custom_answers(key, self.row), {"custom_filing_note": "Filed"})

    @patch("hub.views._send_sqr_for_revision_email")
    def test_assigned_reviewer_explicitly_reopens_approved_hold_for_owner(self, revision_email: MagicMock) -> None:
        """Let the owner revise an approved hold only after its assigned reviewer reopens it."""
        self.approve()
        self.assertEqual(self.inline("status", "on_hold", hold_reason="Awaiting decision").status_code, 200)
        before = self.state()
        self.assertEqual(self.client.post(reverse("hub:sqr-revenue-update", args=[self.row.pk]), {
            "stage_action": "quotation", f"quote-{self.row.pk}-quotation_total_price": "90000",
            f"quote-{self.row.pk}-discount_rate": "5",
        }).status_code, 302)
        self.assertEqual(self.state(), before)
        self.client.force_login(self.engineer)
        response = self.client.post(self.edit_url(), {**self.payload, "sse_manhrs": "36"})
        self.assertEqual(response.status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.sse_manhrs, Decimal(20))
        self.assertEqual(self.state()[1:], before[1:])
        self.client.force_login(self.pm)
        before = self.state()
        self.assertEqual(self.inline("status", "submitted").status_code, 409)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.inline("status", "for_revision", review_notes="Review revised scope").status_code, 200)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.reviewed_at)
        self.assertIsNone(self.row.validity_due_date)
        self.assertEqual(self.row.reviewed_by, self.pm)
        self.assertEqual(self.row.assigned_pm, self.delivery_pm)
        revision_email.assert_called_once()
        self.client.force_login(self.engineer)
        response = self.client.post(self.edit_url(), {**self.payload, "sse_manhrs": "36"})
        self.assertEqual(response.status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.sse_manhrs, Decimal(36))

    def test_nonreviewer_reads_grid_and_history_but_only_edits_delivery(self):
        self.client.force_login(self.pm)
        change = self.inline("sse_manhrs", "22").json()
        quote_entry = self.row.history_entries.get()
        self.client.force_login(self.delivery_pm)
        history = self.client.get(self.history_url()).json()["entries"]
        self.assertEqual(history[0]["id"], quote_entry.pk)
        self.assertFalse(history[0]["restorable"])
        record = self.client.get(reverse("hub:sqr-prototype-data"), {"pk": self.row.pk}).json()["record"]
        self.assertIn("delivery_progress", record["editable"])
        for field in ("status", "sse_manhrs", "proposal_status", "revenue_status"):
            self.assertNotIn(field, record["editable"])
        page = self.client.get(reverse("hub:sqr"), {"view": "tracker", "assigned": "all"})
        parser = TrackerRows()
        parser.feed(page.content.decode())
        row = next(row for row in parser.rows if row["attrs"].get("data-sqr-ref") == self.row.reference_code)
        fields = {cell["attrs"].get("data-sqr-field") for cell in row["cells"]}
        self.assertIn("delivery_progress", fields)
        self.assertTrue(fields.isdisjoint({"status", "sse_manhrs", "proposal_status", "revenue_status"}))
        before = self.state()
        for field, value in (("status", "reviewed"), ("hold_reason", "Wait"), ("sse_manhrs", "28"),
                             ("proposal_status", "closed_won"), ("revenue_status", "billed")):
            with self.subTest(field=field):
                self.assertEqual(self.inline(field, value).status_code, 403)
                self.assertEqual(self.state(), before)
        for url in (change["undo_url"], self.restore_url(quote_entry)):
            self.assertEqual(self.client.post(url).status_code, 403)
            self.assertEqual(self.state(), before)
        delivery = self.inline("delivery_progress", 40).json()
        self.assertTrue(delivery["ok"])
        self.assertEqual(self.client.post(delivery["undo_url"]).status_code, 200)

    def test_approval_cannot_be_reverted_to_an_unapproved_state(self):
        self.client.force_login(self.pm)
        for previous_status in (SqrSubmission.Status.FOR_PROCESSING, SqrSubmission.Status.ON_HOLD):
            for action in ("undo", "restore"):
                with self.subTest(previous_status=previous_status, action=action):
                    self.row = self.create_sqr()
                    if previous_status == SqrSubmission.Status.ON_HOLD:
                        response = self.inline("status", previous_status, hold_reason="Awaiting review")
                        self.assertEqual(response.status_code, 200)
                    self.approve()
                    change = self.row.field_changes.latest("pk")
                    entry = self.row.history_entries.latest("pk")
                    before = self.state()
                    if action == "undo":
                        url = reverse("hub:sqr-inline-undo", args=[self.row.pk, change.pk])
                    else:
                        url = self.restore_url(entry)
                        listed = self.client.get(self.history_url()).json()["entries"]
                        with self.subTest(affordance="history"):
                            self.assertFalse(next(item for item in listed if item["id"] == entry.pk)["restorable"])
                    self.assertEqual(self.client.post(url).status_code, 409)
                    self.assertEqual(self.state(), before)

    def test_undo_to_approved_keeps_approved_hold_locked_and_preserves_metadata(self):
        self.approve()
        approved, _, _ = self.state()
        held = self.inline("status", "on_hold", hold_reason="Awaiting customer decision").json()
        self.assertTrue(held["quote_locked"])
        restore = self.client.post(held["undo_url"])
        self.assertEqual(restore.status_code, 200)
        self.assertTrue(restore.json()["quote_locked"])
        restored, _, _ = self.state()
        self.assertEqual(restored, approved)

    @patch("hub.views._send_sqr_for_revision_email")
    def test_history_cannot_restore_approval_after_a_newer_quote_edit(self, revision_email):
        self.client.force_login(self.pm)
        quote = self.inline("pm_manhrs", "16").json()
        quote_entry = self.row.history_entries.get()
        self.approve()
        before = self.state()
        self.assertEqual(self.inline("pm_manhrs", "24").status_code, 409)
        for url in (quote["undo_url"], self.restore_url(quote_entry)):
            self.assertEqual(self.client.post(url).status_code, 409)
        self.assertEqual(self.state(), before)
        revision = self.inline("status", "for_revision", review_notes="Revise estimate").json()
        revision_entry = self.row.history_entries.latest("pk")
        self.assertEqual(self.inline("pm_manhrs", "24").status_code, 200)
        self.assertEqual(self.inline("pm_manhrs", "16").status_code, 200)
        before = self.state()
        for url in (revision["undo_url"], self.restore_url(revision_entry)):
            self.assertEqual(self.client.post(url).status_code, 409)
            self.assertEqual(self.state(), before)
        listed = self.client.get(self.history_url()).json()["entries"]
        self.assertFalse(next(entry for entry in listed if entry["id"] == revision_entry.pk)["restorable"])

    @patch("hub.views._send_sqr_for_revision_email")
    def test_history_reuses_quote_edits_without_changing_restore_decisions(self, revision_email):
        self.approve()
        self.assertEqual(self.inline("status", "for_revision", review_notes="Revise estimate").status_code, 200)
        revision = self.row.history_entries.latest("pk")
        older = self.row.history_entries.create(
            actor=self.pm, field=revision.field, old_values=revision.old_values,
            new_values=revision.new_values, metadata=revision.metadata,
        )
        same_time = self.row.history_entries.create(
            actor=self.pm, field="pm_manhrs", old_values={"pm_manhrs": "16"}, new_values={"pm_manhrs": "24"},
        )
        self.row.history_entries.filter(pk=same_time.pk).update(created_at=older.created_at)
        self.row.history_entries.create(
            actor=self.pm, field="pm_manhrs", old_values={"pm_manhrs": "16"}, new_values={"pm_manhrs": "16"},
        )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.history_url())
        flags = {entry["id"]: entry["restorable"] for entry in response.json()["entries"]}
        self.assertFalse(flags[revision.pk])
        self.assertTrue(flags[older.pk])
        reads = [query for query in queries if f'FROM "{SqrSubmissionHistory._meta.db_table}"' in query["sql"]]
        self.assertEqual(len(reads), 2)

    def test_approved_proposal_saves_configured_answer_from_saved_price(self):
        key = "sqr_proposal_status"
        schema = form_editor.baseline_schema(key)
        schema["fields"].append({
            "key": "custom_customer_decision", "type": "text", "label": "Customer decision",
            "condition": {"field": "quotation_total_price", "operator": "not_empty"},
        })
        form_editor.save_draft(key, schema, self.admin, 0)
        form_editor.publish(key, self.admin, 1)
        SqrSubmission.objects.filter(pk=self.row.pk).update(quotation_total_price=Decimal("90000.00"))
        self.approve()
        form = form_editor.get_form_class(key)(instance=SqrSubmission.objects.get(pk=self.row.pk))
        response = self.client.post(reverse("hub:sqr-proposal-update", args=[self.row.pk]), {
            TOKEN_FIELD: form.initial[TOKEN_FIELD], "proposal_status": "closed_won",
            "custom_customer_decision": "Accepted",
        })
        self.assertRedirects(response, reverse("hub:sqr"), fetch_redirect_response=False)
        self.assertEqual(form_editor.get_custom_answers(key, self.row), {"custom_customer_decision": "Accepted"})

    def test_current_stage_protects_pricing_but_reviewer_can_save_proposal_and_order(self):
        tracker_url = reverse("hub:sqr-revenue-update", args=[self.row.pk])
        proposal_url = reverse("hub:sqr-proposal-update", args=[self.row.pk])
        unlock_url = reverse("hub:sqr-to-revenue", args=[self.row.pk])
        revenue_url = reverse("hub:sqr-revenue-stage-update", args=[self.row.pk])
        self.client.force_login(self.pm)
        self.assertEqual(self.client.post(tracker_url, {
            "stage_action": "quotation", f"quote-{self.row.pk}-quotation_total_price": "90000",
            f"quote-{self.row.pk}-discount_rate": "5",
        }).status_code, 302)
        self.approve()
        self.client.force_login(self.delivery_pm)
        before = self.state()
        for url, data in ((unlock_url, {}), (proposal_url, {"proposal_status": "closed_won"}),
                          (revenue_url, {"revenue_status": "billed"})):
            self.assertEqual(self.client.post(url, data).status_code, 302)
            self.assertEqual(self.state(), before)
        self.client.force_login(self.pm)
        self.assertEqual(self.client.post(proposal_url, {"proposal_status": "closed_won",
                                                        "quotation_total_price": "95000"}).status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.proposal_status, "closed_won")
        self.assertEqual(self.row.quotation_total_price, Decimal("90000.00"))
        self.assertEqual(self.row.discount_rate, 5)
        self.assertEqual(self.client.post(tracker_url, {
            "stage_action": "order", f"order-{self.row.pk}-po_attachment_link": "https://example.com/po",
        }).status_code, 302)
        self.assertEqual(self.client.post(unlock_url).status_code, 302)
        self.assertEqual(self.client.post(revenue_url, {"revenue_status": "billed"}).status_code, 302)
        self.row.refresh_from_db()
        self.assertTrue(self.row.revenue_unlocked)
        self.assertIsNotNone(self.row.po_attached_at)
        self.assertEqual(self.row.revenue_status, "billed")


class SqrOnHoldMigrationTests(MultipleSqrFixtures, TransactionTestCase):
    def test_0053_preserves_existing_status_remarks_and_approval_metadata(self):
        first = self.create_sqr()
        reviewed_at = timezone.now() - timedelta(days=3)
        due_date = timezone.localdate() + timedelta(days=27)
        SqrSubmission.objects.filter(pk=first.pk).update(
            status="reviewed", remarks="Existing SSE remarks", revenue_remarks="Existing PO remarks",
            reviewed_at=reviewed_at, reviewed_by=self.pm, validity_due_date=due_date,
        )
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        current = [node for node in executor.loader.graph.nodes if node[0] == "hub" and node[1].startswith("0053_")]
        self.assertEqual(len(current), 1)
        previous = [("hub", "0052_allow_multiple_sqrs_per_request")]
        try:
            executor.migrate(previous)
            executor = MigrationExecutor(connection)
            old_sqr = executor.loader.project_state(previous).apps.get_model("hub", "SqrSubmission")
            before = old_sqr.objects.filter(pk=first.pk).values().get()
            executor.migrate(current)
            historical_sqr = executor.loader.project_state(current).apps.get_model("hub", "SqrSubmission")
            after = historical_sqr.objects.filter(pk=first.pk).values().get()
            for field, value in before.items():
                self.assertEqual(after[field], value, field)
            self.assertEqual(after["hold_reason"], "")
            self.assertTrue(historical_sqr._meta.get_field("hold_reason").blank)
            self.assertIn(("on_hold", "On Hold"), historical_sqr._meta.get_field("status").choices)
        finally:
            MigrationExecutor(connection).migrate(leaves)
