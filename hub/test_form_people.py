import json
from copy import deepcopy

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from . import form_editor as editor
from .forms import RequestForm, SqrSubmissionForm
from .form_editor_runtime import TOKEN_FIELD
from .models import FormDefinition, FormVersion, SqrSubmission


@override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class FormPeopleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="people-admin", role="admin", must_change_password=False)
        cls.engineer = User.objects.create_user(username="people-engineer", role="engineer")
        cls.other = User.objects.create_user(username="people-other", role="engineer")
        cls.approver = User.objects.create_user(username="people-approver", role="pm_esg")

    def setUp(self):
        self.client.force_login(self.admin)

    def state(self, key="sqr_submission"):
        return self.client.get(reverse("hub:form-editor-people", args=[key])).json()

    def save(self, state, key="sqr_submission"):
        return self.client.post(reverse("hub:form-editor-people", args=[key]),
                                json.dumps({"expected_revision": state["revision"],
                                            "fields": {key: field["values"] for key, field in state["fields"].items()}}),
                                content_type="application/json")

    def member(self, name="New Configured Manager", group="ESS"):
        return {"value": name, "label": name, "group": group}

    def test_add_remove_member_and_pin_original_version(self):
        state = self.state()
        state["fields"]["customer_contact"]["values"] = [self.member()]
        response = self.save(state)
        self.assertEqual(response.status_code, 200, response.content)
        form = SqrSubmissionForm()
        token = form.initial[TOKEN_FIELD]
        self.assertEqual(form.fields["customer_contact"].clean("New Configured Manager"), "New Configured Manager")
        member_map = json.loads(form.fields["customer_company"].widget.attrs["data-member-map"])
        self.assertEqual(member_map["ESS"], ["New Configured Manager"])
        state = response.json()
        state["fields"]["customer_contact"]["values"] = []
        self.assertEqual(self.save(state).status_code, 200)
        with self.assertRaises(ValidationError):
            SqrSubmissionForm().fields["customer_contact"].clean("New Configured Manager")
        pinned = SqrSubmissionForm({TOKEN_FIELD: token})
        self.assertEqual(pinned.fields["customer_contact"].clean("New Configured Manager"), "New Configured Manager")
        record = SqrSubmission(pk=999, customer_contact="New Configured Manager", customer_company="ESS")
        historical = SqrSubmissionForm(instance=record)
        self.assertEqual(historical.fields["customer_contact"].clean(record.customer_contact), record.customer_contact)
        self.assertIn(record.customer_contact, json.loads(historical.fields["customer_company"].widget.attrs["data-member-map"])["ESS"])
        self.assertEqual(FormVersion.objects.count(), 2)

    def test_request_filters_existing_accounts_without_creating_users(self):
        before = User.objects.count()
        state = self.state("request")
        state["fields"]["engineer"]["values"] = [str(self.engineer.pk)]
        response = self.save(state, "request")
        self.assertEqual(response.status_code, 200, response.content)
        form = RequestForm(actor_role="admin")
        self.assertEqual(list(form.fields["engineer"].queryset), [self.engineer])
        self.assertEqual(User.objects.count(), before)
        state = response.json()
        state["fields"]["engineer"]["values"] = [str(self.admin.pk)]
        self.assertEqual(self.save(state, "request").status_code, 400)
        state["fields"]["engineer"]["values"] = None
        self.assertEqual(self.save(state, "request").status_code, 200)
        self.assertIn(self.other, RequestForm(actor_role="admin").fields["engineer"].queryset)

    def test_bad_names_groups_and_duplicates_do_not_write(self):
        base = self.state()
        invalid = [self.member(""), self.member("<script>"), self.member("x" * 256),
                   self.member(group="Invalid"), self.member(" leading"), {"value": "Name"}]
        for member in invalid:
            with self.subTest(member=member):
                state = deepcopy(base)
                state["fields"]["customer_contact"]["values"] = [member]
                self.assertEqual(self.save(state).status_code, 400)
        state = deepcopy(base)
        state["fields"]["customer_contact"]["values"] = [self.member("Name"), self.member("name")]
        self.assertEqual(self.save(state).status_code, 400)
        self.assertFalse(FormDefinition.objects.exists())
        self.assertFalse(FormVersion.objects.exists())
        state["fields"]["customer_contact"]["values"] = [self.member("Name", "ESS"), self.member("Name", "HP")]
        self.assertEqual(self.save(state).status_code, 200)

    def test_conflicts_and_drafts_are_not_overwritten(self):
        state = self.state("request")
        schema = editor.baseline_schema("request")
        schema["title"] = "Unpublished change"
        editor.save_draft("request", schema, self.admin, 0)
        self.assertEqual(self.save(state, "request").status_code, 409)
        current = self.state("request")
        self.assertTrue(current["has_draft"])
        response = self.save(current, "request")
        self.assertEqual(response.status_code, 409)
        self.assertIn("advanced-editor draft", response.json()["error"])
        self.assertEqual(FormDefinition.objects.get(key="request").draft["title"], "Unpublished change")
        self.assertFalse(FormVersion.objects.exists())

    def test_people_endpoint_role_method_and_scope_guards(self):
        url = reverse("hub:form-editor-people", args=["request"])
        self.assertEqual(self.client.put(url).status_code, 405)
        self.assertEqual(self.client.get(reverse("hub:form-editor-people", args=["profile"])).status_code, 404)
        self.client.force_login(self.engineer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 403)

    def test_published_customizations_survive_and_legacy_names_convert(self):
        schema = editor.baseline_schema("sqr_submission")
        schema["title"] = "Custom SQR Title"
        contact = next(field for field in schema["fields"] if field["key"] == "customer_contact")
        first = SqrSubmissionForm.MEMBERS_BY_GROUP["ESS"][0]
        contact["option_values"] = [first]
        editor.save_draft("sqr_submission", schema, self.admin, 0)
        editor.publish("sqr_submission", self.admin, 1)
        state = self.state()
        self.assertEqual({member["value"] for member in state["fields"]["customer_contact"]["values"]}, {first})
        state["fields"]["customer_contact"]["values"] = [self.member()]
        response = self.save(state)
        self.assertEqual(response.status_code, 200, response.content)
        published = editor.get_schema("sqr_submission")
        self.assertEqual(published["title"], "Custom SQR Title")
        self.assertNotIn("option_values", next(field for field in published["fields"] if field["key"] == "customer_contact"))
        self.assertEqual(self.save(state).status_code, 409)
        self.assertEqual(FormVersion.objects.count(), 2)

    def test_missing_and_extra_fields_fail_without_writes(self):
        state = self.state("request")
        state["fields"] = {}
        self.assertEqual(self.save(state, "request").status_code, 400)
        state["fields"] = {"unexpected": {"values": []}}
        self.assertEqual(self.save(state, "request").status_code, 400)
        self.assertFalse(FormDefinition.objects.exists())

    def test_sqr_modal_uses_added_names_and_retains_removed_name(self):
        state = self.state()
        state["fields"]["customer_contact"]["values"] = [self.member()]
        self.assertEqual(self.save(state).status_code, 200)
        submission = SqrSubmission.objects.create(engineer=self.engineer, pm_esg_reviewer=self.approver,
            customer_name="People customer", customer_company="ESS", customer_contact="Former Manager",
            project_title="People project")
        self.client.force_login(self.engineer)
        response = self.client.get(reverse("hub:sqr-edit", args=[submission.pk]), {"format": "form-editor"})
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertIn("New Configured Manager", payload["selectors"]["customer_contact"])
        self.assertIn("Former Manager", payload["selectors"]["customer_contact"])
        self.assertEqual(json.loads(payload["member_map"])["ESS"], ["New Configured Manager", "Former Manager"])

    def test_member_options_cannot_mix_with_legacy_values(self):
        schema = editor.baseline_schema("sqr_submission")
        contact = next(field for field in schema["fields"] if field["key"] == "customer_contact")
        contact["member_options"] = [self.member()]
        contact["option_values"] = []
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("sqr_submission", schema)

    def test_member_options_are_protected_on_other_fields(self):
        schema = editor.baseline_schema("request")
        schema["fields"][0]["member_options"] = [self.member()]
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("request", schema)
