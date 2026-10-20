from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from hub.models import Account, EngineerActivityLog, Request, RequestLifecycleEvent
from hub.services import request_lifecycle


class RequestLifecycleServiceTests(TestCase):
    def setUp(self):
        self.requestor = User.objects.create_user(
            username="lifecycle-requestor",
            password="pass12345",
            role=User.Roles.REQUESTOR,
        )
        self.primary = User.objects.create_user(
            username="lifecycle-primary",
            password="pass12345",
            role=User.Roles.ENGINEER,
        )
        self.backup = User.objects.create_user(
            username="lifecycle-backup",
            password="pass12345",
            role=User.Roles.ENGINEER,
        )
        self.manager = User.objects.create_user(
            username="lifecycle-manager",
            password="pass12345",
            role=User.Roles.PM_ESG,
        )
        self.account = Account.objects.create(name="Lifecycle Account")

    def create_request(self, **overrides):
        values = {
            "requestor": self.requestor,
            "account": self.account,
            "account_manager": "Lifecycle Requestor",
            "product_category": "Azure",
            "engagement_type": Request.Engagement.SUPPORT,
            "priority": Request.Priority.MEDIUM,
        }
        values.update(overrides)
        return Request.objects.create(**values)

    def test_record_created_tracks_unassigned_request(self):
        request = self.create_request()

        result = request_lifecycle.record_created(request.pk, actor=self.requestor, source="test")

        self.assertEqual(result.request.lifecycle_stage, Request.LifecycleStage.CREATED)
        self.assertEqual(result.request.assignment_revision, 0)
        self.assertEqual(list(request.lifecycle_events.values_list("event_type", flat=True)), ["created"])

    def test_request_save_restores_missing_lifecycle_defaults(self):
        request = Request(
            requestor=self.requestor,
            account=self.account,
            account_manager="Lifecycle Requestor",
            product_category="Azure",
            engagement_type=Request.Engagement.SUPPORT,
            priority=Request.Priority.MEDIUM,
            assignment_revision=None,
            lifecycle_stage=None,
        )

        request.save()

        request.refresh_from_db()
        self.assertEqual(request.assignment_revision, 0)
        self.assertEqual(request.lifecycle_stage, Request.LifecycleStage.CREATED)

    def test_assigned_request_requires_primary_acceptance(self):
        request = self.create_request(engineer=self.primary, backup_engineer=self.backup)
        request_lifecycle.record_created(request.pk, actor=self.requestor, source="test")
        request.refresh_from_db()

        with self.assertRaises(PermissionDenied):
            request_lifecycle.acknowledge_request(
                request.pk,
                actor=self.backup,
                expected_revision=request.assignment_revision,
            )

        result = request_lifecycle.acknowledge_request(
            request.pk,
            actor=self.primary,
            expected_revision=request.assignment_revision,
        )

        self.assertEqual(result.request.lifecycle_stage, Request.LifecycleStage.ONGOING)
        self.assertEqual(
            list(request.lifecycle_events.values_list("event_type", flat=True)),
            ["created", "assigned", "acknowledged", "started"],
        )

    def test_primary_reassignment_returns_ongoing_request_to_assigned(self):
        request = self.create_request(engineer=self.primary, backup_engineer=self.backup)
        request_lifecycle.record_created(request.pk, actor=self.requestor)
        request.refresh_from_db()
        request_lifecycle.acknowledge_request(request.pk, actor=self.primary, expected_revision=request.assignment_revision)
        previous_revision = request.assignment_revision
        request.engineer = self.backup
        request.save()

        result = request_lifecycle.record_assignment_change(
            request.pk,
            previous_engineer_id=self.primary.pk,
            previous_backup_id=self.backup.pk,
            actor=self.manager,
        )

        self.assertEqual(result.request.lifecycle_stage, Request.LifecycleStage.ASSIGNED)
        self.assertEqual(result.request.assignment_revision, previous_revision + 1)
        self.assertEqual(result.events[0].event_type, RequestLifecycleEvent.EventType.ASSIGNED)

    def test_backup_only_change_does_not_reset_progress(self):
        request = self.create_request(engineer=self.primary)
        request_lifecycle.record_created(request.pk, actor=self.requestor)
        request.refresh_from_db()
        request_lifecycle.acknowledge_request(request.pk, actor=self.primary, expected_revision=request.assignment_revision)
        request.refresh_from_db()
        previous_revision = request.assignment_revision
        request.backup_engineer = self.backup
        request.save()

        result = request_lifecycle.record_assignment_change(
            request.pk,
            previous_engineer_id=self.primary.pk,
            previous_backup_id=None,
            actor=self.manager,
        )

        self.assertEqual(result.request.lifecycle_stage, Request.LifecycleStage.ONGOING)
        self.assertEqual(result.request.assignment_revision, previous_revision + 1)
        self.assertEqual(result.events, ())
        self.assertTrue(result.backup_changed)

    def test_assignment_change_preserves_authorized_capacity_override(self):
        request = self.create_request()
        request_lifecycle.record_created(request.pk, actor=self.requestor)
        for index in range(3):
            self.create_request(
                engineer=self.primary,
                engagement_type=(
                    Request.Engagement.DEPLOYMENT
                    if index == 0
                    else Request.Engagement.SUPPORT
                ),
            )
        request.engineer = self.primary
        request._allow_capacity_override = True
        request.save()

        result = request_lifecycle.record_assignment_change(
            request.pk,
            previous_engineer_id=None,
            previous_backup_id=None,
            actor=self.manager,
            allow_capacity_override=True,
        )

        self.assertEqual(result.request.engineer, self.primary)
        self.assertEqual(result.request.lifecycle_stage, Request.LifecycleStage.ASSIGNED)
        self.assertEqual(result.request.assignment_revision, 1)

    def test_acknowledge_does_not_recheck_engineer_capacity(self):
        request = self.create_request()
        request_lifecycle.record_created(request.pk, actor=self.requestor)
        for index in range(3):
            self.create_request(
                engineer=self.primary,
                engagement_type=(
                    Request.Engagement.DEPLOYMENT
                    if index == 0
                    else Request.Engagement.SUPPORT
                ),
            )
        request.engineer = self.primary
        request._allow_capacity_override = True
        request.save()
        request_lifecycle.record_assignment_change(
            request.pk,
            previous_engineer_id=None,
            previous_backup_id=None,
            actor=self.manager,
            allow_capacity_override=True,
        )
        request.refresh_from_db()

        result = request_lifecycle.acknowledge_request(
            request.pk,
            actor=self.primary,
            expected_revision=request.assignment_revision,
        )

        self.assertEqual(result.request.lifecycle_stage, Request.LifecycleStage.ONGOING)
        self.assertEqual(result.request.status, Request.Status.ONGOING)

    def test_repair_replaying_acknowledge_events_is_idempotent(self):
        """Re-running a data repair that replays ACKNOWLEDGED+STARTED events must
        not duplicate the event log (idempotency keys guard each revision)."""
        request = self.create_request(engineer=self.primary)
        request_lifecycle.record_created(request.pk, actor=self.requestor)
        request.refresh_from_db()
        request_lifecycle.acknowledge_request(request.pk, actor=self.primary, expected_revision=request.assignment_revision)
        request.refresh_from_db()

        revision = request.assignment_revision
        baseline_events = list(request.lifecycle_events.values_list("event_type", flat=True))

        # Simulate a data-repair replay using the same idempotency keys.
        request_lifecycle._create_event(
            request,
            event_type=RequestLifecycleEvent.EventType.ACKNOWLEDGED,
            stage=Request.LifecycleStage.ACKNOWLEDGED,
            previous_stage=Request.LifecycleStage.ASSIGNED,
            actor=self.primary,
            source="Data repair",
            idempotency_key=f"accepted:{revision}",
            is_synthetic=True,
        )
        request_lifecycle._create_event(
            request,
            event_type=RequestLifecycleEvent.EventType.STARTED,
            stage=Request.LifecycleStage.ONGOING,
            previous_stage=Request.LifecycleStage.ACKNOWLEDGED,
            actor=self.primary,
            source="Data repair",
            idempotency_key=f"started:{revision}",
            is_synthetic=True,
        )

        self.assertEqual(
            list(request.lifecycle_events.values_list("event_type", flat=True)),
            baseline_events,
        )

    def test_resolve_current_stage_prefers_event_log_over_stale_field(self):
        """The authoritative stage for the card comes from the event log, healing
        the exact Bucket-A bug where the denormalized field says 'assigned' after
        the engineer already acknowledged."""
        request = self.create_request(engineer=self.primary)
        request_lifecycle.record_created(request.pk, actor=self.requestor)
        request.refresh_from_db()
        request_lifecycle.acknowledge_request(request.pk, actor=self.primary, expected_revision=request.assignment_revision)
        request.refresh_from_db()

        # Simulate the real failure: the acknowledged/started events persisted but
        # the denormalized field drifted back to 'assigned'.
        request.lifecycle_stage = Request.LifecycleStage.ASSIGNED
        request.save(update_fields=["lifecycle_stage", "updated_at"])
        request.refresh_from_db()

        self.assertEqual(request.lifecycle_stage, Request.LifecycleStage.ASSIGNED)
        self.assertEqual(request_lifecycle.resolve_current_stage(request), Request.LifecycleStage.ONGOING)

        # reconcile heals the drift and is idempotent on a second call.
        self.assertEqual(request_lifecycle.reconcile_lifecycle_stage(request.pk), Request.LifecycleStage.ONGOING)
        request.refresh_from_db()
        self.assertEqual(request.lifecycle_stage, Request.LifecycleStage.ONGOING)
        self.assertIsNone(request_lifecycle.reconcile_lifecycle_stage(request.pk))


class RequestLifecycleManagePageTests(TestCase):
    def setUp(self):
        self.requestor = User.objects.create_user(
            username="tracker-requestor",
            password="pass12345",
            role=User.Roles.REQUESTOR,
            email="tracker.requestor@example.com",
        )
        self.primary = User.objects.create_user(
            username="tracker-primary",
            password="pass12345",
            role=User.Roles.ENGINEER,
            first_name="Primary",
            last_name="Engineer",
            email="tracker.primary@example.com",
        )
        self.secondary = User.objects.create_user(
            username="tracker-secondary",
            password="pass12345",
            role=User.Roles.ENGINEER,
            first_name="Secondary",
            last_name="Engineer",
            email="tracker.secondary@example.com",
        )
        self.manager = User.objects.create_user(
            username="tracker-manager",
            password="pass12345",
            role=User.Roles.PM_ESG,
        )
        self.account = Account.objects.create(name="Tracker Account")
        self.request = Request.objects.create(
            requestor=self.requestor,
            account=self.account,
            account_manager="Tracker Requestor",
            product_category="Azure",
            engagement_type=Request.Engagement.SUPPORT,
            priority=Request.Priority.MEDIUM,
            engineer=self.primary,
        )
        request_lifecycle.record_created(self.request.pk, actor=self.requestor)

    def test_manage_page_renders_tracker_and_acknowledge_for_primary(self):
        self.client.force_login(self.primary)
        response = self.client.get(reverse("hub:request-manage-collab", args=[self.request.pk]))

        self.assertContains(response, "Request progress")
        self.assertContains(response, "Current stage: <strong>Assigned</strong>", html=True)
        self.assertContains(response, "Primary Engineer")
        self.assertContains(response, "Acknowledge request")
        self.assertContains(response, 'aria-current="step"')

    def test_acknowledge_endpoint_advances_request_and_opens_email_draft(self):
        self.request.refresh_from_db()
        self.client.force_login(self.primary)
        response = self.client.post(
            reverse("hub:request-lifecycle-acknowledge", args=[self.request.pk]),
            {"assignment_revision": self.request.assignment_revision},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opening Mail Draft")
        self.assertContains(response, "acknowledge%20your%20request")
        self.request.refresh_from_db()
        self.assertEqual(self.request.lifecycle_stage, Request.LifecycleStage.ONGOING)

    def test_acknowledge_button_disables_until_reassignment(self):
        self.request.refresh_from_db()
        self.client.force_login(self.primary)
        first = self.client.post(
            reverse("hub:request-lifecycle-acknowledge", args=[self.request.pk]),
            {"assignment_revision": self.request.assignment_revision},
        )
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "Opening Mail Draft")

        second = self.client.post(
            reverse("hub:request-lifecycle-acknowledge", args=[self.request.pk]),
            {"assignment_revision": self.request.assignment_revision},
            follow=True,
        )
        self.assertContains(second, "This request is no longer awaiting acknowledgement.")

        manage_page = self.client.get(reverse("hub:request-manage-collab", args=[self.request.pk]))
        self.assertContains(manage_page, "Acknowledgement sent")
        self.assertContains(manage_page, 'disabled aria-disabled="true"')

        previous_engineer_id = self.request.engineer_id
        self.request.engineer = self.secondary
        self.request.save()
        request_lifecycle.record_assignment_change(
            self.request.pk,
            previous_engineer_id=previous_engineer_id,
            previous_backup_id=None,
            actor=self.manager,
        )

        self.client.force_login(self.secondary)
        reassigned_page = self.client.get(reverse("hub:request-manage-collab", args=[self.request.pk]))
        self.assertContains(reassigned_page, "Acknowledge request")
        self.assertNotContains(reassigned_page, "Acknowledgement sent")
        self.assertNotContains(reassigned_page, 'disabled aria-disabled="true"')

    def test_requestor_cannot_acknowledge(self):
        self.request.refresh_from_db()
        self.client.force_login(self.requestor)
        self.client.post(
            reverse("hub:request-lifecycle-acknowledge", args=[self.request.pk]),
            {"assignment_revision": self.request.assignment_revision},
        )
        self.request.refresh_from_db()
        self.assertEqual(self.request.lifecycle_stage, Request.LifecycleStage.ASSIGNED)

    def test_manage_page_renders_ongoing_when_field_is_stale(self):
        """Regression for Bucket A: even if `lifecycle_stage` retreats to 'assigned'
        while acknowledged/started events remain, the card must show the engineer as
        already acknowledged/ongoing and must NOT re-enable the acknowledge button."""
        self.request.refresh_from_db()
        request_lifecycle.acknowledge_request(
            self.request.pk,
            actor=self.primary,
            expected_revision=self.request.assignment_revision,
        )
        self.request.refresh_from_db()

        # Simulate the DB drift that originally caused the bug.
        self.request.lifecycle_stage = Request.LifecycleStage.ASSIGNED
        self.request.save(update_fields=["lifecycle_stage", "updated_at"])

        self.client.force_login(self.primary)
        response = self.client.get(reverse("hub:request-manage-collab", args=[self.request.pk]))

        self.assertContains(response, "Current stage: <strong>Ongoing</strong>", html=True)
        self.assertContains(response, "Acknowledgement sent")
        self.assertContains(response, 'disabled aria-disabled="true"')
        self.assertNotContains(response, "Acknowledge request")

    def test_stale_field_does_not_silently_re_enable_ack(self):
        """A stale field must not create a silent path that lets a second ack
        (and its redundant email draft) proceed; the acknowledge endpoint must
        reject with a clear conflict even though the card field reads 'assigned'."""
        self.request.refresh_from_db()
        request_lifecycle.acknowledge_request(
            self.request.pk,
            actor=self.primary,
            expected_revision=self.request.assignment_revision,
        )
        self.request.refresh_from_db()
        self.request.lifecycle_stage = Request.LifecycleStage.ASSIGNED
        self.request.save(update_fields=["lifecycle_stage", "updated_at"])
        self.request.refresh_from_db()

        self.client.force_login(self.primary)
        response = self.client.post(
            reverse("hub:request-lifecycle-acknowledge", args=[self.request.pk]),
            {"assignment_revision": self.request.assignment_revision},
            follow=True,
        )

        # The second ack is rejected (events already present, stage advanced in the
        # event log), the page renders the authoritative state, and the engineer sees
        # clear feedback rather than a silent success that re-opens the draft.
        self.assertContains(response, "This request is no longer awaiting acknowledgement.")
        self.assertContains(response, "Current stage: <strong>Ongoing</strong>", html=True)
