import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse
from django.urls import include, path
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from accounts.views import RoleLoginView


@never_cache
@require_GET
def healthz(_request):
    return HttpResponse("ok", content_type="text/plain; charset=utf-8")


@never_cache
@require_GET
def release_info(_request):
    return JsonResponse({
        "version": settings.APP_VERSION,
        "revision": os.getenv("APP_REVISION", "unknown"),
    })


@never_cache
@require_GET
def readyz(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"ready": False}, status=503)
    return JsonResponse({"ready": True})


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("release/", release_info, name="release_info"),
    path("admin/", admin.site.urls),
    path("", RoleLoginView.as_view(), name="landing"),
    path("accounts/login/", RoleLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/", include("accounts.urls")),
    path("", include(("hub.urls", "hub"), namespace="hub")),
]

serve_media = settings.DEBUG or os.getenv("DJANGO_SERVE_MEDIA", "false").lower() == "true"

if serve_media:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
