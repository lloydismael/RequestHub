# Unreleased: Responsive navigation

## Fixed

- The signed-in navbar keeps the brand, theme switch, account button, and menu button on one row on phones and in Profile’s mobile preview. Tablets keep the compact menu until there’s room for inline links on wider screens.
- The sticky menu eases open and closed, can reverse mid-animation, and remembers its compact state when resizing to desktop and back. It skips animation with reduced motion. The links and account dropdown scroll independently in short viewports, so Reports, Notifications, Settings, and Log Out stay reachable.
- The current page stays highlighted but is no longer a link. PPT and SQR update that state when switching views. Other destinations and role permissions are unchanged.
- The compact Christmas navbar uses rounded panel corners instead of a wide pill that cuts into the menu. The desktop pill is unchanged. The lights follow the menu as it resizes without restarting their twinkle animation.
- The compact controls have 44px touch targets and visible focus. Escape closes the focused menu and returns focus to its button. The unread count sits beside the Notifications label instead of covering it.
- The compact menu responds to the first click on Reports even while Chart.js is still loading. Navigation initializes before page content without changing how charts load.

## Maintenance

- Added Chromium coverage for narrow and short viewports, enlarged text, keyboard navigation, interrupted motion, breakpoint changes, reduced motion, and seasonal corners. Added regression checks for current destinations, PPT/SQR view changes, and Christmas light node reuse. Updated the seasonal Profile integration check to avoid depending on navbar class order.
- Added a full-page Chromium regression that holds the Chart.js request open and verifies that a click opens the Reports menu and Enter closes it before the document finishes loading.
- The navbar fix did not redesign Reports; that work is tracked in the [Reports release notes](unreleased-ppt-inspired-reports-changelog.md).

### Local browser checks

The opt-in Chromium checks in [hub/test_mobile_ui.py](../hub/test_mobile_ui.py) render local templates without live network requests. With Playwright and Chromium installed, set `RUN_MOBILE_BROWSER_TESTS=1`, `MOBILE_BOOTSTRAP_CSS` to local Bootstrap 5.3.2 CSS, `MOBILE_BOOTSTRAP_JS` to its bundle, and `MOBILE_CHART_JS` to a local Chart.js 4.4.0 UMD bundle. Place the Bootstrap Icons 1.11.1 stylesheet (`bootstrap-icons.css`) and font (`bootstrap-icons.woff2`) next to the Bootstrap CSS. Run `python manage.py test hub.test_mobile_ui --settings=request_hub.settings_ci` in an isolated test environment. These checks do not replace real-device testing.

## Before and after

| View | Before | After |
| --- | --- | --- |
| Phone, 320 × 460 | [Menu button wraps below the controls](../hub/changelog_assets/pending-responsive-navbar/phone-before.png) | [Controls fit on one row](../hub/changelog_assets/pending-responsive-navbar/phone-after.png) |
| Short landscape, 640 × 280 | [Expanded menu runs offscreen](../hub/changelog_assets/pending-responsive-navbar/landscape-before.png) | [Links scroll inside the sticky menu](../hub/changelog_assets/pending-responsive-navbar/landscape-after.png) |
| Tablet, 992 × 460 | [Inline controls overflow the navbar](../hub/changelog_assets/pending-responsive-navbar/tablet-before.png) | [Compact menu leaves the account button in view](../hub/changelog_assets/pending-responsive-navbar/tablet-after.png) |
| Christmas tablet, 1037 × 460 | [Pill corners cut into the expanded menu](../hub/changelog_assets/pending-responsive-navbar/seasonal-tablet-ppt-before.png) | [Rounded panel frames the links and lights](../hub/changelog_assets/pending-responsive-navbar/seasonal-tablet-ppt-after.png) |
| Closing in landscape, 640 × 280 | [The menu has already snapped shut at 80 ms](../hub/changelog_assets/pending-responsive-navbar/landscape-closing-before.png) | [The menu is partway through closing at the same point](../hub/changelog_assets/pending-responsive-navbar/landscape-closing-after.png) |
| Reports while Chart.js loads, 792 × 480 | [The first click focuses the button but leaves the drawer closed](../hub/changelog_assets/pending-responsive-navbar/reports-toggle-loading-before.png) | [The first click opens the drawer with the same request still pending](../hub/changelog_assets/pending-responsive-navbar/reports-toggle-loading-after.png) |

The [original capture metadata](../hub/changelog_assets/pending-responsive-navbar/capture.json), [motion follow-up metadata](../hub/changelog_assets/pending-responsive-navbar/motion-capture.json), and [toggle follow-up metadata](../hub/changelog_assets/pending-responsive-navbar/toggle-capture.json) record source and image checksums. The original pairs use synthetic Reports data; the motion pairings use synthetic PPT and Notifications fixtures. The toggle pair uses the same synthetic dark Reports page with Chart.js held open in both captures. The follow-up before images capture the earlier working tree, not a release revision.

## Verification and release handoff

- The original layout pass covered 219 Django and Chromium checks and 94 JavaScript checks. The motion follow-up passed 31 focused Django/Chromium checks on disposable PostgreSQL and 87 JavaScript checks. Chromium measured a 28px compact Christmas corner radius, a non-actionable PPT item, and a landscape navbar that moves from 244px open through 162px at 80 ms to 78px closed. A live probe confirmed that the updated SVG, bulbs, and twinkle animations remain connected as the drawer changes height.
- The toggle follow-up passed 54 focused Django and Chromium checks and 20 companion JavaScript checks. Under the same pending Chart.js request, the first click left the drawer at 0px before the fix and opened it to 286px after the fix. The regression also verifies keyboard close before the request finishes.
- Verified all six pairs in **Notifications → Changelogs** with a synthetic entry in the disposable database. A signed-in reader could not see the draft or retrieve its images. After test publication, all captions and twelve PNGs were available.
- No real release was published. Once the change is committed, bind or regenerate the evidence against the exact first-parent and change revisions using the [release changelog workflow](cd-workflow-testing.md#preparing-screenshot-evidence). For the toggle pair, run page scripts and hold Chart.js open in both revisions. Recheck the published entry in Notifications.
