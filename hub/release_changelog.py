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
        clean.append({"sha": sha, "type": kind, "title": title})
    return {"version": version, "revision": revision, "generated_at": generated_at, "commits": clean}


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
        "--format=%H%x1f%s%x1f%b%x1e",
        f"{baseline}..{sha}",
    ).stdout
    commits = []
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        commit_sha, subject, body = record.split("\x1f", 2)
        if is_excluded(subject, body):
            continue
        kind, title = classify(subject)
        commits.append({"sha": commit_sha, "type": kind, "title": title})
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
