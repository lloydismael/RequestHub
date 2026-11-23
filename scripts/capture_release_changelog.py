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

from hub.release_changelog import (
    EVIDENCE_POLICY,
    MANIFEST_PATH,
    MAX_COMPARISONS,
    SHA_RE,
    TEXT_MAX,
    missing_comparisons,
    repository_report_only,
    validate_manifest,
)

LOGGER = logging.getLogger(__name__)
RECIPES_PATH = ROOT / "hub" / "changelog_capture_recipes.json"
ORIGIN = "http://changelog.capture.invalid"
SWIFTSHADER_ARGS = ("--use-angle=swiftshader", "--enable-unsafe-swiftshader")
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
            elif scene.get("fixture") not in ({
                "login", "seasonal_login", "profile", "profile_avatar_shortcut", "admin_profile", "changelog",
                "changelog_editor", "changelog_history", "changelog_feed", "management_accounts", "management_monitoring",
                "sqr_tracker", "sqr_performance", "request_progress", "christmas_canopy",
            } if scene["kind"] == "ui" else {"changelog_contract", "repository_change"}):
                raise CaptureError("Unsupported synthetic capture fixture.")
            if scene.get("fixture") == "repository_change" and {"paths", "omitted_files", "revision"} & scene.keys():
                raise CaptureError("Repository report inputs must be derived from the captured change.")
            viewport = scene.get("viewport", {"width": 1200, "height": 900})
            if not isinstance(viewport, dict) or set(viewport) != {"width", "height"}:
                raise CaptureError("Capture viewport needs width and height.")
            if any(type(viewport[key]) is not int for key in viewport) or not (320 <= viewport["width"] <= 1920 and 240 <= viewport["height"] <= 1600):
                raise CaptureError("Capture viewport is outside supported bounds.")
    return data["recipes"]


def _git(repo, *args):
    result = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(repo), *args], check=False, capture_output=True)
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
    if recipe and repository_report_only(item) and any(scene["kind"] == "ui" for scene in recipe["scenes"]):
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


def _public_paths(changed):
    paths = []
    for name in changed:
        try:
            safe_relative(name)
        except CaptureError:
            continue
        paths.append(name)
    return paths


def _with_report_paths(recipe, changed):
    if not any(scene.get("fixture") == "repository_change" for scene in recipe["scenes"]):
        return recipe
    public_paths = _public_paths(changed)
    return {**recipe, "scenes": [
        {**scene, "paths": public_paths, "omitted_files": len(changed) - len(public_paths)}
        if scene.get("fixture") == "repository_change" else scene for scene in recipe["scenes"]
    ]}


def _select_recipe(repo, item, recipes):
    exact = [recipe for recipe in recipes if recipe.get("commit") == item["sha"]]
    if exact:
        if len(exact) != 1:
            raise CaptureError("Ambiguous capture recipes; declare one exact change recipe.")
        if not any(scene.get("fixture") == "repository_change" for scene in exact[0]["scenes"]):
            return exact[0]

    revisions = [item["parent_sha"], item["sha"]] if item.get("parent_sha") else [item["sha"]]
    changed = [name for name in _git(
        repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", *revisions,
    ).decode("utf-8").split("\0") if name]
    if exact:
        return _with_report_paths(exact[0], changed)

    by_path = [recipe for recipe in recipes if not recipe.get("commit")]
    screens = [recipe for recipe in by_path if any(scene["kind"] == "ui" for scene in recipe["scenes"])
               and any(fnmatchcase(name, pattern) for name in changed for pattern in recipe["paths"])]
    if screens:
        scenes = [scene for recipe in screens for scene in recipe["scenes"]]
        if len(screens) == 1:
            return _with_report_paths(screens[0], changed)
        if len(scenes) > MAX_COMPARISONS or len({scene["title"] for scene in scenes}) != len(scenes):
            raise CaptureError("Overlapping or too many UI scenes; declare one exact change recipe.")
        return _with_report_paths({"id": "affected-screens", "recipes": [recipe["id"] for recipe in screens], "scenes": scenes}, changed)

    matches = [recipe for recipe in by_path if changed and
               all(any(fnmatchcase(name, pattern) for pattern in recipe["paths"]) for name in changed)]
    if not matches:
        if any(name.startswith(("templates/", "static/css/", "static/js/", "static/hub/")) for name in changed):
            raise CaptureError("No UI capture recipe covers the changed screens. Add a recipe or supply actual before/after screenshots.")
        return _with_report_paths({
            "id": "repository-change",
            "scenes": [{
                "kind": "report", "fixture": "repository_change", "title": "Repository change report",
                "before_text": "Verified file presence, sizes and fingerprints at this change's exact first-parent revision.",
                "after_text": "Verified file presence, sizes and fingerprints after this exact change. Source code and private configuration are not displayed.",
            }],
        }, changed)
    if len(matches) != 1:
        raise CaptureError("Ambiguous capture recipes; declare one exact change recipe.")
    return _with_report_paths(matches[0], changed)


def _render(snapshot, scene):
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG"}
    }
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("changelog_capture_scenes.py")), "--snapshot", str(snapshot)],
        check=False,
        input=json.dumps({key: scene[key] for key in ("kind", "fixture", "path", "heading", "paths", "omitted_files", "revision") if key in scene}),
        capture_output=True, text=True, encoding="utf-8", cwd=snapshot, env=environment, timeout=30,
    )
    if result.returncode:
        LOGGER.error("Historical scene rendering failed: %s", result.stderr)
        raise CaptureError("Historical fixture could not render; see capture logs.")
    if not result.stdout.strip():
        raise CaptureError("Historical fixture produced no capture content.")
    return result.stdout


def _enhance_profile_avatar(page, snapshot):
    # Only run the historical form runtime, not the page's other scripts.
    runtime = snapshot / "static" / "hub" / "form_runtime.js"
    if runtime.is_file():
        page.evaluate("""source => {
            (0, eval)(source);
            document.dispatchEvent(new Event('DOMContentLoaded'));
        }""", runtime.read_text(encoding="utf-8"))


def _enhance_christmas_canopy(page, snapshot):
    scripts = snapshot / "static" / "js"
    sources = [(scripts / name).read_text(encoding="utf-8") for name in (
        "christmas-theme.js", "christmas-cord.js", "christmas-canopy.js", "christmas-lights.js",
    ) if (scripts / name).is_file()]
    page.evaluate("""sources => {
        const context = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (type, options) {
            return context.call(this, type, type === 'webgl2' ? {...options, failIfMajorPerformanceCaveat: false} : options);
        };
        document.body.classList.add('dark-mode');
        document.querySelector('.rh-primary-nav').dataset.navSurface = 'dark';
        document.querySelector('.profile-panel--active')?.classList.remove('profile-panel--active');
        document.querySelector('#profile-panel-themes').classList.add('profile-panel--active');
        document.querySelector('.profile-tab--active')?.classList.remove('profile-tab--active');
        document.querySelector('[data-profile-tab="themes"]').classList.add('profile-tab--active');
        for (const source of sources) (0, eval)(source);
    }""", sources)
    page.locator(".seasonal-lights-overlay").wait_for(state="attached", timeout=6000)
    if (scripts / "christmas-canopy.js").is_file():
        page.locator("canvas.seasonal-canopy-rays").wait_for(state="visible", timeout=10000)
    page.locator("#christmas-controls").evaluate("section => window.scrollBy(0, section.getBoundingClientRect().top - 124)")


def _enhance_seasonal_login(page, snapshot):
    shared_lights = bool(page.locator("#login-light-flow").count())
    names = ("christmas-cord.js", "christmas-canopy.js", "christmas-lights.js") if shared_lights else ()
    scripts = (snapshot / "static" / "js").resolve()
    sources = []
    for name in (*names, "login-christmas.js"):
        script = (scripts / name).resolve()
        if not script.is_relative_to(snapshot.resolve()) or not script.is_relative_to(scripts) or not script.is_file():
            raise CaptureError("Required seasonal login script is unavailable inside the snapshot.")
        sources.append(script.read_text(encoding="utf-8"))
    if shared_lights:
        page.emulate_media(reduced_motion="no-preference")
    # Render a fixed first frame on both sides; don't enable the snapshot's page scripts.
    page.evaluate("""sources => {
        document.documentElement.dataset.theme = 'dark';
        document.documentElement.style.colorScheme = 'dark';
        document.body.classList.add('dark-mode');
        const toggle = document.getElementById('theme-checkbox');
        if (toggle) toggle.checked = true;
        const nav = document.querySelector('.navbar[data-nav-surface]');
        if (nav) nav.dataset.navSurface = nav.dataset.navDefaultSurface = 'dark';
        let seed = 421, id = 0;
        Math.random = () => (seed = Math.imul(1664525, seed) + 1013904223 >>> 0) / 4294967296;
        const frames = new Map();
        window.requestAnimationFrame = callback => { frames.set(++id, callback); return id; };
        window.cancelAnimationFrame = frame => frames.delete(frame);
        const context = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (type, options) {
            return context.call(this, type, type === 'webgl2' ? {...options, failIfMajorPerformanceCaveat: false} : options);
        };
        for (const source of sources) (0, eval)(source);
        for (const now of [10, 20, 30]) {
            for (const frame of [...frames.keys()]) {
                const callback = frames.get(frame);
                frames.delete(frame);
                callback?.(now);
            }
        }
    }""", sources)
    if not page.locator(".xmas-scene canvas.xmas-snow").is_visible():
        raise CaptureError("The seasonal login snow did not render.")
    if not shared_lights and not page.locator(".xmas-deco-static").count():
        raise CaptureError("The historical seasonal decoration did not render.")
    if shared_lights and page.viewport_size["width"] >= 480:
        for selector in (".login-card canvas.seasonal-canopy-rays", ".xmas-light-spill"):
            page.locator(selector).wait_for(state="visible", timeout=10000)


def _screenshot(browser, snapshot, scene, output, vendor_dir):
    document = _render(snapshot, scene) + (
        "<style>*,*::before,*::after{animation:none!important;transition:none!important}"
        ".lg-enter{opacity:1!important;transform:none!important}</style>"
    )
    fixture = scene.get("fixture")
    if fixture in {
        "profile_avatar_shortcut", "admin_profile", "changelog", "changelog_editor", "changelog_history",
        "changelog_feed", "management_monitoring", "management_accounts", "request_progress",
    }:
        document += "<style>.navbar{position:static!important}</style>"
    failed_resources = []
    context = browser.new_context(
        viewport=scene.get("viewport", {"width": 1200, "height": 900}),
        # Chromium won't composite scripted canvases with JS disabled. The CSP
        # below still blocks page scripts; only the selected renderers are evaluated.
        java_script_enabled=fixture in {"profile_avatar_shortcut", "christmas_canopy", "seasonal_login"},
        reduced_motion="no-preference" if fixture == "christmas_canopy" else "reduce",
        service_workers="block",
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
        if fixture == "seasonal_login" and not failed_resources:
            _enhance_seasonal_login(page, snapshot)
        if failed_resources:
            raise CaptureError("Capture resources are unavailable or external traffic was blocked.")
        if fixture == "profile_avatar_shortcut":
            _enhance_profile_avatar(page, snapshot)
        if fixture == "christmas_canopy":
            _enhance_christmas_canopy(page, snapshot)
            if failed_resources:
                raise CaptureError("Capture resources are unavailable or external traffic was blocked.")
            page.screenshot(path=str(output), animations="disabled", timeout=15000)
            return
        if fixture == "management_accounts":
            # The old no-script page can be thousands of pixels tall.
            page.locator("#accounts-pane").wait_for(state="visible")
            page.screenshot(path=str(output), animations="disabled", timeout=15000)
            return
        selector = {
            "login": ".login-hero", "seasonal_login": ".login-hero",
            "profile": "#profile-panel-avatar .profile-card, .profile-avatar-block",
            "profile_avatar_shortcut": ".profile-hero",
            "admin_profile": ".settings-admin-wrap",
            "changelog": ".cl-entry", "changelog_editor": ".cl-editor",
            "changelog_history": ".cl-history-entry", "changelog_feed": ".cl-technical-evidence",
            "management_monitoring": "main", "sqr_tracker": "#sqr-proto",
            "sqr_performance": "#sqr-performance-dashboard", "request_progress": ".rmf-progress-card",
        }.get(fixture, "body")
        component = page.locator(selector)
        # Native disclosures work with page scripts disabled at both revisions.
        if fixture == "changelog_history":
            component.locator("details > summary").first.click()
        elif fixture == "changelog_feed":
            component.locator(":scope > summary").click()
            component.locator("details > summary").first.click()
        component.screenshot(path=str(output), animations="disabled", timeout=15000)
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
    visual_observable = False
    complete = False
    from PIL import Image, ImageChops

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
                with Image.open(directory / names[0]) as before_image, Image.open(directory / names[1]) as after_image:
                    changed = before_image.size != after_image.size or ImageChops.difference(
                        before_image.convert("RGB"), after_image.convert("RGB"),
                    ).getbbox() is not None
                observable |= changed
                if not changed and scene["kind"] == "ui":
                    for name in names:
                        (directory / name).unlink()
                    continue
                visual_observable |= scene["kind"] == "ui"
                pairs.append({
                    "title": scene["title"], "before_text": scene["before_text"], "after_text": scene["after_text"],
                    "evidence_kind": scene["kind"],
                    "before_image": f"{relative}/{names[0]}", "after_image": f"{relative}/{names[1]}",
                    "before_revision": parents[1], "after_revision": item["sha"],
                    "capture_note": (
                        f"Automatic {scene['kind']} capture, recipe {recipe['id']}. Exact Git snapshots; synthetic fixtures; external traffic blocked."
                        + (" Only the seasonal decoration scripts rendered a fixed dark-mode frame."
                           if scene.get("fixture") == "seasonal_login"
                           else " Only the historical form runtime updated the profile labels."
                           if scene.get("fixture") == "profile_avatar_shortcut"
                           else " Only the historical theme and navbar scripts rendered the steady lights."
                           if scene.get("fixture") == "christmas_canopy" else " Page scripts disabled.")
                        + (" Repository change report; not a UI screenshot. No source code or live data is displayed."
                           if scene.get("fixture") == "repository_change" else "")
                    ),
                })
        if not observable or (not visual_observable and any(scene["kind"] == "ui" for scene in recipe["scenes"])):
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
        cached = _cached(item, assets)
        packaged_report = cached and repository_report_only(item)
        if cached and not packaged_report:
            continue
        try:
            recipe = _select_recipe(repo, item, recipes)
            if not _cached(item, assets, recipe):
                pending.append((item, recipe))
        except CaptureError as error:
            if not packaged_report:
                failed(item, str(error))
    if not pending:
        return validate_manifest(result), failures
    try:
        from PIL import Image
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import sync_playwright
    except ImportError:
        LOGGER.exception("Playwright capture dependency is unavailable.")
        for item, _ in pending:
            failed(item, "Capture dependency unavailable; install requirements-changelog-capture.txt.")
        return validate_manifest(result), failures
    needs_webgl = any(scene.get("fixture") in {"christmas_canopy", "seasonal_login"}
                      for _, recipe in pending for scene in recipe["scenes"])
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                args=list(SWIFTSHADER_ARGS) if needs_webgl else [],
            )
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
