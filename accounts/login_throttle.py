import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import LoginThrottleBucket

logger = logging.getLogger(__name__)

LOGIN_THROTTLE_LIMIT = 5
SOURCE_LIMIT = 30
WINDOW = timedelta(minutes=15)
CLEANUP_BATCH = 64


@dataclass(frozen=True)
class Reservation:
    pair_key: str
    account_key: str | None
    token: str


def _key(scope, *parts):
    value = json.dumps(parts, separators=(",", ":"))
    return salted_hmac(f"accounts.login-throttle.{scope}", value, algorithm="sha256").hexdigest()


def _locked_bucket(key):
    bucket, _ = LoginThrottleBucket.objects.select_for_update().get_or_create(
        key=key, defaults={"expires_at": timezone.now},
    )
    now = timezone.now()
    if bucket.expires_at <= now:
        bucket.expires_at = now + WINDOW
        bucket.source_attempts = 0
        bucket.reservations = []
        bucket.failed_tokens = []
    return bucket


def _prune_expired():
    try:
        with transaction.atomic():
            expired = list(
                LoginThrottleBucket.objects.select_for_update(skip_locked=True)
                .filter(expires_at__lte=timezone.now()).order_by("expires_at", "pk")
                .values_list("pk", flat=True)[:CLEANUP_BATCH]
            )
            if expired:
                LoginThrottleBucket.objects.filter(pk__in=expired).delete()
    except DatabaseError:
        logger.error("Could not prune expired login quotas.")


def reserve(source, normalized_username, find_user):
    """Resolve the user and commit quota consumption before any password work."""
    try:
        _prune_expired()
        with transaction.atomic():
            source_bucket = _locked_bucket(_key("source", source))
            if source_bucket.source_attempts >= SOURCE_LIMIT:
                return None, None
            # Count submitted credential checks even when a narrower bucket denies them.
            source_bucket.source_attempts += 1
            source_bucket.save(update_fields=("source_attempts", "expires_at"))

            user = find_user()
            pair_key = (
                _key("user-source", source, user.pk) if user is not None
                else _key("unknown-source", source, normalized_username)
            )
            pair = _locked_bucket(pair_key)
            if len(pair.reservations) >= LOGIN_THROTTLE_LIMIT:
                return None, None

            account_key = _key("account", user.pk) if user is not None else None
            account = _locked_bucket(account_key) if account_key else None
            if account is not None and len(account.reservations) >= LOGIN_THROTTLE_LIMIT:
                return None, None

            token = uuid4().hex
            pair.reservations.append(token)
            pair.save(update_fields=("reservations", "failed_tokens", "expires_at"))
            if account is not None:
                account.reservations.append(token)
                account.save(update_fields=("reservations", "expires_at"))
            return user, Reservation(pair_key, account_key, token)
    except DatabaseError:
        logger.error("Could not reserve login quota; credential verification denied.")
        return None, None


def finish(reservation, *, success):
    if reservation.account_key is None:
        return
    try:
        with transaction.atomic():
            # Match reservation lock order; never recreate expired buckets on completion.
            pair = LoginThrottleBucket.objects.select_for_update().filter(pk=reservation.pair_key).first()
            token = reservation.token
            if pair is None or pair.expires_at <= timezone.now() or token not in pair.reservations:
                return
            if not success:
                if token not in pair.failed_tokens:
                    pair.failed_tokens.append(token)
                    pair.save(update_fields=("failed_tokens",))
                return

            # Keep pending checks and every check reserved after this successful one.
            prior = pair.reservations[:pair.reservations.index(token)]
            cleared = {token} | set(prior).intersection(pair.failed_tokens)
            pair.reservations = [item for item in pair.reservations if item not in cleared]
            pair.failed_tokens = [item for item in pair.failed_tokens if item not in cleared]
            pair.save(update_fields=("reservations", "failed_tokens"))

            account = LoginThrottleBucket.objects.select_for_update().filter(pk=reservation.account_key).first()
            if account is not None and account.expires_at > timezone.now():
                account.reservations = [item for item in account.reservations if item not in cleared]
                account.save(update_fields=("reservations",))
    except DatabaseError:
        logger.error("Could not settle a login quota reservation.")
