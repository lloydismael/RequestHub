from django.apps import AppConfig


class HubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hub"

    def ready(self) -> None:
        """Register signals and enable cache-specific delete handling when configured."""
        from . import signals

        signals.configure_notification_delete_signal()
