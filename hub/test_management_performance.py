from copy import deepcopy

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.forms import UserManagementForm
from accounts.models import User
from .form_editor import baseline_schema, publish, save_draft
from .models import FormAnswerSet, FormDefinition
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
