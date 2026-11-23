import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.db import OperationalError

from request_hub import live_debug


class LiveDebugTests(TestCase):
    def setUp(self):
        self.launcher = live_debug
        temporary = self.enterContext(TemporaryDirectory())
        self.env_file = Path(temporary) / ".env"
        self.snapshot_root = Path(temporary) / "request-hub-f5"
        self.enterContext(patch.object(self.launcher, "SNAPSHOT_ROOT", self.snapshot_root, create=True))
        self.enterContext(patch.object(sys, "path", sys.path.copy()))
        self.env_file.write_text(
            "DB_HOST=example.postgres.database.azure.com\nDB_NAME=example\nDB_USER=example\n"
            "DB_PASSWORD='example $password'\nDB_SSLMODE=require\n"
            "DJANGO_SECRET_KEY=deployment-key\nACS_EMAIL_CONNECTION_STRING=deployment-mail\n",
            encoding="utf-8",
        )

    def test_uses_azure_credentials_without_importing_deployment_config(self):
        env = self.launcher.database_environment(self.env_file)
        self.assertEqual(env["DB_HOST"], "example.postgres.database.azure.com")
        self.assertEqual(env["DB_PASSWORD"], "example $password")
        self.assertEqual(env["DB_SSLMODE"], "verify-full")
        self.assertTrue(Path(env["DB_SSLROOTCERT"]).is_file())
        self.assertNotIn("DJANGO_SECRET_KEY", env)
        self.assertNotIn("ACS_EMAIL_CONNECTION_STRING", env)

    def test_wrong_target_fails_before_django_can_connect(self):
        config = self.env_file.read_text(encoding="utf-8").replace(
            "example.postgres.database.azure.com", "127.0.0.1"
        )
        self.env_file.write_text(config, encoding="utf-8")
        with (
            patch.object(self.launcher, "ENV_FILE", self.env_file),
            patch.object(self.launcher.django, "setup") as setup,
            self.assertRaisesRegex(SystemExit, "Azure PostgreSQL.*DB_HOST"),
        ):
            self.launcher.main()
        setup.assert_not_called()

    def test_pending_migration_blocks_launch_without_applying_it(self):
        migration = SimpleNamespace(app_label="accounts", name="0032_example")
        with (
            patch.object(self.launcher, "ENV_FILE", self.env_file),
            patch.dict(os.environ, clear=False),
            patch.object(self.launcher.django, "setup"),
            patch.object(self.launcher, "connection", MagicMock()) as connection,
            patch.object(self.launcher.transaction, "atomic"),
            patch.object(self.launcher, "MigrationExecutor") as executor_class,
            patch.object(self.launcher, "call_command") as run,
        ):
            executor = executor_class.return_value
            executor.loader.applied_migrations = {}
            executor.loader.detect_conflicts.return_value = {}
            executor.migration_plan.return_value = [(migration, False)]
            with self.assertRaisesRegex(SystemExit, r"accounts\.0032_example"):
                self.launcher.main()
            cursor = connection.cursor.return_value.__enter__.return_value
            self.assertEqual(cursor.execute.call_args_list[0].args, ("SET TRANSACTION READ ONLY",))
            executor.migrate.assert_not_called()
            run.assert_not_called()
            connection.close.assert_called_once()

    def test_matching_history_does_not_allow_missing_columns(self):
        model = SimpleNamespace(_meta=SimpleNamespace(
            db_table="accounts_user", can_migrate=lambda connection: True,
            local_concrete_fields=[SimpleNamespace(column="id"), SimpleNamespace(column="new_preference")],
        ))
        with (
            patch.object(self.launcher, "connection", MagicMock()) as connection,
            patch.object(self.launcher.transaction, "atomic"),
            patch.object(self.launcher, "MigrationExecutor") as executor_class,
            patch.object(self.launcher.apps, "get_models", return_value=[model]),
        ):
            executor = executor_class.return_value
            executor.loader.applied_migrations = {}
            executor.loader.detect_conflicts.return_value = {}
            executor.migration_plan.return_value = []
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [("accounts_user", "id")]
            with self.assertRaisesRegex(self.launcher.LiveDebugError, r"accounts_user\.new_preference"):
                self.launcher.check_schema()

    def test_configured_release_snapshot_runs_instead_of_working_tree(self):
        revision = "a" * 40
        root = self.env_file.parent / "request-hub-f5"
        snapshot = root / revision
        (snapshot / "request_hub").mkdir(parents=True)
        (snapshot / "request_hub" / "settings.py").touch()
        (root / "release.json").write_text(json.dumps({
            "revision": revision, "historical_migrations": ["hub.0014_retired"],
        }), encoding="utf-8")
        with (
            patch.object(self.launcher, "ENV_FILE", self.env_file),
            patch.object(self.launcher, "SNAPSHOT_ROOT", root),
            patch.dict(os.environ, clear=True),
            patch.object(sys, "path", sys.path.copy()),
            patch.dict(sys.modules),
            patch.object(self.launcher.django, "setup"),
            patch.object(self.launcher, "connection", MagicMock()),
            patch.object(self.launcher, "check_schema") as check,
            patch.object(self.launcher, "call_command") as run,
        ):
            sys.modules.pop("request_hub", None)
            self.launcher.main()
            self.assertEqual(Path(sys.path[0]), snapshot)
            self.assertEqual(os.environ["APP_REVISION"], revision)
            self.assertEqual(os.environ["DB_HOST"], "example.postgres.database.azure.com")
            self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "request_hub.settings")
        check.assert_called_once_with(frozenset({("hub", "0014_retired")}))
        run.assert_called_once_with("runserver", "127.0.0.1:8000")

    def test_invalid_snapshot_fails_without_falling_back_to_working_tree(self):
        root = self.env_file.parent / "request-hub-f5"
        root.mkdir()
        for revision in ("../outside", "a" * 40):
            with self.subTest(revision=revision):
                (root / "release.json").write_text(json.dumps({"revision": revision}), encoding="utf-8")
                with (
                    patch.object(self.launcher, "ENV_FILE", self.env_file),
                    patch.object(self.launcher, "SNAPSHOT_ROOT", root, create=True),
                    patch.dict(os.environ, clear=True),
                    patch.object(self.launcher.django, "setup") as setup,
                    patch.object(self.launcher, "connection", MagicMock()),
                    patch.object(self.launcher, "check_schema"),
                    patch.object(self.launcher, "call_command"),
                    self.assertRaisesRegex(SystemExit, "snapshot"),
                ):
                    self.launcher.main()
                setup.assert_not_called()

    def test_snapshot_history_allows_only_recorded_rows_and_still_checks_columns(self):
        historic = ("hub", "0014_retired")
        model = SimpleNamespace(_meta=SimpleNamespace(
            db_table="hub_request", can_migrate=lambda connection: True,
            local_concrete_fields=[SimpleNamespace(column="id")],
        ))
        with (
            patch.object(self.launcher, "connection", MagicMock()) as connection,
            patch.object(self.launcher.transaction, "atomic"),
            patch.object(self.launcher, "MigrationExecutor") as executor_class,
            patch.object(self.launcher.apps, "get_models", return_value=[model]),
        ):
            executor = executor_class.return_value
            executor.loader.applied_migrations = {historic: None}
            executor.loader.disk_migrations = {}
            executor.loader.graph.leaf_nodes.return_value = [("hub", "0020_current")]
            executor.loader.detect_conflicts.return_value = {}
            executor.migration_plan.return_value = []
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [("hub_request", "id")]

            self.launcher.check_schema(frozenset({historic}))
            with self.assertRaisesRegex(self.launcher.LiveDebugError, "migrations not in this checkout"):
                self.launcher.check_schema()
            with patch.dict(executor.loader.applied_migrations, {("hub", "0021_newer"): None}):
                with self.assertRaisesRegex(self.launcher.LiveDebugError, "migrations not in this checkout"):
                    self.launcher.check_schema(frozenset({historic}))
            cursor.fetchall.return_value = []
            with self.assertRaisesRegex(self.launcher.LiveDebugError, r"hub_request\.id"):
                self.launcher.check_schema(frozenset({historic}))

    def test_database_failure_does_not_print_credentials(self):
        with (
            patch.object(self.launcher, "ENV_FILE", self.env_file),
            patch.dict(os.environ, clear=False),
            patch.object(self.launcher.django, "setup"),
            patch.object(self.launcher, "connection", MagicMock()),
            patch.object(self.launcher, "check_schema", side_effect=OperationalError("sensitive connection detail")),
            patch.object(self.launcher, "call_command") as run,
        ):
            with self.assertRaisesRegex(SystemExit, "preflight could not connect") as error:
                self.launcher.main()
            self.assertNotIn("sensitive connection detail", str(error.exception))
            run.assert_not_called()

    def test_verified_working_tree_uses_browser_reload_settings(self):
        with (
            patch.object(self.launcher, "ENV_FILE", self.env_file),
            patch.dict(os.environ, {"DB_HOST": "127.0.0.1", "DJANGO_SECRET_KEY": "deployment-key"}, clear=True),
            patch("importlib.util.find_spec", return_value=object()),
            patch.object(self.launcher.django, "setup"),
            patch.object(self.launcher, "connection", MagicMock()),
            patch.object(self.launcher, "check_schema"),
            patch.object(self.launcher, "call_command") as run,
            patch("builtins.print"),
        ):
            self.launcher.main()
            self.assertEqual(os.environ["DB_HOST"], "example.postgres.database.azure.com")
            self.assertEqual(os.environ["PYTHON_DOTENV_DISABLED"], "1")
            self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "request_hub.settings_live_debug")
            self.assertNotEqual(os.environ["DJANGO_SECRET_KEY"], "deployment-key")
        run.assert_called_once_with("runserver", "127.0.0.1:8000")

    def test_missing_optional_reload_package_keeps_the_server_usable(self):
        with (
            patch.object(self.launcher, "ENV_FILE", self.env_file),
            patch.dict(os.environ, {"RUN_MAIN": "true"}, clear=True),
            patch("importlib.util.find_spec", return_value=None),
            patch.object(self.launcher.django, "setup"),
            patch.object(self.launcher, "connection", MagicMock()),
            patch.object(self.launcher, "check_schema"),
            patch.object(self.launcher, "call_command") as run,
            patch("builtins.print") as report,
        ):
            self.launcher.main()
            self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "request_hub.settings")
        run.assert_called_once_with("runserver", "127.0.0.1:8000")
        self.assertIn("requirements-live-preview.txt", " ".join(call.args[0] for call in report.call_args_list))

    def test_reloader_child_keeps_the_parent_signing_key(self):
        with (
            patch.object(self.launcher, "ENV_FILE", self.env_file),
            patch.dict(os.environ, clear=True),
            patch.object(self.launcher.django, "setup"),
            patch.object(self.launcher, "connection", MagicMock()),
            patch.object(self.launcher, "check_schema"),
            patch.object(self.launcher, "call_command"),
            patch("builtins.print"),
        ):
            self.launcher.main()
            parent_key = os.environ["DJANGO_SECRET_KEY"]
            os.environ["RUN_MAIN"] = "true"
            self.launcher.main()
            self.assertEqual(os.environ["DJANGO_SECRET_KEY"], parent_key)
