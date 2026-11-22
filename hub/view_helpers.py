from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .mixins import ADMIN_PANEL_ROLES as ADMIN_PANEL_ROLES  # noqa: PLC0414 - Legacy re-export.
from .models import MANILA_TZ as MANILA_TZ  # noqa: PLC0414 - Legacy re-export.


def _safe_return_url(request, fallback_name):
    target = request.META.get("HTTP_REFERER", "").strip()
    if url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return reverse(fallback_name)
