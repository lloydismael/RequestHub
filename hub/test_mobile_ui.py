import base64
import os
import re
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings

from accounts.forms import ProfileForm, RoleAuthenticationForm, ThemeForm
from accounts.models import User
from request_hub.test_html_helpers import extract_scripts, omit_script_elements


class MobileTemplateTests(SimpleTestCase):
    def test_mobile_styles_follow_page_specific_styles(self):
        base = (settings.BASE_DIR / 'templates' / 'base.html').read_text(encoding='utf-8')
        self.assertTrue('css/mobile.css' in base, 'Shared mobile stylesheet is missing')
        self.assertGreater(base.index('css/mobile.css'), base.index('{% block extra_head %}'))

    def test_profile_uses_shared_phone_breakpoint(self):
        profile = (settings.BASE_DIR / 'templates' / 'accounts' / 'profile_form.html').read_text(encoding='utf-8')
        self.assertTrue("window.matchMedia('(max-width: 767.98px)')" in profile,
                        'Profile breakpoint must match the shared phone breakpoint')


@skipUnless(os.environ.get('RUN_MOBILE_BROWSER_TESTS') == '1',
            'Set RUN_MOBILE_BROWSER_TESTS=1 with Playwright/Chromium for layout regressions')
@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class MobileBrowserTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        definitions = patch('hub.form_editor.get_definition', return_value=None)
        definitions.start()
        cls.addClassCleanup(definitions.stop)
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        cls.bootstrap = Path(os.environ['MOBILE_BOOTSTRAP_CSS']).read_text(encoding='utf-8')
        base = (settings.BASE_DIR / 'templates' / 'base.html').read_text(encoding='utf-8')
        cls.mode_script = next(script for attrs, script in extract_scripts(base)
                               if 'src' not in attrs and 'function syncViewMode' in script)
        cls.navbar_script = next(script for attrs, script in extract_scripts(base)
                                 if 'src' not in attrs and '// Nav avatar dropdown' in script)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def context(self, anonymous=False):
        request = RequestFactory().get('/dashboard/')
        request.user = AnonymousUser() if anonymous else User(username='mobile-fixture', role=User.Roles.ADMIN)
        return {'request': request, 'user': request.user, 'requests': [], 'role': 'admin',
                'page_obj': Paginator(range(60), 50).page(1), 'page_size': 50,
                'pagination_next': '?page=2', 'active_sqr_tab': 'proposal',
                'form': RoleAuthenticationForm() if anonymous else ProfileForm(instance=request.user),
                'theme_form': None if anonymous else ThemeForm(instance=request.user)}

    def page(self, width=390, height=844, template=None, dark=False, fixture=None, scripts=None):
        context = self.browser.new_context(viewport={'width': width, 'height': height},
                                           is_mobile=True, has_touch=True)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(3000)
        page.route('**/*', lambda route: route.abort())
        html = render_to_string(template, fixture or self.context(anonymous=template == 'accounts/login.html')) if template else (
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<main class="container"></main>')
        # Retain inert chart data only for fixtures that opt into local scripts.
        html = omit_script_elements(html, keep_json_data=scripts is not None)

        def inline_styles(match):
            href = re.search(r'href="([^"]+)"', match.group())
            if not href:
                return ''
            url = href[1].split('?')[0]
            if 'bootstrap.min.css' in url:
                return '<style>' + self.bootstrap + '</style>'
            if url.startswith(settings.STATIC_URL) and url.endswith('.css'):
                path = settings.BASE_DIR / 'static' / url[len(settings.STATIC_URL):]
                if path.is_file():
                    return '<style>' + path.read_text(encoding='utf-8') + '</style>'
            return ''

        html = re.sub(r'<link\b[^>]*>', inline_styles, html)
        page.set_content(html)
        if not template:
            page.add_style_tag(content=self.bootstrap)
            for name in ('app', 'liquid-glass', 'liquid-glass-dashboard', 'liquid-glass-ambient', 'mobile'):
                path = settings.BASE_DIR / 'static' / 'css' / (name + '.css')
                if path.exists():
                    page.add_style_tag(content=path.read_text(encoding='utf-8'))
        page.evaluate("document.body.classList.add('lg-enter-ready')")
        page.evaluate(self.mode_script)
        if dark:
            page.evaluate("document.body.classList.add('dark-mode')")
        for script in scripts or ():
            if script.is_file():
                page.add_script_tag(content=script.read_text(encoding='utf-8'))
        return page

    def test_fixture_retains_only_opted_in_chart_data(self):
        fixture = (
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<main class="container"><p>Before &amp; after</p>'
            "<SCRIPT TYPE='application/json' id='chart-data'>{\"label\": \"example\"}</SCRIPT >"
            '<script type="text/plain">Test notes</script><p>Still present</p></main>'
        )
        for keep_data in (False, True):
            with self.subTest(keep_data=keep_data):
                with patch('hub.test_mobile_ui.render_to_string', return_value=fixture):
                    page = self.page(template='hub/dashboard.html', scripts=() if keep_data else None)
                self.assertEqual(page.locator('script').count(), int(keep_data))
                if keep_data:
                    self.assertEqual(page.locator('#chart-data').text_content(), '{"label": "example"}')
                self.assertEqual(page.locator('main p').all_text_contents(), ['Before & after', 'Still present'])

    def report_fixture(self):
        return {
            **self.context(), 'report_view': 'operational', 'request_trend_has_data': True, 'engineer_stats': [{'name': 'North Operations'}],
            'request_trend_chart': {'labels': ['<North>', 'July', 'August'], 'created': [0, None, 4], 'completed': [0, 1, 3]},
            'engineer_chart': {'labels': ['North Operations and Shared Infrastructure', 'Others'],
                               'completed': [8, 0], 'ongoing': [2, 2], 'totals': [10, 2]},
            'engineer_chart_full': {
                'labels': ['North Operations and Shared Infrastructure', *[f'Engineer {n}' for n in range(2, 13)]],
                'completed': [8, *[0] * 11], 'ongoing': [2, *[1] * 11], 'totals': [10, *[1] * 11],
            },
        }

    def activity_report_fixture(self):
        log = SimpleNamespace(
            pk=1, request_date=date(2026, 8, 15), engineer=self.context()['user'],
            account=SimpleNamespace(name='North Operations and Shared Infrastructure'),
            actual_hours=Decimal('2.50'), details='Quarterly readiness review', is_billable=True,
            get_activity_type_display=lambda: 'Support', get_location_display=lambda: 'Office',
        )
        return {
            **self.context(), 'report_view': 'activity', 'activity_logs': [log],
            'activity_logs_page_obj': Paginator([log], 50).page(1),
            'activity_totals': {'entries': 1, 'total_hours': Decimal('2.50')},
            'period_label': 'August 2026', 'activity_month_filter_label': 'August 2026',
        }

    def test_report_filter_and_actions_reflow_at_phone_width(self):
        fixture = {**self.report_fixture(), 'period_label': 'August 2026 to September 2026'}
        page = self.page(320, template='hub/report.html', fixture=fixture)
        page.add_style_tag(content='html { font-size: 20px !important; }')
        summary = page.locator('summary').filter(has_text='Filters')
        self.assertTrue(summary.get_by_text('Created: August 2026 to September 2026').is_visible())
        self.assertFalse(page.get_by_label('Start month').is_visible())
        summary.focus()
        page.keyboard.press('Enter')
        for control in (page.get_by_label('Start month'), page.get_by_label('End month'),
                        page.get_by_role('link', name='Export CSV')):
            self.assertTrue(control.is_visible())
            self.assertLessEqual(control.bounding_box()['x'] + control.bounding_box()['width'], 320)
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    def test_operational_chart_and_attention_reflow_without_dropping_requests(self):
        fixture = {**self.report_fixture(), 'attention_requests': [{
            'reference': 'REQ-01000', 'account': 'North Operations', 'engineer': 'Report Engineer',
            'priority': 'Medium', 'due_date': date(2026, 8, 15), 'is_overdue': True, 'days': 3,
            'url': '/requests/1000/',
        }]}
        for width in (390, 1280):
            with self.subTest(width=width):
                page = self.page(width, template='hub/report.html', fixture=fixture)
                trend = page.get_by_role('heading', name='Requests Created vs Completed').bounding_box()
                panel = page.get_by_role('heading', name='Needs Attention').bounding_box()
                if width == 390:
                    self.assertLess(trend['y'], panel['y'])
                    self.assertTrue(page.get_by_text('Monthly intake against closures', exact=True).is_visible())
                else:
                    self.assertLess(trend['x'], panel['x'])
                    self.assertAlmostEqual(trend['y'], panel['y'], delta=3)
                self.assertEqual(page.get_by_role('link', name='REQ-01000').get_attribute('href'), '/requests/1000/')
                self.assertTrue(page.get_by_text('3 days overdue').is_visible())

    def test_reports_kpis_are_equal_and_use_ppt_summary_scale(self):
        ppt = self.page(1440, template='hub/sqr.html', fixture={**self.context(), 'is_pm': True})
        ppt.locator('#sqr-performance-dashboard, #sqr-pm-content').evaluate_all(
            'elements => elements.forEach(el => el.hidden = false)')
        style = '''el => {
            const tile = getComputedStyle(el);
            const value = getComputedStyle(el.children[1]);
            return [tile.paddingTop, tile.borderTopWidth, tile.rowGap, value.fontSize];
        }'''
        reference = ppt.locator('.sqr-pm-summary').first.evaluate(style)
        for fixture in (self.report_fixture(), self.activity_report_fixture()):
            with self.subTest(view=fixture['report_view']):
                page = self.page(1440, template='hub/report.html', fixture=fixture)
                tiles = page.locator('.rpt2-kpi')
                self.assertEqual(tiles.count(), 5)
                self.assertEqual(tiles.first.evaluate(style), reference)
                for width in (1440, 320):
                    page.set_viewport_size({'width': width, 'height': 900})
                    if width == 320:
                        page.add_style_tag(content='html { font-size: 20px !important; }')
                    boxes = tiles.evaluate_all('els => els.map(el => [el.offsetWidth, el.offsetHeight])')
                    self.assertLessEqual(max(w for w, _ in boxes) - min(w for w, _ in boxes), 1)
                    self.assertEqual(len({h for _, h in boxes}), 1)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    def report_chart_grid_fixture(self):
        fixture = self.report_fixture()
        fixture.update(engagement_chart_has_data=True, engagement_chart={
            'labels': ['Support'], 'completed': [7], 'ongoing': [2],
        })
        fixture['engineer_chart'] = {
            'labels': [f'Engineer {n}' for n in range(1, 11)] + ['Others'],
            'completed': [2] * 11, 'ongoing': [1] * 11,
        }
        return fixture

    def test_report_breakdown_cards_match_with_short_long_and_empty_charts(self):
        scripts = self.chart_scripts('chart-values.js', 'chart-theme.js', 'report-charts.js')
        activity = self.activity_report_fixture()
        activity.update(activity_billable_chart={'labels': ['Billable', 'Not Billable'], 'totals': [2.5, 0]},
                        activity_location_chart={'labels': ['Office', 'Remote'], 'totals': [2, .5]})
        for fixture in (self.report_chart_grid_fixture(), activity):
            with self.subTest(view=fixture['report_view']):
                page = self.page(1280, template='hub/report.html', fixture=fixture, scripts=scripts)
                cards = page.locator('.rpt-chart-grid article')
                boxes = cards.evaluate_all('els => els.map(el => [el.offsetWidth, el.offsetHeight])')
                self.assertEqual(len({h for _, h in boxes}), 1, boxes)
                self.assertLessEqual(max(w for w, _ in boxes) - min(w for w, _ in boxes), 1)
                page.set_viewport_size({'width': 390, 'height': 844})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    def test_reports_show_five_ranked_bars_and_load_the_full_chart(self):
        fixture = self.report_fixture()
        fixture.update(
            product_chart_has_data=True,
            product_chart={'labels': list('ABCDEFG'), 'completed': [1, 5, 7, 3, 2, 6, 4], 'ongoing': [0] * 7},
            engagement_chart_has_data=True,
            engagement_chart={'labels': list('VWXYZ'), 'completed': [1, 2, 3, 4, 5], 'ongoing': [0] * 5},
        )
        page = self.page(390, template='hub/report.html', fixture=fixture,
                         scripts=self.chart_scripts('chart-values.js', 'chart-theme.js', 'report-charts.js'))
        card = page.locator('article').filter(has=page.locator('#productChart'))
        chart_labels = "id => Chart.getChart(id).data.labels"
        self.assertEqual(page.evaluate(chart_labels, 'productChart'), list('CFBGD'))
        self.assertEqual(page.evaluate(chart_labels, 'engineerChart'), fixture['engineer_chart_full']['labels'][:5])
        self.assertEqual(page.evaluate(chart_labels, 'engagementChart'), list('ZYXWV'))
        self.assertFalse(page.locator('article').filter(has=page.locator('#engagementChart'))
                         .get_by_role('button', name='Load more').count())
        self.assertTrue(card.locator('canvas').evaluate('el => el.offsetHeight <= el.parentElement.parentElement.clientHeight'))
        sizes = 'els => els.map(el => el.offsetHeight)'
        cards = page.locator('.rpt-chart-grid article')
        before = cards.evaluate_all(sizes)
        button = card.get_by_role('button', name='Load more', exact=True)
        button.focus()
        page.keyboard.press('Enter')
        modal = page.get_by_role('dialog', name='Requests by Product Category')
        self.assertTrue(modal.is_visible())
        self.assertEqual(page.evaluate(chart_labels, 'rptModalCanvas'), list('CFBGDEA'))
        self.assertTrue(modal.get_by_role('button', name='Download expanded chart as PNG').is_enabled())
        self.assertEqual(cards.evaluate_all(sizes), before)
        page.keyboard.press('Escape')
        self.assertTrue(button.evaluate('el => el === document.activeElement'))
        card.locator('summary').click()
        self.assertEqual(card.locator('tbody th').all_inner_texts(), list('CFBGD'))

    def test_five_wrapped_ranking_labels_fit_the_phone_preview(self):
        fixture = self.report_chart_grid_fixture()
        fixture.update(product_chart_has_data=True, product_chart={
            'labels': [f'Network Infrastructure Division {n}' for n in range(7)],
            'completed': list(range(7)), 'ongoing': [0] * 7,
        })
        page = self.page(320, template='hub/report.html', fixture=fixture,
                         scripts=self.chart_scripts('chart-values.js', 'chart-theme.js', 'report-charts.js'))
        for large in (False, True):
            with self.subTest(large=large):
                page.evaluate('''large => {
                    document.body.classList.toggle('pref-large-text', large);
                    window.dispatchEvent(new Event('requesthub:theme-changed'));
                }''', large)
                page.wait_for_function('''() => {
                    const chart = Chart.getChart('productChart');
                    return chart.height === chart.canvas.parentElement.clientHeight;
                }''')
                data = page.evaluate('''() => {
                    const axis = Chart.getChart('productChart').scales.y;
                    return [axis.ticks.length, axis.getPixelForTick(1) - axis.getPixelForTick(0),
                            Math.max(...axis.getLabelItems().map(({label, font}) => label.length * font.lineHeight))];
                }''')
                self.assertEqual(data[0], 5)
                self.assertGreaterEqual(data[1], data[2], data)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    def test_report_table_details_keep_cards_equal_and_restore_focus(self):
        fixture = self.activity_report_fixture()
        record = {'name': 'North Ops', 'label': 'Support', 'entries': 1,
                  'total_hours': 2.5, 'billable_hours': 2.5, 'non_billable_hours': 0}
        fixture.update(activity_engineer_table=[{**record, 'name': f'Engineer {n}'} for n in range(1, 8)],
                       activity_account_table=[record] * 5)
        page = self.page(320, template='hub/report.html', fixture=fixture,
                         scripts=self.chart_scripts('report-details.js', chart=False))
        page.add_style_tag(content='html { font-size: 20px !important; }')
        table = page.get_by_role('region', name='Engineer Hour Breakdown', exact=True)
        self.assertEqual(table.get_by_role('cell', name='Engineer 5', exact=True).count(), 1)
        self.assertEqual(table.get_by_role('row').count(), 6)
        self.assertEqual(page.get_by_role('button', name='Load more', exact=True).count(), 1)
        cards = page.locator('.rpt-table-grid > div')
        sizes = 'els => els.map(el => el.offsetHeight)'
        before = cards.evaluate_all(sizes)
        self.assertEqual(len(set(before)), 1)
        button = page.get_by_role('button', name='Load more', exact=True)
        button.focus()
        page.keyboard.press('Enter')
        modal = page.get_by_role('dialog', name='Engineer Hour Breakdown')
        self.assertEqual(modal.get_by_role('row').count(), 8)
        self.assertTrue(modal.evaluate('el => el.getBoundingClientRect().right <= innerWidth'))
        self.assertEqual(cards.evaluate_all(sizes), before)
        page.keyboard.press('Escape')
        self.assertTrue(button.evaluate('el => el === document.activeElement'))
        self.assertEqual(table.get_by_role('row').count(), 6)

        without_script = self.page(320, template='hub/report.html', fixture=fixture)
        table = without_script.get_by_role('region', name='Engineer Hour Breakdown', exact=True)
        self.assertEqual(table.get_by_role('row').count(), 8)
        self.assertEqual(without_script.get_by_role('button', name='Load more').count(), 0)

    def test_report_attention_load_more_keeps_links_in_priority_order(self):
        fixture = self.report_fixture()
        fixture['attention_requests'] = [
            {'reference': f'REQ-{n:05}', 'account': 'North Ops', 'engineer': 'Report Engineer',
             'priority': 'High', 'due_date': date(2026, 8, 15), 'is_overdue': True, 'days': 3,
             'url': f'/requests/{n}/'} for n in range(1, 8)
        ]
        page = self.page(1280, template='hub/report.html', fixture=fixture,
                         scripts=self.chart_scripts('report-details.js', chart=False))
        card = page.get_by_role('article', name='Needs Attention')
        self.assertEqual(card.get_by_role('link').all_inner_texts(), [f'REQ-{n:05}' for n in range(1, 6)])
        card.get_by_role('button', name='Load more').click()
        dialog = page.get_by_role('dialog', name='Needs Attention')
        self.assertEqual(dialog.get_by_role('link').all_inner_texts(), [f'REQ-{n:05}' for n in range(1, 8)])
        self.assertEqual(dialog.get_by_role('link', name='REQ-00007').get_attribute('href'), '/requests/7/')

    def test_reports_page_wheel_is_native_outside_charts(self):
        page = self.page(390, template='hub/report.html', fixture=self.report_fixture(),
                         scripts=self.chart_scripts('smooth-scroll.js', chart=False))
        target = page.get_by_role('heading', name='Delivery overview')
        wheel_kept_native = target.evaluate('''el => el.dispatchEvent(new WheelEvent('wheel', {
            deltaY: 140, bubbles: true, cancelable: true,
        }))''')
        self.assertTrue(wheel_kept_native, 'Reports must not cancel the browser’s page wheel scrolling')
        target.hover()
        start = page.evaluate('scrollY')
        page.mouse.wheel(0, 140)
        page.wait_for_function('start => scrollY > start', arg=start)
        self.assertEqual(page.evaluate('scrollX'), 0)

    def test_activity_table_scrolls_to_edit_actions_at_phone_width(self):
        page = self.page(320, template='hub/report.html', fixture=self.activity_report_fixture())
        page.add_style_tag(content='html { font-size: 20px !important; }')
        table = page.get_by_role('region', name='Recent Engineer Activity', exact=True).filter(has=page.get_by_role('table'))
        self.assertTrue(table.evaluate('el => el.tabIndex === 0 && el.scrollWidth > el.clientWidth'))
        self.assertEqual(table.get_by_role('columnheader').all_text_contents(),
                         ['#', 'Date', 'Engineer', 'Account', 'Activity', 'Hrs', 'Location', 'Billable', 'Actions'])
        table.focus()
        page.keyboard.press('ArrowRight')
        page.wait_for_function('document.activeElement.scrollLeft > 0')
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth && scrollX === 0'))
        page.keyboard.press('Tab')
        edit = table.get_by_role('link', name='Edit entry 1 for mobile-fixture')
        self.assertTrue(edit.evaluate('el => el === document.activeElement'))
        self.assertIn('edit_activity=1', edit.get_attribute('href'))

    def chart_scripts(self, *names, chart=True):
        scripts = [Path(os.environ['MOBILE_CHART_JS'])] if chart else []
        if chart:
            self.assertTrue(scripts[0].is_file(), 'MOBILE_CHART_JS must point to local Chart.js 4.4.0')
        return scripts + [settings.BASE_DIR / 'static' / 'js' / name for name in names]

    def test_reports_chart_values_survive_unavailable_renderer(self):
        page = self.page(320, template='hub/report.html', fixture=self.report_fixture(), scripts=self.chart_scripts(
            'chart-theme.js', 'chart-values.js', 'report-charts.js', chart=False))
        card = page.locator('article').filter(has=page.locator('#requestTrendChart'))
        card.locator('summary').focus()
        page.keyboard.press('Enter')
        self.assertEqual(card.locator('tbody tr').all_inner_texts(), ['<North>\t0\t0', 'July\t—\t1', 'August\t4\t3'])
        self.assertTrue(card.get_by_text('Chart unavailable. Exact values are available below.').is_visible())
        ranking = page.locator('article').filter(has=page.locator('#engineerChart'))
        ranking.get_by_role('button', name='Load more', exact=True).click()
        dialog = page.get_by_role('dialog', name='Requests by Engineer')
        self.assertFalse(dialog.get_by_role('button', name='Download expanded chart as PNG').is_enabled())
        dialog.locator('summary').click()
        self.assertEqual(dialog.locator('tbody th').all_inner_texts(), self.report_fixture()['engineer_chart_full']['labels'])

    def test_reports_rankings_and_expanded_chart_share_values_after_theme_change(self):
        page = self.page(390, template='hub/report.html', fixture=self.report_fixture(), scripts=self.chart_scripts(
            'chart-theme.js', 'chart-values.js', 'report-charts.js'))
        card = page.locator('article').filter(has=page.locator('#engineerChart'))
        button = card.get_by_role('button', name='Load more', exact=True)
        button.click()
        page.wait_for_function("Chart.getChart('rptModalCanvas')")
        self.assertEqual(page.evaluate("Chart.getChart('engineerChart').options.indexAxis"), 'y')
        page.locator('#rptChartModal summary').click()
        self.assertEqual(page.locator('#rptChartModal tbody tr').count(), 12)
        self.assertEqual(page.locator('#rptChartModal tbody th').first.inner_text(), 'North Operations and Shared Infrastructure')
        page.evaluate("""() => {
            window.expandedChart = Chart.getChart('rptModalCanvas');
            document.body.classList.add('dark-mode');
            window.dispatchEvent(new Event('requesthub:theme-changed'));
        }""")
        self.assertTrue(page.evaluate("window.expandedChart === Chart.getChart('rptModalCanvas')"))
        self.assertTrue(page.locator('#rptChartModal details').evaluate('el => el.open'))
        self.assertTrue(page.evaluate("""() => {
            const c = Chart.getChart('rptModalCanvas');
            return c.ctx.getImageData(0, 0, 1, 1).data[3] === 255;
        }"""))
        with page.expect_download() as png:
            page.locator('#rptChartModalPng').click()
        self.assertEqual(png.value.path().read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        page.keyboard.press('Escape')
        self.assertTrue(button.evaluate('el => el === document.activeElement'))

    def test_expanded_chart_controls_fit_phone_and_leave_tab_order_on_close(self):
        page = self.page(320, template='hub/report.html', fixture=self.report_fixture(), scripts=self.chart_scripts(
            'chart-values.js', 'chart-theme.js', 'report-charts.js'))
        card = page.locator('article').filter(has=page.locator('#engineerChart'))
        card.get_by_role('button', name='Load more', exact=True).click()
        inner = page.locator('#rptChartModal .rpt-chart-modal__inner')
        self.assertTrue(inner.evaluate('el => el.scrollWidth <= el.clientWidth'))
        page.keyboard.press('Escape')
        self.assertTrue(page.locator('#rptChartModal').evaluate('el => { el.querySelector("button").focus(); return !el.contains(document.activeElement); }'))

    def test_png_draws_series_key_and_narrow_axis_distinguishes_years(self):
        fixture = self.report_fixture()
        fixture['request_trend_chart']['labels'] = ['Jan 2025', 'Jul 2025', 'Jan 2026']
        page = self.page(375, template='hub/report.html', fixture=fixture, scripts=self.chart_scripts(
            'chart-values.js', 'chart-theme.js', 'report-charts.js'))
        page.get_by_role('button', name='Expand Requests Created vs Completed chart').click()
        page.evaluate('''() => {
            const ctx = Chart.getChart('rptModalCanvas').ctx;
            const draw = ctx.fillText.bind(ctx);
            window.pngText = [];
            ctx.fillText = (...args) => { window.pngText.push(args[0]); draw(...args); };
        }''')
        with page.expect_download():
            page.locator('#rptChartModalPng').click()
        self.assertEqual(page.evaluate("window.pngText.includes('Created') && window.pngText.includes('Completed')"), True)
        ticks = page.evaluate("Chart.getChart('rptModalCanvas').scales.x.ticks.map(t => t.label)")
        self.assertNotIn('Jan', ticks)

    def test_chart_initialization_without_context_keeps_exact_values(self):
        page = self.page(375, template='hub/report.html', fixture=self.report_fixture(), scripts=self.chart_scripts(
            'chart-values.js', 'chart-theme.js'))
        page.evaluate("HTMLCanvasElement.prototype.getContext = () => null")
        page.add_script_tag(content=self.chart_scripts('report-charts.js', chart=False)[0].read_text(encoding='utf-8'))
        card = page.locator('.rh-chart-card').filter(has=page.locator('#requestTrendChart'))
        self.assertTrue(card.get_by_text('Chart unavailable. Exact values are available below.').is_visible())
        card.locator('summary').click()
        self.assertEqual(card.locator('tbody tr').count(), 3)

    def graph_fixture(self, dashboard):
        context = self.context()
        data = {
            'request_status': {'labels': ['Ongoing', 'Completed'], 'values': [2, 7 if dashboard else 5]},
            'request_priority': {'labels': ['High', 'Normal'], 'values': [1, 6]},
            'request_engagement': {'labels': ['Consulting'], 'values': [2]},
            'request_monthly': {'labels': ['Jul 2026'], 'values': [3]},
            'activity_type': {'labels': ['Design'], 'values': [2]},
            'monthly': {'labels': ['Jul 2026'], 'hours': [2.75]},
        }
        return {**context, 'role': 'requestor', 'is_requestor_ui': True, 'metrics': {'ongoing': 2, 'completed': 7},
                'request_report_data' if dashboard else 'activity_report_data': data}

    def tab_scripts(self, *names):
        return [Path(os.environ['MOBILE_BOOTSTRAP_JS']), *self.chart_scripts(*names)]

    def test_graph_tabs_mount_when_shown_and_retheme_without_reset(self):
        for dashboard, path, button, chart_id, value in (
            (False, 'activity_logs', 'activity-graph-tab', 'request-status-chart', 5),
            (True, 'dashboard', 'request-report-graph-tab', 'requestor-status-chart', 7),
        ):
            with self.subTest(view=path):
                page = self.page(320, template=f'hub/{path}.html', fixture=self.graph_fixture(dashboard),
                                 scripts=self.tab_scripts('chart-values.js', 'chart-theme.js', 'request-graphs.js'))
                page.evaluate("window.dispatchEvent(new Event('requesthub:theme-changed'))")
                self.assertFalse(page.evaluate('(id) => !!Chart.getChart(id)', chart_id))
                page.locator('#' + button).click()
                card = page.locator('.rh-chart-card').filter(has=page.locator('#' + chart_id))
                self.assertTrue(page.evaluate('(id) => Chart.getChart(id)?.width > 0', chart_id))
                card.locator('summary').click()
                self.assertEqual(card.locator('tbody tr').all_inner_texts(), [f'Completed\t{value}', 'Ongoing\t2'])
                page.evaluate('''id => {
                    window.shownChart = Chart.getChart(id);
                    window.lightBars = window.shownChart.data.datasets[0].backgroundColor;
                    document.body.classList.add('dark-mode', 'pref-reduce-motion');
                    window.dispatchEvent(new Event('requesthub:theme-changed'));
                }''', chart_id)
                self.assertTrue(page.evaluate('''id => window.shownChart === Chart.getChart(id) &&
                    window.lightBars[0] !== window.shownChart.data.datasets[0].backgroundColor[0]''', chart_id))
                self.assertTrue(card.locator('details').evaluate('el => el.open'))

    def sqr_chart_fixture(self):
        return {**self.context(), 'is_pm': True, 'active_sqr_tab': 'reports', 'sqr_reports_summary': {'off_track_count': 0},
                'sqr_reports_funnel_chart': {'labels': ['Total SQR', 'Approved'], 'totals': [6, 2]},
                'sqr_reports_deal_chart': {'labels': ['Open', 'Closed Won'], 'totals': [4, 2]},
                'sqr_reports_group_chart': {'labels': ['North', 'Central'], 'totals': [2, 4]},
                'sqr_reports_monthly_chart': {'labels': ['Jul 2026', 'Sep 2026'], 'totals': [4, 2]},
                'sqr_reports_revenue_chart': {'labels': ['Closed Won'], 'totals': [12345.67]}}

    def test_sqr_report_live_refresh_preserves_chart_values_focus(self):
        fixture = self.sqr_chart_fixture()
        page = self.page(390, template='hub/sqr.html', fixture=fixture, scripts=self.chart_scripts('chart-values.js', 'chart-theme.js'))
        refresh_url = 'http://request-hub.test/sqr/?tab=reports'
        page.route(refresh_url, lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
        html = page.content()
        page.goto(refresh_url)
        html = re.sub(r'<script\b(?![^>]*type="application/json")[^>]*>.*?</script>', '', html, flags=re.S | re.I)
        page.set_content(html)
        updated = render_to_string('hub/partials/sqr_reports.html', {
            **fixture, 'sqr_reports_revenue_chart': {'labels': ['Closed Won'], 'totals': [12345.68]},
            'sqr_reports_funnel_chart': {'labels': [f'Stage {n}' for n in range(12)], 'totals': list(range(12, 0, -1))},
        })
        page.evaluate('''html => {
            window.fetch = () => Promise.resolve({json: () => Promise.resolve({ok: true, html})});
        }''', updated)
        scripts = self.chart_scripts('chart-values.js', 'chart-theme.js', 'sqr-report-charts.js')
        for script in scripts:
            if script.is_file():
                page.add_script_tag(content=script.read_text(encoding='utf-8'))
        if not scripts[-1].is_file():
            original = render_to_string('hub/sqr.html', fixture)
            for script in re.findall(r'<script>(.*?)</script>', original, re.S):
                if 'window.initSqrReportsCharts =' in script or 'window.refreshSqrReports =' in script:
                    page.add_script_tag(content=script)
        self.assertEqual(page.evaluate("Chart.getChart('sqrMonthlyTrendChart').data.datasets[0].data"), [4, None, 2])
        self.assertEqual(page.locator('[data-chart-values="sqrMonthlyTrendChart"] tbody th').all_text_contents(), ['Jul 2026', 'Sep 2026'])
        card = page.locator('.rh-chart-card').filter(has=page.locator('#sqrRevenueOverviewChart'))
        summary = card.locator('summary')
        summary.focus()
        page.keyboard.press('Enter')
        self.assertEqual(card.locator('tbody td').inner_text(), '₱12,345.67')
        page.evaluate('window.refreshSqrReports()')
        page.wait_for_function("document.querySelector('[data-chart-values=sqrRevenueOverviewChart] tbody td')?.textContent.includes('345.68')")
        restored = summary.evaluate('''el => {
            const { top, bottom } = el.getBoundingClientRect();
            return { focused: document.activeElement === el, open: el.parentElement.open, top, bottom, height: innerHeight };
        }''')
        self.assertTrue(restored['focused'] and restored['open'] and
                        0 <= restored['top'] <= restored['bottom'] <= restored['height'], restored)
        page.evaluate("window.liveRevenue = Chart.getChart('sqrRevenueOverviewChart')")
        page.evaluate('window.refreshSqrReports()')
        self.assertTrue(page.evaluate("window.liveRevenue === Chart.getChart('sqrRevenueOverviewChart') && Object.keys(Chart.instances).length === 5"))

    def test_phone_detection_survives_blocked_storage_and_resize(self):
        page = self.page()
        page.evaluate("Object.defineProperty(window, 'localStorage', {get() {throw new Error('blocked')}})")
        page.evaluate(self.mode_script)
        for width in (320, 390, 520, 600, 767):
            page.set_viewport_size({'width': width, 'height': 844})
            page.wait_for_function("document.body.classList.contains('view-mobile')")
            self.assertFalse(page.evaluate("document.body.classList.contains('view-mobile-preview')"))
        page.set_viewport_size({'width': 768, 'height': 844})
        page.wait_for_function("!document.body.classList.contains('view-mobile')")

    def test_explicit_preview_only_on_desktop(self):
        page = self.page(1280)
        page.evaluate("Object.defineProperty(window, 'localStorage', {value: {getItem: () => 'mobile'}})")
        page.evaluate(self.mode_script)
        self.assertTrue(page.evaluate("document.body.classList.contains('view-mobile-preview')"))
        page.set_viewport_size({'width': 600, 'height': 844})
        page.wait_for_function("!document.body.classList.contains('view-mobile-preview')")
        self.assertTrue(page.evaluate("document.body.classList.contains('view-mobile')"))

    def test_phone_rows_preserve_container_gutters(self):
        page = self.page(320)
        page.locator('main').evaluate("el => el.innerHTML = '<div class=\"row g-4\"><div class=\"col-12\">Content</div></div>'")
        boxes = page.evaluate("""() => {
            const main = document.querySelector('main').getBoundingClientRect();
            const col = document.querySelector('.col-12').getBoundingClientRect();
            return {left: col.left - main.left, right: main.right - col.right};
        }""")
        self.assertGreaterEqual(boxes['left'], 10)
        self.assertGreaterEqual(boxes['right'], 10)

    def test_phone_controls_are_readable_and_touch_sized(self):
        page = self.page()
        page.locator('main').evaluate("""el => el.innerHTML = '<p>Readable text</p><label>Account</label>' +
            '<input class="mgmt-search-input"><button class="btn-close" aria-label="Close"></button>' +
            '<a class="nav-link" href="#">Reports</a>'""")
        for selector in ('.mgmt-search-input',):
            self.assertGreaterEqual(page.locator(selector).evaluate('el => parseFloat(getComputedStyle(el).fontSize)'), 16)
        for selector in ('.btn-close', '.nav-link'):
            box = page.locator(selector).bounding_box()
            self.assertGreaterEqual(box['height'], 44)
            self.assertGreaterEqual(box['width'], 44)
        self.assertGreaterEqual(page.locator('p').evaluate('el => parseFloat(getComputedStyle(el).fontSize)'), 14)

    def test_dashboard_and_sqr_contain_scrolling_tables(self):
        for template in ('hub/dashboard.html', 'hub/sqr.html'):
            for width in (320, 390, 520, 600, 767, 768, 1280):
                with self.subTest(template=template, width=width):
                    page = self.page(width, template=template)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                    wrapper = page.locator('.table-responsive').first
                    if wrapper.count():
                        wrapper.evaluate("el => {el.querySelector('table').style.minWidth = '2000px'; el.scrollLeft = 80}")
                        self.assertGreater(wrapper.evaluate('el => el.scrollLeft'), 0)
                        self.assertLessEqual(wrapper.bounding_box()['width'], width)

    def test_ppt_hides_sqr_header_from_view_and_keyboard(self):
        template = (settings.BASE_DIR / 'templates' / 'hub' / 'sqr.html').read_text(encoding='utf-8')
        view_script = next(script for attrs, script in extract_scripts(template)
                           if 'src' not in attrs and '// ── SQR table view toggle' in script)
        for width, dark in ((375, False), (375, True), (1280, False), (1280, True)):
            with self.subTest(width=width, dark=dark):
                page = self.page(width, template='hub/sqr.html', dark=dark,
                                 fixture={**self.context(), 'show_classic_sqr_tracker': True})
                page.locator('main').evaluate("el => el.insertAdjacentHTML('beforeend', '<section data-sqr-view-panel=\"performance\" hidden></section>')")
                page.evaluate(view_script)
                page.evaluate("window.setSqrView('performance')")
                header = page.locator('[data-sqr-header]')
                button = header.locator('[data-sqr-view-button="tracker"]')
                heading = page.get_by_role('heading', name='Service Quotation Request', exact=True)
                self.assertFalse(header.is_visible())
                self.assertEqual(heading.count(), 0)
                self.assertFalse(button.evaluate("el => { el.focus(); return document.activeElement === el; }"))
                page.evaluate("window.setSqrView('tracker')")
                self.assertTrue(header.is_visible())
                self.assertEqual(heading.count(), 1)
                self.assertTrue(button.evaluate("el => { el.focus(); return document.activeElement === el; }"))

    def test_navigation_remains_touch_and_keyboard_accessible(self):
        page = self.page(320, template='hub/dashboard.html')
        toggle = page.locator('.navbar-toggler')
        self.assertEqual(toggle.get_attribute('aria-label'), 'Toggle navigation')
        page.evaluate(self.navbar_script)
        toggle.click()
        page.locator('#navbarNav.show').wait_for()
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        avatar = page.locator('#nav-avatar-trigger')
        self.assertGreaterEqual(avatar.bounding_box()['width'], 44)
        avatar.focus()
        page.keyboard.press('Tab')
        self.assertTrue(toggle.evaluate('el => document.activeElement === el'))
        avatar.focus()
        page.keyboard.press('Enter')
        menu = page.locator('#nav-avatar-menu')
        self.assertEqual(menu.get_attribute('aria-hidden'), 'false')
        box = menu.bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 320)
        page.keyboard.press('Escape')
        self.assertEqual(menu.get_attribute('aria-hidden'), 'true')

    def test_reports_navigation_toggles_while_chart_script_is_loading(self):
        fixture = {**self.report_fixture(), 'csrf_token': '0' * 32}
        html = render_to_string('hub/report.html', fixture)
        page = self.browser.new_page(viewport={'width': 792, 'height': 912}, reduced_motion='reduce')
        self.addCleanup(page.close)
        url = 'http://request-hub.test/reports/'
        pending = []

        def serve(route):
            request_url = route.request.url
            path = urlsplit(request_url).path
            if request_url == url:
                route.fulfill(body=html, content_type='text/html')
            elif path.endswith('/chart.umd.min.js'):
                pending.append(route)
            elif path.endswith('/bootstrap.min.css'):
                route.fulfill(body=self.bootstrap, content_type='text/css')
            elif request_url.startswith('http://request-hub.test/') and path.startswith(settings.STATIC_URL):
                asset = settings.BASE_DIR / 'static' / path[len(settings.STATIC_URL):]
                if asset.is_file():
                    route.fulfill(path=asset)
                else:
                    route.abort()
            else:
                route.abort()

        page.route('**/*', serve)
        try:
            with page.expect_request('**/chart.umd.min.js', timeout=3000):
                page.goto(url, wait_until='commit')
            page.locator('script[src$="/chart.umd.min.js"]').wait_for(state='attached', timeout=3000)
            toggle = page.get_by_role('button', name='Toggle navigation')
            drawer = page.locator('#navbarNav')
            self.assertEqual(page.evaluate('document.readyState'), 'loading')
            toggle.click(timeout=3000)
            self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
            drawer.get_by_role('link', name='Dashboard', exact=True).wait_for(state='visible', timeout=3000)
            toggle.press('Enter', timeout=3000)
            self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
            drawer.wait_for(state='hidden', timeout=3000)
            self.assertEqual(len(pending), 1)
            self.assertEqual(page.evaluate('document.readyState'), 'loading')
        finally:
            for route in pending:
                route.abort()

    def navigation_page(self, width, height=844, *, dark=False, seasonal=False, font_size=16, reduce_motion=True):
        fixture = {**self.report_fixture(), 'new_ticket_count': 128, 'csrf_token': '0' * 32}
        fixture['request'].user.christmas_default_overridden = True
        fixture['request'].user.banner_gradient = 'christmas' if seasonal else User.DEFAULT_THEME
        page = self.page(width, height, template='hub/report.html', fixture=fixture, dark=dark)
        assets = Path(os.environ['MOBILE_BOOTSTRAP_CSS']).parent
        icons = (assets / 'bootstrap-icons.css').read_text(encoding='utf-8')
        font = base64.b64encode((assets / 'bootstrap-icons.woff2').read_bytes()).decode('ascii')
        icons = re.sub(r'@font-face\s*\{[^}]+\}',
                       f'@font-face{{font-family:bootstrap-icons;src:url(data:font/woff2;base64,{font})}}', icons, count=1)
        page.emulate_media(reduced_motion='reduce' if reduce_motion else 'no-preference')
        page.add_style_tag(content=icons)
        page.evaluate('''([fontSize, dark, seasonal, reduceMotion]) => {
            document.documentElement.style.fontSize = `${fontSize}px`;
            document.body.classList.toggle('pref-reduce-motion', reduceMotion);
            const nav = document.querySelector('[data-nav-surface]');
            if (dark) nav.dataset.navSurface = 'dark';
            if (seasonal) nav.classList.add('seasonal-lights--active');
            return document.fonts.ready.then(() => undefined);
        }''', [font_size, dark, seasonal, reduce_motion])
        page.wait_for_function('size => getComputedStyle(document.documentElement).fontSize === `${size}px`', arg=font_size)
        page.evaluate(self.navbar_script)
        return page

    def test_phone_and_profile_preview_keep_navigation_controls_on_one_row(self):
        for width, preview, font_size in ((320, False, 16), (320, False, 20), (375, False, 16), (992, False, 16), (1440, True, 16)):
            with self.subTest(width=width, preview=preview, font_size=font_size):
                page = self.navigation_page(width, font_size=font_size)
                if preview:
                    page.evaluate("Object.defineProperty(window, 'localStorage', {value: {getItem: () => 'mobile'}})")
                    page.evaluate(self.mode_script)
                toggle = page.get_by_role('button', name='Toggle navigation')
                toggle.click()
                drawer = page.locator('#navbarNav.show')
                drawer.wait_for()
                boxes = [page.locator(selector).bounding_box() for selector in (
                    '.navbar-brand', '.navbar .checkbox-label', '#nav-avatar-trigger', '.navbar-toggler',
                )]
                self.assertLessEqual(max(box['y'] for box in boxes),
                                     min(box['y'] + box['height'] for box in boxes), boxes)
                for left, right in zip(boxes, boxes[1:]):
                    self.assertLessEqual(left['x'] + left['width'], right['x'] + 1, boxes)
                self.assertLessEqual(max(box['y'] + box['height'] for box in boxes), drawer.bounding_box()['y'])
                for control in boxes[1:]:
                    self.assertGreaterEqual(min(control['height'], control['width']), 44)

    def test_short_navigation_animates_through_intermediate_heights_in_both_directions(self):
        page = self.navigation_page(640, 280, dark=True, seasonal=True, reduce_motion=False)
        toggle = page.get_by_role('button', name='Toggle navigation')
        sample = '''button => {
            const drawer = document.getElementById(button.getAttribute('aria-controls'));
            const height = () => drawer.getBoundingClientRect().height;
            height();
            button.click();
            const animation = drawer.getAnimations().find(a => a.transitionProperty === 'grid-template-rows');
            if (!animation) return [height(), height(), height()];
            animation.pause();
            const duration = animation.effect.getComputedTiming().duration;
            const heights = [0, duration / 2, duration].map(time => {
                animation.currentTime = time;
                return height();
            });
            animation.finish();
            return heights;
        }'''
        opening = toggle.evaluate(sample)
        self.assertEqual(opening[0], 0, opening)
        self.assertGreater(opening[1], opening[0], opening)
        self.assertLess(opening[1], opening[2], opening)
        nav = page.get_by_role('navigation', name='Main navigation').bounding_box()
        self.assertLessEqual(nav['y'] + nav['height'], 280)
        closing = toggle.evaluate(sample)
        self.assertAlmostEqual(closing[0], opening[2], delta=1)
        self.assertGreater(closing[0], closing[1], closing)
        self.assertGreater(closing[1], closing[2], closing)
        self.assertEqual(closing[2], 0, closing)

    def test_compact_navigation_reverses_and_keeps_its_state_across_breakpoint(self):
        page = self.navigation_page(640, 280, reduce_motion=False)
        toggle = page.locator('.navbar-toggler')
        drawer = page.locator('#navbarNav')
        heights = toggle.evaluate('''button => {
            const drawer = document.getElementById(button.getAttribute('aria-controls'));
            const height = () => drawer.getBoundingClientRect().height;
            height();
            button.click();
            const opening = drawer.getAnimations().find(a => a.transitionProperty === 'grid-template-rows');
            if (opening) {
                opening.pause();
                opening.currentTime = opening.effect.getComputedTiming().duration / 3;
            }
            const before = height();
            button.click();
            return [before, height()];
        }''')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertAlmostEqual(*heights, delta=2)
        reports = drawer.locator('a[href]').filter(has_text='Reports')
        self.assertFalse(reports.evaluate('el => { el.focus(); return el === document.activeElement; }'))
        page.set_viewport_size({'width': 1440, 'height': 460})
        self.assertFalse(toggle.is_visible())
        drawer.get_by_role('link', name='Reports').focus()
        self.assertTrue(drawer.get_by_role('link', name='Reports').evaluate('el => el === document.activeElement'))
        page.set_viewport_size({'width': 640, 'height': 280})
        self.assertTrue(toggle.is_visible())
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        page.wait_for_function("document.activeElement === document.querySelector('.navbar-toggler')")
        self.assertFalse(reports.evaluate('el => { el.focus(); return el === document.activeElement; }'))
        toggle.click()
        page.set_viewport_size({'width': 1440, 'height': 460})
        page.set_viewport_size({'width': 640, 'height': 280})
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        drawer.get_by_role('link', name='Reports').wait_for()

    def test_christmas_navigation_keeps_rounded_panel_corners_in_compact_layouts(self):
        for width, preview in ((640, False), (1037, False), (1440, False), (1440, True)):
            with self.subTest(width=width, preview=preview):
                page = self.navigation_page(width, 460, dark=True, seasonal=True)
                if preview:
                    page.evaluate("document.body.classList.add('view-mobile', 'view-mobile-preview')")
                nav = page.get_by_role('navigation', name='Main navigation')
                radius = 'el => parseFloat(getComputedStyle(el).borderTopLeftRadius)'
                if width == 1440 and not preview:
                    self.assertGreaterEqual(nav.evaluate(radius), nav.bounding_box()['height'] / 2)
                    continue
                toggle = page.get_by_role('button', name='Toggle navigation')
                toggle.click()
                self.assertLess(nav.evaluate(radius), toggle.bounding_box()['height'])
                self.assertTrue(page.locator('#navbarNav').get_by_role('link', name='Reports').is_visible())
                self.assertEqual(page.locator('#navbarNav').evaluate('el => el.getAnimations().length'), 0)

    def test_account_menu_scrolls_to_logout_on_short_viewports(self):
        page = self.navigation_page(320, 280, dark=True, seasonal=True, font_size=20)
        avatar = page.locator('#nav-avatar-trigger')
        avatar.focus()
        page.keyboard.press('Enter')
        menu = page.locator('#nav-avatar-menu')
        page.keyboard.press('Tab')
        self.assertTrue(menu.get_by_role('link', name='Settings').evaluate('el => el === document.activeElement'))
        page.keyboard.press('Tab')
        logout = menu.get_by_role('button', name='Log Out')
        self.assertTrue(logout.evaluate('el => el === document.activeElement'))
        self.assertEqual(page.evaluate('scrollY'), 0)
        self.assertGreater(menu.evaluate('el => el.scrollTop'), 0)
        box = logout.bounding_box()
        self.assertLessEqual(box['y'] + box['height'], 280)
        page.keyboard.press('Escape')
        self.assertTrue(avatar.evaluate('el => el === document.activeElement'))
        self.assertEqual(avatar.get_attribute('aria-expanded'), 'false')
        self.assertEqual(menu.get_by_role('button', name='Log Out').count(), 0)

    def test_navigation_fits_at_tablet_and_desktop_widths(self):
        for width, font_size, compact in ((992, 16, True), (1200, 20, True), (1440, 16, False), (1440, 20, False)):
            with self.subTest(width=width, font_size=font_size):
                page = self.navigation_page(width, font_size=font_size)
                toggle = page.get_by_role('button', name='Toggle navigation')
                drawer = page.locator('#navbarNav')
                self.assertEqual(toggle.is_visible(), compact)
                if compact:
                    self.assertFalse(drawer.is_visible())
                    toggle.tap()
                    drawer.wait_for()
                nav = page.get_by_role('navigation', name='Main navigation').bounding_box()
                notifications = drawer.get_by_role('link', name='Notifications', exact=False)
                self.assertEqual(notifications.locator('[data-notification-count]').text_content(), '128')
                for control in (notifications, page.locator('#nav-avatar-trigger')):
                    box = control.bounding_box()
                    self.assertGreaterEqual(box['x'], nav['x'])
                    self.assertLessEqual(box['x'] + box['width'], nav['x'] + nav['width'], box)
                if not compact:
                    links = [link.bounding_box() for link in drawer.get_by_role('link').all()]
                    self.assertAlmostEqual(max(box['y'] for box in links), min(box['y'] for box in links), delta=1)

    def test_short_landscape_navigation_scrolls_to_keyboard_focused_links(self):
        for width, height, seasonal in ((820, 260, False), (640, 280, True)):
            with self.subTest(width=width, height=height, seasonal=seasonal):
                page = self.navigation_page(width, height, dark=True, seasonal=seasonal)
                toggle = page.get_by_role('button', name='Toggle navigation')
                toggle.focus()
                page.keyboard.press('Enter')
                drawer = page.locator('#navbarNav.show')
                drawer.wait_for()
                nav = page.get_by_role('navigation', name='Main navigation').bounding_box()
                self.assertLessEqual(nav['y'] + nav['height'], height)
                for name in ('Dashboard', 'PPT', 'SQR', 'Management', 'Reports', 'Notifications'):
                    page.keyboard.press('Tab')
                    link = drawer.get_by_role('link', name=name)
                    self.assertTrue(link.evaluate('el => document.activeElement === el'), name)
                self.assertEqual(page.evaluate('scrollY'), 0)
                self.assertGreater(drawer.locator('.navbar-nav').evaluate('el => el.scrollTop'), 0)
                box = link.bounding_box()
                self.assertGreaterEqual(box['y'], 0)
                self.assertLessEqual(box['y'] + box['height'], height)
                page.keyboard.press('Escape')
                page.locator('#navbarNav').wait_for(state='hidden')
                self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
                self.assertTrue(toggle.evaluate('el => document.activeElement === el'))

    def test_shared_pages_fit_phone_and_tablet_in_both_themes(self):
        for template in ('hub/management.html', 'hub/report.html', 'hub/notifications.html',
                         'accounts/profile_form.html', 'accounts/login.html'):
            for width, dark in ((320, False), (600, True), (768, False), (1280, True)):
                with self.subTest(template=template, width=width, dark=dark):
                    page = self.page(width, template=template, dark=dark)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                    folder = os.environ.get('MOBILE_SCREENSHOT_DIR')
                    if folder and width == 600:
                        page.screenshot(path=str(Path(folder) / (Path(template).stem + '-600.png')))

    def test_phone_dialog_fits_landscape_and_scrolls(self):
        page = self.page(600, 360)
        page.locator('main').evaluate("""el => el.innerHTML = '<div class="modal show" style="display:block">' +
            '<div class="modal-dialog"><div class="modal-content"><div class="modal-header">Title</div>' +
            '<div class="modal-body"><div style="height:1000px">Content</div></div>' +
            '<div class="modal-footer"><button class="btn btn-primary">Save</button></div></div></div></div>'""")
        box = page.locator('.modal-content').bounding_box()
        self.assertLessEqual(box['height'], 344)
        body = page.locator('.modal-body')
        body.evaluate('el => el.scrollTop = 80')
        self.assertGreater(body.evaluate('el => el.scrollTop'), 0)

    def test_phone_ambient_is_static_without_compositor_hints(self):
        for dark in (False, True):
            page = self.page(dark=dark)
            page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div class=\"ambient-mesh\"><span class=\"ambient-mesh__orb ambient-mesh__orb--1\"></span></div>')")
            orb = page.locator('.ambient-mesh__orb')
            self.assertEqual(orb.evaluate('el => getComputedStyle(el).animationName'), 'none')
            self.assertEqual(orb.evaluate('el => getComputedStyle(el).willChange'), 'auto')

    def test_phone_login_does_not_subscribe_to_tilt_animation(self):
        page = self.page()
        page.evaluate("""() => {
            document.body.classList.add('page-login');
            document.querySelector('main').innerHTML = '<div class="login-card"><div class="login-card__lens"></div></div>';
            window.DeviceOrientationEvent = window.DeviceOrientationEvent || function () {};
            window.tiltSubscriptions = 0;
            const original = window.addEventListener.bind(window);
            window.addEventListener = (type, ...args) => {
                if (type === 'deviceorientation') window.tiltSubscriptions++;
                return original(type, ...args);
            };
        }""")
        page.evaluate((settings.BASE_DIR / 'static' / 'js' / 'liquid-glass.js').read_text(encoding='utf-8'))
        self.assertEqual(page.evaluate('window.tiltSubscriptions'), 0)

    def test_desktop_and_reduced_motion_remain_supported(self):
        page = self.page(1280)
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div class=\"ambient-mesh\"><span class=\"ambient-mesh__orb ambient-mesh__orb--1\"></span></div>')")
        orb = page.locator('.ambient-mesh__orb')
        self.assertNotEqual(orb.evaluate('el => getComputedStyle(el).animationName'), 'none')
        page.emulate_media(reduced_motion='reduce')
        self.assertEqual(orb.evaluate('el => getComputedStyle(el).animationName'), 'none')
        self.assertEqual(orb.evaluate('el => getComputedStyle(el).willChange'), 'auto')
