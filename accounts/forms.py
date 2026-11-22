from io import BytesIO
from typing import ClassVar

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import authenticate, check_password_with_timing_attack_mitigation, password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
<<<<<<< HEAD
from django.utils.functional import Promise
=======
from django.db import transaction
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
from django.views.decorators.debug import sensitive_variables

from .models import User
from .login_throttle import finish, reserve
from .sign_in_log import client_ip
from .themes import DEFAULT_CUSTOM_COLORS, custom_theme_style, normalize_hex

# Longest edge, in pixels, that a stored profile photo may have.
AVATAR_MAX_EDGE = 512
AVATAR_MAX_PIXELS = 16_000_000

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

LOGIN_FAILURE_MESSAGE = "Sign-in failed. Check your credentials or try again later."
APP_USERNAME_ALIASES = {"admin": "Admin", "admin1": "Admin1"}


class ThemeForm(forms.ModelForm):
    banner_gradient = forms.ChoiceField(choices=User.BANNER_GRADIENT_CHOICES)
    custom_theme_primary = forms.CharField(required=False, max_length=7)
    custom_theme_secondary = forms.CharField(required=False, max_length=7)
    custom_theme_tertiary = forms.CharField(required=False, max_length=7)

    CUSTOM_FIELDS = ("custom_theme_primary", "custom_theme_secondary", "custom_theme_tertiary")
    CHRISTMAS_FIELDS = User.CHRISTMAS_EFFECT_FIELDS
    CHRISTMAS_PREFERENCES_MARKER = "christmas_preferences_present"

    class Meta:
        model = User
        fields: ClassVar[list[str]] = ["banner_gradient", "custom_theme_primary", "custom_theme_secondary", "custom_theme_tertiary", *User.CHRISTMAS_EFFECT_FIELDS]
        widgets: ClassVar[dict[str, forms.Widget]] = {"banner_gradient": forms.RadioSelect()}
        labels: ClassVar[dict[str, str]] = {"pref_christmas_santa": "Santa blanket transition"}
        help_texts: ClassVar[dict[str, str]] = {"pref_christmas_santa": "Santa drops gifts while pulling a festive blanket across the page when you switch light/dark mode."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["banner_gradient"] = self.instance.account_theme
        # Seed legacy bound forms so validation retries retain saved checkbox choices.
        if self.is_bound and not self.data.get(self.CHRISTMAS_PREFERENCES_MARKER):
            self.data = self.data.copy()
            for name in self.CHRISTMAS_FIELDS:
                self.data[name] = getattr(self.instance, name)
        for name in self.CHRISTMAS_FIELDS:
            key = name[len("pref_christmas_"):]
            self.fields[name].widget.attrs.update({
                "id": "christmas-effects-toggle" if key == "enabled" else f"christmas-{key}-toggle",
                "class": "form-check-input m-0",
                "data-christmas-effect": key,
                "aria-describedby": "christmas-master-help christmas-effects-status" if key == "enabled" else f"christmas-{key}-help",
            })

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("banner_gradient") == User.CUSTOM_THEME:
            for name in self.CUSTOM_FIELDS:
                color = normalize_hex(cleaned.get(name))
                if color:
                    cleaned[name] = color
                elif name in self.fields and name not in self.errors:
                    self.add_error(name, "Choose all three palette colours as #RRGGBB.")
        else:
            # A non-custom save keeps the stored palette so switching back restores it.
            for name in self.CUSTOM_FIELDS:
                cleaned[name] = getattr(self.instance, name)
        return cleaned

    @property
    def christmas_options(self):
        return [self[name] for name in self.CHRISTMAS_FIELDS if name != "pref_christmas_enabled"]

    @property
    def custom_colors(self):
        """Palette shown in the editor: submitted values first, then saved, then defaults."""
        colors = []
        for name, fallback in zip(self.CUSTOM_FIELDS, DEFAULT_CUSTOM_COLORS):
            submitted = normalize_hex(self.data.get(name)) if self.is_bound else ""
            colors.append(submitted or normalize_hex(getattr(self.instance, name)) or fallback)
        return colors

    @property
    def custom_style(self):
        return custom_theme_style(*self.custom_colors)


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
        fields: ClassVar[list[str]] = ["first_name", "last_name", "email", "phone_number", "department", "profile_photo", "banner_gradient"]
        widgets: ClassVar[dict[str, forms.Widget]] = {
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

    def __init__(self, *args, is_admin=False, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        if not is_admin:
            department = self.fields["department"]
            department.disabled = True
            department.widget.attrs["title"] = "Only administrators can edit this field"

    def clean_department(self):
        # A partial profile save can omit department even for an administrator.
        if self.add_prefix("department") not in self.data and self.instance.pk:
            return self.instance.department
        return self.cleaned_data.get("department", "")

    def clean_profile_photo(self):
        """Normalize uploads to a square JPEG.

        The browser crop step is a convenience, not a guarantee, so enforce the
        same result server-side: input pixels are limited before decoding, then
        the upload is center-cropped to a square and bounded before storage.
        """
        photo = self.cleaned_data.get("profile_photo")
        if not photo or not hasattr(photo, "read"):
            return photo
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError:  # pragma: no cover - Pillow is a pinned dependency
            return photo
        try:
            photo.seek(0)
            with Image.open(photo) as source:
                width, height = source.size
                if width * height > AVATAR_MAX_PIXELS:
                    raise ValidationError(
                        f"Please upload an image with no more than {AVATAR_MAX_PIXELS:,} pixels.",
                        code="image_too_large",
                    )
                image = source.convert("RGB")
                edge = min(width, height)
                if edge < 1:
                    raise ValidationError("That image file appears to be empty.")
                left = (width - edge) // 2
                top = (height - edge) // 2
                square = image.crop((left, top, left + edge, top + edge))
                if square.width > AVATAR_MAX_EDGE:
                    square = square.resize(
                        (AVATAR_MAX_EDGE, AVATAR_MAX_EDGE), Image.LANCZOS
                    )
                buffer = BytesIO()
                square.save(buffer, format="JPEG", quality=90, optimize=True)
        except ValidationError:
            raise
        except Image.DecompressionBombError:
            raise ValidationError(
                "That image is too large. Please choose a smaller image.",
                code="image_too_large",
            )
        except (UnidentifiedImageError, OSError, ValueError):
            raise ValidationError("Please upload a valid image file (JPG, PNG or GIF).")
        buffer.seek(0)
        name = photo.name or "profile.jpg"
        stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "profile"
        return ContentFile(buffer.read(), name=f"{stem}.jpg")

    field_order: ClassVar[list[str]] = [
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
            else:
                source = client_ip(self.request) if self.request is not None else "unknown"

                def find_user():
                    return User.objects.filter(pk=user.pk).first() if user.pk else None

                fresh_user, reservation = reserve(source, user.get_username().casefold(), find_user)
                if reservation is None:
                    allowed = False
                else:
                    allowed = (
                        fresh_user is not None
                        and check_password_with_timing_attack_mitigation(fresh_user, current_password)
                    )
                    finish(reservation, success=allowed)
                if not allowed:
                    self.add_error("current_password", "Current password is incorrect.")

            if not new_password1:
                self.add_error("new_password1", "Enter a new password.")
            elif user.must_change_password and new_password1 == current_password:
                self.add_error("new_password1", "Choose a password different from your temporary password.")
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

        if commit:
            with transaction.atomic():
                fresh_user = User.objects.select_for_update().get(pk=user.pk)
                if new_password:
                    current_password = self.cleaned_data.get("current_password")
                    if not check_password_with_timing_attack_mitigation(fresh_user, current_password):
                        raise ValidationError("Current password is incorrect.")
                    fresh_user.set_password(new_password)
                    if fresh_user.must_change_password:
                        fresh_user.must_change_password = False
                for field in ("first_name", "last_name", "email", "phone_number", "department", "profile_photo", "banner_gradient"):
                    setattr(fresh_user, field, getattr(user, field))
                update_fields = ["first_name", "last_name", "email", "phone_number", "department", "profile_photo", "banner_gradient"]
                if new_password:
                    update_fields.extend(["password", "must_change_password"])
                fresh_user.save(update_fields=update_fields)
                user = fresh_user
            self.instance = user
            self.save_m2m()

        return user


class RoleAuthenticationForm(AuthenticationForm):
    error_messages: ClassVar[dict[str, str | Promise]] = {**AuthenticationForm.error_messages, "invalid_login": LOGIN_FAILURE_MESSAGE}

    def clean_username(self):
        username = self.cleaned_data["username"]
        return APP_USERNAME_ALIASES.get(username.lower(), username)

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)

        # placeholder=" " is required for the CSS floating-label rule
        # (`input:not(:placeholder-shown) ~ label`) used on the login page.
        self.fields["username"].widget.attrs.update(
            {"class": "form-control", "placeholder": " ", "autocomplete": "username"}
        )
        self.fields["password"].widget.attrs.update(
            {"class": "form-control", "placeholder": " ", "autocomplete": "current-password"}
        )


class StaffAuthenticationForm(AdminAuthenticationForm):
    error_messages: ClassVar[dict[str, str | Promise]] = {**AdminAuthenticationForm.error_messages, "invalid_login": LOGIN_FAILURE_MESSAGE}

    @sensitive_variables("password")
    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username is not None and password:
            self.user_cache = authenticate(self.request, username=username, password=password, staff_only=True)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


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
        fields: ClassVar[list[str]] = ["username", "first_name", "last_name", "email", "department", "role"]
        widgets: ClassVar[dict[str, forms.Widget]] = {
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
        labels: ClassVar[dict[str, str]] = {
            "role": "Account Type",
            "department": "Department",
        }

    field_order: ClassVar[list[str]] = [
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
