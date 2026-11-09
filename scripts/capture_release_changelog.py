"""Capture declared change evidence without using the running application."""

import argparse
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from copy import deepcopy
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.release_changelog import EVIDENCE_POLICY, MANIFEST_PATH, MAX_COMPARISONS, SHA_RE, TEXT_MAX, missing_comparisons, validate_manifest

LOGGER = logging.getLogger(__name__)
RECIPES_PATH = ROOT / "hub" / "changelog_capture_recipes.json"
ORIGIN = "http://changelog.capture.invalid"
VENDOR_ASSETS = {
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css": "bootstrap.min.css",
    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css": "bootstrap-icons.css",
    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/fonts/bootstrap-icons.woff2": "bootstrap-icons.woff2",
    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/fonts/bootstrap-icons.woff": "bootstrap-icons.woff",
}
VENDOR_HASHES = {
    "bootstrap.min.css": "3017df4a76db5f01c2b99b603d88b03106df13bcfe18e67b7c13c2341d3a67df",
    "bootstrap-icons.css": "bb6fd8cd85394cb367e8ac58e47292f2d68eb288fa12fab68e65430a5ddfce48",
    "bootstrap-icons.woff2": "bacd70afda7da1deac2bbd49b5717a4dd133bcd59c379525d705b8492f678e95",
    "bootstrap-icons.woff": "4d4572ef314e1b734cdd6485f913b0396d81bedf4d216a47cfde0cdf32a9316e",
}


class CaptureError(ValueError):
    pass


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise CaptureError("Capture paths must be repository-relative.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} or ":" in part for part in value.split("/")):
        raise CaptureError("Capture paths must stay inside the repository.")
    if any(part.startswith(".env") or part in {".git", ".azure", ".ssh"} for part in path.parts):
        raise CaptureError("Private configuration cannot be captured.")
    if path.suffix.lower() in {".pem", ".key", ".pfx", ".p12"}:
        raise CaptureError("Credentials cannot be captured.")
    return path


def load_recipes(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise CaptureError("Capture recipe configuration cannot be read.") from error
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1 or not isinstance(data.get("recipes"), list):
        raise CaptureError("Capture recipes require schema 1 and a recipe list.")
    identifiers = set()
    for recipe in data["recipes"]:
        if not isinstance(recipe, dict) or not isinstance(recipe.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", recipe["id"]):
            raise CaptureError("Each capture recipe needs a valid identifier.")
        if recipe["id"] in identifiers:
            raise CaptureError("Capture recipe identifiers must be unique.")
        identifiers.add(recipe["id"])
        commit = recipe.get("commit")
        paths = recipe.get("paths", [])
        if commit is not None and (not isinstance(commit, str) or not SHA_RE.fullmatch(commit)):
            raise CaptureError("Capture recipe commit must be a full Git SHA.")
        if not isinstance(paths, list) or (not commit and not paths):
            raise CaptureError("A capture recipe must declare a commit or affected paths.")
        for pattern in paths:
            safe_relative(pattern)
        scenes = recipe.get("scenes")
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_COMPARISONS:
            raise CaptureError("A recipe requires between 1 and 20 scenes.")
        for scene in scenes:
            if not isinstance(scene, dict) or scene.get("kind") not in {"ui", "document", "report"}:
                raise CaptureError("Unsupported capture scene kind.")
            for field in ("title", "before_text", "after_text"):
                value = scene.get(field)
                if not isinstance(value, str) or not value.strip() or len(value) > (200 if field == "title" else TEXT_MAX):
                    raise CaptureError("Capture scenes need bounded titles and before/after descriptions.")
            if scene["kind"] == "document":
                source = safe_relative(scene.get("path"))
                if source.suffix.lower() not in {".md", ".txt", ".rst"} or not isinstance(scene.get("heading"), str) or not scene["heading"].strip():
                    raise CaptureError("Documentation capture needs a selected text section.")
            elif scene.get("fixture") not in ({"login", "changelog", "sqr_performance"} if scene["kind"] == "ui" else {"changelog_contract"}):
                raise CaptureError("Unsupported synthetic capture fixture.")
            viewport = scene.get("viewport", {"width": 1200, "height": 900})
            if not isinstance(viewport, dict) or set(viewport) != {"width", "height"}:
                raise CaptureError("Capture viewport needs width and height.")
            if any(type(viewport[key]) is not int for key in viewport) or not (320 <= viewport["width"] <= 1920 and 240 <= viewport["height"] <= 1600):
                raise CaptureError("Capture viewport is outside supported bounds.")
    return data["recipes"]


def _git(repo, *args):
    result = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(repo), *args], capture_output=True)
    if result.returncode:
        LOGGER.error("Historical Git operation failed: %s", result.stderr.decode("utf-8", errors="replace"))
        raise CaptureError("Historical revision could not be read.")
    return result.stdout


def _snapshot(repo, revision, destination):
    archive = _git(repo, "archive", "--format=tar", revision)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if not member.isfile():
                continue
            try:
                safe_relative(member.name)
            except CaptureError:
                LOGGER.info("Excluded private or unsafe snapshot member.")
                continue
            source.extract(member, destination, filter="data")


def _asset(root, relative):
    from PIL import Image

    path = root.joinpath(*safe_relative(relative).parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise CaptureError("Screenshot is outside the capture assets.")
    if path.stat().st_size > 5 * 1024 * 1024:
        raise CaptureError("Screenshot exceeds 5 MB.")
    with Image.open(path) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"} or image.width < 160 or image.height < 80:
            raise CaptureError("Screenshot is not a supported full-size capture.")
        image.verify()
    return path


def _fingerprint(recipe):
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode("utf-8"))
    for path in (
        Path(__file__), Path(__file__).with_name("changelog_capture_scenes.py"),
        ROOT / "requirements-changelog-capture.txt", ROOT / "requirements.lock",
    ):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _cached(item, assets, recipe=None):
    if missing_comparisons([item], require_all=True):
        return False
    try:
        from PIL import Image

        for pair in item["comparisons"]:
            if pair.get("capture_note", "").startswith("Automatic "):
                if recipe is None:
                    return False
                prefix = f"automatic/{item['sha']}-{_fingerprint(recipe)}/"
                if any(not pair[field].startswith(prefix) for field in ("before_image", "after_image")):
                    return False
            for field in ("before_image", "after_image"):
                _asset(assets, pair[field])
    except ImportError:
        LOGGER.exception("Screenshot validation dependency is unavailable.")
        return False
    except (OSError, CaptureError, Image.DecompressionBombError):
        LOGGER.warning("Stored capture assets need regeneration for %s.", item["sha"], exc_info=True)
        return False
    return True


def _select_recipe(repo, item, recipes):
    exact = [recipe for recipe in recipes if recipe.get("commit") == item["sha"]]
    if exact:
        matches = exact
    else:
        changed = [name for name in _git(
            repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", item["sha"],
        ).decode("utf-8").split("\0") if name]
        matches = [
            recipe for recipe in recipes if not recipe.get("commit") and changed
            and all(any(fnmatchcase(name, pattern) for pattern in recipe["paths"]) for name in changed)
        ]
    if not matches:
        public_paths = []
        for name in changed:
            try:
                safe_relative(name)
            except CaptureError:
                continue
            public_paths.append(name)
        return {
            "id": "repository-change",
            "scenes": [{
                "kind": "report", "fixture": "repository_change", "title": "Repository change report",
                "before_text": "Verified file presence, sizes and fingerprints at this change's exact first-parent revision.",
                "after_text": "Verified file presence, sizes and fingerprints after this exact change. Source code and private configuration are not displayed.",
                "paths": public_paths, "omitted_files": len(changed) - len(public_paths),
            }],
        }
    if len(matches) != 1:
        raise CaptureError("Ambiguous capture recipes; declare one exact change recipe.")
    return matches[0]


def _render(snapshot, scene):
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG"}
    }
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("changelog_capture_scenes.py")), "--snapshot", str(snapshot)],
        input=json.dumps({key: scene[key] for key in ("kind", "fixture", "path", "heading", "paths", "omitted_files", "revision") if key in scene}),
        capture_output=True, text=True, encoding="utf-8", cwd=snapshot, env=environment, timeout=30,
    )
    if result.returncode:
        LOGGER.error("Historical scene rendering failed: %s", result.stderr)
        raise CaptureError("Historical fixture could not render; see capture logs.")
    if not result.stdout.strip():
        raise CaptureError("Historical fixture produced no capture content.")
    return result.stdout


def _screenshot(browser, snapshot, scene, output, vendor_dir):
    document = _render(snapshot, scene) + (
        "<style>*,*::before,*::after{animation:none!important;transition:none!important}"
        ".lg-enter{opacity:1!important;transform:none!important}</style>"
    )
    failed_resources = []
    context = browser.new_context(
        viewport=scene.get("viewport", {"width": 1200, "height": 900}),
        java_script_enabled=False, reduced_motion="reduce", service_workers="block",
    )
    try:
        def serve(route):
            request_url = urlsplit(route.request.url)
            normalized = f"{request_url.scheme}://{request_url.netloc}{request_url.path}"
            if route.request.url.rstrip("/") == ORIGIN:
                route.fulfill(
                    status=200, content_type="text/html", body=document,
                    headers={"Content-Security-Policy": "script-src 'none'; connect-src 'none'; frame-src 'none'"},
                )
                return
            if normalized in VENDOR_ASSETS:
                path = (vendor_dir / VENDOR_ASSETS[normalized]).resolve()
                if not path.is_relative_to(vendor_dir.resolve()):
                    failed_resources.append("invalid vendor resource")
                    route.abort()
                    return
            elif f"{request_url.scheme}://{request_url.netloc}" == ORIGIN and request_url.path.startswith("/static/"):
                try:
                    relative = safe_relative(unquote(request_url.path[len("/static/"):]))
                    path = snapshot.joinpath("static", *relative.parts).resolve()
                    if not path.is_relative_to((snapshot / "static").resolve()):
                        raise CaptureError("Capture resource escaped the snapshot.")
                except CaptureError:
                    failed_resources.append("invalid local resource")
                    route.abort()
                    return
            else:
                failed_resources.append("external resource blocked")
                route.abort()
                return
            if not path.is_file():
                failed_resources.append("required capture resource missing")
                route.abort()
                return
            if normalized in VENDOR_ASSETS and hashlib.sha256(path.read_bytes()).hexdigest() != VENDOR_HASHES[path.name]:
                failed_resources.append("vendor integrity check failed")
                route.abort()
                return
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

        context.route("**/*", serve)
        page = context.new_page()
        page.goto(ORIGIN, wait_until="networkidle", timeout=15000)
        if failed_resources:
            raise CaptureError("Capture resources are unavailable or external traffic was blocked.")
        selector = ".login-card" if scene.get("fixture") == "login" else ".cl-entry" if scene.get("fixture") == "changelog" else "#sqr-performance-dashboard" if scene.get("fixture") == "sqr_performance" else "body"
        page.locator(selector).screenshot(path=str(output), animations="disabled", timeout=15000)
    finally:
        context.close()


def _capture_change(browser, repo, item, recipe, assets, vendor_dir):
    parents = _git(repo, "rev-list", "--parents", "-n", "1", item["sha"]).decode("ascii").split()
    if len(parents) < 2:
        raise CaptureError("This change has no parent revision to capture.")
    if item.get("parent_sha") != parents[1]:
        raise CaptureError("Declared before revision does not match the change's first parent.")
    fingerprint = _fingerprint(recipe)
    relative = f"automatic/{item['sha']}-{fingerprint}"
    directory = assets.joinpath(*relative.split("/"))
    if not directory.resolve().is_relative_to(assets.resolve()):
        raise CaptureError("Capture output escaped the asset directory.")
    directory.mkdir(parents=True, exist_ok=True)
    pairs = []
    outputs = []
    observable = False
    complete = False
    try:
        with tempfile.TemporaryDirectory(prefix="request-hub-capture-") as temporary:
            before = Path(temporary) / "before"
            after = Path(temporary) / "after"
            before.mkdir()
            after.mkdir()
            _snapshot(repo, parents[1], before)
            _snapshot(repo, item["sha"], after)
            for index, scene in enumerate(recipe["scenes"]):
                names = (f"before-{index}.png", f"after-{index}.png")
                for snapshot, name, revision in zip((before, after), names, (parents[1], item["sha"])):
                    output = directory / name
                    outputs.append(output)
                    _screenshot(browser, snapshot, {**scene, "revision": revision}, output, vendor_dir)
                    _asset(assets, f"{relative}/{name}")
                observable |= (directory / names[0]).read_bytes() != (directory / names[1]).read_bytes()
                pairs.append({
                    "title": scene["title"], "before_text": scene["before_text"], "after_text": scene["after_text"],
                    "before_image": f"{relative}/{names[0]}", "after_image": f"{relative}/{names[1]}",
                    "before_revision": parents[1], "after_revision": item["sha"],
                    "capture_note": (
                        f"Automatic {scene['kind']} capture, recipe {recipe['id']}. Exact Git snapshots; synthetic fixtures; browser scripts/traffic disabled."
                        + (" Repository change report; not a UI screenshot. No source code or live data is displayed."
                           if scene.get("fixture") == "repository_change" else "")
                    ),
                })
        if not observable:
            raise CaptureError("Configured scenes show no observable change; choose relevant evidence.")
        complete = True
    finally:
        if not complete:
            for output in outputs:
                output.unlink(missing_ok=True)
    item["comparisons"] = pairs
    item.pop("capture_error", None)


def capture_manifest(manifest, repo, recipes_path, *, vendor_dir=None, failure=None):
    result = deepcopy(validate_manifest(manifest))
    if result.get("evidence_policy") != EVIDENCE_POLICY:
        raise CaptureError("Generate a current all-changes manifest before capturing evidence.")
    repo = Path(repo).resolve()
    assets = repo / "hub" / "changelog_assets"
    if not assets.resolve().is_relative_to(repo):
        raise CaptureError("Capture assets must stay inside the repository.")
    vendor_dir = Path(vendor_dir) if vendor_dir else repo / ".changelog-capture-vendor"
    failures = []
    failed_shas = set()
    pending = []

    def failed(item, message):
        item.pop("comparisons", None)
        item["capture_error"] = message
        if item["sha"] not in failed_shas:
            failures.append(f"{item['sha'][:12]}: {message}")
            failed_shas.add(item["sha"])
        LOGGER.error("Capture failed for %s: %s", item["sha"], message)

    try:
        if failure:
            raise CaptureError(failure)
        recipes = load_recipes(recipes_path)
    except CaptureError as error:
        LOGGER.exception("Capture setup or configuration is unavailable.")
        for item in result["commits"]:
            if not _cached(item, assets):
                failed(item, str(error))
        return validate_manifest(result), failures

    for item in result["commits"]:
        if _cached(item, assets):
            continue
        try:
            recipe = _select_recipe(repo, item, recipes)
            if not _cached(item, assets, recipe):
                pending.append((item, recipe))
        except CaptureError as error:
            failed(item, str(error))
    if not pending:
        return validate_manifest(result), failures
    try:
        from PIL import Image
        from playwright.sync_api import Error as BrowserError, sync_playwright
    except ImportError:
        LOGGER.exception("Playwright capture dependency is unavailable.")
        for item, _ in pending:
            failed(item, "Capture dependency unavailable; install requirements-changelog-capture.txt.")
        return validate_manifest(result), failures
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for item, recipe in pending:
                    try:
                        _capture_change(browser, repo, item, recipe, assets, vendor_dir)
                    except (CaptureError, OSError, subprocess.SubprocessError, BrowserError, Image.DecompressionBombError) as error:
                        LOGGER.exception("Historical capture failed for %s.", item["sha"])
                        failed(item, str(error) if isinstance(error, CaptureError) else "Historical capture failed; see capture logs.")
            finally:
                browser.close()
    except (OSError, BrowserError):
        LOGGER.exception("Capture browser is unavailable.")
        for item, recipe in pending:
            if item["sha"] not in failed_shas and not _cached(item, assets, recipe):
                failed(item, "Capture browser unavailable; install the Playwright Chromium browser.")
    return validate_manifest(result), failures


def prepare_assets(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for url, name in VENDOR_ASSETS.items():
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise CaptureError("Capture vendor asset exceeds its size limit.")
        if hashlib.sha256(data).hexdigest() != VENDOR_HASHES[name]:
            raise CaptureError("Capture vendor asset failed its pinned integrity check.")
        destination = (directory / name).resolve()
        if not destination.is_relative_to(directory.resolve()):
            raise CaptureError("Capture vendor asset escaped its directory.")
        destination.write_bytes(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--recipes", default=str(RECIPES_PATH))
    parser.add_argument("--vendor-dir", default=str(ROOT / ".changelog-capture-vendor"))
    parser.add_argument("--prepare-assets", action="store_true")
    parser.add_argument("--failure", help="Record a capture setup failure without launching the browser.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        if args.prepare_assets:
            prepare_assets(args.vendor_dir)
            return 0
        path = Path(args.manifest)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        result, failures = capture_manifest(manifest, Path(args.repo), Path(args.recipes), vendor_dir=Path(args.vendor_dir), failure=args.failure)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        print(f"Captured {len(result['commits']) - len(failures)} of {len(result['commits'])} change(s); {len(failures)} need evidence.")
        return 1 if failures else 0
    except (CaptureError, OSError, ValueError, urllib.error.URLError):
        LOGGER.exception("Changelog capture could not complete; the generated manifest remains unpublished without evidence.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
