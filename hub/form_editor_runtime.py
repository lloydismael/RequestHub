"""Apply published presentation schemas without replacing domain validation."""
from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
import json

from django import forms
from django.db.models import Q
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

TOKEN_FIELD = "_form_version"
TOKEN_SALT = "hub.form-editor.version"
TOKEN_MAX_AGE = 86400


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def condition_matches(condition, value):
    operator = condition.get("operator")
    empty = value in (None, "", [], ())
    if operator == "empty":
        return empty
    if operator == "not_empty":
        return not empty
    expected = str(condition.get("value", ""))
    matches = expected in [str(item) for item in value] if isinstance(value, (list, tuple)) else str(value if value is not None else "") == expected
    return not matches if operator == "not_equals" else matches


def _custom_field(spec, historical=False):
    choices = [(choice["value"], choice["label"]) for choice in spec.get("choices", [])]
    kwargs = {"label": spec.get("label", spec["key"]), "help_text": spec.get("help_text", ""),
              "required": bool(spec.get("required")) and not historical}
    kind = spec.get("type", "text")
    if kind in ("select", "choice"):
        field = forms.ChoiceField(choices=[("", "— Select —")] + choices, **kwargs)
    elif kind in ("multiselect", "multiple_choice"):
        field = forms.MultipleChoiceField(choices=choices, **kwargs)
    elif kind in ("checkbox", "boolean"):
        field = forms.BooleanField(**kwargs)
    elif kind == "date":
        field = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), **kwargs)
    elif kind in ("number", "decimal"):
        field = forms.DecimalField(max_digits=20, decimal_places=4, **kwargs)
    elif kind == "email":
        field = forms.EmailField(max_length=254, **kwargs)
    elif kind == "url":
        field = forms.URLField(max_length=2000, **kwargs)
    else:
        widget = forms.Textarea(attrs={"rows": 3}) if kind in ("textarea", "long_text") else forms.TextInput()
        field = forms.CharField(max_length=10000, widget=widget, **kwargs)
    field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else ("form-select" if choices else "form-control")
    return field


def _configure_allowed_values(form, key, field, spec, form_key):
    from .form_editor import PERSON_FIELDS, VALUE_FIELDS

    instance = getattr(form, "instance", None)
    current = getattr(instance, key, None) if instance is not None and instance.pk else None
    current = getattr(current, "pk", current)
    if "person_ids" in spec and key in PERSON_FIELDS.get(form_key, set()) and isinstance(field, forms.ModelChoiceField):
        allowed = Q(pk__in=spec["person_ids"])
        if current not in (None, ""):
            allowed |= Q(pk=current)
        field.queryset = field.queryset.filter(allowed)
    if "member_options" in spec and form_key == "sqr_submission" and key == "customer_contact":
        options = spec["member_options"]
        names = list(dict.fromkeys(option["value"] for option in options))
        if current and current not in names:
            names.append(str(current))
        field.choices = [("", "— Select Account Manager —")] + [(name, name) for name in names]
        department = form.fields.get("customer_company")
        if department:
            member_map = {str(group): [] for group, _ in department.choices if group}
            for option in options:
                member_map[option["group"]].append(option["value"])
            current_group = getattr(instance, "customer_company", "") if current else ""
            if current_group in member_map and str(current) not in member_map[current_group]:
                member_map[current_group].append(str(current))
            department.widget.attrs["data-member-map"] = json.dumps(member_map)
    if "option_values" in spec and key in VALUE_FIELDS.get(form_key, set()) and isinstance(field, forms.ChoiceField):
        allowed = set(spec["option_values"])
        if current:
            allowed.add(str(current))
        field.choices = [(value, label) for value, label in field.choices if not value or str(value) in allowed]
        department = form.fields.get("customer_company")
        if department:
            member_map = json.loads(department.widget.attrs.get("data-member-map", "{}"))
            department.widget.attrs["data-member-map"] = json.dumps({
                group: [value for value in members if value in allowed] for group, members in member_map.items()
            })


def configure_form(form, schema=None):
    from . import form_editor
    key = form_editor.get_form_key(form)
    if not key:
        return form
    explicit = schema is not None
    definition = form_editor.get_definition(key)
    version = definition.published_version if definition else None
    pin_error = None
    posted = form.data.get(form.add_prefix(TOKEN_FIELD)) if form.is_bound else None
    if not explicit and posted:
        try:
            payload = signing.loads(posted, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
            if payload.get("key") != key:
                raise signing.BadSignature("Wrong form")
            version_id = payload.get("version")
            if version_id:
                from .models import FormVersion
                version = FormVersion.objects.get(pk=version_id, definition__key=key)
                schema = version.schema
            else:
                version = None
                schema = form_editor.baseline_schema(key)
        except (signing.BadSignature, ValueError, TypeError, KeyError, ObjectDoesNotExist):
            pin_error = "This form has expired or changed. Reload the page and submit again."
    if schema is None:
        schema = form_editor.get_schema(key)
    form.editor_enabled = True
    form.editor_key = key
    form.editor_version = version
    form.editor_schema = schema
    form.editor_title = schema.get("title", "")
    form.editor_description = schema.get("description", "")
    form.editor_pin_error = pin_error
    form.editor_custom_keys = []
    form.editor_visible_custom_keys = []
    instance = getattr(form, "instance", None)
    existing = {}
    if instance is not None and instance.pk:
        existing = form_editor.get_custom_answers(key, instance)
    core = OrderedDict((name, field) for name, field in form.fields.items() if name != TOKEN_FIELD and not name.startswith("custom_"))
    ordered = OrderedDict()
    for spec in schema.get("fields", []):
        name = spec["key"]
        custom = name not in core
        if custom:
            if not name.startswith("custom_"):
                continue
            field = _custom_field(spec, historical=bool(instance is not None and instance.pk and name not in existing))
            if isinstance(field, (forms.ChoiceField, forms.MultipleChoiceField)) and name in existing:
                saved_values = existing[name] if isinstance(existing[name], list) else [existing[name]]
                current_values = {value for value, label in field.choices}
                field.choices = list(field.choices) + [(value, f"{value} (retired)") for value in saved_values if value and value not in current_values]
            form.editor_custom_keys.append(name)
            form.initial[name] = existing.get(name)
        else:
            field = core.pop(name)
            _configure_allowed_values(form, name, field, spec, key)
        if custom or version is not None or explicit:
            field.label = spec.get("label", field.label)
            field.help_text = spec.get("help_text", field.help_text)
            field.widget.attrs["placeholder"] = spec.get("placeholder", field.widget.attrs.get("placeholder", ""))
        field.widget.attrs["data-form-label"] = str(field.label or name.replace("_", " ").capitalize())
        field.widget.attrs["data-form-order"] = str(len(ordered))
        field.widget.attrs["data-form-section"] = spec.get("section", "")
        field.widget.attrs["data-form-width"] = spec.get("width", "full")
        if custom:
            field.widget.attrs["data-form-custom"] = "true"
            visible = not spec.get("hidden", False) and not spec.get("archived", False)
            condition = spec.get("condition")
            if condition:
                field.widget.attrs["data-form-condition"] = json.dumps(condition)
                dependency = form.add_prefix(condition["field"])
                dependency_field = ordered.get(condition["field"]) or form.fields.get(condition["field"])
                dependency_spec = next((item for item in schema["fields"] if item["key"] == condition["field"]), {})
                multiple = isinstance(dependency_field, forms.MultipleChoiceField) or dependency_spec.get("type") in ("multiselect", "multiple_choice")
                if form.is_bound:
                    value = form.data.getlist(dependency) if multiple and hasattr(form.data, "getlist") else form.data.get(dependency)
                    if isinstance(dependency_field, forms.BooleanField) or dependency_spec.get("type") in ("checkbox", "boolean"):
                        value = forms.BooleanField(required=False).to_python(value)
                else:
                    value = form.initial.get(condition["field"], existing.get(condition["field"]))
                visible = visible and condition_matches(condition, value)
            field.widget.attrs["data-editor-required"] = "true" if field.required else "false"
            field.widget.attrs["data-form-archived"] = "true" if spec.get("hidden", False) or spec.get("archived", False) else "false"
            if not visible:
                field.required = False
                if form.is_bound:
                    field.disabled = True
                field.widget.attrs["data-form-hidden"] = "true"
            else:
                form.editor_visible_custom_keys.append(name)
        ordered[name] = field
    ordered.update(core)
    form.fields = ordered
    token = signing.dumps({"key": key, "version": version.pk if version else None}, salt=TOKEN_SALT)
    form.fields[TOKEN_FIELD] = forms.CharField(required=False, widget=forms.HiddenInput(), initial=token)
    form.initial[TOKEN_FIELD] = posted or token
    form._bound_fields_cache = {}
    form.editor_hidden_fields = [form[TOKEN_FIELD]]
    form.editor_custom_fields = [form[name] for name in form.editor_custom_keys]
    return form


def save_configured_instance(form, instance, **save_kwargs):
    from .form_editor import save_custom_answers
    with transaction.atomic():
        instance.save(**save_kwargs)
        save_custom_answers(form, instance)
    return instance


def editable_form(cls):
    original_init = cls.__init__
    original_clean = cls.full_clean
    original_save = getattr(cls, "save", None)

    @wraps(original_init)
    def initialize(self, *args, **kwargs):
        skip = kwargs.pop("_form_editor_skip", False)
        depth = getattr(self, "_editor_init_depth", 0)
        self._editor_init_depth = depth + 1
        try:
            original_init(self, *args, **kwargs)
        finally:
            self._editor_init_depth = depth
        if depth == 0 and not skip:
            configure_form(self)

    @wraps(original_clean)
    def full_clean(self):
        original_clean(self)
        if self.is_bound and getattr(self, "editor_pin_error", None):
            self.add_error(None, self.editor_pin_error)
        if self.is_bound and getattr(self, "editor_version", None) and not self.data.get(self.add_prefix(TOKEN_FIELD)):
            self.add_error(None, "This form has changed. Reload the page and submit again.")

    cls.__init__ = initialize
    cls.full_clean = full_clean
    if original_save:
        @wraps(original_save)
        def save(self, commit=True):
            from .form_editor import save_custom_answers
            if not commit:
                return original_save(self, commit=False)
            with transaction.atomic():
                instance = original_save(self, commit=True)
                save_custom_answers(self, instance)
                return instance
        cls.save = save
    return cls
