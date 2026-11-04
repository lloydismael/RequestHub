from django import forms
from django.core.exceptions import ValidationError

from .models import Changelog


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

    class Meta:
        model = Changelog
        fields = (
            "title", "previous_version", "new_version", "release_date", "summary",
            "before_text", "before_image", "after_text", "after_image", "is_published",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        if self.instance.is_automatic and not any(cleaned.get(name) for name in names):
            return cleaned
        if cleaned.get("is_published"):
            for name in names:
                if name not in self.errors and not cleaned.get(name):
                    self.add_error(name, "This field is required before publishing.")
        return cleaned
