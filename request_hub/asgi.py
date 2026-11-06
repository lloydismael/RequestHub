import os

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "request_hub.settings")

application = get_asgi_application()

if settings.AZURE_TELEMETRY_ENABLED:
    from request_hub.telemetry import configure_telemetry

    configure_telemetry()
