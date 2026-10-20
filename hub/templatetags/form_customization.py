"""Render editor additions without disturbing specialized form layouts."""
from django import template
from django.utils.html import format_html_join

register = template.Library()


@register.simple_tag
def form_editor_token(form):
    """Emit hidden fields once alongside a visible_fields loop, including row IDs."""
    if not form:
        return ""
    fields = {field.html_name: field for field in form.hidden_fields()}
    fields.update({field.html_name: field for field in getattr(form, "editor_hidden_fields", [])})
    return format_html_join("", "{}", ((field,) for field in fields.values()))


@register.filter
def form_editor_initially_hidden(field):
    attrs = field.field.widget.attrs
    return any(attrs.get(name) in (True, "true") for name in ("data-form-hidden", "data-form-archived"))


@register.inclusion_tag("hub/form_custom_fields.html")
def form_editor_extras(form):
    """Manual layouts already render their own core fields and hidden inputs."""
    return {
        "editor_hidden_fields": getattr(form, "editor_hidden_fields", []),
        "editor_custom_fields": getattr(form, "editor_custom_fields", []),
    }
