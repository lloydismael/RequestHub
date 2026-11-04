from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from hub.context_processors import notification_badges
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
        with CaptureQueriesContext(connection) as queries:
            first = notification_badges(request)
            second = notification_badges(request)
        self.assertEqual(first['new_ticket_count'], 1)
        self.assertEqual(first, second)
        self.assertEqual(len(queries), 2)

    def test_separate_users_do_not_share_counts(self):
        self.assertEqual(notification_badges(self.request(self.admin))['new_ticket_count'], 1)
        self.assertEqual(notification_badges(self.request(self.other))['new_ticket_count'], 0)

    def test_unread_count_and_toast_cursor_preserved(self):
        summary = notification_badges(self.request(self.admin))
        self.assertEqual(summary['notification_toast_initial_cursor'], Notification.objects.get(recipient=self.admin).pk)
        Notification.objects.filter(recipient=self.admin).update(is_read=True)
        self.assertEqual(notification_badges(self.request(self.admin))['new_ticket_count'], 0)
