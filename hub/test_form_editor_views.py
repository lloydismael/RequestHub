import json

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from .form_editor import baseline_schema, get_registry
from .form_editor_views import catalog
from .models import FormDefinition, FormVersion


@override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class FormCatalogCategoryTests(SimpleTestCase):
    def test_catalog_groups_every_registered_form_by_category(self):
        sections = catalog()
        self.assertEqual([section["name"] for section in sections], ["Request", "SQR", "Management", "Others"])
        grouped = {form["key"]: section["name"] for section in sections for form in section["forms"]}
        self.assertEqual(set(grouped), set(get_registry()))
        self.assertEqual(grouped["admin_request_filter"], "Request")
        self.assertEqual(grouped["sqr_import"], "SQR")
        self.assertEqual(grouped["account_management"], "Management")
        self.assertEqual(grouped["profile"], "Others")
        self.assertTrue(all(section["description"] and section["forms"] for section in sections))


class FormEditorViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="editor-admin", password="Example-password-937!", role="admin", must_change_password=False)
        self.client.force_login(self.admin)

    def post(self, action, payload, key="request"):
        return self.client.post(reverse(f"hub:form-editor-{action}", args=[key]), json.dumps(payload), content_type="application/json")

    def test_catalog_and_every_preview_render_without_writes(self):
        self.assertEqual(self.client.get(reverse("hub:form-editor")).status_code, 200)
        for key in get_registry():
            with self.subTest(key=key):
                self.assertEqual(self.client.get(reverse("hub:form-editor-detail", args=[key])).status_code, 200)
                response = self.post("preview", {"schema": baseline_schema(key)}, key)
                self.assertEqual(response.status_code, 200, response.content[:500])
                self.assertNotContains(response, 'type="password"')
        self.assertFalse(FormDefinition.objects.exists())
        self.assertFalse(FormVersion.objects.exists())

    def test_save_publish_conflict_rollback_and_reset(self):
        schema = baseline_schema("request")
        schema["title"] = "Custom Request"
        response = self.post("draft", {"schema": schema, "expected_revision": 0})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["revision"], 1)
        self.assertEqual(self.post("publish", {"expected_revision": 0}).status_code, 409)
        published = self.post("publish", {"expected_revision": 1})
        self.assertEqual(published.status_code, 200, published.content)
        version_id = published.json()["published_version"]
        self.assertEqual(self.post("rollback", {"expected_revision": 2, "version_id": version_id}).status_code, 200)
        self.assertEqual(self.post("reset", {"expected_revision": 3}).status_code, 200)
        self.assertEqual(FormVersion.objects.count(), 3)

    def test_person_metadata_and_filtered_preview(self):
        engineer = User.objects.create_user(username="preview-engineer", role="engineer")
        other = User.objects.create_user(username="preview-other", role="engineer")
        response = self.client.get(reverse("hub:form-editor-history", args=["request"]))
        choices = response.json()["person_fields"]["engineer"]["choices"]
        self.assertIn(str(engineer.pk), {choice["value"] for choice in choices})
        schema = baseline_schema("request")
        next(field for field in schema["fields"] if field["key"] == "engineer")["person_ids"] = [str(engineer.pk)]
        response = self.post("preview", {"schema": schema})
        self.assertContains(response, "preview-engineer")
        self.assertNotContains(response, "preview-other")

    def test_sqr_modal_hydrates_filtered_and_historical_selectors(self):
        from .form_editor import get_person_fields
        from .models import SqrSubmission

        engineer = User.objects.create_user(username="modal-engineer", role="engineer", must_change_password=False)
        approver = User.objects.create_user(username="modal-approver", role="pm_esg")
        previous = User.objects.create_user(username="modal-previous", role="pm_esg")
        excluded = User.objects.create_user(username="modal-excluded", role="pm_esg")
        members = get_person_fields("sqr_submission")["customer_contact"]["choices"]
        chosen, removed = members[0]["value"], members[1]["value"]
        schema = baseline_schema("sqr_submission")
        next(field for field in schema["fields"] if field["key"] == "customer_contact")["option_values"] = [chosen]
        next(field for field in schema["fields"] if field["key"] == "pm_esg_reviewer")["person_ids"] = [str(approver.pk)]
        self.assertEqual(self.post("draft", {"schema": schema, "expected_revision": 0}, "sqr_submission").status_code, 200)
        self.assertEqual(self.post("publish", {"expected_revision": 1}, "sqr_submission").status_code, 200)
        submission = SqrSubmission.objects.create(engineer=engineer, pm_esg_reviewer=previous,
            customer_name="Modal customer", customer_company="ESS", customer_contact=removed, project_title="Modal project")
        self.client.force_login(engineer)
        response = self.client.get(reverse("hub:sqr-edit", args=[submission.pk]), {"format": "form-editor"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("_form_version", payload["html"])
        self.assertIn(chosen, payload["selectors"]["customer_contact"])
        self.assertIn(removed, payload["selectors"]["customer_contact"])
        self.assertIn('value="%s" selected' % previous.pk, payload["selectors"]["pm_esg_reviewer"])
        self.assertIn('value="%s"' % approver.pk, payload["selectors"]["pm_esg_reviewer"])
        self.assertNotIn('value="%s"' % excluded.pk, payload["selectors"]["pm_esg_reviewer"])
        values = {value for group in json.loads(payload["member_map"]).values() for value in group}
        self.assertEqual(values, {chosen, removed})
        stranger = User.objects.create_user(username="modal-stranger", role="engineer", must_change_password=False)
        self.client.force_login(stranger)
        denied = self.client.get(reverse("hub:sqr-edit", args=[submission.pk]), {"format": "form-editor"})
        self.assertEqual(denied.status_code, 302)
        self.client.force_login(excluded)
        self.assertEqual(self.client.get(reverse("hub:sqr-edit", args=[submission.pk]), {"format": "form-editor"}).status_code, 302)

    def test_role_and_method_guards(self):
        self.assertEqual(self.client.get(reverse("hub:form-editor-publish", args=["request"])).status_code, 405)
        self.admin.role = "engineer"
        self.admin.save(update_fields=["role"])
        for action in ("detail", "history"):
            self.assertEqual(self.client.get(reverse(f"hub:form-editor-{action}", args=["request"])).status_code, 403)
        self.assertEqual(self.post("draft", {"schema": baseline_schema("request"), "expected_revision": 0}).status_code, 403)

    def test_management_forms_tab_is_selected(self):
        response = self.client.get(reverse("hub:management"), {"tab": "forms"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="forms-pane"')
        self.assertEqual(response.context["active_tab"], "forms")
        self.assertInHTML(
            '<a class="nav-link active" id="forms-tab" '
            'href="%s?tab=forms" data-bs-toggle="tab" data-bs-target="#forms-pane" '
            'role="tab" aria-controls="forms-pane" aria-selected="true">'
            '<i class="bi bi-ui-checks me-1"></i>Forms</a>' % reverse("hub:management"),
            response.content.decode(),
        )

    def test_management_tabs_have_only_one_active_pane(self):
        for tab in ("users", "accounts", "forms"):
            with self.subTest(tab=tab):
                response = self.client.get(reverse("hub:management"), {"tab": tab})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["active_tab"], tab)
                self.assertContains(response, 'class="tab-pane show active"', count=1)
                self.assertRegex(
                    response.content.decode(),
                    r'class="tab-pane show active"\s+id="%s-pane"' % tab,
                )

    def test_published_custom_answer_persists_and_reopens(self):
        from .forms import AccountManagementForm
        from .form_editor import get_custom_answers
        schema = baseline_schema("account_management")
        schema["fields"].append({"key": "custom_notes", "type": "text", "label": "Notes", "required": True})
        self.assertEqual(self.post("draft", {"schema": schema, "expected_revision": 0}, "account_management").status_code, 200)
        self.assertEqual(self.post("publish", {"expected_revision": 1}, "account_management").status_code, 200)
        opened = AccountManagementForm()
        form = AccountManagementForm({"name": "Editor roundtrip", "custom_notes": "Retained answer", "_form_version": opened.initial["_form_version"]})
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        self.assertEqual(get_custom_answers("account_management", account), {"custom_notes": "Retained answer"})
        self.assertEqual(AccountManagementForm(instance=account).initial["custom_notes"], "Retained answer")
        schema["fields"][-1]["type"] = "number"
        self.assertEqual(self.post("draft", {"schema": schema, "expected_revision": 2}, "account_management").status_code, 400)

    def test_invalid_payload_and_unknown_form(self):
        self.assertEqual(self.post("draft", {"schema": {}, "expected_revision": True}).status_code, 400)
        self.assertEqual(self.client.get(reverse("hub:form-editor-detail", args=["unknown"])).status_code, 404)
