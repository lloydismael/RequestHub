# Christmas blanket theme transition

The Christmas theme uses a single continuous light/dark reveal: Santa's sleigh pulls a stitched, folded quilt edge across the viewport while three presents leave the sleigh, fall independently, rotate, and fade.

- **Light → dark:** travel from left to right.
- **Dark → light:** travel from right to left, with mirrored sleigh artwork and tether.
- Travel lasts 1.5 seconds; gifts are released at three staggered points during the pass.
- The destination theme appears directly behind the quilt hem. The page is not first covered by a full-screen blanket.

## Enabling and disabling

Select Christmas in account theme settings and enable **Festive effects** and **Santa blanket transition**. The existing light/dark control in the navbar starts the animation. These choices are account preferences; the theme switch continues to save the selected light/dark mode on the device.

The remembered Christmas login uses the same transition. Other account themes keep their existing light/dark animation and never gain Santa or gifts.

OS reduced motion, account reduced-motion/reduced-effects preferences, the festive master switch, and the Santa-specific switch take priority over the animation. Hiding the tab, resizing, or changing those preferences during a pass cancels decorative motion and retains the current selected theme. Rapid reversals replace the previous pass instead of stacking effects.

## Consistent rendering on desktop and phones

Where supported, View Transitions animate the new-theme snapshot, the sleigh, and individual gift layers with shared timing. Phone width and coarse pointers no longer select a different Christmas animation. A successful native transition never mounts a second viewport document.

Without usable View Transitions, an inert, fixed iframe preserves the old themed viewport. Its clipping edge follows the same sleigh/quilt choreography, exposing the live new theme underneath. Gifts have a separate viewport layer so they stop following the sleigh when released.

The temporary copy preserves window scrolling, nested panel/table scrolling (including RTL offsets), visible form values, readable canvas pixels, and existing scrollbar layout. It removes scripts, inline event handlers, nested frames, active media, and password/hidden/file input values. Its sandbox permits same-origin access only for visual/scroll synchronization; it does **not** permit scripts, forms, popups, or navigation. It cannot receive pointer or keyboard input. The live page and its values are not modified by capture.

The preparing copy stays hidden until resources are ready and all captured scroll offsets have been restored, so a scrolled page does not jump to its top while loading. Snapshot preparation is bounded to one second, with an overall five-second transition safety cleanup. Rendering/asset failures apply the chosen theme without leaving an overlay behind. The copy lives only in the browser during the pass; it is not stored or uploaded. Santa artwork is preloaded to avoid an empty first flyover.

## Developer verification

Production adds no animation or screenshot dependency. The browser regression suite uses the existing development-only Playwright dependency in [requirements-changelog-capture.txt](../requirements-changelog-capture.txt).

From the repository root, using the existing compatible Python environment:

```powershell
node --test scripts_check\test_christmas_theme.cjs scripts_check\test_christmas_snapshot.cjs scripts_check\test_christmas_preview.cjs scripts_check\test_christmas_lights.cjs
& '.\.venv\Scripts\python.exe' scripts_check\test_christmas_transition_browser.py -v
$env:DJANGO_SETTINGS_MODULE = 'request_hub.settings_ci'
& '.\.venv\Scripts\python.exe' manage.py collectstatic --noinput
& '.\.venv\Scripts\python.exe' manage.py test accounts.test_themes accounts.test_christmas_preferences accounts.test_christmas_login --noinput
```

Django uses the configured disposable test database; never substitute or reset the live database. Rebuild static assets after adding or changing resources so manifest-backed deployments can resolve the helper and updated stylesheet.

The real-DOM suite checks both directions at 375, 640, and 1200 pixels with native transitions enabled and disabled, alignment of the quilt/reveal edge, three gift animations, iframe isolation, form/canvas/window and nested-scroll fidelity, hidden preparation under delayed resource loading, reversal cleanup, reduced motion, and non-Christmas behavior. It runs headless because an inactive VS Code browser panel can correctly report `document.hidden` and suppress festive animation.

For manual review, also inspect early/middle/late frames, landscape layout, first-flight artwork, dense dashboards, and any available Firefox/WebKit runtimes. Evidence must use synthetic records, not live dashboard data. End-state screenshots alone do not demonstrate movement.
