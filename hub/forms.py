import json
from collections.abc import Iterable, Iterator
from typing import Any, List

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Case, Q, QuerySet, Value, When
from django.forms import BaseModelFormSet
from django.forms.models import ModelChoiceIterator
from django.utils import timezone
from django.utils.functional import cached_property

from accounts.models import User
from .form_editor import is_baseline_build
from .models import Account, EngineerActivityLog, Request, SqrProjectPerformance, SqrSubmission, StatusLog


class AvatarSelect(forms.Select):
    """Select widget that stores avatar metadata on each option."""

    def __init__(self, *args: Any, avatar_roles: tuple[str, ...] | None = None, **kwargs: Any) -> None:
        """Accept optional metadata and restrict generated avatars to the original choice roles."""
        self.avatar_mapping = {}
        self.option_group_mapping = {}
        self.avatar_roles = avatar_roles
        super().__init__(*args, **kwargs)

    def create_option(
        self, name: str, value: Any, label: Any, selected: bool, index: int,
        subindex: int | None = None, attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Render avatar and role metadata from the same user used for the option label."""
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        option_value = option.get("value")
        if option_value:
            user = getattr(option_value, "instance", None)
            meta = self.avatar_mapping.get(str(option_value))
            if meta is None and isinstance(user, User) and (self.avatar_roles is None or user.role in self.avatar_roles):
                meta = _avatar_metadata(user)
            if meta:
                if meta.get("url"):
                    option["attrs"]["data-avatar"] = meta["url"]
                if meta.get("initial"):
                    option["attrs"]["data-initial"] = meta["initial"]
            group = self.option_group_mapping.get(str(option_value)) or getattr(user, "assignee_group", None)
            if group:
                option["attrs"]["data-role-group"] = group
        return option


def _avatar_metadata(user: User) -> dict[str, str]:
    """Preserve the initial and tolerate missing photos on user options."""
    display_name = (user.get_full_name() or user.username or "?").strip()
    initial = display_name[:1].upper() if display_name else "?"
    avatar_url = ""
    if user.profile_photo:
        try:
            avatar_url = user.profile_photo.url
        except ValueError:
            pass
    return {"url": avatar_url, "initial": initial}


def _build_avatar_mapping(users: Iterable[User]) -> dict[str, dict[str, str]]:
    """Build metadata for callers supplying explicit widget option values."""
    return {str(user.pk): _avatar_metadata(user) for user in users}


class _CachedModelChoiceIterator(ModelChoiceIterator):
    def __iter__(self) -> Iterator[tuple[Any, Any]]:
        """Reuse the form's evaluated queryset when a widget also needs its metadata."""
        if self.field.empty_label is not None:
            yield "", self.field.empty_label
        for instance in self.queryset:
            yield self.choice(instance)


class _MappedModelChoiceField(forms.ModelChoiceField):
    """Use the form's queryset for choices as well as the associated widget map."""

    iterator = _CachedModelChoiceIterator


def _user_display(user):
    full_name = user.get_full_name().strip() if user.get_full_name() else ""
    return full_name or user.username


def _assignee_scope(roles: Iterable[str], *extra_users: User | int | None) -> Q:
    """Include current owners even if they no longer hold an eligible role."""
    extra_ids = [getattr(user, "pk", user) for user in extra_users if user is not None]
    return Q(role__in=roles) | Q(pk__in=extra_ids)


def _engineer_queryset(*extra_users: User | int | None) -> QuerySet[User]:
    """Return assignable engineers in dropdown order."""
    return _assignee_queryset(_assignee_scope(User.ASSIGNABLE_ENGINEER_ROLES, *extra_users))


def _admin_engineer_queryset(*extra_users: User | int | None) -> QuerySet[User]:
    """Include ON_HOLD engineers so admins can assign them."""
    return _assignee_queryset(_assignee_scope(User.ENGINEER_ACCESS_ROLES, *extra_users))


def _assignee_queryset(scope: Q) -> QuerySet[User]:
    """Keep a model queryset in the established dropdown order."""
    return User.objects.filter(scope).order_by("first_name", "last_name")


def _used_account_name_suggestions() -> tuple[str, ...]:
    """Return nonblank, trimmed names in the existing suggestion order."""
    return tuple(name for raw in Account.used_queryset().values_list("name", flat=True) if (name := (raw or "").strip()))


def _engineer_access_queryset():
    return User.objects.filter(role__in=User.ENGINEER_ACCESS_ROLES).order_by("first_name", "last_name")


class RequestForm(forms.ModelForm):
    PROJECT_MANAGER_DISPLAY_NAMES = (
        "Jeram C. Zamora",
        "Marfelie B. Barcenas",
        "Princess Nicole D. Nacianceno",
    )

    account_name = forms.CharField(
        label="Account Name",
        help_text="Search existing accounts or create a new one.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "list": "account-name-options",
                "placeholder": "Search accounts…",
                "autocomplete": "off",
                "data-account-autocomplete": "true",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
            }
        ),
    )
    needed_by = forms.DateField(
        label="Request Date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    deployment_start = forms.DateField(
        label="Deployment Start",
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-deployment-start": "true",
            }
        ),
    )
    deployment_end = forms.DateField(
        label="Deployment End",
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-deployment-end": "true",
            }
        ),
    )
    priority = forms.ChoiceField(
        label="Priority",
        choices=Request.Priority.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-priority-select": "true"}),
    )
    engineer = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=AvatarSelect(attrs={"class": "form-select", "data-avatar-select": "true"}),
        label="Preferred Engineer",
        empty_label="Select preferred engineer",
    )
    backup_engineer = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=AvatarSelect(attrs={"class": "form-select", "data-avatar-select": "true"}),
        label="Backup Engineer",
        empty_label="Select backup engineer (optional)",
    )

    class Meta:
        model = Request
        fields = [
            "account_name",
            "needed_by",
            "product_category",
            "engagement_type",
            "priority",
            "description",
            "engineer",
            "backup_engineer",
        ]
        widgets = {
            "product_category": forms.Select(attrs={"class": "form-select"}),
            "engagement_type": forms.Select(attrs={"class": "form-select"}),
            "priority": forms.Select(attrs={"class": "form-select", "data-priority-select": "true"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def __init__(
        self, *args: Any, actor_role: str | None = None, actor_user: User | None = None,
        collaborative_edit: bool = False, **kwargs: Any,
    ) -> None:
        """Configure assignee and date options for the actor and the current request."""
        self.actor_role = actor_role
        self.actor_user = actor_user
        super().__init__(*args, **kwargs)
        # Allow admins and PM-ESG to bypass engineer capacity limits at the model-validation layer.
        # Django's ModelForm._post_clean() calls instance.full_clean() during form.is_valid(),
        # so the flag must be set here — before that validation runs.
        if actor_role in {User.Roles.ADMIN, User.Roles.PM_ESG}:
            self.instance._allow_capacity_override = True
        include_backup = actor_role in User.ENGINEER_ACCESS_ROLES
        if not include_backup:
            self.fields.pop("backup_engineer", None)
        desired_order = [
            "account_name",
            "needed_by",
            "product_category",
            "engagement_type",
            "deployment_start",
            "deployment_end",
            "priority",
            "description",
            "engineer",
        ]
        if include_backup:
            desired_order.append("backup_engineer")
        self.order_fields(desired_order)
        current_owners = (self.instance.engineer_id, self.instance.backup_engineer_id)
        roles = (
            User.ENGINEER_ACCESS_ROLES if actor_role in {User.Roles.ADMIN, User.Roles.PM_ESG}
            else User.ASSIGNABLE_ENGINEER_ROLES
        )
        self._project_manager_scope = Q(role=User.Roles.PM_ESG, is_active=True)
        if actor_user and actor_user.pk:
            self._project_manager_scope &= ~Q(pk=actor_user.pk)

        requestor_roles = {User.Roles.REQUESTOR, User.Roles.REQUESTOR_ESS, User.Roles.PM_ESS, User.Roles.PM_ESG, User.Roles.ADMIN}
        is_requestor_form = actor_role in requestor_roles
        engineer_scope = _assignee_scope(roles, *current_owners)
        if is_requestor_form:
            assignee_qs = _assignee_queryset(engineer_scope | self._project_manager_scope).annotate(
                assignee_group=Case(When(self._project_manager_scope, then=Value("project_manager")), default=Value("engineer")),
            )
        else:
            assignee_qs = _engineer_queryset(*current_owners)

        engineer_field = self.fields["engineer"]
        engineer_field.queryset = assignee_qs
        engineer_field.label_from_instance = _user_display
        if actor_role in User.ENGINEER_ACCESS_ROLES:
            engineer_field.label = "Turn Over Request"
            engineer_field.empty_label = "Keep current assignment"
        else:
            engineer_field.label = "Preferred Engineer"
            engineer_field.empty_label = "Select preferred engineer"

        engagement_value = ""
        if self.is_bound:
            engagement_value = (
                self.data.get(self.add_prefix("engagement_type"))
                or self.data.get("engagement_type")
                or ""
            )
        elif self.instance.pk and self.instance.engagement_type:
            engagement_value = self.instance.engagement_type

        if is_requestor_form:
            engineer_widget_attrs = engineer_field.widget.attrs
            engineer_widget_attrs["data-engineer-label"] = "Preferred Engineer"
            engineer_widget_attrs["data-project-manager-label"] = "Preferred Project Manager"
            engineer_widget_attrs["data-engineer-empty-label"] = "Select preferred engineer"
            engineer_widget_attrs["data-project-manager-empty-label"] = "Select preferred project manager"
            engineer_widget_attrs["data-project-management-value"] = Request.Engagement.PROJECT_MANAGEMENT
            if engagement_value == Request.Engagement.PROJECT_MANAGEMENT:
                engineer_field.label = "Preferred Project Manager"
                engineer_field.empty_label = "Select preferred project manager"
        if include_backup:
            backup_field = self.fields["backup_engineer"]
            backup_field.queryset = assignee_qs
            backup_field.label_from_instance = _user_display
        priority_field = self.fields["priority"]
        if not self.is_bound:
            if self.instance.pk and self.instance.priority:
                priority_field.initial = self.instance.priority
            else:
                priority_field.initial = Request.Priority.MEDIUM
        priority_widget = priority_field.widget
        if self.is_bound:
            default_priority = getattr(self.instance, "priority", None) or Request.Priority.MEDIUM
        else:
            default_priority = priority_field.initial or Request.Priority.MEDIUM
        priority_widget.attrs["data-default-priority"] = default_priority
        if self.instance.pk:
            self.fields["account_name"].initial = self.instance.account.name
        due_field = self.fields["needed_by"]
        today = timezone.now().date()
        if self.instance.pk and self.instance.start_date:
            due_field.initial = self.instance.start_date

        else:
            due_field.initial = today
        due_field.widget.attrs.pop("min", None)

        engagement_field = self.fields["engagement_type"]
        # PM-ESS and REQUESTOR_ESS cannot see Support
        if actor_role in {User.Roles.REQUESTOR_ESS, User.Roles.PM_ESS}:
            filtered_choices = [
                choice
                for choice in engagement_field.choices
                if choice[0] != Request.Engagement.SUPPORT
            ]
            if self.instance.pk and self.instance.engagement_type == Request.Engagement.SUPPORT:
                filtered_choices.append((Request.Engagement.SUPPORT, Request.Engagement.SUPPORT.label))
            engagement_field.choices = filtered_choices
        # Non-PM roles (Requestor, Engineers, PM-ESS) cannot see Certification — only PM-ESS and PM-ESG can
        _PM_ROLES = {User.Roles.PM_ESS, User.Roles.PM_ESG, User.Roles.ADMIN}
        if actor_role not in _PM_ROLES:
            filtered_choices = [
                choice
                for choice in engagement_field.choices
                if choice[0] != Request.Engagement.CERTIFICATION
            ]
            if self.instance.pk and self.instance.engagement_type == Request.Engagement.CERTIFICATION:
                filtered_choices.append((Request.Engagement.CERTIFICATION, Request.Engagement.CERTIFICATION.label))
            engagement_field.choices = filtered_choices

        # Human-readable blank options instead of Django's default "---------".
        product_field = self.fields["product_category"]
        product_field.choices = self._choices_with_placeholder(
            product_field.choices,
            "Select product category",
        )
        engagement_field.choices = self._choices_with_placeholder(
            engagement_field.choices,
            "Select engagement type",
        )
        product_field.widget.attrs.setdefault("aria-label", "Product category")
        engagement_field.widget.attrs.setdefault("aria-label", "Engagement type")
        engineer_field.widget.attrs.setdefault("aria-label", engineer_field.label)

        _DEPLOYMENT_LIKE = {Request.Engagement.DEPLOYMENT, Request.Engagement.CERTIFICATION}
        deployment_start_field = self.fields["deployment_start"]
        deployment_end_field = self.fields["deployment_end"]
        if self.instance.pk and self.instance.engagement_type in _DEPLOYMENT_LIKE:
            deployment_start_field.initial = self.instance.start_date
            deployment_end_field.initial = self.instance.due_date or self.instance.start_date
        elif not self.is_bound:
            deployment_start_field.initial = today
            deployment_end_field.initial = today

        self._protect_request_details = (
            collaborative_edit and self.instance.pk is not None and actor_role in User.ENGINEER_ACCESS_ROLES
        )
        if self._protect_request_details:
            self.initial.update(account_name=self.instance.account.name, needed_by=self.instance.start_date)
            self.editor_read_only_fields = frozenset({"account_name", "needed_by"})
            for name in self.editor_read_only_fields:
                self.fields[name].disabled = True

    @cached_property
    def account_name_suggestions(self) -> tuple[str, ...]:
        """Read account suggestions only when the form is presented."""
        return _used_account_name_suggestions()

    @cached_property
    def project_manager_ids(self) -> set[int]:
        """Return the approved IDs when validating a project-management assignment."""
        return set(User.objects.filter(self._project_manager_scope).values_list("pk", flat=True))

    def full_clean(self):
        super().full_clean()
        for name in self._errors:
            if name in self.fields:
                widget = self.fields[name].widget
                existing_classes = widget.attrs.get("class", "")
                if "is-invalid" not in existing_classes.split():
                    widget.attrs["class"] = (existing_classes + " is-invalid").strip()

    @staticmethod
    def _choices_with_placeholder(choices, placeholder: str):
        """Replace Django's default blank label (---------) with a clear prompt."""
        cleaned = []
        for value, label in list(choices or ()):
            if value in (None, ""):
                continue
            cleaned.append((value, label))
        return [("", placeholder), *cleaned]

    def clean_account_name(self):
        value = self.cleaned_data["account_name"].strip()
        if not value:
            raise forms.ValidationError("Account name is required.")
        return value

    def clean_needed_by(self):
        request_date = self.cleaned_data.get("needed_by")
        if not request_date:
            return request_date
        today = timezone.now().date()
        return request_date

    def clean(self):
        cleaned_data = super().clean()
        engagement = cleaned_data.get("engagement_type")
        priority = cleaned_data.get("priority")
        if engagement == Request.Engagement.SUPPORT:
            if not priority:
                self.add_error("priority", "Select the priority for support requests.")
        else:
            existing_priority = self.instance.priority if getattr(self.instance, "priority", None) else Request.Priority.MEDIUM
            cleaned_data["priority"] = existing_priority or Request.Priority.MEDIUM

        deployment_start = cleaned_data.get("deployment_start")
        deployment_end = cleaned_data.get("deployment_end")
        if engagement == Request.Engagement.DEPLOYMENT:
            if not deployment_start:
                self.add_error("deployment_start", "Select the deployment start date.")
            if not deployment_end:
                self.add_error("deployment_end", "Select the deployment end date.")
            if deployment_start and deployment_end and deployment_end < deployment_start:
                self.add_error("deployment_end", "Deployment end date cannot be earlier than the start date.")
            if deployment_start:
                cleaned_data["needed_by"] = deployment_start
        else:
            cleaned_data["deployment_start"] = None
            cleaned_data["deployment_end"] = None
        return cleaned_data

    def clean_engineer(self):
        engineer = self.cleaned_data.get("engineer")
        if not engineer:
            return engineer

        engagement = self.cleaned_data.get("engagement_type") or getattr(self.instance, "engagement_type", None)
        if engagement == Request.Engagement.PROJECT_MANAGEMENT and engineer.pk not in self.project_manager_ids:
            raise forms.ValidationError("Select a preferred project manager from the approved list.")

        # Admins and PM-ESG can override engineer capacity limits
        if self.actor_role in {User.Roles.ADMIN, User.Roles.PM_ESG}:
            return engineer

        ongoing_requests = Request.objects.filter(
            engineer=engineer,
            status=Request.Status.ONGOING,
        )

        if self.instance.pk:
            ongoing_requests = ongoing_requests.exclude(pk=self.instance.pk)

        deployment_start = self.cleaned_data.get("deployment_start")
        deployment_end = self.cleaned_data.get("deployment_end")
        max_allowed = 5
        if engagement == Request.Engagement.DEPLOYMENT and deployment_start and deployment_end:
            base_filter = Q(start_date__lte=deployment_end)
            overlap_filter = base_filter & (Q(due_date__gte=deployment_start) | Q(due_date__isnull=True))
            overlapping_deployments = ongoing_requests.filter(
                engagement_type=Request.Engagement.DEPLOYMENT
            ).filter(overlap_filter)
            if overlapping_deployments.exists():
                max_allowed = 3

        if ongoing_requests.count() >= max_allowed:
            if max_allowed == 3:
                raise forms.ValidationError(
                    "This engineer already has three overlapping deployment assignments for the selected window. Choose another engineer or adjust the deployment dates.",
                )
            raise forms.ValidationError(
                "This engineer already has five ongoing requests. Please select another engineer.",
            )

        return engineer

    def save(self, commit=True):
        if not self._protect_request_details:
            account_name = self.cleaned_data["account_name"]
            account, _ = Account.objects.get_or_create(name=account_name)
            self.instance.account = account
        self.instance.engineer = self.cleaned_data.get("engineer")
        priority_value = self.cleaned_data.get("priority") or Request.Priority.MEDIUM
        self.instance.priority = priority_value
        request_date = self.cleaned_data.get("needed_by")
        if request_date:
            self.instance.start_date = request_date
        deployment_start = self.cleaned_data.get("deployment_start")
        deployment_end = self.cleaned_data.get("deployment_end")
        if deployment_start:
            self.instance.start_date = deployment_start
        if deployment_end:
            self.instance.due_date = deployment_end
        elif self.cleaned_data.get("engagement_type") != Request.Engagement.DEPLOYMENT:
            self.instance.due_date = None
        if "backup_engineer" in self.cleaned_data:
            self.instance.backup_engineer = self.cleaned_data.get("backup_engineer")
        return super().save(commit=commit)


class RequestAdminForm(forms.ModelForm):
    account_name = forms.CharField(
        label="Account",
        required=True,
        help_text="Search existing accounts or create a new one.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "list": "account-name-options",
                "placeholder": "Search accounts…",
                "autocomplete": "off",
                "data-account-autocomplete": "true",
                "data-admin-account-field": "true",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
            }
        ),
    )
    request_date = forms.DateField(
        label="Request Date",
        required=True,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        help_text="Date selected by the requestor; SLA and due date will align to this date.",
    )
    requestor = forms.ModelChoiceField(
        queryset=User.objects.filter(role__in=User.REQUEST_CREATOR_ROLES),
        required=True,
        widget=AvatarSelect(attrs={"class": "form-select", "data-avatar-select": "true"}),
        label="Requestor",
        help_text="Switch the request owner (Requestor/Requestor-ESS/PM-ESS/PM-ESG/Admin).",
    )
    engineer = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=AvatarSelect(attrs={"class": "form-select", "data-avatar-select": "true"}),
        label="Assigned Person",
    )
    backup_engineer = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=AvatarSelect(attrs={"class": "form-select", "data-avatar-select": "true"}),
        label="Backup",
        empty_label="Select backup engineer (optional)",
    )

    class Meta:
        model = Request
        fields = [
            "account_name",
            "request_date",
            "requestor",
            "product_category",
            "priority",
            "status",
            "engineer",
            "backup_engineer",
            "due_date",
            "end_date",
            "description",
        ]
        widgets = {
            "product_category": forms.Select(attrs={"class": "form-select", "aria-label": "Product category"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Set up management fields without fetching choices before they are rendered."""
        allow_capacity_override = kwargs.pop("allow_capacity_override", False)
        super().__init__(*args, **kwargs)
        # Flag used by Request.clean() to bypass engineer capacity validation when admin overrides.
        self.instance._allow_capacity_override = allow_capacity_override
        self.order_fields(
            [
                "account_name",
                "request_date",
                "requestor",
                "product_category",
                "priority",
                "status",
                "engineer",
                "backup_engineer",
                "due_date",
                "end_date",
                "description",
            ]
        )
        # Request date initial
        if self.instance and getattr(self.instance, "start_date", None):
            self.fields["request_date"].initial = self.instance.start_date

        self._current_account_name = (self.instance.account.name or "").strip() if self.instance.account_id else ""
        if self._current_account_name:
            self.fields["account_name"].initial = self._current_account_name

        requestor_field = self.fields["requestor"]
        requestor_field.queryset = requestor_field.queryset.order_by("first_name", "last_name")
        requestor_field.label_from_instance = _user_display
        for name in ("engineer", "backup_engineer"):
            field = self.fields[name]
            field.queryset = _admin_engineer_queryset(getattr(self.instance, f"{name}_id"))
            field.label_from_instance = _user_display

        due_field = self.fields["due_date"]
        due_field.required = False
        due_field.help_text = "Leave blank to keep the SLA-based due date."

        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    widget = field.widget
                    existing_classes = widget.attrs.get("class", "")
                    class_list = existing_classes.split()
                    if "is-invalid" not in class_list:
                        widget.attrs["class"] = (existing_classes + " is-invalid").strip()

    @cached_property
    def account_name_suggestions(self) -> tuple[str, ...]:
        """Retain the current account even when it is not a regular suggestion."""
        suggestions = _used_account_name_suggestions()
        if self._current_account_name and self._current_account_name not in suggestions:
            return (self._current_account_name, *suggestions)
        return suggestions

    def clean_account_name(self):
        value = (self.cleaned_data.get("account_name") or "").strip()
        if not value:
            raise forms.ValidationError("Account name is required.")
        return value

    def save(self, commit=True):
        account_name = self.cleaned_data.get("account_name")
        if account_name:
            account, _ = Account.objects.get_or_create(name=account_name)
            self.instance.account = account
        self.instance.start_date = self.cleaned_data.get("request_date") or self.instance.start_date
        return super().save(commit=commit)


class SqrSubmissionForm(forms.ModelForm):
    GROUP_CHOICES = (
        ("", "— Select Department —"),
        ("ESS", "ESS"),
        ("HP", "HP"),
        ("Dell", "Dell"),
        ("ENS", "ENS"),
        ("ESG", "ESG"),
        ("ISG", "ISG"),
        ("Other", "Other"),
    )

    MEMBERS_BY_GROUP = {
        "ESS": [
            "Aileen B. Gutierrez",
            "Leonarda G. Lucena",
            "Kristel Camill V. Roldan",
            "Mica Ella R. Labindao",
            "Anabelle D. Alapide",
            "Jimlyn Espinosa",
            "Aira E. Coloma",
        ],
        "HP": [
            "Ann Irma Tablada",
            "May V. Andres-Duro",
            "Aileen S. Felarca",
            "Genalyn T. Bonto",
            "Brian A. Delos Santos",
            "Beverly Edang - Villamor",
            "Mindy Anne S. Dapon",
            "Kevin Pangalangan",
            "Jacqueline Olesco - Tatel",
            "Lorenz Gabriel M. Dasma\u00f1as",
            "Maria Hilda C. Mallillin",
        ],
        "Dell": [
            "Roberto D. Quiambao Jr.",
            "Rafael D. Raposas",
            "Dinalyn D. Llanera",
            "Ednalyn N. Malang",
            "Queen Deniece Vergara",
            "Jennylyn S. Billones",
            "Ace U. Carlos",
            "Andrea Marie S. Garcia",
            "Ruffy P. Umayam",
            "Lendy Gladys Fabula \u2013 Ogana",
            "Ronstadt Joyce R. Corpuz",
            "Mark Ni\u00f1o B. Huberit",
            "Jelson G. Cabero",
            "Bjay T. Jacinto",
            "Ma. Victoria H. Ronquillo",
        ],
        "ENS": [
            "Debbie S. Eusebio",
            "Christian F. Lamando",
            "Jhoanna Marie Quijano",
        ],
        "ESG": [],
        "ISG": [],
        "Other": [],
    }

    APPROVER_BY_GROUP = {
        "ESS": "Marfelie Barcenas",
        "HP": "Princess Nicole Nacianceno",
        "Dell": "Jeram Zamora",
        "ENS": "Jeram Zamora",
        "ESG": "Jeram Zamora",
        "ISG": "Jeram Zamora",
        "Other": "Jeram Zamora",
    }

    SSE_MANHRS_SCOPES = frozenset([
        "Deployment Only",
        "On-Call Services",
        "Deployment and Project Management",
        "Training",
        # Legacy values kept for existing rows.
        "Support",
        "Implementation",
        "Implementation and Project Management",
        "Demonstration",
        "Other",
    ])
    HIDE_SSE_MANHRS_SCOPES = frozenset([
        "Project Management",
        "Maintenance",
        # Legacy values kept for existing rows.
        "Managed Support and Maintenance Service",
        "Managed Support and Service",
    ])

    SCOPE_CHOICES = (
        ("", "— Select Scope —"),
        ("Deployment Only", "Deployment Only"),
        ("On-Call Services", "On-Call Services"),
        ("Maintenance", "Maintenance"),
        ("Project Management", "Project Management"),
        ("Deployment and Project Management", "Deployment and Project Management"),
        ("Training", "Training"),
    )

    customer_company = forms.ChoiceField(
        choices=GROUP_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Department",
    )

    linked_request = _MappedModelChoiceField(
        queryset=Request.objects.select_related("account").only("id", "reference_code", "account__name").order_by("-id"),
        required=True,
        empty_label="— Select Request ID —",
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Request ID",
    )

    customer_contact = forms.ChoiceField(
        choices=[("", "— Select Member —")],
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Account Manager",
        required=False,
    )

    project_details = forms.ChoiceField(
        choices=SCOPE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Scope of Services",
    )

    pm_esg_reviewer = _MappedModelChoiceField(
        queryset=User.objects.filter(role=User.Roles.PM_ESG).order_by("first_name", "last_name"),
        required=True,
        widget=AvatarSelect(avatar_roles=(User.Roles.PM_ESG,), attrs={"class": "form-select", "data-avatar-select": "true"}),
        label="Approver Name",
    )

    class Meta:
        model = SqrSubmission
        fields = [
            "linked_request",
            "customer_company",
            "customer_contact",
            "pm_esg_reviewer",
            "customer_name",
            "project_title",
            "project_details",
            "sse_manhrs",
            "sqr_folder_link",
            "remarks",
        ]
        labels = {
            "customer_name": "Account Name",
            "customer_company": "Department",
            "customer_contact": "Account Manager",
            "project_title": "Service Description",
            "project_details": "Scope of Services",
            "sse_manhrs": "SSE Manhrs",
            "sqr_folder_link": "SQR Folder Link",
        }
        widgets = {
            "customer_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Select or type account name",
                "list": "sqr-account-name-options",
                "autocomplete": "off",
            }),
            "project_title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter project / engagement name"}),
            "sse_manhrs": forms.NumberInput(attrs={
                "class": "form-control",
                "min": "0",
                "step": "1",
                "placeholder": "0",
                "data-sse-manhrs": "true",
            }),
            "sqr_folder_link": forms.URLInput(attrs={
                "class": "form-control",
                "placeholder": "https://example.com/sqr-folder",
            }),
            "remarks": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Option 1 – Primary proposal, or Option 2 – Alternative proposal",
            }),
        }

    def __init__(self, *args: Any, linked_requests: QuerySet[Request] | None = None, **kwargs: Any) -> None:
        """Set field metadata using the caller's filing or edit scope from the start."""
        super().__init__(*args, **kwargs)
        if linked_requests is not None:
            self.fields["linked_request"].queryset = linked_requests
        required_fields = ("linked_request", "pm_esg_reviewer", "sqr_folder_link")
        for field_name in required_fields:
            self.fields[field_name].required = True
            self.fields[field_name].widget.attrs["required"] = "required"
            self.fields[field_name].widget.attrs["aria-required"] = "true"
        self.fields["linked_request"].help_text = "You can submit multiple separate SQRs for the same assigned Request ID."
        self.fields["linked_request"].error_messages["required"] = "Request ID is required."
        self.fields["pm_esg_reviewer"].error_messages["required"] = "Approver Name is required."
        self.fields["sqr_folder_link"].error_messages["required"] = "SQR Folder Link is required."
        # Include account name in the Request ID dropdown label to help identify requests
        self.fields["linked_request"].label_from_instance = (
            lambda obj: (
                f"{obj.reference_code} \u2013 {obj.account.name}"
                if obj.reference_code and getattr(obj, 'account', None) and obj.account.name
                else obj.reference_code or f"Request #{obj.pk}"
            )
        )
        self.fields["pm_esg_reviewer"].label_from_instance = _user_display
        self.fields["project_title"].help_text = "Project Name / Engagement Name"
        self.fields["remarks"].help_text = "Optional: identify the proposed option or add filing context. Hold reasons are entered separately by PM/Admin."

        department_attrs = self.fields["customer_company"].widget.attrs
        department_attrs["data-member-map"] = json.dumps(self.MEMBERS_BY_GROUP)
        if not is_baseline_build():
            department_attrs["data-approver-map"] = self._approver_map()
            _refresh_sqr_request_account_map(self)
        department_attrs["data-sse-scopes"] = json.dumps(sorted(self.SSE_MANHRS_SCOPES))

        # Populate member choices: all members + preserve any historical free-text value
        all_contacts = [("", "— Select Member —")]
        seen = set()
        for members in self.MEMBERS_BY_GROUP.values():
            for m in members:
                if m not in seen:
                    all_contacts.append((m, m))
                    seen.add(m)
        current_contact = (getattr(self.instance, "customer_contact", "") or "") if self.instance else ""
        if current_contact and current_contact not in seen:
            all_contacts.append((current_contact, current_contact))
        self.fields["customer_contact"].choices = all_contacts

        # Preserve editability of historical values created before dropdown enforcement
        for field_name in ("customer_company", "project_details"):
            current_value = getattr(self.instance, field_name, "") if self.instance else ""
            if current_value and current_value not in dict(self.fields[field_name].choices):
                self.fields[field_name].choices = tuple(self.fields[field_name].choices) + ((current_value, current_value),)

    @cached_property
    def account_name_options(self) -> list[str]:
        """Fetch filing suggestions only for the template that displays them."""
        return list(Account.used_queryset().values_list("name", flat=True).distinct())

    def _approver_map(self) -> str:
        """Keep the original group defaults, independent of a published selector restriction."""
        reviewers = [(user.pk, user.get_full_name().lower()) for user in self.fields["pm_esg_reviewer"].queryset]
        approvers = {}
        for group, name in self.APPROVER_BY_GROUP.items():
            name_parts = name.lower().split()
            for user_id, full_name in reviewers:
                if all(part in full_name for part in name_parts):
                    approvers[group] = str(user_id)
                    break
        return json.dumps(approvers)

    def full_clean(self):
        read_only = getattr(self, "editor_read_only_fields", ())
        if not read_only:
            return super().full_clean()
        # Display the saved quote without revalidating its historical values.
        fields = self.fields
        self.fields = {name: field for name, field in fields.items() if name not in read_only}
        try:
            return super().full_clean()
        finally:
            self.fields = fields

    def clean_linked_request(self):
        value = self.cleaned_data.get("linked_request")
        if not value:
            raise forms.ValidationError("Request ID is required.")
        return value

    def clean_pm_esg_reviewer(self):
        value = self.cleaned_data.get("pm_esg_reviewer")
        if not value:
            raise forms.ValidationError("Approver Name is required.")
        return value

    def clean_sqr_folder_link(self):
        value = (self.cleaned_data.get("sqr_folder_link") or "").strip()
        if not value:
            raise forms.ValidationError("SQR Folder Link is required.")
        return value

    @staticmethod
    def _normalize_scope(scope):
        return " ".join((scope or "").split()).casefold()

    def clean(self):
        cleaned_data = super().clean()
        if "sse_manhrs" in getattr(self, "editor_read_only_fields", ()):
            return cleaned_data
        scope = cleaned_data.get("project_details") or ""
        normalized_scope = self._normalize_scope(scope)
        sse_scopes = {self._normalize_scope(item) for item in self.SSE_MANHRS_SCOPES}
        hidden_sse_scopes = {self._normalize_scope(item) for item in self.HIDE_SSE_MANHRS_SCOPES}
        if normalized_scope in hidden_sse_scopes or normalized_scope not in sse_scopes:
            cleaned_data["sse_manhrs"] = None
        return cleaned_data


def _refresh_sqr_request_account_map(form: SqrSubmissionForm) -> None:
    """Rebuild account metadata from the same scoped queryset used for choices."""
    field = form.fields["linked_request"]
    field.widget.attrs["data-account-map"] = json.dumps({
        str(req.pk): req.account.name for req in field.queryset if req.account_id and req.account.name
    })


class SqrImportForm(forms.Form):
    import_file = forms.FileField(
        label="Import File",
        help_text="Upload an .xlsx file based on the current SQR Export Excel template.",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ),
    )

    def clean_import_file(self):
        uploaded_file = self.cleaned_data.get("import_file")
        if not uploaded_file:
            raise ValidationError("Please choose an Excel (.xlsx) file to import.")
        if not str(uploaded_file.name or "").lower().endswith(".xlsx"):
            raise ValidationError("Only Excel (.xlsx) files are supported.")
        return uploaded_file


class SqrMaintenanceFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_maintenance_notice()

    def apply_maintenance_notice(self):
        notice = self.instance.maintenance_notice
        for name in ("managed_support_start_date", "managed_support_end_date"):
            field = self.fields.get(name)
            if field is not None and notice and notice not in str(field.help_text):
                field.help_text = f"{field.help_text} {notice}".strip()

    def clean(self):
        from .services.sqr_maintenance import validate_manual_maintenance_dates

        cleaned = super().clean()
        date_fields = ("managed_support_start_date", "managed_support_end_date")
        for field in date_fields:
            if self.add_prefix(field) not in self.data:
                cleaned[field] = getattr(self.instance, field)
        if any(field in self.errors for field in date_fields):
            return cleaned
        previous = tuple(getattr(self.instance, field) for field in date_fields)
        proposed = tuple(cleaned.get(field) for field in date_fields)
        if proposed != previous:
            try:
                validate_manual_maintenance_dates(*proposed)
            except ValidationError as error:
                self.add_error(None, error)
        return cleaned


class SqrTrackerEditForm(SqrMaintenanceFormMixin, forms.ModelForm):
    """Edit form for PM / Admin — columns N, Q, T, W, X, AA–AP."""

    pm_manhrs = forms.IntegerField(
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "min": "0",
            "step": "1",
            "list": "pm-manhrs-datalist",
            "placeholder": "e.g. 8, 16, 24, 32, 48",
        }),
        label="PM Man-hrs (N)",
    )

    discount_rate = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=100,
        initial=0,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "min": "0",
            "max": "100",
            "step": "1",
            "list": "discount-rate-datalist",
            "placeholder": "0",
        }),
        label="Discount Rate % (Q)",
    )

    class Meta:
        model = SqrSubmission
        fields = [
            "pm_manhrs",
            "discount_rate",
            "status",
            "proposal_status",
            "po_pnl_date",
            "delivery_start_date",
            "overall_status",
            "schedule_performance",
            "budget_effort_performance",
            "quality_performance",
            "delivery_progress",
            "key_updates_risks",
            "delivery_actual_finish_date",
            "managed_support_start_date",
            "managed_support_end_date",
            "revenue_source",
            "revenue_reference_no",
            "revenue_remarks",
            "revenue_declaration",
        ]
        labels = {
            "status": "SQR Status (T)",
            "proposal_status": "Proposal Status (W)",
            "po_pnl_date": "PO / PNL Date (X)",
            "delivery_start_date": "Start Date (AA)",
            "overall_status": "Overall Status (AB)",
            "schedule_performance": "Schedule Performance",
            "budget_effort_performance": "Budget/Effort Performance",
            "quality_performance": "Quality Performance",
            "delivery_progress": "Overall Progress % (AD)",
            "key_updates_risks": "Key Updates / Risks / Issues (AE)",
            "delivery_actual_finish_date": "Finish Date",
            "managed_support_start_date": "Maintenance Start Date",
            "managed_support_end_date": "Maintenance End Date",
            "revenue_source": "Source (AM)",
            "revenue_reference_no": "Billing Reference (AN)",
            "revenue_remarks": "Remarks (AP)",
            "revenue_declaration": "Revenue Declaration (AQ)",
        }
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
            "proposal_status": forms.Select(attrs={"class": "form-select"}),
            "po_pnl_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "delivery_start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "overall_status": forms.Select(attrs={"class": "form-select"}),
            "schedule_performance": forms.Select(attrs={"class": "form-select"}),
            "budget_effort_performance": forms.Select(attrs={"class": "form-select"}),
            "quality_performance": forms.Select(attrs={"class": "form-select"}),
            "delivery_progress": forms.NumberInput(attrs={
                "class": "form-control", "min": "0", "max": "100", "step": "1", "placeholder": "0–100",
            }),
            "key_updates_risks": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Updates, risks, issues…"}),
            "delivery_actual_finish_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "managed_support_start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "managed_support_end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "revenue_source": forms.TextInput(attrs={"class": "form-control", "placeholder": "Revenue source"}),
            "revenue_reference_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "Reference number"}),
            "revenue_remarks": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Revenue remarks…"}),
            "revenue_declaration": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add empty leading option to optional choice fields
        for fname in ("proposal_status", "overall_status", "schedule_performance", "budget_effort_performance", "quality_performance", "revenue_declaration"):
            if fname in self.fields:
                current = list(self.fields[fname].choices)
                if not current or current[0][0] != "":
                    self.fields[fname].choices = [("", "— Select —")] + current
        # Set default discount_rate to 0 when displaying unbound form
        if not self.is_bound:
            self.initial.setdefault("discount_rate", 0)


class SqrReviewForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.reviewer_role = kwargs.pop("reviewer_role", "")
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            ("", "— Select Status —"),
            (SqrSubmission.Status.FOR_REVISION, SqrSubmission.Status.FOR_REVISION.label),
            (SqrSubmission.Status.APPROVED, SqrSubmission.Status.APPROVED.label),
        ]

        # Always start blank — user must actively choose a status
        if not self.is_bound:
            self.initial["status"] = ""

        selected_status = ""
        if self.is_bound:
            selected_status = (self.data.get("status") or "").strip()
        else:
            selected_status = (getattr(self.instance, "status", "") or "").strip()

        if selected_status == SqrSubmission.Status.FOR_REVISION:
            self.fields["review_notes"].label = "Revision Comments"
            self.fields["review_notes"].widget.attrs["placeholder"] = "Enter revision comments"

    def clean(self):
        cleaned_data = super().clean()
        selected_status = cleaned_data.get("status")
        comments = (cleaned_data.get("review_notes") or "").strip()
        if selected_status == SqrSubmission.Status.FOR_REVISION and self.reviewer_role == User.Roles.PM_ESG and not comments:
            self.add_error("review_notes", "Revision Comments is required when status is For Revision.")
        return cleaned_data

    class Meta:
        model = SqrSubmission
        fields = ["status", "review_notes"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
            "review_notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 5,
                    "placeholder": "Review findings, follow-ups, and approval notes",
                }
            ),
        }


class SqrRevenueQuotationForm(forms.ModelForm):
    class Meta:
        model = SqrSubmission
        fields = ["quotation_total_price", "discount_rate"]
        widgets = {
            "quotation_total_price": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "min": "0",
                    "step": "0.01",
                    "placeholder": "0.00",
                }
            ),
            "discount_rate": forms.Select(attrs={"class": "form-select form-select-sm"}),
        }

    def clean_quotation_total_price(self):
        value = self.cleaned_data.get("quotation_total_price")
        if value is None:
            raise forms.ValidationError("Enter the quotation amount.")
        if value <= 0:
            raise forms.ValidationError("Quotation amount must be greater than zero.")
        return value


class SqrRevenueOrderForm(forms.ModelForm):
    class Meta:
        model = SqrSubmission
        fields = ["po_attachment_link", "revenue_overview"]
        widgets = {
            "po_attachment_link": forms.URLInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "placeholder": "https://...",
                }
            ),
            "revenue_overview": forms.Textarea(
                attrs={
                    "class": "form-control form-control-sm",
                    "rows": 2,
                    "placeholder": "Overview details of the revenue",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["po_attachment_link"].required = True

    def clean_po_attachment_link(self):
        value = (self.cleaned_data.get("po_attachment_link") or "").strip()
        if not value:
            raise forms.ValidationError("Attach the purchase order link to move this to Revenue stage.")
        return value


class SqrProposalStatusForm(forms.ModelForm):
    """PM fills this to set pricing breakdown, manhours, and deal/proposal status."""

    # Override so any integer 0-100 is accepted (not restricted to model choices)
    discount_rate = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=100,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": "0", "max": "100", "step": "1", "placeholder": "0"}
        ),
    )

    class Meta:
        model = SqrSubmission
        fields = [
            "sse_manhrs",
            "sse_amount",
            "pm_manhrs",
            "pm_amount",
            "managed_support_amount",
            "discount_rate",
            "quotation_total_price",
            "validity_due_date",
            "proposal_status",
        ]
        labels = {
            "sse_manhrs": "SSE Manhours",
            "sse_amount": "SSE Amount (PHP)",
            "pm_manhrs": "PM Manhours",
            "pm_amount": "PM Amount (PHP)",
            "managed_support_amount": "Maintenance Amt. (PHP)",
            "discount_rate": "Discount Rate",
            "quotation_total_price": "Total Price (PHP)",
            "validity_due_date": "Validity Due Date",
            "proposal_status": "Proposal Status",
        }
        widgets = {
            "sse_manhrs": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1", "placeholder": "0"}
            ),
            "sse_amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1", "placeholder": "0"}
            ),
            "pm_manhrs": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1", "placeholder": "0"}
            ),
            "pm_amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1", "placeholder": "0"}
            ),
            "managed_support_amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1", "placeholder": "0"}
            ),
            "quotation_total_price": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1", "placeholder": "0"}
            ),
            "validity_due_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "proposal_status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.required = False
        self.fields["proposal_status"].choices = [("", "\u2014 Not set \u2014")] + list(
            SqrSubmission.ProposalStatus.choices
        )


class SqrDeliveryForm(SqrMaintenanceFormMixin, forms.ModelForm):
    """PM fills this to track service delivery once a deal is Closed Won."""

    class Meta:
        model = SqrSubmission
        fields = [
            "po_pnl_date",
            "assigned_pm",
            "assigned_sse",
            "delivery_start_date",
            "overall_status",
            "schedule_performance",
            "budget_effort_performance",
            "quality_performance",
            "delivery_progress",
            "key_updates_risks",
            "delivery_actual_finish_date",
            "managed_support_start_date",
            "managed_support_end_date",
        ]
        labels = {
            "po_pnl_date": "PO / PNL Date",
            "assigned_pm": "Assigned PM",
            "assigned_sse": "Assigned SSE",
            "delivery_start_date": "Start Date (Execution)",
            "overall_status": "Overall Status",
            "schedule_performance": "Schedule Performance",
            "budget_effort_performance": "Budget/Effort Performance",
            "quality_performance": "Quality Performance",
            "delivery_progress": "Overall Progress (%)",
            "key_updates_risks": "Key Updates / Risks / Issues",
            "delivery_actual_finish_date": "Finish Date",
            "managed_support_start_date": "Maintenance Start Date",
            "managed_support_end_date": "Maintenance End Date",
        }
        widgets = {
            "po_pnl_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "assigned_pm": forms.Select(attrs={"class": "form-select"}),
            "assigned_sse": forms.Select(attrs={"class": "form-select"}),
            "delivery_start_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "overall_status": forms.Select(attrs={"class": "form-select"}),
            "schedule_performance": forms.Select(attrs={"class": "form-select"}),
            "budget_effort_performance": forms.Select(attrs={"class": "form-select"}),
            "quality_performance": forms.Select(attrs={"class": "form-select"}),
            "delivery_progress": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "max": "100", "placeholder": "0\u2013100"}
            ),
            "key_updates_risks": forms.Textarea(attrs={"class": "form-control", "rows": "3"}),
            "delivery_actual_finish_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "managed_support_start_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "managed_support_end_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["overall_status"].choices = [("", "\u2014 Not set \u2014")] + list(
            SqrSubmission.OverallStatus.choices
        )
        for field, choices in (
            ("schedule_performance", SqrSubmission.SchedulePerformance.choices),
            ("budget_effort_performance", SqrSubmission.BudgetEffortPerformance.choices),
            ("quality_performance", SqrSubmission.QualityPerformance.choices),
        ):
            self.fields[field].choices = [("", "Not assessed")] + list(choices)
        self.fields["assigned_pm"].queryset = User.objects.filter(role="pm_esg").order_by("first_name", "last_name")
        self.fields["assigned_sse"].queryset = User.objects.filter(role__in=["engineer", "on_hold"]).order_by("first_name", "last_name")
        for f in self.fields.values():
            f.required = False

    def clean_delivery_progress(self):
        value = self.cleaned_data.get("delivery_progress")
        if value is not None and not (0 <= value <= 100):
            raise forms.ValidationError("Progress must be between 0 and 100.")
        return value


class SqrPerformanceForm(forms.ModelForm):
    """PM fills this to track Project Performance (Status + Health) per SQR."""

    class Meta:
        model = SqrProjectPerformance
        fields = [
            "project_manager",
            "start_date",
            "finish_date",
            "status",
            "percent_complete",
            "next_action_remarks",
            "folder_link",
            "schedule_health",
            "scope_health",
            "budget_health",
            "recovery_action_remarks",
        ]
        labels = {
            "project_manager": "Project Manager",
            "start_date": "Start Date",
            "finish_date": "Finish Date",
            "status": "Status",
            "percent_complete": "% Complete",
            "next_action_remarks": "Next Action / Remarks",
            "folder_link": "Folder link",
            "schedule_health": "Schedule Health",
            "scope_health": "Scope Health",
            "budget_health": "Budget Health",
            "recovery_action_remarks": "Recovery Action / Remarks",
        }
        widgets = {
            "project_manager": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "finish_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "percent_complete": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "max": "100", "placeholder": "0-100"}
            ),
            "next_action_remarks": forms.Textarea(attrs={"class": "form-control", "rows": "3"}),
            "folder_link": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://"}),
            "schedule_health": forms.Select(attrs={"class": "form-select"}),
            "scope_health": forms.Select(attrs={"class": "form-select"}),
            "budget_health": forms.Select(attrs={"class": "form-select"}),
            "recovery_action_remarks": forms.Textarea(attrs={"class": "form-control", "rows": "3"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project_manager"].queryset = User.objects.filter(
            role__in=["pm_esg", "admin"]
        ).order_by("first_name", "last_name")
        for f in self.fields.values():
            f.required = False

    def clean_percent_complete(self):
        value = self.cleaned_data.get("percent_complete")
        if value is not None and not (0 <= value <= 100):
            raise forms.ValidationError("Progress must be between 0 and 100.")
        return value


class SqrRevenueForm(forms.ModelForm):
    """PM fills this to record revenue recognition details."""

    class Meta:
        model = SqrSubmission
        fields = [
            "revenue_remarks",
            "revenue_source",
            "revenue_date",
            "revenue_reference_no",
            "revenue_status",
            "revenue_overview",
        ]
        labels = {
            "revenue_remarks": "PO Remarks",
            "revenue_source": "Billing Type",
            "revenue_date": "Billed Date",
            "revenue_reference_no": "Billing Reference",
            "revenue_status": "Billing Status",
            "revenue_overview": "Billing Remarks",
        }
        widgets = {
            "revenue_remarks": forms.Textarea(attrs={"class": "form-control", "rows": "3"}),
            "revenue_source": forms.Select(attrs={"class": "form-select"}),
            "revenue_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "revenue_reference_no": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "SI / PNL / CFDM number"}
            ),
            "revenue_status": forms.Select(attrs={"class": "form-select"}),
            "revenue_overview": forms.Textarea(attrs={"class": "form-control", "rows": "3"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revenue_source"].choices = [("", "\u2014 Not set \u2014")] + [
            ("internal", "Internal"),
            ("invoiced", "Invoiced"),
            ("unbilled", "Unbilled"),
        ]
        self.fields["revenue_status"].choices = [("", "\u2014 Not set \u2014")] + list(
            SqrSubmission.RevenueStatus.choices
        )
        for f in self.fields.values():
            f.required = False


class RequestStatusForm(forms.ModelForm):
    class Meta:
        model = Request
        fields = ["status", "end_date"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
        }

    def __init__(self, *args, actor_user=None, **kwargs):
        self.actor_user = actor_user
        super().__init__(*args, **kwargs)
        self.instance._allow_capacity_override = True

    def _is_blocked_completion_without_activity(self) -> bool:
        if not self.actor_user or self.actor_user.role not in User.ENGINEER_ACCESS_ROLES:
            return False
        if self.instance.status == Request.Status.COMPLETED:
            return False
        if self.actor_user.pk not in {self.instance.engineer_id, self.instance.backup_engineer_id}:
            return False
        return not self.instance.activity_logs.exists()

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        end_date = cleaned_data.get("end_date")
        if status == Request.Status.ONGOING:
            # Reset end date before model validation so toggling back to ongoing passes clean()
            self.instance.end_date = None
            cleaned_data["end_date"] = None
        elif status == Request.Status.COMPLETED:
            if not end_date:
                self.add_error("end_date", "Select the completion date before closing the ticket.")
            else:
                self.instance.end_date = end_date
                if self._is_blocked_completion_without_activity():
                    self.add_error(
                        "status",
                        "Add at least one related activity log before marking this request as completed.",
                    )
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.status == Request.Status.ONGOING:
            instance.end_date = None
        else:
            instance.end_date = self.cleaned_data.get("end_date")
        if commit:
            instance.save()
        return instance


class StatusLogForm(forms.ModelForm):
    def __init__(self, *args, request_obj=None, actor_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_obj = request_obj or (self.instance.request if self.instance.pk else None)
        self.actor_user = actor_user or (self.instance.author if self.instance.pk else None)

    @cached_property
    def mention_people(self):
        from .services.update_mentions import mention_people_data

        return mention_people_data(self.request_obj, exclude_user=self.actor_user)

    class Meta:
        model = StatusLog
        fields = ["message"]
        labels = {"message": "Add status update"}
        widgets = {
            "message": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Add an update"}),
        }

    def clean_message(self):
        message = self.cleaned_data.get("message", "").strip()
        if not message:
            raise forms.ValidationError("Message cannot be empty.")
        from .services.update_mentions import mention_handles, request_people

        handles = mention_handles(message)
        if handles:
            if self.actor_user and self.actor_user.username.casefold() in handles:
                raise forms.ValidationError("You cannot mention yourself. Choose another related person.")
            allowed = {user.username.casefold() for user in request_people(self.request_obj)}
            if not handles.issubset(allowed):
                raise forms.ValidationError("Mention only active people listed for this request. Remove or replace unavailable @mentions.")
        return message


class EngineerActivityLogForm(forms.ModelForm):
    activity_type = forms.ChoiceField(
        label="Type of Activity",
        choices=EngineerActivityLog.ActivityType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    is_billable = forms.TypedChoiceField(
        label="Billing Type",
        choices=(
            ("false", "Not Billable"),
            ("true", "Billable"),
        ),
        initial="false",
        coerce=lambda value: str(value).lower() in {"true", "1", "yes"},
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = EngineerActivityLog
        fields = [
            "request_date",
            "request",
            "account",
            "activity_type",
            "actual_hours",
            "details",
            "location",
            "is_billable",
            "status",
        ]
        labels = {
            "request_date": "Date of Activity",
            "request": "Related Request",
            "account": "Account",
            "actual_hours": "Actual Hours",
            "details": "Details",
            "location": "Work Location",
            "status": "Status",
        }
        widgets = {
            "request_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "request": forms.Select(attrs={"class": "form-select"}),
            "account": forms.Select(attrs={"class": "form-select"}),
            "actual_hours": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.25"}),
            "details": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "location": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, engineer=None, bound_request=None, **kwargs):
        self.engineer = engineer
        self.bound_request = bound_request
        if self.engineer is None:
            raise ValueError("EngineerActivityLogForm requires an engineer instance.")
        super().__init__(*args, **kwargs)

        account_field = self.fields["account"]
        account_field.required = False
        account_qs = Account.used_queryset()
        current_account = getattr(self.instance, "account", None)
        if current_account and current_account.pk:
            account_qs = (account_qs | Account.objects.filter(pk=current_account.pk)).distinct().order_by("name")
        account_field.queryset = account_qs

        request_field = self.fields["request"]
        request_field.required = False
        if self.bound_request is not None:
            related_requests = Request.objects.filter(pk=self.bound_request.pk)
            request_field.required = True
            if not self.is_bound and not self.instance.pk:
                request_field.initial = self.bound_request
        elif self.engineer.pk is None:
            # Form-editor baselines use an unsaved engineer, with no related requests.
            related_requests = Request.objects.none()
        elif self.engineer.role == User.Roles.ADMIN:
            related_requests = Request.objects.all()
        else:
            related_requests = Request.objects.filter(
                Q(engineer=self.engineer) | Q(backup_engineer=self.engineer)
            )
        related_requests = related_requests.order_by("-created_at").select_related("account")
        request_field.queryset = related_requests
        request_field.empty_label = "Select request (optional)"
        self.fields["is_billable"].initial = "false"

        request_date_field = self.fields["request_date"]
        request_date_field.required = True
        if not self.is_bound and not self.instance.pk:
            request_date_field.initial = timezone.now().date()

        if not self.is_bound:
            if self.instance.pk:
                self.fields["is_billable"].initial = "true" if self.instance.is_billable else "false"
                self.fields["activity_type"].initial = self.instance.activity_type
            else:
                self.fields["is_billable"].initial = "false"
                self.fields["activity_type"].initial = EngineerActivityLog.ActivityType.INTERNAL_SUPPORT
                self.fields["status"].initial = EngineerActivityLog.Status.COMPLETED
        else:
            raw_billable = self.data.get(self.add_prefix("is_billable"))
            if raw_billable is None:
                self.fields["is_billable"].initial = "false"

        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    widget = field.widget
                    css = widget.attrs.get("class", "")
                    if "is-invalid" not in css.split():
                        widget.attrs["class"] = (css + " is-invalid").strip()

    def clean_details(self):
        value = (self.cleaned_data.get("details") or "").strip()
        if not value:
            raise forms.ValidationError("Provide the activity details.")
        return value

    def clean_actual_hours(self):
        value = self.cleaned_data.get("actual_hours")
        if value is None:
            return value
        if value <= 0:
            raise forms.ValidationError("Actual hours must be greater than zero.")
        return value

    def clean_request(self):
        request_obj = self.cleaned_data.get("request")
        if not request_obj:
            return request_obj
        allowed_ids = set(self.fields["request"].queryset.values_list("id", flat=True))
        if request_obj.pk not in allowed_ids:
            raise forms.ValidationError("Select a request assigned to you.")
        return request_obj

    def clean(self):
        cleaned_data = super().clean()
        request_obj = cleaned_data.get("request")
        account = cleaned_data.get("account")
        if request_obj:
            if account and account != request_obj.account:
                self.add_error("account", "Account does not match the selected request.")
            else:
                cleaned_data["account"] = request_obj.account
        if not cleaned_data.get("account"):
            self.add_error("account", "Select an account or choose a request.")
        return cleaned_data


class AdminRequestFilterForm(forms.Form):
    reference_code = forms.CharField(
        label="ID",
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": "Search ID"}
        ),
    )
    account = forms.ModelChoiceField(
        label="Account",
        required=False,
        queryset=Account.objects.none(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    account_manager = forms.ModelChoiceField(
        label="Requestor",
        required=False,
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    engineer = forms.ModelChoiceField(
        label="Assigned Engineer",
        required=False,
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    priority = forms.ChoiceField(
        label="Priority",
        required=False,
        choices=[("", "All priorities"), *Request.Priority.choices],
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    status = forms.ChoiceField(
        label="Status",
        required=False,
        choices=[("", "All statuses"), *Request.Status.choices],
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    created_from = forms.DateField(
        label="Created From",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )
    created_to = forms.DateField(
        label="Created To",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )
    end_from = forms.DateField(
        label="End Date From",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )
    end_to = forms.DateField(
        label="End Date To",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )
    days_min = forms.IntegerField(
        label="Days Min",
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "placeholder": "≥"}),
    )
    days_max = forms.IntegerField(
        label="Days Max",
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "placeholder": "≤"}),
    )
    due_from = forms.DateField(
        label="Due Date From",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )
    due_to = forms.DateField(
        label="Due Date To",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.used_queryset()
        self.fields["account"].empty_label = "All accounts"
        requestor_qs = User.objects.filter(role=User.Roles.REQUESTOR).order_by("first_name", "last_name")
        self.fields["account_manager"].queryset = requestor_qs
        self.fields["account_manager"].empty_label = "All requestors"
        self.fields["account_manager"].label_from_instance = _user_display
        engineer_qs = _engineer_access_queryset()
        self.fields["engineer"].queryset = engineer_qs
        self.fields["engineer"].empty_label = "All engineers"
        self.fields["engineer"].label_from_instance = _user_display

    def filter_queryset(self, queryset):
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data.get("reference_code"):
            queryset = queryset.filter(reference_code__icontains=data["reference_code"].strip())
        if data.get("account"):
            queryset = queryset.filter(account=data["account"])
        if data.get("account_manager"):
            queryset = queryset.filter(requestor=data["account_manager"])
        if data.get("engineer"):
            queryset = queryset.filter(engineer=data["engineer"])
        if data.get("priority"):
            queryset = queryset.filter(priority=data["priority"])
        if data.get("status"):
            queryset = queryset.filter(status=data["status"])
        if data.get("created_from"):
            queryset = queryset.filter(created_at__date__gte=data["created_from"])
        if data.get("created_to"):
            queryset = queryset.filter(created_at__date__lte=data["created_to"])
        if data.get("end_from"):
            queryset = queryset.filter(end_date__gte=data["end_from"])
        if data.get("end_to"):
            queryset = queryset.filter(end_date__lte=data["end_to"])
        if data.get("due_from"):
            queryset = queryset.filter(due_date__gte=data["due_from"])
        if data.get("due_to"):
            queryset = queryset.filter(due_date__lte=data["due_to"])
        return queryset

    def filter_sequence(self, requests: Iterable[Request]) -> List[Request]:
        if not self.is_valid():
            return list(requests)
        data = self.cleaned_data
        results = list(requests)
        days_min = data.get("days_min")
        if days_min is not None:
            results = [req for req in results if req.days_since_creation >= days_min]
        days_max = data.get("days_max")
        if days_max is not None:
            results = [req for req in results if req.days_since_creation <= days_max]
        return results

    def has_active_filters(self) -> bool:
        if not self.is_valid():
            return False
        for value in self.cleaned_data.values():
            if value not in {None, ""}:
                return True
        return False


class AccountManagementForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Account name"}),
        }
        labels = {
            "name": "Account Name",
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Account name cannot be blank.")
        return name


class AccountManagementFormSet(BaseModelFormSet):
    def __init__(self, *args, definition=None, answers=None, **kwargs):
        self.editor_definition = definition
        self.editor_answers = answers or {}
        super().__init__(*args, **kwargs)

    def add_prefix(self, index):
        if self.prefix == "account-page" and not self.is_bound and isinstance(index, int) and index < self.initial_form_count():
            return f"{self.prefix}-{self.get_queryset()[index].pk}"
        return super().add_prefix(index)

    def submission_overhead(self):
        return len(self.management_form.fields) + 2  # CSRF and active_tab.

    @staticmethod
    def submission_field_count(form):
        return sum(
            len(field.choices) if isinstance(field, forms.MultipleChoiceField) else 1
            for field in form.fields.values() if not field.disabled
        )

    def add_fields(self, form, index):
        from .form_editor_runtime import configure_form

        super().add_fields(form, index)
        if self.editor_definition is not None:
            configure_form(
                form, form_key="account_management", definition=self.editor_definition,
                answers=self.editor_answers.get(form.instance.pk, {}),
            )
        is_new = self.data.get(form.add_prefix("id")) in (None, "") and not form.instance.pk
        form.fields["name"].widget.attrs["aria-label"] = (
            f'{form.fields["name"].label} for {form.instance.name}' if form.instance.pk
            else "New account name" if is_new else form.fields["name"].label
        )
        if "DELETE" in form.fields:
            form.fields["DELETE"].widget.attrs["class"] = "form-check-input"

    def clean(self):
        initial_count = self.initial_form_count()
        if not 0 <= initial_count <= self.total_form_count():
            raise ValidationError("The account edit batch is invalid. Discard the edits and reload.")
        identities = set()
        invalid_rows = False
        for index, form in enumerate(self.forms):
            if index < initial_count:
                pk = form.instance.pk
                if not pk:
                    form.add_error(None, "This account is no longer available. Discard the edits and reload.")
                    invalid_rows = True
                elif pk in identities:
                    form.add_error(None, "An account can only be submitted once per batch.")
                    invalid_rows = True
                identities.add(pk)
            elif self.data.get(form.add_prefix("id")):
                form.add_error(None, "An existing account cannot be submitted as a new account.")
                invalid_rows = True
        if invalid_rows:
            # A checked DELETE would otherwise suppress these row errors.
            raise ValidationError("The account edit batch contains invalid rows.")
        super().clean()


from .form_editor_runtime import editable_form

for _editable_class in (
    RequestForm, RequestAdminForm, SqrSubmissionForm, SqrImportForm,
    SqrTrackerEditForm, SqrReviewForm, SqrRevenueQuotationForm,
    SqrRevenueOrderForm, SqrProposalStatusForm, SqrDeliveryForm,
    SqrPerformanceForm, SqrRevenueForm, RequestStatusForm, StatusLogForm,
    EngineerActivityLogForm, AdminRequestFilterForm, AccountManagementForm,
):
    editable_form(_editable_class)
del _editable_class
