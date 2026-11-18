# Unreleased: chart consistency

## Improved

- Standardized 27 charts across Dashboard (Requestor and Engineer), Activity Logs, Reports (Operational and Activity Logs), SQR Reports, and Project Performance Tracking (PPT). The expanded Reports chart uses the same styles and values as its thumbnail.
- Added shared light/dark and account-theme colors, opaque chart surfaces, readable horizontal comparisons and rankings, and distinct line styles. Exact values are available by keyboard and touch, including when Chart.js is unavailable. Missing values display as an em dash; zero remains zero.
- Kept the original filters, permissions, metric definitions, and exports. Operational Reports still expand to the full engineer and account-manager rankings and download as PNG. Report activity values retain decimal hours.
- SQR Reports retain business stage order and exact Philippine peso values. The monthly plot breaks at missing observations rather than drawing a line between nonadjacent months. Live refresh retains open chart values and keyboard focus.
- PPT still renders native bars without Chart.js. Saved assessments show a separate striped **Not assessed** segment, with exact project counts for Schedule, Budget/Effort, and Quality.

## Maintenance and verification

- Consolidated data presentation, exact-value tables, themed chart updates, and layout into shared assets loaded only on affected views. Dashboard and Activity Logs still mount charts only when their graph tab is shown.
- Added focused JavaScript and Chromium regressions for exact values, Chart.js failure, lazy tabs, in-place retheming, expanded chart export, SQR refresh, and PPT assessment counts. Documented chart conventions and local test assets in the [README](../README.md#chart-conventions).
- Corrected the PPT header test fixture to enable the classic tracker option it exercises. The combined Django/Chromium run passed 95 tests, and the focused Node run passed 17. A real chart screenshot pair passed the Notifications comparison and draft-image access test against a disposable database. No real release was published.

## Before/after evidence

Matched synthetic Chromium captures cover both Dashboard roles, Activity Logs, both Reports views, SQR Reports, both PPT charts, and expanded Reports. The charts were rendered from the audited parent `d2ab79d051cc7305fe2174c7dec9f8b0ad1c2e94` and the uncommitted implementation, not from the mockup. Captures and metadata are attached to the implementation session. The after state has no commit SHA yet.

After an authorized commit, capture or bind evidence to the exact before/after revisions and package it with the [release changelog workflow](cd-workflow-testing.md#release-changelog). Keep the entry in draft until the evidence is accepted. After authorized publication, verify the image pairs in **Notifications → Changelogs** as an ordinary signed-in reader.
