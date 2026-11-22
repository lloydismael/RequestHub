from typing import ClassVar

from django.http import JsonResponse
from django.views import View

from .mixins import AdminRequiredMixin
from .monitoring import app_snapshot, database_snapshot


class MonitoringView(AdminRequiredMixin, View):
    http_method_names: ClassVar[list[str]] = ["get"]

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return JsonResponse({"code": "forbidden"}, status=403)
        return JsonResponse({"code": "authentication_required"}, status=401)


class MonitoringLiveView(MonitoringView):
    def get(self, request):
        return JsonResponse({"app": app_snapshot(), "database": database_snapshot()})


class MonitoringPlatformView(MonitoringView):
    def get(self, request):
        from .monitoring_azure import platform_snapshot

        return JsonResponse(platform_snapshot())
