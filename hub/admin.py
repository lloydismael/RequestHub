from django.contrib import admin

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
