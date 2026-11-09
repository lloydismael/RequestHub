"""Real-DOM regression checks; uses requirements-changelog-capture.txt."""

import base64
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def fixture_html():
    template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    marker = template.index("const toggle = document.getElementById('theme-checkbox');")
    theme = template[template.rfind("<script>", 0, marker) + 8 : template.index("</script>", marker)]
    styles = "".join(
        f'<link rel="stylesheet" href="{(ROOT / "static" / "css" / name).as_uri()}">'
        for name in ("app.css", "account-themes.css", "christmas-theme.css")
    )
    scripts = "".join(
        f'<script src="{(ROOT / "static" / "js" / name).as_uri()}"></script>'
        for name in ("christmas-theme-snapshot.js", "christmas-theme.js")
    )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        + styles
        + "<style>body{margin:0;min-height:1600px;font-family:system-ui}"
        ".navbar{padding:20px;display:flex;justify-content:space-between}main{padding:25px}"
        "table{width:100%;border-collapse:collapse}td,th{padding:15px;border-bottom:1px solid currentColor}"
        "</style></head><body class='authenticated' data-account-theme='christmas'>"
        "<nav class='navbar'><strong>Synthetic Dashboard</strong><span>"
        "<input id='theme-checkbox' type='checkbox'><label for='theme-checkbox'>Light/dark</label>"
        "</span></nav><main><h1>Dashboard</h1><table><tr><th>Request</th><th>Status</th></tr>"
        "<tr><td>Equipment setup</td><td>In progress</td></tr>"
        "<tr><td>Service review</td><td>Scheduled</td></tr></table>"
        "<input id='fixture-input' value='saved'><input id='fixture-password' type='password'>"
        "<input id='fixture-hidden' type='hidden'><canvas id='fixture-chart' width='240' height='100'></canvas>"
        "<button id='fixture-probe'>Probe</button></main>"
        "<script id='christmas-effects-settings' type='application/json'>"
        '{"enabled":true,"border":false,"snow":false,"lights":false,"cursor":false,"clicks":false,"santa":true}'
        "</script><script>"
        + theme
        + "</script>"
        + scripts
        + "<script>const c=document.getElementById('fixture-chart').getContext('2d');"
        "c.fillStyle='#388256';c.fillRect(10,10,180,70);</script></body></html>"
    )


class ChristmasTransitionBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.temp.name) / "fixture.html"
        cls.fixture.write_text(fixture_html(), encoding="utf-8")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 375, "height": 800})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.fixture.as_uri())

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def switch(self, dark):
        self.page.evaluate(
            "dark=>{const t=document.getElementById('theme-checkbox');t.checked=dark;"
            "t.dispatchEvent(new Event('change',{bubbles:true}));}", dark
        )

    def wait_for_motion(self, native):
        selector = "a.effect?.pseudoElement==='::view-transition-group(christmas-santa)'" if native else "a.effect?.target?.className==='christmas-frozen-viewport'"
        self.page.wait_for_function(f"document.getAnimations().some(a=>{selector})", timeout=4000)

    def finish(self):
        self.page.evaluate(
            "document.getAnimations().forEach(a=>{try{"
            "if(Number.isFinite(a.effect.getComputedTiming().endTime))a.finish();else a.cancel();"
            "}catch{}})"
        )
        self.page.wait_for_function("!document.body.classList.contains('theme-switching')")
        self.assertEqual(self.page.locator(".christmas-santa,.christmas-gifts,.christmas-frozen-viewport").count(), 0)

    def test_native_and_fallback_share_the_edge_in_both_directions(self):
        for width in (375, 640, 1200):
            for native in (True, False):
                for dark in (True, False):
                    with self.subTest(width=width, native=native, dark=dark):
                        self.page.set_viewport_size({"width": width, "height": 800})
                        self.page.evaluate("value=>localStorage.setItem('reqHubTheme',value)", "light" if dark else "dark")
                        self.page.reload()
                        if not native:
                            self.page.evaluate("document.startViewTransition=undefined")
                        self.switch(dark)
                        self.wait_for_motion(native)
                        self.page.evaluate("document.getAnimations().forEach(a=>{a.pause();a.currentTime=750})")
                        state = self.page.evaluate("""({native,dark,width})=>{
                            const root=document.documentElement;
                            const edge=Math.min(180,Math.max(64,width*.16));
                            const hem=document.querySelector('.christmas-santa__blanket');
                            const frame=document.querySelector('.christmas-frozen-viewport');
                            let actual,clip;
                            if(native){
                                actual=parseFloat(getComputedStyle(root,'::view-transition-group(christmas-santa)').translate)+(dark?edge:width-edge);
                                clip=getComputedStyle(root,'::view-transition-new(root)').clipPath;
                            }else{
                                actual=dark?hem.getBoundingClientRect().right:hem.getBoundingClientRect().left;
                                clip=getComputedStyle(frame).clipPath;
                            }
                            return {actual,clip,hemWidth:hem.getBoundingClientRect().width,
                                old:frame?.contentDocument.documentElement.dataset.theme,
                                selected:root.dataset.theme,
                                gifts:document.querySelectorAll('.christmas-gifts .christmas-santa__gift').length,
                                giftAnimations:document.getAnimations().filter(a=>native?
                                    a.effect?.pseudoElement?.startsWith('::view-transition-new(christmas-gift-'):
                                    a.effect?.target?.className?.startsWith('christmas-santa__gift')&&a.effect.getKeyframes().some(k=>k.opacity!==undefined)).length};
                        }""", {"native": native, "dark": dark, "width": width})
                        self.assertAlmostEqual(state["actual"], width / 2, delta=1)
                        self.assertIn("inset(", state["clip"])
                        self.assertLess(state["hemWidth"], width / 2)
                        self.assertEqual(state["selected"], "dark" if dark else "light")
                        self.assertEqual(state["giftAnimations"], 3)
                        if not native:
                            self.assertEqual(state["old"], "light" if dark else "dark")
                            self.assertEqual(state["gifts"], 3)
                        self.finish()

    def test_snapshot_retains_scroll_form_and_canvas_without_active_content(self):
        self.page.evaluate("""()=>{
            document.startViewTransition=undefined;
            document.getElementById('fixture-input').value='edited';
            document.getElementById('fixture-password').value='synthetic-private';
            document.getElementById('fixture-hidden').value='synthetic-credential';
            document.getElementById('fixture-probe').setAttribute('onclick','window.fixtureExecuted=true');
            document.body.insertAdjacentHTML('beforeend',
                '<div id="fixture-horizontal" style="width:160px;overflow:auto"><div style="width:1400px;height:60px">Columns</div></div>'+
                '<div id="fixture-vertical" style="height:100px;width:160px;overflow:auto"><div style="height:600px">Rows</div></div>'+
                '<div id="fixture-rtl" dir="rtl" style="width:160px;overflow:auto"><div style="width:1000px;height:60px">RTL columns</div></div>');
            document.getElementById('fixture-horizontal').scrollLeft=720;
            document.getElementById('fixture-vertical').scrollTop=280;
            document.getElementById('fixture-rtl').scrollLeft=-96;
            document.getElementById('fixture-input').focus();window.scrollTo(0,321);
        }""")
        self.switch(True)
        self.wait_for_motion(False)
        self.page.evaluate("document.getAnimations().forEach(a=>a.pause())")
        state = self.page.evaluate("""()=>{
            const iframe=document.querySelector('.christmas-frozen-viewport'),d=iframe.contentDocument;
            d.getElementById('fixture-probe').click();
            return {scroll:iframe.contentWindow.scrollY,input:d.getElementById('fixture-input').value,
                password:d.getElementById('fixture-password').value,hidden:d.getElementById('fixture-hidden').value,
                scriptCount:d.querySelectorAll('script,iframe,object,embed').length,
                handler:d.getElementById('fixture-probe').getAttribute('onclick'),
                ran:!!iframe.contentWindow.fixtureExecuted,
                canvas:d.querySelector('#fixture-chart').getAttribute('src').startsWith('data:image/'),
                sandbox:iframe.getAttribute('sandbox'),focus:document.activeElement.id,
                horizontal:d.getElementById('fixture-horizontal').scrollLeft,
                vertical:d.getElementById('fixture-vertical').scrollTop,
                rtl:d.getElementById('fixture-rtl').scrollLeft,
                panelWidth:d.getElementById('fixture-vertical').clientWidth,
                originalPanelWidth:document.getElementById('fixture-vertical').clientWidth};
        }""")
        self.assertEqual(state["scroll"], 321)
        self.assertEqual(state["input"], "edited")
        self.assertEqual(state["password"], "")
        self.assertEqual(state["hidden"], "")
        self.assertEqual(state["scriptCount"], 0)
        self.assertIsNone(state["handler"])
        self.assertFalse(state["ran"])
        self.assertTrue(state["canvas"])
        self.assertEqual(state["sandbox"], "allow-same-origin")
        self.assertEqual(state["focus"], "fixture-input")
        self.assertEqual(state["horizontal"], 720)
        self.assertEqual(state["vertical"], 280)
        self.assertEqual(state["rtl"], -96)
        self.assertEqual(state["panelWidth"], state["originalPanelWidth"])
        self.finish()
        self.assertEqual(self.page.evaluate("scrollY"), 321)

    def test_preparing_snapshot_is_hidden_while_resources_are_delayed(self):
        pending_routes = []
        self.page.route("https://snapshot.invalid/delayed.png", lambda route: pending_routes.append(route))
        self.page.evaluate("""()=>{
            window.scrollTo(0,321);
            const source=requestHubChristmasSnapshot.capture();
            source.html=source.html.replace('</body>','<img src="https://snapshot.invalid/delayed.png"></body>');
            window.snapshotReady=false;
            window.snapshotResult=requestHubChristmasSnapshot.mount(source,{timeoutMs:4000}).then(layer=>{
                window.snapshotLayer=layer;window.snapshotReady=true;
            });
        }""")
        self.page.wait_for_function("""document.querySelector('.christmas-frozen-viewport')?.contentDocument?.querySelector('img[src*="snapshot.invalid"]')""")
        preparing = self.page.evaluate("""()=>({
            visibility:getComputedStyle(document.querySelector('.christmas-frozen-viewport')).visibility,
            ready:window.snapshotReady,scroll:window.scrollY,
        })""")
        self.assertFalse(preparing["ready"])
        self.assertEqual(preparing["scroll"], 321)
        self.assertEqual(preparing["visibility"], "hidden")
        self.assertEqual(len(pending_routes), 1)
        pending_routes[0].fulfill(content_type="image/png", body=base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="
        ))
        self.page.wait_for_function("window.snapshotReady")
        restored = self.page.evaluate("""()=>({
            visibility:getComputedStyle(window.snapshotLayer.element).visibility,
            scroll:window.snapshotLayer.element.contentWindow.scrollY,
        })""")
        self.assertEqual(restored["visibility"], "visible")
        self.assertEqual(restored["scroll"], 321)
        self.page.evaluate("window.snapshotLayer.dispose()")

    def test_reversals_and_reduced_motion_leave_only_the_latest_theme(self):
        self.page.evaluate("document.startViewTransition=undefined")
        self.switch(True)
        self.wait_for_motion(False)
        self.switch(False)
        self.wait_for_motion(False)
        self.finish()
        self.assertEqual(self.page.evaluate("localStorage.getItem('reqHubTheme')"), "light")
        self.page.emulate_media(reduced_motion="reduce")
        self.switch(True)
        self.page.wait_for_function("document.documentElement.dataset.theme==='dark'")
        self.assertEqual(self.page.locator(".christmas-santa,.christmas-frozen-viewport").count(), 0)
        self.page.emulate_media(reduced_motion="no-preference")
        self.page.evaluate("document.body.dataset.accountTheme='blue'")
        self.switch(False)
        self.page.wait_for_function("!document.body.classList.contains('theme-switching')")
        self.assertEqual(self.page.locator(".christmas-santa,.christmas-frozen-viewport").count(), 0)


if __name__ == "__main__":
    unittest.main()
