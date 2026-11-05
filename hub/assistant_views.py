import hashlib
import json
import logging
import time
from dataclasses import asdict
from uuid import UUID
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.views.csrf import csrf_failure as default_csrf_failure
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from hub.assistant_directory import (
    AssistantDirectoryNotFoundError,
    AssistantDirectoryValidationError,
    find_department_members,
    list_departments,
)
from hub.assistant_knowledge import (
    GuideNotFoundError,
    allowed_destinations,
    get_guide,
)
from hub.assistant_provider import (
    AssistantProviderError,
    FoundryProvider,
    provider_readiness,
)
from hub.assistant_runtime import (
    AssistantConversationExpiredError,
    AssistantGlobalCapacityError,
    AssistantRateLimitError,
    AssistantRequestConflictError,
    AssistantRequestInProgressError,
    AssistantRuntimeError,
    AssistantRuntimeValidationError,
    acquire_request,
    append_exchange_if_current,
    load_cached_request_response,
    load_conversation,
    release_request,
    reset_conversation,
)
from hub.assistant_service import AssistantToolError, answer_message
from hub.assistant_types import AssistantReply


logger = logging.getLogger("hub.assistant_api")
MAX_REQUEST_BODY_BYTES = 32 * 1024
MAX_MESSAGE_LENGTH = 4_000
STARTERS = (
    "Find a page",
    "How do I use Request Hub?",
    "Browse departments",
)


class AssistantApiValidationError(ValueError):
    pass


def _error(
    code: str,
    status: int,
    message: str | None = None,
    *,
    request_id: UUID | str | None = None,
    extra: dict[str, object] | None = None,
) -> JsonResponse:
    payload: dict[str, object] = {
        "status": "error",
        "code": code,
        "message": message or code.replace("_", " ").capitalize(),
        "request_id": str(request_id or uuid4()),
    }
    if extra:
        payload.update(extra)
    return JsonResponse(
        payload,
        status=status,
    )


def _is_assistant_route(request: HttpRequest) -> bool:
    try:
        view_name = resolve(request.path_info).view_name
    except Resolver404:
        return False
    return view_name.rsplit(":", 1)[-1].startswith("assistant-")


def assistant_csrf_failure(
    request: HttpRequest,
    reason: str = "",
) -> HttpResponse:
    if _is_assistant_route(request):
        return _error("csrf_failed", 403, "The request could not be verified.")
    return default_csrf_failure(request, reason)


def _require_access(request: HttpRequest) -> tuple[User | None, JsonResponse | None]:
    user = getattr(request, "user", None)
    if (
        not getattr(user, "is_authenticated", False)
        or isinstance(user, AnonymousUser)
    ):
        return None, _error("unauthenticated", 401, "Sign in to use the assistant.")
    if not getattr(user, "is_active", False):
        return None, _error("forbidden", 403, "This account cannot use the assistant.")
    if not getattr(user, "show_chatbot", False):
        return None, _error("assistant_opt_in_required", 403, "Enable the assistant in profile preferences first.")
    if not settings.AI_ASSISTANT_NATIVE_ENABLED:
        return None, _error("assistant_disabled", 503, "The native assistant is not enabled.")
    return user, None


def _handle_error(error: Exception, *, request_id: UUID | str | None = None) -> JsonResponse:
    if isinstance(error, PermissionDenied):
        return _error("forbidden", 403, "You do not have permission to access this assistant content.", request_id=request_id)
    if isinstance(error, (GuideNotFoundError, AssistantDirectoryNotFoundError)):
        return _error("not_found", 404, "The requested assistant content was not found.", request_id=request_id)
    if isinstance(error, (AssistantRequestConflictError, AssistantRequestInProgressError)):
        return _error("request_conflict", 409, "The request is stale, duplicated, or already in progress.", request_id=request_id)
    if isinstance(error, AssistantRateLimitError):
        return _error("rate_limited", 429, "The assistant request limit was reached.", request_id=request_id)
    if isinstance(error, AssistantGlobalCapacityError):
        return _error("assistant_busy", 429, "The assistant is at capacity. Try again shortly.", request_id=request_id)
    if isinstance(error, AssistantConversationExpiredError):
        return _error("conversation_expired", 409, "Start a new assistant conversation.", request_id=request_id)
    if isinstance(error, AssistantDirectoryValidationError):
        return _error("invalid_input", 400, "The directory search input is invalid.", request_id=request_id)
    if isinstance(error, AssistantRuntimeValidationError):
        return _error("invalid_input", 400, "The assistant request is invalid.", request_id=request_id)
    if isinstance(error, AssistantProviderError):
        status = {
            "not_configured": 503,
            "invalid_configuration": 503,
            "timeout": 504,
            "authentication_failed": 503,
            "provider_limit": 503,
            "unsupported_capability": 502,
            "upstream_failure": 502,
            "invalid_response": 502,
            "invalid_request": 502,
        }.get(error.code, 502)
        logger.warning("Assistant provider error code=%s request_id=%s", error.code, request_id, exc_info=error)
        return _error(
            error.code,
            status,
            "The assistant service is temporarily unavailable.",
            request_id=request_id,
        )
    if isinstance(error, AssistantToolError):
        return _error("provider_response_invalid", 502, "The assistant returned an invalid response.", request_id=request_id)
    if isinstance(error, AssistantRuntimeError):
        return _error("conversation_conflict", 409, "The assistant conversation is no longer current.", request_id=request_id)
    if isinstance(error, AssistantApiValidationError):
        logger.warning("Assistant API validation error request_id=%s", request_id, exc_info=error)
        return _error("invalid_input", 400, str(error), request_id=request_id)
    return _error("provider_failure", 502, "The assistant request failed.", request_id=request_id)


def _conversation_payload(state) -> dict[str, object]:
    return {
        "conversation_id": state.id,
        "generation": state.generation,
        "messages": [
            {"user": exchange["user"], "assistant": exchange["assistant"]}
            for exchange in state.exchanges
        ],
    }


def _reply_payload(reply: AssistantReply) -> dict[str, object]:
    return {
        "text": reply.text,
        "destinations": [asdict(card) for card in reply.destinations],
        "departments": [asdict(department) for department in reply.departments],
        "members": asdict(reply.members) if reply.members else None,
        "sources": [asdict(source) for source in reply.sources],
    }


def _page_number(value: str | None) -> int:
    if value is None:
        return 1
    try:
        page = int(value)
    except (TypeError, ValueError):
        raise AssistantApiValidationError("Page must be a positive integer.") from None
    if page < 1:
        raise AssistantApiValidationError("Page must be a positive integer.")
    return page


def _unknown_query_parameters(request: HttpRequest, allowed: set[str]) -> bool:
    return any(key not in allowed for key in request.GET)


@require_GET
def assistant_state(request: HttpRequest) -> JsonResponse:
    user, denial = _require_access(request)
    if denial:
        return denial
    try:
        state = load_conversation(request)
    except (PermissionDenied, AssistantRuntimeError) as error:
        return _handle_error(error)
    return JsonResponse(
        {
            **_conversation_payload(state),
            "starters": STARTERS,
            "provider_status": provider_readiness(),
        }
    )


@require_GET
def assistant_guide(request: HttpRequest, key: str) -> JsonResponse:
    user, denial = _require_access(request)
    if denial:
        return denial
    try:
        guide = get_guide(user, key)
        destinations = {
            card.key: card
            for card in allowed_destinations(user, "", None)
        }
        cards = [
            asdict(destinations[destination_key])
            for destination_key in guide.destination_keys
            if destination_key in destinations
        ]
    except (PermissionDenied, GuideNotFoundError) as error:
        return _handle_error(error)
    return JsonResponse(
        {
            "key": guide.key,
            "title": guide.title,
            "purpose": guide.purpose,
            "steps": guide.steps,
            "destinations": cards,
            "source": asdict(guide.source),
            "ai_generated": False,
        }
    )


@require_GET
def assistant_departments(request: HttpRequest) -> JsonResponse:
    user, denial = _require_access(request)
    if denial:
        return denial
    if _unknown_query_parameters(request, {"q", "page"}):
        return _error("invalid_input", 400, "Only q and page are supported.")
    query = request.GET.get("q", "")
    try:
        page = _page_number(request.GET.get("page"))
        departments, total = list_departments(user, query=query, page=page)
    except (AssistantApiValidationError, AssistantDirectoryValidationError) as error:
        return _handle_error(error)
    return JsonResponse(
        {
            "items": [asdict(department) for department in departments],
            "page": page,
            "page_size": 20,
            "total": total,
            "ai_generated": False,
        }
    )


@require_GET
def assistant_members(request: HttpRequest) -> JsonResponse:
    user, denial = _require_access(request)
    if denial:
        return denial
    if _unknown_query_parameters(request, {"department", "q", "page"}):
        return _error("invalid_input", 400, "Only department, q, and page are supported.")
    department = request.GET.get("department", "")
    query = request.GET.get("q", "")
    try:
        page = _page_number(request.GET.get("page"))
        members = find_department_members(
            user,
            department_key=department,
            query=query,
            page=page,
        )
    except (AssistantApiValidationError, AssistantDirectoryValidationError) as error:
        return _handle_error(error)
    except AssistantDirectoryNotFoundError as error:
        return _handle_error(error)
    return JsonResponse(
        {
            **asdict(members),
            "ai_generated": False,
        }
    )


def _message_request(request: HttpRequest) -> tuple[str, str | None, str, UUID, str]:
    if len(request.body) > MAX_REQUEST_BODY_BYTES:
        raise OverflowError
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AssistantApiValidationError("The request body must be valid JSON.") from None
    if not isinstance(payload, dict) or set(payload) != {
        "message",
        "current_page_key",
        "conversation_id",
        "request_id",
    }:
        raise AssistantApiValidationError("The request must contain only the supported fields.")
    message = payload["message"]
    current_page_key = payload["current_page_key"]
    conversation_id = payload["conversation_id"]
    request_id_value = payload["request_id"]
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_MESSAGE_LENGTH
        or not isinstance(conversation_id, str)
        or not conversation_id
        or len(conversation_id) > 64
        or (
            current_page_key is not None
            and (not isinstance(current_page_key, str) or len(current_page_key) > 64)
        )
        or not isinstance(request_id_value, str)
    ):
        raise AssistantApiValidationError("The assistant message or identifiers are invalid.")
    try:
        request_id = UUID(request_id_value)
    except (TypeError, ValueError):
        raise AssistantApiValidationError("Request ID must be a UUID.") from None
    return message.strip(), current_page_key, conversation_id, request_id, request_id_value


def _validate_current_page(user: User, current_page_key: str | None) -> str | None:
    if current_page_key is None:
        return None
    authorized_keys = {card.key for card in allowed_destinations(user, "", None)}
    if current_page_key not in authorized_keys:
        raise AssistantApiValidationError("The current page key is not available to this account.")
    return current_page_key


@require_POST
def assistant_messages(request: HttpRequest) -> JsonResponse:
    user, denial = _require_access(request)
    if denial:
        return denial
    request_id = None
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return _error("request_too_large", 413, "The assistant request exceeds 32 KiB.")
        except ValueError:
            return _error("invalid_input", 400, "The request length is invalid.")
    try:
        message, current_page_key, conversation_id, request_id, _ = _message_request(request)
        current_page_key = _validate_current_page(user, current_page_key)
        state = load_conversation(request)
        if conversation_id != state.id:
            return _error(
                "stale_conversation",
                409,
                "Start a new assistant conversation.",
                request_id=request_id,
            )
        if provider_readiness() != "configured":
            return _error(
                "not_configured",
                503,
                "The assistant provider is not configured.",
                request_id=request_id,
            )

        payload_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "message": message,
                    "current_page_key": current_page_key,
                    "conversation_id": conversation_id,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cached = load_cached_request_response(
            request,
            state,
            request_id,
            payload_fingerprint,
            timezone.now(),
        )
        if cached is not None:
            return JsonResponse(cached)
        lease = acquire_request(user, request_id, timezone.now())
    except OverflowError:
        return _error("request_too_large", 413, "The assistant request exceeds 32 KiB.")
    except (
        AssistantApiValidationError,
        AssistantDirectoryValidationError,
        AssistantRuntimeValidationError,
    ) as error:
        return _handle_error(error, request_id=request_id)
    except (AssistantRuntimeError, PermissionDenied) as error:
        return _handle_error(error, request_id=request_id)

    started_at = time.monotonic()
    try:
        provider = FoundryProvider()
        deadline = started_at + 45
        reply = answer_message(
            user,
            message,
            state,
            current_page_key=current_page_key,
            provider=provider,
            deadline=deadline,
        )
        response_data = {
            "status": "ok",
            **_conversation_payload(state),
            "request_id": str(request_id),
            "reply": _reply_payload(reply),
        }
        persisted = append_exchange_if_current(
            request,
            state,
            user_text=message,
            assistant_text=reply.text,
            request_id=request_id,
            request_fingerprint=payload_fingerprint,
            response_cache=response_data,
        )
        if not persisted:
            return _error(
                "stale_conversation",
                409,
                "The conversation changed while the assistant was responding.",
                request_id=request_id,
            )
        logger.info(
            "Assistant request completed request_id=%s elapsed_ms=%d outcome=success",
            request_id,
            int((time.monotonic() - started_at) * 1000),
        )
        return JsonResponse(response_data)
    except (
        AssistantProviderError,
        AssistantToolError,
        AssistantDirectoryValidationError,
        AssistantRuntimeError,
        PermissionDenied,
    ) as error:
        result = _handle_error(error, request_id=request_id)
        logger.warning(
            "Assistant request failed request_id=%s elapsed_ms=%d error=%s",
            request_id,
            int((time.monotonic() - started_at) * 1000),
            json.loads(result.content).get("code"),
        )
        return result
    finally:
        release_request(lease)


@require_POST
def assistant_conversation_reset(request: HttpRequest) -> JsonResponse:
    user, denial = _require_access(request)
    if denial:
        return denial
    try:
        state = reset_conversation(request)
    except (PermissionDenied, AssistantRuntimeError) as error:
        return _handle_error(error)
    return JsonResponse(_conversation_payload(state))
