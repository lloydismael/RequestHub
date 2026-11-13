import json
from unittest.mock import patch

from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from django.urls import reverse

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

    def create_backup(self):
        response = self.client.post(reverse("accounts:backup-create"), {"reason": "Test snapshot"})
        self.assertEqual(response.status_code, 302)
        from accounts.models import BackupSnapshot
        snapshot = BackupSnapshot.objects.latest("pk")
        downloaded = self.client.get(reverse("accounts:backup-download", args=[snapshot.pk]))
        self.assertEqual(downloaded.status_code, 200)
        return snapshot, json.loads(downloaded.content)

    def test_old_backup_transitions_once_but_current_explicit_blanks_stay_blank(self):
        from datetime import date
        from accounts import recovery
        from hub.models import SqrSubmission, SqrTrackerSettings, SqrSubmissionHistory
        row = SqrSubmission.objects.create(engineer=self.user, pm_esg_reviewer=self.user, customer_name='Fictional old backup', project_title='Recovery fixture',
                                           project_details='Deployment Only', delivery_health='off_track', managed_support_amount=0,
                                           delivery_completion_signed_date=date(2026, 11, 1), managed_support_end_date=date(2027, 2, 9))
        _, payload = self.create_backup()
        payload['models'] = [label for label in payload['models'] if label != 'hub.sqrtrackersettings']
        payload['data'] = [record for record in payload['data'] if record['model'] != 'hub.sqrtrackersettings']
        for record in payload['data']:
            if record['model'] == 'hub.sqrsubmission':
                for field in ('schedule_performance', 'budget_effort_performance', 'quality_performance', 'delivery_transition_metadata'):
                    record['fields'].pop(field)
        payload['checksum'] = recovery.checksum(payload)
        SqrTrackerSettings.objects.update_or_create(pk=1, defaults={'show_classic_sqr_tracker': False})
        preview, _ = recovery.create_preview(self.user, payload)
        recovery.restore_preview(self.user, preview.token)
        row.refresh_from_db()
        self.assertEqual(row.schedule_performance, 'delayed')
        self.assertEqual(row.managed_support_start_date, date(2026, 12, 1))
        self.assertEqual(row.managed_support_end_date, date(2027, 2, 9))
        self.assertIsNone(row.delivery_actual_finish_date)
        self.assertEqual(row.delivery_transition_metadata['version'], 1)
        self.assertIn('raw_end_differs_from_displayed', row.delivery_transition_metadata['warnings'])
        self.assertFalse(SqrSubmissionHistory.objects.exists())
        from hub.services.sqr_settings import classic_tracker_visible
        self.assertTrue(classic_tracker_visible())
        SqrSubmission.objects.filter(pk=row.pk).update(schedule_performance='', budget_effort_performance='', quality_performance='',
                                                       managed_support_start_date=None, managed_support_end_date=None, delivery_transition_metadata={})
        _, current = self.create_backup()
        SqrSubmission.objects.filter(pk=row.pk).update(schedule_performance='on_track', managed_support_start_date=date(2026, 12, 1))
        preview, _ = recovery.create_preview(self.user, current)
        recovery.restore_preview(self.user, preview.token)
        row.refresh_from_db()
        self.assertEqual(row.schedule_performance, '')
        self.assertEqual(row.delivery_transition_metadata, {})
        self.assertIsNone(row.managed_support_start_date)
        self.assertIsNone(row.managed_support_end_date)

    def test_visibility_and_assessments_round_trip_without_relaxing_schema_guard(self):
        from accounts import recovery
        from hub.models import SqrSubmission, SqrTrackerSettings
        from hub.services.sqr_settings import classic_tracker_visible
        row = SqrSubmission.objects.create(engineer=self.user, pm_esg_reviewer=self.user, customer_name='Fictional red backup', project_title='Recorded recovery',
                                           schedule_performance='delayed', budget_effort_performance='over_budget', quality_performance='unmet')
        SqrTrackerSettings.objects.update_or_create(pk=1, defaults={'show_classic_sqr_tracker': False})
        _, payload = self.create_backup()
        SqrSubmission.objects.filter(pk=row.pk).update(schedule_performance='on_track')
        SqrTrackerSettings.objects.filter(pk=1).update(show_classic_sqr_tracker=True)
        preview, _ = recovery.create_preview(self.user, payload)
        recovery.restore_preview(self.user, preview.token)
        row.refresh_from_db()
        self.assertFalse(classic_tracker_visible())
        self.assertEqual((row.schedule_performance, row.budget_effort_performance, row.quality_performance), ('delayed', 'over_budget', 'unmet'))
        import copy
        partial = copy.deepcopy(payload)
        partial['models'].remove('hub.notification')
        partial['checksum'] = recovery.checksum(partial)
        with self.assertRaisesMessage(recovery.RecoveryError, 'Backup coverage does not match'):
            recovery.validate_payload(partial)
        malformed = copy.deepcopy(payload)
        next(record for record in malformed['data'] if record['model'] == 'hub.sqrtrackersettings')['pk'] = 2
        malformed['checksum'] = recovery.checksum(malformed)
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_payload(malformed)
        missing = copy.deepcopy(payload)
        next(record for record in missing['data'] if record['model'] == 'hub.sqrsubmission')['fields'].pop('quality_performance')
        missing['checksum'] = recovery.checksum(missing)
        with self.assertRaisesMessage(recovery.RecoveryError, 'Missing required fields'):
            recovery.validate_payload(missing)

    def test_create_and_download_snapshot_history(self):
        Account.objects.create(name="Saved customer")
        snapshot, payload = self.create_backup()
        self.assertEqual(payload["app"], "request-hub")
        self.assertEqual(str(payload["backup_version"]), "2")
        self.assertTrue(snapshot.checksum)
        response = self.client.get(reverse("accounts:update"))
        self.assertContains(response, "Test snapshot")
        self.assertContains(response, "Recovery Center")

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

    def test_failed_restore_rolls_back_and_retains_safety_snapshot(self):
        from accounts.models import BackupSnapshot, RecoveryEvent
        from accounts import recovery
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
        with patch("accounts.recovery._replace", side_effect=fail):
            with self.assertRaises(RuntimeError):
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
        Request.all_objects.filter(pk=req.pk).update(is_deleted=True, deleted_at=timezone.now())
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

    def test_safety_snapshot_failure_blocks_replacement(self):
        from accounts import recovery
        account = Account.objects.create(name="Original")
        _, payload = self.create_backup()
        preview, _ = recovery.create_preview(self.user, payload)
        with patch("accounts.recovery.persist_snapshot", side_effect=RuntimeError("Snapshot unavailable")):
            with self.assertRaises(RuntimeError):
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
