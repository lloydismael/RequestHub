from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class RequestHubAdminConfig(AdminConfig):
    default_site = "accounts.admin_site.RequestHubAdminSite"


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from accounts import sign_in_log  # noqa: F401 - registers sign-in signal receivers
