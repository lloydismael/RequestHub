from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from cryptography.fernet import Fernet
from django.contrib.messages import get_messages
from django.http import HttpResponse
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from accounts.models import User
from hub.models import OutlookConnection, OutlookOAuthAttempt, OutlookSqrDraft, SqrSubmission
from hub.outlook_views import FLOW_SESSION_KEY, OutlookCallbackView, OutlookDraftView
from hub.services import outlook_drafts as service


def placeholder(request, pk=None):
    return HttpResponse("preview")


urlpatterns = [path("accounts/login/", placeholder, name="login"), path("", include(([
    path("sqr/", placeholder, name="sqr"),
    path("sqr/<int:pk>/approval-email/formatted-draft/", placeholder, name="sqr-approval-email-formatted-draft"),
    path("sqr/<int:pk>/approval-email/microsoft/", OutlookDraftView.as_view(), name="sqr-approval-email-microsoft"),
    path("sqr/outlook/callback/", OutlookCallbackView.as_view(), name="sqr-outlook-callback"),
], "hub"), namespace="hub"))]

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
OID = "33333333-3333-3333-3333-333333333333"
LINK = "https://outlook.office.com/mail/drafts/id/draft-1"


@override_settings(ROOT_URLCONF=__name__, SQR_OUTLOOK_TENANT_ID=TENANT,
                   SQR_OUTLOOK_CLIENT_ID=CLIENT, SQR_OUTLOOK_CLIENT_SECRET="test-client-secret",
                   SQR_OUTLOOK_REDIRECT_URI="https://example.com/sqr/outlook/callback/",
                   SQR_OUTLOOK_TOKEN_KEY=Fernet.generate_key().decode(),
                   SESSION_ENGINE="django.contrib.sessions.backends.db")
class OutlookDraftTests(TestCase):
    def setUp(self):
        self.engineer = User.objects.create_user(username="engineer", role=User.Roles.ENGINEER, email="engineer@example.com")
        self.pm = User.objects.create_user(username="pm", role=User.Roles.PM_ESG, email="pm@example.com")
        self.submission = SqrSubmission.objects.create(engineer=self.engineer, pm_esg_reviewer=self.pm,
                                                       customer_name="Acme", project_title="Assessment",
                                                       status=SqrSubmission.Status.APPROVED)
        self.client.force_login(self.pm)
        self.url = reverse("hub:sqr-approval-email-microsoft", args=[self.submission.pk])
        self.callback = reverse("hub:sqr-outlook-callback")
        self.preview = reverse("hub:sqr-approval-email-formatted-draft", args=[self.submission.pk])
        self.app = Mock()
        self.app.initiate_auth_code_flow.return_value = {
            "state": "random-state", "nonce": "nonce", "code_verifier": "pkce-verifier",
            "auth_uri": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state=random-state",
        }
        self.app.get_accounts.return_value = [{"local_account_id": OID, "realm": TENANT}]
        self.app.acquire_token_silent.return_value = {"access_token": "private-access-token"}
        self.app.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "private-access-token", "id_token_claims": {"tid": TENANT, "oid": OID},
        }
        self.app_patch = patch.object(service.msal, "ConfidentialClientApplication", return_value=self.app)
        self.app_patch.start()
        self.addCleanup(self.app_patch.stop)
        self.get = patch.object(service.requests, "get").start()
        self.post = patch.object(service.requests, "post").start()
        self.addCleanup(patch.stopall)
        self.post.return_value = self.response(201, self.draft())
        self.get.return_value = self.response(200, self.draft())

    def response(self, status, data):
        return Mock(status_code=status, json=Mock(return_value=data))

    def draft(self, **changes):
        return {"id": "draft-1", "isDraft": True, "webLink": LINK, **changes}

    def connect(self):
        cache = service.msal.SerializableTokenCache()
        cache.deserialize('{"AccessToken":{"test":{"secret":"private-access-token"}}}')
        session_hash = service.digest(self.client.session.session_key)
        return OutlookConnection.objects.create(user=self.pm, session_hash=session_hash,
            tenant_id=TENANT, mailbox_oid=OID, encrypted_cache=service.encrypt_cache(cache))

    def authorize(self):
        # Real MSAL generates a fresh state for every attempt; reset fixed test state.
        OutlookOAuthAttempt.objects.all().delete()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://login.microsoftonline.com/"))
        return deepcopy(self.client.session[FLOW_SESSION_KEY])

    def callback_request(self, **params):
        return self.client.get(self.callback, {"state": "random-state", "code": "authorization-code", **params})

    def assert_safe_failure(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.preview)
        messages = " ".join(str(item) for item in get_messages(response.wsgi_request))
        self.assertNotIn("private-access-token", messages)
        self.assertNotIn("test-client-secret", messages)
        self.post.assert_not_called()

    def test_missing_setup(self):
        with override_settings(SQR_OUTLOOK_TOKEN_KEY=""):
            response = self.client.post(self.url)
        self.assert_safe_failure(response)
        self.assertIn("SQR_OUTLOOK", " ".join(str(item) for item in get_messages(response.wsgi_request)))
        self.app.initiate_auth_code_flow.assert_not_called()

    def test_configuration_rejects_untrusted_redirects_and_cookie_sessions(self):
        for uri in ["http://example.com/sqr/outlook/callback/", "https://example.com/elsewhere/",
                    "https://user:pass@example.com/sqr/outlook/callback/", "https://example.com/sqr/outlook/callback/?next=evil"]:
            with self.subTest(uri=uri), override_settings(SQR_OUTLOOK_REDIRECT_URI=uri):
                self.assertFalse(service.outlook_is_configured())
        with override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies"):
            self.assertFalse(service.outlook_is_configured())
        with override_settings(DEBUG=True, SQR_OUTLOOK_REDIRECT_URI="http://localhost:8000/sqr/outlook/callback/"):
            self.assertTrue(service.outlook_is_configured())

    def test_access_denied_and_unapproved(self):
        for user in [self.engineer, User.objects.create_user(username="other", role=User.Roles.PM_ESG)]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url).url, reverse("hub:sqr"))
        self.client.force_login(self.pm)
        self.submission.status = SqrSubmission.Status.FOR_PROCESSING
        self.submission.save()
        self.assertEqual(self.client.post(self.url).url, reverse("hub:sqr"))
        self.post.assert_not_called()
        self.app.initiate_auth_code_flow.assert_not_called()

    def test_admin_allowed_and_get_disallowed(self):
        admin = User.objects.create_user(username="admin", role=User.Roles.ADMIN)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.authorize()

    def test_login_required_and_csrf(self):
        from django.test import Client
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.pm)
        self.assertEqual(protected.post(self.url).status_code, 403)
        self.client.logout()
        callback_response = self.client.get(self.callback, {"code": "private-authorization-code", "state": "state"})
        self.assertEqual(callback_response.status_code, 302)
        self.assertNotIn("private-authorization-code", callback_response.url)
        self.assertEqual(callback_response["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.app.initiate_auth_code_flow.assert_not_called()

    def test_successful_oauth_encrypted_cache_and_resume(self):
        saved = self.authorize()
        self.app.initiate_auth_code_flow.assert_called_once_with(["Mail.ReadWrite"], redirect_uri="https://example.com/sqr/outlook/callback/")
        response = self.callback_request()
        self.assertEqual(response.url, self.preview + "?outlook=1")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.app.acquire_token_by_auth_code_flow.assert_called_once_with(saved["flow"], {"state": "random-state", "code": "authorization-code"})
        connection = OutlookConnection.objects.get()
        self.assertEqual(connection.user_id, self.pm.pk)
        self.assertEqual(connection.mailbox_oid, OID)
        self.assertNotIn("private-access-token", connection.encrypted_cache)
        self.assertNotIn(FLOW_SESSION_KEY, self.client.session)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SqrSubmission.Status.APPROVED)
        self.post.assert_not_called()

    def test_encryption_roundtrip_and_wrong_key(self):
        connection = self.connect()
        _, cache = service.client_and_cache(connection.encrypted_cache)
        self.assertIn("private-access-token", cache.serialize())
        self.assertNotIn("private-access-token", connection.encrypted_cache)
        with override_settings(SQR_OUTLOOK_TOKEN_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(service.OutlookSignInRequired):
                service.client_and_cache(connection.encrypted_cache)

    def test_invalid_state_and_missing_callback(self):
        self.authorize()
        self.assert_safe_failure(self.callback_request(state="wrong"))
        self.app.acquire_token_by_auth_code_flow.assert_not_called()
        self.assertEqual(self.callback_request().url, reverse("hub:sqr"))

    def test_missing_or_duplicate_state(self):
        for params in [{"code": "code"}, {"state": ["random-state", "random-state"], "code": "code"}]:
            self.authorize()
            self.assert_safe_failure(self.client.get(self.callback, params))
        self.app.acquire_token_by_auth_code_flow.assert_not_called()

    def test_expired_flow(self):
        self.authorize()
        session = self.client.session
        flow = session[FLOW_SESSION_KEY]
        flow["expires"] = (timezone.now() - timedelta(seconds=1)).timestamp()
        session[FLOW_SESSION_KEY] = flow
        session.save()
        self.assert_safe_failure(self.callback_request())
        self.app.acquire_token_by_auth_code_flow.assert_not_called()

    def test_expired_database_attempt(self):
        self.authorize()
        OutlookOAuthAttempt.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assert_safe_failure(self.callback_request())

    def test_user_and_session_binding(self):
        for field, value in [("user_id", self.engineer.pk), ("session_hash", "different")]:
            self.authorize()
            session = self.client.session
            flow = session[FLOW_SESSION_KEY]
            flow[field] = value
            session[FLOW_SESSION_KEY] = flow
            session.save()
            self.assert_safe_failure(self.callback_request())
        self.app.acquire_token_by_auth_code_flow.assert_not_called()

    def test_replayed_callback_server_snapshot(self):
        saved = self.authorize()
        self.assertEqual(self.callback_request().url, self.preview + "?outlook=1")
        session = self.client.session
        session[FLOW_SESSION_KEY] = saved
        session.save()
        self.assert_safe_failure(self.callback_request())
        self.app.acquire_token_by_auth_code_flow.assert_called_once()

    def test_msal_nonce_or_pkce_validation_failure(self):
        self.authorize()
        self.app.acquire_token_by_auth_code_flow.side_effect = ValueError("private-access-token nonce mismatch")
        self.assert_safe_failure(self.callback_request())
        self.assertFalse(OutlookConnection.objects.exists())

    def test_cancelled_and_wrong_tenant(self):
        self.authorize()
        self.assert_safe_failure(self.callback_request(error="access_denied", error_description="private-access-token"))
        self.authorize()
        self.app.acquire_token_by_auth_code_flow.return_value["id_token_claims"]["tid"] = CLIENT
        self.assert_safe_failure(self.callback_request())
        self.assertFalse(OutlookConnection.objects.exists())

    def test_full_html_cid_attachment_and_delegated_refresh(self):
        self.connect()
        response = self.client.post(self.url)
        self.assertEqual(response.url, LINK)
        self.app.acquire_token_silent.assert_called_once_with(["Mail.ReadWrite"], account={"local_account_id": OID, "realm": TENANT})
        args, kwargs = self.post.call_args
        self.assertEqual(args[0], "https://graph.microsoft.com/v1.0/me/messages")
        payload = kwargs["json"]
        self.assertEqual(payload["body"]["contentType"], "HTML")
        self.assertIn("cid:sqr-approval-logo@request-hub", payload["body"]["content"])
        self.assertIn("SQR Approval Advisory", payload["body"]["content"])
        attachment = payload["attachments"][0]
        self.assertEqual(attachment["@odata.type"], "#microsoft.graph.fileAttachment")
        self.assertTrue(attachment["isInline"])
        self.assertEqual(attachment["contentId"], "sqr-approval-logo@request-hub")
        self.assertTrue(attachment["contentBytes"])
        self.assertIn({"emailAddress": {"address": "engineer@example.com"}}, payload["toRecipients"])
        self.assertTrue(payload["subject"])
        self.assertEqual(service.SCOPES, ["Mail.ReadWrite"])
        self.app.acquire_token_for_client.assert_not_called()
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SqrSubmission.Status.APPROVED)

    def test_repeated_post_single_create_no_overwriting_user_edits(self):
        self.connect()
        self.assertEqual(self.client.post(self.url).url, LINK)
        self.submission.project_title = "Changed content"
        self.submission.save()
        self.assertEqual(self.client.post(self.url).url, LINK)
        self.post.assert_called_once()
        self.assertEqual(OutlookSqrDraft.objects.count(), 1)

    def test_silent_refresh_absent_requires_signin(self):
        self.connect()
        self.app.acquire_token_silent.return_value = {"error": "interaction_required"}
        self.authorize()
        self.post.assert_not_called()

    def test_refresh_exception_safe_preview(self):
        self.connect()
        self.app.acquire_token_silent.side_effect = requests.Timeout("private-access-token")
        self.assert_safe_failure(self.client.post(self.url))

    def test_timeout_reconcile_without_duplicate(self):
        self.connect()
        self.post.side_effect = requests.Timeout("private-access-token")
        first = self.client.post(self.url)
        self.assertEqual(first.url, self.preview)
        self.assertEqual(OutlookSqrDraft.objects.get().state, "pending")
        self.get.return_value = self.response(200, {"value": []})
        self.assertEqual(self.client.post(self.url).url, self.preview)
        self.get.return_value = self.response(200, {"value": [self.draft()]})
        self.assertEqual(self.client.post(self.url).url, LINK)
        self.post.assert_called_once()
        self.assertIn(service.MARKER_PROPERTY, self.get.call_args.kwargs["params"]["$filter"])

    def test_pending_crash_and_concurrent_owner_only_reconcile(self):
        connection = self.connect()
        OutlookSqrDraft.objects.create(user=self.pm, submission=self.submission, tenant_id=TENANT,
                                      mailbox_oid=OID, correlation_id=__import__("uuid").uuid4(), state="pending")
        self.get.return_value = self.response(200, {"value": []})
        self.assert_safe_failure(self.client.post(self.url))
        self.get.return_value = self.response(200, {"value": [self.draft()]})
        self.assertEqual(self.client.post(self.url).url, LINK)
        self.post.assert_not_called()

    def test_ambiguous_500_and_malformed_response_remain_pending(self):
        self.connect()
        self.post.return_value = self.response(500, {})
        self.assertEqual(self.client.post(self.url).url, self.preview)
        self.get.return_value = self.response(200, {"value": []})
        self.assertEqual(self.client.post(self.url).url, self.preview)
        self.post.assert_called_once()
        self.assertEqual(OutlookSqrDraft.objects.get().state, "pending")

    def test_explicit_rejected_create_allows_safe_retry(self):
        self.connect()
        self.post.return_value = self.response(403, {"error": "private-access-token"})
        self.assertEqual(self.client.post(self.url).url, self.preview)
        self.assertEqual(OutlookSqrDraft.objects.get().state, "new")
        self.post.return_value = self.response(201, self.draft())
        self.assertEqual(self.client.post(self.url).url, LINK)
        self.assertEqual(self.post.call_count, 2)

    def test_sent_draft_never_reopens_or_duplicates(self):
        self.connect()
        self.client.post(self.url)
        self.get.return_value = self.response(200, self.draft(isDraft=False))
        self.assertEqual(self.client.post(self.url).url, self.preview)
        self.assertEqual(OutlookSqrDraft.objects.get().state, "sent")
        self.assertEqual(self.client.post(self.url).url, self.preview)
        self.post.assert_called_once()

    def test_malformed_links_persist_id_without_duplicate(self):
        self.connect()
        self.post.return_value = self.response(201, self.draft(webLink="https://evil.example/draft"))
        self.assertEqual(self.client.post(self.url).url, self.preview)
        self.assertEqual(OutlookSqrDraft.objects.get().message_id, "draft-1")
        self.assertEqual(self.client.post(self.url).url, LINK)
        self.post.assert_called_once()

    def test_link_validation(self):
        for link in ["http://outlook.office.com/mail", "https://outlook.office.com.evil.example/", "//outlook.office.com/mail",
                     "https://evil@outlook.office.com/", "https://outlook.office.com:8443/mail", "https://outlook.office.com/\\evil",
                     "https://outlook.office.com/mail\n", None]:
            with self.subTest(link=link), self.assertRaises(service.OutlookError):
                service.safe_draft_link(link)
        self.assertEqual(service.safe_draft_link("https://outlook.office365.com/mail/drafts"), "https://outlook.office365.com/mail/drafts")

@override_settings(SQR_OUTLOOK_TOKEN_KEY=Fernet.generate_key().decode())
class OutlookConcurrentDraftTests(TransactionTestCase):
    def test_workers_share_durable_pending_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import close_old_connections, connections

        user = User.objects.create_user(username="concurrent-pm", role=User.Roles.PM_ESG)
        submission = SqrSubmission.objects.create(engineer=user, pm_esg_reviewer=user, customer_name="Acme", project_title="Concurrent")
        connection = OutlookConnection.objects.create(user=user, session_hash="session", tenant_id=TENANT,
            mailbox_oid=OID, encrypted_cache="encrypted")
        context = {"subject": "SQR", "html_draft": "<p>Full draft</p>", "recipients": ["engineer@example.com"]}
        started, release = Event(), Event()

        def accepted(*args, **kwargs):
            started.set()
            if not release.wait(15):
                raise AssertionError("Concurrent reconciliation did not finish")
            return Mock(status_code=201, json=Mock(return_value={"id": "draft-1", "isDraft": True, "webLink": LINK}))

        def worker():
            close_old_connections()
            try:
                return service.create_or_reuse_draft(user, submission, connection, "private-token", context)
            finally:
                connections.close_all()

        with patch.object(service.requests, "post", side_effect=accepted) as post:
            with patch.object(service.requests, "get", return_value=Mock(status_code=200, json=Mock(return_value={"value": []}))):
                with ThreadPoolExecutor(max_workers=2) as workers:
                    first = workers.submit(worker)
                    self.assertTrue(started.wait(15))
                    second = workers.submit(worker)
                    try:
                        with self.assertRaises(service.OutlookError):
                            second.result(timeout=15)
                    finally:
                        release.set()
                    self.assertEqual(first.result(timeout=15), LINK)
            post.assert_called_once()
        self.assertEqual(OutlookSqrDraft.objects.count(), 1)
