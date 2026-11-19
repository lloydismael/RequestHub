import re
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Max, Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from .models import Changelog, Notification
from .services.notifications import notification_summary, visible_notifications
from .view_helpers import ADMIN_PANEL_ROLES, MANILA_TZ, _safe_return_url


class NotificationToastDataView(LoginRequiredMixin, View):
    """Return new request/assignment events for the signed-in user's toast queue."""

    PAGE_SIZE = 50

    def get(self, request, *args, **kwargs):
        queryset = (
            Notification.objects.filter(
                recipient=request.user,
                event_type__in=[
                    Notification.EventType.NEW_REQUEST,
                    Notification.EventType.ASSIGNMENT,
                    Notification.EventType.MENTION,
                ],
                related_request__isnull=False,
            )
            .select_related("related_request")
            .order_by("id")
        )
        raw_cursor = request.GET.get("cursor")
        summary = notification_summary(request.user)
        if raw_cursor in (None, ""):
            baseline = queryset.aggregate(value=Max("id"))["value"] or 0
            return JsonResponse({"ok": True, "events": [], "next_cursor": baseline, "has_more": False, **summary})
        try:
            cursor = max(int(raw_cursor), 0)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid cursor"}, status=400)

        rows = list(queryset.filter(id__gt=cursor, is_read=False)[: self.PAGE_SIZE + 1])
        has_more = len(rows) > self.PAGE_SIZE
        rows = rows[: self.PAGE_SIZE]
        events = [
            {
                "id": item.pk,
                "type": item.event_type,
                "request_id": item.related_request_id,
                "reference_code": item.related_request.reference_code,
                "message": item.message,
                "created_at": item.created_at.isoformat(),
                "manage_url": reverse("hub:notification-follow", args=[item.pk]),
            }
            for item in rows
        ]
        return JsonResponse(
            {
                "ok": True,
                "events": events,
                "next_cursor": rows[-1].pk if rows else cursor,
                "has_more": has_more,
                **summary,
            }
        )


class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = "hub/notifications.html"
    context_object_name = "notifications"
    paginate_by = 50

    def get(self, request, *args, **kwargs):
        if request.GET.get("tab") != "changelogs":
            return super().get(request, *args, **kwargs)
        base_qs = visible_notifications(request.user)
        changelog_qs = Changelog.objects.filter(
            is_published=True, release_date__lte=timezone.localdate(),
        )
        page = Paginator(changelog_qs, 10).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "active_tab": "changelogs", "changelogs": page, "page_obj": page,
            "is_paginated": page.has_other_pages(),
            "unread_count": base_qs.filter(is_read=False).count(), "total_count": base_qs.count(),
            "changelog_count": changelog_qs.count(),
        })

    def get_queryset(self):
        queryset = visible_notifications(self.request.user).select_related(
            "related_request", "related_request__account"
        ).order_by("-created_at")

        # ── Filter: read/unread (default to unread for the notification dashboard) ──
        self._filter_read = self.request.GET.get("filter", "unread")
        if self._filter_read == "unread":
            queryset = queryset.filter(is_read=False)
        elif self._filter_read == "read":
            queryset = queryset.filter(is_read=True)

        # ── Filter: category ──
        self._filter_category = self.request.GET.get("category", "")
        if self._filter_category:
            # We can't directly filter on computed properties, so filter on message/source patterns
            cat = self._filter_category
            if cat == "mention":
                queryset = queryset.filter(event_type=Notification.EventType.MENTION)
            elif cat == "new_request":
                queryset = queryset.filter(source__icontains="new request")
            elif cat == "assignment":
                queryset = queryset.filter(message__icontains="assigned to request")
            elif cat == "update":
                queryset = queryset.filter(
                    Q(source__icontains="status update") |
                    Q(message__icontains="posted an update") |
                    Q(message__icontains=" updated ")
                )
            elif cat == "completion":
                queryset = queryset.filter(
                    Q(message__icontains="completed") |
                    Q(message__icontains="closed") |
                    Q(source__icontains="close")
                )
            elif cat == "reminder":
                queryset = queryset.filter(
                    Q(source__icontains="nudge") |
                    Q(message__icontains="reminder")
                )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        base_qs = visible_notifications(user)
        context["unread_count"] = base_qs.filter(is_read=False).count()
        context["total_count"] = base_qs.count()
        context["changelog_count"] = Changelog.objects.filter(
            is_published=True, release_date__lte=timezone.localdate(),
        ).count()
        context["current_filter"] = self._filter_read
        context["current_category"] = self._filter_category

        # ── Category breakdown for the summary bar ──
        category_counts = {
            "new_request": base_qs.filter(source__icontains="new request").count(),
            "assignment": base_qs.filter(message__icontains="assigned to request").count(),
            "update": base_qs.filter(
                Q(source__icontains="status update") |
                Q(message__icontains="posted an update") |
                Q(message__icontains=" updated ")
            ).count(),
            "completion": base_qs.filter(
                Q(message__icontains="completed") |
                Q(message__icontains="closed") |
                Q(source__icontains="close")
            ).count(),
            "reminder": base_qs.filter(
                Q(source__icontains="nudge") |
                Q(message__icontains="reminder")
            ).count(),
        }
        context["category_counts"] = category_counts

        # ── Date-grouped notifications for timeline display ──
        notifications_list = list(context["notifications"])
        context["grouped_notifications"] = self._group_by_date(notifications_list)

        return context

    def _group_by_date(self, notifications):
        """Group notifications by Today, Yesterday, This Week, Older."""
        now = timezone.now().date()
        groups = {"Today": [], "Yesterday": [], "This Week": [], "Older": []}

        for n in notifications:
            created_date = timezone.localtime(n.created_at, MANILA_TZ).date()
            if created_date == now:
                groups["Today"].append(n)
            elif created_date == now - timedelta(days=1):
                groups["Yesterday"].append(n)
            elif (now - created_date).days < 7:
                groups["This Week"].append(n)
            else:
                groups["Older"].append(n)

        return {k: v for k, v in groups.items() if v}


class NotificationReadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        notification.mark_read()
        return HttpResponseRedirect(_safe_return_url(request, "hub:notifications"))


class NotificationFollowRedirectView(LoginRequiredMixin, View):
    def get(self, request, pk):
        notification = get_object_or_404(
            Notification.objects.select_related("related_request"),
            pk=pk,
            recipient=request.user,
        )
        notification.mark_read()
        if notification.related_request:
            req = notification.related_request
            # Route to manage views instead of read-only detail.
            route = "hub:request-manage" if request.user.role in ADMIN_PANEL_ROLES else "hub:request-manage-collab"
            destination = reverse(route, args=[req.pk])
            if notification.event_type == Notification.EventType.MENTION:
                match = re.fullmatch(r"update:(\d+):mention:\d+", notification.event_key)
                if match:
                    destination += f"#update-{match.group(1)}"
            return HttpResponseRedirect(destination)
        return redirect("hub:notifications")


class NotificationDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        notification.delete()
        return HttpResponseRedirect(_safe_return_url(request, "hub:notifications"))


class NotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        request.user.notifications.filter(is_read=False).update(is_read=True)
        return HttpResponseRedirect(_safe_return_url(request, "hub:notifications"))
