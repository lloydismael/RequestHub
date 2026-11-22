from django.urls import path

from .recovery_views import (
    BackupDataView,
    DeletedRequestRecoveryView,
    RestoreDataView,
    SnapshotDownloadView,
    SnapshotRestorePreviewView,
    ValidateBackupView,
)
from .views import ProfileUpdateView, SignInHeartbeatView, StoredFileServeView

app_name = "accounts"

urlpatterns = [
    path("profile/", ProfileUpdateView.as_view(), name="update"),
    path("sign-in/heartbeat/", SignInHeartbeatView.as_view(), name="sign-in-heartbeat"),
    path("media/<path:name>", StoredFileServeView.as_view(), name="media"),
    path("backup/", BackupDataView.as_view(), name="backup"),
    path("restore/", RestoreDataView.as_view(), name="restore"),
    path("backup/create/", BackupDataView.as_view(), name="backup-create"),
    path("backup/<int:pk>/download/", SnapshotDownloadView.as_view(), name="backup-download"),
    path("backup/<int:pk>/preview/", SnapshotRestorePreviewView.as_view(), name="backup-preview"),
    path("backup/validate/", ValidateBackupView.as_view(), name="backup-validate"),
    path("recovery/requests/", DeletedRequestRecoveryView.as_view(), name="requests-recover"),
]
