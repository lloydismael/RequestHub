import os

from django.conf import settings
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "request_hub.settings")

application = get_wsgi_application()

if settings.AZURE_TELEMETRY_ENABLED:
    from request_hub.telemetry import configure_telemetry

    configure_telemetry()
