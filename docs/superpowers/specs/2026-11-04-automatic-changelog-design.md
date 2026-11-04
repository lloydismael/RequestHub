# Automatic changelog from releases: design

Date: 2026-11-04. Status: awaiting approval. Size: small (builds on the existing Changelogs feature).

## Intent

Notifications → Changelogs only shows entries an admin writes by hand, so it goes stale. It should fill itself: when a change ships to production, an entry appears listing what changed, with no manual step.

## Goals

- Every numbered production release (a merge to `master` that ships) that contains new commits gets one published entry, automatically.
- The entry lists those commits as New / Improved / Fixed, with maintenance commits (docs, tests, dependency bumps, CI) collapsed.
- Restarts, concurrent instances, workflow re-runs, rollbacks, and failed or skipped releases never create duplicates or lose commits.
- Admins keep the existing Management → Changelogs flow: edit, unpublish, and add before/after text and screenshots to any entry by hand.

## Non-goals

- Automatic before/after screenshots. Decided with the user: a text list is enough; screenshots are added by hand.
- Showing unreleased commits. `dev` is not deployed anywhere, so a commit appears when its release goes live.
- Backfilling releases before v55.1, using PR descriptions or labels, or a "new update" badge.

## Anti-goals

- A changelog problem blocking a release or an app start.
- New secrets, a new write endpoint, new repo permissions, or new dependencies.
- Exposing commit author names or e-mail addresses.

## Constraints found in the repo

- Production runs with `RUN_MIGRATIONS=false` (enforced by `scripts/deploy-production.sh`), and that script skips its database step when no migration, settings, or requirements file changed. The sync cannot ride on migrations; the app records the entry itself at startup.
- The image has no `.git` (`.dockerignore`), so the commit list must be produced during the build.
- `production-release.yml` is `contents: read`. The deploy job already checks out full history, so `fetch-depth: 0` on the release job follows an existing pattern.
- `hub/__init__.py` is empty, so `python -m hub.release_changelog` runs in CI without Django.
- Releases are Docker Hub tags (`vNN.N`), not git tags. Merges to `master` are GitHub merge commits from `dev`.

## Approaches

1. **Manifest baked into the image, entry created when the app starts** (chosen; my call, stated in conversation). The release job writes the commit list into the image and the app turns it into an entry on start. Trade-off: the entry appears when the new container starts, not when the merge lands; needs one command and one migration.
2. Push entries from CI into the running app. Needs a new secret and a write endpoint, and can leave a release live with no entry. Rejected.
3. Commit a changelog file back to `master` from CI. Needs write access the pipeline deliberately lacks and adds commits outside the dev → master flow. Rejected.

## Design

**Manifest.** `hub/release_changelog.py` (standard library only) runs in the release job after the version is allocated and writes `hub/release_changelog_manifest.json` (git-ignored): `version`, `revision`, `generated_at`, and `commits` as `{sha, type, title}`, newest first. Commits come from `git log --no-merges BASELINE..revision`, capped at 500. `BASELINE` is the v55.1 head, `a0f37126304c245797c346abf2df664e25fa5310`. The range is cumulative on purpose: the app skips commits it has already logged, so a release that failed, or whose container never started, is covered by the next one. If the baseline is missing or not an ancestor of the revision, the generator fails and the step warns instead of dumping history. The step is non-blocking (`continue-on-error`), and the version and SHA reach it through `env`, not inline expressions.

**Filtering and types.** Skip "Initial plan" commits and any commit whose message contains `[skip changelog]`. Classify by subject prefix and strip a recognised prefix (`Feature -`, `feat:`, `Fix -`, `fix(scope):`, `Docs -`, `build(deps):`); other subjects stay as written.

- `feature`, `feat` → New
- `fix`, `bugfix`, `hotfix`, or a leading "Fix …" → Fixed
- `docs`, `test`, `chore`, `ci`, `build`, `refactor`, `style`, or a leading "Bump " → Maintenance
- anything else, including `Enhancement -`, "Add …", "Update …" → Improved

**Sync.** `python manage.py sync_release_changelog [--manifest PATH]`. `entrypoint.sh` runs it after the migrate step and before gunicorn when the manifest file exists and `RUN_CHANGELOG_SYNC` is not `false` (default true, same true/false validation as the other flags). If it fails, the entrypoint prints a warning and the app still starts. Steps: stop if an entry already has the manifest's `revision`; drop commits already stored on any entry; stop if none remain; otherwise create one published entry. The manifest is validated (hex SHAs, bounded string lengths, known types) before anything is written, and titles are rendered escaped.

**Entry.** Title `What's new in vNN.N`; `new_version` from the manifest; `previous_version` is the newest existing entry's `new_version`; `release_date` is `generated_at` in Asia/Manila; summary is a one-line count; commits are stored on the entry.

**Model.** `Changelog` gains `release_sha` (nullable, unique; set only on automatic entries) and `changes` (JSON commit list). Two instances racing on the same release cannot both insert. Migration 0056. Changelog is already in the recovery backup list, so nothing changes there.

**Display.** Automatic entries show a "What changed" section (New / Improved / Fixed lists, Maintenance collapsed). The Before/After panels render only when the entry has comparison content, so manual entries look the same as today. The Management history marks automatic entries.

**Editing.** For automatic entries the form does not require before/after to publish. If any comparison field is filled, all four are required, so panels are never half empty. Manual entries keep today's rules.

## Decisions

- The user decided: text list only, screenshots by hand.
- My calls (stated in conversation, not rejected): publish immediately; one entry per release; commit subjects shown as written; the `[skip changelog]` escape hatch; Maintenance collapsed; the cumulative range and 500-commit cap; entry title and summary wording.

## Risks

- Every signed-in user sees commit subjects as written. Remedies: `[skip changelog]` before merging, or unpublish the entry afterwards.
- The Management UI has no delete. Deleting an automatic entry directly in the database would let its commits reappear in the next release's entry, so unpublish instead.
- The first release after this ships logs everything since v55.1, including these design and plan commits (docs subjects land under Maintenance).

## Left to the builder

The exact classification regex, markup and CSS, summary wording, test layout, and README / CD-doc wording.
