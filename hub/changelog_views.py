from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.dateformat import format as format_date
from django.views import View

from .mixins import AdminRequiredMixin
from .models import Changelog


def published_changelogs():
    return Changelog.objects.filter(is_published=True, release_date__lte=timezone.localdate())


def _history_url(request, entry=None):
    query = {"tab": "changelogs"}
    try:
        page = int(request.GET.get("page", "1"))
    except ValueError:
        page = 1
    if page > 1:
        query["page" if entry else "changelog_page"] = page
    if entry:
        return reverse("hub:notifications", query=query, fragment=f"changelog-{entry.pk}-title")
    return reverse("hub:management", query=query)


def _render_preview(request, context, *, status=200):
    template = "hub/changelog_preview.html"
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        response = render(None, f"{template}#preview", context, status=status)
    else:
        response = render(request, template, context, status=status)
    patch_cache_control(response, private=True, no_store=True)
    patch_vary_headers(response, ("X-Requested-With",))
    return response


class ChangelogAdminPreviewView(AdminRequiredMixin, View):
    def get(self, request, pk):
        entry = get_object_or_404(Changelog, pk=pk)
        return _render_preview(request, {"entry": entry, "admin_preview": True, "back_url": _history_url(request)})


class ChangelogComparisonView(LoginRequiredMixin, View):
    def get(self, request, pk, section, index):
        if section not in {"visual", "technical"} or index < 1:
            raise Http404
        entry = get_object_or_404(published_changelogs(), pk=pk)
        context = {"entry": entry, "back_url": _history_url(request, entry)}
        # Evidence can move between groups when the release is edited.
        if request.GET.get("revision") != format_date(entry.updated_at, "Uu"):
            return _render_preview(request, {**context, "stale": True}, status=409)
        comparisons = entry.comparison_groups[section]
        if index > len(comparisons):
            raise Http404
        return _render_preview(request, {
            **context, "comparison": comparisons[index - 1], "comparison_index": index,
            "technical": section == "technical",
        })
