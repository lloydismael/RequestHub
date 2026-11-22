<<<<<<< HEAD
=======
from django.core.signals import setting_changed
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from accounts.models import User

from .models import Notification, Request
from .services.lookup_cache import (
    invalidate_department_counts,
    invalidate_notification_badges,
    shared_lookups_enabled,
)


def _resolve_actor(instance):
    actor_user = getattr(instance, "_actor_user", None)
    if actor_user:
        return actor_user.get_full_name() or actor_user.username
    return "System"


def _resolve_source(instance, default_label):
    return getattr(instance, "_actor_source", default_label)


@receiver(pre_save, sender=Request)
def cache_previous_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_status = None
        return
    try:
        previous = sender.objects.get(pk=instance.pk)
        instance._previous_status = previous.status
    except sender.DoesNotExist:
        instance._previous_status = None


@receiver(post_save, sender=Request)
def notify_on_completion(sender, instance, created, **kwargs):
    previous_status = getattr(instance, "_previous_status", None)
    if instance.status == Request.Status.COMPLETED and previous_status != Request.Status.COMPLETED:
        code = instance.reference_code or f"REQ-{instance.pk:05d}"
        actor = _resolve_actor(instance)
        source = _resolve_source(instance, "Request · Completion")
        Notification.objects.create(
            recipient=instance.requestor,
            related_request=instance,
            message=f"Request {code} has been completed.",
            actor=actor,
            source=source,
        )
        if instance.engineer:
            Notification.objects.create(
                recipient=instance.engineer,
                related_request=instance,
                message=f"Request {code} closed by admin.",
                actor=actor,
                source=source,
            )


@receiver(pre_save, sender=Notification)
def capture_previous_notification_recipient(
    sender: type[Notification],
    instance: Notification,
    using: str,
    raw: bool,
    **kwargs: object,
) -> None:
    """Find the stored recipient even when a partial save excludes that field."""
    instance._previous_cache_recipient_id = None
    if shared_lookups_enabled() and not raw and instance.pk is not None:
        instance._previous_cache_recipient_id = (
            sender.objects.using(using).filter(pk=instance.pk)
            .values_list("recipient_id", flat=True).first()
        )


@receiver(post_save, sender=Notification)
def refresh_saved_notification_badges(
    sender: type[Notification],
    instance: Notification,
    using: str,
    raw: bool,
    update_fields: frozenset[str] | None,
    **kwargs: object,
) -> None:
    """Invalidate only the recipients affected by the committed write."""
    if raw or not shared_lookups_enabled():
        return
    previous = getattr(instance, "_previous_cache_recipient_id", None)
    if previous is None:
        recipients = (instance.recipient_id,)
    elif update_fields is not None and update_fields.isdisjoint({"recipient", "recipient_id"}):
        recipients = (previous,)
    else:
        recipients = (previous, instance.recipient_id)
    invalidate_notification_badges(recipients, using=using)


<<<<<<< HEAD
@receiver(pre_delete, sender=Notification)
=======
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
def refresh_deleted_notification_badges(
    sender: type[Notification], instance: Notification, using: str, origin: object, **kwargs: object,
) -> None:
    """Schedule invalidation while the recipient is still available in the database."""
<<<<<<< HEAD
    if not shared_lookups_enabled():
        return
=======
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    if origin is instance:
        recipient_id = sender.objects.using(using).filter(pk=instance.pk).values_list("recipient_id", flat=True).first()
    else:
        recipient_id = instance.recipient_id
    if recipient_id is not None:
        invalidate_notification_badges((recipient_id,), using=using)


<<<<<<< HEAD
=======
def configure_notification_delete_signal() -> None:
    """Register cache invalidation only when shared lookups are configured."""
    # A delete listener disables Django's fast path even if it does nothing.
    if shared_lookups_enabled():
        pre_delete.connect(refresh_deleted_notification_badges, sender=Notification)
    else:
        pre_delete.disconnect(refresh_deleted_notification_badges, sender=Notification)


@receiver(setting_changed)
def refresh_notification_delete_signal(setting: str, **kwargs: object) -> None:
    """Keep delete handling in sync when the cache settings are overridden."""
    if setting == "CACHES":
        configure_notification_delete_signal()


>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
@receiver(post_save, sender=User)
def refresh_saved_department_counts(
    sender: type[User],
    instance: User,
    using: str,
    raw: bool,
    created: bool,
    update_fields: frozenset[str] | None,
    **kwargs: object,
) -> None:
    """Refresh counts when a save can change department membership."""
    if not raw and (created or update_fields is None or not update_fields.isdisjoint({"department", "is_active"})):
        invalidate_department_counts(using=using)


@receiver(post_delete, sender=User)
def refresh_deleted_department_counts(
    sender: type[User], instance: User, using: str, **kwargs: object,
) -> None:
    """Remove deleted users from the cached directory counts after commit."""
    invalidate_department_counts(using=using)


# Assignment notifications are created by hub.services.notifications.
ASSIGNMENT_NOTIFICATIONS_CENTRALIZED = True
