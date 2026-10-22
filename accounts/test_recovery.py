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

    def test_schema_guard_allows_explicitly_excluded_outlook_models(self):
        from django.apps import apps
        from accounts import recovery

        actual = {model._meta.label_lower for model in apps.get_app_config("hub").get_models()}
        included = {model._meta.label_lower for model in recovery.hub_models()}
        self.assertEqual(actual, set(recovery.MODEL_LABELS) | set(recovery.EXCLUDED_MODEL_LABELS))
        self.assertEqual(included, set(recovery.MODEL_LABELS))

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
        _, payload = self.create_backup()
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
