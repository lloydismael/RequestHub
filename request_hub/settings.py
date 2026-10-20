import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if DEBUG:
    if not SECRET_KEY:
        SECRET_KEY = "insecure-development-key"
elif not SECRET_KEY or SECRET_KEY == "insecure-development-key":
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a non-default value when DJANGO_DEBUG is False."
    )

APP_VERSION = os.getenv("APP_VERSION", "dev")
PUBLIC_BASE_URL = os.getenv("DJANGO_PUBLIC_BASE_URL", "https://esgrequesthub.com").strip().rstrip("/")
public_url = urlsplit(PUBLIC_BASE_URL)
if (
    public_url.scheme not in (("http", "https") if DEBUG else ("https",))
    or not public_url.hostname
    or public_url.username is not None
    or public_url.password is not None
    or public_url.path
    or public_url.query
    or public_url.fragment
):
    raise ImproperlyConfigured("DJANGO_PUBLIC_BASE_URL must be an absolute HTTPS origin (HTTP is allowed in DEBUG).")
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]

primary_domains = [
    host.strip()
    for host in os.getenv("DJANGO_PRIMARY_DOMAIN", "esgrequesthub.com").split(",")
    if host.strip()
]
for primary_domain in primary_domains:
    if primary_domain not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(primary_domain)

website_hostname = os.getenv("WEBSITE_HOSTNAME")
if website_hostname and website_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(website_hostname)

csrf_hosts = [host.strip() for host in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if host.strip()]
if website_hostname:
    csrf_hosts.append(f"https://{website_hostname}")
if primary_domains:
    schemes = ("https", "http") if DEBUG else ("https",)
    for primary_domain in primary_domains:
        for scheme in schemes:
            origin = f"{scheme}://{primary_domain}"
            if origin not in csrf_hosts:
                csrf_hosts.append(origin)
if csrf_hosts:
    CSRF_TRUSTED_ORIGINS = csrf_hosts

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "accounts.apps.AccountsConfig",
    "hub.apps.HubConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "accounts.middleware.PasswordChangeRequiredMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "request_hub.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "hub.context_processors.notification_badges",
            ],
        },
    },
]

WSGI_APPLICATION = "request_hub.wsgi.application"
ASGI_APPLICATION = "request_hub.asgi.application"

DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres" if DEBUG else "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres" if DEBUG else "")
DB_HOST = os.getenv("DB_HOST", "localhost" if DEBUG else "")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_SSLMODE = os.getenv("DB_SSLMODE", "" if DEBUG else "require")
DB_SSLROOTCERT = os.getenv("DB_SSLROOTCERT")
DB_CONN_MAX_AGE = int(os.getenv("DB_CONN_MAX_AGE", "60"))

if not DEBUG and (not DB_HOST or not DB_USER or not DB_PASSWORD):
    raise ImproperlyConfigured("DB_HOST, DB_USER, and DB_PASSWORD are required when DJANGO_DEBUG is False.")

db_options = {}
if DB_SSLMODE:
    db_options["sslmode"] = DB_SSLMODE
if DB_SSLROOTCERT:
    db_options["sslrootcert"] = DB_SSLROOTCERT

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
        "CONN_MAX_AGE": DB_CONN_MAX_AGE,
        "OPTIONS": db_options,
    }
}

if not db_options:
    DATABASES["default"].pop("OPTIONS")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

media_url_setting = os.getenv("DJANGO_MEDIA_URL", "media/").strip()
if not media_url_setting.startswith("/"):
    media_url_setting = f"/{media_url_setting}"
if not media_url_setting.endswith("/"):
    media_url_setting = f"{media_url_setting}/"
MEDIA_URL = media_url_setting

media_root_setting = os.getenv("DJANGO_MEDIA_ROOT")
if media_root_setting:
    MEDIA_ROOT = Path(media_root_setting).expanduser()
else:
    MEDIA_ROOT = BASE_DIR / "media"

try:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
except OSError:
    # Some deployment targets provide a read-only mount during build; skip creation errors.
    pass

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "hub:dashboard"
LOGOUT_REDIRECT_URL = "login"

# Session security: expire the session cookie when the browser is closed so
# users must log in again after closing the tab/window. SERVER-SIDE age is
# capped at 8 hours (28 800 s) as a fallback for browsers that restore tabs.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 28800  # 8 hours in seconds
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

AUTHENTICATION_BACKENDS = [
    "accounts.backends.CaseInsensitiveUsernameBackend",
]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv("DJANGO_DEFAULT_FROM_EMAIL", "ESG Request Hub <no-reply@esgrequesthub.com>")

# Azure Communication Services Email
ACS_EMAIL_CONNECTION_STRING = os.getenv("ACS_EMAIL_CONNECTION_STRING", "").strip()
ACS_EMAIL_SENDER = os.getenv(
    "ACS_EMAIL_SENDER",
    "DoNotReply@esgrequesthub.com",
).strip()

# Microsoft Graph (MSAL) integration scoped to the phildata tenant.
PHILDATA_TENANT_ID = os.getenv("PHILDATA_TENANT_ID", "").strip()
PHILDATA_CLIENT_ID = os.getenv("PHILDATA_CLIENT_ID", "").strip()
PHILDATA_CLIENT_SECRET = os.getenv("PHILDATA_CLIENT_SECRET", "").strip()
PHILDATA_DOMAIN = os.getenv("PHILDATA_DOMAIN", "phildata.com").strip().lower()
PHILDATA_GRAPH_SCOPE = [
    scope.strip()
    for scope in os.getenv("PHILDATA_GRAPH_SCOPE", "https://graph.microsoft.com/.default").split(",")
    if scope.strip()
]

PROFILE_COMPLETION_EXEMPT_URLS = [
    "accounts:update",
    "logout",
    "healthz",
]

if os.getenv("DJANGO_DEFAULT_USER_PASSWORD"):
    raise ImproperlyConfigured(
        "DJANGO_DEFAULT_USER_PASSWORD has been removed. Delete it from .env and platform settings. "
        "Admin password resets now issue a one-time temporary password."
    )
