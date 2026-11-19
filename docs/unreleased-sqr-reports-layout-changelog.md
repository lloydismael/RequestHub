# Unreleased: Uniform SQR Reports layout

## Improved

- The SQR **Sales Pipeline** follows the main **Reports** page’s layout, with the same accent header, section spacing, metric tiles, and card padding. Tiles have consistent dimensions within each group and use two columns on phones. Long peso values scale to fit without losing precision.
- All five pipeline charts use the same card and plot size, including empty states. Charts sit in two columns on desktop and stack on narrow screens. Long category labels fit the plot; **View chart values** still shows every full label and exact value. Opening the values leaves other rows unchanged.
- Both closed-won rankings use matching cards and labeled, keyboard-scrollable tables. SQR data, export options, permissions, and live chart refresh are unchanged. The main Reports and SQR Tracker keep their existing behavior.

## Before and after

| View | Before | After |
| --- | --- | --- |
| Sales Pipeline, desktop light | [Separate tile styles and uneven chart widths](../hub/changelog_assets/pending-sqr-reports-layout/pipeline-desktop-before.png) | [Reports-style heading and metric tiles](../hub/changelog_assets/pending-sqr-reports-layout/pipeline-desktop-after.png) |
| Sales Pipeline, 390 px dark | [Full-width metric tiles](../hub/changelog_assets/pending-sqr-reports-layout/pipeline-phone-before.png) | [Equal tiles in two columns](../hub/changelog_assets/pending-sqr-reports-layout/pipeline-phone-after.png) |
| Pipeline breakdowns, desktop light | [Plots with different widths and heights](../hub/changelog_assets/pending-sqr-reports-layout/breakdowns-desktop-before.png) | [Equal chart cards and plot heights](../hub/changelog_assets/pending-sqr-reports-layout/breakdowns-desktop-after.png) |

[Capture metadata](../hub/changelog_assets/pending-sqr-reports-layout/capture.json) records the baseline revision, synthetic inputs, viewport sizes, source fingerprints, and image checksums. The after images are from the local working tree, not a published release.

## Verification and release handoff

- The sizing regressions failed against the old layout and passed after the changes. Browser coverage checks matched Reports spacing, equal cards with mixed and empty data, 320 px with enlarged text, exact values for all eight groups, and focus after live refresh. Updated the [README](../README.md).
- In a disposable database, a signed-in reader could not see the draft or load its images. After test publication, all three comparisons appeared under **Notifications → Changelogs**, and the reader could load all six PNGs.
- No real release was published. Bind or regenerate the evidence for the release’s exact revisions and recheck Notifications using the [release workflow](cd-workflow-testing.md#preparing-screenshot-evidence).
