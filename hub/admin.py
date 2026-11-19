from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Account, EngineerActivityLog, Notification, Request, SqrProjectPerformance, SqrSubmission, SqrSubmissionHistory, StatusLog


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    search_fields = ["name"]
    list_display = ("name", "created_at")


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = (
        "reference_code",
        "account",
        "account_manager",
        "priority",
        "status",
        "due_date",
    )
    list_filter = ("priority", "status", "product_category", "engagement_type")
    search_fields = ("reference_code", "account__name", "account_manager")
    autocomplete_fields = ("requestor", "account", "engineer")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "message", "created_at", "is_read")
    list_filter = ("is_read",)
    search_fields = ("message", "recipient__username")


@admin.register(StatusLog)
class StatusLogAdmin(admin.ModelAdmin):
    list_display = ("request", "author", "created_at")
    list_filter = ("created_at",)
    search_fields = ("request__reference_code", "author__username", "message")


@admin.register(EngineerActivityLog)
class EngineerActivityLogAdmin(admin.ModelAdmin):
    list_display = ("request_date", "engineer", "account", "activity_type", "actual_hours", "is_billable", "status")
    list_filter = ("status", "is_billable", "location", "request_date", "account")
    search_fields = ("activity_type", "details", "account__name", "engineer__username", "engineer__first_name", "engineer__last_name")
    autocomplete_fields = ("engineer", "account", "request")


@admin.register(SqrSubmission)
class SqrSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "reference_code",
        "engineer",
        "pm_esg_reviewer",
        "customer_name",
        "status",
        "quotation_total_price",
        "discount_rate",
        "created_at",
        "reviewed_at",
    )
    list_filter = ("status", "created_at", "reviewed_at")
    search_fields = ("reference_code", "customer_name", "project_title", "engineer__username", "pm_esg_reviewer__username")
    autocomplete_fields = ("engineer", "pm_esg_reviewer", "reviewed_by")

    retention_fields = ("status", "reviewed_at", "validity_due_date", "proposal_status", "revenue_status", "revenue_date")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, *self.retention_fields)

    def save_model(self, request, obj, form, change):
        if change and not request.user.is_superuser:
            current = SqrSubmission.objects.select_for_update().values(*self.retention_fields).get(pk=obj.pk)
            for field, value in current.items():
                setattr(obj, field, value)
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not request.user.is_superuser and SqrSubmission.objects.filter(
            SqrSubmission.protected_from_deletion(), pk=obj.pk,
        ).exists():
            return False
        return super().has_delete_permission(request, obj)

    @transaction.atomic
    def delete_model(self, request, obj):
        locked = SqrSubmission.objects.select_for_update().get(pk=obj.pk)
        if not self.has_delete_permission(request, locked):
            raise PermissionDenied
        super().delete_model(request, locked)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        ids = list(queryset.order_by("pk").select_for_update().values_list("pk", flat=True))
        locked = SqrSubmission.objects.filter(pk__in=ids)
        if not request.user.is_superuser and locked.filter(SqrSubmission.protected_from_deletion()).exists():
            raise PermissionDenied
        super().delete_queryset(request, locked)


@admin.register(SqrProjectPerformance)
class SqrProjectPerformanceAdmin(admin.ModelAdmin):
    list_display = ("submission", "project_manager", "status", "percent_complete", "updated_at")
    list_filter = ("status", "schedule_health", "scope_health", "budget_health")
    search_fields = ("submission__reference_code", "submission__project_title")
    autocomplete_fields = ("submission", "project_manager")


@admin.register(SqrSubmissionHistory)
class SqrSubmissionHistoryAdmin(admin.ModelAdmin):
    list_display = ("submission", "action", "field", "actor", "created_at", "restored_at")
    list_filter = ("action", "field", "source", "created_at")
    search_fields = ("submission__reference_code", "actor__username", "actor__first_name", "actor__last_name", "summary")
    autocomplete_fields = ("submission", "actor", "restored_by")
    readonly_fields = ("old_values", "new_values", "metadata", "created_at", "restored_at")

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)
