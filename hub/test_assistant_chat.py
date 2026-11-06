import time
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from accounts.models import User
from hub.assistant_provider import (
    AssistantProviderError,
    FoundryProvider,
    provider_readiness,
)
from hub.assistant_types import (
    AssistantReply,
    AssistantMessage,
    ConversationState,
    ProviderTurn,
    SystemMessage,
    ToolCall,
    ToolDefinition,
    ToolMessage,
    UserMessage,
)
from hub.assistant_views import AssistantApiValidationError, _handle_error
from hub.assistant_service import (
    AssistantToolError,
    answer_message,
    dispatch_tool,
)
from hub.assistant_views import assistant_state


BASE_URL = "https://request-hub.openai.azure.com/openai/v1/"
DEPLOYMENT = "request-hub-assistant"
TOKEN_SCOPE = "https://ai.azure.com/.default"


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.responses = FakeResponses(response=response, error=error)


class AssistantProviderTests(SimpleTestCase):
    settings = override_settings(
        AI_ASSISTANT_NATIVE_ENABLED=True,
        AZURE_AI_BASE_URL=BASE_URL,
        AZURE_AI_DEPLOYMENT=DEPLOYMENT,
        AZURE_AI_AUTH_MODE="api_key",
        AZURE_AI_TOKEN_SCOPE="",
        AZURE_AI_API_KEY="test-provider-secret",
    )

    def setUp(self):
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def make_provider(self, client):
        patcher = patch("hub.assistant_provider.OpenAI", return_value=client)
        sdk_factory = patcher.start()
        self.addCleanup(patcher.stop)
        return FoundryProvider(), sdk_factory

    def make_status_error(self, status_code, code, message="provider secret"):
        from openai import APIStatusError

        response = Mock(status_code=status_code, request=Mock())
        return APIStatusError(
            message,
            response=response,
            body={"code": code, "message": message},
        )

    def test_missing_or_disabled_configuration_never_builds_a_client(self):
        with override_settings(AI_ASSISTANT_NATIVE_ENABLED=False):
            with patch("hub.assistant_provider.OpenAI") as sdk_factory:
                self.assertEqual(provider_readiness(), "not_configured")
                with self.assertRaises(AssistantProviderError):
                    FoundryProvider().complete((), (), deadline=time.monotonic() + 1)
                sdk_factory.assert_not_called()

        with override_settings(
            AI_ASSISTANT_NATIVE_ENABLED=True,
            AZURE_AI_BASE_URL="",
        ):
            with patch("hub.assistant_provider.OpenAI") as sdk_factory:
                self.assertEqual(provider_readiness(), "not_configured")
                sdk_factory.assert_not_called()

    def test_configured_provider_uses_exact_endpoint_and_deployment(self):
        client = FakeClient(
            response=SimpleNamespace(output_text="Hello", output=[], usage=None)
        )
        provider, sdk_factory = self.make_provider(client)

        result = provider.complete((), (), deadline=time.monotonic() + 10)

        self.assertEqual(provider_readiness(), "configured")
        self.assertEqual(result.text, "Hello")
        kwargs = sdk_factory.call_args.kwargs
        self.assertEqual(kwargs["base_url"], BASE_URL)
        self.assertEqual(kwargs["max_retries"], 0)
        request = client.responses.calls[0]
        self.assertEqual(request["model"], DEPLOYMENT)

    def test_managed_identity_uses_the_explicit_token_scope(self):
        client = FakeClient(
            response=SimpleNamespace(output_text="Hello", output=[], usage=None)
        )
        with override_settings(AZURE_AI_AUTH_MODE="managed_identity", AZURE_AI_TOKEN_SCOPE=TOKEN_SCOPE):
            with patch("hub.assistant_provider.ManagedIdentityCredential") as credential_factory:
                with patch(
                    "hub.assistant_provider.get_bearer_token_provider",
                    return_value="token-provider",
                ) as token_provider_factory:
                    with patch("hub.assistant_provider.OpenAI", return_value=client) as sdk_factory:
                        FoundryProvider()

        credential_factory.assert_called_once_with()
        token_provider_factory.assert_called_once_with(
            credential_factory.return_value,
            TOKEN_SCOPE,
        )
        self.assertEqual(sdk_factory.call_args.kwargs["api_key"], "token-provider")

    def test_function_call_ids_survive_input_and_output_conversion(self):
        client = FakeClient(
            response=SimpleNamespace(
                output_text="",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="provider-call-7",
                        name="get_guide",
                        arguments='{"key":"sqr"}',
                    )
                ],
                usage=SimpleNamespace(input_tokens=12, output_tokens=3),
            )
        )
        provider, _ = self.make_provider(client)
        messages = (
            SystemMessage("System rules"),
            UserMessage("Find the SQR guide"),
            AssistantMessage(
                None,
                (ToolCall("prior-call-2", "get_guide", '{"key":"reports"}'),),
            ),
            ToolMessage("prior-call-2", "Guide result"),
        )
        tools = (
            ToolDefinition(
                "get_guide",
                "Read an approved guide",
                {"type": "object", "properties": {}, "required": []},
            ),
        )

        result = provider.complete(messages, tools, deadline=time.monotonic() + 10)

        request_input = client.responses.calls[0]["input"]
        self.assertEqual(request_input[2]["call_id"], "prior-call-2")
        self.assertEqual(request_input[3]["call_id"], "prior-call-2")
        self.assertEqual(result.tool_calls[0].id, "provider-call-7")
        self.assertEqual(result.tool_calls[0].arguments_json, '{"key":"sqr"}')
        self.assertEqual(result.usage, {"input_tokens": 12, "output_tokens": 3})

    def test_timeout_and_expired_deadline_return_typed_errors(self):
        client = FakeClient(error=TimeoutError("secret timeout detail"))
        provider, _ = self.make_provider(client)

        with self.assertRaises(AssistantProviderError) as caught:
            provider.complete((), (), deadline=time.monotonic() + 10)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("secret", str(caught.exception))

        with self.assertRaises(AssistantProviderError) as caught:
            provider.complete((), (), deadline=time.monotonic() - 1)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(len(client.responses.calls), 1)

    def test_unsupported_capability_and_provider_limit_are_typed_and_redacted(self):
        for error, expected_code in (
            (self.make_status_error(400, "model_not_supported"), "unsupported_capability"),
            (self.make_status_error(429, "rate_limit_exceeded"), "provider_limit"),
        ):
            client = FakeClient(error=error)
            provider, _ = self.make_provider(client)

            with self.assertLogs("hub.assistant_provider", level="WARNING") as logs:
                with self.assertRaises(AssistantProviderError) as caught:
                    provider.complete((), (), deadline=time.monotonic() + 10)

            self.assertEqual(caught.exception.code, expected_code)
            self.assertNotIn("provider secret", "\n".join(logs.output))

    def test_final_answer_over_8000_characters_is_rejected(self):
        provider, _ = self.make_provider(
            FakeClient(response=SimpleNamespace(output_text="x" * 8001, output=[], usage=None))
        )

        with self.assertRaises(AssistantProviderError) as caught:
            provider.complete((), (), deadline=time.monotonic() + 10)

        self.assertEqual(caught.exception.code, "provider_limit")


class AssistantApiTestMixin:
    def setUp(self):
        self.settings = override_settings(
            AI_ASSISTANT_NATIVE_ENABLED=True,
            AZURE_AI_BASE_URL=BASE_URL,
            AZURE_AI_DEPLOYMENT=DEPLOYMENT,
            AZURE_AI_AUTH_MODE="api_key",
            AZURE_AI_TOKEN_SCOPE="",
            AZURE_AI_API_KEY="test-provider-secret",
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def create_user(self, username, role):
        return User.objects.create_user(
            username=username,
            password="test-password",
            role=role,
            show_chatbot=True,
            profile_completed=True,
        )


class AssistantToolTests(AssistantApiTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user("tool-admin", User.Roles.ADMIN)
        self.engineer = self.create_user("tool-engineer", User.Roles.ENGINEER)

    def tool_call(self, name, arguments, call_id="tool-call"):
        return ToolCall(
            id=call_id,
            name=name,
            arguments_json=json.dumps(arguments),
        )

    def test_destination_tools_return_only_server_authorized_named_routes(self):
        result = dispatch_tool(
            self.engineer,
            self.tool_call("find_app_destination", {"query": "sqr"}),
            current_page_key=None,
        )
        self.assertEqual([card.key for card in result.destinations], ["sqr"])
        self.assertEqual(result.destinations[0].url, reverse("hub:sqr"))

        admin_result = dispatch_tool(
            self.admin,
            self.tool_call("find_app_destination", {"query": "management"}),
            current_page_key=None,
        )
        self.assertEqual(admin_result.destinations[0].url, reverse("hub:management"))

    def test_tool_dispatch_rejects_unknown_tools_and_unrecognized_arguments(self):
        with self.assertRaises(AssistantToolError):
            dispatch_tool(
                self.admin,
                self.tool_call("query_unrestricted_table", {"table": "users"}),
                current_page_key=None,
            )
        with self.assertRaises(AssistantToolError):
            dispatch_tool(
                self.admin,
                self.tool_call(
                    "find_app_destination",
                    {"query": "reports", "raw_sql": "select *"},
                ),
                current_page_key=None,
            )

    def test_management_help_is_denied_to_unauthorized_roles(self):
        with self.assertRaises(PermissionDenied):
            dispatch_tool(
                self.engineer,
                self.tool_call("get_application_help", {"key": "management"}),
                current_page_key=None,
            )
        result = dispatch_tool(
            self.admin,
            self.tool_call("get_application_help", {"key": "management"}),
            current_page_key=None,
        )
        self.assertIn("administrator", result.text.lower())
        self.assertEqual(result.sources[0].key, "guide:management")

    def test_role_change_during_inference_is_checked_before_each_tool_call(self):
        state = ConversationState(id="conversation-1", generation=1, exchanges=())

        class RoleChangingProvider(_FakeProvider):
            def complete(inner_self, messages, tools, *, deadline):
                if inner_self.call_count == 0:
                    User.objects.filter(pk=self.admin.pk).update(
                        role=User.Roles.ENGINEER,
                    )
                return super(RoleChangingProvider, inner_self).complete(
                    messages,
                    tools,
                    deadline=deadline,
                )

        provider = RoleChangingProvider(
            [
                ProviderTurn(
                    None,
                    (
                        self.tool_call(
                            "get_application_help",
                            {"key": "management"},
                        ),
                    ),
                    (),
                    None,
                ),
                ProviderTurn("Done.", (), (), None),
                ]
        )

        with self.assertRaises(PermissionDenied):
            answer_message(
                self.admin,
                "How do I manage users?",
                state,
                current_page_key=None,
                provider=provider,
                deadline=time.monotonic() + 10,
            )

    def test_directory_tools_expose_only_approved_member_fields(self):
        member = self.create_user("directory-member", User.Roles.REQUESTOR)
        member.first_name = "Visible"
        member.last_name = "Member"
        member.department = "Engineering"
        member.email = "hidden@example.test"
        member.save()

        result = dispatch_tool(
            self.admin,
            self.tool_call(
                "find_department_members",
                {"department_key": "engineering", "query": "", "page": 1},
            ),
            current_page_key=None,
        )

        self.assertEqual(result.directory_row_count, 1)
        self.assertEqual(
            set(asdict(result.members.items[0])),
            {"display_name", "department", "application_role"},
        )
        self.assertNotIn("hidden@example.test", result.text)

    def test_answer_message_returns_server_owned_cards_and_validated_sources(self):
        provider = _FakeProvider(
            [
                ProviderTurn(
                    text=None,
                    tool_calls=(
                        self.tool_call("find_app_destination", {"query": "sqr"}),
                    ),
                    source_keys=(),
                    usage=None,
                ),
                ProviderTurn(
                    text="Open the Service Quotations workspace.",
                    tool_calls=(),
                    source_keys=(),
                    usage={"input_tokens": 2, "output_tokens": 3},
                ),
            ]
        )
        state = ConversationState(id="conversation-1", generation=1, exchanges=())

        reply = answer_message(
            self.engineer,
            "Where is SQR?",
            state,
            current_page_key=None,
            provider=provider,
            deadline=time.monotonic() + 10,
        )

        self.assertEqual(reply.destinations[0].url, reverse("hub:sqr"))
        self.assertEqual(reply.text, "Open the Service Quotations workspace.")
        self.assertEqual(provider.call_count, 2)

    def test_tool_round_and_call_limits_are_enforced(self):
        state = ConversationState(id="conversation-1", generation=1, exchanges=())
        repeated_calls = [
            ProviderTurn(
                text=None,
                tool_calls=(
                    self.tool_call(
                        "get_application_help",
                        {"key": "dashboard"},
                        call_id=f"call-{index}",
                    ),
                ),
                source_keys=(),
                usage=None,
            )
            for index in range(3)
        ]
        repeated_calls.append(
            ProviderTurn(text="Dashboard help.", tool_calls=(), source_keys=(), usage=None)
        )
        reply = answer_message(
            self.engineer,
            "Help me use the dashboard.",
            state,
            current_page_key=None,
            provider=_FakeProvider(repeated_calls),
            deadline=time.monotonic() + 10,
        )
        self.assertEqual(reply.text, "Dashboard help.")

        too_many_calls = ProviderTurn(
            text=None,
            tool_calls=tuple(
                self.tool_call(
                    "get_application_help",
                    {"key": "dashboard"},
                    call_id=f"too-many-{index}",
                )
                for index in range(6)
            ),
            source_keys=(),
            usage=None,
        )
        with self.assertRaises(AssistantToolError):
            answer_message(
                self.engineer,
                "Help me use the dashboard.",
                state,
                current_page_key=None,
                provider=_FakeProvider([too_many_calls]),
                deadline=time.monotonic() + 10,
            )

    def test_exact_five_calls_across_three_rounds_are_allowed(self):
        state = ConversationState(id="conversation-1", generation=1, exchanges=())
        turns = [
            ProviderTurn(
                text=None,
                tool_calls=tuple(
                    self.tool_call(
                        "get_application_help",
                        {"key": "dashboard"},
                        call_id=f"allowed-{round_number}-{call_number}",
                    )
                    for call_number in range(call_count)
                ),
                source_keys=(),
                usage=None,
            )
            for round_number, call_count in enumerate((2, 2, 1))
        ]
        turns.append(
            ProviderTurn(
                text="Use the Dashboard.",
                tool_calls=(),
                source_keys=("guide:dashboard",),
                usage=None,
            )
        )

        reply = answer_message(
            self.engineer,
            "How do I use the dashboard?",
            state,
            current_page_key=None,
            provider=_FakeProvider(turns),
            deadline=time.monotonic() + 10,
        )

        self.assertEqual(reply.text, "Use the Dashboard.")
        self.assertEqual([source.key for source in reply.sources], ["guide:dashboard"])

    def test_directory_evidence_is_capped_at_sixty_rows(self):
        department = "Assistant Row Limit"
        for index in range(61):
            member = self.create_user(
                f"row-limit-{index}",
                User.Roles.REQUESTOR,
            )
            member.department = department
            member.save(update_fields=["department"])

        def member_call(page):
            return self.tool_call(
                "find_department_members",
                {
                    "department_key": department,
                    "query": "",
                    "page": page,
                },
                call_id=f"members-{page}",
            )

        state = ConversationState(id="conversation-1", generation=1, exchanges=())
        allowed_turns = [
            ProviderTurn(
                text=None,
                tool_calls=tuple(member_call(page) for page in (1, 2, 3)),
                source_keys=(),
                usage=None,
            ),
            ProviderTurn(
                text="Here are the first three pages.",
                tool_calls=(),
                source_keys=(),
                usage=None,
            ),
        ]
        reply = answer_message(
            self.admin,
            "Find members in this department.",
            state,
            current_page_key=None,
            provider=_FakeProvider(allowed_turns),
            deadline=time.monotonic() + 10,
        )
        self.assertEqual(len(reply.members.items), 60)
        self.assertEqual(reply.members.page, 3)
        self.assertEqual(reply.members.page_size, 20)
        self.assertEqual(reply.members.total, 61)
        self.assertEqual(reply.members.department_key, department.casefold())
        self.assertEqual(reply.members.query, "")

        over_limit_turns = [
            ProviderTurn(
                text=None,
                tool_calls=tuple(member_call(page) for page in (1, 2, 3, 4)),
                source_keys=(),
                usage=None,
            ),
        ]
        with self.assertRaises(AssistantToolError) as caught:
            answer_message(
                self.admin,
                "Find members in this department.",
                state,
                current_page_key=None,
                provider=_FakeProvider(over_limit_turns),
                deadline=time.monotonic() + 10,
            )
        self.assertEqual(caught.exception.code, "directory_row_limit")

    def test_model_cannot_return_forged_links_sources_or_private_fields(self):
        state = ConversationState(id="conversation-1", generation=1, exchanges=())
        guide_call = self.tool_call(
            "get_application_help",
            {"key": "dashboard"},
        )
        for text in (
            "Open https://example.test/admin.",
            "See guide:invented for details.",
            "The member username is engineer1.",
            "Contact hidden@example.test.",
        ):
            with self.subTest(text=text):
                provider = _FakeProvider(
                    [
                        ProviderTurn(None, (guide_call,), (), None),
                        ProviderTurn(text, (), (), None),
                    ]
                )
                with self.assertRaises(AssistantToolError):
                    answer_message(
                        self.engineer,
                        "Show me the dashboard help.",
                        state,
                        current_page_key=None,
                        provider=provider,
                        deadline=time.monotonic() + 10,
                    )


class AssistantApiTests(AssistantApiTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user("api-admin", User.Roles.ADMIN)
        self.client.force_login(self.admin)

    def post_message(self, client=None, **overrides):
        client = client or self.client
        state = client.get(reverse("hub:assistant-state"))
        body = {
            "message": "Where can I find SQR?",
            "current_page_key": None,
            "conversation_id": state.json()["conversation_id"],
            "request_id": "d6aa46ab-e04a-4f0e-a81e-98b33f812f8a",
        }
        body.update(overrides)
        return client.post(
            reverse("hub:assistant-messages"),
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_anonymous_opted_out_and_disabled_assistant_access_are_gated(self):
        anonymous = self.client_class().get(reverse("hub:assistant-state"))
        self.assertEqual(anonymous.status_code, 401)

        inactive = self.create_user("inactive-assistant", User.Roles.REQUESTOR)
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        inactive_request = RequestFactory().get(reverse("hub:assistant-state"))
        inactive_request.user = inactive
        self.assertEqual(assistant_state(inactive_request).status_code, 403)

        self.admin.show_chatbot = False
        self.admin.save(update_fields=["show_chatbot"])
        response = self.client.get(reverse("hub:assistant-state"))
        self.assertEqual(response.status_code, 403)

        self.admin.show_chatbot = True
        self.admin.save(update_fields=["show_chatbot"])
        with override_settings(AI_ASSISTANT_NATIVE_ENABLED=False):
            response = self.client.get(reverse("hub:assistant-state"))
        self.assertEqual(response.status_code, 503)

    def test_every_supported_role_can_use_the_read_only_department_directory(self):
        for index, role in enumerate(User.Roles.values):
            with self.subTest(role=role):
                user = self.create_user(f"directory-role-{index}", role)
                client = self.client_class()
                client.force_login(user)

                response = client.get(reverse("hub:assistant-departments"))

                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["ai_generated"])

    def test_state_and_static_guides_work_while_model_is_unconfigured(self):
        with override_settings(AZURE_AI_BASE_URL=""):
            state = self.client.get(reverse("hub:assistant-state"))
            guide = self.client.get(
                reverse("hub:assistant-guide", kwargs={"key": "dashboard"})
            )

        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["provider_status"], "not_configured")
        self.assertEqual(guide.status_code, 200)
        self.assertFalse(guide.json()["ai_generated"])

    def test_invalid_json_message_size_and_body_size_have_distinct_errors(self):
        empty = self.client.post(
            reverse("hub:assistant-messages"),
            data="",
            content_type="application/json",
        )
        malformed = self.client.post(
            reverse("hub:assistant-messages"),
            data="{",
            content_type="application/json",
        )
        over_message_limit = self.post_message(message="x" * 4_001)
        with patch(
            "hub.assistant_views.answer_message",
            return_value=AssistantReply(
                text="The request was accepted.",
                destinations=(),
                departments=(),
                members=None,
                sources=(),
            ),
        ):
            at_message_limit = self.post_message(
                message="x" * 4_000,
                request_id="001e8400-e29b-41d4-a716-446655440000",
            )
        oversized_body = self.client.post(
            reverse("hub:assistant-messages"),
            data=b" " * (32 * 1024 + 1),
            content_type="application/json",
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(over_message_limit.status_code, 400)
        self.assertEqual(at_message_limit.status_code, 200)
        self.assertEqual(oversized_body.status_code, 413)

    def test_errors_use_the_public_json_contract(self):
        response = self.client.post(
            reverse("hub:assistant-messages"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(
            response.json(),
            {
                "status": "error",
                "code": "invalid_input",
                "message": "The assistant request is invalid.",
                "request_id": response.json()["request_id"],
            },
        )

    def test_validation_error_details_are_not_returned_to_clients(self):
        response = _handle_error(AssistantApiValidationError("internal implementation detail"))

        self.assertEqual(
            json.loads(response.content)["message"],
            "The assistant request is invalid.",
        )
        self.assertNotIn("internal implementation detail", response.content.decode())

    def test_forged_conversation_and_invalid_pagination_are_rejected(self):
        forged = self.post_message(conversation_id="other-conversation")
        invalid_page = self.client.get(
            reverse("hub:assistant-departments"),
            {"page": "0"},
        )

        self.assertEqual(forged.status_code, 409)
        self.assertEqual(invalid_page.status_code, 400)

    def test_csrf_failure_is_json_and_password_change_gate_is_scoped(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        csrf = csrf_client.post(
            reverse("hub:assistant-messages"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(csrf.status_code, 403)
        self.assertEqual(csrf.json()["code"], "csrf_failed")

        self.admin.must_change_password = True
        self.admin.save(update_fields=["must_change_password"])
        denial = self.client.get(reverse("hub:assistant-state"))
        ordinary = self.client.get(reverse("hub:dashboard"))
        self.assertEqual(denial.status_code, 403)
        self.assertEqual(denial.json()["code"], "password_change_required")
        self.assertEqual(denial.json()["profile_url"], reverse("accounts:update"))
        self.assertEqual(ordinary.status_code, 302)

    def test_password_change_gate_prevents_model_call_for_message_requests(self):
        state = self.client.get(reverse("hub:assistant-state")).json()
        body = {
            "message": "Where is SQR?",
            "current_page_key": None,
            "conversation_id": state["conversation_id"],
            "request_id": "d6aa46ab-e04a-4f0e-a81e-98b33f812f8a",
        }
        self.admin.must_change_password = True
        self.admin.save(update_fields=["must_change_password"])

        with patch("hub.assistant_views.answer_message") as answer:
            denial = self.client.post(
                reverse("hub:assistant-messages"),
                data=json.dumps(body),
                content_type="application/json",
            )

        self.assertEqual(denial.status_code, 403)
        self.assertEqual(denial.json()["code"], "password_change_required")
        self.assertEqual(denial.json()["profile_url"], reverse("accounts:update"))
        answer.assert_not_called()

    def test_rollout_reset_authorization_and_provider_failure_release_are_integrated(self):
        with override_settings(AI_ASSISTANT_NATIVE_ENABLED=False):
            legacy = self.client.get(reverse("hub:dashboard"))
            disabled_state = self.client.get(reverse("hub:assistant-state"))

        self.assertContains(legacy, 'id="ai-chat-iframe"')
        self.assertNotContains(legacy, 'class="rh-assistant"')
        self.assertEqual(disabled_state.status_code, 503)

        opted_out = self.create_user("rollout-opted-out", User.Roles.REQUESTOR)
        opted_out.show_chatbot = False
        opted_out.save(update_fields=["show_chatbot"])
        opted_out_client = self.client_class()
        opted_out_client.force_login(opted_out)
        self.assertEqual(
            opted_out_client.get(reverse("hub:assistant-state")).status_code,
            403,
        )

        native = self.client.get(reverse("hub:dashboard"))
        state = self.client.get(reverse("hub:assistant-state")).json()
        self.assertContains(native, 'class="rh-assistant"')
        self.assertNotContains(native, 'id="ai-chat-iframe"')
        self.assertEqual(state["provider_status"], "configured")

        reset = self.client.post(reverse("hub:assistant-reset"))
        reset_state = self.client.get(reverse("hub:assistant-state")).json()
        self.assertEqual(reset.status_code, 200)
        self.assertNotEqual(state["conversation_id"], reset_state["conversation_id"])

        reply = AssistantReply(
            text="Use the Service Quotations workspace.",
            destinations=(),
            departments=(),
            members=None,
            sources=(),
        )
        with patch(
            "hub.assistant_views.answer_message",
            side_effect=[
                AssistantProviderError("upstream_failure", "connection"),
                reply,
            ],
        ) as answer:
            failed = self.post_message(request_id="d6aa46ab-e04a-4f0e-a81e-98b33f812f8a")
            recovered = self.post_message(request_id="001e8400-e29b-41d4-a716-446655440000")

        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["code"], "upstream_failure")
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(answer.call_count, 2)
        self.assertContains(self.client.get(reverse("hub:dashboard")), 'class="rh-assistant"')

    def test_duplicate_request_reuses_response_without_a_second_provider_call(self):
        response = AssistantReply(
            text="Use the Service Quotations workspace.",
            destinations=(),
            departments=(),
            members=None,
            sources=(),
        )
        with patch("hub.assistant_views.answer_message", return_value=response) as answer:
            first = self.post_message()
            second = self.post_message()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["status"], "ok")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(answer.call_count, 1)

    def test_in_flight_duplicate_request_receives_a_conflict(self):
        response = AssistantReply(
            text="Use the Service Quotations workspace.",
            destinations=(),
            departments=(),
            members=None,
            sources=(),
        )
        nested_responses = []

        def answer_while_lease_is_active(*args, **kwargs):
            nested_responses.append(
                self.post_message(
                    request_id="d6aa46ab-e04a-4f0e-a81e-98b33f812f8a",
                )
            )
            return response

        with patch(
            "hub.assistant_views.answer_message",
            side_effect=answer_while_lease_is_active,
        ) as answer:
            first = self.post_message()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(nested_responses), 1)
        self.assertEqual(nested_responses[0].status_code, 409)
        self.assertEqual(nested_responses[0].json()["code"], "request_conflict")
        self.assertEqual(answer.call_count, 1)

    def test_reusing_a_completed_request_id_for_different_content_conflicts(self):
        response = AssistantReply(
            text="Use the Service Quotations workspace.",
            destinations=(),
            departments=(),
            members=None,
            sources=(),
        )
        with patch("hub.assistant_views.answer_message", return_value=response) as answer:
            first = self.post_message()
            conflicting = self.post_message(message="A different request.")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflicting.status_code, 409)
        self.assertEqual(conflicting.json()["code"], "request_conflict")
        self.assertEqual(answer.call_count, 1)


class _FakeProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.call_count = 0

    def complete(self, messages, tools, *, deadline):
        self.call_count += 1
        if not self.turns:
            raise AssertionError("Unexpected provider call.")
        return self.turns.pop(0)
