import contextlib
import io
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import release_changelog
from .release_changelog import (
    ManifestError,
    build_manifest,
    classify,
    is_excluded,
    main,
    validate_manifest,
)

SHA_A = "a" * 40
SHA_B = "b" * 40


def git(repo, *args):
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.test", "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def commit(repo, subject, body=""):
    args = ["commit", "--allow-empty", "-m", subject]
    if body:
        args += ["-m", body]
    git(repo, *args)
    return git(repo, "rev-parse", "HEAD")


def valid_manifest(**overrides):
    data = {
        "version": "v55.1",
        "revision": SHA_A,
        "generated_at": "2026-11-04T10:00:00+00:00",
        "commits": [{"sha": SHA_B, "type": "new", "title": "Thing"}],
    }
    data.update(overrides)
    return data


class RepoMixin:
    def make_repo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        git(repo, "init", "-b", "main")
        return repo


class ClassifyTests(SimpleTestCase):
    def test_prefixes_and_fallbacks(self):
        cases = {
            "Feature - Changelogs": ("new", "Changelogs"),
            "feat: add export": ("new", "Add export"),
            "Fix - SQR email": ("fixed", "SQR email"),
            "fix(api)!: crash": ("fixed", "Crash"),
            "Docs - plan": ("maintenance", "Plan"),
            "build(deps): bump x": ("maintenance", "Bump x"),
            "Enhancement - Faster list": ("improved", "Faster list"),
            "Fixed typo in header": ("fixed", "Fixed typo in header"),
            "Bump django from 5.1 to 5.2": ("maintenance", "Bump django from 5.1 to 5.2"),
            "SQR - Email Approval Automation": ("improved", "SQR - Email Approval Automation"),
            "Auto-assign engineers": ("improved", "Auto-assign engineers"),
            "Fix -": ("fixed", "Fix -"),
            "feat:": ("new", "feat:"),
        }
        for subject, expected in cases.items():
            with self.subTest(subject=subject):
                self.assertEqual(classify(subject), expected)

    def test_long_subject_is_capped(self):
        kind, title = classify("x" * 250)
        self.assertEqual(kind, "improved")
        self.assertEqual(len(title), 200)
        self.assertTrue(title.endswith("…"))

    def test_exclusions(self):
        self.assertTrue(is_excluded("Initial plan", ""))
        self.assertTrue(is_excluded("initial PLAN", ""))
        self.assertTrue(is_excluded("Docs", "see [Skip Changelog]"))
        self.assertTrue(is_excluded("Docs [skip changelog]", ""))
        self.assertFalse(is_excluded("Initial plan for exports", ""))
        self.assertTrue(is_excluded("", ""))
        self.assertTrue(is_excluded("   ", "some body"))


class ValidateManifestTests(SimpleTestCase):
    def test_new_policy_cannot_disable_required_screenshots(self):
        data = valid_manifest(evidence_policy="all_changes")
        data["commits"][0].update(parent_sha="c" * 40, requires_comparison=False)
        result = validate_manifest(data)
        self.assertEqual(result.get("evidence_policy"), "all_changes")
        self.assertTrue(result["commits"][0].get("requires_comparison"))
        self.assertEqual(result["commits"][0].get("parent_sha"), "c" * 40)

    def test_capture_failure_is_preserved_for_a_recorded_draft(self):
        data = valid_manifest(evidence_policy="all_changes")
        data["commits"][0].update(parent_sha="c" * 40, capture_error="No capture recipe configured.")
        result = validate_manifest(data)
        self.assertEqual(result["commits"][0].get("capture_error"), "No capture recipe configured.")

    def test_new_policy_rejects_screenshots_from_unrelated_revisions(self):
        data = valid_manifest(evidence_policy="all_changes")
        data["commits"][0].update(parent_sha="c" * 40, comparisons=[{
            "title": "Change", "before_text": "Before", "after_text": "After",
            "before_image": "v1/before.png", "after_image": "v1/after.png",
            "before_revision": "d" * 40, "after_revision": SHA_B,
        }])
        with self.assertRaises(ManifestError):
            validate_manifest(data)

    def test_validate_manifest(self):
        data = valid_manifest(extra="x")
        data["commits"][0]["author"] = "someone"
        result = validate_manifest(data)
        self.assertEqual(list(result), ["version", "revision", "generated_at", "commits"])
        self.assertEqual(result["commits"], [{"sha": SHA_B, "type": "new", "title": "Thing"}])

        bad = [
            "not a dict",
            valid_manifest(version="v1 2"),
            valid_manifest(version="v" * 51),
            valid_manifest(revision="A" * 40),
            valid_manifest(revision="a" * 39),
            valid_manifest(generated_at="2026-11-04T10:00:00"),
            valid_manifest(generated_at="nonsense"),
            valid_manifest(commits=[{"sha": "B" * 40, "type": "new", "title": "T"}]),
            valid_manifest(commits=[{"sha": "b" * 39, "type": "new", "title": "T"}]),
            valid_manifest(commits=[{"sha": SHA_B, "type": "bogus", "title": "T"}]),
            valid_manifest(commits=[{"sha": SHA_B, "type": "new", "title": ""}]),
            valid_manifest(commits=[{"sha": SHA_B, "type": "new", "title": "t" * 201}]),
            valid_manifest(commits="nope"),
        ]
        for item in bad:
            with self.subTest(item=item):
                with self.assertRaises(ManifestError):
                    validate_manifest(item)

        commit_item = {"sha": SHA_B, "type": "new", "title": "T"}
        validate_manifest(valid_manifest(commits=[commit_item] * 2))
        self.assertEqual(len(validate_manifest(valid_manifest(commits=[commit_item] * 3))["commits"]), 3)


class BuildManifestTests(RepoMixin, SimpleTestCase):
    def test_every_change_requires_evidence_from_its_actual_parent(self):
        repo = self.make_repo()
        base = commit(repo, "Baseline")
        sha = commit(repo, "docs: update guide")
        result = build_manifest("v1", "HEAD", repo, baseline=base)
        self.assertEqual(result.get("evidence_policy"), "all_changes")
        self.assertEqual(result["commits"], [{
            "sha": sha, "parent_sha": base, "type": "maintenance",
            "title": "Update guide", "requires_comparison": True,
        }])

    def test_history_beyond_500_commits_is_not_silently_lost(self):
        repo = self.make_repo()
        base = commit(repo, "Baseline")
        records = []
        for index in range(501):
            subject = f"fix: change {index}"
            parent = base if index == 0 else f":{index}"
            records.append(
                f"commit refs/heads/main\nmark :{index + 1}\n"
                f"committer t <t@t.test> {1700000000 + index} +0000\n"
                f"data {len(subject)}\n{subject}\nfrom {parent}\n\n"
            )
        result = subprocess.run(
            ["git", "-C", str(repo), "fast-import", "--quiet"],
            input="".join(records).encode("utf-8"), capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = build_manifest("v1", "HEAD", repo, baseline=base)
        self.assertEqual(len(data["commits"]), 501)
        self.assertEqual(data["commits"][-1]["title"], "Change 0")

    def test_commits_since_baseline_newest_first(self):
        repo = self.make_repo()
        base = commit(repo, "Baseline")
        sha_a = commit(repo, "Feature - A")
        commit(repo, "Docs - X", "[skip changelog]")
        git(repo, "checkout", "-b", "side", sha_a)
        commit(repo, "Initial plan")
        git(repo, "checkout", "main")
        git(repo, "merge", "--no-ff", "side", "-m", "Merge side")
        sha_c = commit(repo, "Fix - C")
        now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

        manifest = build_manifest("v55.2", "HEAD", repo, baseline=base, now=now)

        self.assertEqual(
            manifest["commits"],
            [
                {"sha": sha_c, "parent_sha": git(repo, "rev-parse", "HEAD^"), "type": "fixed", "title": "C", "requires_comparison": True},
                {"sha": sha_a, "parent_sha": base, "type": "new", "title": "A", "requires_comparison": True},
            ],
        )
        self.assertEqual(manifest["revision"], sha_c)
        self.assertEqual(manifest["version"], "v55.2")
        self.assertEqual(manifest["generated_at"], now.isoformat())
        self.assertNotIn("t@t.test", json.dumps(manifest))
        self.assertNotIn('"t"', json.dumps(manifest))

    def test_non_ascii_subject_round_trips(self):
        repo = self.make_repo()
        base = commit(repo, "Baseline")
        commit(repo, "Fix - Café 日本語")
        manifest = build_manifest("v1", "HEAD", repo, baseline=base)
        self.assertEqual(manifest["commits"][0]["title"], "Café 日本語")

    def test_empty_subject_commit_is_skipped(self):
        repo = self.make_repo()
        base = commit(repo, "Baseline")
        sha_a = commit(repo, "Feature - A")
        git(repo, "commit", "--allow-empty", "--allow-empty-message", "-m", "")
        sha_b = commit(repo, "Fix - B")
        manifest = build_manifest("v1", "HEAD", repo, baseline=base)
        self.assertEqual(
            manifest["commits"],
            [
                {"sha": sha_b, "parent_sha": git(repo, "rev-parse", "HEAD^"), "type": "fixed", "title": "B", "requires_comparison": True},
                {"sha": sha_a, "parent_sha": base, "type": "new", "title": "A", "requires_comparison": True},
            ],
        )

    def test_small_release_keeps_every_commit_newest_first(self):
        repo = self.make_repo()
        base = commit(repo, "Baseline")
        commit(repo, "Fix - one")
        two = commit(repo, "Fix - two")
        three = commit(repo, "Fix - three")
        manifest = build_manifest("v1", "HEAD", repo, baseline=base)
        self.assertEqual(len(manifest["commits"]), 3)
        self.assertEqual([c["sha"] for c in manifest["commits"][:2]], [three, two])

    def test_baseline_not_ancestor_or_missing_fails(self):
        repo = self.make_repo()
        commit(repo, "Root")
        git(repo, "checkout", "-b", "side")
        side = commit(repo, "Side")
        git(repo, "checkout", "main")
        commit(repo, "Main")
        for baseline in (side, "1" * 40):
            with self.subTest(baseline=baseline):
                with self.assertRaisesRegex(ManifestError, "ancestor"):
                    build_manifest("v1", "HEAD", repo, baseline=baseline)


class MainTests(RepoMixin, SimpleTestCase):
    def test_main_writes_json_and_fails_cleanly(self):
        repo = self.make_repo()
        base = commit(repo, "Baseline")
        commit(repo, "Fix - Café")
        with tempfile.TemporaryDirectory() as out_dir:
            out = Path(out_dir) / "manifest.json"
            code = main(["--version", "v1", "--revision", "HEAD", "--repo", str(repo), "--baseline", base, "--output", str(out)])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(list(data), ["version", "revision", "generated_at", "commits", "evidence_policy"])
            self.assertIn("Café", out.read_text(encoding="utf-8"))

            missing = Path(out_dir) / "missing.json"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["--version", "v1", "--revision", "HEAD", "--repo", str(repo), "--baseline", "1" * 40, "--output", str(missing)])
            self.assertEqual(code, 1)
            self.assertTrue(stderr.getvalue().startswith("error: "))
            self.assertFalse(missing.exists())
