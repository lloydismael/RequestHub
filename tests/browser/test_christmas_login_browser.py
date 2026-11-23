"""Exercise the live sign-in template and its own assets without network or live accounts."""

import os
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from scripts.capture_release_changelog import ORIGIN, ROOT, SWIFTSHADER_ARGS, VENDOR_ASSETS, _render

CARD = ".login-card"
WIRE = ".seasonal-light-wire"
BULB = ".seasonal-light-bulb"
RAYS = ".seasonal-canopy-rays"
SNOW = ".xmas-scene .xmas-snow"
SPILL = ".xmas-light-spill"
CANVAS_SAMPLE = """selector => {
    const source = document.querySelector(selector);
    if (!source?.getBoundingClientRect().width) return null;
    const canvas = document.createElement('canvas');
    canvas.width = 120;
    canvas.height = 80;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    const {data} = ctx.getImageData(0, 0, canvas.width, canvas.height);
    let hash = 0, total = 0, coloredBelow = 0;
    for (let i = 0; i < data.length; i += 4) {
        const [r, g, b, alpha] = data.subarray(i, i + 4);
        hash = Math.imul(hash, 31) + alpha | 0;
        total += alpha;
        if (i >= data.length / 2 && alpha >= 8 && Math.max(r,g,b) - Math.min(r,g,b) >= 35) coloredBelow++;
    }
    return [hash, total, coloredBelow];
}"""


class ChristmasLoginBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vendor = Path(os.environ.get("CHANGELOG_CAPTURE_VENDOR_DIR") or ROOT / ".changelog-capture-vendor")
        if not cls.vendor.is_dir():
            raise unittest.SkipTest("Prepare offline vendor assets with capture_release_changelog.py --prepare-assets.")
        cls.login_html = _render(ROOT, {"kind": "ui", "fixture": "login"})
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(args=list(SWIFTSHADER_ARGS))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1200, "height": 900}, service_workers="block")
        self.addCleanup(self.context.close)
        self.context.route("**/*", self.serve)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def serve(self, route):
        url = urlsplit(route.request.url)
        key = f"{url.scheme}://{url.netloc}{url.path}"
        if key == f"{ORIGIN}/":
            route.fulfill(content_type="text/html", body=self.document)
            return
        if key in VENDOR_ASSETS:
            path = self.vendor / VENDOR_ASSETS[key]
        elif key.startswith(f"{ORIGIN}/static/"):
            path = ROOT / "static" / url.path.removeprefix("/static/")
        else:
            route.abort()
            return
        if path.is_file():
            route.fulfill(path=path)
        else:
            route.abort()

    def open_login(self, *, rays=False, seasonal=True):
        # The synthetic login renders December 31. Remove the seasonal default to
        # exercise an ordinary first visit outside that server-controlled window.
        self.document = self.login_html.replace('class="login-christmas"', 'class=""', 1)
        self.page.add_init_script(f"""(() => {{
            localStorage.setItem('reqHubTheme', 'dark');
            if ({str(seasonal).lower()}) localStorage.setItem('reqHubLoginTheme', 'christmas');
            const context = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, options) {{
                if (type !== 'webgl2') return context.call(this, type, options);
                return {str(rays).lower()} ? context.call(this, type, {{...options, failIfMajorPerformanceCaveat: false}}) : null;
            }};
        }})()""")
        self.page.goto(ORIGIN)
        expect(self.page.locator("body")).to_have_class(re.compile(r"\bdark-mode\b"))
        self.assertEqual(self.page.locator("html").evaluate("el => el.classList.contains('login-christmas')"), seasonal)

    def sample(self, selector):
        return self.page.evaluate(CANVAS_SAMPLE, selector)

    def assert_snow_stops(self):
        frozen = self.sample(SNOW)
        self.assertGreater(frozen[1], 0, "A still snow frame should remain visible")
        self.page.evaluate("""() => new Promise(resolve => {
            const start = performance.now();
            const tick = now => now - start >= 160 ? resolve() : requestAnimationFrame(tick);
            requestAnimationFrame(tick);
        })""")
        self.assertEqual(self.sample(SNOW), frozen, "Snow kept moving with animation disabled")

    def test_card_starts_with_a_traveling_glow_and_no_effects_disclosure(self):
        self.page.add_init_script("localStorage.setItem('reqHubLoginChristmasMotionPaused', '1')")
        self.open_login()
        card = self.page.locator(CARD)
        expect(card.locator(BULB)).to_have_count(36)
        wire = card.locator(WIRE).first
        self.assertEqual(card.locator(WIRE).count(), 2)
        self.assertTrue(all(path.get_attribute("d").endswith("Z") for path in card.locator(WIRE).all()))
        positions = card.locator(BULB).evaluate_all("""bulbs => bulbs.map(bulb => {
            const p = bulb.transform.baseVal.getItem(0).matrix;
            return [p.e, p.f];
        })""")
        width, height = card.evaluate("el => [el.clientWidth, el.clientHeight]")
        for edge in (lambda x,y: x < 14, lambda x,y: x > width - 14,
                     lambda x,y: y < 14, lambda x,y: y > height - 14):
            self.assertGreaterEqual(sum(edge(*p) for p in positions), 4, "Bulbs should not bunch on the short edges")
        reflection = self.sample(SPILL)
        self.assertIsNotNone(reflection)
        self.assertGreater(reflection[2], 20, "Dark mode should reflect colored bulb light below the glass")
        snow = self.sample(SNOW)
        self.page.wait_for_function(f"old => ({CANVAS_SAMPLE})('{SNOW}')[0] !== old", arg=snow[0])

        lamp = card.locator(BULB).first
        rest_lamp = lamp.get_attribute("transform")
        expect(card.locator(".seasonal-light-emitter").first).not_to_have_css("opacity", "1", timeout=3000)
        self.page.wait_for_function(f"old => ({CANVAS_SAMPLE})('{SPILL}')[0] !== old", arg=reflection[0])
        self.assertEqual(lamp.get_attribute("transform"), rest_lamp)
        expect(self.page.get_by_text("Christmas effects", exact=True)).to_have_count(0)

        self.page.emulate_media(reduced_motion="reduce")
        expect(card.locator(".seasonal-light-emitter").first).to_have_css("opacity", "1")
        self.assert_snow_stops()
        self.assertGreater(self.sample(SPILL)[2], 20, "Reduced motion should keep steady reflections")
        self.page.get_by_role("textbox", name="Username").click()
        expect(self.page.get_by_role("textbox", name="Username")).to_be_focused()
        self.assertEqual(self.page.locator("form.login-form").get_attribute("method"), "post")

    def test_cord_can_be_grabbed_where_it_crosses_inside_the_card(self):
        self.open_login()
        card = self.page.locator(CARD)
        hits = card.locator(".seasonal-cord-hit")
        expect(hits).to_have_count(2)
        card.evaluate("el => Promise.all(el.getAnimations().map(animation => animation.finished))")
        wire = card.locator(WIRE).first
        for index, side in enumerate(("top", "bottom")):
            with self.subTest(side=side):
                point = wire.evaluate("""(wire, side) => {
                    const {width, height} = wire.ownerSVGElement.viewBox.baseVal;
                    const length = wire.getTotalLength();
                    let deepest = .5;
                    let target = null;
                    for (let distance = 0; distance < length; distance += 2) {
                        const point = wire.getPointAtLength(distance);
                        const depth = side === 'top' ? point.y : height - point.y;
                        if (point.x > width * .18 && point.x < width * .82 && depth > deepest && depth < 12) {
                            deepest = depth;
                            target = point;
                        }
                    }
                    if (!target) return null;
                    const {x, y} = target.matrixTransform(wire.getScreenCTM());
                    return [x, y];
                }""", side)
                self.assertIsNotNone(point, f"The {side} wire should cross inside the rim")
                x, y = point
                rest_wire = wire.get_attribute("d")
                self.page.mouse.move(x, y)
                self.page.mouse.down()
                try:
                    expect(hits.nth(index)).to_have_class(re.compile(r"\bis-grabbing\b"), timeout=1500)
                    self.page.mouse.move(x + 6, y + (-16 if side == "top" else 16))
                    self.page.wait_for_function(
                        """rest => document.querySelector('.seasonal-light-wire').getAttribute('d') !== rest""",
                        arg=rest_wire,
                    )
                finally:
                    self.page.mouse.up()
                expect(hits.nth(index)).not_to_have_class(re.compile(r"\bis-grabbing\b"))

    def test_dark_reflections_keep_the_last_frame_while_hidden(self):
        self.open_login()
        expect(self.page.locator(BULB)).to_have_count(36)
        self.page.locator(CARD).evaluate("card => Promise.all(card.getAnimations().map(animation => animation.finished))")
        counts = self.page.locator(SPILL).evaluate("""async canvas => {
            const context = canvas.getContext('2d');
            const clear = context.clearRect.bind(context);
            const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            let paints = 0;
            context.clearRect = (...args) => { paints++; clear(...args); };
            try {
                window.dispatchEvent(new Event('scroll'));
                window.dispatchEvent(new Event('pagehide'));
                await nextFrame();
                const hidden = paints;
                window.dispatchEvent(new Event('pageshow'));
                await nextFrame();
                return [hidden, paints - hidden];
            } finally {
                delete context.clearRect;
            }
        }""")
        self.assertEqual(counts[0], 0, "Hiding the page should preserve the last painted reflection")
        self.assertGreater(counts[1], 0, "Showing the page should realign the reflections")

    def test_short_screen_reflections_follow_the_card_when_scrolling(self):
        self.page.set_viewport_size({"width": 844, "height": 390})
        self.page.emulate_media(reduced_motion="reduce")
        self.open_login()
        card = self.page.locator(CARD)
        expect(card.locator(BULB)).to_have_count(24)
        sample = """() => {
            const card = document.querySelector('.login-card').getBoundingClientRect();
            const spill = document.querySelector('.xmas-light-spill');
            const frame = spill.getBoundingClientRect();
            const x = Math.round((card.left - frame.left) * spill.width / frame.width);
            const y = Math.round((card.bottom - frame.top + 2) * spill.height / frame.height);
            const pixels = spill.getContext('2d').getImageData(x, y, Math.floor(card.width), 8).data;
            return Array.from(pixels.filter((_, index) => index % 4 === 3));
        }"""
        def scroll_to_card_bottom(offset):
            scroll = card.evaluate("""(card, offset) => {
                const top = Math.ceil(card.getBoundingClientRect().bottom + scrollY + offset - innerHeight);
                window.scrollTo({top, behavior: 'instant'});
                return scrollY;
            }""", offset)
            self.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            return scroll

        before = scroll_to_card_bottom(12)
        old = self.page.evaluate(sample)
        total = sum(old)
        self.assertGreater(total, 1000, "The snow below the card should receive light")

        self.assertGreater(scroll_to_card_bottom(18), before, "The short layout must actually scroll")
        new = self.page.evaluate(sample)
        difference = sum(abs(a - b) for a, b in zip(old, new))
        self.assertLess(difference, total * 0.02, "The reflected pattern should follow the card")

    def test_phone_keeps_snow_and_sign_in_without_lights_then_recovers_on_resize(self):
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.page.emulate_media(reduced_motion="reduce")
        self.open_login()
        expect(self.page.locator(SNOW)).to_be_visible()
        expect(self.page.locator(SPILL)).to_be_hidden()
        expect(self.page.locator(BULB)).to_have_count(0)
        expect(self.page.get_by_role("button", name="Sign in", exact=True)).to_be_in_viewport()
        self.assert_snow_stops()
        for width, height, bulbs in ((479, 800, 0), (480, 800, 24), (844, 390, 24), (1024, 840, 36)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                expect(self.page.locator(BULB)).to_have_count(bulbs)
                self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
        self.page.locator("#theme-checkbox").press("Space")
        expect(self.page.locator(SPILL)).to_be_hidden()
        self.page.emulate_media(reduced_motion="no-preference")
        self.assert_snow_stops()
        expect(self.page.locator(f"{CARD} .seasonal-light-emitter").first).not_to_have_css("opacity", "1")

    def test_ray_context_loss_keeps_bulbs_and_reflections(self):
        self.open_login(rays=True)
        layer = self.page.locator(f"{CARD} {RAYS}")
        expect(layer).to_be_visible(timeout=15000)
        self.page.locator(CARD).evaluate("card => Promise.all(card.getAnimations().map(animation => animation.finished))")
        self.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        expect(self.page.locator(BULB).first).to_be_hidden()
        layer.evaluate("c => c.getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()")
        expect(layer).to_have_count(0)
        expect(self.page.locator(BULB).first).to_be_visible()
        reflection = self.sample(SPILL)
        self.assertGreater(reflection[2], 20, "The SVG fallback still reflects the colored bulbs")
        self.page.wait_for_function(f"old => ({CANVAS_SAMPLE})('{SPILL}')[0] !== old", arg=reflection[0])

    def test_default_login_has_no_seasonal_effects(self):
        self.open_login(seasonal=False)
        expect(self.page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
        expect(self.page.get_by_text("Christmas effects", exact=True)).to_be_hidden()
        expect(self.page.locator(SNOW)).to_have_count(0)
        expect(self.page.locator(BULB)).to_have_count(0)
