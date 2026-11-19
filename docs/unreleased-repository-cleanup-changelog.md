# Repository cleanup — unreleased

| Area | Before | After |
| --- | --- | --- |
| Scratch files | One-off inspectors, a rewrite script, a patch, and empty scripts and tests were tracked. | Removed seven scratch artifacts and three empty test files. Incident scripts and all nonempty tests remain. |
| Documentation | Historical plans and audits lived alongside current guides or at the repository root. | Moved six documents into [the archive](archive/) and pinned historical links to the revisions they describe. |
| Python | Unused dashboard, assistant, and SQR helpers remained alongside their replacements. | Removed the unused code without changing the active query, response, or import paths. |
| Standalone tests | Node and Playwright tests shared a folder with incident scripts. | Moved 18 Node suites and one opt-in Playwright suite to [tests](../tests/). Updated fixture paths, Django launchers, CI, the Docker build context, and the historical verification instructions. |
| Notifications | The general view module owned the list, actions, and toast polling. | Moved six views to [notification_views.py](../hub/notification_views.py). Preserved routes, permissions, redirects, and compatibility imports. |
| Template and static files | An unused email template, two empty seasonal sign-in assets, and four unreferenced decorative images were tracked. | Removed only those files. The active sign-in and acknowledgement flows are unchanged. |
| SQR browser code | The tracker contained an unused record-matching wrapper. | Removed the wrapper; the active filter path still uses `core.filterRecords`. |

The [packaged comparisons](../hub/changelog_assets/repository-cleanup/manifest.json) show exact first-parent and change revisions. They are technical evidence, not screenshots of a user-facing change. The release generator matches them by commit SHA; this note does not publish a numbered release.
