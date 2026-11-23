"""Exercise the production canopy renderers in offline Chromium."""

import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def fixture_html():
    styles = (ROOT / "static" / "css" / "christmas-lights.css").as_uri()
    scripts = "".join(
        f'<script src="{(ROOT / "static" / "js" / name).as_uri()}"></script>'
        for name in ("christmas-theme-snapshot.js", "christmas-canopy.js", "christmas-cord.js", "christmas-lights.js")
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
        <link rel="stylesheet" href="{styles}">
        <style>body{{margin:0}} .navbar{{box-sizing:border-box;position:relative;width:calc(100% - 48px);height:76px;
        margin:0 auto;border:1px solid #56726f;border-radius:28px;background:#2b413d;display:grid;place-items:center}}
        .navbar a{{color:white}}</style>
        <body class="authenticated christmas-effects-on" data-account-theme="christmas" data-christmas-light-flow="0">
        <nav aria-label="Main navigation" class="navbar rh-primary-nav seasonal-lights" data-nav-surface="dark"
          data-seasonal-lights-theme="christmas"><div class="container-fluid"><a href="#notifications">Notifications</a></div></nav>
        <button id="christmas-nudge-cord" disabled>Nudge cord</button>
        {scripts}</body></html>"""


class ChristmasNavbarBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.temp.name) / "canopy.html"
        cls.fixture.write_text(fixture_html(), encoding="utf-8")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        cls.addClassCleanup(cls.temp.cleanup)
        cls.addClassCleanup(cls.playwright.stop)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1200, "height": 800})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.addCleanup(self.context.close)

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def enable_test_rays(self):
        self.page.add_init_script("""(() => {
            const getContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, options) {
                // Force the explicitly configured Chromium software renderer in CI.
                return getContext.call(this, type, type === 'webgl2' ? {...options, failIfMajorPerformanceCaveat: false} : options);
            };
        })()""")

    def test_disabled_light_flow_is_omitted_but_preview_speed_is_retained(self):
        from scripts.capture_release_changelog import _render

        document = _render(ROOT, {"kind": "ui", "fixture": "christmas_canopy"})
        csp = "<meta http-equiv=\"Content-Security-Policy\" content=\"script-src 'none'\">"
        self.page.route("**/*", lambda route: route.abort())
        self.page.set_content(document.replace("<head>", f"<head>{csp}", 1))
        profile_script = self.page.evaluate(
            "Array.from(document.scripts).find(script => script.text.includes('const themeForm = '))?.text"
        )
        self.assertIsNotNone(profile_script)
        theme_script = (ROOT / "static" / "js" / "christmas-theme.js").read_text(encoding="utf-8")
        self.page.evaluate("sources => sources.forEach(source => (0, eval)(source))", [profile_script, theme_script])
        self.page.get_by_role("button", name="Themes", exact=True).first.click()
        slider = self.page.get_by_role("slider", name="Light flow")
        lights = self.page.get_by_role("checkbox", name="Navbar lights")
        form = self.page.locator("#profile-theme-form")
        submitted_speed = "form => new FormData(form).get('pref_christmas_light_flow')"
        slider.fill("11")
        with self.subTest("reports a whole percentage"):
            self.assertEqual(slider.get_attribute("aria-valuetext"), "55%")
            self.assertEqual(self.page.locator("#christmas-light-flow-value").inner_text(), "55%")
        lights.uncheck()
        self.assertTrue(slider.is_disabled())
        with self.subTest("omits disabled values from the form"):
            self.assertIsNone(form.evaluate(submitted_speed))
        lights.check()
        self.assertEqual(form.evaluate(submitted_speed), "11")

    def test_ray_layer_draws_transparent_light_and_context_loss_restores_svg(self):
        self.enable_test_rays()
        self.page.add_init_script("""(() => {
            const draw = WebGL2RenderingContext.prototype.drawArrays;
            WebGL2RenderingContext.prototype.drawArrays = function(...args) {
                draw.apply(this, args);
                if (this.canvas.classList.contains('seasonal-canopy-rays') && !window.opticalSample) {
                    const at = (x,y) => {
                        const p = new Uint8Array(4);
                        this.readPixels(Math.round(x*this.drawingBufferWidth),Math.round(y*this.drawingBufferHeight),
                            1,1,this.RGBA,this.UNSIGNED_BYTE,p);
                        return p[3];
                    };
                    window.opticalSample = [at(.5,.97), at(.29,.68), at(.68,.53)];
                }
            };
        })()""")
        self.page.goto(self.fixture.as_uri())
        layer = self.page.locator("canvas.seasonal-canopy-rays")
        layer.wait_for(state="visible", timeout=6000)
        sample = self.page.evaluate("window.opticalSample")
        self.assertEqual(sample[0], 0, "the layer must leave the rest of the page transparent")
        self.assertGreater(max(sample[1:]), 0, "the shader must paint illumination inside the glass")
        self.assertTrue(self.page.locator(".seasonal-light-bulb").first.evaluate("el => getComputedStyle(el).visibility === 'hidden'"))
        layer.evaluate("el => el.getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()")
        layer.wait_for(state="detached")
        self.page.locator(".seasonal-light-bulb").first.wait_for(state="visible")
        self.assertEqual(self.page.locator(".seasonal-light-bulb").count(), 36)
        self.page.evaluate("document.body.dataset.christmasLightFlow = '20'")
        self.page.wait_for_function("+document.querySelector('.seasonal-light-emitter').style.opacity < .85")

    def test_canopy_does_not_expand_the_horizontal_scroll_area(self):
        self.enable_test_rays()
        self.page.goto(self.fixture.as_uri())
        self.page.locator("canvas.seasonal-canopy-rays").wait_for(state="visible")
        self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), 1200)
        self.page.set_viewport_size({"width": 490, "height": 800})
        self.page.wait_for_function("document.querySelectorAll('.seasonal-light-bulb').length === 24")
        self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), 490)

    def test_open_compact_drawer_keeps_both_ray_traced_strands(self):
        self.enable_test_rays()
        self.page.set_viewport_size({"width": 768, "height": 800})
        self.page.add_init_script("""(() => {
            const draw = WebGL2RenderingContext.prototype.drawArrays;
            WebGL2RenderingContext.prototype.drawArrays = function(...args) {
                draw.apply(this, args);
                const nav = this.canvas.parentElement;
                if (!nav || nav.offsetHeight < 400) return;
                const box = this.canvas.getBoundingClientRect();
                const bulbs = nav.querySelectorAll('.seasonal-light-bulb');
                window.openDrawerLight = [bulbs[5], bulbs[16]].map(bulb => {
                    const p = new DOMPoint(0, -11).matrixTransform(bulb.getScreenCTM());
                    const x = Math.floor((p.x - box.left) * this.drawingBufferWidth / box.width);
                    const y = Math.floor((box.bottom - p.y) * this.drawingBufferHeight / box.height);
                    if (x < 0 || y < 0 || x >= this.drawingBufferWidth || y >= this.drawingBufferHeight) return 0;
                    const pixel = new Uint8Array(4);
                    this.readPixels(x,y,1,1,this.RGBA,this.UNSIGNED_BYTE,pixel);
                    return pixel[3];
                });
            };
        })()""")
        self.page.goto(self.fixture.as_uri())
        self.page.locator("canvas.seasonal-canopy-rays").wait_for(state="visible")
        self.page.locator("nav").evaluate("nav => nav.style.height = '430px'")
        sample = self.page.wait_for_function("window.openDrawerLight").json_value()
        self.assertEqual(sample, [255, 255])
        self.assertEqual(self.page.locator(".seasonal-light-bulb").count(), 24)
        self.page.emulate_media(reduced_motion="reduce")
        self.page.locator("canvas.seasonal-canopy-rays").wait_for(state="detached")
        self.page.locator(".seasonal-light-bulb").first.wait_for(state="visible")

    def test_print_hides_decorations_in_both_renderers(self):
        self.enable_test_rays()
        self.page.goto(self.fixture.as_uri())
        rays = self.page.locator("canvas.seasonal-canopy-rays")
        rays.wait_for(state="visible")
        self.page.emulate_media(media="print")
        self.assertFalse(rays.is_visible())
        self.assertFalse(self.page.locator(".seasonal-lights-overlay").is_visible())
        self.assertTrue(self.page.get_by_role("link", name="Notifications").is_visible())

    def test_theme_snapshot_keeps_the_strand_without_serializing_the_ray_buffer(self):
        self.enable_test_rays()
        self.page.goto(self.fixture.as_uri())
        self.page.locator("canvas.seasonal-canopy-rays").wait_for(state="visible")
        scene = self.page.evaluate("""async () => {
            const snapshot = await requestHubChristmasSnapshot.mount(requestHubChristmasSnapshot.capture());
            const doc = snapshot.element.contentDocument;
            const bulb = doc.querySelector('.seasonal-light-bulb');
            const result = {
                rays: !!doc.querySelector('.seasonal-canopy-rays'),
                visibility: doc.defaultView.getComputedStyle(bulb).visibility,
            };
            snapshot.dispose();
            return result;
        }""")
        self.assertEqual(scene, {"rays": False, "visibility": "visible"})
        self.assertTrue(self.page.locator("canvas.seasonal-canopy-rays").is_visible())

    def test_svg_cord_drags_without_intercepting_navigation_and_obeys_reduced_motion(self):
        self.page.add_init_script("""(() => {
            const getContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, options) {
                return type === 'webgl2' ? null : getContext.call(this, type, options);
            };
        })()""")
        self.page.goto(self.fixture.as_uri())
        wire = self.page.locator(".seasonal-light-wire").first
        baseline = wire.get_attribute("d")
        grip = self.page.locator(".seasonal-cord-hit").first
        bounds = grip.bounding_box()
        x, y = bounds["x"] + bounds["width"] * .5, bounds["y"] + bounds["height"] * .7
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + 4, y - 20, steps=4)
        self.page.wait_for_function("d => document.querySelector('.seasonal-light-wire').getAttribute('d') !== d", arg=baseline)
        self.page.mouse.up()
        self.page.get_by_role("link", name="Notifications").click()
        self.assertTrue(self.page.url.endswith("#notifications"))
        self.page.emulate_media(reduced_motion="reduce")
        self.page.wait_for_function("d => document.querySelector('.seasonal-light-wire').getAttribute('d') === d", arg=baseline)
        self.assertTrue(self.page.get_by_role("button", name="Nudge cord").is_disabled())
        self.assertEqual(self.page.locator("canvas.seasonal-canopy-rays").count(), 0)
        self.page.set_viewport_size({"width": 390, "height": 800})
        wire.wait_for(state="detached")
