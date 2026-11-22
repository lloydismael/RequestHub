"""Request acknowledgement endpoint."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views import View

from hub import views as legacy_views
from hub.models import Request
from hub.services import request_lifecycle
from hub.views.common import _safe_return_url, logger
<<<<<<< HEAD
from hub.views.requests import (
=======
from hub.views.request_views import (
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    RequestCollaborativeManageView,
    RequestOutlookRedirectView,
    _microsoft_sign_in_redirect,
)


class RequestLifecycleAcknowledgeView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Record acknowledgement for the submitted assignment revision and open its draft."""
        try:
            expected_revision = int(request.POST.get("assignment_revision", ""))
        except (TypeError, ValueError):
            messages.error(request, "The assignment version is invalid. Refresh and try again.")
            return redirect("hub:request-manage-collab", pk=pk)

        request_obj = legacy_views.get_object_or_404(
            Request.objects.select_related("engineer", "requestor"),
            pk=pk,
        )
        engineer_email = request_obj.engineer.email if request_obj.engineer and request_obj.engineer.email else None
        manager_email = request_obj.requestor.email if request_obj.requestor and request_obj.requestor.email else None
        if not engineer_email or not manager_email:
            messages.error(
                request,
                "Unable to draft an acknowledgement email. Ensure the engineer and requestor both have emails configured.",
            )
            return redirect("hub:request-manage-collab", pk=pk)

        sign_in_response = _microsoft_sign_in_redirect(request, request_obj, _safe_return_url(request, "hub:dashboard"))
        if sign_in_response is not None:
            return sign_in_response
        try:
            request_lifecycle.acknowledge_request(
                pk,
                actor=request.user,
                expected_revision=expected_revision,
            )
        except PermissionDenied as exc:
            messages.error(request, str(exc))
            return self._acknowledge_failure(request, pk)
        except request_lifecycle.LifecycleConflictError as exc:
            messages.error(request, "; ".join(exc.messages))
            return self._acknowledge_failure(request, pk)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return self._acknowledge_failure(request, pk)
        except Exception:  # noqa: BLE001 — surface unexpected DB failures instead of a raw 500
            logger.exception("Acknowledge failed for request %s (user %s)", pk, request.user)
            messages.error(
                request,
                "We could not record your acknowledgement right now. "
                "The request has NOT been marked as acknowledged — please refresh and try again.",
            )
            return self._acknowledge_failure(request, pk)
        return RequestOutlookRedirectView().post(request, pk, allow_existing_draft=True)

    def _acknowledge_failure(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Re-render the collab page so the engineer sees the authoritative
        lifecycle state together with the error, instead of a bare redirect.
        """
        try:
            request_obj = RequestCollaborativeManageView()
            request_obj.request = request
            request_obj.kwargs = {"pk": pk}
            request_obj.args = ()
            obj = request_obj.get_object()
            context = request_obj.get_context_data(obj)
            return legacy_views.render(request, request_obj.template_name, context)
        except Exception:  # noqa: BLE001 — fall back to a redirect if re-render fails
            return redirect("hub:request-manage-collab", pk=pk)
