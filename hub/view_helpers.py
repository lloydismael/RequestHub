from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import User

from .models import MANILA_TZ as MANILA_TZ

ADMIN_PANEL_ROLES = {User.Roles.ADMIN, User.Roles.PM_ESG}


def _safe_return_url(request, fallback_name):
    target = request.META.get("HTTP_REFERER", "").strip()
    if url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return reverse(fallback_name)
