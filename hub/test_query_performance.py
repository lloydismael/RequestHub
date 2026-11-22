"""Rendered-query budgets for the expensive paths identified in the audit."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from hub.models import (
    Account,
    EngineerActivityLog,
    Notification,
    Request,
    RequestLifecycleEvent,
    SqrSubmission,
    SqrSubmissionHistory,
    SqrTrackerSettings,
)
from hub.services.notifications import notify_request_edit


class QueryPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        """Create shared fixtures with active, retired, and missing relation cases."""
        users = {}
        for role in (User.Roles.ADMIN, User.Roles.REQUESTOR, User.Roles.ENGINEER, User.Roles.PM_ESG):
            users[role] = User.objects.create_user(
                username=f"budget-{role}", role=role, first_name="Query", last_name=role.label,
                email=f"{role}@example.invalid", phone_number="5550100", profile_photo="tests/avatar.png",
                profile_completed=True, must_change_password=False, idle_timeout_enabled=False,
            )
        cls.admin = users[User.Roles.ADMIN]
        cls.requestor = users[User.Roles.REQUESTOR]
        cls.engineer = users[User.Roles.ENGINEER]
        cls.pm = users[User.Roles.PM_ESG]
        cls.retired = User.objects.create_user(username="budget-retired", role=User.Roles.ON_HOLD)
        cls.account = Account.objects.create(name="Query budget account")
        today = timezone.localdate()
        cls.request_obj, cls.deployment, cls.late, cls.no_due, cls.invalid, cls.deleted = Request.objects.bulk_create([
            Request(reference_code=f"BUDGET-{i}", account=cls.account, requestor=cls.requestor,
                    engineer=cls.engineer, backup_engineer=cls.retired, description="query budget",
                    product_category="Azure", engagement_type=engagement, status=status,
                    end_date=end, due_date=due, is_deleted=deleted, assignment_revision=1,
                    lifecycle_stage=Request.LifecycleStage.ASSIGNED)
            for i, (engagement, status, end, due, deleted) in enumerate([
                (Request.Engagement.DEPLOYMENT, Request.Status.ONGOING, None, today - timedelta(days=1), False),
                (Request.Engagement.CERTIFICATION, Request.Status.ONGOING, None, today + timedelta(days=1), False),
                (Request.Engagement.SUPPORT, Request.Status.COMPLETED, today, today - timedelta(days=1), False),
                (Request.Engagement.SUPPORT, Request.Status.COMPLETED, today - timedelta(days=1), None, False),
                (Request.Engagement.SUPPORT, Request.Status.COMPLETED, today - timedelta(days=5), today, False),
                (Request.Engagement.SUPPORT, Request.Status.ONGOING, None, today, True),
            ])
        ])
        Request.all_objects.filter(account=cls.account).update(start_date=today - timedelta(days=3))
        RequestLifecycleEvent.objects.bulk_create([
            RequestLifecycleEvent(
                request=cls.request_obj, actor=cls.admin, sequence=1,
                stage=Request.LifecycleStage.ASSIGNED, event_type=RequestLifecycleEvent.EventType.ASSIGNED,
                assignment_revision=1, idempotency_key="budget-assigned",
            ),
        ])
        EngineerActivityLog.objects.bulk_create([
            EngineerActivityLog(request=cls.request_obj, account=cls.account, engineer=cls.engineer,
                                request_date=today, actual_hours=hours, is_billable=billable,
                                activity_type=EngineerActivityLog.ActivityType.INTERNAL_SUPPORT)
            for hours, billable in ((Decimal("2.25"), True), (Decimal("1.50"), False))
        ])
        cls.submission = SqrSubmission.objects.bulk_create([
            SqrSubmission(reference_code="BUDGET-SQR", year=today.year, sequence_number=12345,
                          linked_request=cls.request_obj, engineer=cls.engineer, pm_esg_reviewer=cls.pm,
                          customer_name=cls.account.name, customer_company="ESS", project_title="Query budget")
        ])[0]
        SqrTrackerSettings.objects.update_or_create(pk=1, defaults={"show_classic_sqr_tracker": True})

    def get_page(
        self, query_count: int, user: User, url_name: str, args: tuple[int | str, ...] = (),
        params: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Measure a cold authenticated GET including template rendering."""
        self.client.force_login(user)
        ContentType.objects.clear_cache()
        with self.assertNumQueries(query_count):
            response = self.client.get(reverse(url_name, args=args), params or {})
        self.assertEqual(response.status_code, 200)
        return response

    def create_history(self, size: int) -> None:
        """Repeat references to active, retired, and soft-deleted rows in audit snapshots."""
        SqrSubmissionHistory.objects.bulk_create([
            SqrSubmissionHistory(
                submission=self.submission, actor=self.pm, field="assigned_sse", source="query-test",
                old_values={"assigned_sse": str(self.retired.pk), "linked_request": self.deleted.pk},
                new_values={"assigned_sse": self.engineer.pk, "linked_request": str(self.request_obj.pk)},
            ) for _ in range(size)
        ])

    def test_relationship_history_has_constant_queries(self) -> None:
        """Repeated and missing snapshot references must not fetch one object per change."""
        self.create_history(1)
        small = self.get_page(6, self.admin, "hub:sqr-history", (self.submission.pk,)).json()["entries"]
        self.create_history(69)
        large = self.get_page(6, self.admin, "hub:sqr-history", (self.submission.pk,)).json()["entries"]
        self.assertEqual((len(small), len(large)), (1, 70))
        self.assertEqual(large[0]["changes"], [
            {"field": "assigned_sse", "field_label": "Assigned SSE", "old": self.retired.username,
             "new": "Query Engineer"},
            {"field": "linked_request", "field_label": "RQ ID", "old": str(self.deleted.pk),
             "new": "BUDGET-0"},
        ])

    def test_history_pagination_is_opt_in(self) -> None:
        """Page two should not change the legacy history payload or ordering."""
        self.create_history(52)
        self.client.force_login(self.admin)
        url = reverse("hub:sqr-history", args=[self.submission.pk])
        legacy = self.client.get(url).json()
        page = self.client.get(url, {"page": 2}).json()
        self.assertNotIn("pagination", legacy)
        self.assertEqual(len(legacy["entries"]), 52)
        self.assertEqual(page["entries"], legacy["entries"][50:])
        self.assertEqual(page["pagination"], {"page": 2, "page_size": 50, "total": 52, "has_next": False})

    def test_management_capacity_does_not_query_per_engineer(self) -> None:
        """Keep primary-owner limits, exclusion of the current request, and retired assignments."""
        args = (self.request_obj.pk,)
        page = self.get_page(18, self.admin, "hub:request-manage", args)
        self.assertEqual(page.context["engineer_capacity_map"][str(self.engineer.pk)],
                         {"name": "Query Engineer", "load": 1, "capacity": 3})
        self.assertNotIn(str(self.retired.pk), page.context["engineer_capacity_map"])
        extra = User.objects.bulk_create([
            User(username=f"budget-extra-{i}", role=User.Roles.ENGINEER) for i in range(20)
        ])
        page = self.get_page(18, self.admin, "hub:request-manage", args)
        self.assertEqual(page.context["engineer_capacity_map"][str(extra[0].pk)]["capacity"], 5)

    def test_collaborative_detail_query_budget(self) -> None:
        """The current primary assignee can still act on the lifecycle card."""
        page = self.get_page(19, self.engineer, "hub:request-manage-collab", (self.request_obj.pk,))
        self.assertTrue(page.context["lifecycle"]["can_acknowledge"])
        self.assertEqual(page.context["related_activity_summary"]["billable_hours"], Decimal("2.25"))

    def test_classic_sqr_query_budget(self) -> None:
        """Keep the narrower filing dropdown and the engineer-scoped tracker."""
        page = self.get_page(13, self.engineer, "hub:sqr", params={"view": "tracker"})
        self.assertContains(page, "BUDGET-SQR")
        self.assertContains(page, f"{self.request_obj.reference_code} – {self.account.name}")
        self.assertNotContains(page, self.deleted.reference_code)

    def test_admin_dashboard_search_query_budget(self) -> None:
        """Rendered search results must still match requests and omit deleted rows."""
        page = self.get_page(14, self.admin, "hub:dashboard", params={"q": "query budget"})
        self.assertContains(page, self.request_obj.reference_code)
        self.assertNotContains(page, self.deleted.reference_code)

    def test_request_edit_query_budget(self) -> None:
        """Choice metadata and the current account must remain available after editing-query changes."""
        page = self.get_page(8, self.requestor, "hub:request-edit", (self.request_obj.pk,))
        self.assertContains(page, self.account.name)
        self.assertContains(page, 'data-role-group="project_manager"')
        self.assertContains(page, 'data-avatar="')

    def test_sqr_edit_query_budget(self) -> None:
        """Editing keeps the linked request label with the account name."""
        page = self.get_page(9, self.admin, "hub:sqr-edit", (self.submission.pk,))
        self.assertContains(page, f"{self.request_obj.reference_code} – {self.account.name}")

    def test_operational_report_query_budget(self) -> None:
        """Turnaround ignores invalid dates; on-time rate excludes missing due dates."""
        page = self.get_page(12, self.admin, "hub:report")
        self.assertEqual(page.context["health"], {
            "overdue": 1, "due_soon": 1, "unassigned": 0,
            "on_time_rate": 0, "avg_turnaround_days": 2.5,
        })
        self.assertEqual(page.context["totals"]["requests"], 5)

    def test_people_editor_query_budget(self) -> None:
        """The PM and department directory does not need a business record dropdown."""
        page = self.get_page(4, self.admin, "hub:form-editor-people", ("sqr_submission",))
        fields = page.json()["fields"]
        self.assertIn({"value": str(self.pm.pk), "label": "Query PM-ESG"}, fields["pm_esg_reviewer"]["choices"])
        self.assertIn({"value": "ESS", "label": "ESS"}, fields["customer_contact"]["groups"])

    def test_engineer_activity_report_query_budget(self) -> None:
        """Page counts and hours agree with charts without reading requests per log."""
        page = self.get_page(12, self.engineer, "hub:activity-logs")
        self.assertEqual(page.context["hours_summary"], {
            "total": Decimal("3.75"), "billable": Decimal("2.25"), "non_billable": Decimal("1.50"),
        })
        self.assertEqual(page.context["requests_summary"], {"total": 5, "ongoing": 2, "completed": 3})
        self.assertEqual(page.context["logs_total_count"], 2)

    def test_request_edit_inserts_deduplicated_recipients_in_one_batch(self) -> None:
        """Keep the same participant and management recipients without one insert per user."""
        original = Request.objects.get(pk=self.request_obj.pk)
        updated = Request.objects.select_related("requestor", "engineer", "backup_engineer").get(pk=original.pk)
        updated.description = "Changed request description"
        with self.assertNumQueries(3):
            notify_request_edit(self.admin, original, updated, ["description"], "Query test", include_management=True)
        notifications = Notification.objects.filter(source="Query test")
        management_ids = set(User.objects.filter(role__in=(User.Roles.ADMIN, User.Roles.PM_ESG)).values_list("pk", flat=True))
        expected = (management_ids | {self.requestor.pk, self.engineer.pk, self.retired.pk}) - {self.admin.pk}
        self.assertCountEqual(notifications.values_list("recipient_id", flat=True), expected)
        self.assertEqual(notifications.exclude(message__contains="Description updated").count(), 0)
