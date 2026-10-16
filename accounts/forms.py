from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import User

ROLE_LABELS = {
    User.Roles.ADMIN: "Admin",
    User.Roles.PM_ESG: "PM-ESG",
    User.Roles.ENGINEER: "Engineer",
    User.Roles.ON_HOLD: "On Hold",
    User.Roles.REQUESTOR: "Requestor",
    User.Roles.REQUESTOR_ESS: "Requestor-ESS",
    User.Roles.PM_ESS: "PM-ESS",
}

ROLE_CHOICES = [(key, label) for key, label in ROLE_LABELS.items()]

ROLE_ALIAS_MAP = {
    User.Roles.ADMIN: {
        "admin": "Admin",
        "admin1": "Admin1",
    },
    User.Roles.ENGINEER: {
        "admin": "engineer_admin",
        "admin1": "engineer_admin1",
    },
    User.Roles.REQUESTOR: {
        "admin": "account_admin",
        "admin1": "manager_admin1",
    },
}

ROLE_DEFAULT_USERNAMES = {role: aliases.get("admin", "") for role, aliases in ROLE_ALIAS_MAP.items()}


class ProfileForm(forms.ModelForm):
    current_password = forms.CharField(
        label="Current password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "current-password"}),
        help_text="Enter your current password to set a new one.",
    )
    new_password1 = forms.CharField(
        label="New password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        help_text="Enter the same password as before for verification.",
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone_number", "department", "profile_photo", "banner_gradient"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            "department": forms.Select(choices=[
                ("", "— Select department —"),
                ("ESS", "ESS"),
                ("HP", "HP"),
                ("Dell", "Dell"),
                ("ENS", "ENS"),
                ("ESG", "ESG"),
                ("ISG", "ISG"),
                ("Other", "Other"),
            ], attrs={"class": "form-select"}),
            "profile_photo": forms.FileInput(attrs={"class": "form-control"}),
            "banner_gradient": forms.HiddenInput(),
        }

    def __init__(self, *args, is_admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not is_admin:
            dept_widget = self.fields["department"].widget
            dept_widget.attrs["disabled"] = True
            dept_widget.attrs["title"] = "Only administrators can edit this field"
            self.fields["department"].required = False

    def clean_department(self):
        # Non-admin: keep existing value (disabled fields are not submitted)
        submitted = self.data.get("department")
        if submitted is None and self.instance and self.instance.pk:
            return self.instance.department
        return self.cleaned_data.get("department", "")

    field_order = [
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "department",
        "profile_photo",
        "banner_gradient",
        "current_password",
        "new_password1",
        "new_password2",
    ]

    def clean(self):
        cleaned_data = super().clean()
        current_password = cleaned_data.get("current_password")
        new_password1 = cleaned_data.get("new_password1")
        new_password2 = cleaned_data.get("new_password2")
        user = self.instance

        if new_password1 or new_password2:
            if not current_password:
                self.add_error("current_password", "Enter your current password to set a new one.")
            elif not user.check_password(current_password):
                self.add_error("current_password", "Current password is incorrect.")

            if not new_password1:
                self.add_error("new_password1", "Enter a new password.")
            if new_password1 and new_password1 != new_password2:
                self.add_error("new_password2", "Passwords do not match.")

            if new_password1 and not self.errors.get("new_password1"):
                try:
                    password_validation.validate_password(new_password1, user)
                except ValidationError as exc:
                    self.add_error("new_password1", exc)
        elif current_password:
            self.add_error("new_password1", "Enter a new password.")

        if user.must_change_password and not new_password1:
            self.add_error("new_password1", "Set a new password to continue.")
            if not current_password:
                self.add_error("current_password", "Enter your current password to set a new one.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password = self.cleaned_data.get("new_password1")

        if new_password:
            user.set_password(new_password)
            if user.must_change_password:
                user.must_change_password = False

        if commit:
            user.save()
            self.save_m2m()

        return user


class RoleAuthenticationForm(AuthenticationForm):

    def __init__(self, request=None, *args, **kwargs):
        self._matched_role: str | None = None
        username_field = User.USERNAME_FIELD
        data = kwargs.get("data")
        if data:
            data = data.copy()
            username_input = data.get(username_field)
            if username_input:
                normalized = username_input.strip()
                normalized_lower = normalized.lower()
                matched_alias = None
                matched_role = None
                for role_value, alias_map in ROLE_ALIAS_MAP.items():
                    alias = alias_map.get(normalized_lower)
                    if alias:
                        matched_alias = alias
                        matched_role = role_value
                        break

                if matched_alias:
                    data[username_field] = matched_alias
                    self._matched_role = matched_role
                else:
                    exact_match = User.objects.filter(username__exact=normalized).values_list("username", "role").first()
                    if exact_match:
                        actual_username, role_value = exact_match
                        data[username_field] = actual_username
                        self._matched_role = self._matched_role or role_value
                    else:
                        case_insensitive_match = (
                            User.objects.filter(username__iexact=normalized)
                            .order_by("username")
                            .values_list("username", "role")
                            .first()
                        )
                        if case_insensitive_match:
                            actual_username, role_value = case_insensitive_match
                            data[username_field] = actual_username
                            self._matched_role = self._matched_role or role_value
                        else:
                            data[username_field] = normalized_lower
            kwargs["data"] = data

        super().__init__(request=request, *args, **kwargs)

        # placeholder=" " is required for the CSS floating-label rule
        # (`input:not(:placeholder-shown) ~ label`) used on the login page.
        self.fields["username"].widget.attrs.update(
            {"class": "form-control", "placeholder": " ", "autocomplete": "username"}
        )
        self.fields["password"].widget.attrs.update(
            {"class": "form-control", "placeholder": " ", "autocomplete": "current-password"}
        )


class UserManagementForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password", "placeholder": "Set password"}),
        help_text="Required for new users. Leave blank to keep the current password.",
    )
    password2 = forms.CharField(
        label="Confirm Password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password", "placeholder": "Confirm password"}),
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "department", "role"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control", "placeholder": "Username"}),
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "name@example.com"}),
            "department": forms.Select(choices=[
                ("", "— Select department —"),
                ("ESS", "ESS"),
                ("HP", "HP"),
                ("Dell", "Dell"),
                ("ENS", "ENS"),
                ("ESG", "ESG"),
                ("ISG", "ISG"),
                ("Other", "Other"),
            ], attrs={"class": "form-select"}),
        }
        labels = {
            "role": "Account Type",
            "department": "Department",
        }

    field_order = [
        "first_name",
        "last_name",
        "username",
        "email",
        "department",
        "role",
        "password1",
        "password2",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        role_field = self.fields["role"]
        role_field.widget.attrs.update({"class": "form-select"})
        role_field.choices = ROLE_CHOICES

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if self.instance.pk:
            if password1 or password2:
                if not password1:
                    self.add_error("password1", "Enter a password.")
                if not password2:
                    self.add_error("password2", "Confirm the password.")
                if password1 and password2 and password1 != password2:
                    self.add_error("password2", "Passwords do not match.")
                if password1 and password1 == password2 and not self.errors.get("password1"):
                    try:
                        password_validation.validate_password(password1, self.instance)
                    except ValidationError as exc:
                        self.add_error("password1", exc)
        else:
            if not password1:
                self.add_error("password1", "Set an initial password for the new user.")
            if password1 and password1 != password2:
                self.add_error("password2", "Passwords do not match.")
            if password1 and not self.errors.get("password1"):
                try:
                    password_validation.validate_password(password1, self.instance)
                except ValidationError as exc:
                    self.add_error("password1", exc)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
            user.must_change_password = True
        elif not self.instance.pk:
            # Guard against saving a new user without credentials.
            user.set_unusable_password()
            user.must_change_password = True

        if not self.instance.pk:
            user.is_active = True

        if commit:
            user.save()
            self.save_m2m()
        return user

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if not username:
            raise forms.ValidationError("Username cannot be blank.")
        return username.lower()

    def clean_first_name(self):
        first_name = self.cleaned_data.get("first_name")
        return first_name.strip() if first_name else first_name

    def clean_last_name(self):
        last_name = self.cleaned_data.get("last_name")
        return last_name.strip() if last_name else last_name

    def clean_email(self):
        email = self.cleaned_data.get("email")
        return email.strip() if email else email


from hub.form_editor_runtime import editable_form

editable_form(ProfileForm)
editable_form(UserManagementForm)
