from __future__ import annotations

from asgiref.sync import sync_to_async
from django.contrib.auth import (
    check_password_with_timing_attack_mitigation,
    get_user_model,
)
from django.contrib.auth.backends import ModelBackend
from django.views.decorators.debug import sensitive_variables

from .login_throttle import finish, reserve
from .sign_in_log import client_ip


class CaseInsensitiveUsernameBackend(ModelBackend):
    """Authenticate using case-insensitive usernames and shared pre-hash quotas."""

    @sensitive_variables("password")
    def authenticate(self, request, username=None, password=None, *, staff_only=False, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        username = username.strip()
        if not username or "\x00" in username:
            return None

        def find_user():
            field = UserModel.USERNAME_FIELD
            users = UserModel._default_manager
            exact = users.filter(**{field: username}).first()
            if exact is not None:
                return exact
            return users.filter(**{f"{field}__iexact": username}).order_by(field).first()

        source = client_ip(request) if request is not None else "unknown"
        user, reservation = reserve(source, username.casefold(), find_user)
        if reservation is None:
            if request is not None:
                setattr(request, "_login_throttle_rejected", True)
            return None
        allowed = (
            check_password_with_timing_attack_mitigation(user, password)
            and self.user_can_authenticate(user)
            and (not staff_only or user.is_staff)
        )
        finish(reservation, success=allowed)
        return user if allowed else None

    @sensitive_variables("password")
    async def aauthenticate(self, request, username=None, password=None, **kwargs):
        return await sync_to_async(self.authenticate)(request, username, password, **kwargs)
