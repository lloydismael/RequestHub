"""Admin-only presentation editor; previews never bind or save business records."""
import json
from importlib import import_module

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.module_loading import import_string
from django.views import View

from .mixins import AdminRequiredMixin


def catalog():
    from .form_editor import FORM_CATEGORIES, get_registry

    forms = [dict(key=key, label=entry["label"],
                  group=entry["group"] if entry["group"] in FORM_CATEGORIES else "Others",
                  url=reverse("hub:form-editor-detail", args=[key]))
             for key, entry in get_registry().items()]
    return [{"name": name, "description": description,
             "forms": [form for form in forms if form["group"] == name]}
            for name, description in FORM_CATEGORIES.items()
            if any(form["group"] == name for form in forms)]


def _entry(key):
    from .form_editor import get_registry

    entry = get_registry().get(key)
    if entry is None:
        raise Http404("Unknown form.")
    return entry


def _state(key):
    from .form_editor import baseline_schema, get_definition, get_protected_fields, get_person_fields

    definition = get_definition(key)
    versions = definition.versions.select_related("actor").order_by("-number") if definition.pk else []
    history = [{"id": version.pk, "number": version.number,
                "created_at": version.created_at.isoformat(),
                "actor": str(version.actor) if version.actor else "System"}
               for version in versions]
    return {"schema": definition.draft or baseline_schema(key),
            "revision": definition.revision, "history": history,
            "published_version": definition.published_version_id,
            "status": ("Published" if definition.draft == definition.published_version.schema else "Draft")
                      if definition.published_version_id else ("Draft" if definition.pk else "Default"),
            "person_fields": get_person_fields(key),
            "protected_fields": get_protected_fields(key),
            "baseline_keys": [field["key"] for field in baseline_schema(key)["fields"]]}


def _payload(request):
    if request.content_type != "application/json":
        raise ValidationError("Send an application/json request.")
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValidationError("Invalid JSON request.")
    if not isinstance(payload, dict):
        raise ValidationError("Request must be a JSON object.")
    return payload


def _revision(payload):
    revision = payload.get("expected_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValidationError("A non-negative integer expected_revision is required.")
    return revision


class FormEditorCatalogView(AdminRequiredMixin, View):
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        return render(request, "hub/form_editor_catalog.html", {"form_catalog": catalog()})


class FormEditorDetailView(AdminRequiredMixin, View):
    http_method_names = ["get", "head", "options"]

    def get(self, request, key):
        entry = _entry(key)
        state = _state(key)
        state["endpoints"] = {action: reverse("hub:form-editor-" + action, args=[key])
                              for action in ("draft", "preview", "publish", "rollback", "reset", "history")}
        return render(request, "hub/form_editor.html", {"form_key": key,
                      "form_label": entry["label"], "editor_state": state})


def _people_state(key):
    from .form_editor import baseline_form, get_definition, get_person_fields, get_schema

    definition = get_definition(key)
    schema = get_schema(key)
    specs = {field["key"]: field for field in schema["fields"]}
    fields = {}
    for name, metadata in get_person_fields(key).items():
        if metadata["property"] == "person_ids":
            fields[name] = {**metadata, "values": specs[name].get("person_ids")}
    if key == "sqr_submission":
        form = baseline_form(key)
        groups = [{"value": str(value), "label": str(label)}
                  for value, label in form.fields["customer_company"].choices if value]
        defaults = [{"value": name, "label": name, "group": group}
                    for group, names in form.MEMBERS_BY_GROUP.items() for name in names]
        spec = specs["customer_contact"]
        values = spec.get("member_options", defaults)
        if "option_values" in spec:
            values = [option for option in values if option["value"] in spec["option_values"]]
        fields["customer_contact"] = {"label": spec["label"], "property": "member_options",
                                      "groups": groups, "choices": defaults, "values": values}
    has_draft = bool(definition.pk and definition.draft != schema)
    return {"revision": definition.revision, "status": "Published" if definition.published_version_id else "Default",
            "has_draft": has_draft, "fields": fields}


class FormEditorPeopleView(AdminRequiredMixin, View):
    http_method_names = ["get", "post", "head", "options"]

    def dispatch(self, request, *args, **kwargs):
        if kwargs.get("key") not in {"request", "sqr_submission"}:
            raise Http404("This form does not have a people directory.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, key):
        return JsonResponse(_people_state(key))

    def post(self, request, key):
        from copy import deepcopy
        from django.db import transaction
        from . import form_editor as service

        try:
            payload = _payload(request)
            revision = _revision(payload)
            with transaction.atomic():
                service._authorize(request.user)
                definition = service._locked_definition(key, revision)
                schema = service.get_schema(key)
                if definition.draft != schema:
                    raise service.RevisionConflict("An advanced-editor draft exists. Publish or reset it before changing people.")
                metadata = _people_state(key)["fields"]
                fields = payload.get("fields")
                if not isinstance(fields, dict) or set(fields) != set(metadata):
                    raise ValidationError("Send every supported people field and no other fields.")
                schema = deepcopy(schema)
                for spec in schema["fields"]:
                    name = spec["key"]
                    if name not in fields:
                        continue
                    property_name = metadata[name]["property"]
                    if property_name == "person_ids" and fields[name] is None:
                        spec.pop(property_name, None)
                    else:
                        spec[property_name] = fields[name]
                    if property_name == "member_options":
                        spec.pop("option_values", None)
                schema = service.validate_schema(key, schema)
                service._publish_schema(definition, schema, request.user, revision, "publish", source="people_directory")
            return JsonResponse(_people_state(key))
        except service.RevisionConflict as error:
            return JsonResponse({"error": " ".join(error.messages)}, status=409)
        except ValidationError as error:
            return JsonResponse({"error": " ".join(error.messages)}, status=400)


class FormEditorHistoryView(AdminRequiredMixin, View):
    http_method_names = ["get", "head", "options"]

    def get(self, request, key):
        _entry(key)
        return JsonResponse(_state(key))


class FormEditorActionView(AdminRequiredMixin, View):
    http_method_names = ["post", "options"]
    action = "draft"

    def post(self, request, key):
        from . import form_editor as service

        _entry(key)
        try:
            payload = _payload(request)
            revision = _revision(payload)
            if self.action == "draft":
                service.save_draft(key, payload.get("schema"), request.user, revision)
            elif self.action == "publish":
                service.publish(key, request.user, revision)
            elif self.action == "rollback":
                version_id = payload.get("version_id")
                if isinstance(version_id, bool) or not isinstance(version_id, int) or version_id < 1:
                    raise ValidationError("A valid version_id is required.")
                service.rollback(key, version_id, request.user, revision)
            elif self.action == "reset":
                service.reset_to_default(key, request.user, revision)
            return JsonResponse(_state(key))
        except service.RevisionConflict as error:
            return JsonResponse({"error": "This form changed in another session. Reload before saving.",
                                 "details": error.messages}, status=409)
        except ValidationError as error:
            return JsonResponse({"error": " ".join(error.messages), "details": error.messages}, status=400)


class FormEditorPreviewView(AdminRequiredMixin, View):
    http_method_names = ["post", "options"]

    def post(self, request, key):
        from .form_editor import validate_schema
        from .form_editor_runtime import configure_form

        entry = _entry(key)
        try:
            schema = validate_schema(key, _payload(request).get("schema"))
            form_class = entry["form_class"]
            if isinstance(form_class, str):
                form_class = import_string(form_class) if "." in form_class else getattr(import_module("hub.forms"), form_class)
            kwargs = {}
            if key == "request":
                kwargs = {"actor_role": request.user.role, "actor_user": request.user}
            elif key == "profile":
                kwargs = {"is_admin": True}
            if form_class.__name__ == "EngineerActivityLogForm":
                from accounts.models import User
                kwargs["engineer"] = User(role=User.Roles.ENGINEER)
            kwargs["_form_editor_skip"] = True
            form = form_class(**kwargs)
            configure_form(form, schema=schema)
            # Password fields are never rendered, even if a registry entry includes them.
            from django.forms import PasswordInput
            for name in list(form.fields):
                if isinstance(form.fields[name].widget, PasswordInput) or "password" in name.lower():
                    del form.fields[name]
            fields = {field["key"]: field for field in schema["fields"]}
            preview_fields = [{"bound": field, "section": fields.get(field.name, {}).get("section", ""),
                               "width": fields.get(field.name, {}).get("width", "full"),
                               "condition": fields.get(field.name, {}).get("condition")}
                              for field in form.visible_fields()]
            return render(request, "hub/form_editor_preview.html", {"preview_form": form,
                          "preview_fields": preview_fields, "schema": schema})
        except ValidationError as error:
            return JsonResponse({"error": " ".join(error.messages), "details": error.messages}, status=400)
