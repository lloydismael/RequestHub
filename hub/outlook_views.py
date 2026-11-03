"""Protected SQR delegated OAuth endpoints, separate from legacy email delivery."""
import hmac
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator

from hub.models import OutlookConnection, OutlookOAuthAttempt, Request
from hub.services.outlook_drafts import (
    OutlookError, OutlookSignInRequired, access_token, client_and_cache,
    create_or_reuse_draft, digest, encrypt_cache, require_configuration,
    SCOPES,
)
from hub.services.outlook_threads import request_email_access_error
from hub.views import SqrApprovalOutlookRedirectView, _get_sqr_approval_email_context

FLOW_SESSION_KEY = "sqr_outlook_flow"
FLOW_TTL = 600
REQUEST_FLOW = "request"


def preview(pk, resume=False):
    target = reverse("hub:sqr-approval-email-formatted-draft", args=[pk]) if pk else reverse("hub:sqr")
    return redirect(target + ("?outlook=1" if resume else ""))


def begin_authorization(request, pk, session_hash, kind="sqr", return_url=""):
    old = request.session.pop(FLOW_SESSION_KEY, None)
    if old and old.get("flow", {}).get("state"):
        OutlookOAuthAttempt.objects.filter(state_hash=digest(old["flow"]["state"])).update(consumed=True)
    app, _ = client_and_cache()
    flow = app.initiate_auth_code_flow(SCOPES, redirect_uri=settings.SQR_OUTLOOK_REDIRECT_URI)
    auth_url = urlsplit(flow.get("auth_uri", ""))
    if (not flow.get("state") or auth_url.scheme != "https" or auth_url.hostname != "login.microsoftonline.com"
            or auth_url.username or auth_url.password):
        raise OutlookError("Unable to start Microsoft 365 sign-in. Try again later.")
    expires = timezone.now() + timedelta(seconds=FLOW_TTL)
    OutlookOAuthAttempt.objects.create(user=request.user, session_hash=session_hash,
                                      state_hash=digest(flow["state"]), expires_at=expires)
    request.session[FLOW_SESSION_KEY] = {
        "flow": flow, "pk": pk, "kind": kind, "return_url": return_url, "user_id": request.user.pk,
        "session_hash": session_hash, "expires": expires.timestamp(),
    }
    return HttpResponseRedirect(flow["auth_uri"])


@method_decorator(never_cache, name="dispatch")
class OutlookDraftView(LoginRequiredMixin, View):
    http_method_names = ["post", "options"]

    def post(self, request, pk):
        validator = SqrApprovalOutlookRedirectView()
        submission = validator._get_submission(pk)
        error = validator._validate_access(request, submission)
        if error:
            messages.error(request, error)
            return redirect("hub:sqr")
        try:
            session_hash = require_configuration(request)
            try:
                token, connection = access_token(request, session_hash)
            except OutlookSignInRequired:
                return begin_authorization(request, pk, session_hash)
            context = _get_sqr_approval_email_context(submission)
            link = create_or_reuse_draft(request.user, submission, connection, token, context)
            return HttpResponseRedirect(link)
        except OutlookError as exc:
            messages.error(request, str(exc))
        except Exception:
            # Provider exceptions may include credentials, tokens or mailbox content.
            messages.error(request, "Microsoft 365 is unavailable or draft creation is unconfirmed. Try again later; no automatic duplicate will be created.")
        return preview(pk)


@method_decorator(never_cache, name="dispatch")
class OutlookCallbackView(LoginRequiredMixin, View):
    http_method_names = ["get", "options"]

    def dispatch(self, request, *args, **kwargs):
        # Never put an OAuth code in the login URL's next parameter or referrer.
        if not request.user.is_authenticated:
            response = redirect(settings.LOGIN_URL)
        else:
            response = super().dispatch(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response

    def get(self, request):
        saved = request.session.pop(FLOW_SESSION_KEY, None)
        pk = saved.get("pk") if isinstance(saved, dict) else None
        kind = saved.get("kind", "sqr") if isinstance(saved, dict) else "sqr"
        try:
            session_hash = require_configuration(request)
            if not saved:
                raise ValueError("Missing flow")
            state = request.GET.get("state", "")
            flow = saved["flow"]
            if (len(request.GET.getlist("state")) != 1 or not state
                    or not hmac.compare_digest(state, flow.get("state", ""))
                    or saved["user_id"] != request.user.pk
                    or saved["session_hash"] != session_hash
                    or timezone.now().timestamp() >= saved["expires"]):
                raise ValueError("Invalid flow")
            # Database consumption protects against callbacks racing with the same session snapshot.
            with transaction.atomic():
                attempt = OutlookOAuthAttempt.objects.select_for_update().filter(
                    state_hash=digest(state), user=request.user, session_hash=session_hash,
                    consumed=False, expires_at__gt=timezone.now(),
                ).first()
                if not attempt:
                    raise ValueError("Consumed flow")
                attempt.consumed = True
                attempt.save(update_fields=["consumed"])
            if request.GET.get("error") or not request.GET.get("code") or len(request.GET.getlist("code")) != 1:
                raise ValueError("Authorization declined")
            if kind == REQUEST_FLOW:
                target = Request.objects.filter(pk=pk).first()
                if target is None or request_email_access_error(request.user, target):
                    raise ValueError("Access changed")
            else:
                validator = SqrApprovalOutlookRedirectView()
                submission = validator._get_submission(pk)
                if validator._validate_access(request, submission):
                    raise ValueError("Access changed")
            app, cache = client_and_cache()
            # MSAL validates state, nonce and authorization code PKCE using the original server-side flow.
            result = app.acquire_token_by_auth_code_flow(flow, request.GET.dict())
            claims = result.get("id_token_claims", {})
            tenant = claims.get("tid", "")
            oid = claims.get("oid", "")
            if not result.get("access_token") or not oid or tenant.lower() != settings.SQR_OUTLOOK_TENANT_ID.lower():
                raise ValueError("Invalid identity")
            uuid.UUID(oid)
            OutlookConnection.objects.update_or_create(
                user=request.user, session_hash=session_hash,
                defaults={"tenant_id": tenant.lower(), "mailbox_oid": oid.lower(), "encrypted_cache": encrypt_cache(cache)},
            )
            if kind == REQUEST_FLOW:
                messages.success(request, "Microsoft 365 connected. Select the email action again to create the draft in the request thread.")
                return HttpResponseRedirect(saved.get("return_url") or reverse("hub:dashboard"))
            return preview(pk, resume=True)
        except OutlookError as exc:
            messages.error(request, str(exc))
        except Exception:
            hint = "Try again from the request." if kind == REQUEST_FLOW else "Start again from the draft preview."
            messages.error(request, f"Microsoft 365 sign-in could not be verified or was cancelled. {hint}")
        if kind == REQUEST_FLOW:
            return HttpResponseRedirect(saved.get("return_url") or reverse("hub:dashboard"))
        return preview(pk)


class RequestOutlookConnectView(LoginRequiredMixin, View):
    http_method_names = ["get", "options"]

    @method_decorator(never_cache)
    def get(self, request, pk):
        request_obj = get_object_or_404(Request, pk=pk)
        return_url = _safe_next_url(request)
        error = request_email_access_error(request.user, request_obj)
        if error:
            messages.error(request, error)
            return HttpResponseRedirect(return_url)
        try:
            return begin_authorization(request, pk, require_configuration(request), kind=REQUEST_FLOW, return_url=return_url)
        except OutlookError as exc:
            messages.error(request, str(exc))
        except Exception:
            messages.error(request, "Microsoft 365 sign-in could not be started. Try again later.")
        return HttpResponseRedirect(return_url)


def _safe_next_url(request):
    target = request.GET.get("next", "")
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse("hub:dashboard")
