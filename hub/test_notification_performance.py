from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User
from hub.context_processors import new_ticket_count, notification_badges
from hub.models import Notification


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
