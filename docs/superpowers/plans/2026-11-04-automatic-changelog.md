# Automatic Changelog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every numbered production release that ships new commits gets one published entry under Notifications → Changelogs, with no manual step.

**Architecture:** The release job runs a stdlib-only generator that writes a git-ignored commit manifest into the image. At container start `entrypoint.sh` runs `manage.py sync_release_changelog`, which validates the manifest and creates one `Changelog` per release, skipping commits already logged. A unique nullable `release_sha` makes racing starts safe. Any failure warns and never blocks a release or a start.

**Tech Stack:** Python 3.12-compatible stdlib (generator), Django (model, command, templates), POSIX sh, GitHub Actions. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-04-automatic-changelog-design.md` (status: approved)

## Global Constraints

Copied from the spec. Every task includes them.

- Anti-goals: "A changelog problem blocking a release or an app start." "New secrets, a new write endpoint, new repo permissions, or new dependencies." "Exposing commit author names or e-mail addresses."
- Commits: `git log --no-merges BASELINE..revision`, capped at 500. `BASELINE` is `a0f37126304c245797c346abf2df664e25fa5310`. The range is cumulative on purpose. If the baseline is missing or not an ancestor, the generator fails and the step warns. The step is `continue-on-error`; version and SHA reach it through `env`, not inline expressions.
- Skip "Initial plan" commits and any commit whose message contains `[skip changelog]`.
- Types: `feature`, `feat` → New. `fix`, `bugfix`, `hotfix`, or a leading "Fix …" → Fixed. `docs`, `test`, `chore`, `ci`, `build`, `refactor`, `style`, or a leading "Bump " → Maintenance. Anything else, including `Enhancement -`, "Add …", "Update …" → Improved. A recognised prefix (`Feature -`, `feat:`, `Fix -`, `fix(scope):`, `Docs -`, `build(deps):`) is stripped; other subjects stay as written.
- Sync: `entrypoint.sh` runs it after the migrate step and before gunicorn when the manifest file exists and `RUN_CHANGELOG_SYNC` is not `false` (default true, same true/false validation as the other flags). If it fails, the entrypoint prints a warning and the app still starts. Stop if an entry already has the manifest's `revision`; drop commits already stored on any entry; stop if none remain; otherwise create one published entry. The manifest is validated (hex SHAs, bounded string lengths, known types) before anything is written, and titles are rendered escaped.
- Entry: title `What's new in vNN.N`; `new_version` from the manifest; `previous_version` is the newest existing entry's `new_version`; `release_date` is `generated_at` in Asia/Manila; summary is a one-line count; commits are stored on the entry.

## Review Focus

Failure modes the spec implies; the test that pins each is named in the owning task.

1. A subject that is only a prefix (`Fix -`, `feat:`) keeps its original text instead of becoming blank. (Task 1)
2. Hostile or odd subjects: HTML, non-ASCII, over 200 characters, one unbroken long word. Escaped, intact, capped at 200, wrapping on a 375px screen. (Tasks 1, 4, 7)
3. The same release synced repeatedly, from a rebuilt image, by two racing containers, or after a rollback creates no duplicate and no repeated commit. (Task 3)
4. A corrupt manifest or a non-ancestor baseline fails with a clear message and never blocks the release or the app start. (Tasks 1, 3, 6)
5. A UTC time late on one day is the next Manila day; a future-dated manual entry never becomes the "previous version". (Task 3)

## Execution Notes

- Windows PowerShell, repo root `C:\Users\LloydS\Dev\VSCODE\REQ-HUB-GPT5-CODEX`, branch `dev`. Commit locally; never push. Stage only the listed paths, never `git add -A`.
- Commit recipe: `git commit -m "<subject>" -m "[skip changelog]" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"`. Tasks 0 and 6 drop the middle `-m`: their subjects are user-facing release notes.
- Tests: `.\.venv\Scripts\python.exe manage.py test <labels> --settings=request_hub.settings_ci --noinput`, output to a log outside the repo. DB-backed labels take 2–3 minutes. The venv is Python 3.14; avoid syntax newer than 3.12.
- Line endings: working tree CRLF, index LF. After each edit `git diff --stat` must show only intended lines, and `git ls-files --eol entrypoint.sh` must stay `w/lf`.
- `hub/test_changelogs.py`: `ChangelogTests.setUp` deletes the rows migration 0055 seeds and logs in an admin; helpers `entry(**o)`, `payload(**o)`, `screenshot()`; `self.notifications` with `{"tab": "changelogs"}` is the feed; form URLs are `reverse("hub:changelog-edit", args=[pk])` and `reverse("hub:changelog-create")`. Any new `TestCase` that creates entries must delete the seeded rows in `setUp`.

---

### Task 0: Commit the v55.0 → v55.1 seed already in the working tree

**Files:** `README.md`, `hub/test_changelogs.py`, `hub/changelog_assets/` (4 JPEGs), `hub/migrations/0055_v55_1_release_changelogs.py`.

- [ ] **Step 1:** Run `hub.test_changelogs` → `OK`.
- [ ] **Step 2:** Commit `Feature - Changelog entries for v55.0 to v55.1`, staging exactly those four paths.

---

### Task 1: Manifest generator

**Files:** Create `hub/release_changelog.py`, `hub/test_release_changelog.py`. Modify `.gitignore` (add `hub/release_changelog_manifest.json`).

**Interfaces — Produces** (stdlib only, no Django import):

```python
GROUPS = (("new", "New"), ("improved", "Improved"), ("fixed", "Fixed"), ("maintenance", "Maintenance"))
BASELINE = "a0f37126304c245797c346abf2df664e25fa5310"
MAX_COMMITS = 500          # read at call time so tests can patch it
MANIFEST_PATH = Path(__file__).with_name("release_changelog_manifest.json")
class ManifestError(ValueError): ...
def classify(subject: str) -> tuple[str, str]: ...      # (type, title); title capped at 200 (199 chars + "…")
def is_excluded(subject: str, body: str) -> bool: ...
def validate_manifest(data: object) -> dict: ...        # normalized copy, known keys only
def build_manifest(version: str, revision: str, repo: str | os.PathLike = ".",
                   baseline: str = BASELINE, now: datetime | None = None) -> dict: ...
def main(argv: list[str] | None = None) -> int: ...     # --version --revision [--output --repo --baseline]
```

Manifest keys in order: `version`, `revision` (resolved full SHA), `generated_at` (UTC ISO with offset), `commits` (`{sha, type, title}`, newest first).

- [ ] **Step 1: Write failing tests** in `hub/test_release_changelog.py` (`SimpleTestCase`; the temp-repo helper commits with `git -c user.name=t -c user.email=t@t.test -c commit.gpgsign=false`):
  - `test_prefixes_and_fallbacks` — `Feature - Changelogs`→(new, `Changelogs`); `feat: add export`→(new, `Add export`); `Fix - SQR email`→(fixed, `SQR email`); `fix(api)!: crash`→(fixed, `Crash`); `Docs - plan`→(maintenance, `Plan`); `build(deps): bump x`→(maintenance, `Bump x`); `Enhancement - Faster list`→(improved, `Faster list`); `Fixed typo in header`→(fixed, unchanged); `Bump django from 5.1 to 5.2`→(maintenance, unchanged); `SQR - Email Approval Automation` and `Auto-assign engineers`→(improved, unchanged). **Review Focus 1:** `Fix -`→(fixed, `Fix -`) and `feat:`→(new, `feat:`). **Review Focus 2:** a 250-character subject → a 200-character title ending `…`.
  - `test_exclusions` — `is_excluded` is true for `Initial plan`, `initial PLAN`, a body containing `[Skip Changelog]`, and a subject containing `[skip changelog]`; false for `Initial plan for exports`.
  - `test_validate_manifest` — accepts a valid manifest and drops unknown keys; raises `ManifestError` for a non-dict, version `v1 2` or 51 characters, an uppercase or 39-character SHA (revision and commit), a naive `generated_at`, an unknown type, an empty or 201-character title, and more than `MAX_COMMITS` commits (patch it to 2).
  - `test_commits_since_baseline_newest_first` — baseline, then `Feature - A`; then on `main` a commit `Docs - X` with `[skip changelog]` in its body, and a side branch from `Feature - A` holding only `Initial plan`, merged with `--no-ff`; then `Fix - C`; `now` is a timezone-aware datetime passed in. Expect exactly `[C (fixed), A (new)]` (merge, `Initial plan` and `[skip changelog]` all dropped), 40-hex SHAs, `revision` resolved from `"HEAD"`, `generated_at == now.isoformat()`, and no author name or e-mail anywhere in `json.dumps(manifest)`.
  - `test_non_ascii_subject_round_trips` — `Fix - café 日本語` stays `café 日本語` (Windows encoding trap). **Review Focus 2.**
  - `test_cap_keeps_newest` — `MAX_COMMITS` patched to 2 with 3 commits → the 2 newest.
  - `test_baseline_not_ancestor_or_missing_fails` — a baseline on a side branch, and an unknown SHA, each raise `ManifestError` whose message contains `ancestor`. **Review Focus 4.**
  - `test_main_writes_json_and_fails_cleanly` — success returns 0 and writes UTF-8 JSON (`ensure_ascii=False`, `indent=2`) with the keys above; a bad baseline returns 1, prints `error: …` to stderr, and leaves no output file.
- [ ] **Step 2:** Run `hub.test_release_changelog` → FAIL (`ModuleNotFoundError: hub.release_changelog`).
- [ ] **Step 3:** Implement `hub/release_changelog.py`.
  - A prefix is one word, an optional `(scope)` and `!`, then `:` or a spaced `-`/`–`/`—`, case-insensitive. Words: `feature|features|feat` new; `fix|bugfix|hotfix` fixed; `docs|doc|test|tests|chore|ci|build|refactor|style` maintenance; `enhancement|improvement|perf` improved. Capitalise the first letter of the remainder; if nothing remains, keep the original subject.
  - With no recognised prefix, a leading `Fix `/`Fixes `/`Fixed ` is fixed, a leading `Bump ` is maintenance, the rest improved. A hyphenated word such as `Auto-assign` is not a prefix.
  - Run git via `subprocess.run([...], capture_output=True, text=True, encoding="utf-8")` with `-C repo`: `rev-parse --verify <rev>^{commit}`, `merge-base --is-ancestor`, and `log --no-merges --max-count=MAX_COMMITS --format=%H%x1f%s%x1f%b%x1e BASELINE..sha`. Never read author fields. A non-zero git exit becomes `ManifestError`; `build_manifest` returns `validate_manifest(...)` of its result.
  - Validation: version `[A-Za-z0-9._-]{1,50}`; SHAs 40 lowercase hex; `generated_at` timezone-aware ISO; titles 1–200 characters.
- [ ] **Step 4:** Run the module → `OK`. `git check-ignore hub/release_changelog_manifest.json` prints the path. The release job installs nothing, so also run `.\.venv\Scripts\python.exe -c "import sys; sys.modules['django'] = None; import hub.release_changelog"` → exit 0 (the generator must not import Django or `hub.models`).
- [ ] **Step 5:** Commit `Add release changelog manifest generator` (skip tag): `.gitignore`, `hub/release_changelog.py`, `hub/test_release_changelog.py`.

---

### Task 2: Model fields

**Files:** Modify `hub/models.py` (`Changelog`, line 24), `hub/test_changelogs.py`. Create `hub/migrations/0056_changelog_release_fields.py` (generated).

**Interfaces — Consumes:** `GROUPS` (Task 1; `models.py` does `from .release_changelog import GROUPS`). **Produces:**

```python
release_sha = models.CharField(max_length=40, unique=True, null=True, editable=False)
changes = models.JSONField(default=dict, editable=False)   # {"commits": [{"sha", "type", "title"}]}
@property is_automatic -> bool        # bool(release_sha)
@property has_comparison -> bool      # any of before_text, after_text, before_image, after_image
@property change_groups -> list[dict] # [{"key", "label", "titles"}] in GROUPS order, empty groups omitted
```

- [ ] **Step 1: Write failing tests** in `ChangelogTests`, plus helper `automatic(**o)`: a published entry with `previous_version=""`, blank before/after fields, `release_sha="c"*40`, `new_version="v56.0"`, and `changes` holding one commit each titled `Shiny export` (new), `Quicker list` (improved), `Typo fix` (fixed), `Dependency bump` (maintenance).
  - `test_release_sha_is_unique_but_nullable` — two entries with `release_sha=None` coexist; a duplicate SHA raises `IntegrityError`.
  - `test_is_automatic_and_has_comparison` — `entry()` is not automatic and has a comparison; `automatic()` is automatic with none; setting only `after_text` makes `has_comparison` true.
  - `test_change_groups` — commits stored as maintenance, fixed, new, new, improved → keys `["new", "improved", "fixed", "maintenance"]`, labels from `GROUPS`, both `new` titles in stored order.
  - `test_change_groups_tolerates_malformed_changes` — `{}`, `[]`, `{"commits": "x"}` and `{"commits": [1, {"type": "zzz", "title": "t"}, {"type": "new"}]}` all give `[]`.
- [ ] **Step 2:** Run `hub.test_changelogs` → FAIL (unknown field / attribute).
- [ ] **Step 3:** Add the fields and properties. `change_groups` skips items that are not dicts, have an unknown type, or lack a string title.
- [ ] **Step 4:** `.\.venv\Scripts\python.exe manage.py makemigrations hub --name changelog_release_fields` → `0056` holds exactly two `AddField` operations depending on `0055`.
- [ ] **Step 5:** Run `hub.test_changelogs accounts.test_recovery` → `OK`; `makemigrations --check --dry-run` → `No changes detected`.
- [ ] **Step 6:** Commit `Add release fields to changelog entries` (skip tag): `hub/models.py`, `hub/test_changelogs.py`, the migration.

---

### Task 3: Sync service and command

**Files:** Create `hub/changelog_sync.py`, `hub/management/commands/sync_release_changelog.py`, `hub/test_changelog_sync.py`.

**Interfaces — Consumes:** `validate_manifest`, `ManifestError`, `GROUPS`, `MANIFEST_PATH` (Task 1); `Changelog.release_sha`, `.changes` (Task 2); `MANILA_TZ` from `hub.models`. **Produces:**

```python
def load_manifest(path: Path = MANIFEST_PATH) -> dict | None: ...  # None if the file is absent; ManifestError if unreadable, not JSON, or invalid
def summarize(commits: list[dict]) -> str: ...
def _already_synced(revision: str) -> bool: ...                    # tests patch this
def sync_release(manifest: dict) -> Changelog | None: ...          # manifest is already validated
# manage.py sync_release_changelog [--manifest PATH]
```

Rules beyond Global Constraints:
- Logged SHAs come from the `changes` of every entry with a `release_sha`, published or not, read defensively; commits repeated inside the manifest collapse to one.
- Create inside `transaction.atomic()`; an `IntegrityError` returns `None`.
- `previous_version` is the `new_version` of the first entry (default ordering) with `release_date` on or before the new entry's date and `new_version != version`; `""` if none.
- `release_date = datetime.fromisoformat(generated_at).astimezone(MANILA_TZ).date()`.
- Summary: `This release includes {parts} {change|changes}.` Parts are `N <group label lowercased>` in `GROUPS` order, zero counts omitted, joined `a, b and c`; `change` only when the total is 1.
- The command prints `No release manifest at {path}; nothing to sync.` and exits 0 when the file is absent; raises `CommandError("Invalid release manifest: …")` on `ManifestError`; prints `Logged N change(s) for vX.` or `Nothing new to log for vX.`.

- [ ] **Step 1: Write failing tests** in `hub/test_changelog_sync.py` (`TestCase`; `setUp` deletes the seeded rows). Helpers: `manifest(version="v56.0", revision="a"*40, generated_at="2026-11-04T10:00:00+00:00", commits=...)` and `commit(n, type, title)` with SHA `f"{n:040x}"`.
  - `test_creates_one_published_entry` — 2 new, 1 improved, 1 fixed → title `What's new in v56.0`, `new_version "v56.0"`, `previous_version ""`, published, `release_sha == revision`, `changes == {"commits": [...]}`, summary `This release includes 2 new, 1 improved and 1 fixed changes.`
  - `test_summary_wording` — one improved commit → `This release includes 1 improved change.`; one new and one fixed → `This release includes 1 new and 1 fixed changes.`; three maintenance → `This release includes 3 maintenance changes.`
  - `test_same_revision_again_creates_nothing` — a second call, and a third with `version="v56.1"` and the same revision, return `None`; one entry total. **Review Focus 3.**
  - `test_new_revision_logs_only_unlogged_commits` — second manifest (new revision) repeating the first commits plus one → entry 2 holds only the new commit; its `previous_version == "v56.0"`.
  - `test_rollback_creates_nothing` — an older revision whose commits are all logged, and a manifest with an empty commit list → `None`. **Review Focus 3.**
  - `test_unpublished_entry_commits_are_not_relogged` — unpublish entry 1, then a later release with the same commit plus a new one → the new entry lists only the new one.
  - `test_duplicate_shas_in_manifest_collapse`.
  - `test_racing_insert_is_swallowed` — an existing entry with `release_sha=revision` and `changes={}`, `_already_synced` patched to `False`: `sync_release` returns `None`, does not raise, and the revision still has one entry. (`changes={}` keeps the commit filter from short-circuiting before the insert.) **Review Focus 3.**
  - `test_previous_version_rules` — manual `v55.1` dated 2026-11-01, manual `v99.0` dated 2030-01-01, manual `v56.0` dated 2026-11-03; syncing `v56.0` dated 2026-11-04 → `previous_version == "v55.1"`. **Review Focus 5.**
  - `test_release_date_uses_manila_day` — `2026-11-04T17:30:00+00:00` → `date(2026, 11, 5)`; `2026-11-04T15:59:00+00:00` → `date(2026, 11, 4)`. **Review Focus 5.**
  - `test_command_without_manifest_exits_cleanly`, `test_command_rejects_invalid_manifest` (invalid JSON; a 39-character SHA — `CommandError` starting `Invalid release manifest`, no entry created), `test_command_reports_counts` (`Logged 2 change(s) for v56.0.` then `Nothing new to log for v56.0.`) — use `call_command(..., "--manifest", path, stdout=...)` with a temp directory. **Review Focus 4.**
- [ ] **Step 2:** Run `hub.test_changelog_sync` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3:** Implement both files; the command stays a thin wrapper over `load_manifest` and `sync_release`.
- [ ] **Step 4:** Run `hub.test_changelog_sync hub.test_changelogs` → `OK`.
- [ ] **Step 5:** Commit `Add release changelog sync command` (skip tag): the three new files.

---

### Task 4: Entry card shows the changes

**Files:** Modify `templates/hub/partials/changelog_entry.html`, `static/hub/changelogs.css`, `hub/test_changelogs.py`.

**Interfaces — Consumes:** `Changelog.change_groups`, `.has_comparison` (Task 2).

Markup: the version chip shows `previous → new` only when `previous_version` is set, otherwise just `new_version`. A `.cl-changes` block titled "What changed" appears when `change_groups` is non-empty. New / Improved / Fixed are `<section class="cl-change-group">` with a heading, a `.cl-count` and a `<ul>`. Maintenance is `<details class="cl-change-group">`, closed by default, with its count in the summary. `.cl-comparison` renders only when `has_comparison`. Plain `{{ }}` escaping, no `|safe`. CSS: about ten lines for `.cl-changes`, `.cl-change-group`, `.cl-count`, with `overflow-wrap: anywhere` on list items.

- [ ] **Step 1: Write failing tests** in `ChangelogTests` (feed GET):
  - `test_automatic_entry_lists_changes_by_group` — the response has `What changed`, the four titles from `automatic()`, `<details class="cl-change-group">` without `open`, no `cl-comparison`, no `bi-arrow-right`, and `v56.0`.
  - `test_change_titles_are_escaped` — a title `<script>alert(1)</script>` appears as `&lt;script&gt;alert(1)&lt;/script&gt;` and never raw. **Review Focus 2.**
  - `test_long_unbroken_title_renders_whole` — a title of 200 `x` characters appears in full. **Review Focus 2.**
  - `test_manual_entry_is_unchanged` — a published `entry()` still shows `cl-comparison` and `bi-arrow-right`, and no `What changed`.
  - `test_changes_and_comparison_coexist` — an automatic entry with all four comparison fields shows both.
- [ ] **Step 2:** Run `hub.test_changelogs` → FAIL on the new tests only.
- [ ] **Step 3:** Edit the template and CSS as described.
- [ ] **Step 4:** Run `hub.test_changelogs` → `OK`.
- [ ] **Step 5:** Commit `Show release changes on the changelog card` (skip tag): the three files.

---

### Task 5: Admin editing of automatic entries

**Files:** Modify `hub/changelog_forms.py`, `templates/hub/partials/changelog_management.html`, `hub/test_changelogs.py`.

**Interfaces — Consumes:** `Changelog.is_automatic` (Task 2).

Behaviour:
- `ChangelogForm.__init__`: when `self.instance.is_automatic`, `previous_version` is not required.
- `clean()`: the four-field publish requirement is skipped only when the instance is automatic and none of the four fields has a value. Otherwise it applies unchanged.
- Management template: a `<span class="badge text-bg-info">Automatic</span>` beside the status badge; the history line shows only `new_version` when `previous_version` is blank (no stray arrow); the editor line "Publishing requires both descriptions and screenshots." stays for manual entries, and for automatic entries reads "Automatic entries can be published as they are. Add all four Before / After fields to show a comparison." Leave the "Preview comparison" label alone.

- [ ] **Step 1: Write failing tests** in `ChangelogTests`:
  - `test_automatic_entry_saves_published_without_previous_version_or_comparison` — POST to the edit URL with `payload(previous_version="", before_text="", after_text="", title="Edited", is_published="on")` → 302; after reload the title is `Edited`, still published, and `release_sha` and `changes` are unchanged.
  - `test_partial_comparison_on_automatic_entry_is_rejected` — `before_text="x"` only → 200, and `after_text`, `before_image`, `after_image` each carry `This field is required before publishing.`
  - `test_new_manual_entry_still_requires_previous_version` — POST create with `previous_version=""` → 200 with `This field is required.`
  - `test_management_marks_automatic_entries` — with one automatic (`v56.0`) and one manual (`v1` → `v2`) entry, the changelogs tab contains `<span class="badge text-bg-info">Automatic</span>` exactly once, contains `v1 &rarr; v2`, and never `&rarr; v56.0`. GET of the automatic entry's edit URL contains `Automatic entries can be published as they are.` and not `Publishing requires both descriptions and screenshots.`; the manual entry's edit URL is the reverse.
- [ ] **Step 2:** Run `hub.test_changelogs` → FAIL on the new tests only.
- [ ] **Step 3:** Edit the form and template as described.
- [ ] **Step 4:** Run `hub.test_changelogs` → `OK`.
- [ ] **Step 5:** Commit `Let admins edit automatic changelog entries` (skip tag): the three files.

---

### Task 6: Pipeline wiring and docs

**Files:** Modify `.github/workflows/production-release.yml`, `entrypoint.sh`, `README.md` (Changelogs section), `docs/cd-workflow-testing.md`.

- [ ] **Step 1: Workflow.** In the `release-image` job, add `fetch-depth: 0` to "Check out release commit". Between "Allocate next numbered tag" and "Set up Docker Buildx" add a step "Generate release changelog manifest" with `continue-on-error: true`, `env` of `RELEASE_VERSION: ${{ steps.version.outputs.version }}` and `RELEASE_REVISION: ${{ github.event.workflow_run.head_sha }}`, and `run: python -m hub.release_changelog --version "$RELEASE_VERSION" --revision "$RELEASE_REVISION"`. No `${{ }}` inside `run`.
- [ ] **Step 2: Entrypoint.** After the `RUN_MIGRATIONS` case and before `if [ "$#" -gt 0 ]`, add a `RUN_CHANGELOG_SYNC` case in the existing style: `true` runs `python manage.py sync_release_changelog || echo "Release changelog sync failed; continuing startup" >&2` only when `hub/release_changelog_manifest.json` exists; `false` does nothing; anything else prints `RUN_CHANGELOG_SYNC must be true or false` and exits 1.
- [ ] **Step 3: Verify the entrypoint** with a throwaway shim outside the repo (not committed). A temp directory holds a copy of `entrypoint.sh`, an executable fake `python` script placed first on `PATH`, and an optional `hub/release_changelog_manifest.json`. The fake appends its arguments to `calls.log`, and exits 1 only when called as `python manage.py sync_release_changelog` while `FAKE_SYNC_FAIL=1` is set. Run `RUN_COLLECTSTATIC=false RUN_MIGRATIONS=false sh ./entrypoint.sh echo started` from that directory with Git Bash (`C:\Program Files\Git\bin\bash.exe`). **Review Focus 4.** Expected:

  | Manifest file | Environment | Result |
  |---|---|---|
  | present | none | `calls.log` has `manage.py sync_release_changelog`; stdout `started`; exit 0 |
  | present | `FAKE_SYNC_FAIL=1` | stderr has `Release changelog sync failed; continuing startup`; stdout `started`; exit 0 |
  | absent | none | no sync call; `started`; exit 0 |
  | present | `RUN_CHANGELOG_SYNC=false` | no sync call; `started` |
  | present | `RUN_CHANGELOG_SYNC=maybe` | exit 1; stderr `RUN_CHANGELOG_SYNC must be true or false` |

  Production runs the script under Alpine busybox `sh`, not Git Bash. If the Docker daemon is up, also run `docker run --rm -v "${PWD}:/w:ro" python:3.12.13-alpine3.24 sh -n /w/entrypoint.sh` → no output, exit 0. Otherwise note in the Task 7 report that this check was skipped.

- [ ] **Step 4: Verify the workflow and image inputs.** Load the workflow with PyYAML installed into a temp `--target` directory (not the venv, not `requirements`) and assert: it parses; the `release-image` steps run Allocate → Generate manifest → Buildx; checkout has `fetch-depth: 0`; the new step has `continue-on-error: true`, both `env` keys, and no `${{` in `run`. Also confirm `.dockerignore` has no rule matching `hub/release_changelog_manifest.json` (no `hub` or `json` pattern today), so `COPY . .` bakes the manifest in.
- [ ] **Step 5: Docs.** README: replace the "Publishing requires both descriptions and screenshots" sentence with manual-vs-automatic wording, and replace the closing "To log a later release the same way…" sentence with how automatic entries appear and how to suppress or edit them. `docs/cd-workflow-testing.md`: add `## Release changelog` before `## Release speed and timing`, covering the non-blocking step and `fetch-depth: 0`, the baseline and cumulative range, `[skip changelog]`, `RUN_CHANGELOG_SYNC`, that the entry appears when the new container starts, unpublish instead of deleting, and that the first release after merge lists everything since v55.1.
- [ ] **Step 6:** Commit `Feature - Automatic changelog entries for each release` (visible, no skip tag): the four files.

---

### Task 7: End-to-end verification and review

- [ ] **Step 1:** `python -m hub.release_changelog --version v56.0 --revision HEAD` → exit 0; the manifest is not listed by `git status --short`; its first commit is `HEAD`; no `Initial plan` or `[skip changelog]` commit appears; docs commits are typed `maintenance`.
- [ ] **Step 2:** `manage.py migrate`, then `manage.py sync_release_changelog` → `Logged N change(s) for v56.0.`; run it again → `Nothing new to log for v56.0.`
- [ ] **Step 3: Browser QA** (load the browser tools with `tool_search_tool`; run `collectstatic` first; sign in as an admin, creating a throwaway one if needed). At 1280px and 375px check: the v56.0 card shows the version chip without an arrow, the "What changed" groups, and Maintenance collapsed; the v55.1 cards still show their comparisons with images; `document.documentElement.scrollWidth <= innerWidth` at 375px (**Review Focus 2**); Management → Changelogs shows the Automatic badge, and saving the entry without a previous version works. Capture screenshots as evidence.
- [ ] **Step 4: Clean up.** Delete the QA entry (`Changelog.objects.filter(release_sha__isnull=False).delete()` in `manage.py shell`), any throwaway user, `hub/release_changelog_manifest.json`, and stop any dev server you started.
- [ ] **Step 5: Full verification.** `makemigrations --check --dry-run` → `No changes detected`; `manage.py check` → no issues; the full suite → `OK` (run async, log outside the repo). A failure outside the changed modules must be reproduced at `86df22b` in a temporary `git worktree` before it is called pre-existing.
- [ ] **Step 6: Review.** Request a code review per `requesting-code-review` with `BASE_SHA` = the plan commit and `HEAD_SHA` = `HEAD`, passing the spec, this plan's Review Focus, and the task summaries. Fix Critical and Important findings; note Minor ones.
- [ ] **Step 7: Report to the user:** commits made, nothing pushed; live after `dev` merges to `master` and the release workflow runs; the first release lists everything since v55.1; entries appear when the new container starts, not at merge time; a v2 recovery backup made before migration 0056 will not restore (`Missing required fields in hub.changelog`), as after earlier field additions; remedies are `[skip changelog]` before merging or unpublishing afterwards.
