from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from .storage import DatabaseMediaStorage
from .themes import custom_theme_style, custom_theme_vars


def user_profile_photo_upload_to(instance, filename):
    suffix = ""
    if filename and "." in filename:
        suffix = f".{filename.rsplit('.', 1)[-1]}"
    return f"profile_photos/{uuid4().hex}{suffix}"


class StoredFile(models.Model):
    name = models.CharField(max_length=255, unique=True)
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    data = models.BinaryField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class BackupSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("User", null=True, on_delete=models.SET_NULL, related_name="backup_snapshots")
    reason = models.CharField(max_length=255, default="Manual backup")
    payload = models.JSONField()
    size = models.PositiveBigIntegerField()
    checksum = models.CharField(max_length=64)
    counts = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="ready")

    class Meta:
        ordering = ["-created_at", "-pk"]


class RecoveryEvent(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey("User", null=True, on_delete=models.SET_NULL, related_name="recovery_events")
    snapshot = models.ForeignKey(BackupSnapshot, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=20)
    outcome = models.CharField(max_length=20)
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at", "-pk"]


class RecoveryPreview(models.Model):
    token = models.UUIDField(default=uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey("User", on_delete=models.CASCADE)
    snapshot = models.ForeignKey(BackupSnapshot, null=True, blank=True, on_delete=models.SET_NULL)
    payload = models.JSONField()
    consumed_at = models.DateTimeField(null=True, blank=True)


class User(AbstractUser):
    class Roles(models.TextChoices):
        REQUESTOR = "requestor", "Requestor"
        REQUESTOR_ESS = "requestor_ess", "Requestor-ESS"
        PM_ESS = "pm_ess", "PM-ESS"
        PM_ESG = "pm_esg", "PM-ESG"
        ENGINEER = "engineer", "Engineer"
        ON_HOLD = "on_hold", "On Hold"
        ADMIN = "admin", "Admin"

    phone_number = models.CharField(
        max_length=20,
        blank=True,
        validators=[RegexValidator(r"^[0-9+\-() ]*$", "Phone number contains invalid characters.")],
    )
    DEFAULT_THEME = "default"
    CUSTOM_THEME = "custom"
    BANNER_GRADIENT_CHOICES = [
        (DEFAULT_THEME, "Default"),
        ("blue", "Blue"),
        ("sunset", "Sunset"),
        ("forest", "Forest"),
        ("crimson", "Crimson"),
        ("slate", "Slate"),
        ("aurora", "Aurora"),
        ("rose", "Rose"),
        ("teal", "Teal"),
        ("amber", "Amber"),
        ("ocean", "Ocean"),
        (CUSTOM_THEME, "Custom"),
    ]
    HEX_COLOR_VALIDATOR = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Enter a colour as #RRGGBB.")

    profile_photo = models.ImageField(
        upload_to=user_profile_photo_upload_to,
        storage=DatabaseMediaStorage(),
        blank=True,
        null=True,
    )
    banner_gradient = models.CharField(
        max_length=20,
        choices=BANNER_GRADIENT_CHOICES,
        default=DEFAULT_THEME,
        blank=True,
    )
    custom_theme_primary = models.CharField(max_length=7, blank=True, default="", validators=[HEX_COLOR_VALIDATOR])
    custom_theme_secondary = models.CharField(max_length=7, blank=True, default="", validators=[HEX_COLOR_VALIDATOR])
    custom_theme_tertiary = models.CharField(max_length=7, blank=True, default="", validators=[HEX_COLOR_VALIDATOR])
    role =  models.CharField(max_length=20, choices=Roles.choices, default=Roles.REQUESTOR)
    department = models.CharField(max_length=100, blank=True, default="")
    REQUESTOR_ROLES = (Roles.REQUESTOR, Roles.REQUESTOR_ESS)
    REQUEST_CREATOR_ROLES = (Roles.REQUESTOR, Roles.REQUESTOR_ESS, Roles.PM_ESS, Roles.PM_ESG, Roles.ADMIN)
    ENGINEER_ACCESS_ROLES = (Roles.ENGINEER, Roles.ON_HOLD)
    ASSIGNABLE_ENGINEER_ROLES = (Roles.ENGINEER,)
    profile_completed = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    show_chatbot = models.BooleanField(default=False, verbose_name="Show AI Chatbot")
    idle_timeout_enabled = models.BooleanField(default=True, verbose_name="5-Minute Idle Timeout")
    show_login_banner = models.BooleanField(default=True, verbose_name="Welcome Banner on Login")

    # ── Appearance / Performance / Accessibility preferences ─────────────
    # All default to current behaviour so existing users see no change.
    pref_compact_tables = models.BooleanField(
        default=False, verbose_name="Compact Tables",
        help_text="Denser rows and tighter spacing on data tables.",
    )
    pref_large_text = models.BooleanField(
        default=False, verbose_name="Large Text",
        help_text="Increase the base font size across the app.",
    )
    pref_reduce_effects = models.BooleanField(
        default=False, verbose_name="Reduce Visual Effects",
        help_text="Disable glass blur and animated background effects for better performance.",
    )
    pref_reduce_motion = models.BooleanField(
        default=False, verbose_name="Reduce Motion",
        help_text="Disable entrance animations and smooth scrolling.",
    )

    @property
    def custom_theme_colors(self):
        return (self.custom_theme_primary, self.custom_theme_secondary, self.custom_theme_tertiary)

    @property
    def account_theme(self):
        """Effective theme key; unknown values and incomplete custom palettes fall back to default."""
        theme = self.banner_gradient
        if theme not in dict(self.BANNER_GRADIENT_CHOICES):
            return self.DEFAULT_THEME
        if theme == self.CUSTOM_THEME and not custom_theme_vars(*self.custom_theme_colors):
            return self.DEFAULT_THEME
        return theme

    @property
    def account_theme_style(self):
        if self.account_theme != self.CUSTOM_THEME:
            return ""
        return custom_theme_style(*self.custom_theme_colors)

    @property
    def account_theme_label(self):
        return dict(self.BANNER_GRADIENT_CHOICES)[self.account_theme]

    def must_complete_profile(self) -> bool:
        required_fields = [self.email, self.phone_number, self.profile_photo]
        return not self.profile_completed or any(not value for value in required_fields)

    def mark_profile_complete(self):
        self.profile_completed = True
        self.save(update_fields=["profile_completed"])

    @property
    def profile_photo_url(self):
        if not self.profile_photo:
            return ""
        try:
            url = self.profile_photo.url
        except ValueError:
            return ""
        return url

    @property
    def is_engineer_access_role(self) -> bool:
        return self.role in self.ENGINEER_ACCESS_ROLES
