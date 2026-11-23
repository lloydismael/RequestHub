from django.urls import include, path, reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django_browser_reload.middleware import BrowserReloadMiddleware as BaseBrowserReloadMiddleware
from django_browser_reload.views import events

from .urls import urlpatterns as app_urls

urlpatterns = [path("__reload__/", include("django_browser_reload.urls")), *app_urls]

_reload_url = reverse_lazy("django_browser_reload:events")
_reload_stream = never_cache(require_GET(events))


class BrowserReloadMiddleware(BaseBrowserReloadMiddleware):
    async_capable = False

    def __call__(self, request):
        if request.path == _reload_url:
            # Keep long-lived reload connections out of the database-backed session middleware.
            request.get_host()
            return _reload_stream(request)
        if request.method != "GET":
            return self.get_response(request)
        return super().__call__(request)
