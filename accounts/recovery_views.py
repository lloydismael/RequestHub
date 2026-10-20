"""Administrator-only, CSRF-protected local backup and recovery endpoints."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from hub.mixins import AdminRequiredMixin
from hub.models import Request

from .models import BackupSnapshot, RecoveryEvent, User
from . import recovery


def recovery_context(request):
    if not request.user.is_authenticated or request.user.role != User.Roles.ADMIN:
        return {}
    query = request.GET.get("deleted_q", request.GET.get("deleted_query", "")).strip()[:200]
    deleted = Request.all_objects.filter(is_deleted=True).select_related("account", "requestor", "engineer").order_by("-deleted_at", "-pk")
    if query:
        deleted = deleted.filter(Q(reference_code__icontains=query) | Q(account__name__icontains=query) |
                                 Q(description__icontains=query) | Q(requestor__username__icontains=query))
    snapshots = list(BackupSnapshot.objects.select_related("created_by").defer("payload").all()[:10])
    return {"backup_snapshots": snapshots, "latest_backup": snapshots[0] if snapshots else None,
            "recovery_events": RecoveryEvent.objects.select_related("actor", "snapshot").defer("snapshot__payload").all()[:20],
            "deleted_requests": Paginator(deleted, 20).get_page(request.GET.get("deleted_page")),
            "deleted_query": query, "deleted_requests_query": query,
            "recovery_confirmation_phrase": recovery.CONFIRMATION_PHRASE,
            "backup_exclusions": recovery.EXCLUSIONS, "backup_max_bytes": recovery.max_bytes()}


def back(request, error=None, success=None):
    if error:
        messages.error(request, error)
    if success:
        messages.success(request, success)
    return redirect(reverse("accounts:update") + "#backup")


@method_decorator([transaction.non_atomic_requests, never_cache, csrf_protect], name="dispatch")
class RecoveryAdminView(AdminRequiredMixin, View):
    def test_func(self):
        return super().test_func() and self.request.user.is_active


class BackupDataView(RecoveryAdminView):
    def post(self, request):
        try:
            snapshot = recovery.create_snapshot(request.user, request.POST.get("reason", "Manual backup"))
        except Exception:
            return back(request, error="Backup failed. No ready snapshot was created; see recovery audit history.")
        return back(request, success=f"Snapshot #{snapshot.pk} created. Download it from the backup history.")


class SnapshotDownloadView(RecoveryAdminView):
    def get(self, request, pk):
        snapshot = get_object_or_404(BackupSnapshot, pk=pk, status="ready")
        if recovery.checksum(snapshot.payload) != snapshot.checksum:
            recovery.audit(request.user, "download", "failed", snapshot, error="Stored snapshot checksum mismatch")
            return back(request, error="Stored snapshot failed its integrity check.")
        recovery.audit(request.user, "download", "success", snapshot)
        response = HttpResponse(recovery.canonical(snapshot.payload), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="request-hub-snapshot-{snapshot.pk}.json"'
        response["X-Content-Type-Options"] = "nosniff"
        return response


def preview_response(request, payload, snapshot=None):
    preview, report = recovery.create_preview(request.user, payload, snapshot=snapshot)
    return render(request, "accounts/restore_preview.html", {
        "report": report, "preview_token": str(preview.token), "token": str(preview.token),
        "restore_token": str(preview.token), "source_snapshot": snapshot,
        "confirmation_phrase": recovery.CONFIRMATION_PHRASE, "expires_at": preview.expires_at,
    })


class ValidateBackupView(RecoveryAdminView):
    def post(self, request):
        upload = request.FILES.get("backup_file")
        try:
            if upload is None:
                raise recovery.RecoveryError("No backup file was uploaded.")
            payload = recovery.read_upload(upload)
        except Exception as exc:
            recovery.audit(request.user, "validate", "failed", error=str(exc)[:1000])
            return render(request, "accounts/restore_preview.html", {"validation_error": str(exc)}, status=400)
        try:
            return preview_response(request, payload)
        except Exception as exc:
            return render(request, "accounts/restore_preview.html", {"validation_error": "Backup validation failed: " + str(exc)}, status=400)


class SnapshotRestorePreviewView(RecoveryAdminView):
    def post(self, request, pk):
        snapshot = get_object_or_404(BackupSnapshot, pk=pk, status="ready")
        try:
            if recovery.checksum(snapshot.payload) != snapshot.checksum:
                recovery.audit(request.user, "validate", "failed", snapshot, error="Stored snapshot checksum mismatch")
                raise recovery.RecoveryError("Stored snapshot failed its integrity check.")
            return preview_response(request, snapshot.payload, snapshot)
        except Exception as exc:
            return back(request, error="Backup validation failed: " + str(exc))


class RestoreDataView(ValidateBackupView):
    def post(self, request):
        # Compatibility: an old upload form can only validate and display a preview.
        if request.FILES.get("backup_file") is not None:
            return super().post(request)
        if request.POST.get("confirm_restore") != "1" or request.POST.get("confirmation_phrase") != recovery.CONFIRMATION_PHRASE:
            recovery.audit(request.user, "restore", "failed", error="Required restore confirmation missing")
            return back(request, error="Tick the confirmation checkbox and type RESTORE exactly.")
        try:
            safety = recovery.restore_preview(request.user, request.POST.get("preview_token", ""))
        except recovery.RecoveryError as exc:
            return back(request, error=str(exc))
        except Exception:
            return back(request, error="Restore failed and hub changes were rolled back. The safety snapshot remains in history; see the audit event.")
        return back(request, success=f"Hub data restored and verified. Safety snapshot #{safety.pk} is retained.")


class DeletedRequestRecoveryView(RecoveryAdminView):
    def post(self, request):
        if request.POST.get("confirm_recover") != "1":
            recovery.audit(request.user, "recover", "failed", error="Required recovery confirmation missing")
            return back(request, error="Confirm recovery of the selected deleted requests.")
        try:
            count = recovery.recover_requests(request.user, request.POST.getlist("request_ids"))
        except Exception as exc:
            return back(request, error="Request recovery failed: " + str(exc))
        return back(request, success=f"Recovered {count} deleted request(s), including their retained history.")
