import importlib
import json
from pathlib import Path
import tempfile
import unittest


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.evidence = importlib.import_module("release_evidence")
        except ModuleNotFoundError:
            self.fail("Exact-run release evidence handoff is not implemented")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.manifest = self.root / "release.json"
        self.bundle = self.root / "bundle"
        self.sha = "1" * 40
        self.parent = "2" * 40
        self.revision = "3" * 40
        self.data = {
            "version": "next", "revision": self.revision,
            "generated_at": "2026-11-10T00:00:00+00:00", "evidence_policy": "all_changes",
            "commits": [{"sha": self.sha, "parent_sha": self.parent, "type": "maintenance",
                         "title": "Pipeline improvement", "requires_comparison": True,
                         "comparisons": [{"title": "Pipeline report", "before_text": "Before.",
                                          "after_text": "After.", "before_image": "automatic/test/before.png",
                                          "after_image": "automatic/test/after.png",
                                          "before_revision": self.parent, "after_revision": self.sha,
                                          "capture_note": "Exact historical report."}]}],
        }
        from io import BytesIO
        from PIL import Image
        directory = self.assets / "automatic" / "test"
        directory.mkdir(parents=True)
        for name, color in (("before.png", "red"), ("after.png", "blue")):
            image = BytesIO()
            Image.new("RGB", (160, 80), color).save(image, "PNG")
            (directory / name).write_bytes(image.getvalue())
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")

    def export(self):
        self.evidence.export_bundle(self.manifest, self.assets, self.bundle, run_id="123", run_attempt="2")

    def release(self):
        release = json.loads(json.dumps(self.data))
        release["version"] = "v60.1"
        release["commits"][0].pop("comparisons", None)
        self.manifest.write_text(json.dumps(release), encoding="utf-8")
        return self.manifest.read_bytes()

    def apply(self, assets=None, **kwargs):
        return self.evidence.apply_bundle(self.manifest, assets or self.root / "release-assets", self.bundle,
                                          run_id=kwargs.get("run_id", "123"), run_attempt=kwargs.get("run_attempt", "2"))

    def test_exact_run_import_preserves_release_identity_and_all_changes(self):
        self.export()
        self.release()
        count = self.apply()
        result = json.loads(self.manifest.read_text())
        self.assertEqual(count, 1)
        self.assertEqual(result["version"], "v60.1")
        self.assertEqual(result["revision"], self.revision)
        self.assertEqual(len(result["commits"]), 1)
        self.assertEqual(result["commits"][0]["comparisons"][0]["before_revision"], self.parent)
        self.assertTrue((self.root / "release-assets" / "automatic" / "test" / "after.png").is_file())

    def test_other_run_or_attempt_is_rejected_without_changing_release(self):
        self.export()
        original = self.release()
        for kwargs in ({"run_id": "124"}, {"run_attempt": "1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(self.evidence.EvidenceError):
                self.apply(**kwargs)
            self.assertEqual(self.manifest.read_bytes(), original)

    def test_wrong_revision_is_rejected(self):
        self.export()
        original = self.release()
        path = self.bundle / "manifest.json"
        data = json.loads(path.read_text())
        data["revision"] = "4" * 40
        path.write_text(json.dumps(data))
        with self.assertRaises(self.evidence.EvidenceError):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_extra_or_missing_commit_cannot_change_changelog_coverage(self):
        self.export()
        original = self.release()
        path = self.bundle / "manifest.json"
        data = json.loads(path.read_text())
        data["commits"] = []
        path.write_text(json.dumps(data))
        with self.assertRaises(self.evidence.EvidenceError):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_tampered_image_is_rejected_before_copying_any_files(self):
        self.export()
        original = self.release()
        (self.bundle / "assets" / "automatic" / "test" / "after.png").write_bytes(b"corrupt")
        with self.assertRaises(self.evidence.EvidenceError):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertFalse((self.root / "release-assets").exists())

    def test_arbitrary_bundle_files_are_not_copied(self):
        self.export()
        self.release()
        (self.bundle / "entrypoint.sh").write_text("untrusted code")
        self.apply()
        self.assertFalse((self.root / "entrypoint.sh").exists())

    def test_incomplete_capture_keeps_the_change_as_a_draft(self):
        self.data["commits"][0].pop("comparisons")
        self.data["commits"][0]["capture_error"] = "Capture unavailable; retry evidence."
        self.manifest.write_text(json.dumps(self.data))
        self.export()
        self.release()
        self.assertEqual(self.apply(), 0)
        result = json.loads(self.manifest.read_text())
        self.assertEqual(len(result["commits"]), 1)
        self.assertIn("Capture unavailable", result["commits"][0]["capture_error"])

    def test_parent_mismatch_is_rejected(self):
        self.export()
        original = self.release()
        path = self.bundle / "manifest.json"
        data = json.loads(path.read_text())
        data["commits"][0]["parent_sha"] = "5" * 40
        for pair in data["commits"][0]["comparisons"]:
            pair["before_revision"] = "5" * 40
        path.write_text(json.dumps(data))
        with self.assertRaises(self.evidence.EvidenceError):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_non_object_metadata_is_rejected_without_partial_import(self):
        self.export()
        original = self.release()
        (self.bundle / "metadata.json").write_text("[]")
        with self.assertRaises(self.evidence.EvidenceError):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_matching_checksum_does_not_accept_an_undecodable_image(self):
        from hashlib import sha256
        self.export()
        original = self.release()
        bad = b"\x89PNG\r\n\x1a\n"
        path = self.bundle / "assets" / "automatic" / "test" / "after.png"
        path.write_bytes(bad)
        metadata_path = self.bundle / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["files"]["automatic/test/after.png"] = sha256(bad).hexdigest()
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaises(self.evidence.EvidenceError):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_unhashable_manifest_type_cli_preserves_the_draft(self):
        self.export()
        self.release()
        path = self.bundle / "manifest.json"
        data = json.loads(path.read_text())
        data["commits"][0]["type"] = []
        path.write_text(json.dumps(data))
        result = self.evidence.main(["import", "--manifest", str(self.manifest), "--assets", str(self.assets),
                                    "--bundle", str(self.bundle), "--run-id", "123", "--run-attempt", "2"])
        self.assertEqual(result, 0)
        manifest = json.loads(self.manifest.read_text())
        self.assertEqual(len(manifest["commits"]), 1)
        self.assertIn("capture_error", manifest["commits"][0])

    def test_duplicate_commit_is_rejected_without_changing_coverage(self):
        self.export()
        original = self.release()
        path = self.bundle / "manifest.json"
        data = json.loads(path.read_text())
        data["commits"].append(data["commits"][0])
        path.write_text(json.dumps(data))
        with self.assertRaises(self.evidence.EvidenceError):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_missing_bundle_cli_preserves_all_changes_with_actionable_reason(self):
        self.release()
        status = self.evidence.main(["import", "--manifest", str(self.manifest), "--assets", str(self.assets),
                                     "--bundle", str(self.bundle), "--run-id", "123", "--run-attempt", "2"])
        self.assertEqual(status, 0)
        result = json.loads(self.manifest.read_text())
        self.assertEqual(len(result["commits"]), 1)
        self.assertIn("capture_error", result["commits"][0])
        self.assertTrue(result["commits"][0]["requires_comparison"])


if __name__ == "__main__":
    unittest.main()
