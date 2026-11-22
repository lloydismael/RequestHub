"""Request Hub view package and compatibility exports for existing callers."""

# ruff: noqa: I001
# Keep historical bindings for URL registration, imports, and existing patch points.
import base64 as base64
import csv as csv
import html as html
import json as json
import logging as logging
import mimetypes as mimetypes
import re as re
import threading as threading
import uuid as uuid
<<<<<<< HEAD
=======

import requests as requests
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
from accounts.forms import UserManagementForm as UserManagementForm
from accounts.models import SignInLog as SignInLog, User as User
from accounts.sign_in_log import (
    annotate_sign_in_logs as annotate_sign_in_logs,
    filter_sign_in_logs as filter_sign_in_logs,
    SIGN_IN_STATUS_CHOICES as SIGN_IN_STATUS_CHOICES,
    summarize_sign_ins as summarize_sign_ins,
)
from collections import Counter as Counter, defaultdict as defaultdict
from copy import copy as copy
from dataclasses import dataclass as dataclass
from datetime import date as date, datetime as datetime, timedelta as timedelta
from decimal import Decimal as Decimal, InvalidOperation as InvalidOperation
from django.conf import settings as settings
from django.contrib import messages as messages
from django.contrib.auth.mixins import LoginRequiredMixin as LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType as ContentType
from django.contrib.sessions.models import Session as Session
from django.core.cache import cache as cache
from django.core.exceptions import PermissionDenied as PermissionDenied, ValidationError as ValidationError
from django.core.paginator import InvalidPage as InvalidPage, Paginator as Paginator
from django.db import (
    close_old_connections as close_old_connections,
    DatabaseError as DatabaseError,
    IntegrityError as IntegrityError,
    transaction as transaction,
)
from django.db.models import Count as Count, F as F, Max as Max, Min as Min, Q as Q, Sum as Sum
from django.db.models.deletion import ProtectedError as ProtectedError
from django.db.models.functions import TruncMonth as TruncMonth
from django.forms import modelformset_factory as modelformset_factory
from django.http import (
    Http404 as Http404,
    HttpResponse as HttpResponse,
    HttpResponseRedirect as HttpResponseRedirect,
    JsonResponse as JsonResponse,
)
from django.middleware.csrf import get_token as get_token
from django.shortcuts import get_object_or_404 as get_object_or_404, redirect as redirect, render as render
from django.template.loader import render_to_string as render_to_string
from django.urls import reverse as reverse, reverse_lazy as reverse_lazy
from django.utils import timezone as timezone
from django.utils.cache import (
    patch_cache_control as patch_cache_control,
    patch_vary_headers as patch_vary_headers,
)
from django.utils.crypto import get_random_string as get_random_string
from django.utils.html import format_html as format_html, json_script as json_script
from django.utils.safestring import mark_safe as mark_safe
from django.views import View as View
from django.views.generic import (
    DeleteView as DeleteView,
    ListView as ListView,
    TemplateView as TemplateView,
    UpdateView as UpdateView,
)
from email.charset import Charset as Charset, QP as QP
from email.header import Header as Header
from email.mime.application import MIMEApplication as MIMEApplication
from email.mime.image import MIMEImage as MIMEImage
from email.mime.multipart import MIMEMultipart as MIMEMultipart
from email.mime.text import MIMEText as MIMEText
from functools import cache as memoize  # noqa: F401 - legacy export
from functools import partial as partial
from hashlib import sha256 as sha256
from hub.changelog_forms import ChangelogForm as ChangelogForm
from hub.constants import ACCOUNT_MANAGERS as ACCOUNT_MANAGERS
from hub.form_editor_runtime import (
    configure_form as configure_form,
    save_configured_instance as save_configured_instance,
)
from hub.forms import (
    AccountManagementForm as AccountManagementForm,
    AccountManagementFormSet as AccountManagementFormSet,
    AdminRequestFilterForm as AdminRequestFilterForm,
    EngineerActivityLogForm as EngineerActivityLogForm,
    RequestAdminForm as RequestAdminForm,
    RequestForm as RequestForm,
    RequestStatusForm as RequestStatusForm,
    SqrDeliveryForm as SqrDeliveryForm,
    SqrImportForm as SqrImportForm,
    SqrProposalStatusForm as SqrProposalStatusForm,
    SqrRevenueForm as SqrRevenueForm,
    SqrRevenueOrderForm as SqrRevenueOrderForm,
    SqrRevenueQuotationForm as SqrRevenueQuotationForm,
    SqrSubmissionForm as SqrSubmissionForm,
    StatusLogForm as StatusLogForm,
)
from hub.mixins import (
    AdminOrEngineerRequiredMixin as AdminOrEngineerRequiredMixin,
    AdminOrPmEsgRequiredMixin as AdminOrPmEsgRequiredMixin,
    AdminRequiredMixin as AdminRequiredMixin,
    EngineerRequiredMixin as EngineerRequiredMixin,
)
from hub.models import (
    Account as Account,
    Changelog as Changelog,
    EngineerActivityLog as EngineerActivityLog,
    FormAnswerSet as FormAnswerSet,
    Notification as Notification,
    Request as Request,
    RequestCommunication as RequestCommunication,
    SqrSubmission as SqrSubmission,
    SqrSubmissionChange as SqrSubmissionChange,
    SqrSubmissionHistory as SqrSubmissionHistory,
    StatusLog as StatusLog,
)
from hub.services import (
    outlook_threads as outlook_threads,
    request_lifecycle as request_lifecycle,
    sqr_prototype as sqr_prototype,
)
from hub.services.notifications import (
    queue_assignment_notifications as queue_assignment_notifications,
    queue_new_request_notifications as queue_new_request_notifications,
)
from hub.services.outlook_drafts import (
    access_token as access_token,
    outlook_is_configured as outlook_is_configured,
    OutlookError as OutlookError,
    OutlookSignInRequired as OutlookSignInRequired,
    require_configuration as require_configuration,
)
from hub.services.request_email import (
    AssignmentEmailResult as AssignmentEmailResult,
    notify_engineer_assignment_email as notify_engineer_assignment_email,
)
from hub.services.sqr_email import (
    _build_sqr_approval_body_lines as _build_sqr_approval_body_lines,
    _build_sqr_approval_draft_html as _build_sqr_approval_draft_html,
    _build_sqr_approval_html as _build_sqr_approval_html,
    _build_sqr_approval_html_markup as _build_sqr_approval_html_markup,
    _build_sqr_approval_mailto_url as _build_sqr_approval_mailto_url,
    _build_sqr_approval_mime_message as _build_sqr_approval_mime_message,
    _format_sqr_approval_currency as _format_sqr_approval_currency,
    _format_sqr_approval_date as _format_sqr_approval_date,
    _format_sqr_approval_greeting as _format_sqr_approval_greeting,
    _format_sqr_approval_subject as _format_sqr_approval_subject,
    _format_sqr_approval_total_price as _format_sqr_approval_total_price,
    _format_sqr_approval_validity_date as _format_sqr_approval_validity_date,
    _get_sqr_approval_email_context as _get_sqr_approval_email_context,
    _get_sqr_approval_to_emails as _get_sqr_approval_to_emails,
    _get_sqr_logo_attachment as _get_sqr_logo_attachment,
    _get_sqr_service_component as _get_sqr_service_component,
    _normalize_sqr_person_name as _normalize_sqr_person_name,
    _resolve_sqr_account_manager_email as _resolve_sqr_account_manager_email,
    _send_sqr_approved_email as _send_sqr_approved_email,
    _send_sqr_for_revision_email as _send_sqr_for_revision_email,
    _send_sqr_new_submission_email as _send_sqr_new_submission_email,
    _sqr_approval_document_links as _sqr_approval_document_links,
    _unique_sqr_approval_emails as _unique_sqr_approval_emails,
    SQR_ACCOUNT_MANAGER_EMAILS as SQR_ACCOUNT_MANAGER_EMAILS,
    SQR_APPROVAL_HUB_EMAIL as SQR_APPROVAL_HUB_EMAIL,
    SQR_APPROVAL_LOGO_CID as SQR_APPROVAL_LOGO_CID,
    SQR_APPROVAL_LOGO_PATH as SQR_APPROVAL_LOGO_PATH,
    SQR_APPROVAL_LOGO_URL as SQR_APPROVAL_LOGO_URL,
    SQR_APPROVAL_MAILTO_BODY_URL_LIMIT as SQR_APPROVAL_MAILTO_BODY_URL_LIMIT,
    SQR_APPROVAL_SERVICE_COMPONENT as SQR_APPROVAL_SERVICE_COMPONENT,
)
from hub.services.sqr_performance import build_sqr_performance_snapshot as build_sqr_performance_snapshot
from hub.services.sqr_tracker_settings import (
    get_tracker_settings as get_tracker_settings,
    resolve_sqr_view as resolve_sqr_view,
    set_classic_visibility as set_classic_visibility,
)
from hub.services.sqr_workflow import (
    SQR_FILING_QUOTE_FIELDS as SQR_FILING_QUOTE_FIELDS,
    SQR_APPROVED_QUOTE_FIELDS as SQR_APPROVED_QUOTE_FIELDS,
    SQR_REVIEW_FIELDS as SQR_REVIEW_FIELDS,
    SQR_REOPEN_MESSAGE as SQR_REOPEN_MESSAGE,
    _can_review_sqr as _can_review_sqr,
    _sqr_quote_locked as _sqr_quote_locked,
    _sqr_status_choices as _sqr_status_choices,
    SQR_HISTORY_FIELD_LABELS as SQR_HISTORY_FIELD_LABELS,
    SQR_HISTORY_CHOICE_LABELS as SQR_HISTORY_CHOICE_LABELS,
    get_sqr_history_field_label as get_sqr_history_field_label,
    format_sqr_history_value as format_sqr_history_value,
    build_sqr_history_summary as build_sqr_history_summary,
    record_sqr_history as record_sqr_history,
)
<<<<<<< HEAD
from hub.views import requests as requests
=======
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
from hub.views.admin_tools import (
    _can_manage_user as _can_manage_user,
    UserEditView as UserEditView,
    UserManagementView as UserManagementView,
)
from hub.views.common import (
    _csv_safe_cell as _csv_safe_cell,
    _safe_return_url as _safe_return_url,
    ADMIN_PANEL_ROLES as ADMIN_PANEL_ROLES,
    ASSIGNABLE_ENGINEER_ROLES as ASSIGNABLE_ENGINEER_ROLES,
    create_change_status_log as create_change_status_log,
    ENGINEER_ACCESS_ROLES as ENGINEER_ACCESS_ROLES,
    logger as logger,
    MANILA_TZ as MANILA_TZ,
    PM_ESG_ROLE as PM_ESG_ROLE,
    PM_ESS_ROLE as PM_ESS_ROLE,
    REQUEST_CREATOR_ROLES as REQUEST_CREATOR_ROLES,
    REQUESTOR_ROLES as REQUESTOR_ROLES,
    SQR_ACCESS_ROLES as SQR_ACCESS_ROLES,
    summarize_request_changes as summarize_request_changes,
)
from hub.views.dashboard import DashboardLiveDataView as DashboardLiveDataView, DashboardView as DashboardView
from hub.views.notifications import (
    flash_assignment_email_feedback as flash_assignment_email_feedback,
    NotificationDeleteView as NotificationDeleteView,
    NotificationFollowRedirectView as NotificationFollowRedirectView,
    NotificationListView as NotificationListView,
    NotificationMarkAllReadView as NotificationMarkAllReadView,
    NotificationReadView as NotificationReadView,
    NotificationToastDataView as NotificationToastDataView,
    notify_account_manager_request_update as notify_account_manager_request_update,
    notify_engineer_assignment_notification as notify_engineer_assignment_notification,
    notify_status_update as notify_status_update,
)
from hub.views.reports import (
    EngineerActivityLogDeleteView as EngineerActivityLogDeleteView,
    EngineerActivityLogView as EngineerActivityLogView,
    ReportExportView as ReportExportView,
    RequestReportView as RequestReportView,
)
<<<<<<< HEAD
from hub.views.requests import (
=======
from hub.views.request_views import (
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    _microsoft_request_draft as _microsoft_request_draft,
    _microsoft_sign_in_redirect as _microsoft_sign_in_redirect,
    _request_connect_url as _request_connect_url,
    clear_engineer_outlook_lock_on_reassignment as clear_engineer_outlook_lock_on_reassignment,
    get_request_activity_log_context as get_request_activity_log_context,
    normalize_request_form_changed_fields as normalize_request_form_changed_fields,
    RequestAdminUpdateView as RequestAdminUpdateView,
    RequestClosingOutlookRedirectView as RequestClosingOutlookRedirectView,
    RequestCollaborativeManageView as RequestCollaborativeManageView,
    RequestDeleteView as RequestDeleteView,
    RequestExportCSVView as RequestExportCSVView,
    RequestNudgeView as RequestNudgeView,
    RequestOutlookRedirectView as RequestOutlookRedirectView,
    RequestRestoreView as RequestRestoreView,
    RequestStatusUpdateView as RequestStatusUpdateView,
    RequestTeamsRedirectView as RequestTeamsRedirectView,
    RequestUpdateView as RequestUpdateView,
    StatusLogUpdateView as StatusLogUpdateView,
)
from hub.views.sla import RequestLifecycleAcknowledgeView as RequestLifecycleAcknowledgeView
from hub.views.sqr import (
    _build_choice_import_map as _build_choice_import_map,
    _get_sqr_esg_export_columns as _get_sqr_esg_export_columns,
    _get_sqr_export_columns as _get_sqr_export_columns,
    _get_sqr_performance_export_columns as _get_sqr_performance_export_columns,
    _make_sqr_edit_form as _make_sqr_edit_form,
    _normalize_import_text as _normalize_import_text,
    _perf_sqr_mismatches as _perf_sqr_mismatches,
    _refresh_sqr_request_account_map as _refresh_sqr_request_account_map,
    _sqr_queryset_for_user as _sqr_queryset_for_user,
    SqrApprovalEmlDownloadView as SqrApprovalEmlDownloadView,
    SqrApprovalFormattedDraftView as SqrApprovalFormattedDraftView,
    SqrApprovalOutlookRedirectView as SqrApprovalOutlookRedirectView,
    SqrDeliveryUpdateView as SqrDeliveryUpdateView,
    SqrEngineerDeleteView as SqrEngineerDeleteView,
    SqrEngineerUpdateView as SqrEngineerUpdateView,
    SqrExportView as SqrExportView,
    SqrHistoryRestoreView as SqrHistoryRestoreView,
    SqrHistoryView as SqrHistoryView,
    SqrImportView as SqrImportView,
    SqrInlineFieldUndoView as SqrInlineFieldUndoView,
    SqrInlineFieldUpdateView as SqrInlineFieldUpdateView,
    SqrListView as SqrListView,
    SqrPerformanceDataView as SqrPerformanceDataView,
    SqrPerformanceUpdateView as SqrPerformanceUpdateView,
    SqrProposalUpdateView as SqrProposalUpdateView,
    SqrPrototypeDataView as SqrPrototypeDataView,
    SqrReportsDataView as SqrReportsDataView,
    SqrRevenueTrackerUpdateView as SqrRevenueTrackerUpdateView,
    SqrRevenueUpdateView as SqrRevenueUpdateView,
    SqrReviewUpdateView as SqrReviewUpdateView,
    SqrTeamsRedirectView as SqrTeamsRedirectView,
    SqrToRevenueView as SqrToRevenueView,
    SqrTrackerSettingsView as SqrTrackerSettingsView,
)
from pathlib import Path as Path
from typing import Optional as Optional
from urllib.parse import quote as quote, urlencode as urlencode
