import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from workflow_test_utils import read_run_block

BASH = os.environ.get("TEST_BASH") or shutil.which("bash")


class CaptureWorkflowTests(unittest.TestCase):
    def run_capture_step(self, setup="success", vendor_status=0, capture_status=0, complete=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "hub").mkdir()
            from hub import release_changelog
            shutil.copyfile(release_changelog.__file__, root / "hub" / "release_changelog.py")
            item = {"sha": "1" * 40, "parent_sha": "2" * 40, "type": "maintenance",
                    "title": "Documentation change", "requires_comparison": True}
            if complete:
                item["comparisons"] = [{
                    "title": "Guide", "before_text": "Previous guide.", "after_text": "Updated guide.",
                    "before_image": "v1/before.png", "after_image": "v1/after.png",
                    "before_revision": "2" * 40, "after_revision": "1" * 40,
                }]
            path = root / "hub" / "release_changelog_manifest.json"
            path.write_text(json.dumps({
                "version": "v1", "revision": "3" * 40, "generated_at": "2026-11-06T00:00:00+00:00",
                "evidence_policy": "all_changes", "commits": [item],
            }), encoding="utf-8")
            summary = root / "summary.txt"
            calls = root / "calls.txt"
            environment = {
                **os.environ, "CAPTURE_SETUP_OUTCOME": setup, "GITHUB_STEP_SUMMARY": str(summary),
                "TEST_PYTHON": sys.executable, "CAPTURE_CALLS": str(calls),
                "VENDOR_STATUS": str(vendor_status), "CAPTURE_STATUS": str(capture_status),
            }
            stub = """
            python() {
              if [ "$1" = "scripts/capture_release_changelog.py" ]; then
                echo "$*" >> "$CAPTURE_CALLS"
                if [ "$2" = "--prepare-assets" ]; then return "$VENDOR_STATUS"; fi
                return "$CAPTURE_STATUS"
              fi
              "$TEST_PYTHON" "$@"
            }
            timeout() { shift; "$@"; }
            """
            script = read_run_block("container-security.yml", "Capture release changelog screenshots")
            result = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", stub + script],
                                    cwd=root, env=environment, capture_output=True, text=True)
            return (result, json.loads(path.read_text(encoding="utf-8")),
                    summary.read_text(encoding="utf-8") if summary.exists() else "",
                    calls.read_text(encoding="utf-8") if calls.exists() else "")

    def test_dependency_failure_keeps_logged_change_with_actionable_error(self):
        result, manifest, summary, calls = self.run_capture_step(setup="failure")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("tools could not be installed", manifest["commits"][0]["capture_error"])
        self.assertIn("deployment continues", summary)
        self.assertEqual(calls, "")

    def test_vendor_failure_prevents_capture_and_reports_draft_reason(self):
        result, manifest, _, calls = self.run_capture_step(vendor_status=42)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("assets could not be prepared", manifest["commits"][0]["capture_error"])
        self.assertEqual(len(calls.splitlines()), 1)

    def test_capture_timeout_keeps_manifest_and_requires_evidence(self):
        result, manifest, summary, calls = self.run_capture_step(capture_status=124)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("did not complete", manifest["commits"][0]["capture_error"])
        self.assertTrue(manifest["commits"][0]["requires_comparison"])
        self.assertIn("1 need evidence", summary)
        self.assertEqual(len(calls.splitlines()), 2)

    def test_complete_evidence_remains_publishable(self):
        result, manifest, summary, _ = self.run_capture_step(complete=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("capture_error", manifest["commits"][0])
        self.assertIn("1 complete; 0 need evidence", summary)


if __name__ == "__main__":
    unittest.main()
