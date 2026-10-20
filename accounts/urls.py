from django.urls import path

from .views import ProfileUpdateView, StoredFileServeView
from .recovery_views import (
    BackupDataView, RestoreDataView, ValidateBackupView, SnapshotDownloadView,
    SnapshotRestorePreviewView, DeletedRequestRecoveryView,
)

app_name = "accounts"

urlpatterns = [
    path("profile/", ProfileUpdateView.as_view(), name="update"),
    path("media/<path:name>", StoredFileServeView.as_view(), name="media"),
    path("backup/", BackupDataView.as_view(), name="backup"),
    path("restore/", RestoreDataView.as_view(), name="restore"),
    path("backup/create/", BackupDataView.as_view(), name="backup-create"),
    path("backup/<int:pk>/download/", SnapshotDownloadView.as_view(), name="backup-download"),
    path("backup/<int:pk>/preview/", SnapshotRestorePreviewView.as_view(), name="backup-preview"),
    path("backup/validate/", ValidateBackupView.as_view(), name="backup-validate"),
    path("recovery/requests/", DeletedRequestRecoveryView.as_view(), name="requests-recover"),
]
