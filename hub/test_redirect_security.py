from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from accounts.models import User
from hub.models import Request
from hub.views import (
    NotificationDeleteView,
    NotificationMarkAllReadView,
    NotificationReadView,
    RequestAdminUpdateView,
    RequestClosingOutlookRedirectView,
    RequestCollaborativeManageView,
    RequestOutlookRedirectView,
    RequestTeamsRedirectView,
    _safe_return_url,
)


@override_settings(ALLOWED_HOSTS=["testserver", "other.example"], CSRF_TRUSTED_ORIGINS=[])
class RedirectSecurityTests(SimpleTestCase):
    unsafe_targets = (
        "", "https://outside.example/path", "//outside.example/path",
        "///outside.example/path", r"/\outside.example/path",
        r"https://testserver\@outside.example/path",
        "javascript:alert(1)", "https:///outside.example/path",
        "https://testserver.outside.example/path", "https://other.example/path",
        "https://testserver:8443/path", "http://testserver/path", "//testserver/path",
    )

    def make_request(self, referer=None, secure=True):
        headers = {} if referer is None else {"HTTP_REFERER": referer}
        request = RequestFactory().post("/", secure=secure, **headers)
        request.user = SimpleNamespace(
            role=User.Roles.ADMIN, pk=1, email="admin@example.com",
            notifications=Mock(), is_authenticated=True,
        )
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_unsafe_or_missing_return_destinations_use_fallback(self):
        for target in (None, *self.unsafe_targets):
            with self.subTest(target=target):
                self.assertEqual(
                    _safe_return_url(self.make_request(target), "hub:dashboard"),
                    reverse("hub:dashboard"),
                )

    def test_safe_return_destinations_preserve_filters_and_fragments(self):
        for target in (
            "/dashboard/?status=open#requests",
            "https://testserver/dashboard/?status=open#requests",
            " /dashboard/ ",
        ):
            with self.subTest(target=target):
                self.assertEqual(
                    _safe_return_url(self.make_request(target), "hub:dashboard"),
                    target.strip(),
                )
        self.assertEqual(
            _safe_return_url(self.make_request("http://testserver/path", secure=False), "hub:dashboard"),
            "http://testserver/path",
        )

    def test_notification_actions_validate_redirect_and_keep_side_effects(self):
        for view in (NotificationReadView, NotificationDeleteView, NotificationMarkAllReadView):
            for target in (None, *self.unsafe_targets, "/notifications/?category=assigned"):
                with self.subTest(view=view.__name__, target=target):
                    request = self.make_request(target)
                    notification = Mock()
                    with patch("hub.views.get_object_or_404", return_value=notification):
                        kwargs = {} if view is NotificationMarkAllReadView else {"pk": 2}
                        response = view().post(request, **kwargs)
                    expected = target if target == "/notifications/?category=assigned" else reverse("hub:notifications")
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response["Location"], expected)
                    if view is NotificationReadView:
                        notification.mark_read.assert_called_once_with()
                    elif view is NotificationDeleteView:
                        notification.delete.assert_called_once_with()
                    else:
                        request.user.notifications.filter.assert_called_once_with(is_read=False)
                        request.user.notifications.filter.return_value.update.assert_called_once_with(is_read=True)

    def request_object(self):
        return SimpleNamespace(
            pk=2, status=Request.Status.COMPLETED,
            engineer=SimpleNamespace(email="engineer@example.com"), backup_engineer=None,
            requestor=SimpleNamespace(email="requestor@example.com", role=User.Roles.REQUESTOR),
            teams_chat_url="https://teams.microsoft.com/l/chat/0/0?users=engineer@example.com",
        )

    def test_communication_error_branches_use_safe_redirect(self):
        scenarios = (
            (RequestOutlookRedirectView, "unassigned"),
            (RequestOutlookRedirectView, "duplicate"),
            (RequestOutlookRedirectView, "missing_email"),
            (RequestClosingOutlookRedirectView, "not_completed"),
            (RequestClosingOutlookRedirectView, "unassigned"),
            (RequestClosingOutlookRedirectView, "missing_requestor"),
            (RequestClosingOutlookRedirectView, "missing_engineer"),
            (RequestTeamsRedirectView, "forbidden_role"),
            (RequestTeamsRedirectView, "unassigned"),
            (RequestTeamsRedirectView, "duplicate"),
            (RequestTeamsRedirectView, "pm_wrong_requestor"),
            (RequestTeamsRedirectView, "pm_missing_engineer"),
            (RequestTeamsRedirectView, "pm_missing_requestor"),
            (RequestTeamsRedirectView, "pm_duplicate"),
            (RequestTeamsRedirectView, "missing_teams_url"),
        )
        for view, scenario in scenarios:
            for target in ("https://outside.example/path", "/dashboard/?status=open"):
                with self.subTest(view=view.__name__, scenario=scenario, target=target):
                    request = self.make_request(target)
                    obj = self.request_object()
                    duplicate = scenario in {"duplicate", "pm_duplicate"}
                    if scenario == "unassigned":
                        request.user.role = User.Roles.ENGINEER
                    elif scenario == "duplicate":
                        request.user.role = User.Roles.ENGINEER
                        obj.engineer = request.user
                    elif scenario == "missing_email":
                        obj.engineer.email = ""
                    elif scenario == "not_completed":
                        obj.status = Request.Status.ONGOING
                    elif scenario == "missing_requestor":
                        obj.requestor.email = ""
                    elif scenario == "missing_engineer":
                        obj.engineer.email = ""
                        request.user.email = ""
                    elif scenario == "forbidden_role":
                        request.user.role = User.Roles.REQUESTOR
                    elif scenario.startswith("pm_"):
                        request.user.role = User.Roles.PM_ESS
                        if scenario != "pm_wrong_requestor":
                            obj.requestor.role = User.Roles.REQUESTOR_ESS
                        if scenario == "pm_missing_engineer":
                            obj.engineer.email = ""
                        elif scenario == "pm_missing_requestor":
                            obj.requestor.email = ""
                    elif scenario == "missing_teams_url":
                        obj.teams_chat_url = ""
                    with patch("hub.views.get_object_or_404", return_value=obj), patch(
                        "hub.views.RequestCommunication.objects.filter"
                    ) as communication_filter:
                        communication_filter.return_value.exists.return_value = duplicate
                        response = view().post(request, obj.pk)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(
                        response["Location"],
                        target if target.startswith("/dashboard/") else reverse("hub:dashboard"),
                    )

    def test_teams_success_preserves_launch_url(self):
        for target in ("https://outside.example/path", "/dashboard/?status=open"):
            request = self.make_request(target)
            obj = self.request_object()
            with patch("hub.views.get_object_or_404", return_value=obj), patch(
                "hub.views.RequestCommunication.objects.create"
            ), patch("hub.views.render", return_value=HttpResponse("OK")) as render:
                response = RequestTeamsRedirectView().post(request, obj.pk)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(render.call_args.args[2]["teams_url"], obj.teams_chat_url)

    def test_status_log_redirects_use_named_routes_not_incoming_path(self):
        for view_class, route in (
            (RequestAdminUpdateView, "hub:request-manage"),
            (RequestCollaborativeManageView, "hub:request-manage-collab"),
        ):
            with self.subTest(view=view_class.__name__):
                request = self.make_request()
                request.path = "//outside.example/path"
                obj = SimpleNamespace(pk=2)
                view = view_class()
                view.request = request
                view.object = obj
                with patch("hub.views.StatusLogForm") as form, patch(
                    "hub.views.save_configured_instance"
                ), patch("hub.views.notify_status_update"):
                    form.return_value.is_valid.return_value = True
                    if view_class is RequestAdminUpdateView:
                        response = view._handle_status_log_post(request)
                    else:
                        response = view._handle_status_log_post(request, obj)
                self.assertEqual(response["Location"], reverse(route, args=[obj.pk]))

    def test_unauthorized_status_update_redirect_uses_named_route(self):
        request = self.make_request()
        request.path = "//outside.example/path"
        request.user.role = User.Roles.ENGINEER
        obj = SimpleNamespace(pk=2, engineer_id=3)
        response = RequestCollaborativeManageView()._handle_status_update(request, obj)
        self.assertEqual(response["Location"], reverse("hub:request-manage-collab", args=[obj.pk]))

    def test_csrf_rejects_external_origin_and_referer(self):
        for headers in (
            {"HTTP_ORIGIN": "https://outside.example", "HTTP_REFERER": "https://testserver/"},
            {"HTTP_REFERER": "https://outside.example/path"},
        ):
            request = RequestFactory().post("/notifications/mark-all-read/", secure=True, **headers)
            request.POST = request.POST.copy()
            request.POST["csrfmiddlewaretoken"] = get_token(request)
            request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
            middleware = CsrfViewMiddleware(lambda request: HttpResponse("OK"))
            middleware.process_request(request)
            response = middleware.process_view(request, NotificationMarkAllReadView.as_view(), (), {})
            self.assertEqual(response.status_code, 403)

    def test_csrf_accepts_same_origin_with_valid_token(self):
        request = self.make_request("https://testserver/notifications/")
        request.POST = request.POST.copy()
        request.POST["csrfmiddlewaretoken"] = get_token(request)
        request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
        middleware = CsrfViewMiddleware(lambda request: HttpResponse("OK"))
        middleware.process_request(request)
        self.assertIsNone(middleware.process_view(request, NotificationMarkAllReadView.as_view(), (), {}))
