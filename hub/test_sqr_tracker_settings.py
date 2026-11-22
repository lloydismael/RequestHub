import json
import re
from unittest.mock import patch

from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from hub.models import SqrTrackerSettings


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SqrTrackerSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='visibility-admin', role=User.Roles.ADMIN)
        cls.pm = User.objects.create_user(username='visibility-pm', role=User.Roles.PM_ESG)
        cls.engineer = User.objects.create_user(username='visibility-engineer', role=User.Roles.ENGINEER)

    def post_setting(self, payload, client=None):
        return (client or self.client).post('/sqr/tracker-settings/', json.dumps(payload), content_type='application/json')

    def test_setting_default_and_persistence_across_users(self):
        self.client.force_login(self.admin)
        response = self.client.get('/sqr/tracker-settings/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['show_classic_sqr_tracker'])
        self.assertEqual(self.post_setting({'show_classic_sqr_tracker': False}).status_code, 200)
        self.client.logout()
        self.client.force_login(self.pm)
        page = self.client.get(reverse('hub:sqr'), {'view': 'tracker'})
        self.assertFalse(SqrTrackerSettings.objects.get(pk=1).show_classic_sqr_tracker)
        self.assertEqual(page.context['active_sqr_view'], 'prototype')
        self.client.force_login(self.admin)
        self.assertFalse(self.client.get('/sqr/tracker-settings/').json()['show_classic_sqr_tracker'])
        self.assertEqual(SqrTrackerSettings.objects.count(), 1)

    def test_only_admin_can_read_or_change_setting(self):
        self.assertEqual(self.post_setting({'show_classic_sqr_tracker': False}).status_code, 302)
        for user in (self.pm, self.engineer):
            self.client.force_login(user)
            self.assertEqual(self.client.get('/sqr/tracker-settings/').status_code, 403)
            self.assertEqual(self.post_setting({'show_classic_sqr_tracker': False}).status_code, 403)
        self.assertFalse(SqrTrackerSettings.objects.exists())

    def test_setting_requires_boolean_and_csrf(self):
        self.client.force_login(self.admin)
        for payload in ({}, {'show_classic_sqr_tracker': 'false'}, {'show_classic_sqr_tracker': 0},
                        {'show_classic_sqr_tracker': None}, []):
            self.assertEqual(self.post_setting(payload).status_code, 400)
        self.assertEqual(self.client.post('/sqr/tracker-settings/', '{', content_type='application/json').status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(self.post_setting({'show_classic_sqr_tracker': False}, csrf_client).status_code, 403)
        self.assertFalse(SqrTrackerSettings.objects.exists())

    def test_failed_save_preserves_previous_state(self):
        SqrTrackerSettings.objects.create(pk=1, show_classic_sqr_tracker=True)
        self.client.force_login(self.admin)
        with patch.object(SqrTrackerSettings, 'save', side_effect=DatabaseError('private database detail')):
            response = self.post_setting({'show_classic_sqr_tracker': False})
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json()['ok'])
        self.assertNotIn('private database detail', response.content.decode())
        self.assertTrue(SqrTrackerSettings.objects.get(pk=1).show_classic_sqr_tracker)

    def test_hidden_classic_bookmark_omits_controls_and_preparation(self):
        SqrTrackerSettings.objects.create(pk=1, show_classic_sqr_tracker=False)
        self.client.force_login(self.pm)
        with patch('hub.services.sqr_queries.tracker_page', side_effect=AssertionError('Classic preparation must be skipped')):
            page = self.client.get(reverse('hub:sqr'), {'view': 'tracker'})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['active_sqr_view'], 'prototype')
        self.assertTrue(page.context['sqr_view_notice'])
        self.assertTrue(page.context['default_my_assigned_filter'])
        for marker in ('data-sqr-view-panel="tracker"', 'aria-label="SQR tracker selector"', 'data-sqr-view-button=',
                       'id="sqr-table-filter-panel"', 'id="sqr-badge-filters"', 'id="sqr-header-search"'):
            self.assertIsNone(re.search(r'<[^>]*\s' + re.escape(marker), page.content.decode()), marker)
        self.assertContains(page, 'data-sqr-view-panel="prototype"')
        self.assertNotIn('proposal_submissions', page.context)

    def test_ppt_opens_unfiltered_for_both_admin_account_types_when_classic_is_hidden(self):
        SqrTrackerSettings.objects.create(pk=1, show_classic_sqr_tracker=False)
        primary_admin = User.objects.create_superuser(username='visibility-primary-admin', role=User.Roles.ADMIN)
        for admin in (self.admin, primary_admin):
            with self.subTest(is_superuser=admin.is_superuser):
                self.client.force_login(admin)
                page = self.client.get(reverse('hub:sqr'), {'view': 'performance'})
                self.assertContains(page, 'data-default-mine="0"')

    def test_primary_and_explicit_authorized_bookmarks(self):
        self.client.force_login(self.admin)
        for params, expected in (({}, 'prototype'), ({'view': 'unknown'}, 'prototype'),
                                 ({'view': 'tracker'}, 'tracker'), ({'view': 'performance'}, 'performance'),
                                 ({'view': 'performance', 'focus': '42'}, 'tracker')):
            page = self.client.get(reverse('hub:sqr'), params)
            self.assertEqual(page.context['active_sqr_view'], expected)
        self.client.force_login(self.engineer)
        self.assertEqual(self.client.get(reverse('hub:sqr'), {'view': 'performance'}).context['active_sqr_view'], 'prototype')

    def test_profile_control_is_global_admin_only(self):
        SqrTrackerSettings.objects.create(pk=1, show_classic_sqr_tracker=False)
        for user, visible in ((self.admin, True), (self.pm, False), (self.engineer, False)):
            self.client.force_login(user)
            page = self.client.get(reverse('accounts:update'))
            if visible:
                self.assertContains(page, 'Show SQR Tracker (Classic)')
                self.assertContains(page, 'id="sqr-classic-visibility"')
                self.assertFalse(page.context['show_classic_sqr_tracker'])
            else:
                self.assertNotContains(page, 'id="sqr-classic-visibility"')
