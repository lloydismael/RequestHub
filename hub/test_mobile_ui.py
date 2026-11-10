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
        cls.mode_script = next(script for script in re.findall(r'<script>(.*?)</script>', base, re.S | re.I)
                               if 'function syncViewMode' in script)

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

    def page(self, width=390, height=844, template=None, dark=False):
        context = self.browser.new_context(viewport={'width': width, 'height': height},
                                           is_mobile=True, has_touch=True)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(3000)
        page.route('**/*', lambda route: route.abort())
        html = render_to_string(template, self.context(anonymous=template == 'accounts/login.html')) if template else (
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<main class="container"></main>')
        # Keep all verification local: no remote scripts, user data or CDN requests.
        html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S | re.I)
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
        return page

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

    def test_navigation_remains_touch_and_keyboard_accessible(self):
        page = self.page(320, template='hub/dashboard.html')
        toggle = page.locator('.navbar-toggler')
        self.assertEqual(toggle.get_attribute('aria-label'), 'Toggle navigation')
        page.add_script_tag(content=Path(os.environ['MOBILE_BOOTSTRAP_JS']).read_text(encoding='utf-8'))
        toggle.click()
        page.locator('#navbarNav.show').wait_for()
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        base = (settings.BASE_DIR / 'templates' / 'base.html').read_text(encoding='utf-8')
        avatar_script = next(script for script in re.findall(r'<script>(.*?)</script>', base, re.S | re.I)
                             if '// Nav avatar dropdown' in script)
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
