import os
import re
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

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

    def test_reports_rankings_and_expanded_chart_share_values_after_theme_change(self):
        page = self.page(390, template='hub/report.html', fixture=self.report_fixture(), scripts=self.chart_scripts(
            'chart-theme.js', 'chart-values.js', 'report-charts.js'))
        button = page.get_by_role('button', name='Expand Top 10 Engineers + Others chart')
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
        button = page.locator('[data-expand-chart="engineerChart"]')
        button.click()
        inner = page.locator('#rptChartModal .rpt-chart-modal__inner')
        self.assertTrue(inner.evaluate('el => el.scrollWidth <= el.clientWidth'))
        page.keyboard.press('Escape')
        self.assertTrue(page.locator('#rptChartModal').evaluate('el => { el.querySelector("button").focus(); return !el.contains(document.activeElement); }'))

    def test_png_draws_series_key_and_narrow_axis_distinguishes_years(self):
        fixture = self.report_fixture()
        fixture['request_trend_chart']['labels'] = ['Jan 2025', 'Jul 2025', 'Jan 2026']
        page = self.page(375, template='hub/report.html', fixture=fixture, scripts=self.chart_scripts(
            'chart-values.js', 'chart-theme.js', 'report-charts.js'))
        page.locator('[data-expand-chart="requestTrendChart"]').click()
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
        page.add_script_tag(content=Path(os.environ['MOBILE_BOOTSTRAP_JS']).read_text(encoding='utf-8'))
        toggle.click()
        page.locator('#navbarNav.show').wait_for()
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        base = (settings.BASE_DIR / 'templates' / 'base.html').read_text(encoding='utf-8')
        avatar_script = next(script for attrs, script in extract_scripts(base)
                             if 'src' not in attrs and '// Nav avatar dropdown' in script)
        page.evaluate(avatar_script)
        avatar = page.locator('#nav-avatar-trigger')
        self.assertGreaterEqual(avatar.bounding_box()['width'], 44)
        avatar.focus()
        page.keyboard.press('Enter')
        menu = page.locator('#nav-avatar-menu')
        self.assertEqual(menu.get_attribute('aria-hidden'), 'false')
        box = menu.bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 320)
        page.keyboard.press('Escape')
        self.assertEqual(menu.get_attribute('aria-hidden'), 'true')

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
