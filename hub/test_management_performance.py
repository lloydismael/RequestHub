import os
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest import skipUnless
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.forms import UserManagementForm
from accounts.models import User

from .form_editor import baseline_schema, get_custom_answers, publish, save_draft
from .models import Account, FormAnswerSet, FormDefinition
from .views import UserManagementView


class ManagementEditorPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="management-perf-admin", role=User.Roles.ADMIN, must_change_password=False,
        )
        users = User.objects.bulk_create([
            User(username=f"management-perf-user-{index:02d}", role=User.Roles.REQUESTOR)
            for index in range(49)
        ])
        cls.saved_user = users[0]
        cls.other_user = users[1]
        cls.schema = baseline_schema("user_management")
        cls.schema["fields"].append({
            "key": "custom_notes", "type": "text", "label": "Management notes", "required": True,
        })
        draft = save_draft("user_management", cls.schema, cls.admin, 0)
        cls.definition = publish("user_management", cls.admin, draft.revision)
        content_type = ContentType.objects.get_for_model(User)
        for user, notes in ((cls.saved_user, "Saved management notes"), (cls.other_user, "Other user's notes")):
            FormAnswerSet.objects.create(
                definition=cls.definition, content_type=content_type, object_id=user.pk,
                schema_version=cls.definition.published_version, answers={"custom_notes": notes},
            )
        profile = FormDefinition.objects.create(key="profile")
        FormAnswerSet.objects.create(
            definition=profile, content_type=content_type, object_id=cls.saved_user.pk,
            answers={"custom_notes": "Unrelated profile notes"},
        )

    def test_form_configuration_reads_one_definition_snapshot(self):
        with self.assertNumQueries(1):
            form = UserManagementForm()
        self.assertEqual(form.fields["custom_notes"].label, "Management notes")
        self.assertEqual(form.editor_version.pk, self.definition.published_version_id)

    def test_user_editor_queries_stay_bounded_for_one_or_fifty_users(self):
        editor_tables = (FormDefinition._meta.db_table, FormAnswerSet._meta.db_table)
        for params, expected_rows in (({"user_q": self.admin.username}, 1), ({}, 50)):
            with self.subTest(rows=expected_rows):
                request = RequestFactory().get(reverse("hub:management"), params)
                request.user = self.admin
                with CaptureQueriesContext(connection) as queries:
                    response = UserManagementView.as_view()(request)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="mgmt-row align-middle"', count=expected_rows)
                editor_queries = [
                    query["sql"] for query in queries
                    if any(table in query["sql"].lower() for table in editor_tables)
                ]
                self.assertLessEqual(len(editor_queries), 3, "User editor queries must not grow per row")

    def test_user_editor_answers_are_scoped_and_published_changes_are_fresh(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("hub:management"))
        forms = {user.pk: user.editor_form for user in response.context["users"]}
        self.assertEqual(forms[self.saved_user.pk].initial["custom_notes"], "Saved management notes")
        self.assertEqual(forms[self.other_user.pk].initial["custom_notes"], "Other user's notes")
        self.assertIsNone(forms[self.admin.pk].initial["custom_notes"])
        self.assertEqual(forms[self.saved_user.pk].fields["custom_notes"].label, "Management notes")
        self.assertNotContains(response, "Unrelated profile notes")

        schema = deepcopy(self.schema)
        schema["fields"][-1]["label"] = "Updated management notes"
        draft = save_draft("user_management", schema, self.admin, self.definition.revision)
        definition = publish("user_management", self.admin, draft.revision)
        response = self.client.get(reverse("hub:management"), {"user_q": self.saved_user.username})
        form = response.context["users"][0].editor_form
        self.assertEqual(form.fields["custom_notes"].label, "Updated management notes")
        self.assertEqual(form.initial["custom_notes"], "Saved management notes")
        self.assertEqual(form.editor_version.pk, definition.published_version_id)

    def test_each_management_page_only_renders_its_selected_pane(self):
        self.client.force_login(self.admin)
        tabs = ("users", "accounts", "activity", "forms", "changelogs", "monitoring")
        url = reverse("hub:management")
        for selected in tabs:
            with self.subTest(tab=selected):
                response = self.client.get(url, {"tab": selected})
                self.assertContains(response, f'id="{selected}-pane"')
                for other in tabs:
                    if other != selected:
                        self.assertNotContains(response, f'id="{other}-pane"')
                    self.assertContains(response, f'href="{url}?tab={other}"')

    def test_user_search_fragment_keeps_editor_fields_without_the_page_shell(self):
        self.client.force_login(self.admin)
        url = reverse("hub:management")
        params = {"user_q": self.saved_user.username}
        fragment = self.client.get(url, params, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertContains(fragment, 'id="users-tbody"')
        self.assertContains(fragment, "Saved management notes")
        self.assertContains(fragment, 'name="_form_version"')
        self.assertNotContains(fragment, self.other_user.username)
        self.assertNotContains(fragment, 'id="create-user-modal"')
        self.assertNotContains(fragment, 'id="user-search"')
        self.assertIn("X-Requested-With", fragment.headers["Vary"])
        full_page = self.client.get(url, params)
        self.assertContains(full_page, 'id="user-search"')
        self.assertIn("X-Requested-With", full_page.headers["Vary"])

    def test_accounts_get_does_not_query_user_editors_or_sign_in_logs(self):
        Account.objects.create(name="Existing account")
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("hub:management"), {"tab": "accounts"})
        self.assertContains(response, 'value="Existing account"')
        for query in queries:
            sql = query["sql"].lower()
            self.assertNotIn(FormAnswerSet._meta.db_table, sql)
            self.assertNotIn("accounts_signinlog", sql)

    def test_accounts_only_render_one_page_and_batch_editor_queries(self):
        accounts = Account.objects.bulk_create([Account(name=f"Demo {i:03}") for i in range(127)])
        summit = Account.objects.create(name="Summit Research")
        schema = baseline_schema("account_management")
        schema["fields"].append({"key": "custom_notes", "type": "text", "label": "Account notes"})
        draft = save_draft("account_management", schema, self.admin, 0)
        definition = publish("account_management", self.admin, draft.revision)
        content_type = ContentType.objects.get_for_model(Account)
        for account, note in ((accounts[0], "First page answer"), (summit, "Last page answer")):
            FormAnswerSet.objects.create(
                definition=definition, content_type=content_type, object_id=account.pk,
                schema_version=definition.published_version, answers={"custom_notes": note},
            )

        self.client.force_login(self.admin)
        url = reverse("hub:management")
        editor_tables = (FormDefinition._meta.db_table, FormAnswerSet._meta.db_table)
        with CaptureQueriesContext(connection) as queries:
            page = self.client.get(url, {"tab": "accounts"})
        self.assertContains(page, 'class="mgmt-row align-middle"', count=50)
        editor_queries = [q for q in queries if any(table in q["sql"].lower() for table in editor_tables)]
        self.assertEqual(len(editor_queries), 2)
        self.assertContains(page, "First page answer")
        self.assertNotContains(page, "Summit Research")
        self.assertNotContains(page, "Last page answer")
        self.assertTrue(any('LIMIT 50' in q['sql'] and 'hub_account' in q['sql'] for q in queries))
        account_counts = [q for q in queries if 'COUNT(' in q['sql'] and 'FROM "hub_account"' in q['sql']]
        self.assertEqual(len(account_counts), 1, 'An unfiltered page should reuse its directory count')

        fragment = self.client.get(url, {"tab": "accounts", "account_page": 3},
                                   headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertContains(fragment, 'class="mgmt-row align-middle"', count=28)
        self.assertContains(fragment, 'value="Summit Research"')
        self.assertContains(fragment, "Last page answer")
        self.assertNotContains(fragment, 'id="accounts-pane"')
        self.assertIn("no-store", fragment.headers["Cache-Control"])
        self.assertIn("X-Requested-With", page.headers["Vary"])

        search = self.client.get(url, {"tab": "accounts", "account_q": " summit RESEARCH "})
        self.assertContains(search, 'class="mgmt-row align-middle"', count=1)
        self.assertContains(search, 'value="Summit Research"')

    def test_accounts_full_page_form_posts_without_javascript(self):
        account = Account.objects.create(name='Summit Research')
        Account.objects.create(name='Not in filtered page')
        self.client.force_login(self.admin)
        url = reverse('hub:management') + '?tab=accounts&account_q=Summit'
        response = self.client.get(url)
        formset = response.context['account_formset']
        management = formset.management_form
        edit_form, new_form = formset.forms
        for field in (edit_form['id'], edit_form['name'], new_form['name'], *management):
            self.assertContains(response, f'name="{field.html_name}"')
        data = {field.html_name: field.value() for field in management}
        data.update({
            'active_tab': 'accounts', edit_form['id'].html_name: account.pk,
            edit_form['name'].html_name: 'Summit Updated', new_form['name'].html_name: 'Summit Added',
        })
        self.assertRedirects(self.client.post(url, data), url)
        self.assertTrue(Account.objects.filter(pk=account.pk, name='Summit Updated').exists())
        self.assertTrue(Account.objects.filter(name='Summit Added').exists())

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=28)
    def test_retired_choices_can_make_a_page_too_large_for_a_native_save(self):
        account = Account.objects.create(name='Summit Research')
        schema = baseline_schema('account_management')
        labels = [f'label-{index}' for index in range(19)]
        field = {'key': 'custom_labels', 'type': 'multiselect', 'label': 'Labels',
                 'choices': [{'value': label, 'label': label} for label in labels]}
        schema['fields'].append(field)
        draft = save_draft('account_management', schema, self.admin, 0)
        definition = publish('account_management', self.admin, draft.revision)
        FormAnswerSet.objects.create(
            definition=definition, content_type=ContentType.objects.get_for_model(Account),
            object_id=account.pk, schema_version=definition.published_version, answers={'custom_labels': labels},
        )
        field['choices'] = field['choices'][:1]
        draft = save_draft('account_management', schema, self.admin, definition.revision)
        publish('account_management', self.admin, draft.revision)
        self.client.force_login(self.admin)

        response = self.client.get(reverse('hub:management'), {'tab': 'accounts'})

        self.assertContains(response, 'label-18 (retired)')
        self.assertContains(response, 'This page has too many fields to save without JavaScript.')
        save_button = response.content.decode().split('id="accounts-save-btn"', 1)[1].split('>', 1)[0]
        self.assertIn('disabled', save_button)

    def test_protected_delete_rejects_the_entire_account_batch(self):
        from .models import Request

        account = Account.objects.create(name="Still in use")
        other = Account.objects.create(name="Other account")
        Request.objects.create(account=account, requestor=self.admin, account_manager="Demo manager",
                               product_category="Azure", engagement_type=Request.Engagement.SUPPORT)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("hub:management") + "?tab=accounts", {
            "active_tab": "accounts", "form-TOTAL_FORMS": "3", "form-INITIAL_FORMS": "2",
            "form-0-id": other.pk, "form-0-name": "Edited account",
            "form-1-id": account.pk, "form-1-name": account.name, "form-1-DELETE": "on",
            "form-2-name": "New account not saved",
        })
        self.assertContains(response, "This account is in use and cannot be deleted.")
        self.assertContains(response, 'value="Edited account"')
        self.assertContains(response, 'value="New account not saved"')
        self.assertTrue(Account.objects.filter(pk=other.pk, name="Other account").exists())
        self.assertFalse(Account.objects.filter(name="New account not saved").exists())

    def test_account_batch_checks_names_outside_the_page_and_within_the_batch(self):
        first = Account.objects.create(name="First account")
        Account.objects.create(name="Summit Research")
        self.client.force_login(self.admin)
        url = reverse("hub:management") + "?tab=accounts&account_q=First"
        for name, new_name, error in (
            ("Summit Research", "Unused name", "Account with this Name already exists."),
            ("Shared name", "Shared name", "Please correct the duplicate values below."),
        ):
            with self.subTest(name=name):
                response = self.client.post(url, {
                    "active_tab": "accounts", "form-TOTAL_FORMS": 2, "form-INITIAL_FORMS": 1,
                    "form-0-id": first.pk, "form-0-name": name, "form-1-name": new_name,
                })
                self.assertContains(response, error)
                self.assertTrue(Account.objects.filter(pk=first.pk, name="First account").exists())
                self.assertFalse(Account.objects.filter(name=new_name).exists())

    def test_stale_account_identity_cannot_turn_an_edit_into_a_new_account(self):
        account = Account.objects.create(name='Removed account')
        removed_pk = account.pk
        account.delete()
        self.client.force_login(self.admin)
        response = self.client.post(reverse('hub:management') + '?tab=accounts', {
            'active_tab': 'accounts', 'form-TOTAL_FORMS': 2, 'form-INITIAL_FORMS': 1,
            'form-0-id': removed_pk, 'form-0-name': 'Stale edit', 'form-0-DELETE': 'on',
            'form-1-name': 'Unrelated addition',
        })
        self.assertContains(response, 'This account is no longer available. Discard the edits and reload.')
        self.assertFalse(Account.objects.filter(name__in=['Stale edit', 'Unrelated addition']).exists())

    def test_account_batch_rejects_duplicate_row_ids_even_when_one_is_marked_for_deletion(self):
        account = Account.objects.create(name="Original name")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("hub:management") + "?tab=accounts", {
            "active_tab": "accounts", "form-TOTAL_FORMS": 2, "form-INITIAL_FORMS": 2,
            "form-0-id": account.pk, "form-0-name": "Edited name",
            "form-1-id": account.pk, "form-1-name": account.name, "form-1-DELETE": "on",
        })
        self.assertContains(response, "An account can only be submitted once per batch.")
        self.assertTrue(Account.objects.filter(pk=account.pk, name="Original name").exists())

    def test_invalid_account_post_retains_other_edits_in_accounts_pane(self):
        self.client.force_login(self.admin)
        url = reverse("hub:management")
        account = Account.objects.create(name="Existing account")
        response = self.client.post(url, {
            "active_tab": "accounts",
            "form-TOTAL_FORMS": "2",
            "form-INITIAL_FORMS": "1",
            "form-0-id": account.pk,
            "form-0-name": "",
            "form-1-name": "Unsaved new account",
        })
        self.assertContains(response, 'id="accounts-pane"')
        self.assertContains(response, 'value="Unsaved new account"')
        self.assertTrue(response.context["account_formset"][0].errors["name"])
        save_button = response.content.decode().split('id="accounts-save-btn"', 1)[1].split('>', 1)[0]
        self.assertNotIn('display:none', save_button)
        self.assertNotContains(response, 'id="users-pane"')
        account.refresh_from_db()
        self.assertEqual(account.name, "Existing account")

    def test_invalid_user_creation_keeps_bound_modal_and_working_manage_rows(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("hub:management"), {
            "active_tab": "users", "create_user_submit": "1", "create_user-username": "retry-create-user",
        })
        self.assertContains(response, 'value="retry-create-user"')
        self.assertContains(response, 'data-open-on-load="true"')
        self.assertContains(response, f'data-user-pk="{self.saved_user.pk}"')
        self.assertNotContains(response, 'id="accounts-pane"')
        self.assertFalse(User.objects.filter(username="retry-create-user").exists())


@skipUnless(os.environ.get('RUN_MOBILE_BROWSER_TESTS') == '1', 'Requires Playwright/Chromium')
@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ManagementEditorBrowserTests(StaticLiveServerTestCase):
    @contextmanager
    def browser_page(self, user, *, javascript=True):
        from playwright.sync_api import sync_playwright

        self.client.force_login(user)
        asset_dir = Path(os.environ['MOBILE_BOOTSTRAP_CSS']).parent
        chart = os.environ.get('MOBILE_CHART_JS')
        asset_dirs = (asset_dir, Path(chart).parent) if chart else (asset_dir,)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport={'width': 375, 'height': 844}, reduced_motion='reduce',
                                              java_script_enabled=javascript)
                session = self.client.cookies[settings.SESSION_COOKIE_NAME]
                context.add_cookies([{'name': session.key, 'value': session.value, 'url': self.live_server_url}])

                def route_request(route):
                    url = urlsplit(route.request.url)
                    if url.netloc == urlsplit(self.live_server_url).netloc:
                        route.continue_()
                    elif url.hostname == 'cdn.jsdelivr.net':
                        name = url.path.rsplit('/', 1)[-1]
                        asset = next((directory / name for directory in asset_dirs
                                      if (directory / name).is_file()), None)
                        if asset:
                            route.fulfill(path=asset)
                        else:
                            route.abort()
                    else:
                        route.abort()

                context.route('**/*', route_request)
                page = context.new_page()
                page.set_default_timeout(4000)
                yield page
            finally:
                browser.close()

    def test_cross_page_drafts_and_custom_answers_survive_invalid_batch(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='account-browser-admin', role=User.Roles.ADMIN, must_change_password=False)
        Account.objects.bulk_create([Account(name=f'Demo {index:03}') for index in range(101)])
        summit = Account.objects.create(name='Summit Research')
        schema = baseline_schema('account_management')
        schema['fields'] += [
            {'key': 'custom_tags', 'type': 'multiselect', 'label': 'Tags', 'choices': [
                {'value': 'review', 'label': 'Review'}, {'value': 'follow-up', 'label': 'Follow up'},
            ]},
            {'key': 'custom_notes', 'type': 'text', 'label': 'Review notes', 'required': True,
             'condition': {'field': 'custom_tags', 'operator': 'equals', 'value': 'review'}},
        ]
        draft = save_draft('account_management', schema, admin, 0)
        definition = publish('account_management', admin, draft.revision)
        FormAnswerSet.objects.create(
            definition=definition, content_type=ContentType.objects.get_for_model(Account),
            object_id=summit.pk, schema_version=definition.published_version,
            answers={'custom_notes': 'Previously checked'},
        )
        with self.browser_page(admin) as page:
            page.goto(self.live_server_url + reverse('hub:management') + '?tab=accounts&account_q=Summit')
            rows = page.locator('#accounts-tbody tr.mgmt-row')
            expect(rows).to_have_count(1)
            new_row = page.locator('#account-new-row')
            new_row.get_by_label('New account name').fill('New directory entry')
            new_row.get_by_label('Tags').select_option('review')
            new_notes = new_row.locator('input[name$="-custom_notes"]')
            expect(new_notes).to_be_visible()
            new_notes.fill('New account review')

            search = page.get_by_role('searchbox', name='Search accounts')
            search.clear()
            expect(rows).to_have_count(50)
            expect(new_notes).to_be_visible()
            new_row.get_by_text('Review notes', exact=True).click()
            expect(new_notes).to_be_focused()
            rows.first.locator('input[name$="-name"]').fill('Renamed Demo')
            page.get_by_role('link', name='Next page').click()
            expect(rows.first.locator('input[name$="-name"]')).to_have_value('Demo 050')
            page.get_by_role('link', name='Previous page').click()
            expect(rows.first.locator('input[name$="-name"]')).to_have_value('Renamed Demo')
            search.fill('Summit Research')
            expect(rows).to_have_count(1)
            summit_row = rows.first
            expect(summit_row.locator('input[name$="-name"]')).to_have_value('Summit Research')
            expect(summit_row.get_by_label('Review notes')).to_be_hidden()
            summit_row.get_by_label('Tags').select_option(['review', 'follow-up'])
            expect(summit_row.get_by_label('Review notes')).to_be_visible()
            summit_row.get_by_label('Review notes').clear()
            search.clear()
            expect(rows).to_have_count(50)
            expect(rows.first.locator('input[name$="-name"]')).to_have_value('Renamed Demo')
            page.get_by_role('button', name='Save Changes').click()

            summary = page.locator('#accounts-errors')
            expect(summary).to_be_focused()
            expect(new_row.get_by_label('New account name')).to_have_value('New directory entry')
            expect(new_notes).to_have_value('New account review')
            expect(rows.nth(0).locator('input[name$="-name"]')).to_have_value('Renamed Demo')
            expect(rows.nth(1).locator('input[name$="-name"]')).to_have_value('Summit Research')
            summary.get_by_role('link', name='Review notes', exact=False).click()
            notes = rows.nth(1).get_by_label('Review notes')
            expect(notes).to_be_focused()
            notes.fill('Checked both tags')
            page.get_by_role('button', name='Save Changes').click()
            expect(page.get_by_text('Created 1 account, updated 2 accounts.')).to_be_visible()

        self.assertTrue(Account.objects.filter(name='Renamed Demo').exists())
        new_account = Account.objects.get(name='New directory entry')
        self.assertEqual(get_custom_answers('account_management', new_account), {
            'custom_tags': ['review'], 'custom_notes': 'New account review',
        })
        self.assertEqual(get_custom_answers('account_management', summit), {
            'custom_tags': ['review', 'follow-up'], 'custom_notes': 'Checked both tags',
        })

    def test_revisited_draft_preserves_custom_answer_after_editor_reset(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='account-browser-admin', role=User.Roles.ADMIN, must_change_password=False)
        account = Account.objects.create(name='Summit Research')
        schema = baseline_schema('account_management')
        schema['fields'].append({'key': 'custom_notes', 'type': 'text', 'label': 'Account notes'})
        draft = save_draft('account_management', schema, admin, 0)
        definition = publish('account_management', admin, draft.revision)
        FormAnswerSet.objects.create(
            definition=definition, content_type=ContentType.objects.get_for_model(Account),
            object_id=account.pk, schema_version=definition.published_version,
            answers={'custom_notes': 'Saved note'},
        )
        url = self.live_server_url + reverse('hub:management') + '?tab=accounts'
        with self.browser_page(admin) as page:
            page.goto(url)
            row = page.locator('#accounts-tbody tr.mgmt-row')
            row.get_by_label('Account notes').fill('Edited before reset')
            search = page.get_by_role('searchbox', name='Search accounts')
            search.fill('No results')
            expect(row).to_have_count(0)

            reset_url = self.live_server_url + reverse('hub:form-editor-reset', args=['account_management'])
            reset = page.request.post(reset_url, data={'expected_revision': definition.revision}, headers={
                'X-CSRFToken': page.locator('#accounts-edit-form [name=csrfmiddlewaretoken]').input_value(),
            })
            self.assertTrue(reset.ok, reset.text())
            search.fill('Summit')
            expect(row).to_have_count(1)
            expect(row.get_by_label('Account notes')).to_have_value('Edited before reset')
            row.locator('input[name$="-name"]').fill('Summit Renamed')
            page.get_by_role('button', name='Save Changes').click()
            expect(page.get_by_text('Updated 1 account.')).to_be_visible()

        self.assertEqual(Account.objects.get(pk=account.pk).name, 'Summit Renamed')
        self.assertEqual(get_custom_answers('account_management', account), {'custom_notes': 'Edited before reset'})

    def test_reordered_custom_controls_do_not_submit_untouched_rows(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='account-browser-admin', role=User.Roles.ADMIN, must_change_password=False)
        account = Account.objects.create(name='Summit Research')
        schema = baseline_schema('account_management')
        schema['fields'].insert(0, {'key': 'custom_labels', 'type': 'multiselect', 'label': 'Labels', 'choices': [
            {'value': 'first', 'label': 'First'}, {'value': 'second', 'label': 'Second'},
        ]})
        draft = save_draft('account_management', schema, admin, 0)
        definition = publish('account_management', admin, draft.revision)
        FormAnswerSet.objects.create(
            definition=definition, content_type=ContentType.objects.get_for_model(Account),
            object_id=account.pk, schema_version=definition.published_version,
            answers={'custom_labels': ['first', 'second']},
        )
        with self.browser_page(admin) as page:
            page.goto(self.live_server_url + reverse('hub:management') + '?tab=accounts')
            new_row = page.locator('#account-new-row')
            new_row.get_by_label('New account name').fill('New directory entry')
            with page.expect_request(lambda req: req.method == 'POST') as submission:
                page.get_by_role('button', name='Save Changes').click()
            from urllib.parse import parse_qs

            data = parse_qs(submission.value.post_data)
            self.assertEqual(data.get('form-INITIAL_FORMS'), ['0'])
            expect(page.get_by_text('Created 1 account.')).to_be_visible()

    def test_native_account_page_submits_many_multiselect_values(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='account-browser-admin', role=User.Roles.ADMIN, must_change_password=False)
        schema = baseline_schema('account_management')
        labels = [f'label-{i}' for i in range(20)]
        schema['fields'].append({'key': 'custom_labels', 'type': 'multiselect', 'label': 'Labels',
                                 'choices': [{'value': value, 'label': value} for value in labels]})
        draft = save_draft('account_management', schema, admin, 0)
        definition = publish('account_management', admin, draft.revision)
        accounts = Account.objects.bulk_create([Account(name=f'Budget {i:02}') for i in range(50)])
        content_type = ContentType.objects.get_for_model(Account)
        FormAnswerSet.objects.bulk_create([
            FormAnswerSet(definition=definition, content_type=content_type, object_id=account.pk,
                          schema_version=definition.published_version, answers={'custom_labels': labels})
            for account in accounts
        ])
        with self.browser_page(admin, javascript=False) as page:
            page.goto(self.live_server_url + reverse('hub:management') + '?tab=accounts&account_q=Budget')
            page.locator('#accounts-tbody').get_by_label('Account Name for Budget 00', exact=True).fill('Budget 00 edited')
            with page.expect_response(lambda res: res.request.method == 'POST') as submission:
                page.get_by_role('button', name='Save Changes').click()
            self.assertEqual(submission.value.status, 302)
            expect(page.get_by_text('Updated 1 account.')).to_be_visible()

        self.assertEqual(Account.objects.get(pk=accounts[0].pk).name, 'Budget 00 edited')
        self.assertEqual(get_custom_answers('account_management', accounts[0]), {'custom_labels': labels})

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=28)
    def test_oversized_cross_page_batch_keeps_edits_for_a_smaller_retry(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='account-browser-admin', role=User.Roles.ADMIN, must_change_password=False)
        schema = baseline_schema('account_management')
        labels = [f'label-{i}' for i in range(10)]
        schema['fields'].append({'key': 'custom_labels', 'type': 'multiselect', 'label': 'Labels',
                                 'choices': [{'value': value, 'label': value} for value in labels]})
        draft = save_draft('account_management', schema, admin, 0)
        definition = publish('account_management', admin, draft.revision)
        content_type = ContentType.objects.get_for_model(Account)
        for name in ('Summit Research', 'Valley Labs'):
            account = Account.objects.create(name=name)
            FormAnswerSet.objects.create(definition=definition, content_type=content_type, object_id=account.pk,
                                         schema_version=definition.published_version, answers={'custom_labels': labels})
        with self.browser_page(admin) as page:
            page.goto(self.live_server_url + reverse('hub:management') + '?tab=accounts&account_q=Summit')
            row = page.locator('#accounts-tbody tr.mgmt-row')
            name = row.locator('input[name$="-name"]')
            name.fill('Summit Renamed')
            search = page.get_by_role('searchbox', name='Search accounts')
            search.fill('Valley')
            expect(name).to_have_value('Valley Labs')
            name.fill('Valley Renamed')
            button = page.get_by_role('button', name='Save Changes')
            button.click()
            expect(page.get_by_text('Too many account fields to save at once.')).to_be_visible()
            expect(name).to_have_value('Valley Renamed')
            row.get_by_role('button', name='Revert row edits').click()
            expect(name).to_have_value('Valley Labs')
            expect(page.locator('#account-draft-status')).to_have_text('1 unsaved change')
            button.click()
            expect(page.get_by_text('Updated 1 account.')).to_be_visible()

        self.assertEqual(set(Account.objects.values_list('name', flat=True)), {'Summit Renamed', 'Valley Labs'})

    def test_hidden_parent_disables_its_descendant_before_account_draft_is_captured(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='account-browser-admin', role=User.Roles.ADMIN, must_change_password=False)
        account = Account.objects.create(name='Summit Research')
        schema = baseline_schema('account_management')
        schema['fields'] += [
            {'key': 'custom_child', 'type': 'text', 'label': 'Child note',
             'condition': {'field': 'custom_parent', 'operator': 'not_empty'}},
            {'key': 'custom_parent', 'type': 'text', 'label': 'Parent note',
             'condition': {'field': 'name', 'operator': 'equals', 'value': account.name}},
        ]
        draft = save_draft('account_management', schema, admin, 0)
        definition = publish('account_management', admin, draft.revision)
        saved_answers = {'custom_parent': 'Saved parent', 'custom_child': 'Saved child'}
        FormAnswerSet.objects.create(
            definition=definition, content_type=ContentType.objects.get_for_model(Account),
            object_id=account.pk, schema_version=definition.published_version, answers=saved_answers,
        )
        with self.browser_page(admin) as page:
            page.goto(self.live_server_url + reverse('hub:management') + '?tab=accounts')
            row = page.locator('#accounts-tbody tr.mgmt-row')
            child = row.get_by_label('Child note')
            expect(child).to_be_visible()
            child.fill('Do not send this while inactive')
            row.locator('input[name$="-name"]').fill('Summit Renamed')
            expect(row.get_by_label('Parent note')).to_be_hidden()
            expect(child).to_be_hidden()
            page.get_by_role('searchbox', name='Search accounts').fill('No results')
            expect(row).to_have_count(0)
            with page.expect_request(lambda req: req.method == 'POST') as submission:
                page.get_by_role('button', name='Save Changes').click()
            self.assertNotIn('custom_child', submission.value.post_data)
            expect(page.get_by_text('Updated 1 account.')).to_be_visible()

        self.assertEqual(get_custom_answers('account_management', account), saved_answers)

    def test_paging_during_a_pending_search_keeps_query_and_results_in_sync(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='account-browser-admin', role=User.Roles.ADMIN, must_change_password=False)
        Account.objects.bulk_create([Account(name=f'Demo {index:03}') for index in range(51)])
        with self.browser_page(admin) as page:
            page.goto(self.live_server_url + reverse('hub:management') + '?tab=accounts')
            search = page.get_by_role('searchbox', name='Search accounts')
            search.evaluate("""input => {
                input.value = 'Summit';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                document.querySelector('[aria-label="Next page"]').click();
            }""")
            expect(page.locator('#accounts-tbody input[name$="-name"]').first).to_have_value('Demo 050')
            expect(page).to_have_url(self.live_server_url + reverse('hub:management') + '?tab=accounts&account_page=2')
            expect(search).to_have_value('')

    def test_sqr_custom_requirement_refreshes_after_department_clears_manager(self):
        from playwright.sync_api import expect

        admin = User.objects.create_user(username='sqr-editor-admin', role=User.Roles.ADMIN)
        engineer = User.objects.create_user(username='sqr-browser-engineer', role=User.Roles.ENGINEER,
                                            must_change_password=False)
        schema = baseline_schema('sqr_submission')
        schema['fields'].append({
            'key': 'custom_manager_note', 'type': 'text', 'label': 'Manager note', 'required': True,
            'condition': {'field': 'customer_contact', 'operator': 'not_empty'},
        })
        draft = save_draft('sqr_submission', schema, admin, 0)
        publish('sqr_submission', admin, draft.revision)
        with self.browser_page(engineer) as page:
            page.goto(self.live_server_url + reverse('hub:sqr'))
            page.locator('#new-sqr-btn').click()
            modal = page.locator('#create-sqr-form')
            department = modal.locator('#id_customer_company')
            manager = modal.locator('#id_customer_contact')
            note = modal.get_by_label('Manager note')
            department.select_option('ESS')
            manager.select_option(index=1)
            expect(note).to_be_visible()
            expect(note).to_have_js_property('required', True)
            department.select_option('HP')
            expect(manager).to_have_value('')
            expect(note).to_be_hidden()
            expect(note).to_be_disabled()
