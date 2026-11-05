# Request Hub cleanup and dependency alignment

## Intent

Reduce maintenance burden by removing code that has no active application
references, while making the local and container dependency sets agree. The
goal is a smaller, clearer codebase and a reproducible install—not a broad
refactor or an unmeasured performance claim.

## Goals

- Align `requirements.lock` with the declared constraints in `requirements.txt`,
  including the user-selected Django 6.1 series.
- Remove confirmed unused imports and private helper chains.
- Retire the unused request detail implementation and template while retaining
  the existing permanent redirect URL for bookmarks and callers.
- Retire the unused `LandingView`, but preserve `landing.html` and its existing
  hygiene test.
- Update directly affected README references and runtime version documentation.
- Verify retained request, SQR, Outlook, recovery and release behavior with
  targeted tests and safe local checks.

## Non-goals and anti-goals

- Do not change request lifecycle, permissions, authentication, emails, SQR,
  recovery, deployment configuration or API/URL behavior.
- Do not change the disabled profile-completion middleware or its setting.
- Do not delete ambiguous template variants, incident repair/forensics scripts,
  quotation scratch files, or other operator-owned artifacts.
- Do not remove migrations, models, current static assets or management
  commands.
- Do not make unrelated style or performance refactors, publish images, deploy,
  or touch production data.
- Do not claim runtime or image-size improvement without measurements.

## Approaches

1. **Conservative cleanup and dependency alignment — selected by the user.**
   Remove only confirmed unused code, preserve URL compatibility, align the
   lockfile to the already-declared Django 6.1 constraints, and verify focused
   behavior. This addresses the audit findings without changing policy or
   operational entry points.
2. **Extended retirement.** Also remove orphan template variants and scratch
   artifacts. This risks deleting templates or scripts used by operations,
   tests, database-configured forms, or future workflows; it is deferred.
3. **Inventory only.** Make no code/dependency changes. This leaves known dead
   code and contradictory install instructions in place, so it is not selected.

## Decisions and constraints

- The user selected conservative scope and Django 6.1.
- Keep the existing Python 3.12 runtime: both the Dockerfile and README already
  target Python 3.12. The user selected Django 6.1, whose official installation
  FAQ lists Python 3.12 as supported:
  <https://docs.djangoproject.com/en/6.1/faq/install/#what-python-version-can-i-use-with-django>.
- Keep all direct dependency ranges in `requirements.txt`; resolve and pin
  compatible versions in `requirements.lock`, without adding dependencies or
  loosening unrelated constraints. Use the repository's existing lock format
  and Docker install path.
- Keep the `request-detail` named URL and its permanent redirect to
  collaborative request management; remove only the unreachable old view and
  its template.
- Preserve `ProfileCompletionMiddleware`, request-reassignment behavior,
  recovery routes, Outlook delegated draft implementation, and all operator
  scripts.
- Do not load `.env`, use production credentials, or connect to a production
  database. Use isolated local test settings and a disposable PostgreSQL
  database for database-backed/full-suite tests. If one is unavailable, run
  only safe focused checks and report the full-suite validation as blocked.
  Do not deploy or push.
- The current branch is `dev`; perform implementation in an isolated worktree
  after design approval.

## Implementation boundaries

The expected implementation is limited to the requirements and lock files,
the affected Python view/import code, the retired request-detail template,
focused tests, and directly related README text. Before deleting each symbol,
confirm references across tracked code, tests, URL registration, framework
registration and template selection. Do not infer dead code from a name or
text-search absence alone.

The exact lock-generation command and available disposable test database are
implementation-time checks. If dependency resolution cannot meet the declared
constraints or an existing test requires production data, stop that subtask
and report the blocker rather than weakening a constraint or contacting
production.

## Verification

- Re-resolve the declared requirements and run `pip check` in an isolated
  Python 3.12 environment; verify the generated pins satisfy every direct
  constraint.
- Run Django system checks, migration consistency checks, focused regression
  tests for the retained redirect/login behavior and affected business flows,
  and the repository's three Node regression suites.
- Run the full Django suite only against a disposable database; never use the
  application's production configuration.
- Build the container locally without publishing it, if the environment has
  the required tooling and resources.
- Review the final diff for unrelated edits and preserved user changes.
