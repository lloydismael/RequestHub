# Unreleased: PPT navigation

This change is prepared for release, not yet published in Notifications. The after-state has no commit or release revision yet; no revision has been invented.

## Improved

- Project Performance Tracking now opens from **PPT** in the main navigation between Dashboard and SQR. Admin and PM-ESG access is unchanged.
- **SQR** opens the tracker even if PPT was visited last. The tracker/prototype selector remains available, and the active navigation item, page title, focused-project links, and URL follow the selected view.
- Moving PPT into the main navigation preserved its dashboard filters, lazy loading, and refresh behavior.
- The SQR header and its tracker, prototype, reports, export, import, and filter controls are hidden on PPT and remain available on the SQR views. PPT keeps its own dashboard and filters.

## Fixed

- The SQR Tracker Prototype notice no longer interrupts a direct PPT visit; it remains available on the tracker.
- Primary and secondary administrators now open PPT with no filters selected even when the Classic tracker is hidden. Previously, hiding Classic enabled **My Assigned** for admins and could hide the entire portfolio. PM-ESG still defaults to **My Assigned**; saved filters and explicit filter links still take precedence.

## Maintenance

- Updated the SQR guide and added navigation, role, header visibility, view-selection, browser-storage, and existing-workflow regression checks.
- Captured synthetic desktop and mobile before/after evidence without project records or live database data.

## Before/after evidence

| View | Before | After |
| --- | --- | --- |
| Desktop | [Before](../hub/changelog_assets/pending-ppt-navigation/desktop-before.png) | [After](../hub/changelog_assets/pending-ppt-navigation/desktop-after.png) |
| Mobile, expanded menu | [Before](../hub/changelog_assets/pending-ppt-navigation/mobile-before.png) | [After](../hub/changelog_assets/pending-ppt-navigation/mobile-after.png) |

The table above documents the earlier navigation change. Its [capture metadata](../hub/changelog_assets/pending-ppt-navigation/capture.json) records the genuine baseline revision, source fingerprints, and limitations. After images were captured from the working tree; `after_revision` remains unset until an actual release revision exists.

### SQR header removed from PPT

| View | Before | After |
| --- | --- | --- |
| Desktop PPT | [Before](../hub/changelog_assets/pending-ppt-navigation/ppt-header-desktop-before.png) | [After](../hub/changelog_assets/pending-ppt-navigation/ppt-header-desktop-after.png) |
| Mobile PPT | [Before](../hub/changelog_assets/pending-ppt-navigation/ppt-header-mobile-before.png) | [After](../hub/changelog_assets/pending-ppt-navigation/ppt-header-mobile-after.png) |

[Header capture metadata](../hub/changelog_assets/pending-ppt-navigation/ppt-header-capture.json) identifies both captures as uncommitted working-tree states. The before state already includes the navigation change; neither image is represented as an exact Git revision.

### Admin default filters

| Classic hidden | Before | After |
| --- | --- | --- |
| Fresh Admin visit | [My Assigned on; 0 matching records](../hub/changelog_assets/pending-ppt-navigation/ppt-admin-default-before.png) | [No filters; 2 matching records](../hub/changelog_assets/pending-ppt-navigation/ppt-admin-default-after.png) |

The [filter capture metadata](../hub/changelog_assets/pending-ppt-navigation/ppt-admin-default-capture.json) records both working-tree states. Chromium verified both admin account types with Classic shown and hidden, the unchanged PM-ESG default, and saved and explicit filters. The regression failed for both admin types before the fix; 30 focused Django and 16 PPT JavaScript tests passed after it. A synthetic release in disposable PostgreSQL verified that Notifications hid the draft and its images, then showed both descriptions and served the original images after publication. Nothing was published live.

## Verification and release handoff

- 65 focused Django checks passed against a disposable PostgreSQL database, including the Chromium header/accessibility check at phone and desktop widths in light and dark modes. All 17 focused JavaScript checks passed. Browser checks also covered PPT, SQR tracker, prototype, reports, and navbar surfaces at phone, tablet, and desktop widths.
- The new header screenshot pairs, descriptions, and image downloads were verified in Notifications using a synthetic release in an isolated test database; this did not publish a real release.
- After the change is committed, capture or bind suitable evidence to its real first-parent and change revisions using the normal [release changelog workflow](cd-workflow-testing.md#release-changelog), then verify the published entry in Notifications.
