from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.messages import get_messages
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from hub.models import Account, OutlookConnection, Request, RequestCommunication
from hub.services import outlook_drafts as drafts
from hub.services import outlook_threads as threads

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
OID = "33333333-3333-3333-3333-333333333333"
LINK = "https://outlook.office.com/mail/drafts/id/draft-1"
MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"


def graph_response(status, data):
    return Mock(status_code=status, json=Mock(return_value=data))


def search_filter(subject, draft):
    return f"subject eq '{subject}' and isDraft eq {'true' if draft else 'false'}"


@override_settings(SQR_OUTLOOK_TENANT_ID=TENANT, SQR_OUTLOOK_CLIENT_ID=CLIENT,
                   SQR_OUTLOOK_CLIENT_SECRET="test-client-secret",
                   SQR_OUTLOOK_REDIRECT_URI="https://example.com/sqr/outlook/callback/",
                   SQR_OUTLOOK_TOKEN_KEY=Fernet.generate_key().decode(),
                   SESSION_ENGINE="django.contrib.sessions.backends.db")
class RequestOutlookThreadTests(TestCase):
    def setUp(self):
        self.engineer = User.objects.create_user(
            username="thread_engineer", password="pass12345", role=User.Roles.ENGINEER,
            email="thread.engineer@example.com", first_name="Thread", last_name="Engineer",
        )
        self.backup = User.objects.create_user(
            username="thread_backup", password="pass12345", role=User.Roles.ENGINEER, email="thread.backup@example.com",
        )
        self.requestor = User.objects.create_user(
            username="thread_requestor", password="pass12345", role=User.Roles.REQUESTOR_ESS,
            email="thread.requestor@example.com", first_name="Thread", last_name="Requestor",
        )
        self.request_obj = Request.objects.create(
            requestor=self.requestor,
            account=Account.objects.create(name="Thread Account"),
            account_manager="Thread Requestor",
            product_category="Azure",
            engagement_type=Request.Engagement.SUPPORT,
            priority=Request.Priority.MEDIUM,
            engineer=self.engineer,
            backup_engineer=self.backup,
            status=Request.Status.COMPLETED,
            description="Threading support request.",
        )
        self.ack_subject = f"Re: {self.request_obj.reference_code} · Thread Account"
        self.assignment_subject = threads.assignment_subject(self.request_obj.reference_code)
        self.search = {}
        self.calls = []
        self.write_status = None

        self.msal_app = Mock()
        self.msal_app.get_accounts.return_value = [{"local_account_id": OID, "realm": TENANT}]
        self.msal_app.acquire_token_silent.return_value = {"access_token": "private-access-token"}
        self.msal_app.initiate_auth_code_flow.return_value = {
            "state": "random-state", "nonce": "nonce", "code_verifier": "pkce-verifier",
            "auth_uri": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state=random-state",
        }
        self.msal_app.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "private-access-token", "id_token_claims": {"tid": TENANT, "oid": OID},
        }
        patch.object(drafts.msal, "ConfidentialClientApplication", return_value=self.msal_app).start()
        patch.object(drafts.requests, "get", side_effect=self.graph_get).start()
        patch.object(threads.requests, "request", side_effect=self.graph_write).start()
        self.addCleanup(patch.stopall)

    def graph_get(self, url, **kwargs):
        return graph_response(200, {"value": self.search.get(kwargs["params"]["$filter"], [])})

    def graph_write(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json")))
        if self.write_status:
            return graph_response(self.write_status, {})
        if url.endswith("/createReply"):
            return graph_response(201, {"id": "reply-1"})
        if method == "PATCH":
            return graph_response(200, {"id": "reply-1", "webLink": LINK})
        return graph_response(201, {"id": "new-1", "webLink": LINK})

    def connect(self):
        return OutlookConnection.objects.create(
            user=self.engineer,
            session_hash=drafts.digest(self.client.session.session_key),
            tenant_id=TENANT,
            mailbox_oid=OID,
            encrypted_cache=drafts.encrypt_cache(drafts.msal.SerializableTokenCache()),
        )

    def acknowledge(self):
        return self.client.post(reverse("hub:request-outlook", args=[self.request_obj.pk]),
                                HTTP_REFERER=reverse("hub:dashboard"))

    def close(self):
        return self.client.post(reverse("hub:request-outlook-closing", args=[self.request_obj.pk]),
                                HTTP_REFERER=reverse("hub:request-manage-collab", args=[self.request_obj.pk]))

    @staticmethod
    def message_texts(response):
        return [str(item) for item in get_messages(response.wsgi_request)]

    def test_acknowledgement_replies_inside_assignment_thread(self):
        self.client.force_login(self.engineer)
        self.connect()
        self.search[search_filter(self.assignment_subject, False)] = [{"id": "assign-1", "sentDateTime": "2026-11-02T01:00:00Z"}]

        response = self.acknowledge()

        self.assertRedirects(response, LINK, fetch_redirect_response=False)
        self.assertEqual(self.calls[0][:2], ("POST", f"{MESSAGES_URL}/assign-1/createReply"))
        method, url, payload = self.calls[1]
        self.assertEqual((method, url), ("PATCH", f"{MESSAGES_URL}/reply-1"))
        self.assertEqual(payload["subject"], self.ack_subject)
        self.assertEqual(payload["toRecipients"], [
            {"emailAddress": {"address": "thread.engineer@example.com"}},
            {"emailAddress": {"address": "thread.requestor@example.com"}},
        ])
        self.assertTrue(RequestCommunication.objects.filter(
            request=self.request_obj, channel=RequestCommunication.Channel.OUTLOOK,
        ).exists())

    def test_acknowledgement_without_assignment_starts_new_thread_and_says_so(self):
        self.client.force_login(self.engineer)
        self.connect()

        response = self.acknowledge()

        self.assertRedirects(response, LINK, fetch_redirect_response=False)
        self.assertEqual([(method, url) for method, url, _ in self.calls], [("POST", MESSAGES_URL)])
        self.assertTrue(any("starts a new thread" in text for text in self.message_texts(response)))

    def test_closing_replies_to_sent_acknowledgement(self):
        self.client.force_login(self.engineer)
        self.connect()
        self.search[search_filter(self.ack_subject, False)] = [{"id": "ack-1", "sentDateTime": "2026-11-02T02:00:00Z"}]

        response = self.close()

        self.assertRedirects(response, LINK, fetch_redirect_response=False)
        self.assertEqual(self.calls[0][:2], ("POST", f"{MESSAGES_URL}/ack-1/createReply"))
        self.assertEqual(self.calls[1][2]["subject"], self.ack_subject)
        self.assertIn("has been completed and closed", self.calls[1][2]["body"]["content"])

    def test_closing_waits_for_unsent_acknowledgement_draft(self):
        self.client.force_login(self.engineer)
        self.connect()
        self.search[search_filter(self.ack_subject, True)] = [{"id": "draft-ack"}]

        response = self.close()

        self.assertEqual(response.url, reverse("hub:request-manage-collab", args=[self.request_obj.pk]))
        self.assertEqual(self.calls, [])
        self.assertTrue(any("Send the acknowledgement draft" in text for text in self.message_texts(response)))

    def test_missing_connection_redirects_to_sign_in_before_creating_drafts(self):
        self.client.force_login(self.engineer)

        response = self.acknowledge()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("hub:request-outlook-connect", args=[self.request_obj.pk])))
        self.assertEqual(self.calls, [])
        self.assertFalse(RequestCommunication.objects.exists())

    def test_microsoft_failure_falls_back_to_mailto_with_warning(self):
        self.client.force_login(self.engineer)
        self.connect()
        self.search[search_filter(self.assignment_subject, False)] = [{"id": "assign-1"}]
        self.write_status = 500

        with patch("hub.views.render", return_value=HttpResponse("OK")) as render:
            response = self.acknowledge()

        self.assertEqual(render.call_args.args[1], "hub/outlook_redirect.html")
        self.assertTrue(render.call_args.args[2]["mailto_url"].startswith("mailto:"))
        self.assertTrue(any("basic email was opened" in text for text in self.message_texts(response)))

    def test_connect_then_callback_stores_connection_and_returns_to_request(self):
        self.client.force_login(self.engineer)
        return_url = reverse("hub:request-manage-collab", args=[self.request_obj.pk])

        response = self.client.get(reverse("hub:request-outlook-connect", args=[self.request_obj.pk]), {"next": return_url})
        self.assertTrue(response.url.startswith(f"https://login.microsoftonline.com/{TENANT}/"))

        callback = self.client.get(reverse("hub:sqr-outlook-callback"),
                                   {"state": "random-state", "code": "authorization-code"})
        self.assertEqual(callback.url, return_url)
        self.assertTrue(OutlookConnection.objects.filter(user=self.engineer, tenant_id=TENANT, mailbox_oid=OID).exists())
