"""Development settings for the editable, loopback-only VS Code preview."""

from .settings import *

if DEBUG:
    INSTALLED_APPS = [*INSTALLED_APPS, "django_browser_reload"]
    ROOT_URLCONF = "request_hub.urls_live_debug"
    MIDDLEWARE = [
        *MIDDLEWARE[:1],
        "request_hub.urls_live_debug.BrowserReloadMiddleware",
        *MIDDLEWARE[1:],
    ]
