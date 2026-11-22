from collections.abc import Collection
from typing import TYPE_CHECKING

from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.models import AnonymousUser

from accounts.models import User

if TYPE_CHECKING:
    from hub.models import Request


ADMIN_PANEL_ROLES = {User.Roles.ADMIN, User.Roles.PM_ESG}
ENGINEER_ACCESS_ROLES = set(getattr(User, "ENGINEER_ACCESS_ROLES", (User.Roles.ENGINEER,)))
ASSIGNABLE_ENGINEER_ROLES = set(getattr(User, "ASSIGNABLE_ENGINEER_ROLES", (User.Roles.ENGINEER,)))


def user_has_role(user: User | AnonymousUser, roles: Collection[str]) -> bool:
    """Require an authenticated user with one of the supplied roles."""
    return user.is_authenticated and user.role in roles


def can_manage_user(actor: User, target: User) -> bool:
    """Allow only superusers to manage staff or superuser accounts."""
    return actor.is_superuser or not (target.is_superuser or target.is_staff)


def can_update_request_status(user: User, primary_engineer_id: int | None) -> bool:
    """Allow the primary assignee or a management role to update status."""
    return user.pk == primary_engineer_id or user.role in ADMIN_PANEL_ROLES


def is_request_assignee(user: User, request_obj: 'Request') -> bool:
    """Check whether the user is the primary or backup assignee."""
    return user == request_obj.engineer or user == request_obj.backup_engineer


class RoleRequiredMixin(UserPassesTestMixin):
    required_role: str = ""
    required_roles: set[str] | None = None

    def test_func(self) -> bool:
        """Check the configured roles without changing the mixin's denial response."""
        roles = set(self.required_roles or ())
        if self.required_role:
            roles.add(self.required_role)
        return bool(roles) and user_has_role(self.request.user, roles)


class RequestorRequiredMixin(RoleRequiredMixin):
    required_roles = set(getattr(User, "REQUEST_CREATOR_ROLES", User.REQUESTOR_ROLES))


class EngineerRequiredMixin(RoleRequiredMixin):
    required_roles = ENGINEER_ACCESS_ROLES


class AdminRequiredMixin(RoleRequiredMixin):
    required_role = User.Roles.ADMIN


class AdminOrPmEsgRequiredMixin(RoleRequiredMixin):
    required_roles = ADMIN_PANEL_ROLES


class AdminOrEngineerRequiredMixin(UserPassesTestMixin):
    def test_func(self) -> bool:
        """Allow management and engineer access roles with the original denial semantics."""
        engineer_roles = set(getattr(User, "ENGINEER_ACCESS_ROLES", (User.Roles.ENGINEER,)))
        return user_has_role(self.request.user, ADMIN_PANEL_ROLES | engineer_roles)
