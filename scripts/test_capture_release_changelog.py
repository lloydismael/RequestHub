import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from capture_release_changelog import (
    ROOT,
    CaptureError,
    _render,
    _screenshot,
    _select_recipe,
    _snapshot,
    capture_manifest,
    load_recipes,
    main,
    prepare_assets,
)
from PIL import Image

from hub.release_changelog import build_manifest


def git(repo: Path | str, *args: str) -> str:
    """Run Git with a fixture identity in the synthetic capture repository."""
    result = subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        check=False,
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        git(self.repo, "init", "-b", "main")
        (self.repo / "docs").mkdir()
        self.document = self.repo / "docs" / "guide.md"
        self.document.write_text("# Export\n\nExport requires a manual request.\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "Baseline")
        self.before = git(self.repo, "rev-parse", "HEAD")
        self.document.write_text("# Export\n\nExport is available directly from the dashboard.\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "docs: improve export guide")
        self.after = git(self.repo, "rev-parse", "HEAD")
        self.manifest = build_manifest("v1", "HEAD", self.repo, baseline=self.before)
        self.recipes_path = self.repo / "recipes.json"
        self.recipe = {
            "id": "export-guide", "paths": ["docs/guide.md"],
            "scenes": [{
                "kind": "document", "path": "docs/guide.md", "heading": "Export",
                "title": "Export guide", "before_text": "Export required a manual request.",
                "after_text": "The guide describes dashboard export.",
            }],
        }
        self.write_recipes([self.recipe])

    def write_recipes(self, recipes):
        self.recipes_path.write_text(json.dumps({"schema": 1, "recipes": recipes}), encoding="utf-8")

    def write_manifest(self, directory, data):
        destination = self.repo / "hub" / "changelog_assets" / directory
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    def test_real_before_after_document_screenshots_have_exact_provenance(self):
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        item = result["commits"][0]
        self.assertNotIn("capture_error", item)
        self.assertTrue(item["requires_comparison"])
        pair = item["comparisons"][0]
        self.assertEqual(pair["before_revision"], self.before)
        self.assertEqual(pair["after_revision"], self.after)
        images = [self.repo / "hub" / "changelog_assets" / pair[field] for field in ("before_image", "after_image")]
        self.assertNotEqual(images[0].read_bytes(), images[1].read_bytes())
        for path in images:
            self.assertLessEqual(path.stat().st_size, 5 * 1024 * 1024)
            with Image.open(path) as image:
                self.assertGreaterEqual(image.width, 320)
                image.verify()
        repeated, failures = capture_manifest(result, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        self.assertEqual(repeated, result)

    def test_mixed_change_selects_each_affected_screen_without_matching_docs(self):
        for name in ("templates/profile.html", "templates/login.html"):
            path = self.repo / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("<h1>Updated screen</h1>", encoding="utf-8")
        self.document.write_text("# Export\n\nUpdated guidance.\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "feat: update screens and guidance")
        item = build_manifest("v2", "HEAD", self.repo, baseline=self.after)["commits"][0]
        screen = {**self.recipe["scenes"][0], "kind": "ui"}
        recipes = [self.recipe, *[
            {"id": fixture, "paths": [path], "scenes": [{**screen, "fixture": fixture, "title": fixture}]}
            for fixture, path in (("login", "templates/login.html"), ("profile", "templates/profile.html"))
        ]]
        chosen = _select_recipe(self.repo, item, recipes)
        self.assertEqual([scene["fixture"] for scene in chosen["scenes"]], ["login", "profile"])

    def test_unmapped_template_needs_a_screen_capture_instead_of_a_file_report(self):
        (self.repo / "templates").mkdir()
        (self.repo / "templates" / "export.html").write_text("<h1>Export</h1>", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "feat: add export screen")
        manifest = build_manifest("v2", "HEAD", self.repo, baseline=self.after)
        result, failures = capture_manifest(manifest, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertIn("UI capture recipe", result["commits"][0]["capture_error"])
        self.assertFalse(result["commits"][0].get("comparisons"))

    def test_unmapped_change_captures_exact_revision_report(self):
        self.write_recipes([])
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        self.assertEqual(len(result["commits"]), 1)
        item = result["commits"][0]
        self.assertNotIn("capture_error", item)
        pair = item["comparisons"][0]
        self.assertEqual(pair["before_revision"], self.before)
        self.assertEqual(pair["after_revision"], self.after)
        self.assertIn("report", pair["title"].lower())
        self.assertIn("not a UI screenshot", pair["capture_note"])
        assets = self.repo / "hub" / "changelog_assets"
        self.assertNotEqual((assets / pair["before_image"]).read_bytes(), (assets / pair["after_image"]).read_bytes())
        repeated, failures = capture_manifest(result, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        self.assertEqual(repeated, result)

    def test_repository_report_shows_snapshot_metadata_without_source_content(self):
        snapshot = self.repo / "snapshot"
        snapshot.mkdir()
        _snapshot(self.repo, self.after, snapshot)
        document = _render(snapshot, {
            "kind": "report", "fixture": "repository_change", "paths": ["docs/guide.md"],
            "revision": self.after,
        })
        self.assertIn("Repository change report", document)
        self.assertIn("docs/guide.md", document)
        self.assertIn(self.after, document)
        self.assertIn("Present", document)
        self.assertNotIn("Export is available directly from the dashboard", document)

    def test_mixed_changes_get_reports_even_without_a_comprehensive_recipe(self):
        (self.repo / "worker.py").write_text("SOURCE_CONTENT_MUST_NOT_BE_PUBLISHED = True\n", encoding="utf-8")
        self.document.write_text("# Export\n\nThe backend now supports export.\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "feat: connect export backend and guide")
        after = git(self.repo, "rev-parse", "HEAD")
        data = build_manifest("v2", "HEAD", self.repo, baseline=self.after)
        result, failures = capture_manifest(data, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        pair = result["commits"][0]["comparisons"][0]
        self.assertEqual(pair["before_revision"], self.after)
        self.assertEqual(pair["after_revision"], after)
        snapshot = self.repo / "mixed-snapshot"
        snapshot.mkdir()
        _snapshot(self.repo, after, snapshot)
        document = _render(snapshot, {
            "kind": "report", "fixture": "repository_change",
            "paths": ["docs/guide.md", "worker.py"], "revision": after,
        })
        self.assertIn("worker.py", document)
        self.assertIn("docs/guide.md", document)
        self.assertNotIn("SOURCE_CONTENT_MUST_NOT_BE_PUBLISHED", document)

    def test_report_fallback_excludes_private_configuration(self):
        (self.repo / ".env").write_text("FICTIONAL_PRIVATE_CONFIGURATION=not-a-credential\n", encoding="utf-8")
        self.document.write_text("# Export\n\nPublic guide update.\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "chore: update guide and private configuration")
        after = git(self.repo, "rev-parse", "HEAD")
        data = build_manifest("v2", "HEAD", self.repo, baseline=self.after)
        scene = _select_recipe(self.repo, data["commits"][0], [self.recipe])["scenes"][0]
        snapshot = self.repo / "private-snapshot"
        snapshot.mkdir()
        _snapshot(self.repo, after, snapshot)
        document = _render(snapshot, {**scene, "revision": after})
        self.assertIn("docs/guide.md", document)
        self.assertIn("Private or unsafe files excluded: 1", document)
        self.assertNotIn(".env", document)
        self.assertNotIn("FICTIONAL_PRIVATE_CONFIGURATION", document)
        self.assertNotIn("Public guide update", document)

    def test_report_fallback_tracks_renamed_files_and_escapes_file_names(self):
        renamed = "notes & status.txt"
        self.document.rename(self.repo / renamed)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "docs: rename export guide")
        after = git(self.repo, "rev-parse", "HEAD")
        data = build_manifest("v2", "HEAD", self.repo, baseline=self.after)
        scene = _select_recipe(self.repo, data["commits"][0], [])["scenes"][0]
        for revision in (self.after, after):
            with self.subTest(revision=revision):
                snapshot = self.repo / revision
                snapshot.mkdir()
                _snapshot(self.repo, revision, snapshot)
                document = _render(snapshot, {**scene, "revision": revision})
                self.assertIn("docs/guide.md", document)
                self.assertIn("notes &amp; status.txt", document)
                self.assertIn("Present", document)
                self.assertIn("Absent", document)

    def test_report_renderer_rejects_private_paths(self):
        scene = {"kind": "report", "fixture": "repository_change", "paths": [".env"], "revision": self.after}
        with self.assertRaises(CaptureError):
            _render(self.repo, scene)

    def test_repository_report_cannot_be_declared_with_unverified_file_paths(self):
        scene = {
            "kind": "report", "fixture": "repository_change", "paths": ["unchanged.py"],
            "title": "Unverified report", "before_text": "Before", "after_text": "After",
        }
        self.write_recipes([{"id": "unverified-report", "commit": self.after, "scenes": [scene]}])
        with self.assertRaises(CaptureError):
            load_recipes(self.recipes_path)

    def test_ambiguous_recipe_does_not_choose_arbitrary_evidence(self):
        duplicate = {**self.recipe, "id": "other-export-guide"}
        self.write_recipes([self.recipe, duplicate])
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertIn("ambiguous", result["commits"][0]["capture_error"].lower())
        self.assertFalse(result["commits"][0].get("comparisons"))

    def test_private_paths_and_unsupported_scene_kinds_are_rejected(self):
        for scene in (
            {**self.recipe["scenes"][0], "path": "../private.md"},
            {**self.recipe["scenes"][0], "path": ".env"},
            {**self.recipe["scenes"][0], "path": "docs/./guide.md"},
            {**self.recipe["scenes"][0], "kind": "shell"},
        ):
            with self.subTest(scene=scene):
                self.write_recipes([{**self.recipe, "scenes": [scene]}])
                with self.assertRaises(CaptureError):
                    load_recipes(self.recipes_path)

    def test_unchanged_selected_section_is_not_successful_change_evidence(self):
        self.recipe["scenes"][0]["heading"] = "Unrelated absent heading"
        self.write_recipes([self.recipe])
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertIn("observable", result["commits"][0]["capture_error"].lower())
        self.assertFalse(result["commits"][0].get("comparisons"))

    def test_missing_cached_image_is_recaptured_with_verified_report(self):
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        self.write_manifest("automatic", result)
        self.write_manifest("v56.6", self.manifest)
        result = build_manifest("v2", "HEAD", self.repo, baseline=self.before)
        pair = result["commits"][0]["comparisons"][0]
        (self.repo / "hub" / "changelog_assets" / pair["after_image"]).unlink()
        self.write_recipes([])
        repeated, failures = capture_manifest(result, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        recovered = repeated["commits"][0]["comparisons"][0]
        self.assertNotEqual(recovered["after_image"], pair["after_image"])
        self.assertTrue((self.repo / "hub" / "changelog_assets" / recovered["after_image"]).is_file())
        self.assertEqual(recovered["before_revision"], self.before)
        self.assertEqual(recovered["after_revision"], self.after)
        self.assertNotIn("capture_error", repeated["commits"][0])

    def test_browser_failure_replaces_previous_error_and_has_nonzero_failure_count(self):
        from playwright.sync_api import Error
        self.manifest["commits"][0]["capture_error"] = "Previous capture needs retry."
        with patch("playwright.sync_api.sync_playwright") as start:
            start.return_value.__enter__.return_value.chromium.launch.side_effect = Error("browser missing")
            result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertIn("browser unavailable", result["commits"][0]["capture_error"].lower())
        self.assertFalse(result["commits"][0].get("comparisons"))

    def test_invalid_configuration_is_persisted_as_visible_capture_failure(self):
        self.recipes_path.write_text("{broken", encoding="utf-8")
        manifest_path = self.repo / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        status = main(["--manifest", str(manifest_path), "--repo", str(self.repo), "--recipes", str(self.recipes_path)])
        self.assertEqual(status, 1)
        result = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("configuration", result["commits"][0]["capture_error"].lower())
        self.assertTrue(result["commits"][0]["requires_comparison"])

    def test_setup_failure_can_be_recorded_without_attempting_browser_launch(self):
        manifest_path = self.repo / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        with patch("playwright.sync_api.sync_playwright") as start:
            status = main(["--manifest", str(manifest_path), "--repo", str(self.repo),
                           "--failure", "Capture setup failed; retry the release capture."])
        self.assertEqual(status, 1)
        start.assert_not_called()
        result = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("setup failed", result["commits"][0]["capture_error"])

    def test_changed_recipe_invalidates_automatic_cached_evidence(self):
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        self.write_manifest("automatic", result)
        self.write_manifest("v56.6", self.manifest)
        result = build_manifest("v2", "HEAD", self.repo, baseline=self.before)
        self.recipe["scenes"][0]["heading"] = "Unrelated absent heading"
        self.write_recipes([self.recipe])
        repeated, failures = capture_manifest(result, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertFalse(repeated["commits"][0].get("comparisons"))

    def test_rebuilt_manifest_reuses_cache_with_overlapping_curated_metadata(self):
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        assets = self.repo / "hub" / "changelog_assets"
        (assets / "automatic" / "manifest.json").write_text(json.dumps(result), encoding="utf-8")
        curated = {**result, "commits": [{
            "sha": self.after, "type": "maintenance", "title": "Curated guide",
            "before_text": "Manual export", "after_text": "Dashboard export",
        }]}
        (assets / "v56.6").mkdir()
        (assets / "v56.6" / "manifest.json").write_text(json.dumps(curated), encoding="utf-8")
        rebuilt = build_manifest("v2", "HEAD", self.repo, baseline=self.before)
        with patch("capture_release_changelog._capture_change") as capture:
            repeated, failures = capture_manifest(rebuilt, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        capture.assert_not_called()
        self.assertEqual(repeated["commits"][0]["comparisons"], result["commits"][0]["comparisons"])
        self.assertEqual(repeated["commits"][0]["title"], "Curated guide")

        self.recipe["scenes"][0]["heading"] = "Unrelated absent heading"
        self.write_recipes([self.recipe])
        repeated, failures = capture_manifest(rebuilt, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertFalse(repeated["commits"][0].get("comparisons"))

    def test_partial_scene_failure_does_not_keep_a_successful_pair(self):
        self.recipe["scenes"].append({**self.recipe["scenes"][0], "heading": "Missing section"})
        self.write_recipes([self.recipe])
        from capture_release_changelog import _screenshot as screenshot
        calls = 0

        def failing_scene(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise CaptureError("Second scene failed.")
            return screenshot(*args)

        with patch("capture_release_changelog._screenshot", side_effect=failing_scene):
            result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertFalse(result["commits"][0].get("comparisons"))
        self.assertEqual(list((self.repo / "hub" / "changelog_assets").rglob("*.png")), [])

    def test_backend_report_captures_observed_historical_behavior(self):
        (self.repo / "hub").mkdir()
        module = ROOT / "hub" / "release_changelog.py"
        source = module.read_text(encoding="utf-8")
        (self.repo / "hub" / "release_changelog.py").write_text(source, encoding="utf-8")
        git(self.repo, "add", "hub")
        git(self.repo, "commit", "-m", "Introduce contract")
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "hub" / "release_changelog.py").write_text(
            source + "\n_original_classify = classify\n"
            "def classify(subject):\n"
            "    return ('improved', 'Synthetic guide') if subject.startswith('docs:') else _original_classify(subject)\n",
            encoding="utf-8",
        )
        git(self.repo, "add", "hub")
        git(self.repo, "commit", "-m", "fix: release contract")
        manifest = build_manifest("v2", "HEAD", self.repo, baseline=before)
        scene = {key: value for key, value in self.recipe["scenes"][0].items() if key not in {"path", "heading"}}
        self.write_recipes([{"id": "contract", "paths": ["hub/release_changelog.py"],
                             "scenes": [{**scene, "kind": "report", "fixture": "changelog_contract"}]}])
        result, failures = capture_manifest(manifest, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        self.assertEqual(result["commits"][0]["comparisons"][0]["before_revision"], before)

    def test_snapshot_excludes_private_configuration_and_git_symlinks(self):
        (self.repo / ".env").write_text("FICTIONAL_TEST_DATA=not-a-credential", encoding="utf-8")
        git(self.repo, "add", ".env")
        result = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
                                input=b"../outside.md", capture_output=True, check=True)
        git(self.repo, "update-index", "--add", "--cacheinfo",
            f"120000,{result.stdout.decode('ascii').strip()},alias.md")
        git(self.repo, "commit", "-m", "Synthetic excluded files")
        snapshot = self.repo / "snapshot"
        snapshot.mkdir()
        _snapshot(self.repo, "HEAD", snapshot)
        self.assertFalse((snapshot / ".env").exists())
        self.assertFalse((snapshot / "alias.md").exists())
        self.assertTrue((snapshot / "docs" / "guide.md").is_file())

    def test_browser_external_resources_are_blocked_before_screenshot(self) -> None:
        """Refuse a screenshot when the offline browser sees an external resource."""
        from playwright.sync_api import sync_playwright
        output = self.repo / "blocked.png"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                with (
                    patch("capture_release_changelog._render", return_value="<html><body><img src='https://external.invalid/image.png'></body></html>"),
                    self.assertRaisesRegex(CaptureError, "external traffic"),
                ):
                    _screenshot(browser, self.repo, self.recipe["scenes"][0], output, self.repo)
            finally:
                browser.close()
        self.assertFalse(output.exists())

    def test_vendor_integrity_failure_does_not_write_unverified_asset(self) -> None:
        """Do not save evidence if a vendor asset fails its checksum."""
        vendor = self.repo / "vendor"
        with (
            patch("capture_release_changelog.urllib.request.urlopen", return_value=BytesIO(b"incorrect asset")),
            self.assertRaisesRegex(CaptureError, "integrity"),
        ):
            prepare_assets(vendor)
        self.assertEqual(list(vendor.iterdir()), [])

    def test_dependency_lock_changes_invalidate_capture_fingerprint(self):
        from capture_release_changelog import _fingerprint
        before = _fingerprint(self.recipe)
        read_bytes = Path.read_bytes

        def changed_lock(path):
            data = read_bytes(path)
            return data + b"\nfixture dependency change" if path.name == "requirements.lock" else data

        with patch.object(Path, "read_bytes", changed_lock):
            self.assertNotEqual(before, _fingerprint(self.recipe))

    def test_changed_document_does_not_substitute_for_an_unchanged_ui_scene(self):
        visual = {**self.recipe["scenes"][0], "kind": "ui", "fixture": "login", "title": "Sign-in screen"}
        self.recipe["scenes"].append(visual)
        self.write_recipes([self.recipe])
        with patch("capture_release_changelog._render", wraps=_render) as render:
            render.side_effect = lambda snapshot, scene: (
                "<body><div class='login-hero'>Same UI</div></body>" if scene["kind"] == "ui" else _render(snapshot, scene)
            )
            result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(len(failures), 1)
        self.assertFalse(result["commits"][0].get("comparisons"))

    def test_one_changed_scene_can_include_unchanged_companion_context(self):
        self.recipe["scenes"].append({**self.recipe["scenes"][0], "heading": "Absent companion section"})
        self.write_recipes([self.recipe])
        result, failures = capture_manifest(self.manifest, self.repo, self.recipes_path)
        self.assertEqual(failures, [])
        pairs = result["commits"][0]["comparisons"]
        self.assertEqual(len(pairs), 2)
        root = self.repo / "hub" / "changelog_assets"
        self.assertEqual((root / pairs[1]["before_image"]).read_bytes(), (root / pairs[1]["after_image"]).read_bytes())


class HistoricalSceneTests(unittest.TestCase):
    def test_ui_scene_explicitly_selects_accounts_config_without_database(self) -> None:
        """Render an account fixture using explicit app configuration and no live database."""
        result = subprocess.run(
            [sys.executable, "-c", (
                "import sys; from pathlib import Path; "
                "from changelog_capture_scenes import _ui; "
                "_ui(Path(sys.argv[1]), 'login'); "
                "from django.apps import apps; from django.conf import settings; "
                "config = apps.get_app_config('accounts'); "
                "assert config.__class__.__name__ == 'AccountsConfig'; "
                "assert config.get_model('User')._meta.app_config is config; "
                "assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.dummy'"
            ), str(ROOT)],
            check=False,
            cwd=ROOT / "scripts", capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_login_scene_uses_the_seasonal_default_without_browser_storage(self):
        document = _render(ROOT, {"kind": "ui", "fixture": "login"})
        root = re.search(r"<html\b[^>]*>", document).group(0)

        self.assertIn('class="login-christmas"', root)
        self.assertIn("<title>Login &middot; Request Hub</title>", document)

    def test_changelog_loading_recipe_renders_both_disclosure_fallbacks(self):
        recipe = next(
            (recipe for recipe in load_recipes(ROOT / "hub" / "changelog_capture_recipes.json")
             if recipe["id"] == "changelog-loading"),
            None,
        )
        self.assertIsNotNone(recipe, "Deferred changelog previews need a usable visual capture recipe")
        for fixture, fallback in (("changelog_history", "Open preview"), ("changelog_feed", "View full report")):
            with self.subTest(fixture=fixture):
                scene = next(scene for scene in recipe["scenes"] if scene["fixture"] == fixture)
                self.assertIn(fallback, _render(ROOT, scene))

    def test_admin_profile_recipe_renders_personalization_without_scripts(self):
        recipes = load_recipes(ROOT / 'hub' / 'changelog_capture_recipes.json')
        recipe = next((recipe for recipe in recipes if recipe['id'] == 'admin-profile-tools'), None)
        self.assertIsNotNone(recipe, 'Admin profile changes need desktop and mobile evidence')
        scene = recipe['scenes'][0]
        html = _render(ROOT, scene)
        self.assertIn('class="profile-panel profile-panel--active" id="profile-panel-settings"', html)
        self.assertIn('id="sqr-classic-visibility"', html)
        self.assertIn('id="profile-panel-backup"', html)
        self.assertIn('Download latest', html)

    def test_admin_capture_keeps_sticky_navigation_outside_the_panel(self):
        from playwright.sync_api import sync_playwright

        document = """<style>
            body { margin: 0; }
            .navbar { position: sticky; top: 0; height: 60px; background: red; }
            .settings-admin-wrap { height: 1800px; background: white; }
            </style><nav class="navbar"></nav><main class="settings-admin-wrap"></main>"""
        scene = {"kind": "ui", "fixture": "admin_profile", "viewport": {"width": 375, "height": 844}}
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
            directory = Path(temporary)
            output = directory / "admin-profile.png"
            browser = playwright.chromium.launch()
            try:
                with patch("capture_release_changelog._render", return_value=document):
                    _screenshot(browser, directory, scene, output, directory)
            finally:
                browser.close()
            with Image.open(output) as image:
                self.assertEqual([color for _, color in image.convert("RGB").getcolors()], [(255, 255, 255)])

<<<<<<< HEAD
    def test_dark_request_progress_text_blends_into_card_and_stays_readable(self):
        from playwright.sync_api import sync_playwright

        document = _render(ROOT, {"kind": "ui", "fixture": "request_progress"})
        styles = ROOT / "static" / "css"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(java_script_enabled=False)

            def load_styles(route):
                if match := re.fullmatch(r"http://fixture\.invalid/static/css/([\w-]+\.css)(?:\?.*)?", route.request.url):
                    path = styles / match[1]
                    if path.is_file():
                        route.fulfill(path=path, content_type="text/css")
                        return
                route.abort()

            page.route("**/*", load_styles)
            page.set_content(document.replace("<head>", '<head><base href="http://fixture.invalid/">', 1))
            steps = page.get_by_role("list", name="Request lifecycle stages")
            self.assertEqual(steps.get_by_role("listitem").count(), 5)
            for width in (1200, 375):
                for theme in ("christmas", "default"):
                    with self.subTest(width=width, theme=theme):
                        page.set_viewport_size({"width": width, "height": 844})
                        boxes = steps.evaluate("""(list, theme) => {
                            const body = list.closest('body');
                            body.dataset.accountTheme = theme;
                            body.classList.toggle('account-themed', theme !== 'default');
                            const canvas = body.ownerDocument.createElement('canvas').getContext('2d');
                            function rgb(color, background) {
                                canvas.clearRect(0, 0, 1, 1);
                                if (background) {
                                    canvas.fillStyle = background;
                                    canvas.fillRect(0, 0, 1, 1);
                                }
                                canvas.fillStyle = color;
                                canvas.fillRect(0, 0, 1, 1);
                                return canvas.getImageData(0, 0, 1, 1).data;
                            }
                            function luminance([r, g, b]) {
                                const linear = value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
                                return 0.2126 * linear(r / 255) + 0.7152 * linear(g / 255) + 0.0722 * linear(b / 255);
                            }
                            const card = getComputedStyle(list.closest('.rmf-progress-card'));
                            const lightestCard = luminance(rgb(card.backgroundColor, '#fff'));
                            return Array.from(list.querySelectorAll('.rmf-progress__content'), box => ({
                                alpha: rgb(getComputedStyle(box).backgroundColor)[3],
                                text: Array.from(box.children, element => ({
                                    label: element.textContent,
                                    contrast: (luminance(rgb(getComputedStyle(element).color)) + 0.05) / (lightestCard + 0.05),
                                })),
                            }));
                        }""", theme)
                        for box in boxes:
                            self.assertEqual(box["alpha"], 0, "Step text must not have a separate background")
                            for text in box["text"]:
                                self.assertGreaterEqual(text["contrast"], 4.5, text["label"])
            browser.close()

=======
>>>>>>> 1a245b3068f52b9911c1d14f302d675a6a8dbbf1
    def test_accounts_recipe_shows_server_search_without_scripts_or_a_database(self):
        recipes = load_recipes(ROOT / "hub" / "changelog_capture_recipes.json")
        recipe = next((recipe for recipe in recipes if recipe["id"] == "management-accounts"), None)
        self.assertIsNotNone(recipe, "Accounts changes need a before/after screen capture")
        document = _render(ROOT, recipe["scenes"][0])
        self.assertIn('value="Summit Research"', document)
        self.assertIn("Showing 1–1 of 1", document)
        self.assertNotIn('value="Demo Account 001"', document)

    def test_monitoring_recipe_shows_the_actual_management_shell(self):
        path = ROOT / "hub" / "changelog_capture_recipes.json"
        recipe = next((recipe for recipe in load_recipes(path) if recipe["id"] == "management-monitoring"), None)
        self.assertIsNotNone(recipe)
        scene = recipe["scenes"][0]
        before = _render(ROOT, {**scene, "fixture": "changelog_history"})
        after = _render(ROOT, scene)
        self.assertNotIn('id="monitoring-pane"', before)
        self.assertIn('id="monitoring-pane"', after)
        self.assertIn('id="monitoring-tab"', after)
        self.assertIn("Database server utilization", after)
        self.assertIn("Waiting for live data", after)

    @unittest.skipUnless(os.environ.get("CHANGELOG_CAPTURE_VENDOR_DIR"), "Prepare offline vendor assets to exercise UI scenes.")
    def test_historical_ui_fixtures_render_without_application_database(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot"
            snapshot.mkdir()
            _snapshot(ROOT, "HEAD", snapshot)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for fixture in (
                        "login", "seasonal_login", "profile", "changelog", "changelog_editor",
                        "changelog_history", "changelog_feed", "sqr_tracker", "sqr_performance",
                    ):
                        with self.subTest(fixture=fixture):
                            output = Path(temporary) / f"{fixture}.png"
                            _screenshot(browser, snapshot, {"kind": "ui", "fixture": fixture}, output,
                                        Path(os.environ["CHANGELOG_CAPTURE_VENDOR_DIR"]))
                            with Image.open(output) as image:
                                self.assertGreaterEqual(image.width, 320)
                                image.verify()
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main()
