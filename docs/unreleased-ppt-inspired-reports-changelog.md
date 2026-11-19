# Unreleased: PPT-inspired Reports

## Improved

- Operational and Activity Logs now use the Project Performance Tracking dashboard as a layout reference: an accent header, a labeled view switcher, compact KPI tiles, and grouped charts and tables. Reports still uses its own data and controls.
- **Filters** shows the selected period even when collapsed. If the months are reversed, Reports opens the filter and shows the corrected range. Switching views or choosing **Export CSV** keeps the normalized range.
- Operational places **Requests Created vs Completed** beside **Needs Attention** on desktop and above it on narrow screens. Urgent requests retain their links, account, engineer, priority, due date, and textual status. Activity Logs leads with **Monthly Hours Trend** before the breakdowns.
- Both views use the PPT summary tiles’ padding, type scale, and accent borders. All five tiles match in size and reflow to two columns on narrower screens.
- Breakdown charts have matching card and plot sizes, including empty states. Ranking charts, breakdown tables, and **Needs Attention** show up to five items. **Load more** shows the full breakdown or ordered request list in a dialog, without expanding the dashboard card. Keyboard users return to the same button on close.
- Reports uses native page scrolling and no longer scrolls vertically inside ranking previews. Wide tables keep their labeled horizontal scroll regions. The activity log still shows all nine columns on phones. Opening exact chart values below a plot does not stretch unrelated chart rows.

## Unchanged

Role permissions, metrics, date bases, CSV format, chart values and PNG export, 50-row pagination, and activity edit validation and return links. No PPT, database, or dependency changes.

## Before and after

| View | Before | After |
| --- | --- | --- |
| Operational desktop, light | [Needs Attention occupied a full row above the request trend](../hub/changelog_assets/pending-ppt-inspired-reports/operational-desktop-before.png) | [The trend and Needs Attention sit side by side](../hub/changelog_assets/pending-ppt-inspired-reports/operational-desktop-after.png) |
| Activity Logs desktop, dark | [The period was hidden behind an icon-only filter](../hub/changelog_assets/pending-ppt-inspired-reports/activity-desktop-before.png) | [The period is visible with Filters collapsed, above the hours overview and trend](../hub/changelog_assets/pending-ppt-inspired-reports/activity-desktop-after.png) |
| Operational phone, 390 px | [Five full-width tiles pushed the trend below Needs Attention](../hub/changelog_assets/pending-ppt-inspired-reports/operational-phone-before.png) | [Equal PPT-style tiles use two columns](../hub/changelog_assets/pending-ppt-inspired-reports/operational-phone-after.png) |
| Operational breakdowns, light | [Cards grew with the number of rows](../hub/changelog_assets/pending-ppt-inspired-reports/operational-breakdowns-before.png) | [Cards match and rankings scroll inside each plot](../hub/changelog_assets/pending-ppt-inspired-reports/operational-breakdowns-after.png) |
| Activity breakdowns, dark | [Plot types and empty states produced different heights](../hub/changelog_assets/pending-ppt-inspired-reports/activity-breakdowns-before.png) | [All breakdown cards share one size](../hub/changelog_assets/pending-ppt-inspired-reports/activity-breakdowns-after.png) |
| Operational ranking previews, light | [Long rankings used an inner scrollbar](../hub/changelog_assets/pending-ppt-inspired-reports/operational-breakdowns-load-more-before.png) | [Preview five rows and use Load more](../hub/changelog_assets/pending-ppt-inspired-reports/operational-breakdowns-load-more-after.png) |
| Activity ranking previews, dark | [Long engineer rankings scrolled inside the card](../hub/changelog_assets/pending-ppt-inspired-reports/activity-breakdowns-load-more-before.png) | [The preview fits five rows without an inner scrollbar](../hub/changelog_assets/pending-ppt-inspired-reports/activity-breakdowns-load-more-after.png) |
| Activity table previews, dark | [Ten rows made one breakdown card taller](../hub/changelog_assets/pending-ppt-inspired-reports/activity-table-limit-before.png) | [All three breakdown cards match; Load more opens the full table](../hub/changelog_assets/pending-ppt-inspired-reports/activity-table-limit-after.png) |

The [capture metadata](../hub/changelog_assets/pending-ppt-inspired-reports/capture.json) records synthetic fixtures, viewports, source fingerprints, and image checksums. The first three before images include the pending [navbar fix](unreleased-responsive-navbar-changelog.md). The breakdown and preview comparisons document successive local changes. No after image is bound to a release revision.

## Verification and release handoff

- Added Django and Chromium coverage for reversed month ranges, narrow filter layout, trend and attention order, keyboard table scrolling, filtered pagination, and invalid and successful edits. Updated the [README](../README.md) and the [implementation plan](superpowers/plans/2026-11-19-reports-ppt-design.md).
- The five-item change passed 75 targeted Django and Chromium tests and the shared JavaScript chart-values check. Browser coverage includes five-item boundaries, chart values without Chart.js, full PNG export, long labels at 320 px with large text, focus return, and request order. Both views were checked at 320, 390, 768, 1024, and 1440 px in light and dark mode, plus empty reports on a phone.
- In a disposable database, an ordinary signed-in reader could not see the draft or load its images. After test publication, the reader could see all eight comparisons and descriptions in **Notifications → Changelogs** and fetch all sixteen PNGs.
- No real release was published. When the changes are committed, bind or regenerate the evidence against the exact first-parent and change revisions using the [release workflow](cd-workflow-testing.md#preparing-screenshot-evidence), then recheck the published entry in Notifications.
