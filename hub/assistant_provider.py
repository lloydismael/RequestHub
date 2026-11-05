import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Literal, Sequence
from urllib.parse import urlsplit

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from django.conf import settings
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from hub.assistant_types import (
    AssistantMessage,
    ProviderMessage,
    ProviderTurn,
    ToolCall,
    ToolDefinition,
    ToolMessage,
    SystemMessage,
    UserMessage,
)


logger = logging.getLogger("hub.assistant_provider")
MAX_PROVIDER_INPUT_BYTES = 64 * 1024
MAX_PROVIDER_OUTPUT_CHARS = 8_000
MAX_PROVIDER_TOOL_CALLS = 5
MAX_PROVIDER_TOOL_ARGUMENT_BYTES = 16 * 1024
MAX_TOOL_NAME_LENGTH = 64
MAX_TOOL_DESCRIPTION_LENGTH = 1_024
PROVIDER_TIMEOUT_SECONDS = 45
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


_SAFE_MESSAGES = {
    "not_configured": "The assistant provider is not configured.",
    "invalid_configuration": "The assistant provider configuration is invalid.",
    "invalid_request": "The assistant request is invalid or exceeds its supported size.",
    "timeout": "The assistant provider did not respond before the request deadline.",
    "authentication_failed": "The assistant provider authentication failed.",
    "provider_limit": "The assistant provider rejected the request due to a service limit.",
    "unsupported_capability": "The selected assistant deployment does not support this request.",
    "upstream_failure": "The assistant provider request failed.",
    "invalid_response": "The assistant provider returned an unsupported response.",
}


class AssistantProviderError(RuntimeError):
    def __init__(self, code: str, diagnostic_category: str):
        if code not in _SAFE_MESSAGES:
            raise ValueError("Unknown assistant provider error code.")
        super().__init__(_SAFE_MESSAGES[code])
        self.code = code
        self.diagnostic_category = diagnostic_category


@dataclass(frozen=True)
class _ProviderConfiguration:
    base_url: str
    deployment: str
    auth_mode: Literal["managed_identity", "api_key"]
    token_scope: str
    api_key: str


def _read_configuration() -> _ProviderConfiguration | None:
    if not settings.AI_ASSISTANT_NATIVE_ENABLED:
        return None

    base_url = settings.AZURE_AI_BASE_URL
    deployment = settings.AZURE_AI_DEPLOYMENT
    auth_mode = settings.AZURE_AI_AUTH_MODE
    token_scope = settings.AZURE_AI_TOKEN_SCOPE
    api_key = settings.AZURE_AI_API_KEY
    if not base_url or not deployment:
        return None
    if auth_mode == "managed_identity" and not token_scope:
        return None
    if auth_mode == "api_key" and not api_key:
        return None
    if auth_mode not in {"managed_identity", "api_key"}:
        raise AssistantProviderError("invalid_configuration", "auth_mode")

    try:
        endpoint = urlsplit(base_url)
        endpoint_port = endpoint.port
    except ValueError:
        raise AssistantProviderError("invalid_configuration", "base_url") from None
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or not endpoint.hostname.lower().endswith(".openai.azure.com")
        or endpoint.path != "/openai/v1/"
        or endpoint.username is not None
        or endpoint.password is not None
        or endpoint_port is not None
        or endpoint.query
        or endpoint.fragment
    ):
        raise AssistantProviderError("invalid_configuration", "base_url")
    if any(character.isspace() for character in deployment):
        raise AssistantProviderError("invalid_configuration", "deployment")
    if auth_mode == "managed_identity" and any(character.isspace() for character in token_scope):
        raise AssistantProviderError("invalid_configuration", "token_scope")

    return _ProviderConfiguration(
        base_url=base_url,
        deployment=deployment,
        auth_mode=auth_mode,
        token_scope=token_scope,
        api_key=api_key,
    )


def provider_readiness() -> Literal["not_configured", "configured"]:
    try:
        return "configured" if _read_configuration() is not None else "not_configured"
    except AssistantProviderError as error:
        logger.warning(
            "Assistant provider configuration rejected category=%s",
            error.diagnostic_category,
        )
        return "not_configured"


def _create_client(configuration: _ProviderConfiguration) -> OpenAI:
    if configuration.auth_mode == "managed_identity":
        credential = ManagedIdentityCredential()
        api_key = get_bearer_token_provider(credential, configuration.token_scope)
    else:
        api_key = configuration.api_key
    return OpenAI(
        base_url=configuration.base_url,
        api_key=api_key,
        timeout=PROVIDER_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _tool_argument_object(arguments_json: str) -> dict[str, object]:
    if not isinstance(arguments_json, str):
        raise AssistantProviderError("invalid_request", "tool_arguments")
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, ValueError):
        raise AssistantProviderError("invalid_request", "tool_arguments") from None
    if not isinstance(arguments, dict):
        raise AssistantProviderError("invalid_request", "tool_arguments")
    return arguments


def _provider_input(messages: Sequence[ProviderMessage]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for message in messages:
        if isinstance(message, (SystemMessage, UserMessage)):
            result.append({"role": message.role, "content": message.content})
        elif isinstance(message, AssistantMessage):
            if message.content is not None:
                result.append({"role": "assistant", "content": message.content})
            for call in message.tool_calls:
                if not call.id or not call.name:
                    raise AssistantProviderError("invalid_request", "tool_call")
                result.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(
                            _tool_argument_object(call.arguments_json),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
        elif isinstance(message, ToolMessage):
            if not message.tool_call_id:
                raise AssistantProviderError("invalid_request", "tool_result")
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
        else:
            raise AssistantProviderError("invalid_request", "message_type")
    return result


def _provider_tools(tools: Sequence[ToolDefinition]) -> list[dict[str, object]]:
    result = []
    for tool in tools:
        if (
            not _TOOL_NAME_PATTERN.fullmatch(tool.name)
            or len(tool.description) > MAX_TOOL_DESCRIPTION_LENGTH
            or not isinstance(tool.arguments_schema, dict)
        ):
            raise AssistantProviderError("invalid_request", "tool_definition")
        result.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.arguments_schema),
                "strict": True,
            }
        )
    return result


def _error_code(error: APIStatusError) -> tuple[str, str]:
    status = error.status_code
    body = getattr(error, "body", None)
    code = getattr(error, "code", None)
    details = f"{code or ''} {body or ''}".lower()
    if status in {401, 403}:
        return "authentication_failed", "authentication"
    if status == 429 or any(
        marker in details
        for marker in ("rate_limit", "context_length", "token limit", "too many tokens")
    ):
        return "provider_limit", "provider_limit"
    if any(
        marker in details
        for marker in ("model_not_supported", "model not supported", "unsupported")
    ):
        return "unsupported_capability", "unsupported_capability"
    if status >= 500:
        return "upstream_failure", "server"
    return "upstream_failure", "request"


class FoundryProvider:
    def __init__(self):
        self._configuration = _read_configuration()
        self._client = (
            _create_client(self._configuration)
            if self._configuration is not None
            else None
        )

    def complete(
        self,
        messages: Sequence[ProviderMessage],
        tools: Sequence[ToolDefinition],
        *,
        deadline: float,
    ) -> ProviderTurn:
        if self._configuration is None or self._client is None:
            raise AssistantProviderError("not_configured", "disabled_or_incomplete")
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            raise AssistantProviderError("invalid_request", "deadline")
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise AssistantProviderError("timeout", "deadline")

        provider_messages = _provider_input(messages)
        provider_tools = _provider_tools(tools)
        try:
            input_bytes = len(
                json.dumps(
                    {"input": provider_messages, "tools": provider_tools},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, ValueError):
            raise AssistantProviderError("invalid_request", "serialization") from None
        if input_bytes > MAX_PROVIDER_INPUT_BYTES:
            raise AssistantProviderError("invalid_request", "input_limit")

        try:
            response = self._client.responses.create(
                model=self._configuration.deployment,
                input=provider_messages,
                tools=provider_tools or None,
                max_output_tokens=4_096,
                timeout=min(timeout, PROVIDER_TIMEOUT_SECONDS),
            )
        except (APITimeoutError, TimeoutError):
            self._log_failure("timeout")
            raise AssistantProviderError("timeout", "timeout") from None
        except ClientAuthenticationError:
            self._log_failure("authentication")
            raise AssistantProviderError("authentication_failed", "authentication") from None
        except APIStatusError as error:
            code, category = _error_code(error)
            self._log_failure(category)
            raise AssistantProviderError(code, category) from None
        except APIConnectionError:
            self._log_failure("connection")
            raise AssistantProviderError("upstream_failure", "connection") from None

        return self._convert_response(response)

    @staticmethod
    def _log_failure(category: str) -> None:
        logger.warning("Assistant provider request failed category=%s", category)

    @staticmethod
    def _convert_response(response) -> ProviderTurn:
        text = getattr(response, "output_text", None)
        if text is not None and not isinstance(text, str):
            raise AssistantProviderError("invalid_response", "text_type")
        if text is not None and len(text) > MAX_PROVIDER_OUTPUT_CHARS:
            raise AssistantProviderError("provider_limit", "output_limit")

        output = getattr(response, "output", None)
        if not isinstance(output, (list, tuple)):
            raise AssistantProviderError("invalid_response", "output_type")
        tool_calls = []
        argument_bytes = 0
        for item in output:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                call_id = getattr(item, "call_id", None)
                name = getattr(item, "name", None)
                arguments = getattr(item, "arguments", None)
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or not isinstance(name, str)
                    or not _TOOL_NAME_PATTERN.fullmatch(name)
                    or not isinstance(arguments, str)
                ):
                    raise AssistantProviderError("invalid_response", "tool_call")
                try:
                    _tool_argument_object(arguments)
                except AssistantProviderError:
                    raise AssistantProviderError("invalid_response", "tool_arguments") from None
                argument_bytes += len(arguments.encode("utf-8"))
                if (
                    len(tool_calls) >= MAX_PROVIDER_TOOL_CALLS
                    or argument_bytes > MAX_PROVIDER_TOOL_ARGUMENT_BYTES
                ):
                    raise AssistantProviderError("provider_limit", "tool_output_limit")
                tool_calls.append(ToolCall(call_id, name, arguments))
            elif item_type not in {"message", "reasoning", "refusal"}:
                raise AssistantProviderError("invalid_response", "output_item_type")

        usage = getattr(response, "usage", None)
        token_usage = None
        if usage is not None:
            values = {}
            for key in ("input_tokens", "output_tokens"):
                value = getattr(usage, key, None)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    values[key] = value
            token_usage = values or None
        if not text and not tool_calls:
            raise AssistantProviderError("invalid_response", "empty_response")
        return ProviderTurn(
            text=text,
            tool_calls=tuple(tool_calls),
            source_keys=(),
            usage=token_usage,
        )
