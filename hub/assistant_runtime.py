import hashlib
import hmac
import importlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from hub.assistant_types import ConversationState
from hub.models import AssistantUsageLimit


REQUEST_WINDOW = timedelta(minutes=10)
MAX_REQUESTS_PER_WINDOW = 20
MAX_CONCURRENT_MODEL_REQUESTS = 2
LEASE_DURATION = timedelta(seconds=60)
MODEL_REQUEST_DEADLINE_SECONDS = 45
MAX_CONVERSATION_EXCHANGES = 12
MAX_CONVERSATION_BYTES = 64 * 1024
MAX_REQUEST_CACHE_BYTES = 32 * 1024
REQUEST_CACHE_TTL = timedelta(minutes=2)

USER_SCOPE_PREFIX = "user:"
SESSION_SCOPE_PREFIX = "session:"
GLOBAL_SCOPE_PREFIX = "global:"
CONVERSATION_SESSION_KEY = "request_hub_assistant"
REQUEST_CACHE_SESSION_KEY = "request_hub_assistant_retry"


class AssistantRuntimeError(RuntimeError):
    pass


class AssistantRuntimeValidationError(ValueError):
    pass


class AssistantRateLimitError(AssistantRuntimeError):
    pass


class AssistantRequestInProgressError(AssistantRuntimeError):
    pass


class AssistantRequestConflictError(AssistantRuntimeError):
    pass


class AssistantGlobalCapacityError(AssistantRuntimeError):
    pass


class AssistantConversationExpiredError(AssistantRuntimeError):
    pass


@dataclass(frozen=True)
class RequestLease:
    owner_id: UUID
    user_id: int
    global_slot: str
    request_id: UUID
    expires_at: datetime


def _validate_now(now: datetime) -> None:
    if not isinstance(now, datetime) or not timezone.is_aware(now):
        raise AssistantRuntimeValidationError("Runtime timestamps must be timezone-aware.")


def _active_user(user: User) -> User:
    if (
        not getattr(user, "is_authenticated", False)
        or isinstance(user, AnonymousUser)
        or not getattr(user, "pk", None)
    ):
        raise PermissionDenied

    current = User.objects.filter(pk=user.pk).first()
    if current is None or not current.is_active:
        raise PermissionDenied
    return current


def _user_scope_key(user_id: int) -> str:
    return f"{USER_SCOPE_PREFIX}{user_id}"


def _session_scope_key(session_key: str) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        session_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{SESSION_SCOPE_PREFIX}{digest}"


def _session_store(session_key: str):
    if settings.SESSION_ENGINE not in {
        "django.contrib.sessions.backends.db",
        "django.contrib.sessions.backends.cached_db",
    }:
        raise AssistantRuntimeError(
            "The assistant requires database-backed Django sessions."
        )
    engine = importlib.import_module(settings.SESSION_ENGINE)
    return engine.SessionStore(session_key)


def _ensure_runtime_rows(user: User) -> None:
    AssistantUsageLimit.objects.get_or_create(
        scope_key=_user_scope_key(user.pk),
        defaults={"owner": user},
    )
    for slot in range(MAX_CONCURRENT_MODEL_REQUESTS):
        AssistantUsageLimit.objects.get_or_create(
            scope_key=f"{GLOBAL_SCOPE_PREFIX}{slot}",
        )


def _prune_if_window_expired(user: User, now: datetime) -> None:
    row = AssistantUsageLimit.objects.filter(
        scope_key=_user_scope_key(user.pk),
    ).only("window_started_at").first()
    if row is None or row.window_started_at is None or now >= row.window_started_at + REQUEST_WINDOW:
        prune_expired_runtime(now)


def acquire_request(user: User, request_id: UUID, now: datetime) -> RequestLease:
    _validate_now(now)
    if not isinstance(request_id, UUID):
        raise AssistantRuntimeValidationError("Request ID must be a UUID.")
    current_user = _active_user(user)
    _prune_if_window_expired(current_user, now)
    _ensure_runtime_rows(current_user)

    user_key = _user_scope_key(current_user.pk)
    global_keys = tuple(
        f"{GLOBAL_SCOPE_PREFIX}{slot}"
        for slot in range(MAX_CONCURRENT_MODEL_REQUESTS)
    )
    scope_keys = sorted((user_key, *global_keys))
    owner_id = uuid4()
    expires_at = now + LEASE_DURATION

    with transaction.atomic():
        records = {
            record.scope_key: record
            for record in AssistantUsageLimit.objects.select_for_update()
            .filter(scope_key__in=scope_keys)
            .order_by("scope_key")
        }
        user_record = records[user_key]
        if user_record.lease_expires_at and user_record.lease_expires_at > now:
            raise AssistantRequestInProgressError("A request is already in progress.")

        if (
            user_record.window_started_at is None
            or now >= user_record.window_started_at + REQUEST_WINDOW
        ):
            user_record.window_started_at = now
            user_record.request_count = 0
        if user_record.request_count >= MAX_REQUESTS_PER_WINDOW:
            raise AssistantRateLimitError("The assistant request limit was reached.")

        global_record = next(
            (
                records[key]
                for key in global_keys
                if not records[key].lease_expires_at
                or records[key].lease_expires_at <= now
            ),
            None,
        )
        if global_record is None:
            raise AssistantGlobalCapacityError("The assistant is at capacity.")

        user_record.owner = current_user
        user_record.request_count += 1
        user_record.lease_owner = owner_id
        user_record.lease_expires_at = expires_at
        user_record.save(
            update_fields=[
                "owner",
                "request_count",
                "window_started_at",
                "lease_owner",
                "lease_expires_at",
                "updated_at",
            ]
        )
        global_record.lease_owner = owner_id
        global_record.lease_expires_at = expires_at
        global_record.save(update_fields=["lease_owner", "lease_expires_at", "updated_at"])

    return RequestLease(
        owner_id=owner_id,
        user_id=current_user.pk,
        global_slot=global_record.scope_key,
        request_id=request_id,
        expires_at=expires_at,
    )


def release_request(lease: RequestLease) -> None:
    if not isinstance(lease, RequestLease):
        raise AssistantRuntimeValidationError("A valid request lease is required.")

    with transaction.atomic():
        scope_keys = sorted(
            (_user_scope_key(lease.user_id), lease.global_slot),
        )
        records = AssistantUsageLimit.objects.select_for_update().filter(
            scope_key__in=scope_keys,
            lease_owner=lease.owner_id,
        )
        for record in records:
            record.lease_owner = None
            record.lease_expires_at = None
            record.save(update_fields=["lease_owner", "lease_expires_at", "updated_at"])


def _active_session_scope_keys(now: datetime) -> set[str]:
    session_keys = Session.objects.filter(
        expire_date__gt=now,
    ).values_list("session_key", flat=True)
    return {_session_scope_key(session_key) for session_key in session_keys}


def prune_expired_runtime(now: datetime) -> int:
    _validate_now(now)
    inactive_lease = Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now)
    deleted = 0

    expired_session_scopes = (
        AssistantUsageLimit.objects.filter(
            scope_key__startswith=SESSION_SCOPE_PREFIX,
            session_expires_at__lte=now,
        )
        .exclude(scope_key__in=_active_session_scope_keys(now))
        .filter(inactive_lease)
    )
    for record in expired_session_scopes.only("pk", "scope_key", "owner_id"):
        if record.owner_id and AssistantUsageLimit.objects.filter(
            scope_key=_user_scope_key(record.owner_id),
            lease_expires_at__gt=now,
        ).exists():
            continue
        record.delete()
        deleted += 1

    expired_user_scopes = AssistantUsageLimit.objects.filter(
        scope_key__startswith=USER_SCOPE_PREFIX,
        window_started_at__lte=now - REQUEST_WINDOW,
    ).filter(inactive_lease)
    count, _ = expired_user_scopes.delete()
    return deleted + count


def _require_session_key(request) -> str:
    session_key = getattr(request.session, "session_key", None)
    if not session_key:
        raise AssistantConversationExpiredError("The authenticated session has expired.")
    return session_key


def _fresh_session_data(request, user: User) -> tuple[str, dict, object] | None:
    session_key = _require_session_key(request)
    store = _session_store(session_key)
    data = store.load()
    if str(data.get("_auth_user_id", "")) != str(user.pk):
        return None
    return session_key, data, store


def _current_fingerprint(user: User) -> dict[str, object] | None:
    current = User.objects.filter(pk=user.pk).values(
        "is_active",
        "role",
        "show_chatbot",
    ).first()
    if not current or not current["is_active"]:
        return None
    return {
        "user_id": user.pk,
        "role": current["role"],
        "show_chatbot": current["show_chatbot"],
    }


def _serialize_exchanges(exchanges: list[dict[str, str]]) -> str:
    return json.dumps(exchanges, ensure_ascii=False, separators=(",", ":"))


def _validate_exchanges(raw_exchanges: object) -> tuple[dict[str, str], ...]:
    if not isinstance(raw_exchanges, list) or len(raw_exchanges) > MAX_CONVERSATION_EXCHANGES:
        raise AssistantRuntimeError("Stored assistant conversation is invalid.")
    exchanges = []
    for exchange in raw_exchanges:
        if (
            not isinstance(exchange, dict)
            or set(exchange) != {"user", "assistant"}
            or not isinstance(exchange["user"], str)
            or not isinstance(exchange["assistant"], str)
        ):
            raise AssistantRuntimeError("Stored assistant conversation is invalid.")
        exchanges.append(
            {"user": exchange["user"], "assistant": exchange["assistant"]}
        )
    if len(_serialize_exchanges(exchanges).encode("utf-8")) > MAX_CONVERSATION_BYTES:
        raise AssistantRuntimeError("Stored assistant conversation exceeds its limit.")
    return tuple(exchanges)


def _state_from_record(raw: object) -> ConversationState | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AssistantRuntimeError("Stored assistant conversation is invalid.")
    conversation_id = raw.get("id")
    generation = raw.get("generation")
    if (
        not isinstance(conversation_id, str)
        or not isinstance(generation, int)
        or isinstance(generation, bool)
    ):
        raise AssistantRuntimeError("Stored assistant conversation is invalid.")
    return ConversationState(
        id=conversation_id,
        generation=generation,
        exchanges=_validate_exchanges(raw.get("exchanges")),
    )


def _save_session_data(
    request,
    session_key: str,
    data: dict,
    store,
) -> None:
    store._session_cache = dict(data)
    store.save()
    request.session._session_cache = dict(data)
    request.session.modified = False


def _validate_request_cache(
    request_id: UUID,
    request_fingerprint: str,
    response_cache: dict | None = None,
) -> None:
    if not isinstance(request_id, UUID):
        raise AssistantRuntimeValidationError("Request ID must be a UUID.")
    if (
        not isinstance(request_fingerprint, str)
        or len(request_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in request_fingerprint)
    ):
        raise AssistantRuntimeValidationError("Request fingerprint must be a SHA-256 digest.")
    if response_cache is not None:
        if not isinstance(response_cache, dict):
            raise AssistantRuntimeValidationError("Cached response must be an object.")
        try:
            serialized = json.dumps(
                response_cache,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise AssistantRuntimeValidationError("Cached response must be JSON serializable.") from None
        if len(serialized.encode("utf-8")) > MAX_REQUEST_CACHE_BYTES:
            raise AssistantRuntimeValidationError("Cached response exceeds its size limit.")


def load_cached_request_response(
    request,
    state: ConversationState,
    request_id: UUID,
    request_fingerprint: str,
    now: datetime | None = None,
) -> dict | None:
    if not isinstance(state, ConversationState):
        raise AssistantRuntimeValidationError("A valid conversation state is required.")
    _validate_request_cache(request_id, request_fingerprint)
    current_time = now or timezone.now()
    _validate_now(current_time)
    session_key = getattr(request.session, "session_key", None)
    if not session_key or not getattr(request.user, "pk", None):
        return None

    fingerprint = _current_fingerprint(request.user)
    if fingerprint is None:
        return None
    scope_key = _session_scope_key(session_key)
    _session_scope_record(session_key, request.user)
    with transaction.atomic():
        runtime = AssistantUsageLimit.objects.select_for_update().get(scope_key=scope_key)
        session = _fresh_session_data(request, request.user)
        if session is None:
            return None
        _, data, _ = session
        raw = data.get(CONVERSATION_SESSION_KEY)
        current_state = _state_from_record(raw)
        if (
            current_state is None
            or current_state.id != state.id
            or current_state.generation != state.generation
            or state.generation != runtime.session_generation
            or not isinstance(raw, dict)
            or raw.get("user_id") != fingerprint["user_id"]
            or raw.get("role") != fingerprint["role"]
            or raw.get("show_chatbot") != fingerprint["show_chatbot"]
        ):
            return None

        cached = data.get(REQUEST_CACHE_SESSION_KEY)
        if not isinstance(cached, dict) or cached.get("request_id") != str(request_id):
            return None
        if (
            cached.get("generation") != state.generation
            or cached.get("request_fingerprint") != request_fingerprint
        ):
            raise AssistantRequestConflictError("The request ID was already used.")
        try:
            created_at = datetime.fromisoformat(cached["created_at"])
        except (KeyError, TypeError, ValueError):
            raise AssistantRuntimeError("Stored assistant retry state is invalid.") from None
        if not timezone.is_aware(created_at):
            raise AssistantRuntimeError("Stored assistant retry state is invalid.")
        response = cached.get("response")
        if not isinstance(response, dict):
            raise AssistantRuntimeError("Stored assistant retry state is invalid.")
        if current_time >= created_at + REQUEST_CACHE_TTL:
            raise AssistantRequestConflictError("The retained request response has expired.")
        return json.loads(json.dumps(response, ensure_ascii=False))


def _session_scope_record(session_key: str, user: User) -> AssistantUsageLimit:
    record, _ = AssistantUsageLimit.objects.get_or_create(
        scope_key=_session_scope_key(session_key),
        defaults={"owner": user},
    )
    return record


def _conversation_record(
    state: ConversationState,
    fingerprint: dict[str, object],
) -> dict[str, object]:
    return {
        **fingerprint,
        "id": state.id,
        "generation": state.generation,
        "exchanges": list(state.exchanges),
    }


def load_conversation(request) -> ConversationState:
    user = _active_user(request.user)
    fingerprint = _current_fingerprint(user)
    if fingerprint is None:
        raise PermissionDenied
    session_key = _require_session_key(request)
    _session_scope_record(session_key, user)

    with transaction.atomic():
        runtime = AssistantUsageLimit.objects.select_for_update().get(
            scope_key=_session_scope_key(session_key),
        )
        session = _fresh_session_data(request, user)
        if session is None:
            raise AssistantConversationExpiredError("The authenticated session has expired.")
        fresh_key, data, store = session
        raw = data.get(CONVERSATION_SESSION_KEY)
        old_state = _state_from_record(raw)
        same_owner = (
            isinstance(raw, dict)
            and raw.get("user_id") == fingerprint["user_id"]
            and raw.get("role") == fingerprint["role"]
            and raw.get("show_chatbot") == fingerprint["show_chatbot"]
            and old_state is not None
            and old_state.generation == runtime.session_generation
        )
        if same_owner:
            runtime.owner = user
            runtime.session_expires_at = store.get_expiry_date()
            runtime.save(update_fields=["owner", "session_expires_at", "updated_at"])
            return old_state

        runtime.session_generation = max(1, runtime.session_generation + 1)
        runtime.owner = user
        runtime.session_expires_at = store.get_expiry_date()
        state = ConversationState(
            id=str(uuid4()),
            generation=runtime.session_generation,
            exchanges=(),
        )
        data[CONVERSATION_SESSION_KEY] = _conversation_record(state, fingerprint)
        data.pop(REQUEST_CACHE_SESSION_KEY, None)
        runtime.save(
            update_fields=["owner", "session_generation", "session_expires_at", "updated_at"]
        )
        _save_session_data(request, fresh_key, data, store)
        return state


def reset_conversation(request) -> ConversationState:
    user = _active_user(request.user)
    fingerprint = _current_fingerprint(user)
    if fingerprint is None:
        raise PermissionDenied
    session_key = _require_session_key(request)
    _session_scope_record(session_key, user)

    with transaction.atomic():
        runtime = AssistantUsageLimit.objects.select_for_update().get(
            scope_key=_session_scope_key(session_key),
        )
        session = _fresh_session_data(request, user)
        if session is None:
            raise AssistantConversationExpiredError("The authenticated session has expired.")
        fresh_key, data, store = session
        runtime.session_generation = max(1, runtime.session_generation + 1)
        runtime.owner = user
        runtime.session_expires_at = store.get_expiry_date()
        state = ConversationState(
            id=str(uuid4()),
            generation=runtime.session_generation,
            exchanges=(),
        )
        data[CONVERSATION_SESSION_KEY] = _conversation_record(state, fingerprint)
        data.pop(REQUEST_CACHE_SESSION_KEY, None)
        runtime.save(
            update_fields=["owner", "session_generation", "session_expires_at", "updated_at"]
        )
        _save_session_data(request, fresh_key, data, store)
        return state


def append_exchange_if_current(
    request,
    state: ConversationState,
    *,
    user_text: str,
    assistant_text: str,
    request_id: UUID | None = None,
    request_fingerprint: str | None = None,
    response_cache: dict | None = None,
) -> bool:
    if not isinstance(state, ConversationState):
        raise AssistantRuntimeValidationError("A valid conversation state is required.")
    if not isinstance(user_text, str) or not isinstance(assistant_text, str):
        raise AssistantRuntimeValidationError("Conversation messages must be text.")
    cache_requested = any(
        value is not None
        for value in (request_id, request_fingerprint, response_cache)
    )
    if cache_requested:
        if request_id is None or request_fingerprint is None or response_cache is None:
            raise AssistantRuntimeValidationError("Complete request cache data is required.")
        _validate_request_cache(request_id, request_fingerprint, response_cache)
    if not getattr(request.user, "pk", None):
        return False

    session_key = getattr(request.session, "session_key", None)
    if not session_key:
        return False
    fingerprint = _current_fingerprint(request.user)
    if fingerprint is None:
        return False
    scope_key = _session_scope_key(session_key)
    _session_scope_record(session_key, request.user)

    with transaction.atomic():
        runtime = AssistantUsageLimit.objects.select_for_update().get(
            scope_key=scope_key,
        )
        session = _fresh_session_data(request, request.user)
        if session is None:
            return False
        fresh_key, data, store = session
        raw = data.get(CONVERSATION_SESSION_KEY)
        current_state = _state_from_record(raw)
        if (
            current_state is None
            or current_state.id != state.id
            or current_state.generation != state.generation
            or state.generation != runtime.session_generation
            or not isinstance(raw, dict)
            or raw.get("user_id") != fingerprint["user_id"]
            or raw.get("role") != fingerprint["role"]
            or raw.get("show_chatbot") != fingerprint["show_chatbot"]
        ):
            return False

        exchanges = [dict(exchange) for exchange in current_state.exchanges]
        exchanges.append({"user": user_text, "assistant": assistant_text})
        while exchanges and (
            len(exchanges) > MAX_CONVERSATION_EXCHANGES
            or len(_serialize_exchanges(exchanges).encode("utf-8"))
            > MAX_CONVERSATION_BYTES
        ):
            exchanges.pop(0)
        if not exchanges:
            raise AssistantRuntimeValidationError("Conversation exchange exceeds the storage limit.")

        updated_state = ConversationState(
            id=current_state.id,
            generation=current_state.generation,
            exchanges=tuple(exchanges),
        )
        data[CONVERSATION_SESSION_KEY] = _conversation_record(
            updated_state,
            fingerprint,
        )
        if cache_requested:
            data[REQUEST_CACHE_SESSION_KEY] = {
                "request_id": str(request_id),
                "request_fingerprint": request_fingerprint,
                "generation": updated_state.generation,
                "created_at": timezone.now().isoformat(),
                "response": response_cache,
            }
        runtime.owner = request.user
        runtime.session_expires_at = store.get_expiry_date()
        runtime.save(update_fields=["owner", "session_expires_at", "updated_at"])
        _save_session_data(request, fresh_key, data, store)
        return True
