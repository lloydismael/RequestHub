# Unreleased: continuous Santa blanket theme transition

This change is recorded for release preparation, **not yet published in Notifications**. No new application version or after-commit SHA has been invented. Publication must use a genuine release revision and the normal authorized changelog workflow.

## Improved

- Christmas light/dark switching now uses one continuous quilt-edge reveal on desktop and phones: Santa's sleigh pulls a visibly folded, stitched blanket hem through a gold tether.
- Three presents detach from the sleigh at staggered points, fall, rotate, and fade independently in both directions.
- Unsupported View Transition browsers preserve the old themed viewport in an inert, script-free copy that clips away along the same reveal edge; the former full-screen fabric-cover fallback is removed.
- Smaller responsive artwork and hem widths keep the phone viewport readable. Santa artwork is preloaded for the first transition.

## Fixed

- Christmas transition snapshots keep the old theme visible ahead of the hem instead of inheriting the default root cross-fade.
- Fallback gifts no longer inherit the moving sleigh's position after release.
- Snapshot readiness, cancellation, timeouts, resize, tab visibility, and accessibility changes preserve the latest selected theme and dispose transient frames, gifts, and animations.
- Snapshot copies retain visible form values, readable canvas pixels, window scrolling, and nested/RTL table and panel scroll offsets while removing executable content, active media/nested frames, and password/hidden/file input values. Existing scrollbar layout is preserved. Copies are noninteractive and have no script/form/navigation sandbox permissions.
- Preparing copies remain hidden until resources and scroll restoration are ready, preventing a scrolled page from flashing an unfinished snapshot at the document's top.

## Maintenance

- Added the dedicated snapshot helper and consistent authenticated/login template loading with updated asset cache keys.
- Added Node snapshot/lifecycle regression coverage, persistent real-DOM Chromium checks, and Django helper-order/preload coverage. The browser checks reuse the existing optional Playwright dependency; no production dependency was added.
- Rebuilt the generated static manifest so manifest-backed environments can resolve the new helper and changed assets.
- Updated [README theme documentation](../README.md) and added the [transition guide](christmas-theme-transition.md).

## Before/after evidence

All images use identical synthetic dashboard records with the actual application JavaScript/CSS. Animation is paused at early (375 ms), middle (750 ms), and late (1125 ms) phases; these are not live-user screenshots or static end-state substitutes for motion evidence.

| Renderer / phase | Before | After |
| --- | --- | --- |
| Desktop native / early | [Before](../hub/changelog_assets/pending-santa-continuous-reveal/desktop-native-early-before.png) | [After](../hub/changelog_assets/pending-santa-continuous-reveal/desktop-native-early-after.png) |
| Desktop native / middle | [Before](../hub/changelog_assets/pending-santa-continuous-reveal/desktop-native-middle-before.png) | [After](../hub/changelog_assets/pending-santa-continuous-reveal/desktop-native-middle-after.png) |
| Desktop native / late | [Before](../hub/changelog_assets/pending-santa-continuous-reveal/desktop-native-late-before.png) | [After](../hub/changelog_assets/pending-santa-continuous-reveal/desktop-native-late-after.png) |
| Phone fallback / early | [Before](../hub/changelog_assets/pending-santa-continuous-reveal/phone-fallback-early-before.png) | [After](../hub/changelog_assets/pending-santa-continuous-reveal/phone-fallback-early-after.png) |
| Phone fallback / middle | [Before](../hub/changelog_assets/pending-santa-continuous-reveal/phone-fallback-middle-before.png) | [After](../hub/changelog_assets/pending-santa-continuous-reveal/phone-fallback-middle-after.png) |
| Phone fallback / late | [Before](../hub/changelog_assets/pending-santa-continuous-reveal/phone-fallback-late-before.png) | [After](../hub/changelog_assets/pending-santa-continuous-reveal/phone-fallback-late-after.png) |

[Capture metadata](../hub/changelog_assets/pending-santa-continuous-reveal/capture.json) records the genuine baseline revision and verified source fingerprints. After images were captured from this change before release binding; `after_revision` is deliberately unset until an approved release revision is selected. This is not a packaged `manifest.json` and does not alter historical release manifests.

## Verification

- 68 focused Node tests passed.
- 37 Django tests passed after the static build: 32 related theme/login/preference tests plus five existing changelog manifest checks.
- Four headless Chromium browser tests passed, including twelve combinations of width (375/640/1200), direction, and native/fallback rendering; also covers sandbox/form/canvas/window and nested-scroll fidelity, hidden preparation during delayed resource loading, rapid reversals, reduced motion, and other themes.
- Firefox and WebKit browser binaries were not installed locally; their execution has not been claimed.

Before release publication, bind the verified captures to the actual approved release revision, validate the existing changelog/evidence pipeline, and verify the published entry and comparison images in Notifications.
