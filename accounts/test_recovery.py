import json
import re
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from hub.models import Account


class RecoveryCenterTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="recovery-admin", role=User.Roles.ADMIN)
        self.user.must_change_password = False
        self.user.save(update_fields=["must_change_password"])
        self.client.force_login(self.user)

    def upload(self, payload):
        return SimpleUploadedFile("backup.json", json.dumps(payload).encode(), content_type="application/json")

    def test_outlook_models_are_explicitly_excluded_from_recovery(self):
        from django.apps import apps

        from accounts import recovery

        actual = {model._meta.label_lower for model in apps.get_app_config("hub").get_models()}
        self.assertEqual(actual - set(recovery.MODEL_LABELS), set(recovery.EXCLUDED_MODEL_LABELS))
        self.assertEqual(
            {model._meta.label_lower for model in recovery.hub_models()},
            set(recovery.MODEL_LABELS),
        )

    def test_assistant_runtime_data_is_excluded_and_existing_snapshot_restores(self):
        from django.apps import apps

        from accounts import recovery
        from hub.models import AssistantUsageLimit

        actual = {model._meta.label_lower for model in apps.get_app_config("hub").get_models()}
        self.assertEqual(
            actual,
            set(recovery.MODEL_LABELS) | set(recovery.EXCLUDED_MODEL_LABELS),
        )
        self.assertIn("hub.assistantusagelimit", recovery.EXCLUDED_MODEL_LABELS)

        account = Account.objects.create(name="Assistant recovery customer")
        _, payload = self.create_backup()
        self.assertNotIn(
            "hub.assistantusagelimit",
            {row["model"] for row in payload["data"]},
        )
        AssistantUsageLimit.objects.create(
            scope_key=f"user:{self.user.pk}",
            owner=self.user,
        )
        Account.objects.filter(pk=account.pk).update(name="Changed customer")

        preview = self.client.post(
            reverse("accounts:backup-validate"),
            {"backup_file": self.upload(payload)},
        )
        self.assertEqual(preview.status_code, 200)
        response = self.client.post(
            reverse("accounts:restore"),
            {
                "preview_token": preview.context["preview_token"],
                "confirm_restore": "1",
                "confirmation_phrase": "RESTORE",
            },
        )

        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.name, "Assistant recovery customer")
        self.assertTrue(
            AssistantUsageLimit.objects.filter(scope_key=f"user:{self.user.pk}").exists()
        )

    def test_project_restore_preserves_global_tracker_visibility(self):
        from accounts import recovery
        from hub.models import SqrTrackerSettings

        try:
            recovery.hub_models()
        except recovery.RecoveryError as exc:
            self.fail(f"Project recovery unavailable: {exc}")
        account = Account.objects.create(name="Before tracker transition")
        setting = SqrTrackerSettings.objects.create(pk=1, show_classic_sqr_tracker=False)
        _, payload = self.create_backup()
        self.assertNotIn("hub.sqrtrackersettings", payload["models"])
        Account.objects.filter(pk=account.pk).update(name="Changed")
        preview = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
        self.assertEqual(preview.status_code, 200)
        response = self.client.post(reverse("accounts:restore"), {
            "preview_token": preview.context["preview_token"], "confirm_restore": "1",
            "confirmation_phrase": "RESTORE",
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        setting.refresh_from_db()
        self.assertEqual(account.name, "Before tracker transition")
        self.assertFalse(setting.show_classic_sqr_tracker)

    def test_pre_transition_sqr_backup_restores_without_reopening_signed_completion(self):
        from datetime import date

        from accounts import recovery
        from hub.models import SqrSubmission

        row = SqrSubmission.objects.create(
            engineer=self.user, pm_esg_reviewer=self.user, customer_name="Legacy",
            project_title="Signed legacy project", proposal_status="closed_won", delivery_health="off_track",
            delivery_completion_signed_date=date(2026, 11, 1),
        )
        _, payload = self.create_backup()
        for record in payload["data"]:
            if record["model"] == "hub.sqrsubmission":
                for name in ("schedule_performance", "budget_effort_performance", "quality_performance",
                             "delivery_transition_metadata", "legacy_delivery_completed"):
                    record["fields"].pop(name)
        payload["checksum"] = recovery.checksum(payload)
        SqrSubmission.objects.filter(pk=row.pk).update(quality_performance="unmet")
        preview = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
        self.assertEqual(preview.status_code, 200)
        self.assertIsNotNone(preview.context["preview_token"], "Pre-transition SQR backup was rejected")
        response = self.client.post(reverse("accounts:restore"), {
            "preview_token": preview.context["preview_token"], "confirm_restore": "1",
            "confirmation_phrase": "RESTORE",
        })
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertTrue(row.legacy_delivery_completed)
        self.assertIsNone(row.delivery_actual_finish_date)
        self.assertEqual((row.schedule_performance, row.budget_effort_performance, row.quality_performance),
                         ("", "", ""))
        self.assertEqual(row.delivery_transition_metadata, {})

    def test_current_sqr_backup_preserves_assessments_and_unmarked_signed_date(self):
        from datetime import date

        from hub.models import SqrSubmission

        row = SqrSubmission.objects.create(
            engineer=self.user, pm_esg_reviewer=self.user, customer_name="Current",
            project_title="Signed but unfinished", proposal_status="closed_won",
            delivery_completion_signed_date=date(2026, 11, 1),
            schedule_performance="at_risk", budget_effort_performance="within_budget", quality_performance="met",
        )
        _, payload = self.create_backup()
        SqrSubmission.objects.filter(pk=row.pk).update(quality_performance="unmet")
        preview = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
        self.assertEqual(preview.status_code, 200)
        response = self.client.post(reverse("accounts:restore"), {
            "preview_token": preview.context["preview_token"], "confirm_restore": "1",
            "confirmation_phrase": "RESTORE",
        })
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertFalse(row.legacy_delivery_completed)
        self.assertIsNone(row.delivery_actual_finish_date)
        self.assertEqual((row.schedule_performance, row.budget_effort_performance, row.quality_performance),
                         ("at_risk", "within_budget", "met"))

    def test_transition_compatibility_does_not_allow_other_missing_backup_fields(self):
        from accounts import recovery

        Account.objects.create(name="Strict coverage")
        _, payload = self.create_backup()
        payload["data"][0]["fields"].pop("name")
        payload["checksum"] = recovery.checksum(payload)
        with self.assertRaisesMessage(recovery.RecoveryError, "Missing required fields in hub.account"):
            recovery.validate_payload(payload)

    def create_backup(self):
        response = self.client.post(reverse("accounts:backup-create"), {"reason": "Test snapshot"})
        self.assertEqual(response.status_code, 302)
        from accounts.models import BackupSnapshot
        snapshot = BackupSnapshot.objects.latest("pk")
        downloaded = self.client.get(reverse("accounts:backup-download", args=[snapshot.pk]))
        self.assertEqual(downloaded.status_code, 200)
        return snapshot, json.loads(downloaded.content)

    def test_create_and_download_snapshot_history(self):
        Account.objects.create(name="Saved customer")
        snapshot, payload = self.create_backup()
        self.assertEqual(payload["app"], "request-hub")
        self.assertEqual(str(payload["backup_version"]), "2")
        self.assertTrue(snapshot.checksum)
        response = self.client.get(reverse("accounts:update"))
        self.assertContains(response, "Test snapshot")
        self.assertContains(response, "Recovery Center")

    def test_created_backup_feedback_and_latest_download_are_in_recovery_center(self):
        from accounts.models import BackupSnapshot

        response = self.client.post(reverse('accounts:backup-create'), {'reason': 'Before import'}, follow=True)
        snapshot = BackupSnapshot.objects.get()
        html = response.content.decode()
        feedback = f'Snapshot #{snapshot.pk} created.'
        recovery = html.partition('id="profile-panel-backup"')[2]
        self.assertTrue(feedback in recovery, 'The backup result is outside the section linked by the redirect.')
        self.assertEqual(html.count(feedback), 1)
        download = reverse('accounts:backup-download', args=[snapshot.pk])
        latest = re.search(r'<a\b[^>]*href="' + re.escape(download) + r'"[^>]*>[^<]*(?:<i[^>]*></i>)?\s*Download latest\s*</a>', recovery)
        self.assertIsNotNone(latest, 'The new backup should be downloadable without opening history.')

    def test_deleted_request_disclosure_reopens_for_search_and_pagination(self) -> None:
        """Keep deleted requests expanded when applying its search or changing pages."""
        from hub.models import Request

        account = Account.objects.create(name='Recovery example')
        deleted_at = timezone.now()
        Request.all_objects.bulk_create([
            Request(requestor=self.user, account=account, reference_code=f'REQ-RECOVERY-{i:02}',
                    is_deleted=True, deleted_at=deleted_at)
            for i in range(21)
        ])
        url = reverse('accounts:update')
        self.client.raise_request_exception = False
        for params, opened, reference in (({}, False, 'REQ-RECOVERY-20'),
                                          ({'deleted_query': ''}, True, 'REQ-RECOVERY-20'),
                                          ({'deleted_q': 'REQ-RECOVERY-03'}, True, 'REQ-RECOVERY-03'),
                                          ({'deleted_page': '2'}, True, 'REQ-RECOVERY-00')):
            with self.subTest(params=params):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, 200, 'Existing deleted requests must not break the profile.')
                html = response.content.decode()
                disclosure = re.search(r'<details\b[^>]*\bid="recovery-deleted"[^>]*>(.*?)</details>', html, re.DOTALL)
                self.assertIsNotNone(disclosure, 'Deleted requests need their own disclosure.')
                self.assertEqual(bool(re.search(r'\sopen\b', disclosure[0].split('>', 1)[0])), opened)
                self.assertIn(reference, disclosure[1])

    def test_project_restore_cannot_drop_protected_sqr_history(self):
        from accounts import recovery
        from hub.models import SqrSubmission, SqrSubmissionHistory

        sqr = SqrSubmission.objects.create(
            engineer=self.user, pm_esg_reviewer=self.user, customer_name="Retained Customer",
            project_title="Retained SQR", status=SqrSubmission.Status.FOR_REVISION,
        )
        _, payload = self.create_backup()
        preview, _ = recovery.create_preview(self.user, payload)
        entry = SqrSubmissionHistory.objects.create(
            submission=sqr, actor=self.user, field="status",
            old_values={"status": SqrSubmission.Status.APPROVED},
            new_values={"status": SqrSubmission.Status.FOR_REVISION},
        )

        with self.assertRaisesRegex(recovery.RecoveryError, "Only Django superusers"):
            recovery.restore_preview(self.user, preview.token)

        self.assertTrue(SqrSubmissionHistory.objects.filter(pk=entry.pk, submission=sqr).exists())

    def test_validation_is_non_destructive(self):
        account = Account.objects.create(name="Original customer")
        _, payload = self.create_backup()
        response = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/restore_preview.html")
        account.refresh_from_db()
        self.assertEqual(account.name, "Original customer")
        self.assertTrue(response.context["preview_token"])

    def test_invalid_upload_leaves_records_untouched(self):
        account = Account.objects.create(name="Untouched")
        for payload in [[], {}, {"app": "request-hub", "backup_version": "99", "data": []}]:
            response = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "Backup could not be validated", status_code=400)
            self.assertTrue(Account.objects.filter(pk=account.pk, name="Untouched").exists())

    def test_restore_requires_preview_and_confirmation(self):
        account = Account.objects.create(name="Do not delete")
        response = self.client.post(reverse("accounts:restore"), {"confirm_restore": "1", "confirmation_phrase": "RESTORE"})
        self.assertIn(response.status_code, [200, 302, 400])
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())

    def test_round_trip_restore_keeps_safety_snapshot_and_consumes_preview(self):
        from accounts.models import BackupSnapshot
        account = Account.objects.create(name="Before")
        _, payload = self.create_backup()
        Account.objects.filter(pk=account.pk).update(name="After")
        response = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
        token = response.context["preview_token"]
        before = BackupSnapshot.objects.count()
        response = self.client.post(reverse("accounts:restore"), {"preview_token": token, "confirm_restore": "1", "confirmation_phrase": "RESTORE"})
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.name, "Before")
        self.assertGreater(BackupSnapshot.objects.count(), before)
        Account.objects.filter(pk=account.pk).update(name="No replay")
        self.client.post(reverse("accounts:restore"), {"preview_token": token, "confirm_restore": "1", "confirmation_phrase": "RESTORE"})
        account.refresh_from_db()
        self.assertEqual(account.name, "No replay")
        new_account = Account.objects.create(name="Next account")
        self.assertNotEqual(new_account.pk, account.pk)

    def test_failed_restore_rolls_back_and_retains_safety_snapshot(self) -> None:
        """Roll back a failed restore without losing its pre-restore snapshot."""
        from accounts import recovery
        from accounts.models import BackupSnapshot, RecoveryEvent
        account = Account.objects.create(name="Before")
        _, payload = self.create_backup()
        Account.objects.filter(pk=account.pk).update(name="Current")
        preview, _ = recovery.create_preview(self.user, payload)
        count = BackupSnapshot.objects.count()
        original_replace = recovery._replace
        calls = 0
        def fail(payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_replace(payload)
            Account.objects.all().delete()
            raise RuntimeError("Simulated restore failure")
        with (
            patch("accounts.recovery._replace", side_effect=fail),
            self.assertRaises(RuntimeError),
        ):
            recovery.restore_preview(self.user, preview.token)
        self.assertTrue(Account.objects.filter(pk=account.pk, name="Current").exists())
        self.assertEqual(BackupSnapshot.objects.count(), count + 1)
        self.assertTrue(RecoveryEvent.objects.filter(action="restore", outcome="failed").exists())
        preview.refresh_from_db()
        self.assertIsNotNone(preview.consumed_at)

    def test_soft_deleted_request_included_and_bulk_recovery(self):
        from hub.models import Request
        account = Account.objects.create(name="Deleted customer")
        req = Request.objects.create(requestor=self.user, account=account,
                                     account_manager="Manager", product_category="Azure", engagement_type="support")
        response = self.client.post(reverse("hub:request-delete", args=[req.pk]))
        self.assertRedirects(response, reverse("hub:dashboard"), fetch_redirect_response=False)
        self.assertFalse(Request.objects.filter(pk=req.pk).exists())
        _, payload = self.create_backup()
        self.assertTrue(any(row["model"] == "hub.request" and row["pk"] == req.pk
                            and row["fields"]["is_deleted"] for row in payload["data"]))
        preview = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
        self.assertEqual(preview.status_code, 200)
        response = self.client.post(reverse("accounts:requests-recover"),
                                    {"request_ids": [req.pk], "confirm_recover": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Request.objects.filter(pk=req.pk).exists())
        req.refresh_from_db()
        self.assertIsNone(req.deleted_at)

    def test_confirmation_removes_request_from_active_list_and_preserves_history(self):
        from hub.models import EngineerActivityLog, Request, StatusLog

        requestor = User.objects.create_user(username="recovery-requestor", role=User.Roles.REQUESTOR)
        req = Request.objects.create(
            requestor=requestor,
            account=Account.objects.create(name="Recoverable customer"),
            account_manager="Manager",
            product_category="Azure",
            engagement_type=Request.Engagement.SUPPORT,
        )
        update = StatusLog.objects.create(request=req, author=requestor, message="Scheduled follow-up")
        activity = EngineerActivityLog.objects.create(
            request=req, account=req.account, engineer=self.user, request_date=timezone.localdate(),
            actual_hours=1, details="Completed review", location=EngineerActivityLog.Location.OFFICE,
        )
        url = reverse("hub:request-delete", args=[req.pk])
        self.client.force_login(requestor)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url)
        self.assertRedirects(response, reverse("hub:dashboard"), fetch_redirect_response=False)
        self.assertFalse(Request.objects.filter(pk=req.pk).exists())
        removed = Request.all_objects.filter(pk=req.pk).first()
        self.assertIsNotNone(removed)
        self.assertTrue(removed.is_deleted)
        self.assertIsNotNone(removed.deleted_at)
        self.assertEqual(removed.updated_at, removed.deleted_at)
        self.assertEqual(removed.reference_code, req.reference_code)
        self.assertTrue(removed.status_logs.filter(pk=update.pk).exists())
        self.assertTrue(removed.activity_logs.filter(pk=activity.pk).exists())

        deleted_at = removed.deleted_at
        self.assertRedirects(self.client.post(url), reverse("hub:dashboard"), fetch_redirect_response=False)
        removed.refresh_from_db()
        self.assertEqual(removed.deleted_at, deleted_at)
        self.assertEqual(removed.updated_at, deleted_at)
        self.assertTrue(removed.is_deleted)

        restore_url = reverse("hub:request-restore", args=[req.pk])
        self.client.post(restore_url)
        self.assertFalse(Request.objects.filter(pk=req.pk).exists())
        self.client.force_login(self.user)
        response = self.client.post(restore_url, follow=True)
        self.assertEqual(response.redirect_chain, [(reverse("accounts:update") + "#backup", 302)])
        recovery = response.content.decode().partition('id="profile-panel-backup"')[2]
        self.assertIn(f"Request {req.reference_code} has been restored.", recovery)
        restored = Request.objects.get(pk=req.pk)
        self.assertIsNone(restored.deleted_at)
        self.assertTrue(restored.status_logs.filter(pk=update.pk).exists())
        self.assertTrue(restored.activity_logs.filter(pk=activity.pk).exists())

    def test_removal_methods_respect_request_scope(self):
        from hub.models import Request

        owner = User.objects.create_user(username="recovery-owner", role=User.Roles.REQUESTOR)
        other = User.objects.create_user(username="recovery-unrelated", role=User.Roles.REQUESTOR)
        req = Request.objects.create(
            requestor=owner,
            account=Account.objects.create(name="Scoped customer"),
            account_manager="Manager",
            product_category="Azure",
            engagement_type=Request.Engagement.SUPPORT,
        )
        url = reverse("hub:request-delete", args=[req.pk])
        self.client.force_login(other)
        for remove in (self.client.post, self.client.delete):
            with self.subTest(method=remove.__name__):
                self.assertRedirects(remove(url), reverse("hub:dashboard"), fetch_redirect_response=False)
                self.assertTrue(Request.objects.filter(pk=req.pk).exists())
        self.client.force_login(self.user)
        self.assertRedirects(self.client.delete(url), reverse("hub:dashboard"), fetch_redirect_response=False)
        self.assertFalse(Request.objects.filter(pk=req.pk).exists())
        self.assertTrue(Request.all_objects.filter(pk=req.pk, is_deleted=True).exists())

    def test_preview_bound_to_creator_and_expires(self):
        from accounts import recovery
        from accounts.models import RecoveryPreview
        Account.objects.create(name="Protected")
        payload = recovery.build_payload()
        preview, _ = recovery.create_preview(self.user, payload)
        other = User.objects.create_user(username="other-admin", role=User.Roles.ADMIN)
        with self.assertRaises(recovery.RecoveryError):
            recovery.restore_preview(other, preview.token)
        RecoveryPreview.objects.filter(pk=preview.pk).update(expires_at=timezone.now())
        with self.assertRaises(recovery.RecoveryError):
            recovery.restore_preview(self.user, preview.token)
        self.assertTrue(Account.objects.filter(name="Protected").exists())

    def test_other_roles_cannot_access_recovery(self):
        self.user.role = User.Roles.REQUESTOR
        self.user.save(update_fields=["role"])
        for name in ["backup-create", "backup-validate", "restore", "requests-recover"]:
            with self.subTest(name=name):
                response = self.client.post(reverse("accounts:" + name))
                self.assertIn(response.status_code, [302, 403])

    def test_validation_rejects_duplicate_ids_and_unknown_models(self):
        from accounts import recovery
        Account.objects.create(name="Original")
        _, payload = self.create_backup()
        for mutation in ["duplicate", "unknown"]:
            changed = json.loads(json.dumps(payload))
            if mutation == "duplicate":
                changed["data"].append(changed["data"][0])
            else:
                changed["data"][0]["model"] = "accounts.user"
            changed["checksum"] = recovery.checksum(changed)
            with self.assertRaises(recovery.RecoveryError):
                recovery.create_preview(self.user, changed)
            self.assertTrue(Account.objects.filter(name="Original").exists())

    def test_safety_snapshot_failure_blocks_replacement(self) -> None:
        """Leave the current state untouched if its safety snapshot cannot be saved."""
        from accounts import recovery
        account = Account.objects.create(name="Original")
        _, payload = self.create_backup()
        preview, _ = recovery.create_preview(self.user, payload)
        with (
            patch("accounts.recovery.persist_snapshot", side_effect=RuntimeError("Snapshot unavailable")),
            self.assertRaises(RuntimeError),
        ):
            recovery.restore_preview(self.user, preview.token)
        self.assertTrue(Account.objects.filter(pk=account.pk, name="Original").exists())

    def test_validation_rejects_modified_checksum(self):
        Account.objects.create(name="Original")
        _, payload = self.create_backup()
        for record in payload["data"]:
            if record["model"] == "hub.account":
                record["fields"]["name"] = "Tampered"
                break
        response = self.client.post(reverse("accounts:backup-validate"), {"backup_file": self.upload(payload)})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Account.objects.filter(name="Original").exists())
