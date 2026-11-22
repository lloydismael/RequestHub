from django.contrib.auth.models import AnonymousUser
from django.core.cache import caches
from django.db import connection, transaction
from django.http import HttpRequest
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts import recovery
from accounts.models import User
from hub.assistant_directory import list_departments
from hub.context_processors import new_ticket_count, notification_badges
from hub.models import Account, Notification, Request, SqrSubmission
from hub.services.notifications import notify_request_edit, notify_sqr_submission


class NotificationPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='badge-admin', role=User.Roles.ADMIN)
        cls.other = User.objects.create(username='badge-other', role=User.Roles.ADMIN)
        Notification.objects.create(recipient=cls.admin, message='Private notification', source='New request',
                                    event_type=Notification.EventType.NEW_REQUEST)

    def request(self, user):
        request = RequestFactory().get('/dashboard/')
        request.user = user
        return request

    def test_badge_summary_reused_within_request(self):
        request = self.request(self.admin)
        with self.assertNumQueries(1):
            unread = new_ticket_count(request)
            first = notification_badges(request)
            second = notification_badges(request)
        self.assertEqual(unread, 1)
        self.assertEqual(first['new_ticket_count'], unread)
        self.assertEqual(first, second)

    def test_unused_badges_and_anonymous_context_do_not_query(self) -> None:
        """A template that does not use the badges does not load a summary."""
        with self.assertNumQueries(0):
            notification_badges(self.request(self.admin))["app_version"]
            anonymous = notification_badges(self.request(AnonymousUser()))
            self.assertEqual(anonymous["new_ticket_count"], 0)
            self.assertEqual(anonymous["notification_toast_initial_cursor"], 0)

    def test_separate_users_do_not_share_counts(self):
        self.assertEqual(notification_badges(self.request(self.admin))['new_ticket_count'], 1)
        self.assertEqual(notification_badges(self.request(self.other))['new_ticket_count'], 0)

    def test_unread_count_and_toast_cursor_preserved(self):
        summary = notification_badges(self.request(self.admin))
        self.assertEqual(summary['notification_toast_initial_cursor'], Notification.objects.get(recipient=self.admin).pk)
        Notification.objects.filter(recipient=self.admin).update(is_read=True)
        self.assertEqual(notification_badges(self.request(self.admin))['new_ticket_count'], 0)

    def test_activity_state_excludes_hidden_events_but_toast_cursor_keeps_them(self):
        visible = Notification.objects.get(recipient=self.admin)
        hidden = Notification.objects.create(
            recipient=self.admin, event_type=Notification.EventType.ASSIGNMENT,
            source='Assignment', message='Not in the admin Activity feed',
        )
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('hub:notifications'))
        self.assertContains(response, f'data-notification-state="{visible.pk}:1:1"')
        self.assertContains(response, f'data-initial-cursor="{hidden.pk}"')
        self.assertNotContains(response, hidden.message)
        notification_queries = [query for query in queries if '"hub_notification"' in query['sql']]
        self.assertLessEqual(len(notification_queries), 3)

    def test_poll_detects_an_update_that_arrives_during_activity_pagination(self):
        self.client.force_login(self.other)
        notification = None

        def insert_after_count(execute, sql, params, many, context):
            nonlocal notification
            result = execute(sql, params, many, context)
            if notification is None and 'COUNT(*) AS "__count"' in sql and '"hub_notification"' in sql:
                notification = Notification.objects.create(
                    recipient=self.other, message='Arrived during pagination', source='New request',
                    event_type=Notification.EventType.NEW_REQUEST,
                )
            return result

        with connection.execute_wrapper(insert_after_count):
            page = self.client.get(reverse('hub:notifications'))
        self.assertIsNotNone(notification)
        self.assertNotContains(page, notification.message)
        poll = self.client.get(reverse('hub:notification-toast-data'), {'cursor': 0}).json()
        self.assertEqual(poll['latest_id'], notification.pk)
        self.assertEqual(poll['unread_count'], 1)
        state = page.context['notification_state']
        self.assertNotEqual(state, f"{poll['latest_id']}:{poll['total_count']}:{poll['unread_count']}")

    def test_changelog_tab_reuses_paginator_count_and_shared_badge(self):
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('hub:notifications'), {'tab': 'changelogs'})
        self.assertContains(response, 'aria-label="1 unread"')
        sql = [query['sql'] for query in queries]
        self.assertEqual(sum('"hub_notification"' in query for query in sql), 1)
        self.assertEqual(sum('COUNT(' in query and '"hub_changelog"' in query for query in sql), 1)


@override_settings(CACHES={
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "default"},
    "shared_lookups": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "phase5-tests"},
})
class SharedLookupCacheTests(TransactionTestCase):
    def setUp(self) -> None:
        """Use a separate cache and real database commits for invalidation tests."""
        self.cache = caches["shared_lookups"]
        self.cache.clear()
        self.addCleanup(self.cache.clear)
        self.admin = User.objects.create(username="cache-admin", role=User.Roles.ADMIN)
        self.engineer = User.objects.create(
            username="cache-engineer", role=User.Roles.ENGINEER, profile_completed=True,
            must_change_password=False, idle_timeout_enabled=False,
        )
        self.factory = RequestFactory()

    def badge_request(self, user: User) -> HttpRequest:
        """Return a fresh request, so a hit cannot come from request-local state."""
        request = self.factory.get("/dashboard/")
        request.user = user
        return request

    def badge(self, user: User) -> tuple[int, int]:
        """Evaluate both rendered values, not merely construct their lazy proxies."""
        context = notification_badges(self.badge_request(user))
        return int(context["new_ticket_count"]), int(context["notification_toast_initial_cursor"])

    def test_warm_badge_is_scoped_to_user_and_role(self) -> None:
        """Warm hits skip SQL, but a different role sees its own Activity scope."""
        event = self.engineer.notifications.create(event_type=Notification.EventType.ASSIGNMENT)
        with self.assertNumQueries(1):
            self.assertEqual(self.badge(self.engineer), (1, event.pk))
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.engineer), (1, event.pk))
        self.assertEqual(self.badge(self.admin), (0, 0))
        self.engineer.role = User.Roles.ADMIN
        self.assertEqual(self.badge(self.engineer), (0, event.pk))

    def test_committed_create_read_move_and_delete_refresh_affected_users(self) -> None:
        """Refresh old and new recipients, without evicting other users' badges."""
        self.assertEqual(self.badge(self.admin), (0, 0))
        self.assertEqual(self.badge(self.engineer), (0, 0))
        event = self.engineer.notifications.create(event_type=Notification.EventType.MENTION)
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.admin), (0, 0))
        with self.assertNumQueries(1):
            self.assertEqual(self.badge(self.engineer), (1, event.pk))
        event.mark_read()
        with self.assertNumQueries(1):
            self.assertEqual(self.badge(self.engineer), (0, event.pk))
        event.recipient = self.admin
        event.is_read = False
        event.save(update_fields=["recipient", "is_read"])
        self.assertEqual(self.badge(self.engineer), (0, 0))
        self.assertEqual(self.badge(self.admin), (1, event.pk))
        Notification.objects.filter(pk=event.pk).delete()
        self.assertEqual(self.badge(self.admin), (0, 0))

    def test_partial_read_save_invalidates_the_persisted_recipient(self) -> None:
        """An in-memory recipient change must not affect invalidation on a partial save."""
        event = self.engineer.notifications.create(event_type=Notification.EventType.MENTION)
        self.assertEqual(self.badge(self.engineer), (1, event.pk))
        self.assertEqual(self.badge(self.admin), (0, 0))
        event.recipient = self.admin
        event.mark_read()
        self.assertEqual(self.badge(self.engineer), (0, event.pk))
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.admin), (0, 0))

    def test_direct_delete_with_deferred_fields_refreshes_the_badge(self) -> None:
        """Deleting an ID-only instance must not try to read a deleted row."""
        event = self.engineer.notifications.create(event_type=Notification.EventType.MENTION)
        self.assertEqual(self.badge(self.engineer), (1, event.pk))
        Notification.objects.only("pk").get(pk=event.pk).delete()
        self.assertEqual(self.badge(self.engineer), (0, 0))

    def test_rollback_cannot_publish_or_evict_a_committed_badge(self) -> None:
        """Read uncommitted state only locally, without replacing the warm value."""
        self.assertEqual(self.badge(self.engineer), (0, 0))
        with transaction.atomic():
            event = self.engineer.notifications.create(event_type=Notification.EventType.MENTION)
            self.assertEqual(self.badge(self.engineer), (1, event.pk))
            transaction.set_rollback(True)
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.engineer), (0, 0))

    def test_bulk_read_and_creation_invalidate_existing_badges(self) -> None:
        """Both notification writers bypass save signals, as does mark-all-read."""
        account = Account.objects.create(name="Cache fixture")
        original = Request.objects.create(
            requestor=self.engineer, account=account, description="Original",
            account_manager="Fixture Manager", product_category="Azure", engagement_type=Request.Engagement.SUPPORT,
        )
        updated = Request.objects.get(pk=original.pk)
        updated.description = "Updated"
        self.assertEqual(self.badge(self.engineer), (0, 0))
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.engineer), (0, 0))
        with transaction.atomic():
            notify_request_edit(self.admin, original, updated, ["description"], "Edit")
        with self.assertNumQueries(1):
            self.assertEqual(self.badge(self.engineer), (1, 0))
        self.client.force_login(self.engineer)
        response = self.client.post(reverse("hub:notification-mark-all-read"))
        self.assertEqual(response.status_code, 302)
        with self.assertNumQueries(1):
            self.assertEqual(self.badge(self.engineer), (0, 0))

        reviewer = User.objects.create(username="cache-reviewer", role=User.Roles.ENGINEER)
        self.assertEqual(self.badge(reviewer), (0, 0))
        notify_sqr_submission(SqrSubmission(engineer=self.engineer, pm_esg_reviewer=reviewer))
        with self.assertNumQueries(1):
            self.assertEqual(self.badge(reviewer), (1, 0))

    def test_late_cache_fill_cannot_hide_a_new_notification(self) -> None:
        """A summary read before a committed write must not become the warm value."""
        event = None

        def insert_after_summary(execute, sql, params, many, context):
            nonlocal event
            result = execute(sql, params, many, context)
            if event is None and 'AS "toast_cursor"' in sql:
                event = self.engineer.notifications.create(event_type=Notification.EventType.MENTION)
            return result

        with connection.execute_wrapper(insert_after_summary):
            self.assertEqual(self.badge(self.engineer), (0, 0))
        self.assertIsNotNone(event)
        self.assertEqual(self.badge(self.engineer), (1, event.pk))
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.engineer), (1, event.pk))

    def test_cascading_request_delete_invalidates_the_recipient_badge(self) -> None:
        """Deleting a request through the ORM removes its notifications too."""
        item = Request.objects.create(
            requestor=self.admin, account=Account.objects.create(name="Cache cascade"),
            description="Request to remove", account_manager="Fixture Manager",
            product_category="Azure", engagement_type=Request.Engagement.SUPPORT,
        )
        event = self.engineer.notifications.create(
            related_request=item, event_type=Notification.EventType.MENTION,
        )
        self.assertEqual(self.badge(self.engineer), (1, event.pk))
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.engineer), (1, event.pk))
        Request.all_objects.filter(pk=item.pk).delete()
        self.assertEqual(self.badge(self.engineer), (0, 0))

    def test_activity_and_poll_ignore_a_stale_badge(self) -> None:
        """Live notification state must not depend on a warm navigation summary."""
        event = self.engineer.notifications.create(event_type=Notification.EventType.MENTION)
        self.assertEqual(self.badge(self.engineer), (1, event.pk))
        # Simulate a write whose cache invalidation could not reach Redis.
        self.engineer.notifications.update(is_read=True)
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.engineer), (1, event.pk))
        self.client.force_login(self.engineer)
        page = self.client.get(reverse("hub:notifications"))
        self.assertContains(page, f'data-notification-state="{event.pk}:1:0"')
        self.assertEqual(page.context["new_ticket_count"], 0)
        poll = self.client.get(reverse("hub:notification-toast-data"), {"cursor": 0}).json()
        self.assertEqual((poll["unread_count"], poll["latest_id"]), (0, event.pk))

    def test_restore_invalidates_warm_badges_without_save_signals(self) -> None:
        """Recovery's raw replacement also removes an old notification baseline."""
        snapshot = recovery.build_payload()
        event = self.engineer.notifications.create(event_type=Notification.EventType.MENTION)
        self.assertEqual(self.badge(self.engineer), (1, event.pk))
        with self.assertNumQueries(0):
            self.assertEqual(self.badge(self.engineer), (1, event.pk))
        preview, _ = recovery.create_preview(self.admin, snapshot)
        recovery.restore_preview(self.admin, preview.token)
        with self.assertNumQueries(1):
            self.assertEqual(self.badge(self.engineer), (0, 0))

    def test_department_counts_are_cached_but_follow_membership_changes(self) -> None:
        """Aggregate rows refresh when an active user moves or is deactivated."""
        self.engineer.department = "Test Operations"
        self.engineer.save(update_fields=["department"])
        entries, total = list_departments(self.admin, query="Test Operations")
        self.assertEqual((total, entries[0].member_count), (1, 1))
        with self.assertNumQueries(0):
            self.assertEqual(list_departments(self.admin, query="Test Operations"), (entries, total))
        self.engineer.department = "Test Development"
        self.engineer.save(update_fields=["department"])
        self.assertEqual(list_departments(self.admin, query="Test Operations"), ((), 0))
        self.assertEqual(list_departments(self.admin, query="Test Development")[1], 1)
        self.engineer.is_active = False
        self.engineer.save(update_fields=["is_active"])
        self.assertEqual(list_departments(self.admin, query="Test Development"), ((), 0))
