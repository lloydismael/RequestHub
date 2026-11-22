"""Administrative user, account, and changelog management."""

import re
from accounts.forms import UserManagementForm
from accounts.models import SignInLog, User
from accounts.sign_in_log import (
    annotate_sign_in_logs,
    filter_sign_in_logs,
    SIGN_IN_STATUS_CHOICES,
    summarize_sign_ins,
)
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.core.paginator import InvalidPage, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.db.models.deletion import ProtectedError
from django.forms import modelformset_factory
from django.http import HttpRequest, HttpResponse, QueryDict
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.crypto import get_random_string
from django.views import View
from hub import views as legacy_views
from hub.changelog_forms import ChangelogForm
from hub.form_editor_runtime import configure_form
from hub.forms import AccountManagementForm, AccountManagementFormSet
from hub.mixins import AdminRequiredMixin, can_manage_user
from hub.models import Account, Changelog, FormAnswerSet, FormDefinition
from typing import Any
from urllib.parse import urlencode


_can_manage_user = can_manage_user


class UserManagementView(AdminRequiredMixin, LoginRequiredMixin, View):
    template_name = "hub/management.html"
    formset_class = modelformset_factory(User, form=UserManagementForm, extra=0, can_delete=False)
    account_form_class = modelformset_factory(
        Account, form=AccountManagementForm, formset=AccountManagementFormSet,
        extra=1, can_delete=True, can_delete_extra=False,
    )

    def get_queryset(self):
        return User.objects.order_by("date_joined", "username")

    def _paginate_users(self, request):
        """Server-side search and pagination for the management users pane."""
        qs = self.get_queryset()
        query = (request.GET.get("user_q") or "").strip()
        if query:
            terms = [term for term in query.split() if term]
            for term in terms:
                lowered = term.lower()
                matching_roles = [value for value, label in User.Roles.choices if lowered in str(label).lower()]
                qs = qs.filter(
                    Q(first_name__icontains=term)
                    | Q(last_name__icontains=term)
                    | Q(username__icontains=term)
                    | Q(email__icontains=term)
                    | Q(department__icontains=term)
                    | Q(role__icontains=term)
                    | Q(role__in=matching_roles)
                )
        qs = qs.order_by("first_name", "last_name", "username")
        paginator = Paginator(qs, 50)
        page_num = request.GET.get("user_page") or 1
        try:
            page_obj = paginator.page(page_num)
        except InvalidPage:
            page_obj = paginator.page(1)
        return page_obj, query

    def get(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Render the selected management pane or its requested fragment."""
        active_tab = request.GET.get("tab", "users")
        if self.kwargs.get("changelog_editor"):
            active_tab = "changelogs"
        if active_tab not in {"users", "accounts", "activity", "forms", "changelogs", "monitoring"}:
            active_tab = "users"
        if active_tab == "users":
            users_context = self._build_users_context(request)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                response = legacy_views.render(None, f"{self.template_name}#user-results", {
                    **users_context, "request": request, "csrf_token": get_token(request),
                })
                patch_cache_control(response, private=True, no_store=True)
            else:
                response = self._render_users(request, users_context=users_context)
            patch_vary_headers(response, ("X-Requested-With",))
            return response

        if active_tab == "accounts":
            return self._render_accounts(request)

        context = self._tab_context(active_tab)
        if active_tab == "changelogs":
            entry = legacy_views.get_object_or_404(Changelog, pk=self.kwargs["pk"]) if self.kwargs.get("pk") else None
            context.update(
                changelog_form=getattr(self, "changelog_form", None) or ChangelogForm(
                    instance=entry, comparison_page=request.GET.get("comparison_page"),
                ),
                changelog_entry=entry,
                changelog_page=Paginator(Changelog.objects.all(), 10).get_page(request.GET.get("changelog_page")),
            )
        elif active_tab == "activity":
            context.update(self._build_activity_context())
        elif active_tab == "forms":
            from hub.form_editor_views import catalog

            context["form_catalog"] = catalog()
        return legacy_views.render(request, self.template_name, context)

    def _build_users_context(self, request: HttpRequest) -> dict[str, Any]:
        """Build the user directory page with editor forms only for manageable users."""
        from hub.form_editor import get_definition

        users_page_obj, user_search_query = self._paginate_users(request)
        users = list(users_page_obj)
        editor_definition = get_definition("user_management")
        editor_answers = {}
        if editor_definition.pk:
            editor_answers = dict(FormAnswerSet.objects.filter(
                definition=editor_definition,
                content_type=ContentType.objects.get_for_model(User),
                object_id__in=[user.pk for user in users],
            ).values_list("object_id", "answers"))
        for user in users:
            user.can_manage = _can_manage_user(request.user, user)
            if not user.can_manage:
                continue
            form = UserManagementForm(instance=user, _form_editor_skip=True)
            user.editor_form = configure_form(
                form, definition=editor_definition, answers=editor_answers.get(user.pk, {}),
            )
        return {"users": users, "users_page_obj": users_page_obj, "user_search_query": user_search_query}

    def _render_users(
        self, request: HttpRequest, *, users_context: dict[str, Any] | None = None,
        create_user_form: UserManagementForm | None = None,
    ) -> HttpResponse:
        """Render the user pane and reopen the creation form on validation errors."""
        context = self._tab_context("users")
        context.update(self._build_users_context(request) if users_context is None else users_context)
        context.update(
            create_user_form=UserManagementForm(prefix="create_user") if create_user_form is None else create_user_form,
            show_create_user_modal=create_user_form is not None,
            role_choices=User.Roles.choices,
        )
        return legacy_views.render(request, self.template_name, context)

    def post(self, request: HttpRequest, *args: object, **kwargs: Any) -> HttpResponse:
        """Validate and route a form submission to its management action."""
        if self.kwargs.get("changelog_editor"):
            entry = legacy_views.get_object_or_404(Changelog, pk=kwargs["pk"]) if kwargs.get("pk") else None
            self.changelog_form = ChangelogForm(
                request.POST, request.FILES, instance=entry, comparison_page=request.GET.get("comparison_page"),
            )
            if self.changelog_form.is_valid():
                with transaction.atomic():
                    saved = self.changelog_form.save()
                    self.changelog_form.save_comparisons()
                messages.success(request, "Changelog published." if saved.is_published else "Changelog saved as draft.")
                if not saved.is_published and self.changelog_form.comparison_page.has_other_pages():
                    return redirect(
                        reverse("hub:changelog-edit", args=[saved.pk])
                        + f"?comparison_page={self.changelog_form.comparison_page.number}"
                    )
                return redirect(reverse("hub:management") + "?tab=changelogs")
            messages.error(request, "Please correct the changelog errors below.")
            return self.get(request, *args, **kwargs)
        if request.POST.get("active_tab") == "accounts":
            return self._handle_account_submission(request)

        user_action_value = request.POST.get("user_action")
        if user_action_value:
            return self._handle_user_action_request(request, user_action_value)

        if request.POST.get("create_user_submit") == "1":
            create_user_form = UserManagementForm(request.POST, prefix="create_user")
            if create_user_form.is_valid():
                new_user = create_user_form.save()
                display_name = new_user.get_full_name() or new_user.username
                messages.success(request, f"Created new user account for {display_name}.")
                return redirect("hub:management")
            return self._render_users(request, create_user_form=create_user_form)

        formset = self.formset_class(request.POST, queryset=self.get_queryset())
        self._prepare_formset(formset)

        if formset.is_valid():
            current_admins = User.objects.filter(role=User.Roles.ADMIN).count()
            admin_delta = 0
            pending_error = False
            admin_removal_candidates = []

            for form in formset:
                if not form.cleaned_data:
                    continue

                is_delete = form.cleaned_data.get("DELETE")
                if is_delete:
                    if form.instance.pk and form.instance.is_superuser:
                        form.add_error("DELETE", "Superuser accounts cannot be deleted.")
                        pending_error = True
                        continue
                    if form.instance.pk and form.instance.role == User.Roles.ADMIN:
                        admin_delta -= 1
                        admin_removal_candidates.append((form, "delete"))
                    continue

                if not form.instance.pk and not form.has_changed():
                    continue

                new_role = form.cleaned_data.get("role")
                original_role = form.instance.role if form.instance.pk else None

                if form.instance.pk and form.instance.is_superuser and new_role != User.Roles.ADMIN:
                    form.add_error("role", "Superuser accounts must remain administrators.")
                    pending_error = True

                if form.instance.pk:
                    if original_role == User.Roles.ADMIN and new_role != User.Roles.ADMIN:
                        admin_delta -= 1
                        admin_removal_candidates.append((form, "role"))
                    elif original_role != User.Roles.ADMIN and new_role == User.Roles.ADMIN:
                        admin_delta += 1
                else:
                    if new_role == User.Roles.ADMIN:
                        admin_delta += 1

            if pending_error:
                return self._render_users(request)

            if current_admins + admin_delta <= 0:
                if admin_removal_candidates:
                    form, field = admin_removal_candidates[0]
                    if field == "delete":
                        form.add_error(None, "At least one administrator must remain.")
                    else:
                        form.add_error("role", "At least one administrator must remain.")
                else:
                    messages.error(request, "At least one administrator must remain.")
                return self._render_users(request)

            created_count = 0
            updated_count = 0
            deleted_count = 0

            with transaction.atomic():
                for form in formset:
                    if not form.cleaned_data:
                        continue

                    if not form.has_changed() and form.instance.pk:
                        continue

                    is_new = not form.instance.pk
                    if not is_new:
                        target = legacy_views.get_object_or_404(User.objects.select_for_update(), pk=form.instance.pk)
                        if not _can_manage_user(request.user, target):
                            raise PermissionDenied("Only Django superusers can manage privileged accounts.")
                    form.save()
                    if is_new:
                        created_count += 1
                    else:
                        updated_count += 1

            if created_count or updated_count or deleted_count:
                parts = []
                if created_count:
                    parts.append(f"created {created_count} user account{'s' if created_count != 1 else ''}")
                if updated_count:
                    parts.append(f"updated {updated_count} user account{'s' if updated_count != 1 else ''}")
                if deleted_count:
                    parts.append(f"removed {deleted_count} user account{'s' if deleted_count != 1 else ''}")
                messages.success(request, ", ".join(parts).capitalize() + ".")
            else:
                messages.info(request, "No changes detected.")
            return redirect("hub:management")

        return self._render_users(request)

    @staticmethod
    def _account_page(request, per_page=50):
        query = request.GET.get("account_q", "").strip()
        accounts = Account.objects.order_by("name", "pk")
        if query:
            accounts = accounts.filter(name__icontains=query)
        return Paginator(accounts, per_page).get_page(request.GET.get("account_page")), query

    def _build_account_formset(
        self, accounts: QuerySet[Account], data: QueryDict | None = None, *, prefix: str | None = None,
        definition: FormDefinition | None = None,
    ) -> AccountManagementFormSet:
        """Build the editor formset with the saved answers for its accounts."""
        from hub.form_editor import get_definition

        if definition is None:
            definition = get_definition("account_management")
        answers = {}
        if definition and definition.published_version_id:
            answers = dict(FormAnswerSet.objects.filter(
                definition=definition, content_type=ContentType.objects.get_for_model(Account),
                object_id__in=[account.pk for account in accounts],
            ).values_list("object_id", "answers"))
        return self.account_form_class(data, queryset=accounts, prefix=prefix, definition=definition, answers=answers)

    def _render_accounts(
        self, request: HttpRequest, *, account_formset: AccountManagementFormSet | None = None,
    ) -> HttpResponse:
        """Render the Accounts page or fragment while retaining invalid submitted rows."""
        from hub.form_editor import get_definition

        is_batch = account_formset is not None
        field_limit = settings.DATA_UPLOAD_MAX_NUMBER_FIELDS
        definition = get_definition("account_management") if not is_batch else account_formset.editor_definition
        per_page = 50
        if field_limit is not None:
            schema_fields = definition.published_version.schema["fields"] if definition.published_version_id else ()
            # Reserve the formset inputs and new row; count every possible multiselect value.
            custom_fields = sum(
                len(field["choices"]) if field["type"] == "multiselect" else 1
                for field in schema_fields if field["key"].startswith("custom_")
                and not (field.get("hidden") or field.get("archived"))
            )
            new_row_fields = 3 + custom_fields  # Name, ID, signed version, plus custom fields.
            existing_row_fields = new_row_fields + 1  # DELETE.
            per_page = max(1, min(per_page, (field_limit - 6 - new_row_fields) // existing_row_fields))
        page, query = self._account_page(request, per_page)
        fragment = not is_batch and request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if not is_batch:
            # Fragments must not reuse a prefix from the persistent new-account row.
            account_formset = self._build_account_formset(
                page.object_list, prefix="account-page" if fragment else None, definition=definition,
            )
        extra_forms = account_formset.extra_forms
        page_query = {"tab": "accounts"}
        if query:
            page_query["account_q"] = query
        page_url = reverse("hub:management") + "?" + urlencode(page_query)
        context = {
            "active_tab": "accounts", "account_formset": account_formset,
            "account_page_obj": page, "account_search_query": query, "account_page_url": page_url,
            "account_list_url": page_url + (f"&account_page={page.number}" if page.number > 1 else ""),
            "account_edit_rows": account_formset.initial_forms,
            "account_new_form": extra_forms[0] if extra_forms else None,
            "account_field_limit": field_limit,
        }
        if is_batch:
            # Keep every submitted row available for correction, even off-page drafts.
            context["account_edit_rows"] += extra_forms[1:]
        if fragment:
            response = legacy_views.render(None, f"{self.template_name}#account-results", context)
        else:
            context["account_native_save_blocked"] = field_limit is not None and (
                account_formset.submission_overhead()
                + sum(account_formset.submission_field_count(form) for form in account_formset)
                > field_limit
            )
            tab_context = self._tab_context("accounts", total_accounts=None if query else page.paginator.count)
            response = legacy_views.render(request, self.template_name, {**tab_context, **context})
        patch_cache_control(response, private=True, no_store=True)
        patch_vary_headers(response, ("X-Requested-With",))
        return response

    def _handle_account_submission(self, request: HttpRequest) -> HttpResponse:
        """Apply the account batch atomically or redisplay its validation errors."""
        from hub.form_editor import SchemaValidationError

        data = request.POST
        account_ids = set()
        for key, value in data.items():
            if re.fullmatch(r"form-\d+-id", key):
                try:
                    pk = int(value)
                except ValueError:
                    continue
                if 0 < pk <= 2**63 - 1:
                    account_ids.add(pk)
        accounts = Account.objects.filter(pk__in=account_ids).order_by("pk")
        save_error = None
        with transaction.atomic():
            account_formset = self._build_account_formset(accounts.select_for_update(), data)
            if not account_formset.is_valid():
                return self._render_accounts(request, account_formset=account_formset)

            created = updated = deleted = 0
            try:
                with transaction.atomic():
                    for index, form in enumerate(account_formset):
                        if not form.has_changed():
                            continue
                        if form.cleaned_data.get("DELETE"):
                            form.instance.delete()
                            deleted += 1
                        else:
                            is_new = not form.instance.pk
                            form.save()
                            if is_new:
                                created += 1
                            else:
                                updated += 1
            except ProtectedError:
                save_error = (index, "DELETE", "This account is in use and cannot be deleted.")
            except SchemaValidationError as exc:
                save_error = (index, None, " ".join(exc.messages))
            except IntegrityError as exc:
                if getattr(getattr(exc, "__cause__", None), "pgcode", None) != "23505":
                    raise
                save_error = (index, "name", "Account with this Name already exists.")

        if save_error:
            index, field, error = save_error
            # A transaction rollback does not restore PKs or attributes on model instances.
            account_formset = self._build_account_formset(accounts, data)
            account_formset.is_valid()
            account_formset.forms[index].add_error(field, error)
            return self._render_accounts(request, account_formset=account_formset)

        parts = []
        if created:
            parts.append(f"created {created} account{'s' if created != 1 else ''}")
        if updated:
            parts.append(f"updated {updated} account{'s' if updated != 1 else ''}")
        if deleted:
            parts.append(f"removed {deleted} account{'s' if deleted != 1 else ''}")
        if parts:
            messages.success(request, ", ".join(parts).capitalize() + ".")
        else:
            messages.info(request, "No account changes detected.")
        page_query = {"tab": "accounts"}
        for key in ("account_q", "account_page"):
            if request.GET.get(key):
                page_query[key] = request.GET[key]
        return redirect(reverse("hub:management") + "?" + urlencode(page_query))

    @staticmethod
    def _tab_context(active_tab, *, total_accounts=None):
        return {
            "active_tab": active_tab,
            "total_users": User.objects.count(),
            "total_accounts": Account.objects.count() if total_accounts is None else total_accounts,
        }

    def _build_activity_context(self):
        now = timezone.now()
        query = (self.request.GET.get("activity_q") or "").strip()
        status = self.request.GET.get("activity_status") or "all"
        if status not in dict(SIGN_IN_STATUS_CHOICES):
            status = "all"
        logs = filter_sign_in_logs(SignInLog.objects.select_related("user"), query=query, status=status, now=now)
        paginator = Paginator(logs, 50)
        try:
            page_obj = paginator.page(self.request.GET.get("activity_page") or 1)
        except InvalidPage:
            page_obj = paginator.page(1)
        page_obj.object_list = annotate_sign_in_logs(page_obj.object_list, now)
        return {
            "activity_logs_page_obj": page_obj,
            "activity_query": query,
            "activity_status": status,
            "activity_status_choices": SIGN_IN_STATUS_CHOICES,
            "activity_base_query": urlencode({"tab": "activity", "activity_q": query, "activity_status": status}),
            "activity_summary": summarize_sign_ins(now),
        }

    @staticmethod
    def _prepare_formset(formset):
        for form in formset:
            delete_field = form.fields.get("DELETE")
            if delete_field:
                existing_class = delete_field.widget.attrs.get("class", "")
                delete_field.widget.attrs["class"] = (existing_class + " form-check-input").strip()

    @staticmethod
    def _sync_account_baseline():
        # Accounts are created via request get_or_create and Management "New".
        # Do not bulk-seed ACCOUNT_NAME_RAW on an empty table — that reintroduces
        # hundreds of unused names after prune_unused_accounts.
        return

    @transaction.atomic
    def _handle_user_action_request(self, request, action_value):
        action, separator, raw_user_id = (action_value or "").partition(":")
        if not separator:
            messages.error(request, "We could not determine the requested action.")
            return redirect("hub:management")

        try:
            target_user = User.objects.select_for_update().get(pk=int(raw_user_id))
        except (User.DoesNotExist, ValueError):
            messages.error(request, "We could not find the selected user account.")
            return redirect("hub:management")

        if action in {"reset_password", "delete_user"} and not _can_manage_user(request.user, target_user):
            raise PermissionDenied("Only Django superusers can manage privileged accounts.")
        if action == "reset_password":
            return self._reset_user_password(request, target_user)
        if action == "delete_user":
            return self._delete_user_account(request, target_user)

        messages.error(request, "Unknown action requested.")
        return redirect("hub:management")

    @staticmethod
    def _clear_user_sessions(target_user: User) -> int:
        removed = 0
        user_id = str(target_user.pk)
        session_qs = Session.objects.filter(expire_date__gte=timezone.now())
        for session in session_qs:
            try:
                data = session.get_decoded()
            except Exception:
                continue
            if str(data.get("_auth_user_id")) == user_id:
                session.delete()
                removed += 1
        return removed

    def _reset_user_password(self, request, target_user: User):
        temporary_password = self._generate_temporary_password()
        target_user.set_password(temporary_password)
        target_user.must_change_password = True
        target_user.save(update_fields=["password", "must_change_password"])
        removed_sessions = self._clear_user_sessions(target_user)
        display_name = target_user.get_full_name() or target_user.username
        message = (
            f"Reset password for {display_name}. Temporary password: {temporary_password}. "
            "They must set a new password on next sign-in."
        )
        if removed_sessions:
            message += f" Ended {removed_sessions} session{'s' if removed_sessions != 1 else ''}."
        messages.success(request, message)
        return redirect("hub:management")

    @staticmethod
    def _generate_temporary_password() -> str:
        return get_random_string(16, allowed_chars="abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789")

    @staticmethod
    def _protected_delete_error_message(display_name: str, protected_records: set) -> str:
        if not protected_records:
            return f"Cannot delete the account for {display_name} because it is still referenced by protected records."

        summary = []
        for record in sorted(protected_records, key=lambda obj: (obj._meta.label_lower, getattr(obj, "reference_code", "") or "", obj.pk)):
            model_name = record._meta.verbose_name.title()
            identifier = getattr(record, "reference_code", None) or getattr(record, "title", None) or getattr(record, "username", None)
            if identifier:
                summary.append(f"{model_name} {identifier}")
            else:
                summary.append(f"{model_name} #{record.pk}")

        details = ", ".join(summary[:3])
        if len(summary) > 3:
            details += ", ..."
        return (
            f"Cannot delete the account for {display_name} because it is still referenced by protected records: {details}. "
            "Reassign or delete those related records first."
        )

    def _delete_user_account(self, request, target_user: User):
        if target_user.is_superuser:
            messages.error(request, "Superuser accounts cannot be deleted.")
            return redirect("hub:management")

        if target_user.pk == request.user.pk:
            messages.error(request, "You cannot delete your own account.")
            return redirect("hub:management")

        if target_user.role == User.Roles.ADMIN:
            admin_count = User.objects.filter(role=User.Roles.ADMIN).count()
            if admin_count <= 1:
                messages.error(request, "At least one administrator must remain.")
                return redirect("hub:management")

        display_name = target_user.get_full_name() or target_user.username
        try:
            target_user.delete()
        except ProtectedError as exc:
            protected_records = getattr(exc, "protected", set())
            if not protected_records and len(exc.args) > 1 and isinstance(exc.args[1], set):
                protected_records = exc.args[1]
            message = self._protected_delete_error_message(display_name, protected_records)
            messages.error(request, message)
            return redirect("hub:management")

        messages.success(request, f"Deleted user account for {display_name}.")
        return redirect("hub:management")


class UserEditView(AdminRequiredMixin, LoginRequiredMixin, View):
    """Handle the per-user Manage modal: update info and optionally change password."""

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Save changes to a user the administrator is allowed to manage."""
        target_user = legacy_views.get_object_or_404(User.objects.select_for_update(), pk=pk)
        if not _can_manage_user(request.user, target_user):
            raise PermissionDenied("Only Django superusers can manage privileged accounts.")
        form = UserManagementForm(request.POST, instance=target_user)
        if form.is_valid():
            form.save()
            display_name = target_user.get_full_name() or target_user.username
            messages.success(request, f"Updated user account for {display_name}.")
        else:
            for field_name, field_errors in form.errors.items():
                label = form.fields[field_name].label if field_name in form.fields else field_name
                for error in field_errors:
                    messages.error(request, f"{label}: {error}")
        return redirect("hub:management")
