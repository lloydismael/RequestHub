"""Short-lived shared lookups with commit-safe cache generations."""

import json
from collections.abc import Callable, Iterable
from functools import cache as memoize
from typing import TypeVar
from uuid import uuid4

from django.conf import settings
from django.core.cache import caches
from django.db import connections, transaction
from redis.exceptions import RedisError

CACHE_ALIAS = "shared_lookups"
NOTIFICATION_BADGES = "notification-badges"
DEPARTMENT_COUNTS = "department-counts"
_LOOKUP_TTL = 30
_GENERATION_TTL = 300
_T = TypeVar("_T")


class LookupSerializer:
    """Store lookup payloads as JSON, not pickled Python objects."""

    def dumps(self, value: object) -> str:
        """Encode a lookup or generation for Redis."""
        return json.dumps(value, separators=(",", ":"))

    def loads(self, value: bytes) -> object:
        """Decode a Redis entry written by this cache."""
        return json.loads(value)


def shared_lookups_enabled() -> bool:
    """Check for the dedicated cache without falling back to the default alias."""
    return CACHE_ALIAS in settings.CACHES


def _generation_key(namespace: str, using: str, scope: int | None = None) -> str:
    """Identify the global or recipient-specific generation for a database."""
    key = f"{namespace}:{using}:generation"
    return key if scope is None else f"{key}:{scope}"


def _new_generation() -> str:
    """Never reuse an old generation, even after eviction or expiry."""
    return uuid4().hex


def read_shared_lookup(
    namespace: str,
    load: Callable[[], _T],
    *,
    using: str,
    scope: int | None = None,
    variant: str = "",
) -> _T:
    """Use a shared value when enabled and outside any database transaction."""
    if not shared_lookups_enabled():
        return load()
    db = connections[using]
    if db.in_atomic_block or not db.settings_dict["AUTOCOMMIT"] or (
        db.connection is not None and not db.get_autocommit()
    ):
        return load()

    backend = caches[CACHE_ALIAS]
    keys = [_generation_key(namespace, using)]
    if scope is not None:
        keys.append(_generation_key(namespace, using, scope))
    load_once = memoize(load)
    try:
        generations = backend.get_many(keys)
        tokens = [
            generations[key] if key in generations
            else backend.get_or_set(key, _new_generation, timeout=_GENERATION_TTL)
            for key in keys
        ]
        # Capture the generations before querying so a late fill cannot undo invalidation.
        value_key = ":".join((namespace, using, "value", str(scope), variant, *tokens))
        return backend.get_or_set(value_key, load_once, timeout=_LOOKUP_TTL)
    except RedisError:
        return load_once()


def _invalidate(namespace: str, using: str, scopes: Iterable[int | None]) -> None:
    """Rotate affected generations after commit and let old payloads expire."""
    if not shared_lookups_enabled():
        return
    keys = tuple(dict.fromkeys(_generation_key(namespace, using, scope) for scope in scopes))
    if not keys:
        return

    def rotate_generations() -> None:
        """Leave a failed Redis invalidation to the payload's short expiry."""
        generation = _new_generation()
        try:
            caches[CACHE_ALIAS].set_many(dict.fromkeys(keys, generation), timeout=_GENERATION_TTL)
        except RedisError:
            pass

    transaction.on_commit(rotate_generations, using=using)


def invalidate_notification_badges(recipient_ids: Iterable[int], *, using: str) -> None:
    """Invalidate all role variants for the affected notification recipients."""
    _invalidate(NOTIFICATION_BADGES, using, recipient_ids)


def invalidate_all_notification_badges(*, using: str) -> None:
    """Invalidate every notification badge after raw backup replacement."""
    _invalidate(NOTIFICATION_BADGES, using, (None,))


def invalidate_department_counts(*, using: str) -> None:
    """Invalidate the active-member counts without caching any member records."""
    _invalidate(DEPARTMENT_COUNTS, using, (None,))
