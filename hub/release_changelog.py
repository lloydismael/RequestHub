"""Build the release changelog manifest from git history.

Standard library only: the release job runs this with just Python and git.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GROUPS = (("new", "New"), ("improved", "Improved"), ("fixed", "Fixed"), ("maintenance", "Maintenance"))
BASELINE = "a0f37126304c245797c346abf2df664e25fa5310"
MAX_COMMITS = 500
MANIFEST_PATH = Path(__file__).with_name("release_changelog_manifest.json")
ASSET_DIR = Path(__file__).with_name("changelog_assets")
COMPARISON_FIELDS = ("title", "before_text", "after_text", "before_image", "after_image")
COMPARISON_META_FIELDS = ("before_revision", "after_revision", "capture_note")
MAX_COMPARISONS = 20
TEXT_MAX = 10000
IMAGE_PATH_RE = re.compile(r"(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+\.(?:png|jpe?g|webp)", re.IGNORECASE)

TITLE_MAX = 200
KNOWN_TYPES = {key for key, _label in GROUPS}
VERSION_RE = re.compile(r"[A-Za-z0-9._-]{1,50}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
PREFIX_RE = re.compile(r"^(\w+)(?:\([^)]*\))?!?(?::|\s+[-–—](?=\s|$))\s*(.*)$", re.DOTALL)
PREFIX_TYPES = {
    **dict.fromkeys(("feature", "features", "feat"), "new"),
    **dict.fromkeys(("fix", "bugfix", "hotfix"), "fixed"),
    **dict.fromkeys(("docs", "doc", "test", "tests", "chore", "ci", "build", "refactor", "style"), "maintenance"),
    **dict.fromkeys(("enhancement", "improvement", "perf"), "improved"),
}


class ManifestError(ValueError):
    pass


def _cap(title):
    if len(title) > TITLE_MAX:
        return title[: TITLE_MAX - 1] + "…"
    return title


def classify(subject):
    subject = subject.strip()
    match = PREFIX_RE.match(subject)
    if match and match.group(1).lower() in PREFIX_TYPES:
        kind = PREFIX_TYPES[match.group(1).lower()]
        rest = match.group(2).strip()
        title = rest[0].upper() + rest[1:] if rest else subject
        return kind, _cap(title)
    lowered = subject.lower()
    if lowered.startswith(("fix ", "fixes ", "fixed ")):
        kind = "fixed"
    elif lowered.startswith("bump "):
        kind = "maintenance"
    else:
        kind = "improved"
    return kind, _cap(subject)


def is_excluded(subject, body):
    if not subject.strip():
        return True
    if subject.strip().lower() == "initial plan":
        return True
    return "[skip changelog]" in f"{subject}\n{body}".lower()


def complete_comparison(comparison):
    return isinstance(comparison, dict) and all(
        isinstance(comparison.get(field), str) and comparison[field].strip()
        for field in COMPARISON_FIELDS
    )


def missing_comparisons(commits):
    return [
        item.get("title") if isinstance(item.get("title"), str) and item["title"].strip() else "Untitled UI/UX change"
        for item in commits
        if isinstance(item, dict) and item.get("requires_comparison") and not (
            isinstance(item.get("comparisons"), list)
            and item["comparisons"]
            and all(complete_comparison(pair) for pair in item["comparisons"])
        )
    ]


def _validate_comparison(comparison):
    if not complete_comparison(comparison):
        raise ManifestError("comparisons require a title, descriptions and before/after images")
    clean = {field: comparison[field] for field in COMPARISON_FIELDS}
    if len(clean["title"]) > TITLE_MAX or any(len(clean[field]) > TEXT_MAX for field in ("before_text", "after_text")):
        raise ManifestError("comparison text is too long")
    for field in ("before_image", "after_image"):
        path = clean[field]
        if len(path) > 255 or not IMAGE_PATH_RE.fullmatch(path) or any(part in {".", ".."} for part in path.split("/")):
            raise ManifestError("invalid comparison image path")
    for field in COMPARISON_META_FIELDS:
        if field not in comparison:
            continue
        value = comparison[field]
        if not isinstance(value, str) or (field.endswith("_revision") and not SHA_RE.fullmatch(value)):
            raise ManifestError(f"invalid comparison {field}")
        if len(value) > TEXT_MAX:
            raise ManifestError("comparison capture note is too long")
        clean[field] = value
    return clean


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ManifestError("manifest must be an object")
    version = data.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ManifestError("invalid version")
    revision = data.get("revision")
    if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
        raise ManifestError("invalid revision")
    generated_at = data.get("generated_at")
    try:
        parsed = datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        raise ManifestError("invalid generated_at") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManifestError("generated_at must include a timezone offset")
    commits = data.get("commits")
    if not isinstance(commits, list):
        raise ManifestError("commits must be a list")
    if len(commits) > MAX_COMMITS:
        raise ManifestError(f"too many commits (max {MAX_COMMITS})")
    clean = []
    for item in commits:
        if not isinstance(item, dict):
            raise ManifestError("commit must be an object")
        sha, kind, title = item.get("sha"), item.get("type"), item.get("title")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise ManifestError("invalid commit sha")
        if kind not in KNOWN_TYPES:
            raise ManifestError("invalid commit type")
        if not isinstance(title, str) or not 1 <= len(title) <= TITLE_MAX:
            raise ManifestError("invalid commit title")
        commit = {"sha": sha, "type": kind, "title": title}
        if "requires_comparison" in item:
            if not isinstance(item["requires_comparison"], bool):
                raise ManifestError("requires_comparison must be a boolean")
            commit["requires_comparison"] = item["requires_comparison"]
        for field in ("before_text", "after_text"):
            if field in item:
                if not isinstance(item[field], str) or not item[field].strip() or len(item[field]) > TEXT_MAX:
                    raise ManifestError(f"invalid commit {field}")
                commit[field] = item[field]
        if "comparisons" in item:
            comparisons = item["comparisons"]
            if not isinstance(comparisons, list) or len(comparisons) > MAX_COMPARISONS:
                raise ManifestError(f"comparisons must be a list of at most {MAX_COMPARISONS} pairs")
            commit["comparisons"] = [_validate_comparison(pair) for pair in comparisons]
        clean.append(commit)
    return {"version": version, "revision": revision, "generated_at": generated_at, "commits": clean}


def curated_manifests(asset_dir=ASSET_DIR):
    asset_dir = Path(asset_dir)
    if not asset_dir.is_dir():
        return []
    manifests = []
    for directory in sorted(asset_dir.iterdir()):
        path = directory / "manifest.json"
        if not directory.is_dir() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ManifestError(f"cannot read curated release {directory.name}: {exc}") from None
        manifests.append(validate_manifest(data))
    return manifests


def _git(repo, *args, ok=(0,)):
    result = subprocess.run(
        ["git", "-C", os.fspath(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode not in ok:
        raise ManifestError(f"git {args[0]} failed: {result.stderr.strip() or result.returncode}")
    return result


def build_manifest(version, revision, repo=".", baseline=BASELINE, now=None):
    sha = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").stdout.strip()
    ancestor = _git(repo, "merge-base", "--is-ancestor", baseline, sha, ok=(0, 1, 128))
    if ancestor.returncode != 0:
        raise ManifestError(f"baseline {baseline} is missing or not an ancestor of {sha}")
    log = _git(
        repo,
        "log",
        "--no-merges",
        f"--max-count={MAX_COMMITS}",
        "--name-only",
        "--format=%x1e%H%x1f%s%x1f%b%x1f",
        f"{baseline}..{sha}",
    ).stdout
    notes = {
        item["sha"]: item
        for manifest in curated_manifests(Path(repo) / "hub" / "changelog_assets")
        for item in manifest["commits"]
    }
    commits = []
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        commit_sha, subject, body, files = record.split("\x1f", 3)
        if is_excluded(subject, body):
            continue
        kind, title = classify(subject)
        item = dict(notes.get(commit_sha, {"sha": commit_sha, "type": kind, "title": title}))
        if any(path.startswith(("templates/", "static/")) for path in files.splitlines()):
            item["requires_comparison"] = True
        commits.append(item)
    now = now or datetime.now(timezone.utc)
    return validate_manifest(
        {"version": version, "revision": sha, "generated_at": now.isoformat(), "commits": commits}
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate the release changelog manifest.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", default=os.fspath(MANIFEST_PATH))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--baseline", default=BASELINE)
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(args.version, args.revision, args.repo, args.baseline)
        Path(args.output).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (ManifestError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
