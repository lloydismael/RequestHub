"""Versioning, schema boundaries and answer persistence regression tests."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import User
from . import form_editor as editor
from .models import Account, FormAnswerSet, FormDefinition, FormEditorAuditEvent, FormVersion


class FormEditorSchemaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="editor_admin", role="admin")
        cls.other = User.objects.create_user(username="editor_other", role="engineer")

    def schema(self, **custom):
        schema = editor.baseline_schema("account_management")
        if custom:
            field = {"key": "custom_notes", "type": "text", "label": "Notes", "required": False,
                     "hidden": False, "section": "", "width": "full"}
            field.update(custom)
            schema["fields"].append(field)
        return schema

    def published(self, **custom):
        definition = editor.save_draft("account_management", self.schema(**custom), self.admin, 0)
        return editor.publish("account_management", self.admin, definition.revision)

    def test_registry_covers_business_forms_and_excludes_auth(self):
        from .forms import AccountManagementForm
        from accounts.forms import RoleAuthenticationForm
        self.assertEqual(editor.get_form_key(AccountManagementForm), "account_management")
        self.assertIsNone(editor.get_form_key(RoleAuthenticationForm))
        for key in editor.get_registry():
            with self.subTest(key=key):
                schema = editor.baseline_schema(key)
                self.assertTrue(schema["fields"])
                self.assertEqual(editor.validate_schema(key, schema), schema)

    def test_activity_log_baseline_has_no_requests_for_unsaved_engineer(self):
        form = editor.baseline_form("engineer_activity_log")
        self.assertIsNone(form.engineer.pk)
        self.assertFalse(form.fields["request"].queryset.exists())

    def test_activity_logs_page_renders_without_published_schema(self):
        self.client.force_login(self.other)
        response = self.client.get("/activity-logs/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity")

    def test_person_selection_validation(self):
        schema = editor.baseline_schema("request")
        engineer = next(field for field in schema["fields"] if field["key"] == "engineer")
        engineer["person_ids"] = [str(self.other.pk)]
        self.assertEqual(editor.validate_schema("request", schema), schema)
        for values in ([str(self.admin.pk)], ["999999"], [self.other.pk], [str(self.other.pk)] * 2, "all"):
            engineer["person_ids"] = values
            with self.subTest(values=values), self.assertRaises(editor.SchemaValidationError):
                editor.validate_schema("request", schema)
        protected = self.schema()
        protected["fields"][0]["person_ids"] = []
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("account_management", protected)

    def test_sqr_allowed_members_and_required_approver(self):
        schema = editor.baseline_schema("sqr_submission")
        metadata = editor.get_person_fields("sqr_submission")
        member = next(field for field in schema["fields"] if field["key"] == "customer_contact")
        member["option_values"] = [metadata["customer_contact"]["choices"][0]["value"]]
        editor.validate_schema("sqr_submission", schema)
        member["option_values"] = ["Not an approved member"]
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("sqr_submission", schema)
        member.pop("option_values")
        approver = next(field for field in schema["fields"] if field["key"] == "pm_esg_reviewer")
        approver["person_ids"] = []
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("sqr_submission", schema)

    def test_published_retired_people_can_be_removed_without_expanding_eligibility(self):
        schema = editor.baseline_schema("request")
        field = next(field for field in schema["fields"] if field["key"] == "engineer")
        field["person_ids"] = [str(self.other.pk)]
        definition = editor.save_draft("request", schema, self.admin, 0)
        editor.publish("request", self.admin, definition.revision)
        self.other.role = "requestor"
        self.other.save(update_fields=["role"])
        editor.validate_schema("request", schema)
        self.assertNotIn(str(self.other.pk), {item["value"] for item in editor.get_person_fields("request")["engineer"]["choices"]})

    def test_read_only_defaults_do_not_create_rows(self):
        self.assertIsNone(editor.get_definition("account_management").pk)
        self.assertEqual(editor.get_schema("account_management"), self.schema())
        self.assertFalse(FormDefinition.objects.exists())
        self.assertIsNone(editor.get_published_version("account_management"))

    def test_unknown_forms_fail_closed(self):
        for key in ("login", "password", "arbitrary", ""):
            with self.assertRaises(editor.SchemaValidationError):
                editor.baseline_schema(key)

    def test_safe_core_presentation_and_order(self):
        schema = self.schema()
        field = schema["fields"][0]
        field.update(label="Customer", help_text="Customer account name", placeholder="Enter name", width="half", section="Details")
        self.assertEqual(editor.validate_schema("account_management", schema), schema)

    def test_core_validation_cannot_change(self):
        for property_name, value in (("required", False), ("hidden", True), ("type", "text"),
                                     ("choices", []), ("condition", {"field": "custom_x", "operator": "empty"})):
            schema = self.schema()
            field = schema["fields"][0]
            if property_name == "required":
                value = not field["required"]
            field[property_name] = value
            with self.subTest(property_name=property_name), self.assertRaises(editor.SchemaValidationError):
                editor.validate_schema("account_management", schema)

    def test_security_presentation_is_locked(self):
        schema = editor.baseline_schema("user_management")
        password = next(f for f in schema["fields"] if f["key"] == "password1")
        password["label"] = "Something else"
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("user_management", schema)
        self.assertIn("password1", editor.get_protected_fields("user_management"))

    def test_schema_rejects_injection_and_unsafe_properties(self):
        mutations = [lambda s: s.update(code="danger"), lambda s: s["fields"][0].update(binding="role"),
                     lambda s: s["fields"][0].update(label="<script>alert(1)</script>"),
                     lambda s: s["fields"].append(deepcopy(s["fields"][0])),
                     lambda s: s["fields"].clear(), lambda s: s["fields"][0].update(required=1)]
        for mutation in mutations:
            schema = self.schema()
            mutation(schema)
            with self.assertRaises(editor.SchemaValidationError):
                editor.validate_schema("account_management", schema)

    def test_custom_types_and_choice_validation(self):
        for kind in editor.CUSTOM_TYPES:
            schema = self.schema(type=kind)
            if kind in {"select", "multiselect"}:
                schema["fields"][-1]["choices"] = [{"value": "yes", "label": "Yes"}]
            editor.validate_schema("account_management", schema)
        for change in ({"type": "file"}, {"type": "select"}, {"type": "text", "default": "unsafe"},
                       {"type": "select", "choices": [{"value": "x", "label": "X"}, {"value": "x", "label": "Again"}]}):
            with self.assertRaises(editor.SchemaValidationError):
                editor.validate_schema("account_management", self.schema(**change))

    def test_condition_references_and_cycles(self):
        schema = self.schema(condition={"field": "name", "operator": "not_empty", "value": ""})
        editor.validate_schema("account_management", schema)
        for target in ("missing", "custom_notes"):
            broken = deepcopy(schema)
            broken["fields"][-1]["condition"]["field"] = target
            with self.assertRaises(editor.SchemaValidationError):
                editor.validate_schema("account_management", broken)
        schema["fields"][-1]["condition"]["field"] = "custom_other"
        schema["fields"].append({"key": "custom_other", "type": "text", "condition": {"field": "custom_notes", "operator": "empty"}})
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("account_management", schema)

    def test_malformed_schema_values_raise_validation_errors(self):
        for property_name in ("width", "type"):
            with self.subTest(property_name=property_name), self.assertRaises(editor.SchemaValidationError):
                editor.validate_schema("account_management", self.schema(**{property_name: []}))
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("account_management", self.schema(condition={"field": "name", "operator": []}))

    def test_security_field_positions_are_locked(self):
        schema = editor.baseline_schema("user_management")
        schema["fields"].reverse()
        with self.assertRaises(editor.SchemaValidationError):
            editor.validate_schema("user_management", schema)

    def test_immutable_reconstructed_instance_and_upserts(self):
        version = self.published(label="Original").published_version
        replacement = FormVersion(pk=version.pk, definition=version.definition, number=version.number,
                                  schema={"fields": []}, actor=self.admin)
        with self.assertRaises(ValidationError):
            replacement.save()
        with self.assertRaises(ValidationError):
            FormVersion.objects.bulk_create([replacement], update_conflicts=True, update_fields=["schema"], unique_fields=["id"])
        version.refresh_from_db()
        self.assertEqual(version.schema["fields"][-1]["label"], "Original")

    def test_draft_live_separation_and_copy_isolation(self):
        schema = self.schema(label="Unpublished")
        definition = editor.save_draft("account_management", schema, self.admin, 0)
        self.assertEqual(definition.revision, 1)
        self.assertEqual(editor.get_schema("account_management"), editor.baseline_schema("account_management"))
        self.assertEqual(editor.get_schema("account_management", published=False)["fields"][-1]["label"], "Unpublished")
        definition = editor.publish("account_management", self.admin, 1)
        live = editor.get_schema("account_management")
        live["title"] = "Mutation"
        self.assertNotEqual(editor.get_schema("account_management")["title"], "Mutation")
        self.assertEqual(definition.published_version.number, 1)

    def test_stale_revisions_fail_without_changes(self):
        self.published(label="Notes")
        for operation in (lambda: editor.save_draft("account_management", self.schema(), self.admin, 0),
                          lambda: editor.publish("account_management", self.admin, 1),
                          lambda: editor.reset_to_default("account_management", self.admin, 0)):
            with self.assertRaises(editor.RevisionConflict):
                operation()
        self.assertEqual(FormVersion.objects.count(), 1)
        self.assertEqual(FormEditorAuditEvent.objects.count(), 2)
        for revision in (None, True, "2", -1):
            with self.assertRaises(editor.RevisionConflict):
                editor.publish("account_management", self.admin, revision)

    def test_actor_role_is_enforced(self):
        for actor in (None, self.other, User(role="admin")):
            with self.assertRaises(PermissionDenied):
                editor.save_draft("account_management", self.schema(), actor, 0)
        self.admin.is_active = False
        with self.assertRaises(PermissionDenied):
            editor.save_draft("account_management", self.schema(), self.admin, 0)

    def test_rollback_reset_create_new_versions_and_audit(self):
        definition = self.published(label="Original")
        original = definition.published_version
        definition = editor.save_draft("account_management", self.schema(label="Changed"), self.admin, 2)
        definition = editor.publish("account_management", self.admin, 3)
        definition = editor.rollback("account_management", original.pk, self.admin, 4)
        self.assertEqual(definition.published_version.number, 3)
        self.assertEqual(definition.published_version.schema, original.schema)
        definition = editor.reset_to_default("account_management", self.admin, 5)
        self.assertEqual(definition.revision, 6)
        self.assertEqual(editor.get_schema("account_management"), self.schema())
        self.assertEqual(list(definition.audit_events.values_list("action", flat=True))[:2], ["reset", "rollback"])
        self.assertEqual(FormVersion.objects.count(), 4)

    def test_cross_form_rollback_and_answer_version_rejected(self):
        definition = self.published(label="Notes")
        with self.assertRaises(editor.SchemaValidationError):
            editor.rollback("profile", definition.published_version.pk, self.admin, 0)
        self.assertFalse(FormDefinition.objects.filter(key="profile").exists())

    def test_published_versions_and_audit_are_immutable(self):
        definition = self.published(label="Notes")
        for record in (definition.published_version, definition.audit_events.first()):
            with self.assertRaises(ValidationError):
                record.save()
            with self.assertRaises(ValidationError):
                record.delete()
            with self.assertRaises(ValidationError):
                type(record).objects.filter(pk=record.pk).update(actor=None)
            with self.assertRaises(ValidationError):
                type(record).objects.filter(pk=record.pk).delete()

    def test_atomic_audit_failure_rolls_back_everything(self):
        with patch.object(editor, "_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                editor.save_draft("account_management", self.schema(label="Notes"), self.admin, 0)
        self.assertFalse(FormDefinition.objects.exists())

    def answer_form(self, definition, **answers):
        return SimpleNamespace(editor_key="account_management", editor_version=definition.published_version,
                               editor_custom_keys=["custom_notes"], editor_visible_custom_keys=["custom_notes"],
                               cleaned_data=answers, errors={})

    def test_answers_round_trip_and_preserve_hidden_old_values(self):
        definition = self.published(label="Notes")
        account = Account.objects.create(name="Customer")
        form = self.answer_form(definition, custom_notes="Original", forged="ignored")
        answers = editor.save_custom_answers(form, account)
        self.assertEqual(answers.answers, {"custom_notes": "Original"})
        self.assertEqual(answers.schema_version, definition.published_version)
        form.editor_visible_custom_keys = []
        form.cleaned_data = {"custom_notes": "Forged hidden"}
        editor.save_custom_answers(form, account)
        self.assertEqual(editor.get_custom_answers("account_management", account), {"custom_notes": "Original"})
        self.assertEqual(FormAnswerSet.objects.count(), 1)

    def test_answer_record_allowlist(self):
        definition = self.published(label="Notes")
        with self.assertRaises(editor.SchemaValidationError):
            editor.save_custom_answers(self.answer_form(definition, custom_notes="x"), self.admin)
        account = Account.objects.create(name="Allowed")
        answers = FormAnswerSet(definition=definition, content_type=ContentType.objects.get_for_model(User),
                                object_id=account.pk, schema_version=definition.published_version)
        with self.assertRaises(ValidationError):
            answers.full_clean()

    def test_condition_hidden_answers_ignore_forged_visibility(self):
        definition = self.published(label="Notes", condition={"field": "name", "operator": "equals", "value": "Allowed"})
        account = Account.objects.create(name="Not allowed")
        editor.save_custom_answers(self.answer_form(definition, name="Not allowed", custom_notes="Forged"), account)
        self.assertEqual(editor.get_custom_answers("account_management", account), {})

    def test_answer_dates_decimals_and_invalid_form(self):
        definition = self.published(label="Notes")
        account = Account.objects.create(name="Typed")
        form = self.answer_form(definition, custom_notes=[date(2026, 10, 15), Decimal("2.50")])
        self.assertEqual(editor.save_custom_answers(form, account).answers["custom_notes"], ["2026-10-15", "2.50"])
        form.errors = {"custom_notes": "invalid"}
        with self.assertRaises(editor.SchemaValidationError):
            editor.save_custom_answers(form, account)
