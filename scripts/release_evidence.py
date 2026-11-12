"""Transfer screenshot evidence, never executable content, from the exact CI run."""

import argparse
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hub.release_changelog import EVIDENCE_POLICY, ManifestError, missing_comparisons, validate_manifest

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BUNDLE_BYTES = 100 * 1024 * 1024


class EvidenceError(ValueError):
    pass


def _read(path):
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise EvidenceError("Evidence metadata exceeds the size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest(path):
    try:
        data = validate_manifest(_read(path))
    except (TypeError, ValueError) as error:
        raise EvidenceError("Evidence manifest schema is invalid") from error
    if data.get("evidence_policy") != EVIDENCE_POLICY:
        raise EvidenceError("Evidence must retain the all-changes policy")
    shas = [item["sha"] for item in data["commits"]]
    if len(shas) != len(set(shas)):
        raise EvidenceError("Duplicate evidence commit")
    return data


def _identity(run_id, run_attempt):
    if not all(re.fullmatch(r"[1-9][0-9]*", str(value)) for value in (run_id, run_attempt)):
        raise EvidenceError("Invalid upstream run identity")
    return {"run_id": str(run_id), "run_attempt": str(run_attempt)}


def _image(root, relative):
    path = root.joinpath(*relative.split("/"))
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or path.is_symlink():
        raise EvidenceError("Evidence image escaped its asset directory")
    if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        raise EvidenceError("Evidence image exceeds the size limit")
    data = path.read_bytes()
    try:
        from PIL import Image
    except ImportError as error:
        raise EvidenceError("Evidence decoder is unavailable") from error
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP"):
                    raise EvidenceError("Unsupported evidence image")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise EvidenceError("Evidence image cannot be safely decoded") from error
    return data


def _images(manifest):
    return {pair[field] for item in manifest["commits"] for pair in item.get("comparisons", [])
            for field in ("before_image", "after_image")}


def _write(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def export_bundle(manifest_path, assets, destination, *, run_id, run_attempt):
    manifest_path, assets, destination = map(Path, (manifest_path, assets, destination))
    manifest = _manifest(manifest_path)
    metadata = {**_identity(run_id, run_attempt), "revision": manifest["revision"], "files": {}}
    images = {}
    total = 0
    for relative in sorted(_images(manifest)):
        data = _image(assets, relative)
        total += len(data)
        if total > MAX_BUNDLE_BYTES:
            raise EvidenceError("Evidence bundle exceeds the size limit")
        images[relative] = data
        metadata["files"][relative] = sha256(data).hexdigest()
    destination.mkdir(parents=True, exist_ok=True)
    for relative, data in images.items():
        output = destination / "assets" / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    _write(destination / "manifest.json", manifest)
    _write(destination / "metadata.json", metadata)


def apply_bundle(manifest_path, assets, bundle, *, run_id, run_attempt):
    manifest_path, assets, bundle = map(Path, (manifest_path, assets, bundle))
    release = _manifest(manifest_path)
    captured = _manifest(bundle / "manifest.json")
    metadata = _read(bundle / "metadata.json")
    if not isinstance(metadata, dict):
        raise EvidenceError("Evidence metadata must be an object")
    identity = _identity(run_id, run_attempt)
    if any(metadata.get(key) != value for key, value in identity.items()):
        raise EvidenceError("Evidence is from another CI run or attempt")
    if captured["revision"] != release["revision"] or metadata.get("revision") != release["revision"]:
        raise EvidenceError("Evidence revision does not match the release")
    incoming = {item["sha"]: item for item in captured["commits"]}
    if set(incoming) != {item["sha"] for item in release["commits"]}:
        raise EvidenceError("Evidence does not cover the authoritative release commit range")
    for item in release["commits"]:
        source = incoming[item["sha"]]
        if source.get("parent_sha") != item.get("parent_sha"):
            raise EvidenceError("Evidence parent does not match the release")
    files = metadata.get("files")
    if not isinstance(files, dict) or set(files) != _images(captured):
        raise EvidenceError("Evidence inventory does not match the manifest")
    images = {}
    total = 0
    for relative, checksum in files.items():
        data = _image(bundle / "assets", relative)
        total += len(data)
        if total > MAX_BUNDLE_BYTES or sha256(data).hexdigest() != checksum:
            raise EvidenceError("Evidence checksum or size validation failed")
        target = assets.joinpath(*relative.split("/"))
        if not target.resolve().is_relative_to(assets.resolve()) or target.is_symlink():
            raise EvidenceError("Evidence destination escaped its asset directory")
        # Never overwrite approved, tracked images with downloaded evidence.
        if target.exists() and target.read_bytes() != data:
            raise EvidenceError("Evidence would overwrite a different packaged image")
        images[relative] = data
    result = deepcopy(release)
    complete = 0
    for item in result["commits"]:
        source = incoming[item["sha"]]
        if missing_comparisons([item], require_all=True):
            for field in ("comparisons", "before_text", "after_text", "capture_error"):
                if field in source:
                    item[field] = source[field]
            if not missing_comparisons([source], require_all=True):
                item.pop("capture_error", None)
        if not missing_comparisons([item], require_all=True):
            complete += 1
    result = validate_manifest(result)
    for relative, data in images.items():
        output = assets / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    _write(manifest_path, result)
    return complete


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("export", "import"))
    parser.add_argument("--manifest", default="hub/release_changelog_manifest.json")
    parser.add_argument("--assets", default="hub/changelog_assets")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    args = parser.parse_args(argv)
    try:
        function = export_bundle if args.operation == "export" else apply_bundle
        result = function(args.manifest, args.assets, args.bundle, run_id=args.run_id, run_attempt=args.run_attempt)
        if args.operation == "import":
            print(f"Imported exact-run evidence: {result} change(s) complete.")
    except (OSError, ValueError, ManifestError) as error:
        if args.operation == "export":
            print(f"Evidence export failed: {error}", file=sys.stderr)
            return 1
        print(f"::warning::CI evidence unavailable or rejected: {error}")
        manifest = _manifest(Path(args.manifest))
        for item in manifest["commits"]:
            if missing_comparisons([item], require_all=True):
                item["capture_error"] = "CI screenshot evidence unavailable or rejected; retry capture or add evidence in Management."
        _write(Path(args.manifest), manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
