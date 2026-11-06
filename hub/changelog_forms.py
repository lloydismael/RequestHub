from copy import deepcopy

from django import forms
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models.fields.files import FieldFile

from .models import Changelog, changelog_image_upload_to
from .release_changelog import COMPARISON_FIELDS, COMPARISON_META_FIELDS, EVIDENCE_POLICY, MAX_COMPARISONS, SHA_RE, TEXT_MAX, missing_comparisons


class ScreenshotField(forms.ImageField):
    def to_python(self, data):
        if data and data.size > 5 * 1024 * 1024:
            raise ValidationError("Screenshots must be 5 MB or smaller.")
        image = super().to_python(data)
        if image and image.image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValidationError("Upload a PNG, JPEG or WebP screenshot.")
        return image


class ChangelogForm(forms.ModelForm):
    before_image = ScreenshotField(required=False, label="Before screenshot")
    after_image = ScreenshotField(required=False, label="After screenshot")
    publish_when_ready = forms.BooleanField(
        required=False, label="Automatically publish when every change has screenshot evidence",
        help_text="Clear this to hold the draft even after automatic capture succeeds.",
    )

    class Meta:
        model = Changelog
        fields = (
            "title", "previous_version", "new_version", "release_date", "summary",
            "before_text", "before_image", "after_text", "after_image", "is_published", "publish_when_ready",
        )
        labels = {
            "before_text": "Before description", "after_text": "After description",
            "is_published": "Publish to Notifications",
        }
        widgets = {
            "release_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "summary": forms.Textarea(attrs={"rows": 3}),
            "before_text": forms.Textarea(attrs={"rows": 4}),
            "after_text": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, comparison_page=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._comparison_specs = []
        self._comparison_sources = {}
        self.comparison_groups = []
        self._require_all = self.instance.requires_all_evidence or (
            self.instance.is_automatic and self.is_bound
            and self.fields["publish_when_ready"].widget.value_from_datadict(self.data, self.files, "publish_when_ready")
        )
        if self.instance.is_automatic:
            self.fields["publish_when_ready"].initial = self.instance.publish_when_ready
        else:
            self.fields.pop("publish_when_ready")
        commits = self.instance.changes.get("commits", []) if isinstance(self.instance.changes, dict) else []
        if not isinstance(commits, list):
            commits = []
        editable = []
        for commit_index, item in enumerate(commits):
            if not isinstance(item, dict):
                continue
            pairs = item.get("comparisons", [])
            pairs = [pair for pair in pairs if isinstance(pair, dict)] if isinstance(pairs, list) else []
            if not self._require_all and not item.get("requires_comparison") and not pairs:
                continue
            editable.append((commit_index, item, pairs))
        # Two changes with 20 pairs each stay below Django's field/file upload limits.
        self.comparison_page = Paginator(editable, 2).get_page(comparison_page)
        for commit_index, item, pairs in self.comparison_page:
            group = {
                "title": item.get("title", "Change"), "required": bool(self._require_all or item.get("requires_comparison")),
                "capture_error": item.get("capture_error", ""), "pairs": [],
            }
            slots = pairs + ([{}] if len(pairs) < MAX_COMPARISONS else [])
            for pair_index, pair in enumerate(slots):
                names = {}
                initial = {}
                comparison_fields = COMPARISON_FIELDS + (("before_revision", "after_revision") if self._require_all else ())
                for field in comparison_fields:
                    name = f"comparison_{commit_index}_{pair_index}_{field}"
                    names[field] = name
                    value = pair.get(field, "")
                    value = value if isinstance(value, str) else ""
                    if field.endswith("_image"):
                        image_field = self.instance._meta.get_field(field)
                        value = FieldFile(self.instance, image_field, value) if value else None
                        form_field = ScreenshotField(required=False, label=field.replace("_", " ").capitalize(), initial=value)
                        form_field.widget.attrs["accept"] = "image/png,image/jpeg,image/webp"
                        form_field.help_text = "PNG, JPEG or WebP, up to 5 MB."
                    elif field.endswith("_revision"):
                        value = value or item.get("parent_sha" if field == "before_revision" else "sha", "")
                        form_field = forms.CharField(
                            required=False, label=field.replace("_", " ").capitalize(), initial=value,
                            max_length=40, help_text="Exact Git revision represented by this screenshot.",
                        )
                    else:
                        if field == "title" and not pairs and pair_index == 0:
                            value = group["title"]
                        form_field = forms.CharField(
                            required=False, label=field.replace("_", " ").capitalize(), initial=value,
                            max_length=200 if field == "title" else TEXT_MAX,
                            widget=forms.TextInput() if field == "title" else forms.Textarea(attrs={"rows": 3}),
                        )
                    self.fields[name] = form_field
                    initial[field] = value
                self._comparison_specs.append((commit_index, names, initial))
                self._comparison_sources[names["title"]] = pair
                group["pairs"].append({"fields": [self[name] for name in names.values()], "additional": bool(pairs) and not pair})
            self.comparison_groups.append(group)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"
            )
        for name in ("before_image", "after_image"):
            self.fields[name].widget.attrs["accept"] = "image/png,image/jpeg,image/webp"
            self.fields[name].help_text = "PNG, JPEG or WebP, up to 5 MB."
            if not self.instance.is_automatic:
                self.fields[name].help_text += " Required for publishing."
        if self.instance.is_automatic:
            self.fields["previous_version"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("previous_version") == cleaned.get("new_version") and cleaned.get("new_version"):
            self.add_error("new_version", "The new version must differ from the previous version.")
        names = ("before_text", "after_text", "before_image", "after_image")
        publishing = cleaned.get("is_published")
        if self.instance.is_published and not publishing:
            cleaned["publish_when_ready"] = False
        if publishing and (not self.instance.is_automatic or any(cleaned.get(name) for name in names)):
            for name in names:
                if name not in self.errors and not cleaned.get(name):
                    self.add_error(name, "This field is required before publishing.")
        self._pending_comparisons = {}
        for commit_index, fields, initial in self._comparison_specs:
            values = {}
            for field, name in fields.items():
                omitted = self.is_bound and name not in self.data and name not in self.files and f"{name}-clear" not in self.data
                values[field] = initial[field] if omitted else cleaned.get(name)
            has_content = any(values[field] for field in COMPARISON_FIELDS if field != "title")
            if not has_content and not (values["title"] and values["title"] != initial["title"]):
                continue
            source = self._comparison_sources[fields["title"]]
            if all(isinstance(values[field], FieldFile) and values[field].name == source.get(field) for field in ("before_image", "after_image")):
                values.update({field: source[field] for field in COMPARISON_META_FIELDS if field not in fields and isinstance(source.get(field), str)})
            elif self._require_all and has_content:
                values["capture_note"] = "Screenshots supplied manually for the declared revisions."
            if self._require_all:
                item = self.instance.changes["commits"][commit_index]
                for field, expected in (("before_revision", item.get("parent_sha")), ("after_revision", item.get("sha"))):
                    value = values.get(field, "")
                    if value and (not SHA_RE.fullmatch(value) or value != expected):
                        self.add_error(fields[field], "Use the exact change revision and its first parent.")
            if publishing:
                for field, name in fields.items():
                    if name not in self.errors and not values[field]:
                        self.add_error(name, "This field is required before publishing.")
            self._pending_comparisons.setdefault(commit_index, []).append(values)
        if self._comparison_specs:
            changes = deepcopy(self.instance.changes)
            for commit_index, _fields, _initial in self._comparison_specs:
                changes["commits"][commit_index]["comparisons"] = [
                    {field: getattr(value, "name", value) or "" for field, value in pair.items()}
                    for pair in self._pending_comparisons.get(commit_index, [])
                ]
                candidate = {key: value for key, value in changes["commits"][commit_index].items() if key != "capture_error"}
                if not missing_comparisons([candidate], require_all=self._require_all):
                    changes["commits"][commit_index].pop("capture_error", None)
            if self._require_all:
                changes["evidence_policy"] = EVIDENCE_POLICY
            self.instance.changes = changes
            missing = missing_comparisons(changes["commits"], require_all=self._require_all)
            if missing and publishing:
                self.add_error(None, "Changes require before and after screenshots with descriptions: " + "; ".join(missing))
        return cleaned

    def save(self, commit=True):
        if self.instance.is_automatic:
            intent = self.cleaned_data.get("publish_when_ready", False)
            if intent != self.instance.publish_when_ready:
                self.instance.publish_when_ready = intent
        return super().save(commit=commit)

    def save_comparisons(self):
        if not self._comparison_specs:
            return
        changes = deepcopy(self.instance.changes)
        storage = self.instance._meta.get_field("before_image").storage
        for commit_index in {index for index, _fields, _initial in self._comparison_specs}:
            pairs = []
            for values in self._pending_comparisons.get(commit_index, []):
                pair = dict(values)
                for field in ("before_image", "after_image"):
                    image = pair[field]
                    if isinstance(image, FieldFile):
                        pair[field] = image.name
                    elif image:
                        pair[field] = storage.save(changelog_image_upload_to(self.instance, image.name), image)
                    else:
                        pair[field] = ""
                pairs.append({field: value or "" for field, value in pair.items()})
            changes["commits"][commit_index]["comparisons"] = pairs
        self.instance.changes = changes
        if self.instance.publish_when_ready:
            self.instance.is_published = not self.instance.missing_comparisons
        self.instance.clean()
        self.instance.save(update_fields=["changes", "is_published", "updated_at"])
