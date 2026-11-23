"""Start the localhost debugger against Azure only after a read-only schema check."""

import importlib.util
import json
import os
import re
import secrets
import sys
from pathlib import Path

import certifi
import django
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.db.migrations import exceptions as migration_errors
from django.db.migrations.executor import MigrationExecutor
from django.utils.autoreload import DJANGO_AUTORELOAD_ENV
from dotenv import dotenv_values

WORKSPACE = Path(__file__).resolve().parent.parent
ENV_FILE = WORKSPACE / ".env"
SNAPSHOT_ROOT = WORKSPACE / ".venv" / "request-hub-f5"


class LiveDebugError(Exception):
    pass


def select_source() -> tuple[bool, frozenset[tuple[str, str]]]:
    try:
        manifest = (SNAPSHOT_ROOT / "release.json").read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        sys.path.insert(0, str(WORKSPACE))
        return False, frozenset()
    except OSError:
        raise LiveDebugError("Cannot read the local release snapshot configuration.") from None
    try:
        config = json.loads(manifest)
    except ValueError:
        raise LiveDebugError("Invalid local release snapshot configuration.") from None
    if not isinstance(config, dict):
        raise LiveDebugError("Invalid local release snapshot configuration.")
    revision = config.get("revision")
    names = config.get("historical_migrations", [])
    if (
        not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)
        or not isinstance(names, list) or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z_]+\.[0-9]{4}_[a-z0-9_]+", name)
            for name in names
        )
    ):
        raise LiveDebugError("Invalid local release snapshot revision or migration list.")
    source = SNAPSHOT_ROOT / revision
    if not (source / "request_hub" / "settings.py").is_file():
        raise LiveDebugError("Local release snapshot is missing. Restore it or remove the local snapshot configuration.")
    if "request_hub" in sys.modules:
        raise LiveDebugError("Start the live debugger by script path to use the local release snapshot.")
    sys.path.insert(0, str(source))
    os.environ["APP_REVISION"] = revision
    return True, frozenset(tuple(name.split(".", 1)) for name in names)


def database_environment(path: Path) -> dict[str, str]:
    config = dotenv_values(path, encoding="utf-8-sig", interpolate=False)
    host = (config.get("DB_HOST") or "").strip()
    if not host.lower().endswith(".postgres.database.azure.com") or any(
        not (part and part.isascii() and part.replace("-", "").isalnum()) for part in host.split(".")
    ):
        raise LiveDebugError("Set an Azure PostgreSQL server as DB_HOST in the ignored .env file.")
    required = ("DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise LiveDebugError(f"Set {', '.join(missing)} in the ignored .env file.")
    port = config.get("DB_PORT") or "5432"
    if not port.isascii() or not port.isdecimal() or len(port) > 5 or not 1 <= int(port) <= 65535:
        raise LiveDebugError("Set DB_PORT to a valid PostgreSQL port in the ignored .env file.")
    return {
        **{key: config[key] for key in required},
        "DB_HOST": host,
        "DB_PORT": port,
        "DB_SSLMODE": "verify-full",
        "DB_SSLROOTCERT": config.get("DB_SSLROOTCERT") or certifi.where(),
    }


def check_schema(historical_migrations: frozenset[tuple[str, str]] = frozenset()) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        executor = MigrationExecutor(connection)
        loader = executor.loader
        loader.check_consistent_history(connection)
        if loader.detect_conflicts():
            raise LiveDebugError("This checkout has conflicting migrations. Resolve them before using Live Azure.")
        targets = loader.graph.leaf_nodes()
        pending = executor.migration_plan(targets)
        if pending:
            names = ", ".join(f"{migration.app_label}.{migration.name}" for migration, _ in pending[:5])
            raise LiveDebugError(f"Live Azure is missing migrations: {names}. Deploy them through the normal release workflow first.")
        unknown = loader.applied_migrations.keys() - loader.disk_migrations.keys() - historical_migrations
        managed_apps = {app_label for app_label, _ in targets}
        if any(app_label in managed_apps for app_label, _ in unknown):
            raise LiveDebugError("Live Azure has migrations not in this checkout. Use the matching release or update your checkout.")
        cursor.execute("""
            SELECT relation.relname, attribute.attname
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_attribute AS attribute ON attribute.attrelid = relation.oid
            WHERE pg_catalog.pg_table_is_visible(relation.oid)
              AND attribute.attnum > 0 AND NOT attribute.attisdropped
        """)
        columns = set(cursor.fetchall())
        missing = sorted(
            f"{model._meta.db_table}.{field.column}"
            for model in apps.get_models(include_auto_created=True)
            if model._meta.can_migrate(connection)
            for field in model._meta.local_concrete_fields
            if (model._meta.db_table, field.column) not in columns
        )
        if missing:
            raise LiveDebugError(f"Live Azure is missing expected columns: {', '.join(missing[:5])}. Check the release schema.")


def main() -> None:
    is_reload_child = os.environ.get(DJANGO_AUTORELOAD_ENV) == "true"
    try:
        pinned_release, historical_migrations = select_source()
        browser_reload = not pinned_release and importlib.util.find_spec("django_browser_reload") is not None
        os.environ.update(database_environment(ENV_FILE))
        signing_key = os.environ.get("DJANGO_SECRET_KEY") if is_reload_child else None
        os.environ.update(
            DJANGO_SETTINGS_MODULE="request_hub.settings_live_debug" if browser_reload else "request_hub.settings",
            DJANGO_DEBUG="True",
            DJANGO_SECRET_KEY=signing_key or secrets.token_urlsafe(48),
            DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost",
            DJANGO_PRIMARY_DOMAIN="",
            DJANGO_PUBLIC_BASE_URL="http://127.0.0.1:8000",
            PYTHON_DOTENV_DISABLED="1",
            AZURE_TELEMETRY_ENABLED="false",
            ACS_EMAIL_CONNECTION_STRING="",
            PHILDATA_CLIENT_SECRET="",
            SQR_OUTLOOK_CLIENT_SECRET="",
            SQR_OUTLOOK_CERT_PATH="",
            REDIS_LOOKUP_URL="",
            AI_ASSISTANT_NATIVE_ENABLED="false",
        )
        django.setup()
        try:
            check_schema(historical_migrations)
        finally:
            connection.close()
    except LiveDebugError as error:
        raise SystemExit(f"Live Azure: {error}") from None
    except (DatabaseError, ImproperlyConfigured, OSError):
        raise SystemExit("Live Azure: preflight could not connect or inspect the schema. Check the database settings, TLS certificate, and network access.") from None
    except (migration_errors.InconsistentMigrationHistory, migration_errors.BadMigrationError,
            migration_errors.NodeNotFoundError, migration_errors.CircularDependencyError):
        raise SystemExit("Live Azure: migration history does not match this checkout. No migrations were applied.") from None
    if is_reload_child:
        print("Live Azure: schema check passed. Sign-in and other localhost actions write to production.", flush=True)
        if pinned_release:
            print("Live Azure: running a pinned release; saves in the workspace will not appear here.", flush=True)
        elif not browser_reload:
            print("Live Azure: install requirements-live-preview.txt to enable browser refresh.", flush=True)
    call_command("runserver", "127.0.0.1:8000")


if __name__ == "__main__":
    main()
