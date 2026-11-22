"""Shared request hub view constants and response helpers."""

import logging
from typing import Any

from accounts.models import User
from hub.mixins import ASSIGNABLE_ENGINEER_ROLES as ASSIGNABLE_ENGINEER_ROLES  # noqa: PLC0414 - Legacy re-export.
from hub.mixins import ENGINEER_ACCESS_ROLES as ENGINEER_ACCESS_ROLES  # noqa: PLC0414 - Legacy re-export.
from hub.services.request_changes import (
    create_change_status_log as create_change_status_log,  # noqa: PLC0414 - Legacy re-export.
)
from hub.services.request_changes import (
    summarize_request_changes as summarize_request_changes,  # noqa: PLC0414 - Legacy re-export.
)
from hub.view_helpers import ADMIN_PANEL_ROLES as ADMIN_PANEL_ROLES  # noqa: PLC0414 - Legacy re-export.
from hub.view_helpers import MANILA_TZ as MANILA_TZ  # noqa: PLC0414 - Legacy re-export.
from hub.view_helpers import _safe_return_url as _safe_return_url  # noqa: PLC0414 - Legacy re-export.

REQUESTOR_ROLES = set(User.REQUESTOR_ROLES)
REQUEST_CREATOR_ROLES = set(getattr(User, "REQUEST_CREATOR_ROLES", User.REQUESTOR_ROLES))
PM_ESS_ROLE = User.Roles.PM_ESS
PM_ESG_ROLE = User.Roles.PM_ESG
SQR_ACCESS_ROLES = ENGINEER_ACCESS_ROLES | ADMIN_PANEL_ROLES

logger = logging.getLogger("hub.views")


def _csv_safe_cell(value: Any) -> Any:
    """Keep CSV text literal while preserving genuine numeric values."""
    if not isinstance(value, str):
        return value
    first = next(
        (char for char in value if not (char.isspace() or ord(char) < 32 or char == "\ufeff")),
        "",
    )
    if value.startswith(("\t", "\r", "\n")) or first in (
        "=", "+", "-", "@", "\uff1d", "\uff0b", "\uff0d", "\uff20",
    ):
        return "'" + value
    return value
