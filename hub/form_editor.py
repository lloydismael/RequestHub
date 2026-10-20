"""Allowlisted, versioned form presentation and custom-answer services."""
from contextvars import ContextVar
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import json
import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Max
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.encoding import force_str
from django.utils.html import strip_tags

from .models import FormAnswerSet, FormDefinition, FormEditorAuditEvent, FormVersion

_BUILDING_BASELINE = ContextVar("form_editor_building_baseline", default=False)
CUSTOM_TYPES = frozenset({"text", "textarea", "number", "date", "email", "url", "select", "multiselect", "checkbox"})
FIELD_PROPERTIES = frozenset({"key", "label", "help_text", "placeholder", "required", "hidden", "section", "width", "type", "choices", "condition", "person_ids", "option_values", "member_options"})
PERSON_FIELDS = {
    "request": {"engineer", "backup_engineer"},
    "request_admin": {"requestor", "engineer", "backup_engineer"},
    "sqr_submission": {"pm_esg_reviewer"},
    "sqr_delivery": {"assigned_pm", "assigned_sse"},
    "sqr_performance": {"project_manager"},
}
VALUE_FIELDS = {"sqr_submission": {"customer_contact"}}
SECURITY_FIELDS = frozenset({"username", "email", "role", "department", "password", "password1", "password2", "current_password", "new_password1", "new_password2"})


class RevisionConflict(ValidationError):
    pass


class SchemaValidationError(ValidationError):
    pass


def is_baseline_build():
    return _BUILDING_BASELINE.get()


FORM_CATEGORIES = {
    "Request": "Request creation, status, filter, and activity forms.",
    "SQR": "SQR submission, import, tracker, review, and revenue forms.",
    "Management": "Account and user administration forms.",
    "Others": "Personal profile and other forms.",
}


def get_registry():
    names = {
        "RequestForm": ("New Request", "Request"),
        "RequestAdminForm": ("Manage Request", "Request"),
        "RequestStatusForm": ("Request Status", "Request"),
        "StatusLogForm": ("Status Log", "Request"),
        "EngineerActivityLogForm": ("Engineer Activity Log", "Request"),
        "AdminRequestFilterForm": ("Request Filters", "Request"),
        "SqrSubmissionForm": ("New SQR Submission", "SQR"),
        "SqrImportForm": ("SQR Import", "SQR"),
        "SqrTrackerEditForm": ("SQR Tracker", "SQR"),
        "SqrReviewForm": ("SQR Review", "SQR"),
        "SqrRevenueQuotationForm": ("SQR Quotation", "SQR"),
        "SqrRevenueOrderForm": ("SQR Order", "SQR"),
        "SqrProposalStatusForm": ("SQR Proposal Status", "SQR"),
        "SqrDeliveryForm": ("SQR Delivery", "SQR"),
        "SqrPerformanceForm": ("SQR Performance", "SQR"),
        "SqrRevenueForm": ("SQR Revenue", "SQR"),
        "AccountManagementForm": ("Accounts", "Management"),
        "UserManagementForm": ("User Management", "Management"),
        "ProfileForm": ("Profile", "Others"),
    }
    registry = {}
    for name, (label, group) in names.items():
        key = re.sub(r"(?<!^)(?=[A-Z])", "_", name.removesuffix("Form")).lower()
        module = "accounts.forms" if name in {"ProfileForm", "UserManagementForm"} else "hub.forms"
        registry[key] = {"label": label, "group": group, "form_class": f"{module}.{name}"}
    return registry


def get_form_class(form_key):
    entry = get_registry().get(form_key)
    if entry is None:
        raise SchemaValidationError("Unknown or protected form.")
    return import_string(entry["form_class"])


def get_form_key(form):
    cls = form if isinstance(form, type) else type(form)
    for key in get_registry():
        if cls is get_form_class(key):
            return key
    return None


def baseline_form(form_key):
    cls = get_form_class(form_key)
    kwargs = {"_form_editor_skip": True}
    if form_key == "request":
        kwargs["actor_role"] = "admin"
    elif form_key == "profile":
        kwargs["is_admin"] = True
    elif form_key == "engineer_activity_log":
        kwargs["engineer"] = get_user_model()(role="engineer")
    token = _BUILDING_BASELINE.set(True)
    try:
        form = cls(**kwargs)
    finally:
        _BUILDING_BASELINE.reset(token)
    return form


def get_person_fields(form_key):
    """Expose only allowlisted business selectors, never account-management controls."""
    if form_key not in PERSON_FIELDS and form_key not in VALUE_FIELDS:
        return {}
    form = baseline_form(form_key)
    result = {}
    for key in PERSON_FIELDS.get(form_key, set()):
        field = form.fields.get(key)
        if isinstance(field, forms.ModelChoiceField) and field.queryset.model is get_user_model():
            result[key] = {"label": force_str(field.label), "property": "person_ids",
                           "choices": [{"value": str(user.pk), "label": force_str(field.label_from_instance(user))}
                                       for user in field.queryset]}
    for key in VALUE_FIELDS.get(form_key, set()):
        field = form.fields.get(key)
        if isinstance(field, forms.ChoiceField):
            result[key] = {"label": force_str(field.label), "property": "option_values",
                           "choices": [{"value": force_str(value), "label": force_str(label)}
                                       for value, label in field.choices if value]}
    return result


def baseline_schema(form_key):
    form = baseline_form(form_key)
    fields = []
    for key, field in form.fields.items():
        if key.startswith("_form_") or key.startswith("custom_"):
            continue
        fields.append({
            "key": key,
            "label": force_str(field.label if field.label is not None else key.replace("_", " ").capitalize()),
            "help_text": strip_tags(force_str(field.help_text)),
            "placeholder": force_str(field.widget.attrs.get("placeholder", "")),
            "required": bool(field.required),
            "hidden": bool(field.widget.is_hidden),
            "section": "",
            "width": "full",
        })
    return {"fields": fields, "title": get_registry()[form_key]["label"], "description": ""}


def get_protected_fields(form_key):
    baseline = baseline_schema(form_key)
    return {
        f["key"]: "Credential, identity and authorization controls cannot be customized."
        for f in baseline["fields"]
        if form_key in {"profile", "user_management"} and f["key"] in SECURITY_FIELDS
    }


def _text(value, name, limit):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise SchemaValidationError(f"Invalid {name} (maximum {limit} characters).")
    if "<" in value or ">" in value:
        raise SchemaValidationError(f"{name} must be plain text, not HTML.")
    return value


def validate_schema(form_key, schema):
    baseline = baseline_schema(form_key)
    if not isinstance(schema, dict) or set(schema) - {"fields", "title", "description"}:
        raise SchemaValidationError("Schema contains unsupported properties.")
    try:
        size = len(json.dumps(schema, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise SchemaValidationError("Schema must contain JSON values.")
    if size > 100000:
        raise SchemaValidationError("Schema exceeds 100 KB.")
    raw_fields = schema.get("fields")
    if not isinstance(raw_fields, list) or len(raw_fields) > 100:
        raise SchemaValidationError("Schema must contain at most 100 fields.")
    core = {f["key"]: f for f in baseline["fields"]}
    protected = {key for key in core if form_key in {"profile", "user_management"} and key in SECURITY_FIELDS}
    result = {"fields": [], "title": _text(schema.get("title", baseline["title"]), "title", 200),
              "description": _text(schema.get("description", ""), "description", 2000)}
    seen = set()
    selector_fields = get_person_fields(form_key) if any(
        isinstance(field, dict) and ("person_ids" in field or "option_values" in field)
        for field in raw_fields
    ) else {}
    published_fields = {}
    for published in FormVersion.objects.filter(definition__key=form_key).only("schema"):
        for old_field in published.schema.get("fields", []):
            published_fields.setdefault(old_field["key"], []).append(old_field)
    for raw in raw_fields:
        if not isinstance(raw, dict) or set(raw) - FIELD_PROPERTIES:
            raise SchemaValidationError("Field contains unsupported properties.")
        key = raw.get("key")
        if not isinstance(key, str) or key in seen:
            raise SchemaValidationError("Field keys must be unique strings.")
        seen.add(key)
        is_custom = key not in core
        if is_custom and not re.fullmatch(r"custom_[A-Za-z0-9][A-Za-z0-9_]{0,63}", key):
            raise SchemaValidationError("Unknown core field or invalid stable custom key.")
        field = deepcopy(core[key]) if not is_custom else {
            "key": key, "label": "", "help_text": "", "placeholder": "", "required": False,
            "hidden": False, "section": "", "width": "full",
        }
        field.update(deepcopy(raw))
        for name, limit in (("label", 200), ("help_text", 2000), ("placeholder", 200), ("section", 100)):
            _text(field[name], name, limit)
        if type(field["required"]) is not bool or type(field["hidden"]) is not bool:
            raise SchemaValidationError("Required and hidden must be booleans.")
        if not isinstance(field["width"], str) or field["width"] not in {"full", "half"}:
            raise SchemaValidationError("Width must be full or half.")
        for property_name in ("person_ids", "option_values"):
            if property_name not in raw:
                continue
            selector = selector_fields.get(key)
            if is_custom or not selector or selector["property"] != property_name:
                raise SchemaValidationError(f"Allowed values for {key} are protected.")
            values = raw[property_name]
            if not isinstance(values, list) or len(values) > 1000 or any(not isinstance(value, str) for value in values):
                raise SchemaValidationError("Allowed values must be a list of stable strings (maximum 1000).")
            if len(set(values)) != len(values):
                raise SchemaValidationError("Allowed values must be unique.")
            eligible = {choice["value"] for choice in selector["choices"]}
            historical = {value for old in published_fields.get(key, []) for value in old.get(property_name, [])}
            if not set(values).issubset(eligible | historical):
                raise SchemaValidationError(f"Select only eligible values for {key}.")
            if field["required"] and not values:
                raise SchemaValidationError(f"Select at least one allowed value for required field {key}.")
        if "member_options" in raw:
            if form_key != "sqr_submission" or key != "customer_contact":
                raise SchemaValidationError("Member options are only supported for SQR Account Manager.")
            if "option_values" in raw:
                raise SchemaValidationError("Use member options or allowed values, not both.")
            options = raw["member_options"]
            groups = {str(value) for value, _ in baseline_form(form_key).fields["customer_company"].choices if value}
            if not isinstance(options, list) or len(options) > 1000:
                raise SchemaValidationError("Member options must be a list (maximum 1000).")
            seen_members = set()
            for option in options:
                if not isinstance(option, dict) or set(option) != {"value", "label", "group"}:
                    raise SchemaValidationError("Member options require value, label and group.")
                name = _text(option["value"], "member name", 255)
                label = _text(option["label"], "member label", 255)
                group = option["group"]
                if not name.strip() or name != name.strip() or label != name:
                    raise SchemaValidationError("Member names must be nonempty trimmed plain text; label must match name.")
                if not isinstance(group, str) or group not in groups:
                    raise SchemaValidationError("Select a valid department for each member.")
                identity = (group, name.casefold())
                if identity in seen_members:
                    raise SchemaValidationError("Member names must be unique within each department.")
                seen_members.add(identity)
        if not is_custom:
            if any(field[name] != core[key][name] for name in ("required", "hidden")) or any(name in raw for name in ("type", "choices", "condition")):
                raise SchemaValidationError(f"Core field {key} validation and visibility are protected.")
            if key in protected and field != core[key]:
                raise SchemaValidationError(f"Security field {key} is protected.")
        else:
            if not isinstance(field.get("type"), str) or field["type"] not in CUSTOM_TYPES:
                raise SchemaValidationError("Unsupported custom field type.")
            choices = field.get("choices", [])
            if not isinstance(choices, list) or len(choices) > 100:
                raise SchemaValidationError("Invalid choices.")
            values = set()
            for choice in choices:
                if not isinstance(choice, dict) or set(choice) != {"value", "label"}:
                    raise SchemaValidationError("Choices require value and label only.")
                value = _text(choice["value"], "choice value", 100)
                _text(choice["label"], "choice label", 200)
                if not value or value in values:
                    raise SchemaValidationError("Choice values must be nonempty and unique.")
                values.add(value)
            if field["type"] in {"select", "multiselect"} and not choices:
                raise SchemaValidationError("Choice fields require choices.")
            if choices and field["type"] not in {"select", "multiselect"}:
                raise SchemaValidationError("Only choice fields accept choices.")
            field["choices"] = choices
            if any(old["type"] != field["type"] for old in published_fields.get(key, [])):
                raise SchemaValidationError("Published custom field types cannot change. Archive it and add a new field.")
        result["fields"].append(field)
    if not set(core).issubset(seen):
        raise SchemaValidationError("Core fields cannot be removed.")
    for index, field in enumerate(baseline["fields"]):
        if field["key"] in protected and result["fields"][index]["key"] != field["key"]:
            raise SchemaValidationError(f"Security field {field['key']} position is protected.")
    dependencies = {}
    for field in result["fields"]:
        if "condition" not in field:
            continue
        condition = field["condition"]
        if not isinstance(condition, dict) or set(condition) - {"field", "operator", "value"}:
            raise SchemaValidationError("Invalid condition.")
        target = condition.get("field")
        if not isinstance(target, str) or target not in seen or target == field["key"] or target in protected:
            raise SchemaValidationError("Condition references an invalid or protected field.")
        if not isinstance(condition.get("operator"), str) or condition["operator"] not in {"equals", "not_equals", "empty", "not_empty"}:
            raise SchemaValidationError("Unsupported condition operator.")
        _text(condition.get("value", ""), "condition value", 1000)
        dependencies[field["key"]] = target
    for key in dependencies:
        visited = set()
        while key in dependencies:
            if key in visited:
                raise SchemaValidationError("Conditions cannot contain cycles.")
            visited.add(key)
            key = dependencies[key]
    return result


def get_definition(form_key):
    get_form_class(form_key)
    definition = FormDefinition.objects.select_related("published_version").filter(key=form_key).first()
    return definition or FormDefinition(key=form_key, draft=baseline_schema(form_key), revision=0)


def get_published_version(form_key):
    return get_definition(form_key).published_version


def get_schema(form_key, published=True):
    definition = get_definition(form_key)
    if published:
        return deepcopy(definition.published_version.schema) if definition.published_version_id else baseline_schema(form_key)
    return deepcopy(definition.draft)


def _authorize(actor):
    if not actor or not actor.pk or not actor.is_active or actor.role != "admin":
        raise PermissionDenied("Only active Admin accounts may customize forms.")


def _expected(expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise RevisionConflict("A nonnegative expected revision is required.")


def _locked_definition(form_key, expected_revision):
    _expected(expected_revision)
    get_form_class(form_key)
    try:
        with transaction.atomic():
            definition, _ = FormDefinition.objects.get_or_create(key=form_key, defaults={"draft": baseline_schema(form_key)})
    except IntegrityError:
        definition = FormDefinition.objects.get(key=form_key)
    definition = FormDefinition.objects.select_for_update().get(pk=definition.pk)
    if definition.revision != expected_revision:
        raise RevisionConflict("The form was changed by another editor. Reload before saving.")
    return definition


def _update(definition, expected_revision, **values):
    count = FormDefinition.objects.filter(pk=definition.pk, revision=expected_revision).update(
        revision=F("revision") + 1, updated_at=timezone.now(), **values)
    if count != 1:
        raise RevisionConflict("The form was changed by another editor. Reload before saving.")
    definition.refresh_from_db()


def _audit(definition, actor, action, version=None, **details):
    FormEditorAuditEvent.objects.create(definition=definition, actor=actor, action=action,
                                      revision=definition.revision, version=version, details=details)


@transaction.atomic
def save_draft(form_key, schema, actor, expected_revision):
    _authorize(actor)
    schema = validate_schema(form_key, schema)
    definition = _locked_definition(form_key, expected_revision)
    _update(definition, expected_revision, draft=schema)
    _audit(definition, actor, "save_draft", schema=schema)
    return definition


def _publish_schema(definition, schema, actor, expected_revision, action, **details):
    number = (definition.versions.aggregate(number=Max("number"))["number"] or 0) + 1
    version = FormVersion.objects.create(definition=definition, number=number, schema=schema, actor=actor)
    _update(definition, expected_revision, draft=schema, published_version=version)
    _audit(definition, actor, action, version, **details)
    return definition


@transaction.atomic
def publish(form_key, actor, expected_revision):
    _authorize(actor)
    definition = _locked_definition(form_key, expected_revision)
    return _publish_schema(definition, validate_schema(form_key, definition.draft), actor, expected_revision, "publish")


@transaction.atomic
def rollback(form_key, version_id, actor, expected_revision):
    _authorize(actor)
    definition = _locked_definition(form_key, expected_revision)
    try:
        version = definition.versions.get(pk=version_id)
    except (FormVersion.DoesNotExist, ValueError, TypeError):
        raise SchemaValidationError("Version does not belong to this form.")
    return _publish_schema(definition, validate_schema(form_key, version.schema), actor, expected_revision, "rollback", source_version=version.pk)


@transaction.atomic
def reset_to_default(form_key, actor, expected_revision):
    _authorize(actor)
    definition = _locked_definition(form_key, expected_revision)
    return _publish_schema(definition, baseline_schema(form_key), actor, expected_revision, "reset")


def get_custom_answers(form_key, instance):
    cls = get_form_class(form_key)
    if not instance.pk:
        return {}
    if getattr(getattr(cls, "_meta", None), "model", None) is not type(instance):
        raise SchemaValidationError("Record type is not allowed for this form.")
    answer_set = FormAnswerSet.objects.filter(
        definition__key=form_key, content_type=ContentType.objects.get_for_model(instance), object_id=instance.pk,
    ).first()
    return deepcopy(answer_set.answers) if answer_set else {}


def _condition_matches(condition, value):
    operator = condition["operator"]
    if operator in {"empty", "not_empty"}:
        empty = value in (None, "", [], ())
        return empty if operator == "empty" else not empty
    expected = condition.get("value", "")
    equal = expected in [str(item) for item in value] if isinstance(value, (list, tuple)) else str(value if value is not None else "") == expected
    return equal if operator == "equals" else not equal


def _json_answer(value):
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_answer(item) for item in value]
    raise SchemaValidationError("Unsupported custom answer value.")


@transaction.atomic
def save_custom_answers(form, instance):
    key = getattr(form, "editor_key", None) or get_form_key(form)
    if not key or not instance.pk:
        return None
    cls = get_form_class(key)
    model = getattr(getattr(cls, "_meta", None), "model", None)
    if model is None or type(instance) is not model:
        raise SchemaValidationError("Record type is not allowed for this form.")
    version = getattr(form, "editor_version", getattr(form, "_form_editor_version", None))
    if version is not None:
        version = FormVersion.objects.select_related("definition").get(pk=version.pk)
        if version.definition.key != key:
            raise SchemaValidationError("Answer version belongs to another form.")
        schema = version.schema
    else:
        schema = baseline_schema(key)
    fields = {f["key"]: f for f in schema["fields"] if f["key"].startswith("custom_")}
    if not fields:
        return None
    if not hasattr(form, "cleaned_data") or form.errors:
        raise SchemaValidationError("Custom answers require a valid form.")
    definition = version.definition
    content_type = ContentType.objects.get_for_model(model)
    # Lock the business record even when no answer set exists yet.
    model._base_manager.select_for_update().get(pk=instance.pk)
    answer_set, _ = FormAnswerSet.objects.get_or_create(definition=definition, content_type=content_type, object_id=instance.pk)
    answers = deepcopy(answer_set.answers)
    configured = set(getattr(form, "editor_custom_keys", fields))
    visible = set(getattr(form, "editor_visible_custom_keys", fields)) & configured

    def is_active(field_key, visited=None):
        visited = set() if visited is None else visited
        if field_key in visited or field_key not in visible or fields[field_key]["hidden"]:
            return False
        condition = fields[field_key].get("condition")
        if not condition:
            return True
        target = condition["field"]
        if target in fields and not is_active(target, visited | {field_key}):
            return False
        return _condition_matches(condition, form.cleaned_data.get(target))

    for field_key, field in fields.items():
        if not is_active(field_key):
            continue
        if field_key in form.cleaned_data:
            answers[field_key] = _json_answer(form.cleaned_data[field_key])
    try:
        encoded = json.dumps(answers, allow_nan=False)
    except (TypeError, ValueError):
        raise SchemaValidationError("Custom answers must be valid JSON.")
    if len(encoded.encode("utf-8")) > 100000:
        raise SchemaValidationError("Custom answers exceed 100 KB.")
    answer_set.answers = answers
    answer_set.schema_version = version
    answer_set.full_clean()
    answer_set.save()
    return answer_set
