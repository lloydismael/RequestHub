from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django import forms
from django.test import SimpleTestCase, TestCase

from accounts.models import User

from . import form_editor as editor
from .form_editor_runtime import (
    TOKEN_FIELD,
    condition_matches,
    configure_form,
    editable_form,
)
from .forms import RequestForm, SqrDeliveryForm, SqrSubmissionForm, SqrTrackerEditForm
from .models import Account, FormDefinition, FormVersion, Request, SqrSubmission


@editable_form
class RuntimeExampleForm(forms.Form):
    subject = forms.CharField()
    trigger = forms.CharField(required=False)


class FormEditorRuntimeTests(SimpleTestCase):
    def setUp(self):
        self.schema = {
            "title": "Custom form", "description": "Introduction",
            "fields": [
                {"key": "subject", "label": "Summary", "required": False},
                {"key": "trigger", "label": "Trigger"},
                {"key": "custom_detail", "type": "text", "label": "Detail", "required": True,
                 "condition": {"field": "trigger", "operator": "equals", "value": "yes"}},
            ],
        }
        for name, value in (("get_form_key", "example"), ("get_definition", None),
                            ("get_schema", self.schema), ("baseline_schema", self.schema)):
            patcher = patch(f"hub.form_editor.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_core_validation_is_unchanged(self):
        form = RuntimeExampleForm({"trigger": "no"})
        self.assertFalse(form.is_valid())
        self.assertIn("subject", form.errors)
        self.assertNotIn("custom_detail", form.errors)

    def test_conditional_required_field_is_validated(self):
        form = RuntimeExampleForm({"subject": "Test", "trigger": "yes"})
        self.assertFalse(form.is_valid())
        self.assertIn("custom_detail", form.errors)

    def test_inactive_custom_dependency_does_not_require_its_descendant(self):
        schema = {**self.schema, 'fields': [
            *self.schema['fields'][:2],
            {'key': 'custom_detail', 'type': 'text', 'required': True,
             'condition': {'field': 'custom_parent', 'operator': 'not_equals', 'value': 'other'}},
            {'key': 'custom_parent', 'type': 'text',
             'condition': {'field': 'trigger', 'operator': 'equals', 'value': 'yes'}},
        ]}
        for parent in ({}, {'custom_parent': 'Retained value'}):
            with self.subTest(parent=parent):
                form = RuntimeExampleForm({'subject': 'Test', 'trigger': 'no', **parent}, _form_editor_skip=True)
                configure_form(form, schema)
                self.assertTrue(form.is_valid(), form.errors)
        active = RuntimeExampleForm({'subject': 'Test', 'trigger': 'yes'}, _form_editor_skip=True)
        configure_form(active, schema)
        self.assertIn('custom_detail', active.errors)

    def test_hidden_conditional_field_remains_renderable(self):
        form = RuntimeExampleForm()
        self.assertEqual([field.name for field in form.editor_custom_fields], ["custom_detail"])
        self.assertFalse(form.fields["custom_detail"].disabled)
        self.assertEqual(form.fields["custom_detail"].widget.attrs["data-editor-required"], "true")

    def test_tampered_version_is_rejected(self):
        form = RuntimeExampleForm({"subject": "Test", TOKEN_FIELD: "invalid"})
        self.assertFalse(form.is_valid())
        self.assertIn("Reload the page", str(form.non_field_errors()))

    def test_baseline_token_survives_presentation_changes(self):
        token = RuntimeExampleForm().initial[TOKEN_FIELD]
        form = RuntimeExampleForm({"subject": "Test", "trigger": "no", TOKEN_FIELD: token})
        self.assertTrue(form.is_valid(), form.errors)

    def test_preview_reconfiguration_updates_cached_bound_fields(self):
        form = RuntimeExampleForm()
        original = form["custom_detail"]
        schema = {**self.schema, "fields": [{"key": "custom_detail", "type": "textarea", "label": "Updated"}]}
        configure_form(form, schema=schema)
        self.assertIsNot(form.editor_custom_fields[0], original)
        self.assertEqual(form.editor_custom_fields[0].label, "Updated")

    def test_conditions_preserve_zero_and_boolean_values(self):
        self.assertTrue(condition_matches({"operator": "equals", "value": 0}, 0))
        self.assertTrue(condition_matches({"operator": "equals", "value": False}, False))
        self.assertFalse(condition_matches({"operator": "empty"}, 0))
        self.assertTrue(condition_matches({"operator": "equals", "value": "yes"}, ["no", "yes"]))

    def test_saved_custom_choices_remain_available_when_retired(self):
        schema = {"fields": [{"key": "subject"}, {"key": "trigger"},
                             {"key": "custom_choice", "type": "select", "choices": [{"value": "new", "label": "New"}]}]}
        form = RuntimeExampleForm(_form_editor_skip=True)
        form.instance = SimpleNamespace(pk=1)
        with patch("hub.form_editor.get_custom_answers", return_value={"custom_choice": "old"}):
            configure_form(form, schema)
        self.assertIn(("old", "old (retired)"), form.fields["custom_choice"].choices)
        self.assertEqual(form.initial["custom_choice"], "old")

    def test_skip_flag_avoids_runtime_for_baseline(self):
        form = RuntimeExampleForm(_form_editor_skip=True)
        self.assertFalse(hasattr(form, "editor_enabled"))

class SqrPublishedDeliveryRuntimeTests(TestCase):
    def test_old_published_and_pinned_layouts_keep_current_delivery_contract(self):
        retired = {"delivery_target_finish_date", "delivery_completion_signed_date", "delivery_health", "warranty_end_date"}
        for key, form_class in (("sqr_delivery", SqrDeliveryForm), ("sqr_tracker_edit", SqrTrackerEditForm)):
            with self.subTest(form=key):
                old_schema = {"fields": [{"key": name, "label": "Legacy control"} for name in sorted(retired)] + [
                    {"key": "schedule_performance", "label": "Health Status", "section": "Progress"},
                ]}
                definition = FormDefinition.objects.create(key=key, draft=old_schema)
                old_version = FormVersion.objects.create(definition=definition, number=1, schema=old_schema)
                definition.published_version = old_version
                definition.save()
                row = SqrSubmission(project_details="Implementation", managed_support_amount=0,
                                    managed_support_end_date=date(2028, 2, 6))
                rendered = form_class(instance=row)
                token = rendered.initial[TOKEN_FIELD]
                new_version = FormVersion.objects.create(definition=definition, number=2, schema={"fields": []})
                definition.published_version = new_version
                definition.save()
                pinned = form_class({TOKEN_FIELD: token}, instance=row)
                self.assertEqual(pinned.editor_version.pk, old_version.pk)
                for form in (rendered, pinned):
                    self.assertIn("Maintenance Start Date is required when an End Date is set.", form.as_p())
                    self.assertFalse(retired.intersection(form.fields))
                    self.assertEqual(form.fields["schedule_performance"].label, "Schedule Performance")
                    self.assertEqual(form.fields["schedule_performance"].widget.attrs["data-form-section"], "Progress")
                    for name in ("quality_performance", "budget_effort_performance", "delivery_actual_finish_date",
                                 "managed_support_start_date", "managed_support_end_date"):
                        self.assertIn(name, form.fields)
                    self.assertEqual(form.fields["schedule_performance"].clean("delayed"), "delayed")
                    with self.assertRaises(forms.ValidationError):
                        form.fields["schedule_performance"].clean("off_track")


class PersonSelectionRuntimeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.first = User.objects.create_user(username="allowed_engineer", role="engineer")
        cls.second = User.objects.create_user(username="other_engineer", role="engineer")
        cls.manager = User.objects.create_user(username="allowed_pm", role="pm_esg")
        cls.requestor = User.objects.create_user(username="ineligible_requestor", role="requestor")

    def schema(self, key, field, values, property_name="person_ids"):
        schema = editor.baseline_schema(key)
        next(item for item in schema["fields"] if item["key"] == field)[property_name] = values
        return schema

    def test_runtime_intersects_actor_rules_and_rejects_removed_people(self):
        schema = self.schema("request", "engineer", [str(self.first.pk), str(self.requestor.pk)])
        form = RequestForm(actor_role="requestor", _form_editor_skip=True)
        configure_form(form, schema)
        self.assertEqual(list(form.fields["engineer"].queryset), [self.first])
        with self.assertRaises(forms.ValidationError):
            form.fields["engineer"].clean(str(self.second.pk))
        with self.assertRaises(forms.ValidationError):
            form.fields["engineer"].clean(str(self.requestor.pk))

    def test_existing_selection_survives_configuration_but_not_actor_rules(self):
        schema = self.schema("request", "engineer", [str(self.first.pk)])
        account = Account.objects.create(name="Historical account")
        instance = Request(pk=100, engineer=self.second, account=account)
        form = RequestForm(instance=instance, actor_role="requestor", _form_editor_skip=True)
        configure_form(form, schema)
        self.assertIn(self.second, form.fields["engineer"].queryset)
        instance = Request(pk=101, engineer=self.manager, account=account)
        form = RequestForm(instance=instance, actor_role="requestor", actor_user=self.manager, _form_editor_skip=True)
        original = set(form.fields["engineer"].queryset.values_list("pk", flat=True))
        configure_form(form, schema)
        self.assertTrue(set(form.fields["engineer"].queryset.values_list("pk", flat=True)).issubset(original))

    def test_empty_optional_selector_and_all_eligible_default(self):
        schema = self.schema("request", "engineer", [])
        form = RequestForm(actor_role="requestor", _form_editor_skip=True)
        configure_form(form, schema)
        self.assertFalse(form.fields["engineer"].queryset.exists())
        form = RequestForm(actor_role="requestor", _form_editor_skip=True)
        count = form.fields["engineer"].queryset.count()
        configure_form(form, editor.baseline_schema("request"))
        self.assertEqual(form.fields["engineer"].queryset.count(), count)

    def test_sqr_member_map_and_approver_are_filtered(self):
        choices = editor.get_person_fields("sqr_submission")["customer_contact"]["choices"]
        chosen = choices[0]["value"]
        schema = self.schema("sqr_submission", "customer_contact", [chosen], "option_values")
        next(item for item in schema["fields"] if item["key"] == "pm_esg_reviewer")["person_ids"] = [str(self.manager.pk)]
        form = SqrSubmissionForm(_form_editor_skip=True)
        configure_form(form, schema)
        self.assertEqual({value for value, label in form.fields["customer_contact"].choices if value}, {chosen})
        import json
        members = json.loads(form.fields["customer_company"].widget.attrs["data-member-map"])
        self.assertEqual({value for values in members.values() for value in values}, {chosen})
        self.assertEqual(list(form.fields["pm_esg_reviewer"].queryset), [self.manager])

    def test_pinned_person_list_is_stable_after_publish(self):
        admin = User.objects.create_user(username="person_admin", role="admin")
        first_schema = self.schema("request", "engineer", [str(self.first.pk)])
        definition = editor.save_draft("request", first_schema, admin, 0)
        editor.publish("request", admin, definition.revision)
        token = RequestForm(actor_role="requestor").initial[TOKEN_FIELD]
        definition.refresh_from_db()
        second_schema = self.schema("request", "engineer", [str(self.second.pk)])
        definition = editor.save_draft("request", second_schema, admin, definition.revision)
        editor.publish("request", admin, definition.revision)
        bound = RequestForm({TOKEN_FIELD: token}, actor_role="requestor")
        self.assertEqual(list(bound.fields["engineer"].queryset), [self.first])
        self.assertEqual(list(RequestForm(actor_role="requestor").fields["engineer"].queryset), [self.second])
