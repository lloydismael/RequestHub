# PPT-Inspired Reports Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` after this proposal is approved. Steps use checkboxes for tracking.

**Goal:** Make Operational and Activity Logs at `/reports/` feel like the Project Performance Tracking (PPT) dashboard without changing what either report measures or does.

**Architecture:** Adapt PPT's visual patterns in the Reports templates and the Reports section of the dashboard stylesheet. Keep Reports server-rendered with its current Chart.js enhancement. Do not load PPT styles, scripts, or data on Reports. See the [unreleased Reports notes](../../unreleased-ppt-inspired-reports-changelog.md) for implementation and verification status.

**Tech Stack:** Django templates, Bootstrap 5.3, shared theme tokens, Chart.js 4.4, Playwright (Chromium).

**Spec and reference:** Use PPT at `/sqr/?view=performance`, specifically its [dashboard markup](../../../templates/hub/partials/sqr_performance.html) and [styles](../../../static/css/liquid-glass-sqr.css). The [Reports view](../../../hub/views.py), [templates](../../../templates/hub/report.html), and [existing tests](../../../hub/tests.py) define the behavior to preserve.

## Global constraints

- Keep both `/reports/` views (`?report_view=operational` and `?report_view=activity`), `/reports/export/`, and the current admin/PM-ESG authorization. No data-model, query, metric, CSV format, or dependency changes.
- Keep the month inputs (`start_month`, `end_month`), normalization, Clear behavior, and date basis: request creation for Operational, log date for Activity Logs. Tabs and CSV links keep the selected normalized range.
- Keep all existing charts, series, units, table rows, ranking rules, `*-data` JSON IDs, `data-expand-chart` hooks, and the shared exact-values disclosure. No PPT assessment metrics, project filters, or lazy-loading endpoint.
- Preserve the activity edit GET/POST contract (`edit_activity`, `log_id`, month fields, CSRF), form validation, and existing return URL. Keep the 50-row `activity_log_page` pagination.
- Use existing theme tokens for light and dark and respect reduced-effects and reduced-motion preferences. Keep visible focus, touch-sized controls, and text labels for statuses. At 320 CSS pixels only table regions may scroll horizontally; controls and page layout may not.

## Design mapping

| PPT pattern | Reports adaptation |
| --- | --- |
| Accent rule, eyebrow, prominent title | Use the eyebrow **Request Hub analytics**, one **Reports** H1, and the existing view-specific description. Keep the Reports navbar surface marker. Put **Export CSV** beside the title on wide screens; wrap below the description when space runs out. |
| Visible Filters disclosure | Below the hero, put the **Operational** and **Activity Logs** links in a labeled nav (`aria-current="page"` on the active link). Follow with a native **Filters** disclosure. Show the active period in its summary even when closed; open it on a reversed range. |
| Compact KPI tiles and grouped surfaces | Use neutral panels, optional semantic accents, and a label, value, and explanatory line per tile. No clickable PPT KPIs. Preserve blanks or dashes for unavailable rates instead of showing zero. |
| Primary chart and soft attention panel | **Operational:** overview → Requests Created vs Completed beside Needs Attention on desktop, with the chart first on narrow screens → remaining four charts → Account Manager Breakdown and Engineer Allocation. Use the existing attention rows and retain request links, due dates, and text status. |
| Chart sections and scrollable trackers | **Activity Logs:** overview → full-width Monthly Hours Trend → five remaining charts → Engineer, Account, and Activity Type breakdowns → Recent Engineer Activity. Keep editing and pagination next to the log table. |

Use three steps of visual hierarchy: H1 for Reports, H2 for each major section, H3 for cards within a section. Use a `2fr : 1fr` chart/attention grid and a two-column chart grid at `64rem` and above; stack below that. Use equal KPI tiles with PPT's padding and type scale: five columns on desktop and two on narrower screens. Favor neutral surfaces and PPT's restrained accent over copying its page-specific selectors or CSS variables. Extract shared primitives later only if a second change warrants it.

## Review focus

1. Reversed dates (September to August 2026) show an open filter and the corrected August–September range; switch-view and CSV links carry the corrected values (Task 1).
2. A 320px phone with enlarged text can reveal filters, see wrapped actions, and scroll wide tables independently of the page (Tasks 1 and 3).
3. An empty period shows meaningful empty states and no expand buttons for empty charts; a nonempty chart with a failed renderer still exposes exact values (Task 2).
4. Long engineer labels, missing series values, and theme changes do not drop ranking rows or change PNG exports (Task 2).
5. Page 2 preserves month filters; invalid edits redisplay their errors, and saving or cancelling follows the existing filtered return path (Task 3).
6. Breakdown cards match with short, long, and empty datasets. Ranking charts, breakdown tables, and Needs Attention preview at most five items. **Load more** opens the full content in a keyboard-accessible dialog, keeping the dashboard layout in place. The two lead trends, PPT, and Recent Engineer Activity are not capped. Opening exact values must not stretch unrelated chart rows.

## Task 1: Build the shared Reports shell

**Files:** Modify [report.html](../../../templates/hub/report.html), [report_filter.html](../../../templates/hub/partials/report_filter.html), and the Reports section of [liquid-glass-dashboard.css](../../../static/css/liquid-glass-dashboard.css). Extend [test_mobile_ui.py](../../../hub/test_mobile_ui.py) and update the relevant [RequestReportViewTests](../../../hub/tests.py) assertions from exact classes to visible behavior.

**Interface:** Consume `report_view`, `period_query`, `period_label`, `period_range_swapped`, `period_start_month`, and `period_end_month`. Produce one `.rpt-shell` subtree and one native `details` with a **Filters** summary. Keep `report-start-month`, `report-end-month`, and `data-navbar-surface="theme"`.

- [x] In `RequestReportViewTests`, add `test_reversed_month_filter_stays_visible_and_links_use_normalized_range` for both views. Submit September–August 2026 and assert the native disclosure is open, input values are `2026-08` and `2026-09`, the reversal hint is present, and the tab and export URLs use those months. In `MobileBrowserTests`, add `test_report_filter_and_actions_reflow_at_phone_width`: at 320px with 20px root text, verify the closed summary shows the selected period, use Enter on **Filters**, check both labeled fields and **Export CSV** are reachable, and assert no document horizontal overflow.
- [x] Run `python manage.py test hub.tests.RequestReportViewTests hub.test_mobile_ui.MobileBrowserTests.test_report_filter_and_actions_reflow_at_phone_width --settings=request_hub.settings_ci`. Expect the new disclosure/reflow assertions to fail before the change.
- [x] Implement the hero and toolbar. Move the GET form into `details` with `open` for reversed ranges. Retain Apply, Clear, both date labels and the period caption; place the reversal feedback with the form. Remove only the old filter popup script and obsolete popup CSS. Keep the activity edit script.
- [x] Rerun the same selectors; confirm both views, CSV contents, and role checks pass. Use the existing [local browser setup](../../../README.md) and a disposable PostgreSQL database, not a shared application database.

## Task 2: Restyle KPIs, charts, and Needs Attention

**Files:** Modify [report.html](../../../templates/hub/report.html), [report_chart.html](../../../templates/hub/partials/report_chart.html) only for card presentation, and the Reports CSS section in [liquid-glass-dashboard.css](../../../static/css/liquid-glass-dashboard.css). Keep [chart_plot.html](../../../templates/hub/partials/chart_plot.html) and [report-charts.js](../../../static/js/report-charts.js) unchanged unless a tested layout regression requires a hook fix.

**Interface:** Use `.rpt-shell` from Task 1. Keep all existing `report_chart.html` arguments and the expanded modal IDs, including `rptChartModal`, `rptModalCanvas`, and `rptChartModalPng`.

- [x] Add `test_operational_chart_and_attention_stack_without_dropping_data`: at 390px the trend precedes and sits above Needs Attention; at 1280px both are side by side; one overdue synthetic request remains a link with its original status text. Check an empty-period render keeps the existing empty message and omits the corresponding expand control.
- [x] Run `python manage.py test hub.test_mobile_ui.MobileBrowserTests.test_operational_chart_and_attention_stack_without_dropping_data --settings=request_hub.settings_ci`; expect the old order/layout to fail.
- [x] Apply the mapping above. Keep the five existing tiles per view and the current hints and values. Reserve chart sizing space, keep `View chart values` reachable, and ensure long labels can wrap without pushing cards past their columns. Retain legends, units, empty messages, the expanded all-engineer/all-manager data, focus return, and PNG download.
- [x] Rerun the new check with the existing `test_reports_chart_values_survive_unavailable_renderer`, `test_chart_initialization_without_context_keeps_exact_values`, `test_reports_rankings_and_expanded_chart_share_values_after_theme_change`, and `test_png_draws_series_key_and_narrow_axis_distinguishes_years` selectors in `MobileBrowserTests`. Expect all to pass.

## Task 3: Contain tables and verify the activity workflow

**Files:** Modify the table wrappers in [report.html](../../../templates/hub/report.html) and [report_activity_log.html](../../../templates/hub/partials/report_activity_log.html) and the Reports CSS in [liquid-glass-dashboard.css](../../../static/css/liquid-glass-dashboard.css). Extend [test_mobile_ui.py](../../../hub/test_mobile_ui.py) and [RequestReportViewTests](../../../hub/tests.py).

**Interface:** Preserve existing captions, column order, edit links and form IDs, and pagination parameters. Give each populated table wrapper a focusable `.rpt-table-scroll` region named by its card heading; mark the active activity page with `aria-current="page"`.

- [x] In `MobileBrowserTests`, add `test_activity_table_scrolls_to_edit_actions_at_phone_width`: tab to the **Recent Engineer Activity** region at 320px with 20px root text and a wide synthetic entry, press Right Arrow, and verify the region alone scrolls and its named edit link can receive focus.
- [x] In `RequestReportViewTests`, extend `test_activity_report_pagination_preserves_month_filters` to follow page 2 and assert one row remains, with August 2026 still selected. Add `test_report_activity_edit_preserves_errors_and_filtered_return`: reuse `_create_activity_log()`; open its edit URL with `start_month=2026-08&end_month=2026-09` and verify Cancel returns to that filtered list. Post the form with `actual_hours=not-a-number` and assert `activity_form.errors['actual_hours']` is populated and saved hours remain `2.50`. Submit `3.50` with the other required fields valid and assert the saved hours and redirect to the same filtered list.
- [x] Run `python manage.py test hub.tests.RequestReportViewTests hub.test_mobile_ui.MobileBrowserTests.test_activity_table_scrolls_to_edit_actions_at_phone_width --settings=request_hub.settings_ci`; expect the pre-change table to fail the named-region/keyboard assertion while the existing edit and pagination behavior passes.
- [x] Apply the PPT table treatment: contained horizontal scrolling, visible focus, compact headings, and readable cell labels. Give icon-only edit and pagination links accessible names. Keep cancellation, validation, and submission behavior; do not introduce an edit API or change the redirect.
- [x] Run `python manage.py test hub.tests.RequestReportViewTests hub.test_mobile_ui --settings=request_hub.settings_ci` with the local browser fixtures; expect no failures. Review both report views against PPT at 320, 390, 768, 1024, and 1440px, in light/dark and reduced effects. Check empty and populated views, keyboard focus, large text, CSV, chart expansion/PNG, and editing. Confirm PPT itself is unchanged.
- [x] Capture matching synthetic before/after views, record the implemented changes in the [unreleased notes](../../unreleased-ppt-inspired-reports-changelog.md), and verify all screenshot pairs and descriptions in Notifications as an ordinary signed-in reader in a disposable database.
- [ ] Release handoff: after committing, bind or recapture evidence at exact revisions and recheck the published entry using the [release changelog workflow](../../cd-workflow-testing.md#release-changelog).
